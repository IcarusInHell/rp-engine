"""Drop-ledger provenance (Build B) — collector unit logic + per-site emission guards.

Each emission guard is mutation-proved: removing the ``record_drop`` at its
swallow-point must red the corresponding assertion (silent-drop discipline — a drop
the ledger can't see is itself a silent drop). The collector is async-task-isolated
via a contextvar, so the integration test also confirms emission survives the awaits
between ``_filter_sent_cards`` and ``_apply_tier_budget``.
"""

from __future__ import annotations

from rp_engine.config import ContextConfig, PromptConfig, TokenBudgetConfig
from rp_engine.utils.provenance import (
    collecting,
    get_collector,
    record_drop,
    record_injected,
    record_produced,
)


def test_collector_orphan_math():
    """orphans = produced − injected − dropped (the producer-with-no-consumer flag)."""
    with collecting() as c:
        record_produced("context.docs", "a")
        record_produced("context.docs", "b")
        record_produced("context.docs", "c")
        record_injected("a")  # a reached the prompt
        record_drop("context.tier_budget", "document", "over_budget", item_id="c")
    rep = c.report(rp_folder="RP", branch="main")
    assert rep.orphans == ["b"], "b was produced but neither injected nor dropped"
    assert rep.counts == {"drops": 1, "produced": 3, "injected": 1, "orphans": 1}


def test_record_helpers_noop_without_collector():
    """Outside collecting(), the record_* helpers are silent no-ops — zero overhead,
    no None-check needed at swallow-points."""
    assert get_collector() is None
    record_drop("x", "y", "z", item_id="q")  # must not raise
    record_produced("x", "q")
    record_injected("q")
    assert get_collector() is None


def test_collecting_flushes_through_sink_on_exit():
    """A sink given to collecting() receives exactly one provenance report on exit."""
    sent: list[tuple] = []
    with collecting(
        sink=lambda cat, ev, data: sent.append((cat, ev, data)),
        rp_folder="RP", branch="b", session_id="s",
    ):
        record_drop("context.tier_budget", "document", "over_budget", item_id="c")
    assert len(sent) == 1
    cat, ev, data = sent[0]
    assert (cat, ev) == ("provenance", "request")
    assert data["rp_folder"] == "RP" and data["counts"]["drops"] == 1


def test_collecting_sink_failure_does_not_propagate():
    """The flush is fail-safe: a throwing sink must NOT break the request it observes."""
    def boom(*_args):
        raise RuntimeError("sink down")

    # Should not raise out of the with-block.
    with collecting(sink=boom, rp_folder="RP"):
        record_drop("x", "y", "z", item_id="q")
    assert get_collector() is None  # contextvar still reset despite the sink error


# ── Per-site emission guards ─────────────────────────────────────────────


def _card(eid: str, *, content: str = "", summary: str = "") -> dict:
    return {
        "id": eid, "name": eid.upper(), "card_type": "lore",
        "file_path": f"{eid}.md", "content": content, "summary": summary,
        "content_hash": f"h-{eid}",
    }


def _ranked(eid: str, source: str, score: float, **kw) -> tuple:
    return (eid, (_card(eid, **kw), source, score))


async def test_tier_budget_drop_emits_provenance_event(seeded_rp):
    """The verified ``_apply_tier_budget`` drop also emits a DropEvent into the active
    collector (alongside its existing log). full budget = 0.6*300 = 180; two 150-char
    full docs → 'b' overflows and is dropped.

    Mutation (remove the record_drop in _apply_tier_budget): no provenance event for
    'b' → this reds, while the keep/log behavior (test_tier_budget) stays green.
    """
    ce = seeded_rp.container.context_engine
    ce._config_override = ContextConfig(max_context_chars=300)
    ranked = [
        _ranked("a", "keyword", 1.0, content="A" * 150),
        _ranked("b", "keyword", 1.0, content="B" * 150),
    ]
    with collecting() as c:
        docs, _ = await ce._filter_sent_cards(ranked, None, 1)

    assert {d.name for d in docs} == {"A"}, "the over-budget doc is dropped (behavior unchanged)"
    budget_drops = [d for d in c.drops if d.stage == "context.tier_budget"]
    assert [d.item_id for d in budget_drops] == ["b"], (
        "the budget-dropped doc must emit exactly one DropEvent keyed by entity_id"
    )
    assert budget_drops[0].reason == "over_budget"
    assert budget_drops[0].item_kind == "document"


async def test_tier_budget_no_drop_no_event(seeded_rp):
    """Disabled budget (the default) → no drop → no provenance event. Guards against
    a spurious-emission regression (the ledger must report only real drops)."""
    ce = seeded_rp.container.context_engine
    ce._config_override = ContextConfig(max_context_chars=0)
    ranked = [
        _ranked("a", "keyword", 1.0, content="A" * 5000),
        _ranked("b", "keyword", 1.0, content="B" * 5000),
    ]
    with collecting() as c:
        docs, _ = await ce._filter_sent_cards(ranked, None, 1)
    assert {d.name for d in docs} == {"A", "B"}
    assert [d for d in c.drops if d.stage == "context.tier_budget"] == []


async def test_prompt_order_omission_emits_provenance_event(seeded_rp):
    """A depth-0 section present this turn but omitted from a `prompt_order` list is
    dropped — and now emits a DropEvent keyed by the section name.

    Mutation (remove the omission emission in _apply_prompt_order): no event for the
    omitted section → reds, while the reorder/drop behavior is unchanged.
    """
    pa = seeded_rp.container.prompt_assembler
    static = [("writing_principles", "WP"), ("rp_guidelines", "RG")]
    with collecting() as c:
        ordered = pa._apply_prompt_order(static, [], set(), ["writing_principles"])

    emitted = [n for n, _ in ordered if n != "__boundary__"]
    assert emitted == ["writing_principles"], "only the listed section is kept"
    order_drops = [d for d in c.drops if d.stage == "prompt.section_order"]
    assert [d.item_id for d in order_drops] == ["rp_guidelines"], (
        "the present-but-omitted section must emit one DropEvent keyed by its name"
    )


async def test_example_reserve_drop_emits_provenance_event(seeded_rp):
    """Example pairs trimmed to fit the token-budget reserve emit a summary DropEvent.
    model_context_window=4500 with default max_tokens=4000 + safety_margin=500 →
    total=0 → reserve_budget=0 → every pair is dropped.

    Mutation (remove the emission in _fit_examples_to_reserve): no event → reds, while
    the trim/log behavior is unchanged.
    """
    pa = seeded_rp.container.prompt_assembler
    pa._prompt_config_override = PromptConfig(
        token_budget=TokenBudgetConfig(model_context_window=4500),
    )
    examples = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there friend"},
    ]
    with collecting() as c:
        kept = pa._fit_examples_to_reserve(examples)

    assert kept == [], "a zero reserve budget drops every example pair"
    ex_drops = [d for d in c.drops if d.stage == "prompt.example_reserve"]
    assert len(ex_drops) == 1, "one summary DropEvent for the trimmed example dialogue"
    assert ex_drops[0].item_kind == "example_pair"
    assert ex_drops[0].reason == "reserve_budget"


# ── Subsystem-failure swallows (context_engine) ──────────────────────────


class _AsyncBoom:
    """Stand-in dependency whose every awaited attribute call raises."""

    def __getattr__(self, _name):
        async def _raise(*_a, **_k):
            raise RuntimeError("subsystem down")
        return _raise


async def test_semantic_search_failure_emits_subsystem_drop(seeded_rp):
    """A failed vector search degrades to [] AND emits a subsystem DropEvent.
    Mutation (remove the record_drop): no event → reds; the [] return is unchanged."""
    ce = seeded_rp.container.context_engine
    ce.vector_search = _AsyncBoom()
    with collecting() as c:
        result = await ce._semantic_search("anything", rp_folder=seeded_rp.rp_folder)
    assert result == [], "failed subsystem degrades to empty (behavior unchanged)"
    drops = [d for d in c.drops if d.stage == "context.semantic_search"]
    assert len(drops) == 1
    assert drops[0].item_kind == "subsystem" and drops[0].reason == "failure"


async def test_past_exchange_failure_emits_subsystem_drop(seeded_rp):
    ce = seeded_rp.container.context_engine
    ce.lance_store = _AsyncBoom()
    with collecting() as c:
        result = await ce._search_past_exchanges("q", seeded_rp.rp_folder, "main", 5, None)
    assert result == []
    drops = [d for d in c.drops if d.stage == "context.past_exchanges"]
    assert len(drops) == 1 and drops[0].reason == "failure"


async def test_extracted_memories_failure_emits_subsystem_drop(seeded_rp, monkeypatch):
    ce = seeded_rp.container.context_engine

    async def _raise(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ce.db, "fetch_all", _raise)
    with collecting() as c:
        result = await ce._get_extracted_memories("q", seeded_rp.rp_folder, "main", [])
    assert result == []
    drops = [d for d in c.drops if d.stage == "context.extracted_memories"]
    assert len(drops) == 1 and drops[0].reason == "failure"


async def test_writing_intelligence_failure_emits_subsystem_drop(seeded_rp):
    ce = seeded_rp.container.context_engine

    class _SyncBoom:
        def prepare(self, *_a, **_k):
            raise RuntimeError("writing down")

    ce.writing_intelligence = _SyncBoom()
    with collecting() as c:
        result = await ce._get_writing_constraints("msg", None)
    assert result is None
    drops = [d for d in c.drops if d.stage == "context.writing_intelligence"]
    assert len(drops) == 1 and drops[0].reason == "failure"


# ── Orphan flag: produced − injected − dropped ───────────────────────────


async def test_orphan_flag_marks_rendered_lorebook_injected(seeded_rp):
    """A produced lorebook entry that the assembler renders into the world_info
    section shows as injected — NOT an orphan (proves the produce↔inject wiring +
    consistent keys across context_engine and prompt_assembler).

    Mutation (remove the record_injected in _build_dynamic_sections's world_info
    loop): the produced entry is never marked injected → it becomes an orphan → the
    orphans==[] assertion reds. A produced item with NO render branch (e.g. a new
    producer the assembler wasn't taught to inject) surfaces the same way — the
    producer-with-no-consumer signal this flag exists for."""
    from rp_engine.models.context import ContextResponse, LorebookEntryHit

    pa = seeded_rp.container.prompt_assembler
    resp = ContextResponse(
        current_exchange=1,
        lorebook_entries=[
            LorebookEntryHit(entry_id=7, name="Dragons", content="Dragons.", scope="rp"),
        ],
    )
    with collecting() as c:
        # produce side (mirrors context_engine.get_context recording produced items)
        record_produced("lorebook", "lorebook:7")
        # inject side renders the world_info section → record_injected
        pa._build_dynamic_sections(resp)

    assert c.orphans() == [], "the rendered lorebook entry must be injected, not orphaned"
