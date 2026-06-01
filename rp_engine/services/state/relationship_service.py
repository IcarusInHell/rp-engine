"""Relationship domain service — directional trust and the relationship graph.

Owns trust reads/writes (directional ``a→b``), modifier trust effects, session
caps, and the relationship-graph assembly. Depends on CharacterService for the
graph's character nodes (per decompose-state-manager Option B).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from rp_engine.config import ModifierTrustEffect, TrustConfig, get_config
from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.state import (
    CharacterDetail,
    RelationshipDetail,
    RelationshipGraphResponse,
    RelGraphEdge,
    RelGraphMetadata,
    RelGraphNode,
    TrustModification,
)
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.state.character_service import CharacterService
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_list
from rp_engine.utils.state_helpers import resolve_exchange_number
from rp_engine.utils.trust import trust_stage

logger = logging.getLogger(__name__)


class RelationshipService:
    """Directional trust + relationship-graph assembly, scoped by branch."""

    def __init__(
        self,
        db: Database,
        character_service: CharacterService,
        config: TrustConfig | None = None,
        resolver: AncestryResolver | None = None,
    ) -> None:
        self.db = db
        self.character_service = character_service
        self._config_override: TrustConfig | None = config
        self.resolver = resolver
        self.diagnostic_logger = None  # injected by container

    @property
    def config(self) -> TrustConfig:
        """Read trust config dynamically so hot-reloaded changes take effect.

        Tests can pass a custom TrustConfig at construction to override.
        Production code passes None; the property reads from get_config().
        """
        if self._config_override is not None:
            return self._config_override
        return get_config().trust

    # ===================================================================
    # Modifier Trust Effects
    # ===================================================================

    async def _get_modifier_effects(
        self, char_name: str, rp_folder: str
    ) -> ModifierTrustEffect | None:
        """Look up combined modifier trust effects for an NPC.

        Reads the NPC's behavioral_modifiers from story_cards, maps each to
        config-defined trust effects, and merges them into a single effect.
        """
        if not self.config.modifier_effects:
            return None

        card = await self.db.fetch_one(
            """SELECT frontmatter FROM story_cards
               WHERE rp_folder = ? AND LOWER(name) = LOWER(?)
                 AND card_type IN ('character', 'npc')""",
            [rp_folder, char_name],
        )
        if not card:
            return None

        fm = safe_parse_json(card.get("frontmatter"))
        modifiers = safe_parse_json_list(fm.get("behavioral_modifiers"))
        if not modifiers:
            return None

        # Merge effects from all modifiers
        merged = ModifierTrustEffect()
        has_effects = False
        for mod_name in modifiers:
            mod_str = mod_name if isinstance(mod_name, str) else str(mod_name)
            effect = self.config.modifier_effects.get(mod_str.upper())
            if effect:
                has_effects = True
                # Ceiling offset: use the most restrictive (most negative)
                if effect.ceiling_offset != 0:
                    merged.ceiling_offset = min(merged.ceiling_offset, effect.ceiling_offset)
                # Multipliers: multiply together
                merged.gain_multiplier *= effect.gain_multiplier
                merged.loss_multiplier *= effect.loss_multiplier
                # Instant shifts: merge (later modifier wins on collision)
                merged.instant_shifts.update(effect.instant_shifts)

        return merged if has_effects else None

    # ===================================================================
    # Relationships & Trust
    # ===================================================================

    async def _get_relationship(
        self, char_a: str, char_b: str, rp_folder: str, branch: str = "main"
    ) -> RelationshipDetail | None:
        """Fetch char_a's relationship toward char_b — strictly directional (a→b).

        Trust is asymmetric by design (``(a,b)`` ≠ ``(b,a)``); this reads only the
        a→b direction and never merges or swaps to b→a. Internal helper for
        ``update_trust`` — zero external callers (hence the underscore prefix).
        """
        if not self.resolver:
            logger.warning("_get_relationship called without resolver")
            return None

        trust_data = await self.resolver.resolve_trust(char_a, char_b, rp_folder, branch)

        if trust_data["live_score"] == 0 and trust_data["baseline_score"] == 0:
            return None

        # Get modification history
        mods = await self.resolver.resolve_trust_full_history(
            char_a, char_b, rp_folder, branch
        )
        modifications = [
            TrustModification(
                date=m.get("date"),
                change=m.get("change") or 0,
                direction=m.get("direction") or "neutral",
                reason=m.get("reason"),
                exchange_id=m.get("exchange_id"),
                branch=m.get("branch"),
                exchange_number=m.get("exchange_number"),
            )
            for m in mods
        ]

        # Load relationship dynamic (role) from entity_connections
        dynamic_row = await self.db.fetch_one(
            """SELECT role FROM entity_connections
               WHERE connection_type = 'has_relationship'
                 AND ((LOWER(from_entity) = LOWER(?) AND LOWER(to_entity) = LOWER(?))
                   OR (LOWER(from_entity) = LOWER(?) AND LOWER(to_entity) = LOWER(?)))
                 AND role IS NOT NULL
               LIMIT 1""",
            [char_a, char_b, char_b, char_a],
        )

        return RelationshipDetail(
            character_a=char_a,
            character_b=char_b,
            initial_trust_score=trust_data["baseline_score"],
            trust_modification_sum=trust_data["branch_modifications_sum"],
            live_trust_score=trust_data["live_score"],
            trust_stage=trust_data["trust_stage"],
            dynamic=dynamic_row["role"] if dynamic_row else None,
            modifications=modifications,
        )

    async def get_all_relationships(
        self,
        rp_folder: str,
        branch: str = "main",
        character: str | None = None,
    ) -> list[RelationshipDetail]:
        """Fetch all relationships, optionally filtered by character (batch)."""
        if not self.resolver:
            logger.warning("get_all_relationships called without resolver")
            return []

        # Get all trust baselines for this branch
        if character:
            baseline_rows = await self.db.fetch_all(
                """SELECT * FROM trust_baselines
                   WHERE rp_folder = ? AND branch = ?
                     AND (LOWER(character_a) = LOWER(?) OR LOWER(character_b) = LOWER(?))""",
                [rp_folder, branch, character, character],
            )
        else:
            baseline_rows = await self.db.fetch_all(
                "SELECT * FROM trust_baselines WHERE rp_folder = ? AND branch = ?",
                [rp_folder, branch],
            )

        if not baseline_rows:
            return []

        # Batch fetch modification sums for all pairs at once
        mod_rows = await self.db.fetch_all(
            """SELECT character_a, character_b, COALESCE(SUM(change), 0) as total
               FROM trust_modifications WHERE rp_folder = ? AND branch = ?
               GROUP BY character_a, character_b""",
            [rp_folder, branch],
        )
        mod_map: dict[tuple[str, str], int] = {}
        for row in mod_rows:
            mod_map[(row["character_a"], row["character_b"])] = row["total"]

        # Batch fetch relationship dynamics (roles) from entity_connections.
        # B3: from_entity/to_entity are stored as "rp_folder:key", so scope the
        # query to this folder — without it the query scans every folder's rows.
        role_rows = await self.db.fetch_all(
            """SELECT from_entity, to_entity, role FROM entity_connections
               WHERE connection_type = 'has_relationship' AND role IS NOT NULL
                 AND (from_entity LIKE ? OR to_entity LIKE ?)""",
            [f"{rp_folder}:%", f"{rp_folder}:%"],
        )
        role_map: dict[tuple[str, str], str] = {}
        for rr in role_rows:
            role_map[(rr["from_entity"].lower(), rr["to_entity"].lower())] = rr["role"]

        results = []
        seen_pairs: set[tuple[str, str]] = set()
        for br in baseline_rows:
            pair = (br["character_a"], br["character_b"])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            baseline = br.get("baseline_score") or 0
            mod_sum = mod_map.get(pair, 0)
            live = baseline + mod_sum

            # Look up dynamic from either direction
            a_lower, b_lower = pair[0].lower(), pair[1].lower()
            dynamic = role_map.get((a_lower, b_lower)) or role_map.get((b_lower, a_lower))

            results.append(RelationshipDetail(
                character_a=br["character_a"],
                character_b=br["character_b"],
                initial_trust_score=baseline,
                trust_modification_sum=mod_sum,
                live_trust_score=live,
                trust_stage=trust_stage(live),
                dynamic=dynamic,
            ))

        return results

    async def get_relationship_graph(
        self,
        rp_folder: str,
        branch: str = "main",
        pov_character: str | None = None,
    ) -> RelationshipGraphResponse:
        """Build a relationship graph with nodes (characters) and edges (trust)."""
        card_info, card_id_to_name = await self._load_character_cards_for_graph(rp_folder)
        char_states = await self.character_service.get_all_characters(rp_folder, branch)
        relationships = await self.get_all_relationships(rp_folder, branch)
        trend_map, mod_count_map = await self._compute_trust_trends(rp_folder, branch)

        # Card-defined initial_relationships become fallback edges (only for pairs
        # without a live trust_baseline row).
        card_edges = self._build_initial_relationship_edges(
            card_info, card_id_to_name, relationships
        )

        # Collect all character names from cards + relationships + card edges
        all_names: set[str] = set(card_info.keys())
        for rel in relationships:
            all_names.add(rel.character_a)
            all_names.add(rel.character_b)
        for ce in card_edges:
            all_names.add(ce.from_char)
            all_names.add(ce.to_char)

        nodes = self._build_graph_nodes(
            all_names, card_info, char_states, relationships, card_edges
        )
        edges = self._build_graph_edges(
            relationships, card_edges, trend_map, mod_count_map
        )

        npc_count = sum(1 for n in nodes if not n.is_player_character)
        return RelationshipGraphResponse(
            nodes=nodes,
            edges=edges,
            metadata=RelGraphMetadata(total_npcs=npc_count, total_edges=len(edges)),
        )

    async def _load_character_cards_for_graph(
        self, rp_folder: str
    ) -> tuple[dict[str, dict], dict[str, str]]:
        """Load character/NPC cards → (name→info map, card_id→name map)."""
        card_rows = await self.db.fetch_all(
            """SELECT name, importance, frontmatter FROM story_cards
               WHERE rp_folder = ? AND card_type IN ('character', 'npc')""",
            [rp_folder],
        )

        card_info: dict[str, dict] = {}
        card_id_to_name: dict[str, str] = {}
        for row in card_rows:
            fm = safe_parse_json(row["frontmatter"])
            card_info[row["name"]] = {
                "importance": row["importance"],
                "primary_archetype": fm.get("primary_archetype"),
                "is_player_character": (
                    fm.get("character_type", "").lower() == "pc"
                    or fm.get("pov_character", False) is True
                    or fm.get("role", "").lower() == "pov_character"
                ),
                "initial_relationships": fm.get("initial_relationships", []),
            }
            cid = fm.get("card_id")
            if cid:
                card_id_to_name[str(cid).lower()] = row["name"]
        return card_info, card_id_to_name

    async def _compute_trust_trends(
        self, rp_folder: str, branch: str
    ) -> tuple[dict[tuple[str, str], list[int]], dict[tuple[str, str], int]]:
        """Build per-pair trend (last 5 changes) + total modification-count maps."""
        trend_rows = await self.db.fetch_all(
            """SELECT character_a, character_b, change,
                      ROW_NUMBER() OVER (PARTITION BY character_a, character_b ORDER BY created_at DESC) as rn
               FROM trust_modifications
               WHERE rp_folder = ? AND branch = ?""",
            [rp_folder, branch],
        )
        trend_map: dict[tuple[str, str], list[int]] = {}
        mod_count_map: dict[tuple[str, str], int] = {}
        for row in trend_rows:
            pair = (row["character_a"], row["character_b"])
            mod_count_map[pair] = mod_count_map.get(pair, 0) + 1
            if row["rn"] <= 5:
                trend_map.setdefault(pair, []).append(row["change"])
        return trend_map, mod_count_map

    def _build_initial_relationship_edges(
        self,
        card_info: dict[str, dict],
        card_id_to_name: dict[str, str],
        relationships: list[RelationshipDetail],
    ) -> list[RelGraphEdge]:
        """Card-defined initial_relationships → fallback edges for pairs that have
        no live trust_baseline row yet (so the graph isn't empty pre-modification).
        """
        existing_pairs: set[tuple[str, str]] = set()
        for rel in relationships:
            existing_pairs.add((rel.character_a.lower(), rel.character_b.lower()))
            existing_pairs.add((rel.character_b.lower(), rel.character_a.lower()))

        card_edges: list[RelGraphEdge] = []
        for card_name, info in card_info.items():
            for ir in info.get("initial_relationships") or []:
                if not isinstance(ir, dict):
                    continue
                target_id = str(ir.get("target", "")).lower()
                target_name = card_id_to_name.get(target_id, "")
                if not target_name:
                    continue
                pair_key = (card_name.lower(), target_name.lower())
                if pair_key in existing_pairs:
                    continue
                existing_pairs.add(pair_key)
                existing_pairs.add((target_name.lower(), card_name.lower()))
                score = ir.get("trust", 0)
                card_edges.append(RelGraphEdge(
                    from_char=card_name,
                    to_char=target_name,
                    trust_score=score,
                    trust_stage=trust_stage(score),
                    dynamic=ir.get("role"),
                    trend="stable",
                    modification_count=0,
                ))
        return card_edges

    def _max_trust_for_character(
        self,
        name: str,
        relationships: list[RelationshipDetail],
        card_edges: list[RelGraphEdge],
    ) -> int:
        """Largest-magnitude trust score touching ``name``.

        Iterates relationship rows first, then card edges (relationships-first so
        first-seen wins on a magnitude tie), comparing on ``abs()`` with a strict
        ``>``. Replaces the former ``type("_Rel", ...)`` attribute-bridging hack
        (B1) by reading each list by its real field names.
        """
        max_trust = 0
        for rel in relationships:
            if rel.character_a == name or rel.character_b == name:
                if abs(rel.live_trust_score) > abs(max_trust):
                    max_trust = rel.live_trust_score
        for e in card_edges:
            if e.from_char == name or e.to_char == name:
                if abs(e.trust_score) > abs(max_trust):
                    max_trust = e.trust_score
        return max_trust

    def _build_graph_nodes(
        self,
        all_names: set[str],
        card_info: dict[str, dict],
        char_states: dict[str, CharacterDetail],
        relationships: list[RelationshipDetail],
        card_edges: list[RelGraphEdge],
    ) -> list[RelGraphNode]:
        """Build one node per character, sorted by name; trust = abs-max edge."""
        nodes: list[RelGraphNode] = []
        for name in sorted(all_names):
            info = card_info.get(name, {})
            char_state = char_states.get(name)
            max_trust = self._max_trust_for_character(name, relationships, card_edges)
            nodes.append(RelGraphNode(
                name=name,
                is_player_character=info.get("is_player_character", False),
                importance=info.get("importance"),
                primary_archetype=info.get("primary_archetype"),
                trust_score=max_trust,
                trust_stage=trust_stage(max_trust),
                emotional_state=char_state.emotional_state if char_state else None,
                location=char_state.location if char_state else None,
            ))
        return nodes

    def _build_graph_edges(
        self,
        relationships: list[RelationshipDetail],
        card_edges: list[RelGraphEdge],
        trend_map: dict[tuple[str, str], list[int]],
        mod_count_map: dict[tuple[str, str], int],
    ) -> list[RelGraphEdge]:
        """Build edges from trust_baselines (with trend) + card-defined edges."""
        edges: list[RelGraphEdge] = []
        for rel in relationships:
            pair = (rel.character_a, rel.character_b)
            changes = trend_map.get(pair, [])
            trend_sum = sum(changes)
            trend = "rising" if trend_sum > 0 else ("falling" if trend_sum < 0 else "stable")

            edges.append(RelGraphEdge(
                from_char=rel.character_a,
                to_char=rel.character_b,
                trust_score=rel.live_trust_score,
                trust_stage=rel.trust_stage,
                dynamic=rel.dynamic,
                trend=trend,
                modification_count=mod_count_map.get(pair, 0),
            ))
        edges.extend(card_edges)
        return edges

    async def _get_session_trust_caps(
        self, char_a: str, char_b: str, rp_folder: str, branch: str
    ) -> dict[str, int]:
        """Compute session trust caps from DB.

        Sums gains and losses for this character pair in the current active session.
        Survives server restarts — DB is the source of truth.
        """
        # Find active session
        session_row = await self.db.fetch_one(
            """SELECT id FROM sessions
               WHERE rp_folder = ? AND branch = ? AND ended_at IS NULL
               ORDER BY started_at DESC LIMIT 1""",
            [rp_folder, branch],
        )
        if not session_row:
            return {"gained": 0, "lost": 0}

        row = await self.db.fetch_one(
            """SELECT
                   COALESCE(SUM(CASE WHEN tm.change > 0 THEN tm.change ELSE 0 END), 0) as session_gained,
                   COALESCE(SUM(CASE WHEN tm.change < 0 THEN tm.change ELSE 0 END), 0) as session_lost
               FROM trust_modifications tm
               JOIN exchanges e ON tm.exchange_id = e.id
               WHERE e.session_id = ?
                 AND tm.rp_folder = ? AND tm.branch = ?
                 AND ((LOWER(tm.character_a) = LOWER(?) AND LOWER(tm.character_b) = LOWER(?))
                   OR (LOWER(tm.character_a) = LOWER(?) AND LOWER(tm.character_b) = LOWER(?)))""",
            [session_row["id"], rp_folder, branch,
             char_a, char_b, char_b, char_a],
        )

        return {
            "gained": row["session_gained"] if row else 0,
            "lost": row["session_lost"] if row else 0,
        }

    async def update_trust(
        self,
        char_a: str,
        char_b: str,
        change: int,
        direction: str,
        reason: str,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
        bypass_session_cap: bool = False,
    ) -> RelationshipDetail:
        """Apply a trust change between two characters.

        Uses trust_modifications with direct columns (character_a, character_b, branch,
        exchange_number, rp_folder) for direct querying without JOIN.
        Checks session caps (computed from DB), modifier effects, and score bounds.

        Args:
            bypass_session_cap: If True, skip session cap checks. Used for
                HONOR_BOUND instant shifts triggered by oath events.
        """
        if not self.resolver:
            raise RuntimeError("update_trust requires an AncestryResolver")

        now = datetime.now(UTC).isoformat()
        exchange_number = await resolve_exchange_number(self.db, exchange_id, rp_folder, branch)

        # Resolve current trust through ancestry
        trust_data = await self.resolver.resolve_trust(char_a, char_b, rp_folder, branch)
        current_live = trust_data["live_score"]

        effective_change = await self._compute_effective_trust_change(
            char_a, char_b, change, current_live, rp_folder, branch, bypass_session_cap
        )

        if effective_change == 0:
            return await self._relationship_or_fallback(
                char_a, char_b, rp_folder, branch, current_live
            )

        await self._ensure_trust_baseline_exists(char_a, char_b, rp_folder, branch, now)

        # Insert trust modification with direct columns
        future = await self.db.enqueue_write(
            """INSERT INTO trust_modifications
                   (date, change, direction, reason, exchange_id, created_at,
                    character_a, character_b, branch, exchange_number, rp_folder)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [now[:10], effective_change, direction, reason, exchange_id, now,
             char_a, char_b, branch, exchange_number, rp_folder],
            priority=PRIORITY_ANALYSIS,
        )
        await future

        self._log_trust_update(
            char_a=char_a, char_b=char_b, requested=change, effective=effective_change,
            direction=direction, old_score=current_live,
            new_score=current_live + effective_change, reason=reason,
            rp_folder=rp_folder, branch=branch, exchange_id=exchange_id,
            bypass=bypass_session_cap,
        )

        return await self._relationship_or_fallback(
            char_a, char_b, rp_folder, branch, current_live + effective_change
        )

    async def _compute_effective_trust_change(
        self,
        char_a: str,
        char_b: str,
        change: int,
        current_live: int,
        rp_folder: str,
        branch: str,
        bypass_session_cap: bool,
    ) -> int:
        """Resolve the requested change into the effective applied change.

        Pipeline: session caps (unless bypassed) → modifier gain/loss multipliers
        → clamp to modifier-adjusted score bounds. Returns 0 when capped/clamped out.
        """
        effective_change = change

        # Check session caps (computed from DB — survives restarts)
        if not bypass_session_cap:
            caps = await self._get_session_trust_caps(char_a, char_b, rp_folder, branch)

            if change > 0:
                remaining_gain = self.config.session_max_gain - caps["gained"]
                if remaining_gain <= 0:
                    logger.info(
                        "Session gain cap reached for %s<->%s (gained=%d, cap=%d)",
                        char_a, char_b, caps["gained"], self.config.session_max_gain,
                    )
                    effective_change = 0
                else:
                    effective_change = min(change, remaining_gain)
            elif change < 0:
                remaining_loss = self.config.session_max_loss - caps["lost"]
                if remaining_loss >= 0:
                    logger.info(
                        "Session loss cap reached for %s<->%s (lost=%d, cap=%d)",
                        char_a, char_b, caps["lost"], self.config.session_max_loss,
                    )
                    effective_change = 0
                else:
                    effective_change = max(change, remaining_loss)

        # Apply modifier trust effects (after session cap, before score bounds)
        modifier_effects = await self._get_modifier_effects(char_b, rp_folder)
        if modifier_effects and effective_change != 0:
            # Apply gain/loss multipliers
            if effective_change > 0 and modifier_effects.gain_multiplier != 1.0:
                effective_change = max(1, int(effective_change * modifier_effects.gain_multiplier))
            elif effective_change < 0 and modifier_effects.loss_multiplier != 1.0:
                effective_change = min(-1, int(effective_change * modifier_effects.loss_multiplier))

        # Clamp to modifier-adjusted score bounds
        effective_max = self.config.max_score + (
            modifier_effects.ceiling_offset if modifier_effects else 0
        )
        effective_min = self.config.min_score
        new_live = current_live + effective_change
        if new_live > effective_max:
            effective_change = effective_max - current_live
        elif new_live < effective_min:
            effective_change = effective_min - current_live

        return effective_change

    async def _ensure_trust_baseline_exists(
        self, char_a: str, char_b: str, rp_folder: str, branch: str, now: str
    ) -> None:
        """Create a zero baseline row for the pair/branch if none exists (case-insensitive)."""
        existing_baseline = await self.db.fetch_one(
            """SELECT * FROM trust_baselines
               WHERE LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)
                 AND rp_folder = ? AND branch = ?""",
            [char_a, char_b, rp_folder, branch],
        )
        if not existing_baseline:
            future = await self.db.enqueue_write(
                """INSERT OR IGNORE INTO trust_baselines
                       (character_a, character_b, rp_folder, branch, baseline_score, created_at)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                [char_a, char_b, rp_folder, branch, now],
                priority=PRIORITY_ANALYSIS,
            )
            await future

    def _log_trust_update(
        self,
        *,
        char_a: str,
        char_b: str,
        requested: int,
        effective: int,
        direction: str,
        old_score: int,
        new_score: int,
        reason: str,
        rp_folder: str,
        branch: str,
        exchange_id: int | None,
        bypass: bool,
    ) -> None:
        """Emit the structured trust-update diagnostic (no-op without a logger)."""
        if not self.diagnostic_logger:
            return
        self.diagnostic_logger.log(
            category="trust",
            event="trust_updated",
            data={
                "char_a": char_a,
                "char_b": char_b,
                "requested_change": requested,
                "effective_change": effective,
                "direction": direction,
                "old_score": old_score,
                "new_score": new_score,
                "reason": reason,
                "rp_folder": rp_folder,
                "branch": branch,
                "exchange_id": exchange_id,
                "bypass_session_cap": bypass,
            },
        )

    async def _relationship_or_fallback(
        self, char_a: str, char_b: str, rp_folder: str, branch: str, fallback_score: int
    ) -> RelationshipDetail:
        """Return the resolved a→b relationship, or a minimal fallback at the given score."""
        rel = await self._get_relationship(char_a, char_b, rp_folder, branch)
        if rel:
            return rel
        return RelationshipDetail(
            character_a=char_a, character_b=char_b,
            live_trust_score=fallback_score, trust_stage=trust_stage(fallback_score),
        )
