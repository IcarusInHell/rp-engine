"""Shared helpers for the exchange-domain routers.

Used by ``exchanges.py``, ``bookmarks.py``, and ``annotations.py`` after the
Phase 7a flat split.

The ``BOOKMARK_ANNOTATION_JOINS`` SQL fragment (PoC-5) moved to
``utils/exchange_sql.py`` in Phase 7b: ``ExchangeSearchService`` now needs it
too, so it lives in a neutral leaf module below both the router and service
layers (single home preserved, no duplication).
"""

from __future__ import annotations

from fastapi import HTTPException

from rp_engine.database import Database
from rp_engine.models.exchange import AnnotationResponse


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
    ``e`` table alias pre-applied (used throughout these routers).
    """
    from rp_engine.services.ancestry_resolver import AncestryResolver

    return AncestryResolver.build_ancestry_sql(rp_folder, chain, table_alias="e")
