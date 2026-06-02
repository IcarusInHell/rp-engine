"""Async analysis pipeline — orchestrates post-exchange state extraction.

After each exchange save, the pipeline:
1. Calls ResponseAnalyzer (LLM) to extract structured data
2. Updates character state via StateManager
3. Updates trust via StateManager
4. Adds events via StateManager
5. Records new entities in card_gaps table
6. Runs ThreadTracker counter updates
7. Runs TimestampTracker time advancement
8. Records all changes in an analysis manifest for undo/redo
9. Marks exchange analysis_status as completed
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import UTC, datetime

from rp_engine.config import AnalysisConfig, TrustConfig, get_config
from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.analysis import AnalysisResult
from rp_engine.models.state import CharacterUpdate, SceneUpdate
from rp_engine.services.response_analyzer import ResponseAnalyzer
from rp_engine.services.state_manager import StateManager
from rp_engine.services.thread_tracker import ThreadTracker
from rp_engine.services.timestamp_tracker import TimestampTracker
from rp_engine.utils.text import snippet_around_keyword

logger = logging.getLogger(__name__)

# Trust change type mapping
TRUST_CHANGE_TYPES = {"trust_increase", "trust_decrease"}

# Tables tracked in manifest entries — maps table name to the column
# used to identify rows created by a specific exchange.
MANIFEST_TABLES = {
    "character_state_entries": ("exchange_number", "rp_folder", "branch"),
    "scene_state_entries": ("exchange_number", "rp_folder", "branch"),
    "trust_modifications": ("exchange_id", "rp_folder", "branch"),
    "events": ("exchange_id", "rp_folder", "branch"),
    "extracted_memories": ("exchange_id", "rp_folder", "branch"),
    "card_gap_exchanges": ("exchange_number", "rp_folder", "branch"),
    "continuity_warnings": ("current_exchange", "rp_folder", "branch"),
    "thread_counter_entries": ("exchange_number", "rp_folder", "branch"),
    "custom_state_entries": ("exchange_number", "rp_folder", "branch"),
}


class AnalysisPipeline:
    """Async pipeline that processes exchanges sequentially."""

    def __init__(
        self,
        db: Database,
        response_analyzer: ResponseAnalyzer,
        state_manager: StateManager,
        thread_tracker: ThreadTracker,
        timestamp_tracker: TimestampTracker,
        trust_config: TrustConfig | None = None,
        lance_store=None,
        continuity_checker=None,
        custom_state_manager=None,
        analysis_config: AnalysisConfig | None = None,
        knowledge_resolver=None,
    ) -> None:
        self.db = db
        self.response_analyzer = response_analyzer
        self.state_manager = state_manager
        self.thread_tracker = thread_tracker
        self.timestamp_tracker = timestamp_tracker
        self._trust_config_override: TrustConfig | None = trust_config
        self.lance_store = lance_store
        self.continuity_checker = continuity_checker
        self.custom_state_manager = custom_state_manager
        self.knowledge_resolver = knowledge_resolver
        self._analysis_config_override: AnalysisConfig | None = analysis_config
        self._queue: asyncio.Queue[tuple[int, str, str]] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None
        self._running = False
        self.diagnostic_logger = None  # injected by container

    @property
    def trust_config(self) -> TrustConfig:
        """Read trust config dynamically so hot-reloaded changes take effect."""
        override = getattr(self, "_trust_config_override", None)
        if override is not None:
            return override
        return get_config().trust

    @property
    def analysis_config(self) -> AnalysisConfig:
        """Read analysis config dynamically so hot-reloaded changes take effect."""
        override = getattr(self, "_analysis_config_override", None)
        if override is not None:
            return override
        return get_config().analysis

    def start(self) -> None:
        """Start the consumer loop."""
        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop())
        logger.info("Analysis pipeline started")

    async def stop(self) -> None:
        """Stop the consumer loop, drain remaining items."""
        if not self._running:
            return
        self._running = False
        # Put sentinel to wake consumer
        await self._queue.put((-1, "__STOP__", ""))
        if self._consumer_task:
            await self._consumer_task
        logger.info("Analysis pipeline stopped")

    async def enqueue(
        self, exchange_id: int, rp_folder: str, branch: str = "main"
    ) -> None:
        """Enqueue an exchange for analysis."""
        await self._queue.put((exchange_id, rp_folder, branch))
        logger.info("Enqueued exchange %d for analysis", exchange_id)

    async def _consumer_loop(self) -> None:
        """Process exchanges sequentially."""
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            exchange_id, rp_folder, branch = item
            if rp_folder == "__STOP__":
                # Drain remaining
                while not self._queue.empty():
                    try:
                        item = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    eid, rpf, br = item
                    if rpf == "__STOP__":
                        continue
                    await self._process_with_retry(eid, rpf, br)
                break

            await self._process_with_retry(exchange_id, rp_folder, branch)

    async def _process_with_retry(
        self, exchange_id: int, rp_folder: str, branch: str
    ) -> None:
        """Process with exponential backoff retry (2s, 4s, 8s)."""
        delays = [2, 4, 8]
        for attempt in range(len(delays) + 1):
            try:
                result = await self._process_exchange(exchange_id, rp_folder, branch)
                logger.info(
                    "Analysis complete for exchange %d: %d chars updated, %d trust, %d events",
                    exchange_id,
                    result.characters_updated,
                    result.trust_changes,
                    result.events_added,
                )
                return
            except Exception as e:
                if attempt < len(delays):
                    delay = delays[attempt]
                    logger.warning(
                        "Analysis attempt %d failed for exchange %d, retrying in %ds: %s",
                        attempt + 1, exchange_id, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Analysis permanently failed for exchange %d: %s",
                        exchange_id, e,
                    )
                    # Mark as failed
                    await self.db.enqueue_write(
                        "UPDATE exchanges SET analysis_status = 'failed' WHERE id = ?",
                        [exchange_id],
                        priority=PRIORITY_ANALYSIS,
                    )

    async def _process_exchange(
        self, exchange_id: int, rp_folder: str, branch: str
    ) -> AnalysisResult:
        """Process a single exchange through the full pipeline."""
        # 1. Load exchange
        exchange = await self.db.fetch_one(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id]
        )
        if not exchange:
            raise ValueError(f"Exchange {exchange_id} not found")

        user_msg = exchange["user_message"]
        asst_resp = exchange["assistant_response"]
        exchange_number = exchange.get("exchange_number", 0)

        # 1b. Auto-supersede existing active manifest for this exchange
        await self._supersede_existing_manifest(exchange_id, rp_folder, branch)

        # 1c. Create manifest row
        manifest_id = await self._create_manifest(
            exchange_id, exchange_number, rp_folder, branch,
            session_id=exchange.get("session_id"),
        )

        # 2. Call ResponseAnalyzer (LLM)
        custom_schemas = None
        raw_schemas = None  # Keep for step 7c reuse
        if self.custom_state_manager:
            raw_schemas = await self.custom_state_manager.list_schemas(rp_folder)
            custom_schemas = [
                {"name": s.name, "data_type": s.data_type, "belongs_to": s.belongs_to,
                 "description": (s.config or {}).get("description", "")}
                for s in raw_schemas if s.inject_as != "hidden"
            ] or None

        analysis = await self.response_analyzer.analyze(
            exchange_id, user_msg, asst_resp, rp_folder, branch,
            custom_schemas=custom_schemas,
        )

        # Store raw response and model in manifest
        raw_response = getattr(analysis, "_raw_response", None)
        model_used = getattr(analysis, "_model_used", None)
        if raw_response or model_used:
            await self.db.enqueue_write(
                "UPDATE analysis_manifests SET raw_response = ?, model_used = ? WHERE id = ?",
                [raw_response, model_used, manifest_id],
                priority=PRIORITY_ANALYSIS,
            )

        result = AnalysisResult(exchange_id=exchange_id)

        # 3. Apply character state updates
        for char_name, char_state in analysis.story_state.characters.items():
            updates = CharacterUpdate(
                location=char_state.location if char_state.location else None,
                conditions=char_state.conditions if char_state.conditions else None,
                emotional_state=char_state.emotional_state if char_state.emotional_state else None,
            )
            await self.state_manager.update_character(
                char_name, updates, rp_folder, branch, exchange_id=exchange_id
            )
            result.characters_updated += 1

        # 4. Apply scene context updates
        sc = analysis.story_state.scene_context
        if sc.location or sc.time_of_day or sc.mood:
            await self.state_manager.update_scene(
                SceneUpdate(
                    location=sc.location or None,
                    time_of_day=sc.time_of_day or None,
                    mood=sc.mood or None,
                ),
                rp_folder,
                branch,
                exchange_id=exchange_id,
            )

        # 5. Apply trust changes from relationship dynamics.
        await self._apply_relationship_dynamics(
            analysis.relationship_dynamics, rp_folder, branch, exchange_id, result,
        )

        # 6. Add significant events
        for ev in analysis.story_state.significant_events:
            await self.state_manager.add_event(
                event=ev.event,
                characters=ev.characters,
                significance=ev.significance,
                rp_folder=rp_folder,
                branch=branch,
                exchange_id=exchange_id,
            )
            result.events_added += 1

        # 6b. Store extracted memories
        for mem in analysis.memories:
            if mem.description:
                await self.db.enqueue_write(
                    """INSERT INTO extracted_memories
                       (rp_folder, branch, exchange_id, session_id, description,
                        significance, characters, in_story_timestamp, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    [rp_folder, branch, exchange_id, exchange.get("session_id"),
                     mem.description, mem.significance,
                     json.dumps(mem.characters), mem.timestamp],
                    priority=PRIORITY_ANALYSIS,
                )
                result.memories_added += 1

        # 6c. Apply knowledge changes — the analyzer already extracted
        # knowledge_boundaries (no extra LLM call). When a character learns
        # something, mark the matching existing knowledge ref as known-truth.
        # Precision-first: the resolver skips (and logs) ambiguous/no matches
        # rather than risk a wrong write that would leak the wrong reality.
        if self.knowledge_resolver:
            for kb in analysis.knowledge_boundaries:
                try:
                    await self.knowledge_resolver.apply_knowledge_change(kb, rp_folder)
                except Exception as e:
                    logger.warning("Knowledge change apply failed for %r: %s", kb.who, e)

        # 7. Record new entities in card_gaps + gap exchanges
        now = datetime.now(UTC).isoformat()
        combined_text = f"{user_msg}\n{asst_resp}"

        new_entity_groups = [
            (analysis.new_entities.characters, "character"),
            (analysis.new_entities.locations, "location"),
            (analysis.new_entities.concepts, "lore"),
        ]
        for entities, gap_type in new_entity_groups:
            for ent in entities:
                if ent.name:
                    await self._track_new_entity(
                        ent.name, gap_type, rp_folder, branch,
                        exchange_number, combined_text, now,
                    )
                    result.card_gaps_added += 1

        # 7b. Continuity check (optional — disabled by default)
        if self.continuity_checker:
            try:
                cont_warnings = await self.continuity_checker.check_exchange(
                    exchange_id, analysis, rp_folder, branch,
                )
                result.continuity_warnings = len(cont_warnings)
            except Exception as e:
                logger.warning("Continuity check failed for exchange %d: %s", exchange_id, e)

        # 7c. Apply custom state changes
        if self.custom_state_manager and analysis.custom_state_changes:
            await self._apply_custom_state_changes(
                analysis.custom_state_changes, rp_folder, branch, exchange_number,
                schemas=raw_schemas,
            )
            result.custom_state_changes = len(analysis.custom_state_changes)

        # 8. Thread counter updates
        alerts = await self.thread_tracker.update_counters(
            asst_resp, rp_folder, branch, exchange_id
        )
        result.thread_alerts = len(alerts)

        # 9. Timestamp advancement
        ts_result = await self.timestamp_tracker.advance_time(
            asst_resp, rp_folder, branch
        )
        result.timestamp_advanced = ts_result.new_timestamp is not None

        # 10. Collect manifest entries (flush writes first, then query)
        await self._flush_analysis_writes()
        await self._collect_manifest_entries(
            manifest_id, exchange_id, exchange_number, rp_folder, branch,
        )

        # 11. Mark analysis as completed
        future = await self.db.enqueue_write(
            "UPDATE exchanges SET analysis_status = 'completed' WHERE id = ?",
            [exchange_id],
            priority=PRIORITY_ANALYSIS,
        )
        await future

        result.status = "completed"

        self._log_analysis_complete(
            result, exchange_number, rp_folder, branch,
            manifest_id, model_used, raw_response,
        )

        return result

    # ===================================================================
    # State application
    # ===================================================================

    async def _apply_relationship_dynamics(
        self,
        dynamics: list,
        rp_folder: str,
        branch: str,
        exchange_id: int,
        result: AnalysisResult,
    ) -> None:
        """Apply trust changes and emit fallback events from relationship dynamics.

        DIRECTIONAL convention: ``rd.characters`` is ``[truster, trusted]`` — char_a's
        trust toward char_b changes (``update_trust`` stores ``(char_a, char_b)`` =
        ``(truster, trusted)`` with no normalization, and trust READS are now
        direction-aware, so order is load-bearing). The analyzer prompt pins this
        ordering (see response_analyzer "ORDER MATTERS"), but it is LLM-best-effort,
        not guaranteed: a mis-ordered pair records the change against the reverse
        direction. Deterministic write paths (the /relationships/{a}/{b} router and
        card seeding) are exact; this analysis path is the one best-effort site.
        Known limitation, documented for Phase 2.

        Mutates ``result`` in place (matching the inline-counter style of steps 3/6).
        """
        for rd in dynamics:
            if len(rd.characters) < 2:
                continue
            char_a, char_b = rd.characters[0], rd.characters[1]

            if rd.change_type == "trust_increase":
                await self.state_manager.update_trust(
                    char_a, char_b,
                    change=self.trust_config.increase_value,
                    direction="increase",
                    reason=rd.evidence,
                    rp_folder=rp_folder,
                    branch=branch,
                    exchange_id=exchange_id,
                )
                result.trust_changes += 1
            elif rd.change_type == "trust_decrease":
                await self.state_manager.update_trust(
                    char_a, char_b,
                    change=-self.trust_config.decrease_value,
                    direction="decrease",
                    reason=rd.evidence,
                    rp_folder=rp_folder,
                    branch=branch,
                    exchange_id=exchange_id,
                )
                result.trust_changes += 1
            else:
                # Other types (conflict_introduced, etc.) → event only
                await self.state_manager.add_event(
                    event=f"{rd.change_type}: {rd.evidence}",
                    characters=rd.characters,
                    significance="medium",
                    rp_folder=rp_folder,
                    branch=branch,
                    exchange_id=exchange_id,
                )
                result.events_added += 1

    def _log_analysis_complete(
        self,
        result: AnalysisResult,
        exchange_number: int,
        rp_folder: str,
        branch: str,
        manifest_id: int,
        model_used: str | None,
        raw_response: str | None,
    ) -> None:
        """Emit the analysis_complete diagnostic event (no-op when logging disabled)."""
        if not self.diagnostic_logger:
            return
        data = result.model_dump()
        data.update(
            exchange_number=exchange_number,
            rp_folder=rp_folder,
            branch=branch,
            manifest_id=manifest_id,
            model_used=model_used,
        )
        self.diagnostic_logger.log(
            category="analysis",
            event="analysis_complete",
            data=data,
            content={"raw_response": raw_response[:2000] if raw_response else None},
        )

    # ===================================================================
    # Manifest operations
    # ===================================================================

    async def _create_manifest(
        self,
        exchange_id: int,
        exchange_number: int,
        rp_folder: str,
        branch: str,
        session_id: str | None = None,
    ) -> int:
        """Create an analysis manifest row. Returns the manifest id."""
        future = await self.db.enqueue_write(
            """INSERT INTO analysis_manifests
                   (rp_folder, branch, exchange_id, exchange_number, session_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            [rp_folder, branch, exchange_id, exchange_number, session_id,
             datetime.now(UTC).isoformat()],
            priority=PRIORITY_ANALYSIS,
        )
        manifest_id = await future
        logger.debug("Created analysis manifest %d for exchange %d", manifest_id, exchange_id)
        return manifest_id

    async def _supersede_existing_manifest(
        self, exchange_id: int, rp_folder: str, branch: str,
    ) -> None:
        """If an active manifest exists for this exchange, undo it (mark as superseded)."""
        await self._undo_active_manifest_for(
            exchange_id, rp_folder, branch, new_status="superseded",
        )

    async def _undo_active_manifest_for(
        self, exchange_id: int, rp_folder: str, branch: str,
        new_status: str = "superseded",
    ) -> dict[str, int]:
        """Find and undo the active manifest for a given exchange_id. Returns tables_affected."""
        existing = await self.db.fetch_one(
            """SELECT id FROM analysis_manifests
               WHERE exchange_id = ? AND rp_folder = ? AND branch = ? AND status = 'active'""",
            [exchange_id, rp_folder, branch],
        )
        if existing:
            logger.info("Undoing manifest %d for exchange %d (→ %s)",
                        existing["id"], exchange_id, new_status)
            return await self._undo_manifest(existing["id"], new_status=new_status)
        return {}

    async def _flush_analysis_writes(self) -> None:
        """Ensure all queued analysis writes are committed before querying."""
        sentinel = await self.db.enqueue_write(
            "SELECT 1", [], priority=PRIORITY_ANALYSIS,
        )
        await sentinel

    async def _collect_manifest_entries(
        self,
        manifest_id: int,
        exchange_id: int,
        exchange_number: int,
        rp_folder: str,
        branch: str,
    ) -> None:
        """Query all tables for rows created by this exchange and record them as manifest entries."""
        for table_name, (id_col, rp_col, br_col) in MANIFEST_TABLES.items():
            id_value = exchange_id if id_col == "exchange_id" else exchange_number
            rows = await self.db.fetch_all(
                f"SELECT id FROM {table_name} WHERE {id_col} = ? AND {rp_col} = ? AND {br_col} = ?",
                [id_value, rp_folder, branch],
            )
            for row in rows:
                await self.db.enqueue_write(
                    """INSERT INTO analysis_manifest_entries
                           (manifest_id, target_table, target_id, operation)
                       VALUES (?, ?, ?, 'insert')""",
                    [manifest_id, table_name, row["id"]],
                    priority=PRIORITY_ANALYSIS,
                )

    async def _undo_manifest(
        self, manifest_id: int, new_status: str = "undone",
    ) -> dict[str, int]:
        """Undo all state changes tracked by a manifest.

        Returns a dict of {table_name: rows_deleted}.
        """
        entries = await self.db.fetch_all(
            "SELECT target_table, target_id FROM analysis_manifest_entries WHERE manifest_id = ?",
            [manifest_id],
        )

        tables_affected: Counter[str] = Counter()
        for entry in entries:
            table = entry["target_table"]
            target_id = entry["target_id"]

            # Skip resolved card_gaps (gaps where a card was already created)
            if table == "card_gap_exchanges":
                gap = await self.db.fetch_one(
                    """SELECT cg.entity_name FROM card_gap_exchanges cge
                       JOIN card_gaps cg ON cge.entity_name = cg.entity_name
                           AND cge.rp_folder = cg.rp_folder AND cge.branch = cg.branch
                       JOIN story_cards sc ON LOWER(sc.name) = LOWER(cg.entity_name)
                           AND sc.rp_folder = cg.rp_folder
                       WHERE cge.id = ?""",
                    [target_id],
                )
                if gap:
                    continue

            future = await self.db.enqueue_write(
                f"DELETE FROM {table} WHERE id = ?",
                [target_id],
                priority=PRIORITY_ANALYSIS,
            )
            await future
            tables_affected[table] += 1

        # Fix up card_gaps seen_counts after deleting card_gap_exchanges
        if "card_gap_exchanges" in tables_affected:
            await self._fix_card_gap_counts(manifest_id)

        # Mark manifest as undone/superseded
        now = datetime.now(UTC).isoformat()
        future = await self.db.enqueue_write(
            "UPDATE analysis_manifests SET status = ?, undone_at = ? WHERE id = ?",
            [new_status, now, manifest_id],
            priority=PRIORITY_ANALYSIS,
        )
        await future

        # Clear analyzed_at on the exchange
        manifest = await self.db.fetch_one(
            "SELECT exchange_id FROM analysis_manifests WHERE id = ?", [manifest_id],
        )
        if manifest:
            future = await self.db.enqueue_write(
                "UPDATE exchanges SET analysis_status = 'pending' WHERE id = ?",
                [manifest["exchange_id"]],
                priority=PRIORITY_ANALYSIS,
            )
            await future

        logger.info("Undone manifest %d: %s", manifest_id, tables_affected)
        return tables_affected

    async def _fix_card_gap_counts(self, manifest_id: int) -> None:
        """After deleting card_gap_exchanges, recalculate seen_counts for affected entities only."""
        # Find which entity_names were tracked by this manifest's card_gap_exchanges entries
        affected = await self.db.fetch_all(
            """SELECT DISTINCT cge.entity_name, cge.rp_folder, cge.branch
               FROM analysis_manifest_entries ame
               JOIN card_gap_exchanges cge ON cge.id = ame.target_id
               WHERE ame.manifest_id = ? AND ame.target_table = 'card_gap_exchanges'""",
            [manifest_id],
        )
        # If the join fails (rows already deleted), fall back to manifest metadata
        if not affected:
            manifest = await self.db.fetch_one(
                "SELECT rp_folder, branch FROM analysis_manifests WHERE id = ?", [manifest_id],
            )
            if not manifest:
                return
            # Recalc all gaps for this rp/branch as fallback
            affected = await self.db.fetch_all(
                "SELECT entity_name, rp_folder, branch FROM card_gaps WHERE rp_folder = ? AND branch = ?",
                [manifest["rp_folder"], manifest["branch"]],
            )

        for row in affected:
            entity_name = row["entity_name"]
            rp_folder = row["rp_folder"]
            branch = row["branch"]
            count = await self.db.fetch_one(
                """SELECT COUNT(*) as cnt FROM card_gap_exchanges
                   WHERE entity_name = ? AND rp_folder = ? AND branch = ?""",
                [entity_name, rp_folder, branch],
            )
            new_count = count["cnt"] if count else 0
            if new_count == 0:
                await self.db.enqueue_write(
                    "DELETE FROM card_gaps WHERE entity_name = ? AND rp_folder = ? AND branch = ?",
                    [entity_name, rp_folder, branch],
                    priority=PRIORITY_ANALYSIS,
                )
            else:
                await self.db.enqueue_write(
                    "UPDATE card_gaps SET seen_count = ? WHERE entity_name = ? AND rp_folder = ? AND branch = ?",
                    [new_count, entity_name, rp_folder, branch],
                    priority=PRIORITY_ANALYSIS,
                )

    async def undo_exchange_analysis(
        self, exchange_number: int, rp_folder: str, branch: str,
        cascade: bool = True,
    ) -> tuple[int, dict[str, int], list[int]]:
        """Undo analysis for an exchange. Returns (manifest_id, tables_affected, cascade_list).

        If cascade=True, re-enqueues analysis for subsequent exchanges up to
        undo_cascade_depth.
        """
        manifest = await self.db.fetch_one(
            """SELECT id, exchange_id FROM analysis_manifests
               WHERE rp_folder = ? AND branch = ? AND exchange_number = ? AND status = 'active'
               ORDER BY id DESC LIMIT 1""",
            [rp_folder, branch, exchange_number],
        )
        if not manifest:
            return 0, {}, []

        tables_affected = await self._undo_manifest(manifest["id"])

        cascade_list: list[int] = []
        if cascade:
            depth = self.analysis_config.undo_cascade_depth
            subsequent = await self.db.fetch_all(
                """SELECT id, exchange_number FROM exchanges
                   WHERE rp_folder = ? AND branch = ?
                     AND exchange_number > ? AND exchange_number <= ?
                     AND analysis_status = 'completed'
                   ORDER BY exchange_number ASC""",
                [rp_folder, branch, exchange_number, exchange_number + depth],
            )
            for ex in subsequent:
                await self._undo_active_manifest_for(
                    ex["id"], rp_folder, branch, new_status="superseded",
                )
                await self.enqueue(ex["id"], rp_folder, branch)
                cascade_list.append(ex["exchange_number"])

        return manifest["id"], tables_affected, cascade_list

    async def get_manifest(
        self, exchange_number: int, rp_folder: str, branch: str,
    ) -> dict | None:
        """Get the active manifest for an exchange with its entries."""
        manifest = await self.db.fetch_one(
            """SELECT * FROM analysis_manifests
               WHERE rp_folder = ? AND branch = ? AND exchange_number = ?
                 AND status = 'active'
               ORDER BY id DESC LIMIT 1""",
            [rp_folder, branch, exchange_number],
        )
        if not manifest:
            return None

        entries = await self.db.fetch_all(
            "SELECT * FROM analysis_manifest_entries WHERE manifest_id = ?",
            [manifest["id"]],
        )

        entry_counts = dict(Counter(e["target_table"] for e in entries))

        return {
            **dict(manifest),
            "entries": [dict(e) for e in entries],
            "entry_counts": entry_counts,
        }

    async def get_manifests(
        self, rp_folder: str, branch: str,
    ) -> list[dict]:
        """List all manifests for an RP/branch."""
        manifests = await self.db.fetch_all(
            """SELECT m.*, COUNT(e.id) as entry_count
               FROM analysis_manifests m
               LEFT JOIN analysis_manifest_entries e ON e.manifest_id = m.id
               WHERE m.rp_folder = ? AND m.branch = ?
               GROUP BY m.id
               ORDER BY m.exchange_number DESC""",
            [rp_folder, branch],
        )
        return [dict(m) for m in manifests]

    async def preview_undo(
        self, exchange_number: int, rp_folder: str, branch: str,
    ) -> dict | None:
        """Preview what undoing an exchange's analysis would remove."""
        manifest = await self.db.fetch_one(
            """SELECT id, exchange_number FROM analysis_manifests
               WHERE rp_folder = ? AND branch = ? AND exchange_number = ? AND status = 'active'
               ORDER BY id DESC LIMIT 1""",
            [rp_folder, branch, exchange_number],
        )
        if not manifest:
            return None

        entries = await self.db.fetch_all(
            "SELECT target_table, target_id FROM analysis_manifest_entries WHERE manifest_id = ?",
            [manifest["id"]],
        )

        tables_affected = dict(Counter(e["target_table"] for e in entries))

        # Find cascade exchanges
        depth = self.analysis_config.undo_cascade_depth
        subsequent = await self.db.fetch_all(
            """SELECT exchange_number FROM exchanges
               WHERE rp_folder = ? AND branch = ?
                 AND exchange_number > ? AND exchange_number <= ?
                 AND analysis_status = 'completed'
               ORDER BY exchange_number ASC""",
            [rp_folder, branch, exchange_number, exchange_number + depth],
        )

        return {
            "exchange_number": exchange_number,
            "manifest_id": manifest["id"],
            "entries_count": len(entries),
            "tables_affected": tables_affected,
            "cascade_exchanges": [e["exchange_number"] for e in subsequent],
        }

    # ===================================================================
    # Custom state changes
    # ===================================================================

    async def _apply_custom_state_changes(
        self,
        changes: list,
        rp_folder: str,
        branch: str,
        exchange_number: int,
        schemas: list | None = None,
    ) -> None:
        """Apply extracted custom state changes via CustomStateManager."""
        if schemas is None:
            schemas = await self.custom_state_manager.list_schemas(rp_folder)
        schema_by_name = {s.name.lower(): s for s in schemas}

        for change in changes:
            schema = schema_by_name.get(change.schema_name.lower())
            if not schema:
                logger.warning("Unknown custom state schema: %s", change.schema_name)
                continue

            entity_id = change.entity or None

            current = await self.custom_state_manager.get_value(
                schema.id, rp_folder, branch, entity_id=entity_id
            )
            current_value = current.value if current else None

            new_value = self._compute_custom_state_value(
                current_value, change.action, change.value, schema.data_type, schema.config
            )
            if new_value is not None:
                await self.custom_state_manager.set_value(
                    schema_id=schema.id,
                    value=new_value,
                    rp_folder=rp_folder,
                    branch=branch,
                    entity_id=entity_id,
                    exchange_number=exchange_number,
                    changed_by="analysis",
                    reason=f"Extracted from exchange {exchange_number}",
                )

    @staticmethod
    def _compute_custom_state_value(
        current,
        action: str,
        new_value,
        data_type: str,
        config: dict | None,
    ):
        """Compute the new value for a custom state change. Returns None if invalid."""
        if data_type == "number":
            try:
                num = float(new_value) if new_value is not None else 0
            except (TypeError, ValueError):
                return None
            if action == "set":
                result = num
            elif action == "add":
                result = (float(current) if current else 0) + num
            elif action == "subtract":
                result = (float(current) if current else 0) - num
            else:
                return None
            if config:
                if "min" in config:
                    result = max(result, config["min"])
                if "max" in config:
                    result = min(result, config["max"])
            return result

        elif data_type == "text":
            if action == "set":
                return str(new_value) if new_value is not None else ""
            return None

        elif data_type == "list":
            current_list = list(current) if isinstance(current, list) else []
            if action == "add":
                if new_value not in current_list:
                    current_list.append(new_value)
                return current_list
            elif action == "remove":
                if isinstance(new_value, str):
                    lower_val = new_value.lower()
                    current_list = [item for item in current_list
                                    if not (isinstance(item, str) and item.lower() == lower_val)]
                else:
                    current_list = [item for item in current_list if item != new_value]
                return current_list
            elif action == "set":
                return list(new_value) if isinstance(new_value, list) else [new_value]
            return None

        elif data_type == "object":
            if action == "set":
                return new_value if isinstance(new_value, dict) else None
            return None

        return None

    # ===================================================================
    # Card gap helpers
    # ===================================================================

    async def _track_new_entity(
        self,
        name: str,
        gap_type: str,
        rp_folder: str,
        branch: str,
        exchange_number: int,
        combined_text: str,
        now: str,
    ) -> None:
        """Record one newly-mentioned entity as a card gap + gap exchange.

        ``_upsert_card_gap`` and ``_record_gap_exchange`` are always called together
        with the same arguments from step 7; this consolidates the pair. Both remain
        separately callable for callers that want one without the other.
        """
        await self._upsert_card_gap(name, gap_type, rp_folder, branch, now)
        await self._record_gap_exchange(
            name, rp_folder, branch, exchange_number, combined_text, now,
        )

    async def _upsert_card_gap(
        self, entity_name: str, suggested_type: str, rp_folder: str, branch: str, now: str
    ) -> None:
        """Insert or increment seen_count for a card gap."""
        await self.db.enqueue_write(
            """INSERT INTO card_gaps (entity_name, rp_folder, branch, suggested_type, seen_count, first_seen, last_seen)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(entity_name, rp_folder, branch)
               DO UPDATE SET seen_count = seen_count + 1, last_seen = excluded.last_seen""",
            [entity_name, rp_folder, branch, suggested_type, now, now],
            priority=PRIORITY_ANALYSIS,
        )

    async def _record_gap_exchange(
        self,
        entity_name: str,
        rp_folder: str,
        branch: str,
        exchange_number: int,
        combined_text: str,
        now: str,
    ) -> None:
        """Record which exchange mentioned a gap entity, with a text snippet."""
        chunk = snippet_around_keyword(combined_text, entity_name)
        mention_type = self._classify_mention_type(entity_name, combined_text)
        await self.db.enqueue_write(
            """INSERT OR IGNORE INTO card_gap_exchanges
                   (entity_name, rp_folder, branch, exchange_number, chunk_text, mention_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [entity_name, rp_folder, branch, exchange_number, chunk, mention_type, now],
            priority=PRIORITY_ANALYSIS,
        )

    @staticmethod
    def _classify_mention_type(entity_name: str, text: str) -> str:
        """Classify whether the entity is a primary focus or peripheral mention.

        Primary: entity appears 3+ times or in the first sentence.
        """
        lower_text = text.lower()
        lower_name = entity_name.lower()
        count = lower_text.count(lower_name)
        if count >= 3:
            return "primary"
        first_period = text.find(".")
        first_chunk = text[:first_period] if first_period > 0 else text[:200]
        if lower_name in first_chunk.lower():
            return "primary"
        return "peripheral"
