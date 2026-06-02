"""LanceDB vector store for exchange embeddings and card vectors.

Provides CoW-compatible vector storage with native versioning. Stores
embeddings alongside metadata for filtered ANN search. Replaces the
SQLite BLOB-based vector storage from vector_search.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)


def _lance_escape(val: str) -> str:
    """Escape quotes in values interpolated into LanceDB .where() filters."""
    return val.replace("\\", "\\\\").replace('"', '\\"')


def _build_ancestry_where(
    rp_folder: str,
    branch: str,
    max_exchange: int | None = None,
    ancestry_chain: list[tuple[str, int]] | None = None,
) -> str:
    """Build a LanceDB WHERE clause, optionally spanning ancestor branches.

    If ``ancestry_chain`` is provided, produces a compound OR filter:
    ``rp_folder = "X" AND ((branch = "child" AND exchange_number <= N) OR ...)``
    """
    rp_filter = f'rp_folder = "{_lance_escape(rp_folder)}"'

    if ancestry_chain:
        clauses: list[str] = []
        for chain_branch, chain_max in ancestry_chain:
            cap = chain_max
            if chain_branch == branch and max_exchange is not None:
                cap = min(cap, max_exchange)
            clauses.append(
                f'(branch = "{_lance_escape(chain_branch)}" AND exchange_number <= {int(cap)})'
            )
        return f"{rp_filter} AND ({' OR '.join(clauses)})"

    # Single-branch fallback
    where = f'{rp_filter} AND branch = "{_lance_escape(branch)}"'
    if max_exchange is not None:
        where += f" AND exchange_number <= {int(max_exchange)}"
    return where

# Schema for exchange vectors
EXCHANGE_SCHEMA = pa.schema([
    ("text", pa.string()),
    ("vector", pa.list_(pa.float32())),
    ("exchange_number", pa.int32()),
    ("rp_folder", pa.string()),
    ("branch", pa.string()),
    ("session_id", pa.string()),
    ("speaker", pa.string()),  # "user" or "assistant"
    ("in_story_timestamp", pa.string()),
    ("chunking_hash", pa.string()),
])

# Schema for card vectors
CARD_SCHEMA = pa.schema([
    ("text", pa.string()),
    ("vector", pa.list_(pa.float32())),
    ("card_id", pa.string()),
    ("card_type", pa.string()),
    ("rp_folder", pa.string()),
    ("file_path", pa.string()),
    ("chunk_index", pa.int32()),
    ("total_chunks", pa.int32()),
])


@dataclass
class LanceSearchResult:
    """One LanceDB vector-search hit — matched text, similarity score (1 − distance), and source metadata."""

    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class LanceStore:
    """LanceDB-backed vector store for exchanges and cards."""

    def __init__(
        self,
        db_path: str | Path,
        embed_fn: Callable | None = None,
        dimension: int = 384,
        embedding_model: str = "unknown",
    ) -> None:
        self.db_path = Path(db_path)
        self._embed_fn = embed_fn
        self._dimension = dimension
        self._embedding_model = embedding_model
        self._db = None
        self._exchange_table = None
        self._card_table = None

    async def initialize(self) -> None:
        """Open or create the LanceDB database and tables."""
        import asyncio

        import lancedb

        self.db_path.mkdir(parents=True, exist_ok=True)
        self._db = await asyncio.to_thread(lancedb.connect, str(self.db_path))

        # Open or create tables (try open first, create if missing)
        try:
            self._exchange_table = self._db.open_table("exchange_vectors")
        except Exception:
            self._exchange_table = self._db.create_table(
                "exchange_vectors",
                schema=EXCHANGE_SCHEMA,
            )

        try:
            self._card_table = self._db.open_table("card_vectors")
        except Exception:
            self._card_table = self._db.create_table(
                "card_vectors",
                schema=CARD_SCHEMA,
            )

        logger.info("LanceDB initialized at %s", self.db_path)

    async def embed_exchange(
        self,
        exchange_number: int,
        user_message: str,
        assistant_response: str,
        rp_folder: str,
        branch: str = "main",
        session_id: str | None = None,
        in_story_timestamp: str | None = None,
        chunking_strategy: str = "fixed",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> int:
        """Chunk and embed an exchange (user + assistant). Returns chunk count."""
        if self._embed_fn is None or self._exchange_table is None:
            return 0

        from rp_engine.utils.text import compute_chunking_hash, get_chunker

        chunker = get_chunker(chunking_strategy)
        c_hash = compute_chunking_hash(chunking_strategy, chunk_size, chunk_overlap, rp_folder)
        chunks_data = []

        # Shared metadata for all chunks in this exchange
        base = {
            "exchange_number": exchange_number,
            "rp_folder": rp_folder,
            "branch": branch,
            "session_id": session_id or "",
            "in_story_timestamp": in_story_timestamp or "",
            "chunking_hash": c_hash,
        }

        for speaker, text in [("user", user_message), ("assistant", assistant_response)]:
            for chunk in chunker(text, chunk_size=chunk_size, overlap=chunk_overlap):
                chunks_data.append({**base, "text": chunk, "speaker": speaker})

        if not chunks_data:
            return 0

        # Get embeddings
        try:
            texts = [c["text"] for c in chunks_data]
            embeddings = await self._embed_fn(texts)
        except Exception as e:
            logger.warning("Exchange embedding failed (model=%s): %s", self._embedding_model, e)
            return 0

        # Build records with vectors
        for i, chunk_data in enumerate(chunks_data):
            chunk_data["vector"] = embeddings[i] if i < len(embeddings) else [0.0] * self._dimension

        await asyncio.to_thread(self._exchange_table.add, chunks_data)
        return len(chunks_data)

    async def embed_card(
        self,
        card_id: str,
        content: str,
        rp_folder: str,
        card_type: str | None = None,
        file_path: str | None = None,
    ) -> int:
        """Chunk and embed a story card. Returns chunk count."""
        if self._embed_fn is None or self._card_table is None:
            return 0

        from rp_engine.utils.text import chunk_text

        # Remove existing vectors for this card
        try:
            await asyncio.to_thread(self._card_table.delete, f'card_id = "{_lance_escape(card_id)}"')
        except Exception:
            pass  # Table may be empty

        chunks = chunk_text(content, chunk_size=1000, overlap=200)
        if not chunks:
            return 0

        try:
            embeddings = await self._embed_fn(chunks)
        except Exception as e:
            logger.warning("Card embedding failed for %s (model=%s): %s", card_id, self._embedding_model, e)
            return 0

        records = []
        for i, chunk in enumerate(chunks):
            records.append({
                "text": chunk,
                "vector": embeddings[i] if i < len(embeddings) else [0.0] * self._dimension,
                "card_id": card_id,
                "card_type": card_type or "",
                "rp_folder": rp_folder,
                "file_path": file_path or "",
                "chunk_index": i,
                "total_chunks": len(chunks),
            })

        await asyncio.to_thread(self._card_table.add, records)
        return len(records)

    async def search_exchanges(
        self,
        query_text: str,
        rp_folder: str,
        branch: str = "main",
        limit: int = 5,
        max_exchange: int | None = None,
        ancestry_chain: list[tuple[str, int]] | None = None,
    ) -> list[LanceSearchResult]:
        """Search exchange vectors for relevant past conversations.

        If ``ancestry_chain`` is provided, searches across all ancestor branches
        using a compound OR filter. Each entry is ``(branch_name, max_exchange_number)``.
        """
        if self._embed_fn is None or self._exchange_table is None:
            return []

        try:
            embeddings = await self._embed_fn([query_text])
            query_vec = embeddings[0]
        except Exception as e:
            logger.warning("Exchange search embedding failed (model=%s): %s", self._embedding_model, e)
            return []

        try:
            def _search():
                where = _build_ancestry_where(rp_folder, branch, max_exchange, ancestry_chain)
                return (
                    self._exchange_table.search(query_vec)
                    .where(where)
                    .limit(limit)
                    .to_list()
                )

            results = await asyncio.to_thread(_search)
        except Exception as e:
            logger.warning("Exchange vector search failed: %s", e)
            return []

        search_results = []
        for row in results:
            search_results.append(LanceSearchResult(
                text=row["text"],
                score=1.0 - row.get("_distance", 0),  # Lance returns distance
                metadata={
                    "exchange_number": row.get("exchange_number"),
                    "speaker": row.get("speaker"),
                    "session_id": row.get("session_id"),
                    "in_story_timestamp": row.get("in_story_timestamp"),
                },
            ))

        return search_results

    async def search_cards(
        self,
        query_text: str,
        rp_folder: str,
        limit: int = 10,
    ) -> list[LanceSearchResult]:
        """Search card vectors for relevant story cards."""
        if self._embed_fn is None or self._card_table is None:
            return []

        try:
            embeddings = await self._embed_fn([query_text])
            query_vec = embeddings[0]
        except Exception as e:
            logger.warning("Card search embedding failed (model=%s): %s", self._embedding_model, e)
            return []

        try:
            def _search():
                return (
                    self._card_table.search(query_vec)
                    .where(f'rp_folder = "{_lance_escape(rp_folder)}"')
                    .limit(limit)
                    .to_list()
                )

            results = await asyncio.to_thread(_search)
        except Exception as e:
            logger.warning("Card vector search failed: %s", e)
            return []

        return [
            LanceSearchResult(
                text=row["text"],
                score=1.0 - row.get("_distance", 0),
                metadata={
                    "card_id": row.get("card_id"),
                    "card_type": row.get("card_type"),
                    "file_path": row.get("file_path"),
                    "chunk_index": row.get("chunk_index"),
                },
            )
            for row in results
        ]

    async def delete_exchange_vectors(
        self,
        rp_folder: str,
        branch: str,
        exchange_number: int,
    ) -> None:
        """Delete vectors for a specific exchange number."""
        if self._exchange_table is None:
            return
        try:
            await asyncio.to_thread(
                self._exchange_table.delete,
                f'rp_folder = "{_lance_escape(rp_folder)}" AND branch = "{_lance_escape(branch)}" '
                f"AND exchange_number = {exchange_number}",
            )
        except Exception as e:
            logger.warning("Exchange vector delete failed: %s", e)

    async def rewind_exchanges(
        self,
        rp_folder: str,
        branch: str,
        after_exchange: int,
    ) -> None:
        """Delete exchange vectors after a given exchange number (for rewind)."""
        if self._exchange_table is None:
            return
        try:
            await asyncio.to_thread(
                self._exchange_table.delete,
                f'rp_folder = "{_lance_escape(rp_folder)}" AND branch = "{_lance_escape(branch)}" '
                f"AND exchange_number > {after_exchange}",
            )
        except Exception as e:
            logger.warning("Exchange vector rewind failed: %s", e)

    async def list_exchange_chunks(
        self,
        rp_folder: str | None = None,
        branch: str = "main",
        limit: int = 200,
    ) -> list[dict]:
        """List exchange chunks from LanceDB for the Chunk Viewer."""
        if self._exchange_table is None:
            return []

        try:
            def _fetch():
                # Build filter to push down to LanceDB instead of loading entire table
                filters = []
                if rp_folder:
                    filters.append(f'rp_folder = "{_lance_escape(rp_folder)}"')
                if branch:
                    filters.append(f'branch = "{_lance_escape(branch)}"')

                query = self._exchange_table.search().select(
                    ["exchange_number", "chunk_index", "text", "rp_folder", "branch", "session_id", "chunking_hash"]
                )
                if filters:
                    query = query.where(" AND ".join(filters))
                query = query.limit(limit)
                return query.to_list()

            rows = await asyncio.to_thread(_fetch)
            return rows
        except Exception as e:
            logger.warning("Failed to list exchange chunks: %s", e)
            return []

    async def reindex_exchanges(
        self,
        db,
        rp_folder: str | None = None,
        branch: str = "main",
        chunking_strategy: str = "fixed",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        selective: bool = False,
    ) -> dict:
        """Re-embed all exchanges from the database. Returns stats.

        If ``selective=True``, skips exchanges whose vectors already have
        a matching chunking_hash (avoids unnecessary re-embedding).
        """
        if self._embed_fn is None or self._exchange_table is None:
            return {"status": "skipped", "reason": "no embed_fn or table"}

        from rp_engine.utils.text import compute_chunking_hash

        target_hash = compute_chunking_hash(chunking_strategy, chunk_size, chunk_overlap, rp_folder or "")

        # Determine which exchange numbers already have the correct hash
        existing_hashes: set[int] = set()
        if selective and rp_folder:
            try:
                def _fetch_hashes():
                    filters = [f'rp_folder = "{_lance_escape(rp_folder)}"']
                    if branch:
                        filters.append(f'branch = "{_lance_escape(branch)}"')
                    results = (
                        self._exchange_table.search()
                        .select(["exchange_number", "chunking_hash"])
                        .where(" AND ".join(filters))
                        .limit(100000)
                        .to_list()
                    )
                    up_to_date = set()
                    for r in results:
                        if r.get("chunking_hash") == target_hash:
                            up_to_date.add(r["exchange_number"])
                    return up_to_date

                existing_hashes = await asyncio.to_thread(_fetch_hashes)
            except Exception:
                pass  # If query fails, rechunk everything

        if not selective:
            # Clear existing exchange vectors for this scope
            try:
                if rp_folder:
                    await asyncio.to_thread(
                        self._exchange_table.delete,
                        f'rp_folder = "{_lance_escape(rp_folder)}" AND branch = "{_lance_escape(branch)}"',
                    )
                else:
                    await asyncio.to_thread(
                        self._exchange_table.delete,
                        "rp_folder IS NOT NULL",
                    )
            except Exception:
                pass

        # Load exchanges
        if rp_folder:
            rows = await db.fetch_all(
                "SELECT * FROM exchanges WHERE rp_folder = ? AND branch = ? ORDER BY exchange_number",
                [rp_folder, branch],
            )
        else:
            rows = await db.fetch_all(
                "SELECT * FROM exchanges ORDER BY rp_folder, branch, exchange_number"
            )

        embedded = 0
        skipped = 0
        failed = 0
        for row in rows:
            ex_num = row["exchange_number"]

            # Skip if already up-to-date
            if selective and ex_num in existing_hashes:
                skipped += 1
                continue

            # Delete old vectors for this exchange if selective
            if selective:
                try:
                    await self.delete_exchange_vectors(row["rp_folder"], row["branch"], ex_num)
                except Exception:
                    pass

            try:
                count = await self.embed_exchange(
                    exchange_number=ex_num,
                    user_message=row["user_message"],
                    assistant_response=row["assistant_response"],
                    rp_folder=row["rp_folder"],
                    branch=row["branch"],
                    session_id=row.get("session_id"),
                    in_story_timestamp=row.get("in_story_timestamp"),
                    chunking_strategy=chunking_strategy,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
                if count > 0:
                    embedded += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning("Reindex failed for exchange %d: %s", row["id"], e)
                failed += 1

        return {
            "status": "complete",
            "total_exchanges": len(rows),
            "embedded": embedded,
            "skipped": skipped,
            "failed": failed,
        }

    async def close(self) -> None:
        """Close LanceDB connection."""
        self._db = None
        self._exchange_table = None
        self._card_table = None
