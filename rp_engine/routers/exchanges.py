"""Exchange (chat message) storage endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import PRIORITY_EXCHANGE, Database
from rp_engine.dependencies import (
    get_branch_manager,
    get_db,
    get_exchange_writer,
    get_lance_store,
    get_state_manager,
)
from rp_engine.models.exchange import (
    AnnotationCreate,
    AnnotationListResponse,
    AnnotationResponse,
    AnnotationUpdate,
    BookmarkCreate,
    BookmarkListResponse,
    BookmarkResponse,
    BookmarkUpdate,
    ExchangeDetail,
    ExchangeListResponse,
    ExchangeResponse,
    ExchangeSave,
    ExchangeSearchHit,
    ExchangeSearchResponse,
    ExchangeUpdate,
)
from rp_engine.services.branch_manager import BranchManager
from rp_engine.services.exchange_writer import ExchangeWriter
from rp_engine.services.lance_store import LanceStore
from rp_engine.services.state_manager import StateManager
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_array
from rp_engine.utils.text import truncate_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exchanges", tags=["exchanges"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detail_from_row(row: dict) -> ExchangeDetail:
    metadata = safe_parse_json(row.get("metadata")) or None
    npcs = safe_parse_json_array(row.get("npcs_involved")) or None
    variant_count = row.get("variant_count", 0) or 0
    continue_count = row.get("continue_count", 0) or 0
    bookmark_name = row.get("bookmark_name")
    annotation_count = row.get("annotation_count", 0) or 0
    return ExchangeDetail(
        id=row["id"],
        exchange_number=row["exchange_number"],
        session_id=row["session_id"],
        branch=row.get("branch"),
        user_message=row["user_message"],
        assistant_response=row["assistant_response"],
        in_story_timestamp=row.get("in_story_timestamp"),
        location=row.get("location"),
        npcs_involved=npcs,
        message_mode=row.get("message_mode", "rp"),
        analysis_status=row.get("analysis_status", "pending"),
        created_at=row["created_at"],
        metadata=metadata,
        has_variants=variant_count > 0,
        variant_count=variant_count,
        continue_count=continue_count,
        is_bookmarked=bookmark_name is not None,
        bookmark_name=bookmark_name,
        has_annotations=annotation_count > 0,
        annotation_count=annotation_count,
    )


def _annotation_from_row(r: dict) -> AnnotationResponse:
    """Convert a raw annotation DB row to an AnnotationResponse."""
    return AnnotationResponse(
        id=r["id"],
        exchange_number=r["exchange_number"],
        exchange_id=r["exchange_id"],
        content=r["content"],
        annotation_type=r["annotation_type"],
        include_in_context=bool(r["include_in_context"]),
        resolved=bool(r["resolved"]),
        created_at=r["created_at"],
        updated_at=r.get("updated_at"),
    )


async def _fetch_or_404(db: Database, query: str, params: list, msg: str) -> dict:
    """Fetch a single row or raise 404."""
    row = await db.fetch_one(query, params)
    if not row:
        raise HTTPException(404, detail=msg)
    return row


def _build_update(fields: dict) -> tuple[list[str], list]:
    """Build SET clause fragments from non-None fields.

    Values should be pre-converted (e.g. ``int(bool_val)``) by the caller.
    Returns ``(["col = ?", ...], [val, ...])`` — empty lists when nothing changed.
    """
    updates, params = [], []
    for col, val in fields.items():
        if val is not None:
            updates.append(f"{col} = ?")
            params.append(val)
    return updates, params


def _build_ancestry_sql(
    rp_folder: str, chain: list[tuple[str, int]]
) -> tuple[str, list]:
    """Build a parameterized SQL WHERE clause from an ancestry chain.

    Thin wrapper around ``AncestryResolver.build_ancestry_sql()`` with the
    ``e`` table alias pre-applied (used throughout this router).
    """
    from rp_engine.services.ancestry_resolver import AncestryResolver

    return AncestryResolver.build_ancestry_sql(rp_folder, chain, table_alias="e")


_BOOKMARK_ANNOTATION_JOINS = """
    LEFT JOIN exchange_bookmarks b
        ON b.rp_folder = e.rp_folder AND b.branch = e.branch
        AND b.exchange_number = e.exchange_number
    LEFT JOIN (
        SELECT rp_folder, branch, exchange_number, COUNT(*) AS acnt
        FROM exchange_annotations GROUP BY rp_folder, branch, exchange_number
    ) a ON a.rp_folder = e.rp_folder AND a.branch = e.branch
        AND a.exchange_number = e.exchange_number"""


class SearchMode(StrEnum):
    semantic = "semantic"
    keyword = "keyword"
    hybrid = "hybrid"


# ---------------------------------------------------------------------------
# Exchange CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ExchangeResponse, status_code=201)
async def save_exchange(
    body: ExchangeSave,
    db: Database = Depends(get_db),
    exchange_writer: ExchangeWriter = Depends(get_exchange_writer),
    state_manager: StateManager = Depends(get_state_manager),
    branch_manager: BranchManager = Depends(get_branch_manager),
):
    """Save a user+assistant exchange. Handles idempotency and rewinds.

    Rewind = creating a new branch from the conflict point (append-only).
    """
    # 1. Resolve session
    session_id = body.session_id
    if not session_id:
        active = await db.fetch_one(
            "SELECT id, rp_folder, branch FROM sessions "
            "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        )
        if not active:
            raise HTTPException(404, detail="No active session")
        session_id = active["id"]
        rp_folder = active["rp_folder"]
        branch = active["branch"]
    else:
        session = await db.fetch_one(
            "SELECT * FROM sessions WHERE id = ?", [session_id]
        )
        if not session:
            raise HTTPException(404, detail=f"Session {session_id} not found")
        rp_folder = session["rp_folder"]
        branch = session["branch"]

    # 2. Idempotency check (uses dedicated column + unique index)
    if body.idempotency_key:
        existing = await db.fetch_one(
            "SELECT * FROM exchanges WHERE rp_folder = ? AND branch = ? AND idempotency_key = ?",
            [rp_folder, branch, body.idempotency_key],
        )
        if existing:
            return ExchangeResponse(
                id=existing["id"],
                exchange_number=existing["exchange_number"],
                session_id=existing["session_id"],
                created_at=existing["created_at"],
                analysis_status=existing.get("analysis_status", "pending"),
                idempotent_hit=True,
            )

    # 3. Parent validation
    if body.parent_exchange_number is not None:
        latest = await db.fetch_val(
            "SELECT MAX(exchange_number) FROM exchanges WHERE rp_folder=? AND branch=?",
            [rp_folder, branch],
        )
        if latest is not None and latest != body.parent_exchange_number:
            raise HTTPException(409, detail={
                "error": "exchange_conflict",
                "message": f"Expected parent {body.parent_exchange_number}, latest is {latest}",
                "latest_exchange": latest,
            })

    # 4. Determine exchange_number + rewind (via branch creation)
    exchange_number = body.exchange_number
    rewound_count = None

    if exchange_number is not None:
        conflicting = await db.fetch_one(
            "SELECT id FROM exchanges WHERE rp_folder=? AND branch=? AND exchange_number=?",
            [rp_folder, branch, exchange_number],
        )
        if conflicting:
            # Rewind = create a new branch from exchange_number - 1
            rewind_point = exchange_number - 1
            new_branch_name = body.metadata.get("branch_name") if body.metadata else None
            if not new_branch_name:
                new_branch_name = await branch_manager.generate_rewind_branch_name(
                    branch, rewind_point, rp_folder
                )

            # Create branch with full state snapshot at the rewind point
            await branch_manager.create_branch(
                name=new_branch_name,
                rp_folder=rp_folder,
                description=f"Rewind from exchange {exchange_number}",
                branch_from=branch,
                branch_point_exchange=rewind_point,
            )
            branch = new_branch_name

            to_delete_count = await db.fetch_val(
                "SELECT COUNT(*) FROM exchanges WHERE rp_folder=? AND branch=? AND exchange_number>=?",
                [rp_folder, branch, exchange_number],
            )
            rewound_count = to_delete_count or 0
    else:
        exchange_number = None  # Will be assigned atomically below

    # 5. Insert via ExchangeWriter
    metadata = body.metadata or {}
    if body.idempotency_key:
        metadata["idempotency_key"] = body.idempotency_key

    exchange_id, exchange_number = await exchange_writer.save_exchange(
        session_id=session_id,
        rp_folder=rp_folder,
        branch=branch,
        user_message=body.user_message,
        assistant_response=body.assistant_response,
        exchange_number=exchange_number,
        in_story_timestamp=body.in_story_timestamp,
        location=body.location,
        metadata=metadata if metadata else None,
        idempotency_key=body.idempotency_key,
    )

    return ExchangeResponse(
        id=exchange_id,
        exchange_number=exchange_number,
        session_id=session_id,
        created_at=datetime.now(UTC).isoformat(),
        analysis_status="pending",
        rewound_count=rewound_count,
    )


_LIST_QUERY = f"""
    SELECT e.*,
           COALESCE(v.cnt, 0) AS variant_count,
           COALESCE(v.max_continue, 0) AS continue_count,
           b.name AS bookmark_name,
           COALESCE(a.acnt, 0) AS annotation_count
    FROM exchanges e
    LEFT JOIN (
        SELECT exchange_id, COUNT(*) AS cnt,
               MAX(continue_count) AS max_continue
        FROM exchange_variants GROUP BY exchange_id
    ) v ON v.exchange_id = e.id{_BOOKMARK_ANNOTATION_JOINS}
"""


@router.put("/{exchange_number}", response_model=ExchangeDetail)
async def edit_exchange(
    exchange_number: int,
    body: ExchangeUpdate,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    exchange_writer: ExchangeWriter = Depends(get_exchange_writer),
):
    """Edit an exchange's user message and/or assistant response.

    For assistant responses, creates a new variant with source='manual_edit'.
    The original response is preserved as a variant for swipe-back.
    """
    try:
        updated = await exchange_writer.update_exchange(
            exchange_number,
            rp_folder,
            branch,
            user_message=body.user_message,
            assistant_response=body.assistant_response,
            re_embed=body.re_embed,
            re_analyze=body.re_analyze,
        )
    except ValueError as e:
        raise HTTPException(404, detail=str(e)) from None

    # Fetch enriched detail (with variant/bookmark/annotation counts)
    row = await db.fetch_one(
        f"""{_LIST_QUERY} WHERE e.id = ?""",
        [updated["id"]],
    )
    return _detail_from_row(row)


@router.get("", response_model=ExchangeListResponse)
async def list_exchanges(
    session_id: str | None = Query(None),
    branch: str | None = Query(None),
    rp_folder: str | None = Query(None),
    include_ancestry: bool = Query(True),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
    branch_manager: BranchManager = Depends(get_branch_manager),
):
    """List exchanges with optional filters.

    When ``include_ancestry`` is True (default) and both ``rp_folder`` and
    ``branch`` are specified, walks the branch ancestry chain so child branches
    see exchanges from parent branches up to each branch point.
    """
    # Ancestry-aware path: build compound WHERE from ancestry chain
    if include_ancestry and rp_folder and branch and not session_id:
        chain = await branch_manager.get_ancestry_chain(rp_folder, branch)
        ancestry_clauses, ancestry_params = _build_ancestry_sql(rp_folder, chain)

        where = f" WHERE {ancestry_clauses}"
        total = await db.fetch_val(
            f"SELECT COUNT(*) FROM exchanges e{where}", ancestry_params,
        )
        rows = await db.fetch_all(
            f"{_LIST_QUERY}{where} ORDER BY e.exchange_number DESC LIMIT ? OFFSET ?",
            ancestry_params + [limit, offset],
        )
    else:
        # Flat query (original behavior)
        conditions = []
        params: list = []
        if session_id:
            conditions.append("e.session_id = ?")
            params.append(session_id)
        if branch:
            conditions.append("e.branch = ?")
            params.append(branch)
        if rp_folder:
            conditions.append("e.rp_folder = ?")
            params.append(rp_folder)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        total = await db.fetch_val(
            f"SELECT COUNT(*) FROM exchanges e{where}", params,
        )
        rows = await db.fetch_all(
            f"{_LIST_QUERY}{where} ORDER BY e.exchange_number DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )

    return ExchangeListResponse(
        exchanges=[_detail_from_row(r) for r in rows],
        total_count=total or 0,
    )


@router.get("/recent", response_model=ExchangeListResponse)
async def recent_exchanges(
    limit: int = Query(10, ge=1, le=100),
    db: Database = Depends(get_db),
):
    """Get the most recent N exchanges across all sessions."""
    rows = await db.fetch_all(
        f"{_LIST_QUERY} ORDER BY e.created_at DESC LIMIT ?",
        [limit],
    )
    return ExchangeListResponse(
        exchanges=[_detail_from_row(r) for r in rows],
        total_count=len(rows),
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search", response_model=ExchangeSearchResponse)
async def search_exchanges(
    q: str = Query(..., min_length=1),
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    limit: int = Query(20, ge=1, le=100),
    min_score: float = Query(0.3, ge=0.0, le=1.0),
    mode: SearchMode = Query(SearchMode.semantic),
    db: Database = Depends(get_db),
    lance_store: LanceStore | None = Depends(get_lance_store),
    branch_manager: BranchManager = Depends(get_branch_manager),
):
    """Search exchange history via semantic, keyword, or hybrid mode."""
    results: list[ExchangeSearchHit] = []

    # Get ancestry chain for cross-branch search
    ancestry_chain = await branch_manager.get_ancestry_chain(rp_folder, branch)

    # --- Semantic search via LanceDB ---
    lance_hits: dict[int, float] = {}
    if mode in (SearchMode.semantic, SearchMode.hybrid) and lance_store:
        lance_results = await lance_store.search_exchanges(
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
        ancestry_where, ancestry_params = _build_ancestry_sql(rp_folder, ancestry_chain)
        kw_rows = await db.fetch_all(
            f"""SELECT exchange_number FROM exchanges e
               WHERE {ancestry_where}
               AND (user_message LIKE ? OR assistant_response LIKE ?)
               LIMIT ?""",
            ancestry_params + [like_param, like_param, limit * 2],
        )
        for row in kw_rows:
            keyword_hits[row["exchange_number"]] = 0.8  # flat keyword score

    # --- Combine via RRF for hybrid, or use single source ---
    scored: dict[int, float] = {}
    if mode == SearchMode.hybrid:
        rrf_k = 60
        # Rank each source
        lance_ranked = sorted(lance_hits.items(), key=lambda x: -x[1])
        kw_ranked = sorted(keyword_hits.items(), key=lambda x: -x[1])
        for rank, (ex_num, _) in enumerate(lance_ranked):
            scored[ex_num] = scored.get(ex_num, 0) + 0.7 / (rrf_k + rank + 1)
        for rank, (ex_num, _) in enumerate(kw_ranked):
            scored[ex_num] = scored.get(ex_num, 0) + 0.3 / (rrf_k + rank + 1)
    elif mode == SearchMode.semantic:
        scored = lance_hits
    else:
        scored = keyword_hits

    if not scored:
        return ExchangeSearchResponse(query=q, mode=mode.value, total_results=0, results=[])

    # Sort by score descending, take top N
    top = sorted(scored.items(), key=lambda x: -x[1])[:limit]
    exchange_numbers = [ex_num for ex_num, _ in top]

    # Fetch full exchange data + bookmark/annotation enrichment (ancestry-aware)
    placeholders = ",".join("?" for _ in exchange_numbers)
    ancestry_where, ancestry_params = _build_ancestry_sql(rp_folder, ancestry_chain)
    rows = await db.fetch_all(
        f"""SELECT e.*, b.name AS bookmark_name,
                   COALESCE(a.acnt, 0) AS annotation_count
            FROM exchanges e{_BOOKMARK_ANNOTATION_JOINS}
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

    return ExchangeSearchResponse(
        query=q, mode=mode.value, total_results=len(results), results=results,
    )


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

@router.post("/{exchange_number}/bookmark", response_model=BookmarkResponse, status_code=201)
async def create_bookmark(
    exchange_number: int,
    body: BookmarkCreate,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Create a bookmark on an exchange."""
    exchange = await db.fetch_one(
        "SELECT id FROM exchanges WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
        [rp_folder, branch, exchange_number],
    )
    if not exchange:
        raise HTTPException(404, detail=f"Exchange {exchange_number} not found")

    # Auto-generate name if not provided
    if not body.name:
        count = await db.fetch_val(
            "SELECT COUNT(*) FROM exchange_bookmarks WHERE rp_folder = ? AND branch = ?",
            [rp_folder, branch],
        )
        body.name = f"Bookmark #{(count or 0) + 1}"

    now = datetime.now(UTC).isoformat()
    try:
        future = await db.enqueue_write(
            """INSERT INTO exchange_bookmarks
               (rp_folder, branch, exchange_number, exchange_id, name, note, color, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [rp_folder, branch, exchange_number, exchange["id"],
             body.name, body.note, body.color, now],
            priority=PRIORITY_EXCHANGE,
        )
        await future
    except Exception:
        raise HTTPException(409, detail="Bookmark already exists for this exchange") from None

    row = await db.fetch_one(
        "SELECT * FROM exchange_bookmarks WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
        [rp_folder, branch, exchange_number],
    )
    return BookmarkResponse(**row)


@router.get("/{exchange_number}/bookmark", response_model=BookmarkResponse)
async def get_bookmark(
    exchange_number: int,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Get the bookmark for an exchange."""
    row = await _fetch_or_404(
        db,
        "SELECT * FROM exchange_bookmarks WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
        [rp_folder, branch, exchange_number],
        "No bookmark on this exchange",
    )
    return BookmarkResponse(**row)


@router.put("/{exchange_number}/bookmark", response_model=BookmarkResponse)
async def update_bookmark(
    exchange_number: int,
    body: BookmarkUpdate,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Update a bookmark's name, note, or color."""
    existing = await _fetch_or_404(
        db,
        "SELECT * FROM exchange_bookmarks WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
        [rp_folder, branch, exchange_number],
        "No bookmark on this exchange",
    )

    updates, params = _build_update({
        "name": body.name, "note": body.note, "color": body.color,
    })

    if updates:
        future = await db.enqueue_write(
            f"UPDATE exchange_bookmarks SET {', '.join(updates)} WHERE id = ?",
            params + [existing["id"]],
            priority=PRIORITY_EXCHANGE,
        )
        await future

    row = await db.fetch_one(
        "SELECT * FROM exchange_bookmarks WHERE id = ?", [existing["id"]]
    )
    return BookmarkResponse(**row)


@router.delete("/{exchange_number}/bookmark")
async def delete_bookmark(
    exchange_number: int,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Remove a bookmark from an exchange."""
    existing = await _fetch_or_404(
        db,
        "SELECT id FROM exchange_bookmarks WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
        [rp_folder, branch, exchange_number],
        "No bookmark on this exchange",
    )

    future = await db.enqueue_write(
        "DELETE FROM exchange_bookmarks WHERE id = ?",
        [existing["id"]],
        priority=PRIORITY_EXCHANGE,
    )
    await future
    return {"deleted": True}


# Dedicated bookmarks list endpoint (mounted at /api/bookmarks via separate router)
bookmarks_router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])


@bookmarks_router.get("", response_model=BookmarkListResponse)
async def list_bookmarks(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    color: str | None = Query(None),
    sort: str = Query("exchange_number"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
):
    """List all bookmarks for an RP/branch."""
    conditions = ["rp_folder = ?", "branch = ?"]
    params: list = [rp_folder, branch]

    if color:
        conditions.append("color = ?")
        params.append(color)

    where = " WHERE " + " AND ".join(conditions)

    sort_col = "exchange_number" if sort == "exchange_number" else "created_at"

    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM exchange_bookmarks{where}", params,
    )
    rows = await db.fetch_all(
        f"SELECT * FROM exchange_bookmarks{where} ORDER BY {sort_col} LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    return BookmarkListResponse(
        bookmarks=[BookmarkResponse(**r) for r in rows],
        total_count=total or 0,
    )


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

@router.post("/{exchange_number}/annotations", response_model=AnnotationResponse, status_code=201)
async def create_annotation(
    exchange_number: int,
    body: AnnotationCreate,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Add an annotation to an exchange."""
    exchange = await db.fetch_one(
        "SELECT id FROM exchanges WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
        [rp_folder, branch, exchange_number],
    )
    if not exchange:
        raise HTTPException(404, detail=f"Exchange {exchange_number} not found")

    now = datetime.now(UTC).isoformat()
    future = await db.enqueue_write(
        """INSERT INTO exchange_annotations
           (rp_folder, branch, exchange_id, exchange_number, content,
            annotation_type, include_in_context, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [rp_folder, branch, exchange["id"], exchange_number,
         body.content, body.annotation_type, int(body.include_in_context), now],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    # Fetch the created row (last insert)
    row = await db.fetch_one(
        """SELECT * FROM exchange_annotations
           WHERE rp_folder = ? AND branch = ? AND exchange_number = ?
           ORDER BY id DESC LIMIT 1""",
        [rp_folder, branch, exchange_number],
    )
    return _annotation_from_row(row)


@router.get("/{exchange_number}/annotations", response_model=AnnotationListResponse)
async def list_exchange_annotations(
    exchange_number: int,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """List all annotations for a specific exchange."""
    rows = await db.fetch_all(
        """SELECT * FROM exchange_annotations
           WHERE rp_folder = ? AND branch = ? AND exchange_number = ?
           ORDER BY created_at""",
        [rp_folder, branch, exchange_number],
    )
    annotations = [_annotation_from_row(r) for r in rows]
    return AnnotationListResponse(annotations=annotations, total_count=len(annotations))


# Dedicated annotations router for operations by annotation ID + global list
annotations_router = APIRouter(prefix="/api/annotations", tags=["annotations"])


@annotations_router.put("/{annotation_id}", response_model=AnnotationResponse)
async def update_annotation(
    annotation_id: int,
    body: AnnotationUpdate,
    db: Database = Depends(get_db),
):
    """Edit an annotation."""
    await _fetch_or_404(
        db,
        "SELECT id FROM exchange_annotations WHERE id = ?",
        [annotation_id],
        "Annotation not found",
    )

    updates, params = _build_update({
        "content": body.content,
        "annotation_type": body.annotation_type,
        "include_in_context": int(body.include_in_context) if body.include_in_context is not None else None,
    })

    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now(UTC).isoformat())
        future = await db.enqueue_write(
            f"UPDATE exchange_annotations SET {', '.join(updates)} WHERE id = ?",
            params + [annotation_id],
            priority=PRIORITY_EXCHANGE,
        )
        await future

    row = await db.fetch_one(
        "SELECT * FROM exchange_annotations WHERE id = ?", [annotation_id]
    )
    return _annotation_from_row(row)


@annotations_router.delete("/{annotation_id}")
async def delete_annotation(
    annotation_id: int,
    db: Database = Depends(get_db),
):
    """Delete an annotation."""
    await _fetch_or_404(
        db,
        "SELECT id FROM exchange_annotations WHERE id = ?",
        [annotation_id],
        "Annotation not found",
    )

    future = await db.enqueue_write(
        "DELETE FROM exchange_annotations WHERE id = ?",
        [annotation_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future
    return {"deleted": True}


@annotations_router.patch("/{annotation_id}/resolve", response_model=AnnotationResponse)
async def toggle_resolve(
    annotation_id: int,
    db: Database = Depends(get_db),
):
    """Toggle the resolved status of an annotation."""
    existing = await _fetch_or_404(
        db,
        "SELECT * FROM exchange_annotations WHERE id = ?",
        [annotation_id],
        "Annotation not found",
    )

    new_resolved = 0 if existing["resolved"] else 1
    now = datetime.now(UTC).isoformat()
    future = await db.enqueue_write(
        "UPDATE exchange_annotations SET resolved = ?, updated_at = ? WHERE id = ?",
        [new_resolved, now, annotation_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    row = await db.fetch_one(
        "SELECT * FROM exchange_annotations WHERE id = ?", [annotation_id]
    )
    return _annotation_from_row(row)


@annotations_router.get("", response_model=AnnotationListResponse)
async def list_all_annotations(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    annotation_type: str | None = Query(None),
    resolved: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
):
    """List all annotations for an RP/branch with optional filters."""
    conditions = ["rp_folder = ?", "branch = ?"]
    params: list = [rp_folder, branch]

    if annotation_type:
        conditions.append("annotation_type = ?")
        params.append(annotation_type)
    if resolved is not None:
        conditions.append("resolved = ?")
        params.append(int(resolved))

    where = " WHERE " + " AND ".join(conditions)

    total = await db.fetch_val(
        f"SELECT COUNT(*) FROM exchange_annotations{where}", params,
    )
    rows = await db.fetch_all(
        f"SELECT * FROM exchange_annotations{where} ORDER BY exchange_number, created_at LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    annotations = [_annotation_from_row(r) for r in rows]
    return AnnotationListResponse(annotations=annotations, total_count=total or 0)


# ---------------------------------------------------------------------------
# Delete exchange (legacy)
# ---------------------------------------------------------------------------

@router.delete("/{exchange_id}")
async def delete_exchange(
    exchange_id: int,
    db: Database = Depends(get_db),
    lance_store=Depends(get_lance_store),
):
    """Delete an exchange (deprecated -- prefer rewind via branch creation).

    In the CoW system, exchanges are append-only. This endpoint is kept
    for backward compatibility but only removes the exchange record itself.
    State changes made by the exchange persist in CoW entries.
    """
    row = await db.fetch_one("SELECT * FROM exchanges WHERE id = ?", [exchange_id])
    if not row:
        raise HTTPException(404, detail=f"Exchange {exchange_id} not found")

    future = await db.enqueue_write(
        "DELETE FROM exchanges WHERE id=?",
        [exchange_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    # Keep vector store in sync — remove vectors for this exchange
    if lance_store:
        rp_folder = row.get("rp_folder", "")
        branch = row.get("branch", "main")
        exchange_number = row.get("exchange_number", 0)
        if exchange_number > 0:
            await lance_store.rewind_exchanges(rp_folder, branch, exchange_number - 1)

    return {"deleted": True, "exchange_id": exchange_id}
