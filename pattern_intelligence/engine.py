"""Base pattern intelligence engine — shared lifecycle and pattern management.

Subclasses override:
- prepare(): domain-specific classify → retrieve → format pipeline
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Callable, Optional

from .db import BasePatternDB
from .feedback import BaseFeedbackProcessor
from .types import FeedbackInput, Pattern


class BasePatternIntelligence:
    """Shared engine logic: session management, record_outcome, pattern CRUD, stats.

    Subclasses set up their own components in __init__ and implement prepare().
    """

    def __init__(self, db: BasePatternDB,
                 feedback_processor: BaseFeedbackProcessor,
                 proficiency_updater: Any):
        self.db = db
        self.feedback_processor = feedback_processor
        self.proficiency_updater = proficiency_updater

        self._session_id: Optional[str] = None
        self._current_signature: Any = None
        self._current_injected_ids: list[str] = []
        self._current_output_id: Optional[str] = None
        self._exchange_count: int = 0

    def start_session(self) -> str:
        """Begin a new learning session (resets the exchange counter); returns the session id."""
        self._session_id = self.db.create_session()
        self._exchange_count = 0
        return self._session_id

    def end_session(self) -> dict:
        """End the session, persisting exchange count + active-pattern count + average proficiency; returns the summary dict."""
        patterns = self.db.get_all_patterns()
        avg_prof = (sum(p.proficiency for p in patterns) / len(patterns)) if patterns else 0.0
        self.db.end_session(self._session_id, self._exchange_count,
                            len(patterns), avg_prof)
        summary = {
            "session_id": self._session_id,
            "exchanges": self._exchange_count,
            "patterns_active": len(patterns),
            "avg_proficiency": round(avg_prof, 3),
        }
        self._session_id = None
        return summary

    @abstractmethod
    def prepare(self, *args: Any, **kwargs: Any) -> Any:
        """Domain-specific: classify → retrieve → format → log output."""
        ...

    def _log_and_track(self, signature: Any, payload_ids: list[str]) -> None:
        """Shared post-prepare bookkeeping."""
        self._current_signature = signature
        self._current_injected_ids = payload_ids
        self._current_output_id = self.db.log_output(
            self._session_id or "", signature, payload_ids, "")

    def record_outcome(self, output_text: str, accepted: bool = True,
                       feedback: Optional[FeedbackInput] = None) -> dict:
        """Record whether the last prepared output was accepted or corrected — bumps proficiency on accept, and on correction extracts patterns from feedback and applies regression penalties."""
        self._exchange_count += 1

        if accepted:
            if self._current_output_id:
                self.db.mark_output_accepted(self._current_output_id)
            self.proficiency_updater.on_accepted(self._current_injected_ids)
            result = {
                "output_id": self._current_output_id,
                "patterns_updated": list(self._current_injected_ids),
                "patterns_created": [],
            }
        elif feedback:
            if self._current_output_id:
                self.db.mark_output_corrected(self._current_output_id, [])

            pattern_ids = self.feedback_processor.process(
                feedback, self._current_signature)

            corrected_and_injected = set(pattern_ids) & set(self._current_injected_ids)
            corrected_not_injected = set(pattern_ids) - set(self._current_injected_ids)

            self.proficiency_updater.on_corrected(
                list(corrected_and_injected), self._current_injected_ids)

            for pid in corrected_not_injected:
                self.proficiency_updater.on_regression(pid)

            if self._current_output_id:
                self.db.mark_output_corrected(self._current_output_id, pattern_ids)

            result = {
                "output_id": self._current_output_id,
                "patterns_updated": list(corrected_and_injected),
                "patterns_created": list(set(pattern_ids) - corrected_and_injected),
            }
        else:
            result = {
                "output_id": self._current_output_id,
                "patterns_updated": [],
                "patterns_created": [],
            }

        self._current_signature = None
        self._current_injected_ids = []
        self._current_output_id = None
        return result

    # --- Direct Pattern Management ---

    def add_pattern(self, pattern: Pattern) -> str:
        """Insert a pattern directly (bypassing feedback extraction); returns its id."""
        return self.db.insert_pattern(pattern)

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        """Fetch a single pattern by id, or None."""
        return self.db.get_pattern(pattern_id)

    def list_patterns(self, category: Optional[str] = None) -> list[Pattern]:
        """List all patterns, optionally filtered to one category value."""
        patterns = self.db.get_all_patterns()
        if category:
            patterns = [p for p in patterns if p.category.value == category]
        return patterns

    def update_pattern(self, pattern: Pattern) -> None:
        """Persist changes to an existing pattern."""
        self.db.update_pattern(pattern)

    # --- Diagnostics ---

    def get_stats(self) -> dict:
        """Return aggregate pattern stats — totals, average proficiency, per-category counts, and low-proficiency / high-severity counts."""
        patterns = self.db.get_all_patterns()
        if not patterns:
            return {"total_patterns": 0, "avg_proficiency": 0.0,
                    "by_category": {}, "low_proficiency_count": 0,
                    "high_severity_count": 0}
        by_cat = {}
        for p in patterns:
            cat = p.category.value
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "total_patterns": len(patterns),
            "avg_proficiency": round(
                sum(p.proficiency for p in patterns) / len(patterns), 3),
            "by_category": by_cat,
            "low_proficiency_count": sum(1 for p in patterns if p.proficiency < 0.4),
            "high_severity_count": sum(1 for p in patterns if p.severity > 0.7),
        }

    # --- Lifecycle ---

    def close(self) -> None:
        """Close the underlying pattern database."""
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
