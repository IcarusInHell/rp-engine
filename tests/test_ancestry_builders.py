"""Foundation: ancestry WHERE/SQL builders.

These pure builders are the backbone of branch-aware queries. The silent-drop
risk is a builder that *omits a parent clause*, making a child branch blind to
inherited rows. Every chain entry must appear in the output.
"""

from __future__ import annotations

from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.lance_store import _build_ancestry_where


def test_build_ancestry_sql_includes_every_chain_branch():
    chain = [("child", 2**31), ("main", 7)]
    where, params = AncestryResolver.build_ancestry_sql("TestRP", chain)

    assert where.count("OR") == 1, "two-branch chain must OR exactly two clauses"
    # rp_folder + (branch, max) per entry, in order.
    assert params == ["TestRP", "child", 2**31, "main", 7]
    assert "branch = ?" in where and "exchange_number <= ?" in where


def test_build_ancestry_sql_applies_table_alias():
    where, params = AncestryResolver.build_ancestry_sql(
        "TestRP", [("main", 5)], table_alias="e"
    )
    assert "e.rp_folder = ?" in where
    assert "e.branch = ?" in where
    assert "e.exchange_number <= ?" in where
    assert params == ["TestRP", "main", 5]


def test_lance_where_spans_ancestors_with_caps():
    chain = [("child", 2**31), ("main", 7)]
    where = _build_ancestry_where("TestRP", "child", max_exchange=3, ancestry_chain=chain)
    # Current branch capped at min(chain_cap, max_exchange) = 3; parent capped at 7.
    assert 'branch = "child" AND exchange_number <= 3' in where
    assert 'branch = "main" AND exchange_number <= 7' in where
    assert where.startswith('rp_folder = "TestRP"')
    assert " OR " in where, "ancestor clause must not be dropped"


def test_lance_where_single_branch_fallback():
    where = _build_ancestry_where("TestRP", "main", max_exchange=4, ancestry_chain=None)
    assert where == 'rp_folder = "TestRP" AND branch = "main" AND exchange_number <= 4'
