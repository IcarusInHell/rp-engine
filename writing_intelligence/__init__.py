"""Writing intelligence package — learns, retrieves, and injects prose-craft patterns keyed by mode / register / intensity / position / content elements."""

from .engine import WritingIntelligence
from .types import (
    TaskSignature, Pattern, CorrectionPair, ScoredPattern,
    InjectionPayload, FeedbackInput, ExtractionResult,
    Mode, Register, Intensity, Position, Element,
    PatternCategory, Direction,
)

__all__ = [
    "WritingIntelligence",
    "TaskSignature", "Pattern", "CorrectionPair", "ScoredPattern",
    "InjectionPayload", "FeedbackInput", "ExtractionResult",
    "Mode", "Register", "Intensity", "Position", "Element",
    "PatternCategory", "Direction",
]
