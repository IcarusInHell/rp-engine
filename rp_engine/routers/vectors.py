"""Vector chunk inspection endpoints for the Chunk Viewer dev tool."""

from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from rp_engine.database import Database
from rp_engine.dependencies import get_db, get_lance_store, get_vector_search
from rp_engine.services.vector_search import VectorSearch
from rp_engine.utils.embedding import has_real_embedding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vectors", tags=["vectors"])


class ChunkRow(BaseModel):
    """One vector chunk row with metadata — file/card identity, chunk index, content, and a real-embedding-presence flag."""

    id: int
    file_path: str | None
    rp_folder: str | None
    card_type: str | None
    chunk_index: int
    total_chunks: int
    content: str
    has_embedding: bool
    created_at: str | None


class VectorStats(BaseModel):
    """Aggregate chunk statistics — totals, embedding coverage, file count, average chunk size, and cards missing vectors."""

    total_chunks: int
    chunks_with_embeddings: int
    chunks_without_embeddings: int
    total_files: int
    avg_chunk_size: float
    cards_without_vectors: list[str]


class SearchDebugRequest(BaseModel):
    """Debug-search request body — a query string with optional RP-folder filter and result limit."""

    query: str
    rp_folder: str | None = None
    limit: int = 10


class DebugSearchResult(BaseModel):
    """One debug-search hit with per-source scores (vector, BM25, fused) and which source(s) found it."""

    id: int
    file_path: str | None
    card_type: str | None
    chunk_index: int
    content: str
    vector_score: float | None
    bm25_score: float | None
    fused_score: float
    found_by: str  # "vector", "bm25", "both"


@router.get("/chunks", response_model=list[ChunkRow])
async def list_chunks(
    rp_folder: str | None = Query(None),
    card_type: str | None = Query(None),
    file_path: str | None = Query(None),
    limit: int = Query(200, le=1000),
    offset: int = Query(0),
    db: Database = Depends(get_db),
):
    """List all vector chunks with metadata. Used by the Chunk Browser tab."""
    conditions = []
    params: list = []

    if rp_folder:
        conditions.append("rp_folder = ?")
        params.append(rp_folder)
    if card_type:
        conditions.append("card_type = ?")
        params.append(card_type)
    if file_path:
        conditions.append("file_path = ?")
        params.append(file_path)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    rows = await db.fetch_all(
        f"""SELECT id, file_path, rp_folder, card_type, chunk_index,
                   total_chunks, content, embedding, created_at
            FROM vectors{where}
            ORDER BY file_path, chunk_index
            LIMIT ? OFFSET ?""",
        params,
    )

    return [
        ChunkRow(
            id=row["id"],
            file_path=row["file_path"],
            rp_folder=row["rp_folder"],
            card_type=row["card_type"],
            chunk_index=row["chunk_index"] or 0,
            total_chunks=row["total_chunks"] or 1,
            content=row["content"],
            has_embedding=has_real_embedding(row["embedding"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/chunks/{file_path:path}", response_model=list[ChunkRow])
async def get_chunks_for_file(
    file_path: str,
    db: Database = Depends(get_db),
):
    """Get all chunks for a specific card file. Used by the Card→Chunks tab."""
    rows = await db.fetch_all(
        """SELECT id, file_path, rp_folder, card_type, chunk_index,
                  total_chunks, content, embedding, created_at
           FROM vectors WHERE file_path = ?
           ORDER BY chunk_index""",
        [file_path],
    )
    if not rows:
        raise HTTPException(404, detail=f"No chunks found for: {file_path}")

    return [
        ChunkRow(
            id=row["id"],
            file_path=row["file_path"],
            rp_folder=row["rp_folder"],
            card_type=row["card_type"],
            chunk_index=row["chunk_index"] or 0,
            total_chunks=row["total_chunks"] or 1,
            content=row["content"],
            has_embedding=has_real_embedding(row["embedding"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.get("/stats", response_model=VectorStats)
async def get_vector_stats(
    rp_folder: str | None = Query(None),
    db: Database = Depends(get_db),
):
    """Aggregate chunk statistics. Used by the Chunk Viewer stats bar."""
    cond = "WHERE rp_folder = ?" if rp_folder else ""
    params = [rp_folder] if rp_folder else []

    # SQL aggregates — avoid loading all content BLOBs into memory
    agg = await db.fetch_one(
        f"""SELECT COUNT(*) as total,
                   SUM(LENGTH(content)) as total_chars,
                   COUNT(DISTINCT file_path) as total_files
            FROM vectors {cond}""",
        params,
    )
    total = agg["total"] if agg else 0
    total_chars = agg["total_chars"] or 0 if agg else 0
    total_files = agg["total_files"] if agg else 0
    avg_size = total_chars / total if total > 0 else 0.0

    # Count real embeddings via SQL — avoids loading all BLOBs into memory
    embed_agg = await db.fetch_one(
        f"""SELECT COUNT(*) as cnt FROM vectors
            {cond + ' AND' if cond else 'WHERE'} embedding IS NOT NULL
            AND embedding != ZEROBLOB(LENGTH(embedding))""",
        params,
    )
    with_embed = embed_agg["cnt"] if embed_agg else 0

    # Cards without vectors — use LEFT JOIN
    card_cond = "AND sc.rp_folder = ?" if rp_folder else ""
    card_params = params if rp_folder else []
    cards_without_rows = await db.fetch_all(
        f"""SELECT sc.file_path FROM story_cards sc
            LEFT JOIN vectors v ON sc.file_path = v.file_path
            WHERE v.id IS NULL {card_cond}""",
        card_params,
    )
    cards_without = [r["file_path"] for r in cards_without_rows]

    return VectorStats(
        total_chunks=total,
        chunks_with_embeddings=with_embed,
        chunks_without_embeddings=total - with_embed,
        total_files=total_files,
        avg_chunk_size=round(avg_size, 1),
        cards_without_vectors=cards_without,
    )


@router.get("/exchange-chunks")
async def list_exchange_chunks(
    rp_folder: str | None = Query(None),
    branch: str = Query("main"),
    limit: int = Query(200, le=1000),
    lance_store=Depends(get_lance_store),
):
    """List exchange vector chunks from LanceDB."""
    if lance_store is None:
        return []
    return await lance_store.list_exchange_chunks(rp_folder, branch, limit)


@router.post("/reindex-exchanges")
async def reindex_exchanges(
    rp_folder: str | None = Query(None),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    lance_store=Depends(get_lance_store),
):
    """Re-embed all exchanges into LanceDB. Runs async, doesn't block other requests."""
    if lance_store is None:
        raise HTTPException(503, detail="LanceDB not available")
    result = await lance_store.reindex_exchanges(db, rp_folder, branch)
    return result


@router.post("/search-debug", response_model=list[DebugSearchResult])
async def search_debug(
    body: SearchDebugRequest,
    db: Database = Depends(get_db),
    vector_search: VectorSearch = Depends(get_vector_search),
):
    """Search with per-source scores (vector, BM25, fused) for debugging."""
    query = body.query.strip()
    if not query:
        raise HTTPException(400, detail="query is required")

    rp_folder = body.rp_folder
    limit = min(body.limit, 50)
    from rp_engine.services.vector_search import _RRF_K

    # --- Vector search ---
    vector_scores: dict[int, float] = {}
    try:
        query_vec = await vector_search._embed_fn([query])
        if query_vec and len(query_vec) > 0:
            q = np.array(query_vec[0], dtype=np.float32)
            raw = await vector_search._vector_search(q, rp_folder)
            vector_scores = {row_id: score for row_id, score in raw}
    except Exception as e:
        logger.warning("Vector search failed in debug: %s", e)

    # --- BM25 search ---
    bm25_scores: dict[int, float] = {}
    try:
        raw_bm25 = await vector_search._bm25_search(query, rp_folder)
        bm25_scores = {row_id: score for row_id, score in raw_bm25}
    except Exception as e:
        logger.warning("BM25 search failed in debug: %s", e)

    # --- RRF fusion ---
    # CONSOLIDATION NOTE (A3, 3rd site): the rank-fusion formula below
    # (1/(_RRF_K + rank), summed across legs) duplicates the A3 family —
    # services/vector_search.py `_rrf_fuse` ∥ utils/search_ranking.py
    # `reciprocal_rank_fusion`. This debug endpoint inlines it because it also
    # needs the intermediate per-source rank maps for its DebugSearchResult
    # output; a shared helper would need to expose those rankings to be reused
    # here. Cross-linked for the eventual A3 consolidation pass.
    all_ids = set(vector_scores) | set(bm25_scores)
    if not all_ids:
        return []

    vector_ranked = {row_id: rank for rank, (row_id, _) in enumerate(
        sorted(vector_scores.items(), key=lambda x: x[1], reverse=True), 1
    )}
    bm25_ranked = {row_id: rank for rank, (row_id, _) in enumerate(
        sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True), 1
    )}

    fused: dict[int, float] = {}
    for row_id in all_ids:
        v_rank = vector_ranked.get(row_id)
        b_rank = bm25_ranked.get(row_id)
        score = 0.0
        if v_rank:
            score += 1.0 / (_RRF_K + v_rank)
        if b_rank:
            score += 1.0 / (_RRF_K + b_rank)
        fused[row_id] = score

    top_ids = sorted(fused, key=lambda x: fused[x], reverse=True)[:limit]

    # Load content
    results: list[DebugSearchResult] = []
    for row_id in top_ids:
        row = await db.fetch_one(
            "SELECT id, file_path, card_type, chunk_index, content FROM vectors WHERE id = ?",
            [row_id],
        )
        if not row:
            continue

        in_vector = row_id in vector_scores
        in_bm25 = row_id in bm25_scores
        found_by = "both" if (in_vector and in_bm25) else ("vector" if in_vector else "bm25")

        results.append(DebugSearchResult(
            id=row["id"],
            file_path=row["file_path"],
            card_type=row["card_type"],
            chunk_index=row["chunk_index"] or 0,
            content=row["content"],
            vector_score=vector_scores.get(row_id),
            bm25_score=bm25_scores.get(row_id),
            fused_score=round(fused[row_id], 6),
            found_by=found_by,
        ))

    return results
