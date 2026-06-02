"""Writing intelligence engine — thin subclass of shared base."""

from typing import Callable, Optional

from pattern_intelligence.engine import BasePatternIntelligence
from .types import (
    TaskSignature, FeedbackInput, InjectionPayload, Pattern,
)
from .db import PatternDB
from .classifier import TaskClassifier
from .retrieval import PatternRetriever
from .injection import InjectionFormatter
from .feedback import FeedbackProcessor
from .proficiency import ProficiencyUpdater


class WritingIntelligence(BasePatternIntelligence):
    """Writing intelligence orchestrator — wires classifier + retriever + formatter + feedback/proficiency over a writing PatternDB."""

    def __init__(self, db_path: str = "writing_intelligence.db",
                 token_budget: int = 600,
                 llm_call: Optional[Callable[[str], str]] = None,
                 count_tokens: Optional[Callable[[str], int]] = None):
        db = PatternDB(db_path)
        self.classifier = TaskClassifier()
        self.retriever = PatternRetriever(db)
        self.formatter = InjectionFormatter(token_budget, count_tokens)
        feedback_processor = FeedbackProcessor(db, llm_call)
        proficiency_updater = ProficiencyUpdater(db)

        super().__init__(db, feedback_processor, proficiency_updater)

    def prepare(self, prompt: str, preceding_content: Optional[str] = None,
                task_override: Optional[dict] = None) -> InjectionPayload:
        """Classify the writing task, retrieve matching patterns, and format the injection payload (logging the signature + included patterns)."""
        task_sig = self.classifier.classify(prompt, preceding_content, task_override)
        scored = self.retriever.retrieve(task_sig)
        payload = self.formatter.format(scored, task_sig)

        self._log_and_track(task_sig, payload.patterns_included)

        return payload
