"""Centralized state management using copy-on-write branching.

Characters, relationships, scenes, and events are resolved through the branch
ancestry graph. State is stored as CoW snapshots (character_state_entries,
scene_state_entries) and trust modifications with direct branch/exchange columns.

Cards are read-only — all runtime state lives in the DB, scoped by branch.

``StateManager`` is a thin facade over four single-domain services
(``rp_engine/services/state/``): CharacterService, RelationshipService (trust +
graph), SceneService, and EventService. The public API is unchanged — every
method delegates. ``get_full_state`` stays on the facade because it composes all
four domains for a single external caller.
"""

from __future__ import annotations

import logging

from rp_engine.config import TrustConfig
from rp_engine.database import Database
from rp_engine.models.context import SceneState
from rp_engine.models.state import (
    CharacterDetail,
    CharacterUpdate,
    EventDetail,
    RelationshipDetail,
    RelationshipGraphResponse,
    SceneUpdate,
    StateSnapshot,
)
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.state.character_service import CharacterService
from rp_engine.services.state.event_service import EventService
from rp_engine.services.state.relationship_service import RelationshipService
from rp_engine.services.state.scene_service import SceneService

logger = logging.getLogger(__name__)


class StateManager:
    """Facade over the character / relationship / scene / event domain services.

    Delegates each call to the appropriate domain service; keeps the public API
    that 8 importers and 13 endpoints depend on unchanged.
    """

    def __init__(self, db: Database, config: TrustConfig | None = None, resolver: AncestryResolver | None = None) -> None:
        self.db = db
        self.resolver = resolver

        # Domain services. RelationshipService depends on CharacterService for the
        # graph's character nodes, so construct characters first.
        self.characters = CharacterService(db)
        self.scenes = SceneService(db)
        self.events = EventService(db)
        self.relationships = RelationshipService(
            db, character_service=self.characters, config=config, resolver=resolver
        )

        # Backing field set directly (not via the forwarding setter) so it doesn't
        # touch self.relationships before the sub-services exist.
        self._diagnostic_logger = None  # injected by container

    @property
    def diagnostic_logger(self):
        """The structured diagnostic logger (forwarded to the relationship service)."""
        return self._diagnostic_logger

    @diagnostic_logger.setter
    def diagnostic_logger(self, value) -> None:
        self._diagnostic_logger = value
        # Only the relationship domain emits diagnostics today (trust updates).
        self.relationships.diagnostic_logger = value

    # ===================================================================
    # Character State
    # ===================================================================

    async def get_character(
        self, name: str, rp_folder: str, branch: str = "main"
    ) -> CharacterDetail | None:
        """Fetch a single character by name, resolving state through ancestry."""
        return await self.characters.get_character(name, rp_folder, branch)

    async def get_all_characters(
        self, rp_folder: str, branch: str = "main"
    ) -> dict[str, CharacterDetail]:
        """Fetch all characters, resolving state through ancestry (batch)."""
        return await self.characters.get_all_characters(rp_folder, branch)

    async def get_characters_at_location(
        self, location: str, rp_folder: str, branch: str = "main"
    ) -> list[CharacterDetail]:
        """Fetch characters at a specific location, resolving through ancestry."""
        return await self.characters.get_characters_at_location(location, rp_folder, branch)

    async def update_character(
        self,
        name: str,
        updates: CharacterUpdate,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
    ) -> CharacterDetail:
        """Write a new character state snapshot (CoW entry)."""
        return await self.characters.update_character(
            name, updates, rp_folder, branch, exchange_id
        )

    # ===================================================================
    # Relationships & Trust
    # ===================================================================

    async def get_all_relationships(
        self,
        rp_folder: str,
        branch: str = "main",
        character: str | None = None,
    ) -> list[RelationshipDetail]:
        """Fetch all relationships, optionally filtered by character (batch)."""
        return await self.relationships.get_all_relationships(rp_folder, branch, character)

    async def get_relationship_graph(
        self,
        rp_folder: str,
        branch: str = "main",
        pov_character: str | None = None,
    ) -> RelationshipGraphResponse:
        """Build a relationship graph with nodes (characters) and edges (trust)."""
        return await self.relationships.get_relationship_graph(rp_folder, branch, pov_character)

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
        """Apply a trust change between two characters."""
        return await self.relationships.update_trust(
            char_a, char_b, change, direction, reason,
            rp_folder, branch, exchange_id, bypass_session_cap,
        )

    # ===================================================================
    # Scene Context
    # ===================================================================

    async def get_scene(
        self, rp_folder: str, branch: str = "main"
    ) -> SceneState:
        """Read the current scene context."""
        return await self.scenes.get_scene(rp_folder, branch)

    async def update_scene(
        self,
        updates: SceneUpdate,
        rp_folder: str,
        branch: str = "main",
        exchange_id: int | None = None,
    ) -> SceneState:
        """Write a new scene state snapshot (CoW entry)."""
        return await self.scenes.update_scene(updates, rp_folder, branch, exchange_id)

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
        return await self.events.get_events(
            rp_folder, branch, limit, significance, character
        )

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
        return await self.events.add_event(
            event, characters, significance, rp_folder, branch,
            exchange_id, in_story_timestamp,
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
