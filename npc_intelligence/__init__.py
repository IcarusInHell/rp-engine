"""NPC behavioral intelligence package — learns, retrieves, and injects NPC behavioral patterns keyed by archetype / trust stage / interaction type / scene signals."""

from .engine import NPCIntelligence
from .types import (
    BehavioralSignature, Pattern, CorrectionPair, ScoredPattern,
    InjectionPayload, FeedbackInput, ExtractionResult,
    Archetype, Modifier, TrustStage, InteractionType, SceneSignal,
    BehavioralCategory, Direction,
)

__all__ = [
    "NPCIntelligence",
    "BehavioralSignature", "Pattern", "CorrectionPair", "ScoredPattern",
    "InjectionPayload", "FeedbackInput", "ExtractionResult",
    "Archetype", "Modifier", "TrustStage", "InteractionType", "SceneSignal",
    "BehavioralCategory", "Direction",
]
