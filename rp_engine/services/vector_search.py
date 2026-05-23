"""Hybrid vector + BM25 search with RRF fusion.

Stores embeddings as float32 BLOBs in SQLite. Vector search uses numpy
for cosine similarity. BM25 uses FTS5. Results are fused via Reciprocal
Rank Fusion (RRF).

The embed_fn is injectable — defaults to a minimal httpx-based embedder
that Phase 3's LLMClient can replace.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from rp_engine.config import SearchConfig, get_config
from rp_engine.database import PRIORITY_REINDEX, Database
from rp_engine.utils.lru_cache import LRUCache
from rp_engine.utils.text import chunk_text, sanitize_fts_query

logger = logging.getLogger(__name__)

# RRF fusion constant
_RRF_K = 60


@dataclass
class SearchResult:
    content: str
    file_path: str | None = None
    rp_folder: str | None = None
    card_type: str | None = None
    chunk_index: int = 0
    total_chunks: int = 1
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorSearch:
    """Hybrid search: cosine similarity + BM25 with RRF fusion."""

    def __init__(
        self,
        db: Database,
        config: SearchConfig | None = None,
        embed_fn: Callable | None = None,
        api_key: str | None = None,
        embedding_model: str = "unknown",
    ) -> None:
        self.db = db
        self._config_override: SearchConfig | None = config
        self._embed_fn = embed_fn or self._default_embed
        self._api_key = api_key
        self._embedding_model = embedding_model
        # LRU cache: rp_folder → (ids, matrix, norms)
        # Cache maxsize is set at construction (immutable — changing requires rebuild)
        effective_config = config or SearchConfig()
        self._vector_cache: LRUCache[str, tuple[list[int], np.ndarray, np.ndarray]] = LRUCache(
            maxsize=effective_config.vector_cache_max
        )

    @property
    def config(self) -> SearchConfig:
        """Read search config dynamically so hot-reloaded changes take effect.

        Tests can pass a custom SearchConfig at construction to override.
        Production code passes None; the property reads from get_config().
        """
        if self._config_override is not None:
            return self._config_override
        return get_config().search

    async def search(
        self,
        query: str,
        rp_folder: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Hybrid search: embed query → vector search + BM25 → RRF fusion."""
        vector_results: list[tuple[int, float]] = []
        bm25_results: list[tuple[int, float]] = []

        # Vector search (may fail if no embed_fn / API)
        try:
            query_vec = await self._embed_fn([query])
            if query_vec and len(query_vec) > 0:
                vector_results = await self._vector_search(
                    np.array(query_vec[0], dtype=np.float32), rp_folder
                )
        except Exception as e:
            logger.warning("Vector search failed (model=%s), falling back to BM25 only: %s", self._embedding_model, e)

        # BM25 search
        try:
            bm25_results = await self._bm25_search(query, rp_folder)
        except Exception as e:
            logger.warning("BM25 search failed: %s", e)

        if not vector_results and not bm25_results:
            return []

        # RRF fusion
        fused = self._rrf_fuse(vector_results, bm25_results)

        # Load content for top results
        results: list[SearchResult] = []
        for row_id, score in fused[:limit]:
            row = await self.db.fetch_one(
                """SELECT content, file_path, rp_folder, card_type,
                          chunk_index, total_chunks, metadata
                   FROM vectors WHERE id = ?""",
                [row_id],
            )
            if row:
                from rp_engine.utils.json_helpers import safe_parse_json
                meta = safe_parse_json(row.get("metadata"))
                results.append(SearchResult(
                    content=row["content"],
                    file_path=row.get("file_path"),
                    rp_folder=row.get("rp_folder"),
                    card_type=row.get("card_type"),
                    chunk_index=row.get("chunk_index", 0),
                    total_chunks=row.get("total_chunks", 1),
                    score=score,
                    metadata=meta,
                ))

        return results

    async def index_document(
        self,
        content: str,
        file_path: str,
        rp_folder: str,
        card_type: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Chunk text, embed, and store. Returns chunk count."""
        chunks = chunk_text(content, self.config.chunk_size, self.config.chunk_overlap)
        if not chunks:
            return 0

        # Remove existing vectors for this file
        await self.remove_document(file_path)

        # Embed all chunks
        try:
            embeddings = await self._embed_fn(chunks)
        except Exception as e:
            logger.warning("Embedding failed for %s (model=%s), storing without vectors: %s", file_path, self._embedding_model, e)
            embeddings = None

        import json
        meta_json = json.dumps(metadata) if metadata else None
        now = datetime.now(UTC).isoformat()

        for i, chunk in enumerate(chunks):
            if embeddings and i < len(embeddings):
                embedding_blob = _vec_to_blob(embeddings[i])
            else:
                # Store zero vector as placeholder
                embedding_blob = _vec_to_blob([0.0] * self.config.embedding_dimension)

            future = await self.db.enqueue_write(
                """INSERT INTO vectors (content, embedding, file_path, chunk_index,
                       total_chunks, rp_folder, card_type, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [chunk, embedding_blob, file_path, i, len(chunks),
                 rp_folder, card_type, meta_json, now],
                priority=PRIORITY_REINDEX,
            )
            await future

        # Invalidate cache
        self._vector_cache.pop(rp_folder, None)

        # Update indexed_files
        future = await self.db.enqueue_write(
            """INSERT OR REPLACE INTO indexed_files
                   (file_path, rp_folder, chunk_count, indexed_at)
               VALUES (?, ?, ?, ?)""",
            [file_path, rp_folder, len(chunks), now],
            priority=PRIORITY_REINDEX,
        )
        await future

        return len(chunks)

    async def remove_document(self, file_path: str) -> int:
        """Delete all chunks for a file."""
        count = await self.db.fetch_val(
            "SELECT COUNT(*) FROM vectors WHERE file_path = ?", [file_path]
        )
        if count and count > 0:
            future = await self.db.enqueue_write(
                "DELETE FROM vectors WHERE file_path = ?",
                [file_path],
                priority=PRIORITY_REINDEX,
            )
            await future
            future = await self.db.enqueue_write(
                "DELETE FROM indexed_files WHERE file_path = ?",
                [file_path],
                priority=PRIORITY_REINDEX,
            )
            await future

        return count or 0

    async def reindex_all(self, rp_folder: str) -> dict[str, int]:
        """Full reindex from story_cards table."""
        from rp_engine.utils.frontmatter import parse_frontmatter

        cards = await self.db.fetch_all(
            "SELECT file_path, content, card_type FROM story_cards WHERE rp_folder = ?",
            [rp_folder],
        )

        total_chunks = 0
        total_files = 0
        for card in cards:
            if card.get("content"):
                # Strip YAML frontmatter — only chunk the body text
                _, body = parse_frontmatter(card["content"])
                if not body.strip():
                    continue
                chunks = await self.index_document(
                    body,
                    card["file_path"],
                    rp_folder,
                    card.get("card_type"),
                )
                total_chunks += chunks
                total_files += 1

        self._vector_cache.pop(rp_folder, None)
        return {"files": total_files, "chunks": total_chunks}

    def clear_cache(self) -> None:
        """Clear the vector cache (used during shutdown)."""
        self._vector_cache.clear()

    # ----- Internal methods -----

    async def _vector_search(
        self, query_vec: np.ndarray, rp_folder: str | None
    ) -> list[tuple[int, float]]:
        """Cosine similarity against cached embedding matrix."""
        cache_key = rp_folder or "__all__"

        cache = self._vector_cache.get(cache_key)
        if cache is None:
            await self._load_vectors(cache_key, rp_folder)
            cache = self._vector_cache.get(cache_key)

        if cache is None or len(cache[0]) == 0:
            return []

        ids, matrix, norms = cache

        # Normalize query
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        # Cosine similarity: q @ M^T / (|q| * |m|)
        similarities = matrix @ query_vec / (norms * query_norm + 1e-10)

        # Filter by threshold and sort
        results: list[tuple[int, float]] = []
        for i, sim in enumerate(similarities):
            if sim >= self.config.similarity_threshold:
                results.append((ids[i], float(sim)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    async def _bm25_search(
        self, query: str, rp_folder: str | None
    ) -> list[tuple[int, float]]:
        """FTS5 BM25 search."""
        fts_query = sanitize_fts_query(query)
        if not fts_query:
            return []

        if rp_folder:
            rows = await self.db.fetch_all(
                """SELECT v.id, -bm25(vectors_fts) as score
                   FROM vectors_fts
                   JOIN vectors v ON v.id = vectors_fts.rowid
                   WHERE vectors_fts MATCH ? AND v.rp_folder = ?
                   ORDER BY score DESC
                   LIMIT 50""",
                [fts_query, rp_folder],
            )
        else:
            rows = await self.db.fetch_all(
                """SELECT v.id, -bm25(vectors_fts) as score
                   FROM vectors_fts
                   JOIN vectors v ON v.id = vectors_fts.rowid
                   WHERE vectors_fts MATCH ?
                   ORDER BY score DESC
                   LIMIT 50""",
                [fts_query],
            )

        if not rows:
            return []

        # Normalize scores to 0-1
        max_score = max(r["score"] for r in rows) if rows else 1.0
        if max_score <= 0:
            max_score = 1.0

        return [(r["id"], r["score"] / max_score) for r in rows]

    def _rrf_fuse(
        self,
        vector_results: list[tuple[int, float]],
        bm25_results: list[tuple[int, float]],
    ) -> list[tuple[int, float]]:
        """Reciprocal Rank Fusion of vector + BM25 results."""
        scores: dict[int, float] = {}

        for rank, (row_id, _) in enumerate(vector_results):
            scores[row_id] = scores.get(row_id, 0) + self.config.vector_weight / (_RRF_K + rank + 1)

        for rank, (row_id, _) in enumerate(bm25_results):
            scores[row_id] = scores.get(row_id, 0) + self.config.bm25_weight / (_RRF_K + rank + 1)

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    async def _load_vectors(self, cache_key: str, rp_folder: str | None) -> None:
        """Load all embeddings into numpy matrix for fast search."""
        if rp_folder:
            rows = await self.db.fetch_all(
                "SELECT id, embedding FROM vectors WHERE rp_folder = ?",
                [rp_folder],
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT id, embedding FROM vectors"
            )

        if not rows:
            self._vector_cache.put(cache_key, ([], np.array([]), np.array([])))
            return

        ids: list[int] = []
        vecs: list[np.ndarray] = []
        for row in rows:
            try:
                vec = _blob_to_vec(row["embedding"])
                # Skip zero vectors
                if np.any(vec != 0):
                    ids.append(row["id"])
                    vecs.append(vec)
            except Exception:
                continue

        if not vecs:
            self._vector_cache.put(cache_key, ([], np.array([]), np.array([])))
            return

        matrix = np.stack(vecs)
        norms = np.linalg.norm(matrix, axis=1)
        self._vector_cache.put(cache_key, (ids, matrix, norms))

    async def _default_embed(self, texts: list[str]) -> list[list[float]]:
        """Minimal embedding via httpx + OpenRouter. Falls back to error."""
        if not self._api_key:
            raise RuntimeError("No API key configured for embeddings")

        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": "openai/text-embedding-3-small", "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]


def _vec_to_blob(vec: list[float] | np.ndarray) -> bytes:
    """Convert float vector to raw float32 bytes."""
    if isinstance(vec, np.ndarray):
        return vec.astype(np.float32).tobytes()
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> np.ndarray:
    """Convert raw float32 bytes to numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()
