"""Foundation: BranchManager ancestry chain + snapshots.

THE foundation-level silent-drop test: a freshly-created child branch — before it
has any exchanges of its own — must still *see its parent's history*. The whole
branched-RP NPC bug (Phase 1) is a downstream instance of getting this wrong, so
we lock the correct behavior at the source here.
"""

from __future__ import annotations

from tests.assertions import assert_count_exact, assert_nonempty
from tests.factories import (
    RP_FOLDER,
    insert_exchange,
    insert_session,
    seed_trust_baseline,
)


async def _seed_main_with_exchanges(db, branch_manager, n: int) -> None:
    await branch_manager.ensure_main_branch(RP_FOLDER)
    await insert_session(db)
    for i in range(1, n + 1):
        await insert_exchange(db, i)


async def test_fresh_branch_sees_parent_exchanges(db, branch_manager):
    """A child branch with zero exchanges of its own must inherit the parent's."""
    await _seed_main_with_exchanges(db, branch_manager, 3)
    await branch_manager.create_branch("B", RP_FOLDER, branch_from="main")

    rows = await branch_manager.get_exchanges_with_ancestry(RP_FOLDER, "B", limit=5)

    assert_nonempty(rows, label="branch B exchanges via ancestry")
    assert_count_exact(len(rows), 3, label="parent exchanges visible from fresh branch B")
    assert {r["exchange_number"] for r in rows} == {1, 2, 3}


async def test_ancestry_chain_includes_parent(db, branch_manager, resolver):
    await _seed_main_with_exchanges(db, branch_manager, 2)
    await branch_manager.create_branch("B", RP_FOLDER, branch_from="main")

    chain = await resolver.get_ancestry_chain(RP_FOLDER, "B")
    branches = {b for b, _ in chain}
    assert branches == {"B", "main"}, "chain must walk B → main, dropping neither"
    main_cap = dict(chain)["main"]
    assert main_cap == 2, "parent must be capped at the branch point (latest exchange)"


async def test_create_branch_snapshots_trust_baselines(db, branch_manager):
    await branch_manager.ensure_main_branch(RP_FOLDER)
    await insert_session(db)
    await insert_exchange(db, 1)
    await seed_trust_baseline(db, "Alice", "Bob", 5)

    await branch_manager.create_branch("B", RP_FOLDER, branch_from="main")

    snap = await db.fetch_one(
        """SELECT baseline_score FROM trust_baselines
           WHERE rp_folder = ? AND branch = ? AND character_a = ? AND character_b = ?""",
        [RP_FOLDER, "B", "Alice", "Bob"],
    )
    assert snap is not None, "trust baseline must be snapshotted onto the child branch"
    assert snap["baseline_score"] == 5


async def test_fresh_start_branch_has_no_parent(db, branch_manager):
    """branch_point_exchange=0 → a clean root branch (no inherited history)."""
    await _seed_main_with_exchanges(db, branch_manager, 3)
    await branch_manager.create_branch(
        "Fresh", RP_FOLDER, branch_from="main", branch_point_exchange=0
    )

    chain = await branch_manager.get_ancestry_chain(RP_FOLDER, "Fresh")
    assert {b for b, _ in chain} == {"Fresh"}, "fresh-start branch must have no parent"

    rows = await branch_manager.get_exchanges_with_ancestry(RP_FOLDER, "Fresh", limit=5)
    assert rows == [], "fresh-start branch inherits no exchanges"
