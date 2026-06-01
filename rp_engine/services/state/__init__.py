"""State domain services backing the ``StateManager`` facade.

``StateManager`` (``rp_engine/services/state_manager.py``) delegates to these
four single-domain services. They can also be unit-tested in isolation.
"""

from rp_engine.services.state.character_service import CharacterService
from rp_engine.services.state.event_service import EventService
from rp_engine.services.state.relationship_service import RelationshipService
from rp_engine.services.state.scene_service import SceneService

__all__ = [
    "CharacterService",
    "EventService",
    "RelationshipService",
    "SceneService",
]
