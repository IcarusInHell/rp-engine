"""Timeline view models."""

from __future__ import annotations

from pydantic import BaseModel


class TimelineExchange(BaseModel):
    """One exchange as a timeline entry — snippets plus in-story and wall-clock timestamps."""
    exchange_number: int
    user_snippet: str
    assistant_snippet: str
    in_story_timestamp: str | None = None
    created_at: str | None = None
    session_id: str | None = None


class TimelineBranch(BaseModel):
    """A branch's ordered exchanges for timeline rendering, with active flag and count."""
    name: str
    created_from: str | None = None
    branch_point: int | None = None
    is_active: bool = False
    exchange_count: int = 0
    exchanges: list[TimelineExchange] = []


class DivergencePoint(BaseModel):
    """An exchange number where two or more branches split."""
    exchange_number: int
    branches: list[str]


class TimelineResponse(BaseModel):
    """Full timeline for an RP — all branches plus their divergence points."""
    rp_folder: str
    branches: list[TimelineBranch]
    divergence_points: list[DivergencePoint]
