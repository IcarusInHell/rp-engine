"""ExchangeSearchService — multi-mode exchange history search.

Extracted verbatim from ``search_exchanges`` in Phase 7b (split-exchanges-router
Option B). Owns semantic (LanceDB) + keyword (SQLite LIKE) + hybrid (RRF combine)
search, ancestry-aware scoping, and the bookmark/annotation enrichment fetch. The
RRF combine lives in ``utils/search_ranking.reciprocal_rank_fusion`` (R3); the
ancestry WHERE-clause is built directly off ``AncestryResolver.build_ancestry_sql``
to avoid a service→router import.
"""

from __future__ import annotations

import logging

from rp_engine.database import Database
from rp_engine.models.exchange import ExchangeSearchHit, SearchMode
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.branch_manager import BranchManager
from rp_engine.services.lance_store import LanceStore
from rp_engine.utils.exchange_sql import BOOKMARK_ANNOTATION_JOINS
from rp_engine.utils.json_helpers import safe_parse_json_array
from rp_engine.utils.search_ranking import reciprocal_rank_fusion
from rp_engine.utils.text import truncate_text

logger = logging.getLogger(__name__)


class ExchangeSearchService:
    """Multi-mode exchange-history search — semantic (LanceDB), keyword (SQLite LIKE), and hybrid (RRF), all ancestry-scoped."""

    def __init__(
        self,
        db: Database,
        lance_store: LanceStore | None,
        branch_manager: BranchManager,
    ):
        self.db = db
        self.lance_store = lance_store
        self.branch_manager = branch_manager

    async def search(
        self,
        *,
        q: str,
        rp_folder: str,
        branch: str,
        mode: SearchMode,
        limit: int,
        min_score: float,
    ) -> list[ExchangeSearchHit]:
        """Search exchange history via semantic, keyword, or hybrid mode."""
        results: list[ExchangeSearchHit] = []

        # Get ancestry chain for cross-branch search
        ancestry_chain = await self.branch_manager.get_ancestry_chain(rp_folder, branch)

        # --- Semantic search via LanceDB ---
        lance_hits: dict[int, float] = {}
        if mode in (SearchMode.semantic, SearchMode.hybrid) and self.lance_store:
            lance_results = await self.lance_store.search_exchanges(
                query_text=q, rp_folder=rp_folder, branch=branch, limit=limit * 2,
                ancestry_chain=ancestry_chain,
            )
            for hit in lance_results:
                ex_num = hit.metadata.get("exchange_number")
                if ex_num is not None and hit.score >= min_score:
                    # Keep highest score per exchange
                    if ex_num not in lance_hits or hit.score > lance_hits[ex_num]:
                        lance_hits[ex_num] = hit.score

        # --- Keyword search via SQLite LIKE (ancestry-aware) ---
        keyword_hits: dict[int, float] = {}
        if mode in (SearchMode.keyword, SearchMode.hybrid):
            like_param = f"%{q}%"
            ancestry_where, ancestry_params = AncestryResolver.build_ancestry_sql(
                rp_folder, ancestry_chain, table_alias="e"
            )
            kw_rows = await self.db.fetch_all(
                f"""SELECT exchange_number FROM exchanges e
                   WHERE {ancestry_where}
                   AND (user_message LIKE ? OR assistant_response LIKE ?)
                   LIMIT ?""",
                ancestry_params + [like_param, like_param, limit * 2],
            )
            for row in kw_rows:
                keyword_hits[row["exchange_number"]] = 0.8  # flat keyword score

        # --- Combine via RRF for hybrid, or use single source ---
        if mode == SearchMode.hybrid:
            scored = reciprocal_rank_fusion(lance_hits, keyword_hits)
        elif mode == SearchMode.semantic:
            scored = lance_hits
        else:
            scored = keyword_hits

        if not scored:
            return []

        # Sort by score descending, take top N
        top = sorted(scored.items(), key=lambda x: -x[1])[:limit]
        exchange_numbers = [ex_num for ex_num, _ in top]

        # Fetch full exchange data + bookmark/annotation enrichment (ancestry-aware)
        placeholders = ",".join("?" for _ in exchange_numbers)
        ancestry_where, ancestry_params = AncestryResolver.build_ancestry_sql(
            rp_folder, ancestry_chain, table_alias="e"
        )
        rows = await self.db.fetch_all(
            f"""SELECT e.*, b.name AS bookmark_name,
                       COALESCE(a.acnt, 0) AS annotation_count
                FROM exchanges e{BOOKMARK_ANNOTATION_JOINS}
                WHERE {ancestry_where}
                AND e.exchange_number IN ({placeholders})""",
            ancestry_params + exchange_numbers,
        )

        row_map = {r["exchange_number"]: r for r in rows}
        for ex_num, score in top:
            row = row_map.get(ex_num)
            if not row:
                continue
            npcs = safe_parse_json_array(row.get("npcs_involved"))
            bookmark_name = row.get("bookmark_name")
            annotation_count = row.get("annotation_count", 0) or 0
            results.append(ExchangeSearchHit(
                exchange_number=row["exchange_number"],
                exchange_id=row["id"],
                user_message_snippet=truncate_text(row["user_message"]),
                assistant_response_snippet=truncate_text(row["assistant_response"]),
                relevance_score=round(score, 4),
                timestamp=row["created_at"],
                session_id=row.get("session_id"),
                npcs_mentioned=npcs if npcs else None,
                is_bookmarked=bookmark_name is not None,
                bookmark_name=bookmark_name,
                annotation_count=annotation_count,
            ))

        return results
