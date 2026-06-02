"""Writing intelligence types — the 5 task-dimension enums, the writing-pattern category taxonomy, and the TaskSignature / InjectionPayload dataclasses."""

from dataclasses import dataclass, field
from enum import Enum
# Re-export shared types from base package
from pattern_intelligence.types import (
    CorrectionPair,
    Direction,
    ExtractionResult,
    FeedbackInput,
    Pattern,
    ScoredPattern,
)


# === TRIGGER DIMENSION ENUMS (Writing-specific) ===

class Mode(str, Enum):
    """Writing mode — the task-intent dimension (drafting, continuing, revising, expanding, condensing)."""

    DRAFTING = "drafting"
    CONTINUING = "continuing"
    REVISING = "revising"
    EXPANDING = "expanding"
    CONDENSING = "condensing"

class Register(str, Enum):
    """Prose register — the passage's mode of writing (action, dialogue, introspection, description, exposition, transition)."""

    ACTION = "action"
    DIALOGUE = "dialogue"
    INTROSPECTION = "introspection"
    DESCRIPTION = "description"
    EXPOSITION = "exposition"
    TRANSITION = "transition"

class Intensity(str, Enum):
    """Emotional intensity of the passage — high / medium / low, inferred from keyword and punctuation cues."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class Position(str, Enum):
    """Narrative position — where the passage sits in the arc (opening, rising, climax, falling, closing, mid)."""

    OPENING = "opening"
    RISING = "rising"
    CLIMAX = "climax"
    FALLING = "falling"
    CLOSING = "closing"
    MID = "mid"

class Element(str, Enum):
    """Content element present in the passage — physical action, dialogue, internal thought, sensory detail, etc.; zero or more apply."""

    PHYSICAL_ACTION = "physical_action"
    DIALOGUE = "dialogue"
    INTERNAL_THOUGHT = "internal_thought"
    ENVIRONMENTAL_DESCRIPTION = "environmental_description"
    CHARACTER_DESCRIPTION = "character_description"
    EMOTIONAL_BEAT = "emotional_beat"
    SENSORY_DETAIL = "sensory_detail"


# === PATTERN ENUMS ===

class PatternCategory(str, Enum):
    """Writing pattern category — the learned-pattern taxonomy (figurative-language, pacing, word-choice, reader-trust, ...)."""

    FIGURATIVE_LANGUAGE = "figurative_language"
    NARRATIVE_DISTANCE = "narrative_distance"
    INFORMATION_ORDERING = "information_ordering"
    DETAIL_SELECTION = "detail_selection"
    READER_TRUST = "reader_trust"
    PACING = "pacing"
    DIALOGUE = "dialogue"
    WORD_CHOICE = "word_choice"
    STRUCTURE = "structure"
    EMOTIONAL_LOGIC = "emotional_logic"
    EMOTIONAL_RESISTANCE = "emotional_resistance"
    ENVIRONMENTAL_PRESSURE = "environmental_pressure"
    SENSORY_RESTRAINT = "sensory_restraint"
    PERFORMANCE_INTERIORITY = "performance_interiority"


# === DATACLASSES ===

@dataclass
class TaskSignature:
    """The full 5-dimension fingerprint for one writing task — mode + register + intensity + position + content elements."""

    mode: Mode
    register: Register
    intensity: Intensity
    position: Position
    elements: list[Element]

    def all_trigger_values(self) -> set[str]:
        """Flatten all dimension values into a set for overlap matching."""
        values = {self.mode.value, self.register.value,
                  self.intensity.value, self.position.value}
        values.update(e.value for e in self.elements)
        return values

    @classmethod
    def from_dict(cls, d: dict) -> "TaskSignature":
        """
        Build from a dict with string values. Missing keys get defaults.
        Accepts partial dicts for RP Engine integration.
        """
        return cls(
            mode=Mode(d.get("mode", "drafting")),
            register=Register(d.get("register", "description")),
            intensity=Intensity(d.get("intensity", "medium")),
            position=Position(d.get("position", "mid")),
            elements=[Element(e) for e in d.get("elements", [])],
        )


@dataclass
class InjectionPayload:
    """The formatted text block ready for system prompt insertion."""
    text: str
    token_count: int
    patterns_included: list[str]  # pattern IDs
    task_signature: TaskSignature
