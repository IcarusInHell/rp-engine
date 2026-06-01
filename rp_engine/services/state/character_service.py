"""Character domain service — character cards, runtime state, and the ledger."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from rp_engine.database import PRIORITY_ANALYSIS, Database
from rp_engine.models.state import CharacterDetail, CharacterUpdate
from rp_engine.services.state_entry_resolver import (
    latest_character_state,
    latest_character_states_batch,
)
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_list
from rp_engine.utils.state_helpers import resolve_exchange_number

logger = logging.getLogger(__name__)


class CharacterService:
    """Resolve and update character state through the branch ancestry graph."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
        exchange_number = await resolve_exchange_number(self.db, exchange_id, rp_folder, branch)

        # 1. Find the character's card_id — cannot update a character without a card
        card = await self.db.fetch_one(
            """SELECT id, name FROM story_cards
               WHERE rp_folder = ? AND LOWER(name) = LOWER(?)
                 AND card_type IN ('character', 'npc')""",
            [rp_folder, name],
        )
        if not card:
            logger.warning("No story card found for character '%s' in rp_folder '%s'", name, rp_folder)
            return CharacterDetail(name=name)

        card_id = card["id"]

        # 2. Ensure ledger entry exists (insert active, or reactivate dormant)
        await self._ensure_character_ledger(card_id, rp_folder, branch, exchange_number, now)

        # 3. Resolve current state (direct branch query) and merge updates over it
        current_runtime = await latest_character_state(self.db, rp_folder, branch, card_id)
        merged = self._merge_character_runtime(updates, current_runtime)

        # 4. Insert CoW entry (INSERT OR REPLACE for same exchange_number)
        future = await self.db.enqueue_write(
            """INSERT OR REPLACE INTO character_state_entries
                   (card_id, rp_folder, branch, exchange_number,
                    location, conditions, emotional_state, last_seen, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [card_id, rp_folder, branch, exchange_number,
             merged["location"], merged["conditions"], merged["emotional_state"],
             merged["last_seen"], now],
            priority=PRIORITY_ANALYSIS,
        )
        await future

        result = await self.get_character(name, rp_folder, branch)
        return result  # type: ignore[return-value]

    async def _ensure_character_ledger(
        self, card_id: int, rp_folder: str, branch: str, exchange_number: int, now: str
    ) -> None:
        """Ensure an active character_ledger row exists for this card/branch.

        Inserts a fresh ``active`` row when absent, or reactivates a ``dormant`` one.
        """
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

    def _merge_character_runtime(
        self, updates: CharacterUpdate, current_runtime: dict | None
    ) -> dict:
        """Merge update fields over the current runtime snapshot.

        A ``None`` update field keeps the current value. ``conditions`` is stored
        as a JSON string and defaults to ``"[]"`` when never set.
        """
        return {
            "location": updates.location if updates.location is not None else (
                current_runtime.get("location") if current_runtime else None
            ),
            "conditions": (
                json.dumps(updates.conditions)
                if updates.conditions is not None
                else (current_runtime.get("conditions") if current_runtime else "[]")
            ),
            "emotional_state": updates.emotional_state if updates.emotional_state is not None else (
                current_runtime.get("emotional_state") if current_runtime else None
            ),
            "last_seen": updates.last_seen if updates.last_seen is not None else (
                current_runtime.get("last_seen") if current_runtime else None
            ),
        }
