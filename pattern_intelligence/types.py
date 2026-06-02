"""Shared types for pattern intelligence systems.

Domain-specific enums and signature dataclasses stay in their respective packages.
Shared dataclasses (Pattern, CorrectionPair, etc.) live here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol


class Direction(str, Enum):
    """Pattern direction — whether the learned pattern is something to AVOID or to PREFER."""

    AVOID = "avoid"
    PREFER = "prefer"


class SignatureProtocol(Protocol):
    """Protocol for domain-specific signatures (BehavioralSignature, TaskSignature)."""

    def all_trigger_values(self) -> set[str]: ...

    @classmethod
    def from_dict(cls, d: dict) -> "SignatureProtocol": ...


@dataclass
class CorrectionPair:
    """A before/after example tied to a pattern — original excerpt, user revision, and token counts for budget-aware example selection."""

    id: str
    pattern_id: str
    original: str
    revised: str
    critique: Optional[str] = None
    extracted_rule: Optional[str] = None
    tokens_original: int = 0
    tokens_revised: int = 0
    created_at: Optional[datetime] = None


@dataclass
class Pattern:
    """A learned pattern. Category is stored as an enum value from the domain package."""

    id: str
    category: Any  # BehavioralCategory or PatternCategory — domain-specific enum
    subcategory: Optional[str]
    description: str
    direction: Direction
    severity: float = 0.5
    frequency: int = 0
    correction_count: int = 0
    proficiency: float = 0.2
    context_triggers: list[str] = field(default_factory=list)
    compressed_rule: Optional[str] = None
    created_at: Optional[datetime] = None
    last_triggered: Optional[datetime] = None
    last_corrected: Optional[datetime] = None
    # Populated at retrieval time, not stored in patterns table
    correction_pairs: list[CorrectionPair] = field(default_factory=list)


@dataclass
class ScoredPattern:
    """A Pattern with its computed retrieval score."""

    pattern: Pattern
    score: float
    relevance: float
    injection_level: str  # "critical", "moderate", "reminder", "skip"


@dataclass
class FeedbackInput:
    """What the caller provides when submitting feedback."""

    original_output: str
    user_feedback: Optional[str] = None
    user_rewrite: Optional[str] = None
    session_id: Optional[str] = None


@dataclass
class ExtractionResult:
    """What LLM-assisted pattern extraction returns per identified pattern."""

    category: str
    subcategory: Optional[str]
    description: str
    direction: str
    compressed_rule: str
    context_triggers: list[str]
    original_excerpt: Optional[str] = None
    revised_excerpt: Optional[str] = None
    critique_excerpt: Optional[str] = None
