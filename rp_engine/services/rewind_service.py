"""RewindService — the rewind-via-branch-creation workflow.

Extracted verbatim from ``save_exchange`` in Phase 7b (split-exchanges-router
R2: standalone single-purpose service, not folded into ``ExchangeWriter``). A
rewind is append-only: when a save targets an ``exchange_number`` that already
exists, a new branch is forked from ``exchange_number - 1`` and the save lands
there. The conflict *detection* stays in the router handler; this service owns
everything after a conflict is confirmed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rp_engine.database import Database
from rp_engine.services.branch_manager import BranchManager

logger = logging.getLogger(__name__)


@dataclass
class RewindResult:
    """Outcome of a rewind: the forked branch and the count of orphaned exchanges."""

    new_branch: str
    rewound_count: int


class RewindService:
    """Rewind-via-branch-fork — forks a new branch at ``exchange_number − 1`` so a conflicting save lands there (append-only, never deletes)."""

    def __init__(self, db: Database, branch_manager: BranchManager):
        self.db = db
        self.branch_manager = branch_manager

    async def rewind_to(
        self,
        *,
        rp_folder: str,
        source_branch: str,
        exchange_number: int,
        branch_name_hint: str | None = None,
    ) -> RewindResult:
        """Fork a new branch at ``exchange_number - 1`` and report orphaned rows.

        ``branch_name_hint`` (from ``metadata.branch_name``) names the new branch
        when provided; otherwise a unique rewind name is generated.
        """
        rewind_point = exchange_number - 1
        new_branch_name = branch_name_hint
        if not new_branch_name:
            new_branch_name = await self.branch_manager.generate_rewind_branch_name(
                source_branch, rewind_point, rp_folder
            )

        # Bug A: count the orphaned exchanges on the OLD (source) branch BEFORE
        # creating the new branch. create_branch snapshots state but does not copy
        # exchanges, so the new branch has zero rows >= exchange_number —
        # querying it (as the original code did) always yielded 0.
        to_delete_count = await self.db.fetch_val(
            "SELECT COUNT(*) FROM exchanges WHERE rp_folder=? AND branch=? AND exchange_number>=?",
            [rp_folder, source_branch, exchange_number],
        )
        rewound_count = to_delete_count or 0

        # Create branch with full state snapshot at the rewind point
        await self.branch_manager.create_branch(
            name=new_branch_name,
            rp_folder=rp_folder,
            description=f"Rewind from exchange {exchange_number}",
            branch_from=source_branch,
            branch_point_exchange=rewind_point,
        )

        return RewindResult(new_branch=new_branch_name, rewound_count=rewound_count)
