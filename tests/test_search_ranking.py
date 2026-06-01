"""Unit: utils/search_ranking.reciprocal_rank_fusion (Phase 7b).

The RRF combine was extracted out of ``search_exchanges`` into a pure helper so
it is testable without the embed/LanceDB stack — hybrid ranking is otherwise
non-deterministic under the hash-embed test stub (see the exchanges-router test
header), so nothing else verifies the fusion math. These assert exact float
values: a wrong weight (0.7/0.3) or ``k`` (60) or off-by-one in ``rank + 1``
survives a shape-only check but fails here.
"""

from __future__ import annotations

import pytest

from rp_engine.utils.search_ranking import reciprocal_rank_fusion


def test_rrf_fuses_both_sources_with_exact_weights():
    # ex1: semantic only (rank 0). ex2: keyword only (rank 0). ex3: both (rank 1 each).
    semantic = {1: 0.9, 3: 0.5}
    keyword = {2: 0.9, 3: 0.7}
    scored = reciprocal_rank_fusion(semantic, keyword)

    assert scored[1] == pytest.approx(0.7 / 61)          # 0.7 / (60 + 0 + 1)
    assert scored[2] == pytest.approx(0.3 / 61)          # 0.3 / (60 + 0 + 1)
    assert scored[3] == pytest.approx(0.7 / 62 + 0.3 / 62)  # both, rank 1 each


def test_rrf_item_in_both_sources_outranks_single_source():
    """An exchange present in both sources must outrank one in either alone."""
    semantic = {1: 0.9, 3: 0.5}
    keyword = {2: 0.9, 3: 0.7}
    scored = reciprocal_rank_fusion(semantic, keyword)
    assert scored[3] > scored[1] > scored[2], scored


def test_rrf_empty_sources_yield_empty():
    assert reciprocal_rank_fusion({}, {}) == {}
