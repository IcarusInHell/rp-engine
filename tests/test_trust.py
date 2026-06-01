"""Foundation: trust stages + the DIRECTIONAL read contract.

Trust is asymmetric by design: ``(character_a, character_b)`` means
"character_a's trust toward character_b". Alice→Bob and Bob→Alice are distinct
rows and must read back distinctly.

The read helpers currently OR/merge both directions, conflating them. The
directional tests below assert the *corrected* (Phase 2) behavior and are
``xfail(strict=True)`` until that fix lands — a strict xfail will FAIL on an
unexpected pass, which is the signal to convert them to hard assertions.
"""

from __future__ import annotations

import pytest

from rp_engine.utils.trust import (
    fetch_trust_map,
    fetch_trust_pair,
    resolve_trust_for_pair,
    trust_stage,
)
from tests.assertions import assert_directional
from tests.factories import (
    RP_FOLDER,
    seed_trust_baseline,
    seed_trust_modification,
)


# ---- trust_stage: pure, current behavior ----

@pytest.mark.parametrize(
    "score,stage",
    [
        (-50, "hostile"),
        (-30, "antagonistic"),
        (-15, "suspicious"),
        (-5, "wary"),
        (0, "neutral"),
        (5, "neutral"),
        (15, "familiar"),
        (25, "trusted"),
        (50, "devoted"),
    ],
)
def test_trust_stage_thresholds(score, stage):
    assert trust_stage(score) == stage


def test_trust_stage_clamps_out_of_range():
    assert trust_stage(-999) == "hostile"
    assert trust_stage(999) == "devoted"


# ---- single-direction reads (current behavior, no conflation in play) ----

async def test_fetch_trust_pair_single_direction(db):
    await seed_trust_baseline(db, "Alice", "Bob", 5)
    await seed_trust_modification(db, "Alice", "Bob", 3)
    baseline, mod_sum = await fetch_trust_pair(db, RP_FOLDER, "main", "Alice", "Bob")
    assert (baseline, mod_sum) == (5, 3)


async def test_resolve_trust_live_score(db):
    await seed_trust_baseline(db, "Alice", "Bob", 5)
    await seed_trust_modification(db, "Alice", "Bob", 3)
    res = await resolve_trust_for_pair(db, "Alice", "Bob", RP_FOLDER, "main")
    assert res.baseline == 5
    assert res.modification_sum == 3
    assert res.live_score == 8
    assert res.stage == "neutral"


# ---- DIRECTIONAL contract (Phase 2 read-fix landed — hard assertions) ----

async def test_fetch_trust_pair_is_directional(db):
    await seed_trust_baseline(db, "Alice", "Bob", 5)
    await seed_trust_baseline(db, "Bob", "Alice", 10)
    await seed_trust_modification(db, "Alice", "Bob", 3)
    await seed_trust_modification(db, "Bob", "Alice", 7)

    ab = await fetch_trust_pair(db, RP_FOLDER, "main", "Alice", "Bob")
    ba = await fetch_trust_pair(db, RP_FOLDER, "main", "Bob", "Alice")

    assert ab == (5, 3), "Alice→Bob must read its own row only"
    assert ba == (10, 7), "Bob→Alice must read its own row only"
    assert_directional(ab, ba, label="trust pair")


async def test_resolve_trust_is_directional(db):
    await seed_trust_baseline(db, "Alice", "Bob", 5)
    await seed_trust_baseline(db, "Bob", "Alice", 10)
    await seed_trust_modification(db, "Alice", "Bob", 3)
    await seed_trust_modification(db, "Bob", "Alice", 7)

    ab = await resolve_trust_for_pair(db, "Alice", "Bob", RP_FOLDER, "main")
    ba = await resolve_trust_for_pair(db, "Bob", "Alice", RP_FOLDER, "main")

    assert ab.live_score == 8, "Alice→Bob = 5 + 3"
    assert ba.live_score == 17, "Bob→Alice = 10 + 7"
    assert_directional(ab.live_score, ba.live_score, label="live trust score")


async def test_fetch_trust_map_keeps_directions_separate(db):
    """fetch_trust_map already keys by stored (a,b) order — locks that it stays
    directional (two distinct entries, never merged) through the Phase 2 changes."""
    await seed_trust_baseline(db, "Alice", "Bob", 5)
    await seed_trust_baseline(db, "Bob", "Alice", 10)

    trust_map = await fetch_trust_map(db, RP_FOLDER, "main")
    assert trust_map[("alice", "bob")][0] == 5
    assert trust_map[("bob", "alice")][0] == 10
    assert_directional(
        trust_map[("alice", "bob")][0],
        trust_map[("bob", "alice")][0],
        label="trust map direction",
    )
