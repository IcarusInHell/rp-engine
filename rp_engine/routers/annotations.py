"""Exchange annotation endpoints (split out of exchanges.py in Phase 7a)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import PRIORITY_EXCHANGE, Database
from rp_engine.dependencies import get_db
from rp_engine.models.exchange import (
    AnnotationCreate,
    AnnotationListResponse,
    AnnotationResponse,
    AnnotationUpdate,
    DeleteResponse,
)
from rp_engine.routers._shared import (
    _annotation_from_row,
    _build_update,
    _fetch_or_404,
)

logger = logging.getLogger(__name__)

# Exchange-scoped annotation operations live under /api/exchanges/{n}/annotations.
router = APIRouter(prefix="/api/exchanges", tags=["annotations"])

# Operations by annotation ID + the global list live at their own URL root.
top_router = APIRouter(prefix="/api/annotations", tags=["annotations"])


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
    # Bug C: use the inserted row id from the write future, not an
    # `ORDER BY id DESC LIMIT 1` re-query (two concurrent inserts on the same
    # exchange could swap and return the wrong row to one caller).
    new_id = await future

    row = await db.fetch_one(
        "SELECT * FROM exchange_annotations WHERE id = ?", [new_id]
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


@top_router.put("/{annotation_id}", response_model=AnnotationResponse)
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


@top_router.delete("/{annotation_id}", response_model=DeleteResponse)
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
    return DeleteResponse()


@top_router.patch("/{annotation_id}/resolve", response_model=AnnotationResponse)
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


@top_router.get("", response_model=AnnotationListResponse)
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
