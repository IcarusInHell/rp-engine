"""NPC behavioral pattern retrieval — delegates to shared implementation."""
from rp_engine.utils.retrieval import BasePatternRetriever
from .types import BehavioralSignature, ScoredPattern
from .db import BehavioralPatternDB


class BehavioralRetriever(BasePatternRetriever):
    """NPC-specific pattern retriever."""

    def __init__(self, db: BehavioralPatternDB, recency_window_days: float = 7.0):
        super().__init__(db, recency_window_days)

    def retrieve(self, behavioral_sig: BehavioralSignature, max_results: int = 20) -> list[ScoredPattern]:
        """Fetch and score patterns whose triggers overlap the signature's flattened dimension values, returning the top `max_results` ScoredPatterns."""
        trigger_values = behavioral_sig.all_trigger_values()
        patterns = self.db.get_patterns_by_triggers(trigger_values)
        scored_dicts = self._score_patterns(patterns, trigger_values)

        return [
            ScoredPattern(
                pattern=d["pattern"],
                score=d["score"],
                relevance=d["relevance"],
                injection_level=d["injection_level"],
            )
            for d in scored_dicts[:max_results]
        ]
