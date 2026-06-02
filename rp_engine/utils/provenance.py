"""Request-scoped drop-ledger collector + contextvar plumbing (Build B).

Neutral leaf: no service imports, so both ``context_engine`` and ``prompt_assembler``
(and, later, routers for Build A) can record drops without a backward import. The
collector only accumulates; the request owner (``ChatManager``) flushes it through
``DiagnosticLogger``. All ``record_*`` helpers are no-ops when no collector is set on
the current async task, so swallow-points pay nothing outside a chat request and never
need a None-check.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Generator
from contextvars import ContextVar
from typing import Any

from rp_engine.models.provenance import DropEvent, ProvenanceReport

logger = logging.getLogger(__name__)

_current: ContextVar[ProvenanceCollector | None] = ContextVar(
    "provenance_collector", default=None,
)


class ProvenanceCollector:
    """Accumulates drop events + produced/injected item ids for one request."""

    def __init__(self) -> None:
        self.drops: list[DropEvent] = []
        self._produced: dict[str, set[str]] = {}
        self._injected: set[str] = set()

    def add_drop(self, event: DropEvent) -> None:
        self.drops.append(event)

    def add_produced(self, stage: str, item_id: str) -> None:
        self._produced.setdefault(stage, set()).add(item_id)

    def add_injected(self, item_id: str) -> None:
        self._injected.add(item_id)

    def orphans(self) -> list[str]:
        """Produced but neither injected nor dropped — the producer-with-no-consumer flag."""
        produced: set[str] = set()
        for ids in self._produced.values():
            produced |= ids
        dropped = {d.item_id for d in self.drops if d.item_id}
        return sorted(produced - self._injected - dropped)

    def report(
        self,
        *,
        rp_folder: str | None = None,
        branch: str | None = None,
        session_id: str | None = None,
    ) -> ProvenanceReport:
        orphans = self.orphans()
        counts = {
            "drops": len(self.drops),
            "produced": sum(len(ids) for ids in self._produced.values()),
            "injected": len(self._injected),
            "orphans": len(orphans),
        }
        return ProvenanceReport(
            rp_folder=rp_folder,
            branch=branch,
            session_id=session_id,
            drops=list(self.drops),
            orphans=orphans,
            counts=counts,
        )


def get_collector() -> ProvenanceCollector | None:
    """The collector for the current async task, or None if not collecting."""
    return _current.get()


def record_drop(
    stage: str,
    item_kind: str,
    reason: str,
    *,
    item_id: str | None = None,
    score: float | None = None,
    budget_before: int | None = None,
    budget_after: int | None = None,
    detail: str | None = None,
) -> None:
    """Record a drop on the active collector. No-op when not collecting."""
    collector = _current.get()
    if collector is not None:
        collector.add_drop(
            DropEvent(
                stage=stage,
                item_kind=item_kind,
                reason=reason,
                item_id=item_id,
                score=score,
                budget_before=budget_before,
                budget_after=budget_after,
                detail=detail,
            )
        )


def record_produced(stage: str, item_id: str | None) -> None:
    """Record that ``item_id`` was produced by ``stage``. No-op when not collecting."""
    collector = _current.get()
    if collector is not None and item_id:
        collector.add_produced(stage, item_id)


def record_injected(item_id: str | None) -> None:
    """Record that ``item_id`` actually reached the assembled prompt. No-op when not collecting."""
    collector = _current.get()
    if collector is not None and item_id:
        collector.add_injected(item_id)


@contextlib.contextmanager
def collecting(
    *,
    sink: Callable[[str, str, dict[str, Any]], object] | None = None,
    rp_folder: str | None = None,
    branch: str | None = None,
    session_id: str | None = None,
    event: str = "request",
) -> Generator[ProvenanceCollector]:
    """Set a fresh collector for the current async task for the duration of the block.

    If ``sink`` is given (e.g. ``DiagnosticLogger.log``), the collector's report is
    written through it on exit as ``sink("provenance", event, report)`` — the canonical
    flush, owned here so no caller re-implements it. Fail-safe: the contextvar is always
    reset first, and a sink/report error is swallowed (the ledger must never break the
    request it observes). Omit ``sink`` to just read ``collector.report()`` yourself
    (e.g. a dry-run inspector — Build A).
    """
    collector = ProvenanceCollector()
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)
        if sink is not None:
            try:
                report = collector.report(
                    rp_folder=rp_folder, branch=branch, session_id=session_id,
                )
                sink("provenance", event, report.model_dump())
            except Exception:
                logger.debug("provenance flush failed", exc_info=True)
