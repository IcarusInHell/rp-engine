"""Exchange (chat message) storage endpoints.

Bookmarks and annotations were split into ``bookmarks.py`` / ``annotations.py``
in Phase 7a; shared helpers live in ``_shared.py``. This module owns exchange
CRUD + search.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import Database
from rp_engine.dependencies import (
    get_branch_manager,
    get_db,
    get_exchange_search_service,
    get_exchange_writer,
    get_rewind_service,
)
from rp_engine.models.exchange import (
    ExchangeDetail,
    ExchangeListResponse,
    ExchangeResponse,
    ExchangeSave,
    ExchangeSearchResponse,
    ExchangeUpdate,
    SearchMode,
)
from rp_engine.routers._shared import _build_ancestry_sql
from rp_engine.services.branch_manager import BranchManager
from rp_engine.services.exchange_search_service import ExchangeSearchService
from rp_engine.services.exchange_writer import ExchangeWriter
from rp_engine.services.rewind_service import RewindService
from rp_engine.utils.exchange_sql import BOOKMARK_ANNOTATION_JOINS
from rp_engine.utils.json_helpers import safe_parse_json, safe_parse_json_array

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


# ---------------------------------------------------------------------------
# Exchange CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ExchangeResponse, status_code=201)
async def save_exchange(
    body: ExchangeSave,
    db: Database = Depends(get_db),
    exchange_writer: ExchangeWriter = Depends(get_exchange_writer),
    rewind_service: RewindService = Depends(get_rewind_service),
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
    new_branch = None

    if exchange_number is not None:
        conflicting = await db.fetch_one(
            "SELECT id FROM exchanges WHERE rp_folder=? AND branch=? AND exchange_number=?",
            [rp_folder, branch, exchange_number],
        )
        if conflicting:
            # Rewind = fork a new branch from exchange_number - 1. RewindService
            # owns the workflow (name-gen + Bug-A orphan count on the OLD branch +
            # snapshot create_branch); the save then lands on the new branch.
            result = await rewind_service.rewind_to(
                rp_folder=rp_folder,
                source_branch=branch,
                exchange_number=exchange_number,
                branch_name_hint=body.metadata.get("branch_name") if body.metadata else None,
            )
            branch = result.new_branch
            new_branch = result.new_branch  # Bug B: signal the client it moved branches
            rewound_count = result.rewound_count
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
        new_branch=new_branch,
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
    ) v ON v.exchange_id = e.id{BOOKMARK_ANNOTATION_JOINS}
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
    search_service: ExchangeSearchService = Depends(get_exchange_search_service),
):
    """Search exchange history via semantic, keyword, or hybrid mode."""
    results = await search_service.search(
        q=q, rp_folder=rp_folder, branch=branch,
        mode=mode, limit=limit, min_score=min_score,
    )
    return ExchangeSearchResponse(
        query=q, mode=mode.value, total_results=len(results), results=results,
    )
