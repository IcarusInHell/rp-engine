"""Scene domain service — current scene context for an RP/branch."""

from __future__ import annotations

from datetime import UTC, datetime

from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.context import SceneState
from rp_engine.models.state import SceneUpdate
from rp_engine.services.state_entry_resolver import latest_scene_state
from rp_engine.utils.state_helpers import resolve_exchange_number


class SceneService:
    """Read and update the CoW-snapshotted scene state, scoped by branch."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
        exchange_number = await resolve_exchange_number(self.db, exchange_id, rp_folder, branch)

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
