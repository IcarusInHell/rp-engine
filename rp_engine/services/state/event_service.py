"""Event domain service — timeline events for an RP/branch."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.state import EventDetail
from rp_engine.utils.json_helpers import safe_parse_json_list


class EventService:
    """Read and append timeline events, scoped by branch."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
