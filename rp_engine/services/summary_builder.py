"""Summary builder — generates narrative session summaries from key moments.

Finds the session's most significant events (trust changes, thread progressions,
state changes) and sends them to an LLM for narrative summary generation.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from rp_engine.config import get_config
from rp_engine.database import Database
from rp_engine.models.session import SessionSummary
from rp_engine.services.lance_store import LanceStore
from rp_engine.services.llm_client import LLMClient
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_array
from rp_engine.utils.trust import fetch_thread_progress

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """\
Summarize this RP session based on the key moments below. Write a concise narrative
summary (3-5 paragraphs) that captures what happened. Include:
- Major character interactions and their outcomes
- Trust changes and why they happened
- Plot thread progression
- Unresolved questions or new developments

Write in past tense, narrative style. Do not use bullet points or headers.

Key Moments:
{moments}

Session Stats:
- Exchanges: {exchange_count}
- Trust changes: {trust_summary}
- Active threads: {thread_summary}
"""


class SummaryBuilder:
    """Generates narrative session summaries from key moments."""

    def __init__(
        self,
        db: Database,
        lance_store: LanceStore,
        llm_client: LLMClient,
    ) -> None:
        self.db = db
        self.lance_store = lance_store
        self.llm_client = llm_client

    async def build_session_summary(
        self,
        session_id: str,
        rp_folder: str,
        branch: str = "main",
    ) -> SessionSummary:
        """Build a narrative summary from a session's key moments."""
        # Gather key moments
        moments = await self._gather_key_moments(session_id, rp_folder, branch)

        # Get session stats
        exchange_count = await self.db.fetch_val(
            "SELECT COUNT(*) FROM exchanges WHERE session_id = ?",
            [session_id],
        ) or 0

        trust_summary = await self._get_trust_summary(session_id)
        thread_summary = await self._get_thread_summary(rp_folder, branch)

        # Format moments for LLM
        formatted_moments = self._format_moments(moments)

        # Generate narrative via LLM
        if exchange_count == 0:
            narrative = "This session had no exchanges recorded."
        elif not moments:
            narrative = "This was a quiet session with no significant events detected."
        else:
            prompt = _SUMMARY_PROMPT.format(
                moments=formatted_moments,
                exchange_count=exchange_count,
                trust_summary=trust_summary,
                thread_summary=thread_summary,
            )
            response = await self.llm_client.generate(
                messages=[{"role": "user", "content": prompt}],
                model=self.llm_client.models.card_generation,
                temperature=get_config().llm.temperatures.summary,
                max_tokens=1500,
            )
            narrative = response.content

        now = datetime.now(UTC).isoformat()

        # Store in DB
        await self._store_summary(session_id, rp_folder, branch, narrative, moments, now)

        return SessionSummary(
            session_id=session_id,
            rp_folder=rp_folder,
            branch=branch,
            narrative_summary=narrative,
            key_moments=moments,
            generated_at=now,
        )

    async def get_summary(self, session_id: str) -> SessionSummary | None:
        """Retrieve a stored summary."""
        row = await self.db.fetch_one(
            "SELECT * FROM session_summaries WHERE session_id = ?",
            [session_id],
        )
        if not row:
            return None
        key_moments = safe_parse_json_array(row.get("key_moments"))
        return SessionSummary(
            session_id=row["session_id"],
            rp_folder=row["rp_folder"],
            branch=row["branch"],
            narrative_summary=row["narrative_summary"],
            key_moments=key_moments,
            generated_at=row["generated_at"],
        )

    async def _gather_key_moments(
        self, session_id: str, rp_folder: str, branch: str
    ) -> list[dict]:
        """Collect and rank significant events from the session."""
        moments: list[dict] = []

        # Trust modifications
        trust_rows = await self.db.fetch_all(
            """SELECT tm.change, tm.reason, tm.character_a, tm.character_b,
                      ex.exchange_number, ex.user_message, ex.assistant_response
               FROM trust_modifications tm
               JOIN exchanges ex ON tm.exchange_id = ex.id
               WHERE ex.session_id = ?
               ORDER BY ABS(tm.change) DESC""",
            [session_id],
        )
        for row in trust_rows:
            moments.append({
                "type": "trust_change",
                "weight": 10 + abs(row["change"]) * 2,
                "npc": row["character_b"],
                "delta": row["change"],
                "reason": row["reason"] or "",
                "exchange_number": row["exchange_number"],
                "snippet": (row["assistant_response"] or "")[:300],
            })

        # Thread counter changes
        thread_rows = await self.db.fetch_all(
            """SELECT te.thread_id, pt.name, te.counter_before, te.counter_after,
                      te.exchange_number, te.chunk_text
               FROM thread_evidence te
               JOIN plot_threads pt ON te.thread_id = pt.id AND pt.rp_folder = te.rp_folder
               JOIN exchanges ex ON te.exchange_number = ex.exchange_number
                   AND ex.rp_folder = ? AND ex.branch = ?
               WHERE ex.session_id = ?
               ORDER BY ABS(te.counter_after - te.counter_before) DESC""",
            [rp_folder, branch, session_id],
        )
        for row in thread_rows:
            delta = (row["counter_after"] or 0) - (row["counter_before"] or 0)
            moments.append({
                "type": "thread_change",
                "weight": 8 + abs(delta) * 2,
                "thread_name": row["name"],
                "delta": delta,
                "exchange_number": row["exchange_number"],
                "snippet": row.get("chunk_text") or "",
            })

        # Significant events
        event_rows = await self.db.fetch_all(
            """SELECT e.event, ex.exchange_number,
                      ex.assistant_response
               FROM events e
               JOIN exchanges ex ON e.exchange_id = ex.id
               WHERE ex.session_id = ?
               ORDER BY e.created_at""",
            [session_id],
        )
        for row in event_rows:
            moments.append({
                "type": "event",
                "weight": 5,
                "event": row["event"],
                "event_type": "general",
                "exchange_number": row["exchange_number"],
                "snippet": (row["assistant_response"] or "")[:300],
            })

        # Sort by weight descending, cap at 8
        moments.sort(key=lambda m: m["weight"], reverse=True)
        return moments[:8]

    def _format_moments(self, moments: list[dict]) -> str:
        """Format moments for the LLM prompt."""
        parts: list[str] = []
        for i, m in enumerate(moments, 1):
            if m["type"] == "trust_change":
                parts.append(
                    f"{i}. [Trust] {m['npc']}: {m['delta']:+d} — {m['reason']}\n"
                    f"   Context: {m['snippet']}"
                )
            elif m["type"] == "thread_change":
                parts.append(
                    f"{i}. [Thread] {m['thread_name']}: {m['delta']:+d}\n"
                    f"   Context: {m['snippet']}"
                )
            elif m["type"] == "event":
                parts.append(
                    f"{i}. [Event] {m['event']}\n"
                    f"   Context: {m['snippet']}"
                )
        return "\n\n".join(parts) if parts else "No significant moments detected."

    async def _get_trust_summary(self, session_id: str) -> str:
        """One-line trust change summary."""
        rows = await self.db.fetch_all(
            """SELECT tm.character_b, SUM(tm.change) as total
               FROM trust_modifications tm
               JOIN exchanges ex ON tm.exchange_id = ex.id
               WHERE ex.session_id = ?
               GROUP BY tm.character_b""",
            [session_id],
        )
        if not rows:
            return "No trust changes"
        return ", ".join(f"{r['character_b']} ({r['total']:+d})" for r in rows)

    async def _get_thread_summary(self, rp_folder: str, branch: str) -> str:
        """One-line active thread summary."""
        rows = await fetch_thread_progress(self.db, rp_folder, branch)
        if not rows:
            return "No active threads"
        parts = []
        for r in rows:
            counter = r["current_counter"]
            # thresholds is a JSON object like {"gentle": 5, "moderate": 10, "strong": 15}
            raw = r.get("thresholds")
            if raw:
                th = safe_parse_json(raw)
                max_th = max(th.values()) if th else "?"
            else:
                max_th = "?"
            parts.append(f"{r['name']} ({counter}/{max_th})")
        return ", ".join(parts)

    async def _store_summary(
        self,
        session_id: str,
        rp_folder: str,
        branch: str,
        narrative: str,
        moments: list[dict],
        generated_at: str,
    ) -> None:
        """Store/update summary in the database."""
        future = await self.db.enqueue_write(
            """INSERT OR REPLACE INTO session_summaries
               (session_id, rp_folder, branch, narrative_summary, key_moments, generated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [session_id, rp_folder, branch, narrative, json.dumps(moments), generated_at],
        )
        await future
