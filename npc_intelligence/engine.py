"""NPC intelligence engine — thin subclass of shared base."""

from typing import Callable, Optional

from pattern_intelligence.engine import BasePatternIntelligence
from .types import (
    BehavioralSignature, FeedbackInput, InjectionPayload, Pattern,
)
from .db import BehavioralPatternDB
from .classifier import BehavioralClassifier
from .retrieval import BehavioralRetriever
from .injection import BehavioralInjectionFormatter
from .feedback import BehavioralFeedbackProcessor
from .proficiency import ProficiencyUpdater


class NPCIntelligence(BasePatternIntelligence):
    """NPC behavioral intelligence orchestrator — wires classifier + retriever + formatter + feedback/proficiency over a BehavioralPatternDB."""

    def __init__(self, db_path: str = "npc_intelligence.db",
                 token_budget: int = 500,
                 llm_call: Optional[Callable[[str], str]] = None,
                 count_tokens: Optional[Callable[[str], int]] = None):
        db = BehavioralPatternDB(db_path)
        self.classifier = BehavioralClassifier()
        self.retriever = BehavioralRetriever(db)
        self.formatter = BehavioralInjectionFormatter(token_budget, count_tokens)
        feedback_processor = BehavioralFeedbackProcessor(db, llm_call)
        proficiency_updater = ProficiencyUpdater(db)

        super().__init__(db, feedback_processor, proficiency_updater)

    def prepare(self, npc_name: str, archetype: str = "common_people",
                modifiers: Optional[list[str]] = None,
                trust_stage: str = "neutral", trust_score: int = 0,
                scene_signals: Optional[dict[str, float]] = None,
                scene_prompt: str = "",
                override: Optional[dict] = None) -> InjectionPayload:
        """Classify the NPC moment, retrieve matching patterns, and format the injection payload (logging the signature + included patterns)."""
        behavioral_sig = self.classifier.classify(
            archetype=archetype,
            modifiers=modifiers,
            trust_stage=trust_stage,
            trust_score=trust_score,
            scene_signals=scene_signals,
            scene_prompt=scene_prompt,
            override=override,
        )
        scored = self.retriever.retrieve(behavioral_sig)
        payload = self.formatter.format(scored, behavioral_sig, npc_name)

        self._log_and_track(behavioral_sig, payload.patterns_included)

        return payload
