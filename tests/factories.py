"""Direct-to-DB seed helpers for tests.

These insert rows straight through the write queue (no card files, no LLM) so
foundation/service tests can construct precise state. The container-driven
fixtures in ``conftest.py`` cover the full card-indexing path separately.
"""

from __future__ import annotations

from datetime import UTC, datetime

from rp_engine.database import Database
from rp_engine.utils.trust import trust_stage

RP_FOLDER = "TestRP"


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def insert_session(
    db: Database,
    *,
    session_id: str = "sess-1",
    rp_folder: str = RP_FOLDER,
    branch: str = "main",
) -> str:
    fut = await db.enqueue_write(
        "INSERT INTO sessions (id, rp_folder, branch, started_at) VALUES (?, ?, ?, ?)",
        [session_id, rp_folder, branch, _now()],
    )
    await fut
    return session_id


async def insert_exchange(
    db: Database,
    exchange_number: int,
    *,
    session_id: str = "sess-1",
    rp_folder: str = RP_FOLDER,
    branch: str = "main",
    user_message: str | None = None,
    assistant_response: str | None = None,
) -> int:
    """Insert one exchange. Returns its row id."""
    fut = await db.enqueue_write(
        """INSERT INTO exchanges
               (session_id, rp_folder, branch, exchange_number,
                user_message, assistant_response, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            session_id,
            rp_folder,
            branch,
            exchange_number,
            user_message or f"user line {exchange_number}",
            assistant_response or f"assistant line {exchange_number}",
            _now(),
        ],
    )
    return await fut


async def seed_trust_baseline(
    db: Database,
    char_a: str,
    char_b: str,
    score: int,
    *,
    rp_folder: str = RP_FOLDER,
    branch: str = "main",
    source: str = "card",
) -> None:
    """Seed a *directional* trust baseline: ``char_a``'s trust toward ``char_b``."""
    fut = await db.enqueue_write(
        """INSERT INTO trust_baselines
               (character_a, character_b, rp_folder, branch,
                baseline_score, baseline_stage, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [char_a, char_b, rp_folder, branch, score, trust_stage(score), source, _now()],
    )
    await fut


async def seed_trust_modification(
    db: Database,
    char_a: str,
    char_b: str,
    change: int,
    *,
    exchange_number: int = 1,
    rp_folder: str = RP_FOLDER,
    branch: str = "main",
) -> None:
    """Seed a *directional* trust modification: ``char_a`` toward ``char_b``."""
    fut = await db.enqueue_write(
        """INSERT INTO trust_modifications
               (character_a, character_b, rp_folder, branch,
                change, exchange_number, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [char_a, char_b, rp_folder, branch, change, exchange_number, _now()],
    )
    await fut
