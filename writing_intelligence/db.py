"""Writing pattern database — thin subclass of shared base."""

from typing import Any

from pattern_intelligence.db import BasePatternDB
from .types import PatternCategory, TaskSignature


class PatternDB(BasePatternDB):
    """Writing pattern store — BasePatternDB bound to PatternCategory; serializes the 5-dimension task signature to its trigger row."""

    _category_enum = PatternCategory

    def __init__(self, db_path: str = "writing_intelligence.db"):
        super().__init__(db_path)

    def _serialize_signature(self, signature: Any) -> dict:
        sig: TaskSignature = signature
        return {
            "mode": sig.mode.value,
            "register": sig.register.value,
            "intensity": sig.intensity.value,
            "position": sig.position.value,
            "elements": [e.value for e in sig.elements],
        }
