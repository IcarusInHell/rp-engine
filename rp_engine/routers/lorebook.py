"""Lorebook management endpoints (Phase 5b — system layer only).

Lorebooks are FILE-DROP (files = source of truth); these endpoints manage *which*
files are active per RP and trigger re-indexing. They do NOT author entries — a
full authoring/management UI is a deferred future plan. The frontend uses these
to: list active + available lorebooks, toggle the per-RP active-set (writes
``lorebooks:`` to Story_Guidelines.md frontmatter), and re-index after edits.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from rp_engine.config import get_config
from rp_engine.dependencies import (
    get_guidelines_service,
    get_lorebook_indexer,
    get_vault_root,
)
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.lorebook_indexer import LorebookIndexer
from rp_engine.utils.frontmatter import parse_file, serialize_frontmatter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lorebook", tags=["lorebook"])


class LorebookFile(BaseModel):
    """One discoverable lorebook file — stem, scope ('rp' or 'global'), on-disk path, and whether it's active for this RP."""

    stem: str
    scope: str          # 'rp' | 'global'
    path: str
    active: bool


class LorebookListResponse(BaseModel):
    """Lorebook listing — the global enabled flag, the per-RP active-set (None = all per-RP files active), and the discovered files."""

    enabled: bool                       # global lorebook_enabled config
    active_set: list[str] | None        # explicit per-RP list, or None = all per-RP active
    files: list[LorebookFile]


class ActiveSetUpdate(BaseModel):
    """Request body to set the per-RP active lorebook stems (written to `lorebooks:` frontmatter)."""

    lorebooks: list[str]


def _guidelines_path(vault_root: Path, rp_folder: str) -> Path:
    return vault_root / rp_folder / "RP State" / "Story_Guidelines.md"


@router.get("", response_model=LorebookListResponse)
async def list_lorebooks(
    rp_folder: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
    indexer: LorebookIndexer = Depends(get_lorebook_indexer),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """List available lorebook files (per-RP + global) with active flags.

    Per-RP active-set: an explicit ``lorebooks:`` frontmatter list selects files
    by stem; absent (None) → ALL per-RP files active. Global files are always
    available (active independent of the per-RP list)."""
    cfg = get_config().context
    guidelines = guidelines_svc.get_guidelines(rp_folder)
    active_set = guidelines.lorebooks if guidelines else None

    files: list[LorebookFile] = []
    rp_dir = indexer.rp_lorebook_dir(rp_folder)
    for p in LorebookIndexer._discover(rp_dir):
        active = active_set is None or p.stem in set(active_set)
        files.append(LorebookFile(stem=p.stem, scope="rp", path=str(p), active=active))
    if cfg.lorebook_global_path:
        for p in LorebookIndexer._discover(Path(cfg.lorebook_global_path)):
            files.append(LorebookFile(stem=p.stem, scope="global", path=str(p), active=True))

    return LorebookListResponse(enabled=cfg.lorebook_enabled, active_set=active_set, files=files)


@router.put("/active", response_model=LorebookListResponse)
async def set_active_set(
    body: ActiveSetUpdate,
    rp_folder: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
    indexer: LorebookIndexer = Depends(get_lorebook_indexer),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Write the per-RP ``lorebooks:`` active-set to Story_Guidelines.md frontmatter."""
    path = _guidelines_path(vault_root, rp_folder)
    if not path.exists():
        raise HTTPException(404, detail=f"No guidelines found for {rp_folder}")

    cleaned = [s.strip() for s in body.lorebooks if isinstance(s, str) and s.strip()]
    frontmatter, file_body = parse_file(path)
    if frontmatter is None:
        raise HTTPException(422, detail="Could not parse guidelines frontmatter")
    frontmatter["lorebooks"] = cleaned
    path.write_text(serialize_frontmatter(frontmatter, file_body), encoding="utf-8")
    guidelines_svc.invalidate(rp_folder)

    return await list_lorebooks(rp_folder, vault_root, indexer, guidelines_svc)


@router.post("/reindex")
async def reindex_lorebooks(
    rp_folder: str = Query(...),
    indexer: LorebookIndexer = Depends(get_lorebook_indexer),
):
    """Re-index a per-RP lorebook folder + the global library from disk."""
    cfg = get_config().context
    rp_count = await indexer.index_rp(rp_folder)
    global_count = await indexer.index_global(cfg.lorebook_global_path)
    return {"reindexed": True, "rp_entries": rp_count, "global_entries": global_count}
