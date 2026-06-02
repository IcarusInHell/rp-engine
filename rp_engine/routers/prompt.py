"""Prompt section-ordering endpoints (Phase 5a).

Manages the per-RP ``prompt_order`` list stored in ``Story_Guidelines.md``
frontmatter. ``prompt_order`` governs the order of **depth-0** prompt sections
(static + non-injected dynamic); sections pulled to an injection depth > 0 are
controlled by ``injection_depths`` instead and are unaffected by omission here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from rp_engine.dependencies import get_guidelines_service, get_vault_root
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.prompt_assembler import (
    DEFAULT_PROMPT_ORDER,
    _ALL_SECTION_NAMES,
)
from rp_engine.utils.frontmatter import parse_file, serialize_frontmatter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


class PromptOrderResponse(BaseModel):
    """Current effective order + the default + the set of known section names."""
    order: list[str]
    is_custom: bool
    default: list[str]
    known_sections: list[str]


class PromptOrderUpdate(BaseModel):
    order: list[str]


def _guidelines_path(vault_root: Path, rp_folder: str) -> Path:
    return vault_root / rp_folder / "RP State" / "Story_Guidelines.md"


@router.get("/order", response_model=PromptOrderResponse)
async def get_prompt_order(
    rp_folder: str = Query(...),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Return the RP's section order (custom if set, else the default)."""
    guidelines = guidelines_svc.get_guidelines(rp_folder)
    custom = guidelines.prompt_order if guidelines else None
    return PromptOrderResponse(
        order=custom if custom is not None else list(DEFAULT_PROMPT_ORDER),
        is_custom=custom is not None,
        default=list(DEFAULT_PROMPT_ORDER),
        known_sections=sorted(_ALL_SECTION_NAMES),
    )


@router.put("/order", response_model=PromptOrderResponse)
async def set_prompt_order(
    body: PromptOrderUpdate,
    rp_folder: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Write ``prompt_order`` to the RP's Story_Guidelines.md frontmatter.

    Unknown section names are rejected (a typo would silently drop the section at
    prompt time). Empty / blank entries are dropped.
    """
    path = _guidelines_path(vault_root, rp_folder)
    if not path.exists():
        raise HTTPException(404, detail=f"No guidelines found for {rp_folder}")

    cleaned = [s.strip() for s in body.order if isinstance(s, str) and s.strip()]
    unknown = [s for s in cleaned if s not in _ALL_SECTION_NAMES]
    if unknown:
        raise HTTPException(
            422, detail=f"Unknown section name(s): {', '.join(unknown)}"
        )

    frontmatter, file_body = parse_file(path)
    if frontmatter is None:
        raise HTTPException(422, detail="Could not parse guidelines frontmatter")
    frontmatter["prompt_order"] = cleaned
    path.write_text(serialize_frontmatter(frontmatter, file_body), encoding="utf-8")
    guidelines_svc.invalidate(rp_folder)

    return PromptOrderResponse(
        order=cleaned,
        is_custom=True,
        default=list(DEFAULT_PROMPT_ORDER),
        known_sections=sorted(_ALL_SECTION_NAMES),
    )


@router.post("/order/reset", response_model=PromptOrderResponse)
async def reset_prompt_order(
    rp_folder: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Remove the custom ``prompt_order`` so the default order applies."""
    path = _guidelines_path(vault_root, rp_folder)
    if not path.exists():
        raise HTTPException(404, detail=f"No guidelines found for {rp_folder}")

    frontmatter, file_body = parse_file(path)
    if frontmatter is None:
        raise HTTPException(422, detail="Could not parse guidelines frontmatter")
    frontmatter.pop("prompt_order", None)
    path.write_text(serialize_frontmatter(frontmatter, file_body), encoding="utf-8")
    guidelines_svc.invalidate(rp_folder)

    return PromptOrderResponse(
        order=list(DEFAULT_PROMPT_ORDER),
        is_custom=False,
        default=list(DEFAULT_PROMPT_ORDER),
        known_sections=sorted(_ALL_SECTION_NAMES),
    )
