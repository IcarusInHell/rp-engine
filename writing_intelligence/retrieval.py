"""Writing pattern retrieval — delegates to shared implementation."""
from rp_engine.utils.retrieval import BasePatternRetriever
from .types import TaskSignature, ScoredPattern
from .db import PatternDB


class PatternRetriever(BasePatternRetriever):
    """Writing-specific pattern retriever."""

    def __init__(self, db: PatternDB, recency_window_days: float = 7.0):
        super().__init__(db, recency_window_days)

    def retrieve(self, task_sig: TaskSignature, max_results: int = 20) -> list[ScoredPattern]:
        """Fetch and score patterns whose triggers overlap the signature's flattened dimension values, returning the top `max_results` ScoredPatterns."""
        trigger_values = task_sig.all_trigger_values()
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
