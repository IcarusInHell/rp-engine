"""Auto-save service — automatically saves RP exchanges when <output> tags are present.

DEPRECATED: Auto-save via <output> tags is deprecated. The preferred flow is for
clients to call save_exchange explicitly after every RP response. This service is
retained for backward compatibility but is disabled by default (AutoSaveConfig.enabled=False).

When get_scene_context is called with last_response containing <output>...</output> tags,
the previous exchange is auto-saved before returning context for the new turn.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rp_engine.database import Database
from rp_engine.services.exchange_writer import ExchangeWriter

logger = logging.getLogger(__name__)

_OUTPUT_RE = re.compile(r"<output>(.*?)</output>", re.DOTALL)


def extract_rp_content(text: str | None) -> str | None:
    """Extract RP narrative from <output> tags. Returns None if no tags found."""
    if not text:
        return None
    match = _OUTPUT_RE.search(text)
    return match.group(1).strip() if match else None


@dataclass
class SessionTracker:
    """Per-session in-memory state for auto-save."""

    session_id: str
    rp_folder: str
    branch: str
    last_user_message: str | None = None
    next_exchange_number: int = 1
    active: bool = True


@dataclass
class AutoSaveResult:
    """Result of a successful auto-save."""

    exchange_id: int
    exchange_number: int
    session_id: str


class AutoSaveManager:
    """Manages automatic exchange saving when <output> tags are present.

    .. deprecated::
        Use explicit ``save_exchange`` calls instead. This manager is disabled
        by default and will be removed in a future major version.
    """

    def __init__(self, db: Database, exchange_writer: ExchangeWriter) -> None:
        self.db = db
        self.exchange_writer = exchange_writer
        self._sessions: dict[str, SessionTracker] = {}
        self._active_rp: bool = False  # Off by default

    def set_active(self, enabled: bool) -> None:
        """Toggle auto-save globally."""
        self._active_rp = enabled
        logger.info("Auto-save %s", "enabled" if enabled else "disabled")

    def is_active(self) -> bool:
        """Check if auto-save is globally enabled."""
        return self._active_rp

    @property
    def session_count(self) -> int:
        """Number of tracked sessions."""
        return len(self._sessions)

    def cleanup_session(self, session_id: str) -> None:
        """Remove tracker for a finished session to free memory."""
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Remove all session trackers (used during shutdown)."""
        self._sessions.clear()

    async def try_auto_save(
        self,
        user_message: str,
        last_response: str | None,
        rp_folder: str,
        branch: str,
        session_id: str | None,
    ) -> AutoSaveResult | None:
        """Attempt to auto-save the previous exchange.

        Returns AutoSaveResult if an exchange was saved, None otherwise.
        """
        # 1. Global toggle check
        if not self._active_rp:
            return None

        # 2. Extract RP content from last_response
        extracted = extract_rp_content(last_response)

        # 3. Resolve session
        resolved_session_id = session_id
        if not resolved_session_id:
            active = await self.db.fetch_one(
                "SELECT id, rp_folder, branch FROM sessions "
                "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            )
            if not active:
                return None
            resolved_session_id = active["id"]
            rp_folder = active["rp_folder"]
            branch = active["branch"]

        # 4. Get or create session tracker
        tracker = await self._get_or_create_tracker(
            resolved_session_id, rp_folder, branch
        )

        # 5. No <output> tags → just record user_message for next turn
        if extracted is None:
            tracker.last_user_message = user_message
            return None

        # 6. First call (no previous user_message) → record and return
        if tracker.last_user_message is None:
            tracker.last_user_message = user_message
            return None

        # 7. Save the exchange
        result = await self._save_exchange(
            tracker=tracker,
            user_message=tracker.last_user_message,
            assistant_response=extracted,
        )

        # 8. Update tracker for next turn
        tracker.last_user_message = user_message

        return result

    async def _get_or_create_tracker(
        self, session_id: str, rp_folder: str, branch: str
    ) -> SessionTracker:
        """Get existing tracker or create a new one, querying DB for exchange count."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Query DB for max exchange number to handle server restarts
        latest = await self.db.fetch_val(
            "SELECT MAX(exchange_number) FROM exchanges WHERE rp_folder=? AND branch=?",
            [rp_folder, branch],
        )
        next_num = (latest or 0) + 1

        tracker = SessionTracker(
            session_id=session_id,
            rp_folder=rp_folder,
            branch=branch,
            next_exchange_number=next_num,
        )
        self._sessions[session_id] = tracker
        logger.info(
            "Auto-save tracker created for session %s (next_exchange=%d)",
            session_id,
            next_num,
        )
        return tracker

    async def _save_exchange(
        self,
        tracker: SessionTracker,
        user_message: str,
        assistant_response: str,
    ) -> AutoSaveResult:
        """Insert exchange into DB and enqueue for analysis."""
        exchange_id, exchange_number = await self.exchange_writer.save_exchange(
            session_id=tracker.session_id,
            rp_folder=tracker.rp_folder,
            branch=tracker.branch,
            user_message=user_message,
            assistant_response=assistant_response,
        )

        tracker.next_exchange_number = exchange_number + 1

        return AutoSaveResult(
            exchange_id=exchange_id,
            exchange_number=exchange_number,
            session_id=tracker.session_id,
        )
