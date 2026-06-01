"""Search-result ranking helpers.

``reciprocal_rank_fusion`` is the hybrid-search combine extracted verbatim from
``search_exchanges`` in Phase 7b (split-exchanges-router R3: the RRF combine
lives in a dedicated helper module owned by ``ExchangeSearchService``; the
vector store stays narrowly the vector store). Pure function — two score maps in,
one fused score map out — so it is unit-testable directly without the LLM/embed
stack.
"""

from __future__ import annotations


def reciprocal_rank_fusion(
    semantic_hits: dict[int, float],
    keyword_hits: dict[int, float],
    *,
    k: int = 60,
    semantic_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> dict[int, float]:
    """Fuse two ranked score maps into one via weighted Reciprocal Rank Fusion.

    Each source is ranked by descending score; an item's contribution is
    ``weight / (k + rank + 1)`` (rank is 0-based). An item present in both
    sources accumulates both contributions. Keys are ``exchange_number``.
    """
    scored: dict[int, float] = {}
    semantic_ranked = sorted(semantic_hits.items(), key=lambda x: -x[1])
    keyword_ranked = sorted(keyword_hits.items(), key=lambda x: -x[1])
    for rank, (ex_num, _) in enumerate(semantic_ranked):
        scored[ex_num] = scored.get(ex_num, 0) + semantic_weight / (k + rank + 1)
    for rank, (ex_num, _) in enumerate(keyword_ranked):
        scored[ex_num] = scored.get(ex_num, 0) + keyword_weight / (k + rank + 1)
    return scored
