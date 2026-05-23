"""Centralized state management using copy-on-write branching.

Characters, relationships, scenes, and events are resolved through the branch
ancestry graph. State is stored as CoW snapshots (character_state_entries,
scene_state_entries) and trust modifications with direct branch/exchange columns.

Cards are read-only — all runtime state lives in the DB, scoped by branch.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from rp_engine.config import ModifierTrustEffect, TrustConfig, get_config
from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.context import SceneState
from rp_engine.models.state import (
    CharacterDetail,
    CharacterUpdate,
    EventDetail,
    RelationshipDetail,
    RelationshipGraphResponse,
    RelGraphEdge,
    RelGraphMetadata,
    RelGraphNode,
    SceneUpdate,
    StateSnapshot,
    TrustModification,
)
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.state_entry_resolver import (
    latest_character_state,
    latest_character_states_batch,
    latest_scene_state,
)
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_list
from rp_engine.utils.trust import trust_stage

logger = logging.getLogger(__name__)


class StateManager:
    """Manages character state, relationships/trust, scene context, and events.

    Uses AncestryResolver for branch-aware state resolution.
    """

    def __init__(self, db: Database, config: TrustConfig | None = None, resolver: AncestryResolver | None = None) -> None:
        self.db = db
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
    # Exchange Number Resolution
    # ===================================================================

    async def _resolve_exchange_number(
        self, exchange_id: int | None, rp_folder: str, branch: str
    ) -> int:
        """Resolve an exchange_id to an exchange_number, or get the latest."""
        if exchange_id is not None:
            row = await self.db.fetch_one(
                "SELECT exchange_number FROM exchanges WHERE id = ?", [exchange_id]
            )
            if row:
                return row["exchange_number"]
        # Fall back to latest exchange_number on this branch
        latest = await self.db.fetch_val(
            "SELECT MAX(exchange_number) FROM exchanges WHERE rp_folder = ? AND branch = ?",
            [rp_folder, branch],
        )
        return latest or 0

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
    # Character State
    # ===================================================================

    async def get_character(
        self, name: str, rp_folder: str, branch: str = "main"
    ) -> CharacterDetail | None:
        """Fetch a single character by name, resolving state through ancestry."""
        # 1. Find the character's card in story_cards
        card = await self.db.fetch_one(
            """SELECT * FROM story_cards
               WHERE rp_folder = ? AND LOWER(name) = LOWER(?)
                 AND card_type IN ('character', 'npc')""",
            [rp_folder, name],
        )
        if not card:
            return None

        # 2. Resolve runtime state (direct branch query — state snapshotted at branch creation)
        runtime = await latest_character_state(self.db, rp_folder, branch, card["id"])

        # 3. Build CharacterDetail from card + runtime state
        fm = safe_parse_json(card.get("frontmatter"))

        return CharacterDetail(
            name=card["name"],
            card_path=card.get("file_path"),
            is_player_character=bool(fm.get("is_player_character", False)),
            importance=card.get("importance") or fm.get("importance"),
            primary_archetype=fm.get("primary_archetype"),
            secondary_archetype=fm.get("secondary_archetype"),
            behavioral_modifiers=safe_parse_json_list(fm.get("behavioral_modifiers")),
            location=runtime.get("location") if runtime else None,
            conditions=safe_parse_json_list(runtime.get("conditions") if runtime else None),
            emotional_state=runtime.get("emotional_state") if runtime else None,
            last_seen=runtime.get("last_seen") if runtime else None,
            updated_at=runtime.get("created_at") if runtime else None,
        )

    async def get_all_characters(
        self, rp_folder: str, branch: str = "main"
    ) -> dict[str, CharacterDetail]:
        """Fetch all characters, resolving state through ancestry (batch)."""
        # Get active characters from ledger + card data in one query
        ledger_rows = await self.db.fetch_all(
            """SELECT cl.card_id, sc.*
               FROM character_ledger cl
               JOIN story_cards sc ON cl.card_id = sc.id
               WHERE cl.rp_folder = ? AND cl.branch = ? AND cl.status = 'active'""",
            [rp_folder, branch],
        )
        if not ledger_rows:
            return {}

        # Batch fetch runtime state
        card_ids = [lr["card_id"] for lr in ledger_rows]
        runtime_map = await latest_character_states_batch(
            self.db, rp_folder, branch, card_ids
        ) if card_ids else {}

        result = {}
        for lr in ledger_rows:
            fm = safe_parse_json(lr.get("frontmatter"))
            runtime = runtime_map.get(lr["card_id"])

            detail = CharacterDetail(
                name=lr["name"],
                card_path=lr.get("file_path"),
                is_player_character=bool(fm.get("is_player_character", False)),
                importance=lr.get("importance") or fm.get("importance"),
                primary_archetype=fm.get("primary_archetype"),
                secondary_archetype=fm.get("secondary_archetype"),
                behavioral_modifiers=safe_parse_json_list(fm.get("behavioral_modifiers")),
                location=runtime.get("location") if runtime else None,
                conditions=safe_parse_json_list(runtime.get("conditions") if runtime else None),
                emotional_state=runtime.get("emotional_state") if runtime else None,
                last_seen=runtime.get("last_seen") if runtime else None,
                updated_at=runtime.get("created_at") if runtime else None,
            )
            result[lr["name"]] = detail
        return result

    async def get_characters_at_location(
        self, location: str, rp_folder: str, branch: str = "main"
    ) -> list[CharacterDetail]:
        """Fetch characters at a specific location, resolving through ancestry."""
        all_chars = await self.get_all_characters(rp_folder, branch)
        return [
            c for c in all_chars.values()
            if c.location and c.location.lower() == location.lower()
        ]

    async def update_character(
        self,
        name: str,
        updates: CharacterUpdate,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
    ) -> CharacterDetail:
        """Write a new character state snapshot (CoW entry).

        Creates a full snapshot by merging updates with the current resolved state.
        Also ensures the character exists in the ledger.
        """
        now = datetime.now(UTC).isoformat()
        exchange_number = await self._resolve_exchange_number(exchange_id, rp_folder, branch)

        # 1. Find or create the character's card_id
        card = await self.db.fetch_one(
            """SELECT id, name FROM story_cards
               WHERE rp_folder = ? AND LOWER(name) = LOWER(?)
                 AND card_type IN ('character', 'npc')""",
            [rp_folder, name],
        )

        if card:
            card_id = card["id"]

            # 2. Ensure ledger entry exists
            ledger = await self.db.fetch_one(
                "SELECT * FROM character_ledger WHERE card_id = ? AND rp_folder = ? AND branch = ?",
                [card_id, rp_folder, branch],
            )
            if not ledger:
                future = await self.db.enqueue_write(
                    """INSERT OR IGNORE INTO character_ledger
                           (card_id, rp_folder, branch, status, activated_at_exchange, created_at)
                       VALUES (?, ?, ?, 'active', ?, ?)""",
                    [card_id, rp_folder, branch, exchange_number, now],
                    priority=PRIORITY_ANALYSIS,
                )
                await future
            elif ledger["status"] == "dormant":
                future = await self.db.enqueue_write(
                    """UPDATE character_ledger SET status = 'active', activated_at_exchange = ?
                       WHERE card_id = ? AND rp_folder = ? AND branch = ?""",
                    [exchange_number, card_id, rp_folder, branch],
                    priority=PRIORITY_ANALYSIS,
                )
                await future

            # 3. Resolve current state and merge (direct branch query)
            current_runtime = await latest_character_state(self.db, rp_folder, branch, card_id)

            new_location = updates.location if updates.location is not None else (
                current_runtime.get("location") if current_runtime else None
            )
            new_conditions = (
                json.dumps(updates.conditions)
                if updates.conditions is not None
                else (current_runtime.get("conditions") if current_runtime else "[]")
            )
            new_emotional = updates.emotional_state if updates.emotional_state is not None else (
                current_runtime.get("emotional_state") if current_runtime else None
            )
            new_last_seen = updates.last_seen if updates.last_seen is not None else (
                current_runtime.get("last_seen") if current_runtime else None
            )

            # 4. Insert CoW entry (INSERT OR REPLACE for same exchange_number)
            future = await self.db.enqueue_write(
                """INSERT OR REPLACE INTO character_state_entries
                       (card_id, rp_folder, branch, exchange_number,
                        location, conditions, emotional_state, last_seen, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [card_id, rp_folder, branch, exchange_number,
                 new_location, new_conditions, new_emotional, new_last_seen, now],
                priority=PRIORITY_ANALYSIS,
            )
            await future
        else:
            # No story card found — cannot update character without a card
            logger.warning("No story card found for character '%s' in rp_folder '%s'", name, rp_folder)
            return CharacterDetail(name=name)

        result = await self.get_character(name, rp_folder, branch)
        return result  # type: ignore[return-value]

    # ===================================================================
    # Relationships & Trust
    # ===================================================================

    async def get_relationship(
        self, char_a: str, char_b: str, rp_folder: str, branch: str = "main"
    ) -> RelationshipDetail | None:
        """Fetch a relationship between two characters using ancestry-resolved trust."""
        if not self.resolver:
            logger.warning("get_relationship called without resolver")
            return None

        trust_data = await self.resolver.resolve_trust(char_a, char_b, rp_folder, branch)
        # Also check reverse direction
        trust_data_rev = await self.resolver.resolve_trust(char_b, char_a, rp_folder, branch)

        # Use whichever has data (or merge both)
        if trust_data["live_score"] == 0 and trust_data_rev["live_score"] != 0:
            trust_data = trust_data_rev
            char_a, char_b = char_b, char_a

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

        # Batch fetch relationship dynamics (roles) from entity_connections
        role_rows = await self.db.fetch_all(
            """SELECT from_entity, to_entity, role FROM entity_connections
               WHERE connection_type = 'has_relationship' AND role IS NOT NULL""",
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
        # 1. Get character cards → nodes
        card_rows = await self.db.fetch_all(
            """SELECT name, importance, frontmatter FROM story_cards
               WHERE rp_folder = ? AND card_type IN ('character', 'npc')""",
            [rp_folder],
        )

        # Build name→card info map + card_id→name mapping
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

        # 2. Get character states
        char_states = await self.get_all_characters(rp_folder, branch)

        # 3. Get relationships from trust_baselines
        relationships = await self.get_all_relationships(rp_folder, branch)

        # 4. Build trend data from trust_modifications (last 5 per pair)
        trend_rows = await self.db.fetch_all(
            """SELECT character_a, character_b, change,
                      ROW_NUMBER() OVER (PARTITION BY character_a, character_b ORDER BY created_at DESC) as rn
               FROM trust_modifications
               WHERE rp_folder = ? AND branch = ?""",
            [rp_folder, branch],
        )
        # Group last 5 changes per pair
        trend_map: dict[tuple[str, str], list[int]] = {}
        mod_count_map: dict[tuple[str, str], int] = {}
        for row in trend_rows:
            pair = (row["character_a"], row["character_b"])
            mod_count_map[pair] = mod_count_map.get(pair, 0) + 1
            if row["rn"] <= 5:
                trend_map.setdefault(pair, []).append(row["change"])

        # 5. Extract card-defined initial_relationships as fallback edges
        # These provide edges even before any trust modifications happen
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

        # 6. Collect all character names from cards + relationships + card edges
        all_names: set[str] = set(card_info.keys())
        for rel in relationships:
            all_names.add(rel.character_a)
            all_names.add(rel.character_b)
        for ce in card_edges:
            all_names.add(ce.from_char)
            all_names.add(ce.to_char)

        # 7. Build nodes
        all_edges_for_trust = list(relationships) + [
            type("_Rel", (), {"character_a": e.from_char, "character_b": e.to_char, "live_trust_score": e.trust_score})
            for e in card_edges
        ]
        nodes: list[RelGraphNode] = []
        for name in sorted(all_names):
            info = card_info.get(name, {})
            char_state = char_states.get(name)
            # Find max trust score for this character across all relationships
            max_trust = 0
            for rel in all_edges_for_trust:
                if rel.character_a == name or rel.character_b == name:
                    if abs(rel.live_trust_score) > abs(max_trust):
                        max_trust = rel.live_trust_score

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

        # 8. Build edges from trust_baselines + card-defined relationships
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

        npc_count = sum(1 for n in nodes if not n.is_player_character)
        return RelationshipGraphResponse(
            nodes=nodes,
            edges=edges,
            metadata=RelGraphMetadata(total_npcs=npc_count, total_edges=len(edges)),
        )

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
        exchange_number = await self._resolve_exchange_number(exchange_id, rp_folder, branch)

        # Resolve current trust through ancestry
        trust_data = await self.resolver.resolve_trust(char_a, char_b, rp_folder, branch)
        current_live = trust_data["live_score"]

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

        if effective_change == 0:
            rel = await self.get_relationship(char_a, char_b, rp_folder, branch)
            if rel:
                return rel
            return RelationshipDetail(
                character_a=char_a, character_b=char_b,
                live_trust_score=current_live, trust_stage=trust_stage(current_live),
            )

        # Ensure trust baseline exists for this pair/branch (case-insensitive match)
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

        if self.diagnostic_logger:
            self.diagnostic_logger.log(
                category="trust",
                event="trust_updated",
                data={
                    "char_a": char_a,
                    "char_b": char_b,
                    "requested_change": change,
                    "effective_change": effective_change,
                    "direction": direction,
                    "old_score": current_live,
                    "new_score": current_live + effective_change,
                    "reason": reason,
                    "rp_folder": rp_folder,
                    "branch": branch,
                    "exchange_id": exchange_id,
                    "bypass_session_cap": bypass_session_cap,
                },
            )

        # Return updated relationship
        rel = await self.get_relationship(char_a, char_b, rp_folder, branch)
        if rel:
            return rel
        return RelationshipDetail(
            character_a=char_a, character_b=char_b,
            live_trust_score=current_live + effective_change,
            trust_stage=trust_stage(current_live + effective_change),
        )

    # ===================================================================
    # Scene Context
    # ===================================================================

    async def get_scene(
        self, rp_folder: str, branch: str = "main"
    ) -> SceneState:
        """Read the current scene context (direct branch query — state snapshotted at branch creation)."""
        row = await latest_scene_state(self.db, rp_folder, branch)
        if row:
            return SceneState(
                location=row.get("location"),
                time_of_day=row.get("time_of_day"),
                mood=row.get("mood"),
                in_story_timestamp=row.get("in_story_timestamp"),
            )
        return SceneState()

    async def update_scene(
        self,
        updates: SceneUpdate,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
    ) -> SceneState:
        """Write a new scene state snapshot (CoW entry).

        Merges updates with current resolved state.
        """
        now = datetime.now(UTC).isoformat()
        exchange_number = await self._resolve_exchange_number(exchange_id, rp_folder, branch)

        # Resolve current state
        current = await self.get_scene(rp_folder, branch)

        new_location = updates.location if updates.location is not None else current.location
        new_time = updates.time_of_day if updates.time_of_day is not None else current.time_of_day
        new_mood = updates.mood if updates.mood is not None else current.mood
        new_ts = updates.in_story_timestamp if updates.in_story_timestamp is not None else current.in_story_timestamp

        # Insert CoW entry
        future = await self.db.enqueue_write(
            """INSERT OR REPLACE INTO scene_state_entries
                   (rp_folder, branch, exchange_number, location, time_of_day,
                    mood, in_story_timestamp, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [rp_folder, branch, exchange_number, new_location, new_time,
             new_mood, new_ts, now],
            priority=PRIORITY_ANALYSIS,
        )
        await future

        return SceneState(
            location=new_location,
            time_of_day=new_time,
            mood=new_mood,
            in_story_timestamp=new_ts,
        )

    # ===================================================================
    # Events
    # ===================================================================

    async def get_events(
        self,
        rp_folder: str,
        branch: str = "main",
        limit: int = 15,
        significance: str | None = None,
        character: str | None = None,
    ) -> list[EventDetail]:
        """Fetch events with optional filters, ordered by created_at DESC."""
        sql = "SELECT * FROM events WHERE rp_folder = ? AND branch = ?"
        params: list = [rp_folder, branch]

        if significance:
            sql += " AND significance = ?"
            params.append(significance)

        if character:
            sql += " AND EXISTS (SELECT 1 FROM json_each(characters) WHERE LOWER(json_each.value) = LOWER(?))"
            params.append(character)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = await self.db.fetch_all(sql, params)
        return [self._row_to_event(row) for row in rows]

    async def add_event(
        self,
        event: str,
        characters: list[str],
        significance: str,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
        in_story_timestamp: str | None = None,
    ) -> EventDetail:
        """Insert a new event and return it."""
        now = datetime.now(UTC).isoformat()
        chars_json = json.dumps(characters)

        future = await self.db.enqueue_write(
            """INSERT INTO events
                   (rp_folder, branch, in_story_timestamp, event, characters,
                    significance, exchange_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [rp_folder, branch, in_story_timestamp, event, chars_json, significance, exchange_id, now],
            priority=PRIORITY_ANALYSIS,
        )
        row_id = await future

        row = await self.db.fetch_one("SELECT * FROM events WHERE id = ?", [row_id])
        return self._row_to_event(row)

    def _row_to_event(self, row: dict) -> EventDetail:
        """Convert a database row to an EventDetail model."""
        return EventDetail(
            id=row["id"],
            in_story_timestamp=row.get("in_story_timestamp"),
            event=row["event"],
            characters=safe_parse_json_list(row.get("characters")),
            significance=row.get("significance"),
            exchange_id=row.get("exchange_id"),
            created_at=row.get("created_at"),
        )

    # ===================================================================
    # Full State Snapshot
    # ===================================================================

    async def get_full_state(
        self, rp_folder: str, branch: str = "main"
    ) -> StateSnapshot:
        """Assemble the full state snapshot for an RP/branch."""
        characters = await self.get_all_characters(rp_folder, branch)
        relationships = await self.get_all_relationships(rp_folder, branch)
        scene = await self.get_scene(rp_folder, branch)
        events = await self.get_events(rp_folder, branch)

        session_row = await self.db.fetch_one(
            """SELECT * FROM sessions
               WHERE rp_folder = ? AND branch = ? AND ended_at IS NULL
               ORDER BY started_at DESC LIMIT 1""",
            [rp_folder, branch],
        )
        session = dict(session_row) if session_row else None

        return StateSnapshot(
            characters=characters,
            relationships=relationships,
            scene=scene,
            events=events,
            session=session,
            branch=branch,
        )
