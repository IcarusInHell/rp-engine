"""Pydantic models for branch management."""

from __future__ import annotations

from pydantic import BaseModel


class BranchCreate(BaseModel):
    """Request to create a branch, optionally forking from a point on another branch."""
    name: str
    rp_folder: str
    description: str | None = None
    branch_from: str | None = None
    branch_point_exchange: int | None = None


class BranchInfo(BaseModel):
    """Metadata for a single branch — origin, branch point, active/archived flags, exchange count."""
    name: str
    rp_folder: str
    created_from: str | None = None
    branch_point_session: str | None = None
    branch_point_exchange: int | None = None
    description: str | None = None
    is_active: bool = False
    is_archived: bool = False
    created_at: str | None = None
    exchange_count: int = 0


class BranchArchiveRequest(BaseModel):
    """Request to archive or unarchive a branch."""
    archived: bool = True


class BranchListResponse(BaseModel):
    """All branches for an RP plus which one is active."""
    active_branch: str | None = None
    branches: list[BranchInfo]


class BranchSwitchRequest(BaseModel):
    """Request to switch the active branch by name."""
    name: str


class BranchSwitchResponse(BaseModel):
    """Result of a branch switch — new active branch and the previous one."""
    active_branch: str
    previous_branch: str | None = None


class CheckpointCreate(BaseModel):
    """Request to create a named checkpoint at the current exchange."""
    name: str
    description: str | None = None


class CheckpointInfo(BaseModel):
    """Metadata for a checkpoint — branch, exchange number, and description."""
    name: str
    branch: str
    exchange_number: int
    description: str | None = None
    created_at: str


class CheckpointRestoreRequest(BaseModel):
    """Request to restore a checkpoint by name."""
    checkpoint_name: str


class CheckpointRestoreResponse(BaseModel):
    """Result of a checkpoint restore — source, exchange number, and any new branch forked."""
    restored_from: str
    exchange_number: int
    new_branch: str | None = None
    rewound_count: int | None = None  # deprecated, kept for backward compat
