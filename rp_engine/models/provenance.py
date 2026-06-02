"""Drop-ledger provenance models — typed events for the per-request drop collector (Build B)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DropEvent(BaseModel):
    """One thing produced-then-dropped, or a subsystem that errored to nothing."""

    stage: str
    item_kind: str
    reason: str
    item_id: str | None = None
    score: float | None = None
    budget_before: int | None = None
    budget_after: int | None = None
    detail: str | None = None


class ProvenanceReport(BaseModel):
    """Flushed per-request drop-ledger summary (written to the diagnostic log)."""

    rp_folder: str | None = None
    branch: str | None = None
    session_id: str | None = None
    drops: list[DropEvent] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
