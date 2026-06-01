"""Exchange bookmark endpoints (split out of exchanges.py in Phase 7a)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import PRIORITY_EXCHANGE, Database
from rp_engine.dependencies import get_db
from rp_engine.models.exchange import (
    BookmarkCreate,
    BookmarkListResponse,
    BookmarkResponse,
    BookmarkUpdate,
    DeleteResponse,
)
from rp_engine.routers._shared import _build_update, _fetch_or_404

logger = logging.getLogger(__name__)

# Exchange-scoped bookmark operations live under /api/exchanges/{n}/bookmark.
router = APIRouter(prefix="/api/exchanges", tags=["bookmarks"])

# The dedicated bookmarks list lives at its own URL root.
list_router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])


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

    # Auto-generate name if not provided. Bug D: derive from the highest existing
    # "Bookmark #N" suffix, not COUNT(*) — a deleted bookmark would otherwise let
    # the next auto-name collide with a previously-used one. "Bookmark #" is 10
    # chars, so SUBSTR(name, 11) starts at the first digit.
    if not body.name:
        max_n = await db.fetch_val(
            """SELECT MAX(CAST(SUBSTR(name, 11) AS INTEGER))
               FROM exchange_bookmarks
               WHERE rp_folder = ? AND branch = ?
                 AND name LIKE 'Bookmark #%'""",
            [rp_folder, branch],
        )
        body.name = f"Bookmark #{(max_n or 0) + 1}"

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
        # Bug C: use the inserted row id from the write future, not a racy
        # re-query by (rp_folder, branch, exchange_number).
        new_id = await future
    except Exception:
        raise HTTPException(409, detail="Bookmark already exists for this exchange") from None

    row = await db.fetch_one(
        "SELECT * FROM exchange_bookmarks WHERE id = ?", [new_id]
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


@router.delete("/{exchange_number}/bookmark", response_model=DeleteResponse)
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
    return DeleteResponse()


@list_router.get("", response_model=BookmarkListResponse)
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
