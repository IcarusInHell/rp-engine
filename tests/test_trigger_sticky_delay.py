"""Phase 5a — trigger sticky/delay + Layer-B keyword stemming.

The trigger grammar had **no** tests before this phase (dormant capability), so
these lock the new firing model and the morphology-aware matching:

- sticky_turns N: re-inject for N-1 turns after a condition-driven fire.
- delay_turns N: require N *consecutive* matching turns before firing.
- stemming (default-on, union with raw substring): a match that fired before
  still fires; inflections additionally match. The kill-switch
  (``prompt.trigger_stemming``) restores exact matching, and a stem-only fire is
  logged (over-firing is diagnosable, not silent).

Defaults (sticky=1, delay=0) must reproduce the pre-5a behavior exactly.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from rp_engine.services.trigger_evaluator import TriggerEvaluator

pytestmark = pytest.mark.asyncio


def _patch_stemming(monkeypatch, enabled: bool) -> None:
    """Force ``prompt.trigger_stemming`` for the evaluator, order-independent of
    any cached global config."""
    monkeypatch.setattr(
        "rp_engine.services.trigger_evaluator.get_config",
        lambda: SimpleNamespace(prompt=SimpleNamespace(trigger_stemming=enabled)),
    )


async def _insert_trigger(
    db, *, tid="t1", rp="rp", expr='any("sword")', match_mode="any",
    sticky_turns=1, delay_turns=0, cooldown_turns=0, inject_content="NOTE",
) -> None:
    conds = json.dumps([{"type": "expression", "expr": expr}])
    fut = await db.enqueue_write(
        """INSERT INTO situational_triggers
           (id, rp_folder, name, inject_type, inject_content, conditions, match_mode,
            priority, cooldown_turns, sticky_turns, delay_turns, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        [tid, rp, "T", "context_note", inject_content, conds, match_mode, 0,
         cooldown_turns, sticky_turns, delay_turns],
    )
    await fut


# ---------------------------------------------------------------------------
# Sticky
# ---------------------------------------------------------------------------

async def test_sticky_persists_after_fire(db, monkeypatch):
    """sticky_turns=3: fires on match (turn 5), then re-injects on the 2 following
    turns without a match, then stops. Mutation: sticky_active=False → turns 6/7
    return empty."""
    _patch_stemming(monkeypatch, False)
    await _insert_trigger(db, sticky_turns=3, expr='any("sword")')
    te = TriggerEvaluator(db)

    fired5 = await te.evaluate_all("rp", "main", "she drew a sword", {}, 5)
    fired6 = await te.evaluate_all("rp", "main", "nothing happens", {}, 6)
    fired7 = await te.evaluate_all("rp", "main", "still nothing", {}, 7)
    fired8 = await te.evaluate_all("rp", "main", "still nothing", {}, 8)

    assert len(fired5) == 1 and "sticky" not in fired5[0].matched_conditions[0]
    assert len(fired6) == 1 and "sticky" in fired6[0].matched_conditions[0]
    assert len(fired7) == 1, "sticky window (3 turns) should still re-inject at turn 7"
    assert fired8 == [], "sticky window expired at turn 8 (current-last=3 not < 3)"


async def test_sticky_default_no_persistence(db, monkeypatch):
    """sticky_turns=1 (default) = pre-5a: fires only on the matching turn."""
    _patch_stemming(monkeypatch, False)
    await _insert_trigger(db, sticky_turns=1, expr='any("sword")')
    te = TriggerEvaluator(db)
    assert len(await te.evaluate_all("rp", "main", "a sword", {}, 1)) == 1
    assert await te.evaluate_all("rp", "main", "nothing", {}, 2) == [], (
        "sticky_turns=1 must not re-inject — default behavior changed"
    )


# ---------------------------------------------------------------------------
# Delay
# ---------------------------------------------------------------------------

async def test_delay_requires_consecutive_matches(db, monkeypatch):
    """delay_turns=2: first match arms, second consecutive match fires."""
    _patch_stemming(monkeypatch, False)
    await _insert_trigger(db, delay_turns=2, expr='any("storm")')
    te = TriggerEvaluator(db)
    assert await te.evaluate_all("rp", "main", "a storm brews", {}, 1) == [], (
        "delay_turns=2 must not fire on the first match"
    )
    assert len(await te.evaluate_all("rp", "main", "the storm hits", {}, 2)) == 1, (
        "delay_turns=2 must fire on the second consecutive match"
    )


async def test_delay_resets_on_miss(db, monkeypatch):
    """A non-matching turn resets the consecutive counter (consecutive, not
    cumulative) — so match/miss/match does not fire."""
    _patch_stemming(monkeypatch, False)
    await _insert_trigger(db, delay_turns=2, expr='any("storm")')
    te = TriggerEvaluator(db)
    await te.evaluate_all("rp", "main", "a storm", {}, 1)        # c=1
    await te.evaluate_all("rp", "main", "calm skies", {}, 2)     # c=0 (reset)
    fired = await te.evaluate_all("rp", "main", "a storm", {}, 3)  # c=1
    assert fired == [], "delay counter must reset on a miss (consecutive, not cumulative)"


# ---------------------------------------------------------------------------
# Layer-B stemming
# ---------------------------------------------------------------------------

async def test_stemmed_match_both_sides(db, monkeypatch):
    """Keyword 'running' matches text 'he runs' — requires BOTH the arg AND the
    text to be stemmed (they meet at 'run'). One-sided-stem mutation guard:
    stemming only one side leaves 'running' vs {'run'/'runs'} unmatched → red."""
    _patch_stemming(monkeypatch, True)
    await _insert_trigger(db, expr='any("running")')
    te = TriggerEvaluator(db)
    assert len(await te.evaluate_all("rp", "main", "he runs fast", {}, 1)) == 1, (
        "inflected keyword vs inflected text did not match — a side skipped stemming"
    )


async def test_stemmed_match_keyword_inflected_text_base(db, monkeypatch):
    """The reverse direction: keyword 'swords' matches text containing 'sword'."""
    _patch_stemming(monkeypatch, True)
    await _insert_trigger(db, expr='any("swords")')
    te = TriggerEvaluator(db)
    assert len(await te.evaluate_all("rp", "main", "a single sword", {}, 1)) == 1


async def test_killswitch_off_restores_exact(db, monkeypatch):
    """With stemming disabled, 'running' no longer matches 'he runs' (raw only)."""
    _patch_stemming(monkeypatch, False)
    await _insert_trigger(db, expr='any("running")')
    te = TriggerEvaluator(db)
    assert await te.evaluate_all("rp", "main", "he runs fast", {}, 1) == [], (
        "kill-switch off must restore exact matching"
    )


async def test_stem_only_fire_is_logged(db, monkeypatch, caplog):
    """A fire that depends on stemming (raw substring would miss) logs a warning.
    Mutation: drop the warn branch → no record. Keyword 'running' vs 'he runs':
    'running' is not a substring of 'he runs', so the fire is stem-only."""
    _patch_stemming(monkeypatch, True)
    await _insert_trigger(db, expr='any("running")')
    te = TriggerEvaluator(db)
    with caplog.at_level(logging.WARNING):
        fired = await te.evaluate_all("rp", "main", "he runs fast", {}, 1)
    assert len(fired) == 1
    assert any("ONLY due to stemmed" in r.message for r in caplog.records), (
        "stem-only fire was not observable (no warning logged)"
    )


async def test_none_inversion_stemming_can_suppress(db, monkeypatch):
    """none() is the one inversion: stemming makes a concept 'present' more often,
    so it can SUPPRESS a fire. none('running') on 'he runs' fires with stemming
    OFF (raw: 'running' absent) but is suppressed with stemming ON ('run' present).
    Locks the documented non-additive behavior of none()."""
    await _insert_trigger(db, expr='none("running")')
    te = TriggerEvaluator(db)

    _patch_stemming(monkeypatch, False)
    assert len(await te.evaluate_all("rp", "main", "he runs fast", {}, 1)) == 1, (
        "none('running') should fire when 'running' is absent (raw)"
    )

    _patch_stemming(monkeypatch, True)
    assert await te.evaluate_all("rp", "main", "he runs fast", {}, 2) == [], (
        "stemming should make 'run' present → none() suppressed (documented inversion)"
    )


async def test_raw_substring_fire_not_flagged_stem_only(db, monkeypatch, caplog):
    """A genuine raw-substring match is NOT flagged stem-only (precision)."""
    _patch_stemming(monkeypatch, True)
    await _insert_trigger(db, expr='any("sword")')
    te = TriggerEvaluator(db)
    with caplog.at_level(logging.WARNING):
        fired = await te.evaluate_all("rp", "main", "a sword gleams", {}, 1)
    assert len(fired) == 1
    assert not any("ONLY due to stemmed" in r.message for r in caplog.records), (
        "raw substring match wrongly flagged as stem-only"
    )
