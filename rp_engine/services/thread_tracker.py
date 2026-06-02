"""Plot thread counter management — no LLM, pure DB + text matching.

Ported from .claude/hooks/thread-tracker.js. Tracks how many exchanges
have passed since each plot thread was last mentioned. Fires alerts when
counters cross configurable thresholds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.analysis import ThreadDetail, ThreadEvidence
from rp_engine.models.context import ThreadAlert
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_list
from rp_engine.utils.stemmer import stem, stem_tokens
from rp_engine.utils.text import snippet_around_keyword

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {"gentle": 5, "moderate": 10, "strong": 15}


class ThreadTracker:
    """Track plot thread mention counters and fire threshold alerts."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def update_counters(
        self,
        response_text: str,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
    ) -> list[ThreadAlert]:
        """Update all thread counters and return any threshold alerts.

        Uses CoW thread_counter_entries: one row per exchange for rewind support.
        Also updates the thread_counters cache table for fast reads.

        For each active thread:
        - If ANY keyword, related character, or related location appears
          in response_text → reset counter to 0. Single-word keywords match
          morphology-aware via the shared stemmer (a ``sword`` keyword matches
          ``swords``); characters/locations match exact (proper nouns).
        - Otherwise → increment counter by 1
        - Check thresholds and generate alerts
        """
        threads = await self._load_active_threads(rp_folder)
        if not threads:
            return []

        counters = await self._load_counters(rp_folder, branch)
        text_lower = response_text.lower()
        # Stemmed token set, computed once and reused per thread. Single-word
        # keyword matching goes through this so authored keywords match inflected
        # RP text (consistency invariant: both sides stemmed — see _check_mention).
        text_stems = stem_tokens(response_text)
        now = datetime.now(UTC).isoformat()
        alerts: list[ThreadAlert] = []

        # Resolve exchange_number
        exchange_number = 0
        if exchange_id is not None:
            row = await self.db.fetch_one(
                "SELECT exchange_number FROM exchanges WHERE id = ?", [exchange_id]
            )
            if row:
                exchange_number = row["exchange_number"]

        for thread in threads:
            thread_id = thread["id"]
            prev_counter = counters.get(thread_id, thread.get("current_counter") or 0)

            mentioned, matched_term = self._check_mention(thread, text_lower, text_stems)

            if mentioned:
                new_counter = 0
                direction = "reset"
                logger.debug("Thread %r: mentioned (%s), reset to 0", thread_id, matched_term)
            else:
                new_counter = prev_counter + 1
                direction = "increment"
                logger.debug(
                    "Thread %r: not mentioned, %d → %d",
                    thread_id, prev_counter, new_counter,
                )

            # Record evidence
            chunk_text = None
            if mentioned and matched_term:
                chunk_text = snippet_around_keyword(response_text, matched_term, window=100)
            await self._record_evidence(
                thread_id, rp_folder, branch, exchange_number,
                matched_term, chunk_text, prev_counter, new_counter, direction, now,
            )

            # Write CoW entry (append-only, enables rewind)
            future = await self.db.enqueue_write(
                """INSERT INTO thread_counter_entries
                       (thread_id, rp_folder, branch, exchange_number,
                        counter_value, mentioned, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [thread_id, rp_folder, branch, exchange_number,
                 new_counter, 1 if mentioned else 0, now],
                priority=PRIORITY_ANALYSIS,
            )
            await future

            # Also update cache table for fast reads
            future = await self.db.enqueue_write(
                """INSERT INTO thread_counters (thread_id, rp_folder, branch, current_counter, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(thread_id, rp_folder, branch)
                   DO UPDATE SET current_counter = excluded.current_counter,
                                 updated_at = excluded.updated_at""",
                [thread_id, rp_folder, branch, new_counter, now],
                priority=PRIORITY_ANALYSIS,
            )
            await future

            # Check thresholds
            thresholds = self._parse_thresholds(thread.get("thresholds"))
            consequences = self._parse_consequences(thread.get("consequences"))
            alert = self._check_threshold(
                thread_id, thread["name"], new_counter, thresholds, consequences
            )
            if alert:
                alerts.append(alert)

        return alerts

    async def get_all_threads(
        self, rp_folder: str, branch: str = "main"
    ) -> list[ThreadDetail]:
        """Load all threads with their current counter values."""
        threads = await self._load_active_threads(rp_folder, include_resolved=True)
        counters = await self._load_counters(rp_folder, branch)

        result: list[ThreadDetail] = []
        for t in threads:
            thread_id = t["id"]
            result.append(
                ThreadDetail(
                    thread_id=thread_id,
                    name=t["name"],
                    thread_type=t.get("thread_type"),
                    priority=t.get("priority"),
                    status=t.get("status", "active"),
                    keywords=safe_parse_json_list(t.get("keywords")),
                    current_counter=counters.get(thread_id, 0),
                    thresholds=self._parse_thresholds(t.get("thresholds")),
                    consequences=self._parse_consequences(t.get("consequences")),
                    related_characters=safe_parse_json_list(
                        t.get("related_characters")
                    ),
                )
            )
        return result

    async def get_thread(
        self, thread_id: str, rp_folder: str, branch: str = "main",
        include_evidence: bool = True,
    ) -> ThreadDetail | None:
        """Load a single thread with its current counter and evidence trail."""
        row = await self.db.fetch_one(
            "SELECT * FROM plot_threads WHERE id = ? AND rp_folder = ?",
            [thread_id, rp_folder],
        )
        if not row:
            return None

        counter_row = await self.db.fetch_one(
            "SELECT current_counter FROM thread_counters "
            "WHERE thread_id = ? AND rp_folder = ? AND branch = ?",
            [thread_id, rp_folder, branch],
        )
        counter = counter_row["current_counter"] if counter_row else 0

        evidence: list[ThreadEvidence] = []
        if include_evidence:
            evidence = await self.get_thread_evidence(thread_id, rp_folder, branch)

        return ThreadDetail(
            thread_id=row["id"],
            name=row["name"],
            thread_type=row.get("thread_type"),
            priority=row.get("priority"),
            status=row.get("status", "active"),
            keywords=safe_parse_json_list(row.get("keywords")),
            current_counter=counter,
            thresholds=self._parse_thresholds(row.get("thresholds")),
            consequences=self._parse_consequences(row.get("consequences")),
            related_characters=safe_parse_json_list(row.get("related_characters")),
            evidence=evidence,
        )

    async def get_alerts(
        self, rp_folder: str, branch: str = "main"
    ) -> list[ThreadAlert]:
        """Check all active threads and return any current alerts."""
        threads = await self._load_active_threads(rp_folder)
        counters = await self._load_counters(rp_folder, branch)
        alerts: list[ThreadAlert] = []

        for t in threads:
            thread_id = t["id"]
            counter = counters.get(thread_id, 0)
            thresholds = self._parse_thresholds(t.get("thresholds"))
            consequences = self._parse_consequences(t.get("consequences"))
            alert = self._check_threshold(
                thread_id, t["name"], counter, thresholds, consequences
            )
            if alert:
                alerts.append(alert)

        return alerts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_active_threads(
        self, rp_folder: str, include_resolved: bool = False
    ) -> list[dict]:
        """Load threads from plot_threads table."""
        if include_resolved:
            return await self.db.fetch_all(
                "SELECT * FROM plot_threads WHERE rp_folder = ?", [rp_folder]
            )
        return await self.db.fetch_all(
            "SELECT * FROM plot_threads WHERE rp_folder = ? AND status != 'resolved'",
            [rp_folder],
        )

    async def _load_counters(
        self, rp_folder: str, branch: str
    ) -> dict[str, int]:
        """Load current counter values as {thread_id: counter}.

        Prefers CoW thread_counter_entries (latest per thread). Falls back
        to the thread_counters cache table.
        """
        # Try CoW entries first (latest per thread_id)
        rows = await self.db.fetch_all(
            """SELECT tce.thread_id, tce.counter_value
               FROM thread_counter_entries tce
               INNER JOIN (
                   SELECT thread_id, MAX(exchange_number) as max_ex
                   FROM thread_counter_entries
                   WHERE rp_folder = ? AND branch = ?
                   GROUP BY thread_id
               ) latest ON tce.thread_id = latest.thread_id
                   AND tce.exchange_number = latest.max_ex
               WHERE tce.rp_folder = ? AND tce.branch = ?""",
            [rp_folder, branch, rp_folder, branch],
        )
        if rows:
            return {r["thread_id"]: r["counter_value"] for r in rows}

        # Fall back to cache table
        rows = await self.db.fetch_all(
            "SELECT thread_id, current_counter FROM thread_counters "
            "WHERE rp_folder = ? AND branch = ?",
            [rp_folder, branch],
        )
        return {r["thread_id"]: r["current_counter"] for r in rows}

    @staticmethod
    def _check_mention(
        thread: dict, text_lower: str, text_stems: set[str]
    ) -> tuple[bool, str | None]:
        """Check if any keyword, character, or location appears in text.

        Keyword matching is morphology-aware via the shared stemmer, mirroring
        ``entity_extractor``: a **single-word** keyword is stemmed and matched
        against ``text_stems`` (so a ``sword`` keyword matches ``swords``, and
        an inflected ``running`` keyword matches ``runs``); **multi-word**
        keyword phrases stay exact substring matches (stemming a phrase is
        meaningless). Related characters and locations are proper nouns and are
        matched exactly — never stemmed (``Ross`` must not become ``Ros``).

        Consistency invariant: both sides go through ``stem`` — the authored
        keyword here and the runtime text via ``stem_tokens`` in the caller. A
        one-sided stem silently breaks matching.

        Returns (mentioned, matched_term) where matched_term is the first match found.
        """
        # Keywords — stem single words; multi-word phrases stay exact substring.
        for kw in safe_parse_json_list(thread.get("keywords")):
            kw_norm = kw.lower().strip()
            if not kw_norm:
                continue
            if " " in kw_norm:
                if kw_norm in text_lower:
                    return True, kw
            elif stem(kw_norm) in text_stems:
                return True, kw

        # Related characters (proper nouns — exact)
        for char in safe_parse_json_list(thread.get("related_characters")):
            if char.lower() in text_lower:
                return True, char

        # Related locations (proper nouns — exact)
        for loc in safe_parse_json_list(thread.get("related_locations")):
            if loc.lower() in text_lower:
                return True, loc

        return False, None

    @staticmethod
    def _check_threshold(
        thread_id: str,
        name: str,
        counter: int,
        thresholds: dict[str, int],
        consequences: dict[str, str],
    ) -> ThreadAlert | None:
        """Check if counter crosses any threshold. Returns highest matched."""
        level = None
        threshold = 0

        strong = thresholds.get("strong", 15)
        moderate = thresholds.get("moderate", 10)
        gentle = thresholds.get("gentle", 5)

        if counter >= strong:
            level = "strong"
            threshold = strong
        elif counter >= moderate:
            level = "moderate"
            threshold = moderate
        elif counter >= gentle:
            level = "gentle"
            threshold = gentle

        if not level:
            return None

        consequence = consequences.get(level, "")

        return ThreadAlert(
            thread_id=thread_id,
            name=name,
            level=level,
            counter=counter,
            threshold=threshold,
            consequence=consequence,
        )

    @staticmethod
    def _parse_thresholds(raw: str | dict | None) -> dict[str, int]:
        """Parse thresholds JSON or return defaults."""
        parsed = safe_parse_json(raw)
        if parsed:
            return {k: int(v) for k, v in parsed.items() if k in ("gentle", "moderate", "strong")}
        return dict(DEFAULT_THRESHOLDS)

    @staticmethod
    def _parse_consequences(raw: str | dict | None) -> dict[str, str]:
        """Parse consequences JSON."""
        parsed = safe_parse_json(raw)
        if not isinstance(parsed, dict):
            return {}
        return {k: str(v) for k, v in parsed.items()}

    async def get_thread_evidence(
        self,
        thread_id: str,
        rp_folder: str,
        branch: str = "main",
        limit: int = 20,
    ) -> list[ThreadEvidence]:
        """Get evidence trail for a specific thread, ordered by exchange number."""
        rows = await self.db.fetch_all(
            """SELECT thread_id, exchange_number, keyword_matched, chunk_text,
                      counter_before, counter_after, direction, created_at
               FROM thread_evidence
               WHERE thread_id = ? AND rp_folder = ? AND branch = ?
               ORDER BY exchange_number ASC
               LIMIT ?""",
            [thread_id, rp_folder, branch, limit],
        )
        return [ThreadEvidence(**dict(r)) for r in rows]

    async def get_recent_evidence(
        self,
        rp_folder: str,
        branch: str = "main",
        limit: int = 10,
    ) -> list[ThreadEvidence]:
        """Get recent evidence across all threads."""
        rows = await self.db.fetch_all(
            """SELECT thread_id, exchange_number, keyword_matched, chunk_text,
                      counter_before, counter_after, direction, created_at
               FROM thread_evidence
               WHERE rp_folder = ? AND branch = ?
               ORDER BY exchange_number DESC
               LIMIT ?""",
            [rp_folder, branch, limit],
        )
        return [ThreadEvidence(**dict(r)) for r in rows]

    async def _record_evidence(
        self,
        thread_id: str,
        rp_folder: str,
        branch: str,
        exchange_number: int,
        keyword_matched: str | None,
        chunk_text: str | None,
        counter_before: int,
        counter_after: int,
        direction: str,
        created_at: str,
    ) -> None:
        """Record evidence for a thread counter change."""
        await self.db.enqueue_write(
            """INSERT INTO thread_evidence
                   (thread_id, rp_folder, branch, exchange_number,
                    chunk_text, keyword_matched, counter_before,
                    counter_after, direction, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [thread_id, rp_folder, branch, exchange_number,
             chunk_text, keyword_matched, counter_before,
             counter_after, direction, created_at],
            priority=PRIORITY_ANALYSIS,
        )


    async def set_counter(
        self,
        thread_id: str,
        counter_value: int,
        rp_folder: str,
        branch: str = "main",
        exchange_number: int = 0,
    ) -> None:
        """Manually set a thread counter, writing to both CoW entries and cache.

        This is the correct path for manual counter updates via the API,
        ensuring both thread_counter_entries (CoW) and thread_counters (cache)
        stay in sync.
        """
        now = datetime.now(UTC).isoformat()

        # Write CoW entry
        future = await self.db.enqueue_write(
            """INSERT INTO thread_counter_entries
                   (thread_id, rp_folder, branch, exchange_number,
                    counter_value, mentioned, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            [thread_id, rp_folder, branch, exchange_number,
             counter_value, now],
            priority=PRIORITY_ANALYSIS,
        )
        await future

        # Update cache table
        future = await self.db.enqueue_write(
            """INSERT INTO thread_counters (thread_id, rp_folder, branch, current_counter, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(thread_id, rp_folder, branch)
               DO UPDATE SET current_counter = excluded.current_counter,
                             updated_at = excluded.updated_at""",
            [thread_id, rp_folder, branch, counter_value, now],
            priority=PRIORITY_ANALYSIS,
        )
        await future
