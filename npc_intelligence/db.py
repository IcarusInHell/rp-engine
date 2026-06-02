"""NPC behavioral pattern database — thin subclass of shared base."""

from typing import Any

from pattern_intelligence.db import BasePatternDB
from .types import BehavioralCategory, BehavioralSignature


class BehavioralPatternDB(BasePatternDB):
    """NPC behavioral pattern store — BasePatternDB bound to BehavioralCategory; serializes the 5-dimension behavioral signature to its trigger row."""

    _category_enum = BehavioralCategory

    def __init__(self, db_path: str = "npc_intelligence.db"):
        super().__init__(db_path)

    def _serialize_signature(self, signature: Any) -> dict:
        sig: BehavioralSignature = signature
        return {
            "archetype": sig.archetype.value,
            "modifiers": [m.value for m in sig.modifiers],
            "trust_stage": sig.trust_stage.value,
            "interaction_type": sig.interaction_type.value,
            "scene_signals": [s.value for s in sig.scene_signals],
        }
