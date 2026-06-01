"""Trust stage computation and query helpers — single source of truth.

Used by context_engine, npc_engine, state_manager, ancestry_resolver,
branch_manager, and trigger_evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rp_engine.database import Database


@dataclass
class TrustResolution:
    """Result of resolving live trust between two characters."""

    baseline: int
    modification_sum: int
    live_score: int
    stage: str
    source: str  # 'card', 'runtime', 'default'

# Trust stage thresholds — expanded -50 to 50 range with 8 stages
TRUST_STAGES = [
    (-50, -36, "hostile"),
    (-35, -21, "antagonistic"),
    (-20, -11, "suspicious"),
    (-10, -1, "wary"),
    (0, 9, "neutral"),
    (10, 19, "familiar"),
    (20, 34, "trusted"),
    (35, 50, "devoted"),
]


def trust_stage(score: int) -> str:
    """Compute trust stage from score in -50 to 50 range."""
    for low, high, stage in TRUST_STAGES:
        if low <= score <= high:
            return stage
    # Clamp to extremes
    return TRUST_STAGES[0][2] if score < TRUST_STAGES[0][0] else TRUST_STAGES[-1][2]


async def fetch_trust_pair(
    db: Database, rp_folder: str, branch: str, char_a: str, char_b: str
) -> tuple[int, int]:
    """Return (baseline, modification_sum) for the directional pair char_a → char_b.

    Trust is asymmetric: ``(character_a, character_b)`` means "char_a's trust toward
    char_b". Reads the char_a→char_b row ONLY — never OR-merges the reverse direction
    (doing so flattens Alice's-trust-of-Bob into Bob's-trust-of-Alice). Callers that
    want the reverse direction must pass the args swapped.
    """
    baseline = await db.fetch_val(
        """SELECT baseline_score FROM trust_baselines
           WHERE rp_folder = ? AND branch = ?
             AND LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)""",
        [rp_folder, branch, char_a, char_b],
    )
    mod_sum = await db.fetch_val(
        """SELECT COALESCE(SUM(change), 0) FROM trust_modifications
           WHERE rp_folder = ? AND branch = ?
             AND LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)""",
        [rp_folder, branch, char_a, char_b],
    )
    return (baseline or 0, mod_sum or 0)


async def fetch_trust_map(
    db: Database, rp_folder: str, branch: str
) -> dict[tuple[str, str], tuple[int, int]]:
    """Return {(char_a_lower, char_b_lower): (baseline, mod_sum)} for all pairs."""
    result: dict[tuple[str, str], tuple[int, int]] = {}

    baseline_rows = await db.fetch_all(
        """SELECT LOWER(character_a) as ca, LOWER(character_b) as cb, baseline_score
           FROM trust_baselines WHERE rp_folder = ? AND branch = ?""",
        [rp_folder, branch],
    )
    for row in baseline_rows:
        pair = (row["ca"], row["cb"])
        result[pair] = (row["baseline_score"], 0)

    mod_rows = await db.fetch_all(
        """SELECT LOWER(character_a) as ca, LOWER(character_b) as cb,
                  COALESCE(SUM(change), 0) as total
           FROM trust_modifications WHERE rp_folder = ? AND branch = ?
           GROUP BY LOWER(character_a), LOWER(character_b)""",
        [rp_folder, branch],
    )
    for row in mod_rows:
        pair = (row["ca"], row["cb"])
        baseline = result.get(pair, (0, 0))[0]
        result[pair] = (baseline, row["total"])

    return result


async def fetch_thread_progress(
    db: Database, rp_folder: str, branch: str
) -> list[dict]:
    """Return thread counter rows: name, current_counter, thresholds, consequences."""
    return await db.fetch_all(
        """SELECT pt.name, tc.current_counter, pt.thresholds, pt.consequences
           FROM thread_counters tc
           JOIN plot_threads pt ON tc.thread_id = pt.id AND tc.rp_folder = pt.rp_folder
           WHERE tc.rp_folder = ? AND tc.branch = ?""",
        [rp_folder, branch],
    )


async def resolve_trust_for_pair(
    db: Database,
    char_a: str,
    char_b: str,
    rp_folder: str,
    branch: str,
) -> TrustResolution:
    """Single source of truth for resolving live trust between two characters.

    Resolution:
    1. trust_baselines row for (char_a, char_b) on this branch (case-insensitive)
    2. Reverse direction: (char_b, char_a) if forward not found
    3. Fallback: baseline 0, source 'default'

    Then: baseline + SUM(trust_modifications) = live score
    """
    # Look up baseline (either direction)
    baseline_row = await db.fetch_one(
        """SELECT baseline_score, source FROM trust_baselines
           WHERE rp_folder = ? AND branch = ?
             AND LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)""",
        [rp_folder, branch, char_a, char_b],
    )
    if not baseline_row:
        baseline_row = await db.fetch_one(
            """SELECT baseline_score, source FROM trust_baselines
               WHERE rp_folder = ? AND branch = ?
                 AND LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)""",
            [rp_folder, branch, char_b, char_a],
        )

    if baseline_row:
        baseline = baseline_row.get("baseline_score") or 0
        source = baseline_row.get("source") or "runtime"
    else:
        baseline = 0
        source = "default"

    # Sum modifications for the directional pair char_a → char_b ONLY.
    # OR-merging the reverse here flattens directions: a one-sided modification on
    # char_b → char_a would leak into char_a → char_b's live score (the Phase-0c
    # discriminator that exposed this bug). Baselines stay forward-first with a
    # reverse fallback above (one-sided cards), which is a deliberate fallback, not
    # a merge — but modifications are always read in the requested direction.
    mod_sum = await db.fetch_val(
        """SELECT COALESCE(SUM(change), 0) FROM trust_modifications
           WHERE rp_folder = ? AND branch = ?
             AND LOWER(character_a) = LOWER(?) AND LOWER(character_b) = LOWER(?)""",
        [rp_folder, branch, char_a, char_b],
    )
    mod_sum = mod_sum or 0

    live = baseline + mod_sum
    return TrustResolution(
        baseline=baseline,
        modification_sum=mod_sum,
        live_score=live,
        stage=trust_stage(live),
        source=source,
    )
