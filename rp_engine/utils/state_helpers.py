"""Cross-domain state helpers shared by the state domain services.

``resolve_exchange_number`` is needed by CharacterService, SceneService, and
RelationshipService when writing CoW snapshots, so it lives here as a free
function rather than on any single domain service (per decompose-state-manager
Option B, R1).
"""

from __future__ import annotations

from rp_engine.database import Database


async def resolve_exchange_number(
    db: Database, exchange_id: int | None, rp_folder: str, branch: str
) -> int:
    """Resolve an exchange_id to an exchange_number, or get the latest."""
    if exchange_id is not None:
        row = await db.fetch_one(
            "SELECT exchange_number FROM exchanges WHERE id = ?", [exchange_id]
        )
        if row:
            return row["exchange_number"]
    # Fall back to latest exchange_number on this branch
    latest = await db.fetch_val(
        "SELECT MAX(exchange_number) FROM exchanges WHERE rp_folder = ? AND branch = ?",
        [rp_folder, branch],
    )
    return latest or 0
