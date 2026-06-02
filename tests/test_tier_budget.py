"""Phase 7 — per-tier context budget (the tiered-context plan's "Token Budget
(Optional Enhancement)", deferred P3→P4 and wired here).

A total ``context.max_context_chars`` budget for the documents section, split per
tier (``tier_allocation`` full/brief/reference). Each tier is budgeted
INDEPENDENTLY (full's share does not spill into brief). Over-budget docs are
DROPPED (never silently — a warning logs the names), lowest-priority-first within
each tier, but at least the top-ranked doc of a tier is always admitted so a
too-small budget can't nuke the most relevant card.

Default ``max_context_chars=0`` disables the budget entirely → byte-identical to
pre-Phase-7 output (the Phase 4 golden separately locks that path).

CRITICAL placement invariant (advisor-caught): the budget runs in
``context_engine._filter_sent_cards`` *before* a surviving doc is recorded in
``context_sent`` — so a budget-dropped doc is NEVER marked "sent". Recording a
dropped doc as sent would suppress it next turn (it'd look already-loaded) and it
could never reach the LLM: the exact silent cross-turn drop migration 031 closed.
``test_dropped_doc_is_not_recorded_sent`` is the discriminating guard.
"""

from __future__ import annotations

import logging

from rp_engine.config import ContextConfig, TierAllocation


def _card(eid: str, *, content: str = "", summary: str = "") -> dict:
    return {
        "id": eid,
        "name": eid.upper(),
        "card_type": "lore",
        "file_path": f"{eid}.md",
        "content": content,
        "summary": summary,
        "content_hash": f"h-{eid}",
    }


def _ranked(eid: str, source: str, score: float, **kw) -> tuple:
    return (eid, (_card(eid, **kw), source, score))


def _budget_cfg(chars: int, **kw) -> ContextConfig:
    return ContextConfig(max_context_chars=chars, **kw)


async def test_tier_budget_drops_over_budget_docs_and_logs(seeded_rp, caplog):
    """full budget = 0.6 * 300 = 180. Two full docs of 150 chars: the first fits,
    the second pushes the tier over budget → dropped + logged (observable).

    Mutation (disable the budget / never drop): FULLB survives and no warning →
    both assertions red.
    """
    ce = seeded_rp.container.context_engine
    ce._config_override = _budget_cfg(300)
    ranked = [
        _ranked("a", "keyword", 1.0, content="A" * 150),
        _ranked("b", "keyword", 1.0, content="B" * 150),
    ]
    with caplog.at_level(logging.WARNING):
        docs, _ = await ce._filter_sent_cards(ranked, None, 1)

    names = {d.name for d in docs}
    assert "A" in names, "the first full doc fits within budget and must stay"
    assert "B" not in names, "the over-budget second full doc must be dropped"
    assert any("budget" in r.message.lower() and "B" in r.message for r in caplog.records), (
        "a dropped document must be logged (silent-drop guard)"
    )


async def test_dropped_doc_is_not_recorded_sent(seeded_rp):
    """THE cross-turn guard: a budget-dropped doc must NOT be written to
    context_sent — otherwise next turn it looks already-loaded and is suppressed,
    a silent cross-turn drop (migration 031's bug class).

    Mutation (record sent BEFORE applying the budget, i.e. the original misplaced
    design): 'b' lands in context_sent → this reds.
    """
    db = seeded_rp.db
    ce = seeded_rp.container.context_engine
    ce._config_override = _budget_cfg(300)
    ranked = [
        _ranked("a", "keyword", 1.0, content="A" * 150),
        _ranked("b", "keyword", 1.0, content="B" * 150),  # dropped by budget
    ]
    docs, _ = await ce._filter_sent_cards(ranked, "sess-1", 1)  # FK-valid seeded session
    assert {d.name for d in docs} == {"A"}, "only the in-budget doc survives"

    kept_row = await db.fetch_one(
        "SELECT 1 FROM context_sent WHERE session_id = ? AND entity_id = ?", ["sess-1", "a"]
    )
    dropped_row = await db.fetch_one(
        "SELECT 1 FROM context_sent WHERE session_id = ? AND entity_id = ?", ["sess-1", "b"]
    )
    assert kept_row is not None, "the doc that shipped must be recorded sent"
    assert dropped_row is None, (
        "a budget-DROPPED doc must NOT be recorded sent (else it's suppressed next "
        "turn and never reaches the LLM — a silent cross-turn drop)"
    )


async def test_tier_budget_disabled_by_default_keeps_all(seeded_rp):
    """max_context_chars=0 (the default) disables budgeting → every doc survives.

    Mutation (treat 0 as a real budget): FULLB drops → red.
    """
    ce = seeded_rp.container.context_engine
    ce._config_override = _budget_cfg(0)
    ranked = [
        _ranked("a", "keyword", 1.0, content="A" * 5000),
        _ranked("b", "keyword", 1.0, content="B" * 5000),
    ]
    docs, _ = await ce._filter_sent_cards(ranked, None, 1)
    assert {d.name for d in docs} == {"A", "B"}, "budget disabled → no doc may be dropped"


async def test_tier_budget_is_per_tier_independent(seeded_rp):
    """Tiers are budgeted independently: full exhausting its 0.6 share must NOT
    starve a brief doc on its own 0.3 share. Budget 300 → full=180, brief=90.

    Mutation (one shared budget): FullA's 150 chars + a shared 300 budget would
    leave room, but per-tier proves brief is independent — a shared-budget
    regression that drops BriefC reds its assertion.
    """
    ce = seeded_rp.container.context_engine
    ce._config_override = _budget_cfg(300)
    ranked = [
        _ranked("fulla", "keyword", 1.0, content="A" * 150),   # full, fits (180)
        _ranked("fullb", "keyword", 1.0, content="B" * 150),   # full, overflows → drop
        _ranked("briefc", "semantic", 0.8, summary="C" * 50),  # brief, own 90 budget → keep
    ]
    docs, _ = await ce._filter_sent_cards(ranked, None, 1)
    names = {d.name for d in docs}
    assert "FULLA" in names, "first full doc fits"
    assert "FULLB" not in names, "second full doc overflows the full sub-budget"
    assert "BRIEFC" in names, "brief doc draws on its own independent sub-budget"


async def test_tier_budget_admits_at_least_top_doc_per_tier(seeded_rp):
    """A single full doc larger than the whole full sub-budget is still admitted
    (per-card max_card_content_length is the real cap). Budget 100 → full=60.

    Mutation (drop on the first doc too / remove the `consumed > 0` guard): FullA
    vanishes → red.
    """
    ce = seeded_rp.container.context_engine
    ce._config_override = _budget_cfg(100)
    ranked = [_ranked("a", "keyword", 1.0, content="A" * 150)]
    docs, _ = await ce._filter_sent_cards(ranked, None, 1)
    assert {d.name for d in docs} == {"A"}, "the top doc of a tier is always admitted, even over budget"


def test_tier_allocation_defaults_sum_to_one():
    """The default split is the plan's 0.6/0.3/0.1 — a drift guard so a careless
    edit to the allocation defaults is caught."""
    a = TierAllocation()
    assert (a.full, a.brief, a.reference) == (0.6, 0.3, 0.1)
