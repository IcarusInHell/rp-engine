"""Phase 5b — the lorebook REUSES TriggerEvaluator (no second matcher).

The whole thesis of Phase 5b is that the matching engine already exists: the
lorebook routes its conditions through ``TriggerEvaluator.evaluate_conditions``,
which shares the exact ``_match_conditions`` core that ``evaluate_all`` uses for
situational triggers. These tests guard against a forked second matcher and lock
the storage-shape decision forced by the evaluator being a FLAT parser.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rp_engine.services.trigger_evaluator import TriggerEvaluator

pytestmark = pytest.mark.asyncio


def _patch_stemming(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(
        "rp_engine.services.trigger_evaluator.get_config",
        lambda: SimpleNamespace(prompt=SimpleNamespace(trigger_stemming=enabled)),
    )


async def _insert_trigger(db, *, tid, rp, conditions, match_mode) -> None:
    conds = json.dumps(conditions)
    fut = await db.enqueue_write(
        """INSERT INTO situational_triggers
           (id, rp_folder, name, inject_type, inject_content, conditions, match_mode,
            priority, cooldown_turns, sticky_turns, delay_turns, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 1, 0, 1)""",
        [tid, rp, "T", "context_note", "NOTE", conds, match_mode],
    )
    await fut


# ---------------------------------------------------------------------------
# Reuse: identical condition → identical decision via trigger and lorebook path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem", [False, True])
async def test_near_identical_for_trigger_and_lorebook(db, monkeypatch, stem):
    """A ``near()`` condition must behave IDENTICALLY whether evaluated as a
    situational trigger (``evaluate_all`` → fires) or a lorebook entry
    (``evaluate_conditions`` → matched). This is the reuse-not-reinvention lock:
    both route through the same ``_match_conditions`` core.

    Mutation proof: if ``evaluate_conditions`` were a forked matcher (e.g. a bare
    substring scan ignoring the distance arg), the distance-fail case below would
    diverge — the trigger would not fire but the lorebook would match.
    """
    _patch_stemming(monkeypatch, stem)
    cond = [{"type": "expression", "expr": 'near("dwarf","anger",30)'}]
    await _insert_trigger(db, tid="t1", rp="rp", conditions=cond, match_mode="all")
    te = TriggerEvaluator(db)

    cases = [
        "the dwarf seethed with anger",                       # near → match
        "the dwarf stood. " + ("x " * 40) + "anger flared",   # too far → no match
        "a quiet elf walked by",                              # neither word → no match
    ]
    for text in cases:
        fired = await te.evaluate_all("rp", "main", text, {}, current_turn=1)
        trigger_fired = len(fired) == 1
        matched, _raw, _descs = await te.evaluate_conditions(
            cond, "all", text, {}, "rp", "main"
        )
        assert trigger_fired == matched, (
            f"trigger/lorebook diverged for {text!r}: "
            f"trigger={trigger_fired} lorebook={matched}"
        )


async def test_stem_only_observability_on_lorebook_path(db, monkeypatch):
    """``evaluate_conditions`` returns ``matched_raw`` distinct from ``matched``
    so the lorebook layer can flag a stem-only hit (same observability the
    trigger path has). The conservative stemmer only handles REGULAR inflections
    (``running``/``runs``→``run``; it does NOT map irregulars like
    ``dwarves``→``dwarv``≠``dwarf``), so use a regular case where the arg is also
    not a raw substring of the text.
    """
    _patch_stemming(monkeypatch, True)
    cond = [{"type": "expression", "expr": 'any("running")'}]
    te = TriggerEvaluator(db)
    matched, raw, _descs = await te.evaluate_conditions(
        cond, "any", "she runs fast home", {}, "rp", "main"
    )
    assert matched is True, "stemmed: 'running'/'runs'→'run' should match"
    assert raw is False, "exact: 'running' is not a substring of 'she runs fast home'"


# ---------------------------------------------------------------------------
# Storage-shape lock: compound scope is a condition LIST, NOT a nested expr.
# The evaluator is a flat parser — a nested all(any(...),near(...)) string
# mis-parses (confirmed Phase 5b). Layered scope MUST be multiple condition
# dicts + match_mode="all".
# ---------------------------------------------------------------------------

async def test_compound_scope_requires_condition_list_not_nested_expr(db, monkeypatch):
    _patch_stemming(monkeypatch, False)
    te = TriggerEvaluator(db)
    text = "the dwarven warrior seethed with anger"  # 'dwarven' + 'anger' co-occur

    # WRONG (the plan's prose, verbatim): a nested expression string. The flat
    # parser reads func=all, args=['dwarf','dwarven','dwarf','anger'] → requires
    # all four substrings → 'dwarf' absent → False. Silent under-fire.
    nested = [{"type": "expression",
               "expr": 'all(any("dwarf","dwarven"), near("dwarven","anger",100))'}]
    bad, _r, _d = await te.evaluate_conditions(nested, "all", text, {}, "rp", "main")
    assert bad is False, "nested expr mis-parses — proves it must not be used"

    # RIGHT: a condition LIST + match_mode='all' composes correctly.
    layered = [
        {"type": "expression", "expr": 'any("dwarf","dwarven")'},
        {"type": "expression", "expr": 'near("dwarven","anger",100)'},
    ]
    good, _r, _d = await te.evaluate_conditions(layered, "all", text, {}, "rp", "main")
    assert good is True, "condition list + match_mode=all is the correct shape"

    # And the layered scope stays SILENT when only one leg holds.
    one_leg = "the dwarven smith hammered a blade"  # dwarven present, no anger
    silent, _r, _d = await te.evaluate_conditions(layered, "all", one_leg, {}, "rp", "main")
    assert silent is False, "layered all() must not fire on a single leg"
