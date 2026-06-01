"""Shared card-lookup helpers for the cards router package.

``_find_card_row`` requires a ``card_type`` (CRUD endpoints know it from the URL
path) and falls back to alias lookup. The cross-type variant used by reciprocal-
relationship sync now lives on ``CardAuthoringService`` (Phase 6b) — a service
importing a router helper would be backwards layering.
"""

from __future__ import annotations

from rp_engine.database import Database
from rp_engine.utils.normalization import normalize_key


def _normalize_url_name(name: str) -> str:
    """Normalize a URL path name — convert hyphens to spaces, then normalize."""
    return normalize_key(name.replace("-", " "))


async def _find_card_row(
    db: Database,
    card_type: str,
    name: str,
    *,
    rp_folder: str | None = None,
) -> dict | None:
    """Look up a card row by type + name, falling back to alias search.

    If *rp_folder* is given, also tries entity ID lookup (``rp_folder:key``).
    Returns the DB row dict or ``None``.
    """
    normalized = _normalize_url_name(name)

    # Direct lookup by type + normalized name
    row = await db.fetch_one(
        "SELECT * FROM story_cards WHERE card_type = ? AND LOWER(name) = ?",
        [card_type, normalized],
    )
    if row:
        return row

    # Try entity ID if rp_folder provided (useful for card_id references)
    if rp_folder:
        entity_id = f"{rp_folder}:{normalized}"
        row = await db.fetch_one(
            "SELECT * FROM story_cards WHERE id = ?", [entity_id]
        )
        if row:
            return row

    # Fallback: alias lookup (try both normalized forms)
    for alias_key in (normalized, normalize_key(name)):
        row = await db.fetch_one(
            """SELECT sc.* FROM story_cards sc
               JOIN entity_aliases ea ON sc.id = ea.entity_id
               WHERE sc.card_type = ? AND ea.alias = ?""",
            [card_type, alias_key],
        )
        if row:
            return row

    return None
