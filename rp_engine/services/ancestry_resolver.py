"""Ancestry resolution for copy-on-write branching.

Resolves state by walking the branch ancestry graph. Each branch has a parent
(created_from) and a branch_point_exchange. The ancestry chain goes from the
current branch up to "main" (root).

Resolution algorithm for CoW tables (character_state_entries, scene_state_entries):
  for each (branch, max_exchange) in ancestry chain:
      query table WHERE branch=? AND exchange_number <= max_exchange
      ORDER BY exchange_number DESC LIMIT 1
      if found -> return
  return None (or card default)

Trust shortcut (no full ancestry walk needed):
  baseline = trust_baselines[char_a, char_b, branch] or card initial
  mods = SUM(trust_modifications WHERE char_a, char_b, branch)
  return baseline + mods
"""

from __future__ import annotations

import logging

from rp_engine.database import Database
from rp_engine.utils.lru_cache import LRUCache

logger = logging.getLogger(__name__)


class AncestryResolver:
    """Resolves state through the branch ancestry graph."""

    def __init__(self, db: Database) -> None:
        self.db = db
        # Cache: (rp_folder, branch) -> [(branch, max_exchange), ...]
        self._ancestry_cache: LRUCache[tuple[str, str], list[tuple[str, int]]] = LRUCache(maxsize=64)

    # ===================================================================
    # Ancestry Chain
    # ===================================================================

    async def get_ancestry_chain(
        self, rp_folder: str, branch: str
    ) -> list[tuple[str, int]]:
        """Get the ancestry chain from current branch to root.

        Returns list of (branch_name, max_exchange_number) tuples.
        The first entry is the current branch (max_exchange = infinity/very large).
        Each subsequent entry is a parent with max_exchange = branch_point_exchange.
        """
        cache_key = (rp_folder, branch)
        cached = self._ancestry_cache.get(cache_key)
        if cached is not None:
            return cached

        chain: list[tuple[str, int]] = []
        current = branch
        visited: set[str] = set()

        while current and current not in visited:
            visited.add(current)

            # Current branch has no upper limit on exchange_number
            if current == branch:
                chain.append((current, 2**31))  # effectively unlimited
            else:
                # Parent branches are capped at the branch_point_exchange
                # already added by the previous iteration
                pass

            # Look up parent
            row = await self.db.fetch_one(
                "SELECT created_from, branch_point_exchange FROM branches WHERE name = ? AND rp_folder = ?",
                [current, rp_folder],
            )
            if not row or not row.get("created_from"):
                break

            parent = row["created_from"]
            branch_point = row.get("branch_point_exchange") or 0
            chain.append((parent, branch_point))
            current = parent

        self._ancestry_cache.put(cache_key, chain)
        return chain

    @staticmethod
    def build_ancestry_sql(
        rp_folder: str,
        chain: list[tuple[str, int]],
        table_alias: str = "",
    ) -> tuple[str, list]:
        """Build a parameterized SQL WHERE clause from an ancestry chain.

        Returns ``(where_clause, params)`` suitable for an exchanges query.
        ``table_alias`` is prepended to column names (e.g. ``"e"`` → ``e.branch``).
        """
        prefix = f"{table_alias}." if table_alias else ""
        clauses: list[str] = []
        params: list = []
        for chain_branch, max_exchange in chain:
            clauses.append(f"({prefix}branch = ? AND {prefix}exchange_number <= ?)")
            params.extend([chain_branch, max_exchange])
        where = f"{prefix}rp_folder = ? AND ({' OR '.join(clauses)})"
        return where, [rp_folder] + params

    def invalidate_cache(self, rp_folder: str | None = None, branch: str | None = None) -> None:
        """Clear ancestry chain cache.

        If rp_folder/branch specified, only clear entries that include that branch.
        Otherwise, clear everything.
        """
        if rp_folder is None:
            self._ancestry_cache.clear()
            return

        keys_to_remove = [
            k for k in self._ancestry_cache
            if k[0] == rp_folder and (branch is None or k[1] == branch)
        ]
        for k in keys_to_remove:
            self._ancestry_cache.pop(k)

    # ===================================================================
    # Generic Resolution (single latest entry)
    # ===================================================================

    async def resolve_through_ancestry(
        self,
        table: str,
        rp_folder: str,
        branch: str,
        filters: dict[str, str | int] | None = None,
        exchange_number: int | None = None,
    ) -> dict | None:
        """Find the latest matching row by walking the ancestry chain.

        For CoW tables like character_state_entries and scene_state_entries.
        Returns the first matching row found (most recent in ancestry).
        """
        chain = await self.get_ancestry_chain(rp_folder, branch)

        for chain_branch, max_exchange in chain:
            # Cap the exchange_number if specified
            effective_max = min(max_exchange, exchange_number) if exchange_number else max_exchange

            where_parts = ["rp_folder = ?", "branch = ?", "exchange_number <= ?"]
            params: list = [rp_folder, chain_branch, effective_max]

            if filters:
                for col, val in filters.items():
                    where_parts.append(f"{col} = ?")
                    params.append(val)

            where_clause = " AND ".join(where_parts)
            row = await self.db.fetch_one(
                f"SELECT * FROM {table} WHERE {where_clause} ORDER BY exchange_number DESC LIMIT 1",
                params,
            )
            if row:
                return dict(row)

        return None

    # ===================================================================
    # Generic Collection (all matching entries from ancestry)
    # ===================================================================

    async def collect_from_ancestry(
        self,
        table: str,
        rp_folder: str,
        branch: str,
        filters: dict[str, str | int] | None = None,
        exchange_number: int | None = None,
        order_by: str = "exchange_number DESC",
        limit: int | None = None,
    ) -> list[dict]:
        """Collect ALL matching rows from the entire ancestry chain.

        For tables where we want all accumulated data across the
        ancestry chain, not just the latest entry.
        """
        chain = await self.get_ancestry_chain(rp_folder, branch)
        results: list[dict] = []

        for chain_branch, max_exchange in chain:
            effective_max = min(max_exchange, exchange_number) if exchange_number else max_exchange

            where_parts = ["rp_folder = ?", "branch = ?", "exchange_number <= ?"]
            params: list = [rp_folder, chain_branch, effective_max]

            if filters:
                for col, val in filters.items():
                    where_parts.append(f"{col} = ?")
                    params.append(val)

            where_clause = " AND ".join(where_parts)
            rows = await self.db.fetch_all(
                f"SELECT * FROM {table} WHERE {where_clause} ORDER BY {order_by}",
                params,
            )
            results.extend(dict(r) for r in rows)

        # Sort combined results and apply limit
        # Parse exchange_number for sorting
        if "DESC" in order_by:
            results.sort(key=lambda r: r.get("exchange_number", 0), reverse=True)
        else:
            results.sort(key=lambda r: r.get("exchange_number", 0))

        if limit:
            results = results[:limit]

        return results

    # ===================================================================
    # Character State Resolution
    # ===================================================================

    async def resolve_character_state(
        self,
        card_id: str,
        rp_folder: str,
        branch: str,
        exchange_number: int | None = None,
    ) -> dict | None:
        """Resolve the latest character runtime state through ancestry.

        Returns the most recent character_state_entries row for this card_id
        that is visible from the given branch/exchange point.
        """
        return await self.resolve_through_ancestry(
            table="character_state_entries",
            rp_folder=rp_folder,
            branch=branch,
            filters={"card_id": card_id},
            exchange_number=exchange_number,
        )

    # ===================================================================
    # Scene State Resolution
    # ===================================================================

    async def resolve_scene_state(
        self,
        rp_folder: str,
        branch: str,
        exchange_number: int | None = None,
    ) -> dict | None:
        """Resolve the latest scene state through ancestry."""
        return await self.resolve_through_ancestry(
            table="scene_state_entries",
            rp_folder=rp_folder,
            branch=branch,
            exchange_number=exchange_number,
        )

    # ===================================================================
    # Trust Resolution
    # ===================================================================

    async def resolve_trust(
        self,
        char_a: str,
        char_b: str,
        rp_folder: str,
        branch: str,
    ) -> dict:
        """Resolve current trust between two characters.

        Delegates to the consolidated resolve_trust_for_pair utility,
        then enriches with source_branch/source_exchange metadata.

        Returns dict with: baseline_score, branch_modifications_sum, live_score,
        trust_stage, source_branch, source_exchange.
        """
        from rp_engine.utils.trust import resolve_trust_for_pair

        resolution = await resolve_trust_for_pair(
            self.db, char_a, char_b, rp_folder, branch
        )

        # Look up audit metadata from the baseline row (if any)
        source_branch = None
        source_exchange = None
        baseline_row = await self.db.fetch_one(
            """SELECT source_branch, source_exchange FROM trust_baselines
               WHERE rp_folder = ? AND branch = ?
                 AND ((LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?))
                   OR (LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)))""",
            [rp_folder, branch, char_a, char_b, char_b, char_a],
        )
        if baseline_row:
            source_branch = baseline_row.get("source_branch")
            source_exchange = baseline_row.get("source_exchange")

        return {
            "baseline_score": resolution.baseline,
            "branch_modifications_sum": resolution.modification_sum,
            "live_score": resolution.live_score,
            "trust_stage": resolution.stage,
            "source_branch": source_branch,
            "source_exchange": source_exchange,
        }

    async def resolve_trust_full_history(
        self,
        char_a: str,
        char_b: str,
        rp_folder: str,
        branch: str,
    ) -> list[dict]:
        """Get the full trust modification history through ancestry.

        Walks the ancestry chain to collect all trust modifications for this pair.
        """
        chain = await self.get_ancestry_chain(rp_folder, branch)
        all_mods: list[dict] = []

        for chain_branch, max_exchange in chain:
            rows = await self.db.fetch_all(
                """SELECT * FROM trust_modifications
                   WHERE character_a = ? AND character_b = ?
                     AND rp_folder = ? AND branch = ?
                     AND exchange_number <= ?
                   ORDER BY exchange_number DESC""",
                [char_a, char_b, rp_folder, chain_branch, max_exchange],
            )
            all_mods.extend(dict(r) for r in rows)

        return all_mods
