"""Context endpoints — the main intelligence API.

POST /api/context — primary endpoint. Client sends raw user_message,
gets back everything needed: cards, NPC briefs, state, alerts, guidelines.

Also includes guidelines, graph resolution, continuity brief, and system prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.dependencies import (
    get_auto_save_manager,
    get_context_engine,
    get_graph_resolver,
    get_guidelines_service,
    get_prompt_assembler,
    get_vault_root,
)
from rp_engine.models.context import (
    AutoSaveResult as AutoSaveResultModel,
)
from rp_engine.models.context import (
    ContextRequest,
    ContextResponse,
    ResolveRequest,
)
from rp_engine.models.rp import GuidelinesResponse
from rp_engine.services.auto_save import AutoSaveManager
from rp_engine.services.context_engine import ContextEngine
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.prompt_assembler import PromptAssembler
from rp_engine.utils.frontmatter import parse_file, serialize_frontmatter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/context", tags=["context"])


@router.post("", response_model=ContextResponse)
async def get_context(
    body: ContextRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    session_id: str = Query(None),
    skip_guidelines: bool = Query(False),
    context_engine: ContextEngine = Depends(get_context_engine),
    auto_save: AutoSaveManager | None = Depends(get_auto_save_manager),
):
    """The main endpoint. Smart API, dumb client.

    When auto-save is enabled (deprecated) and last_response contains <output> tags,
    the previous exchange is auto-saved before returning context.
    Preferred: clients call save_exchange explicitly after every RP response.
    """
    auto_saved = None
    if auto_save is not None:
        result = await auto_save.try_auto_save(
            body.user_message, body.last_response, rp_folder, branch, session_id
        )
        if result is not None:
            auto_saved = AutoSaveResultModel(
                exchange_id=result.exchange_id,
                exchange_number=result.exchange_number,
                session_id=result.session_id,
            )

    resp = await context_engine.get_context(body, rp_folder, branch, session_id)

    if auto_saved is not None:
        resp.auto_saved = auto_saved

    if skip_guidelines:
        resp.guidelines = None

    return resp


@router.post("/resolve")
async def resolve_context(
    body: ResolveRequest,
    rp_folder: str = Query(...),
    graph_resolver=Depends(get_graph_resolver),
):
    """Explicit graph resolution from keywords."""
    seed_ids = []
    for kw in body.keywords:
        eid = await graph_resolver.resolve_entity(kw, rp_folder)
        if eid:
            seed_ids.append(eid)

    if not seed_ids:
        return {"connections": [], "seed_count": 0}

    connections = await graph_resolver.get_connections(
        seed_ids, max_hops=body.max_hops, max_results=body.max_results
    )

    return {
        "connections": [
            {
                "entity_id": c.entity_id,
                "entity_name": c.entity_name,
                "card_type": c.card_type,
                "hop": c.hop,
                "path": c.path,
                "connection_type": c.connection_type,
                "content": c.content,
                "summary": c.summary,
            }
            for c in connections
        ],
        "seed_count": len(seed_ids),
    }


@router.get("/continuity")
async def get_continuity(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    context_engine: ContextEngine = Depends(get_context_engine),
):
    """Data-only continuity brief (Phase 2 — no LLM synthesis)."""
    return await context_engine.get_continuity_brief(rp_folder, branch)


@router.get("/guidelines", response_model=GuidelinesResponse)
async def get_guidelines(
    rp_folder: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Get parsed Story_Guidelines.md frontmatter for an RP."""
    guidelines_path = vault_root / rp_folder / "RP State" / "Story_Guidelines.md"
    if not guidelines_path.exists():
        raise HTTPException(404, detail=f"No guidelines found for {rp_folder}")

    resp = guidelines_svc.get_guidelines(rp_folder)
    if resp is None:
        raise HTTPException(422, detail="Could not parse guidelines frontmatter")

    return resp


@router.put("/guidelines", response_model=GuidelinesResponse)
async def update_guidelines(
    body: dict,
    rp_folder: str = Query(...),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Update Story_Guidelines.md frontmatter fields (partial merge)."""
    guidelines_path = vault_root / rp_folder / "RP State" / "Story_Guidelines.md"
    if not guidelines_path.exists():
        raise HTTPException(404, detail=f"No guidelines found for {rp_folder}")

    frontmatter, file_body = parse_file(guidelines_path)
    if frontmatter is None:
        raise HTTPException(422, detail="Could not parse guidelines frontmatter")

    # Extract body separately (not a frontmatter field)
    new_body = body.pop("body", None)
    if new_body is not None:
        file_body = new_body

    # Merge updated fields into existing frontmatter
    allowed_fields = {
        "pov_mode", "pov_character", "dual_characters", "narrative_voice",
        "tense", "tone", "scene_pacing", "response_length",
        "integrate_user_narrative", "preserve_user_details",
        "sensitive_themes", "hard_limits",
        "include_writing_principles", "include_npc_framework", "include_output_format",
    }
    for key, value in body.items():
        if key in allowed_fields:
            frontmatter[key] = value

    # Write back
    guidelines_path.write_text(
        serialize_frontmatter(frontmatter, file_body), encoding="utf-8"
    )

    # Invalidate cache
    guidelines_svc.invalidate(rp_folder)

    # Re-read and return the updated guidelines
    resp = GuidelinesResponse(
        pov_mode=frontmatter.get("pov_mode"),
        pov_character=frontmatter.get("pov_character"),
        dual_characters=frontmatter.get("dual_characters", []),
        narrative_voice=frontmatter.get("narrative_voice"),
        tense=frontmatter.get("tense"),
        tone=frontmatter.get("tone"),
        scene_pacing=frontmatter.get("scene_pacing"),
        integrate_user_narrative=frontmatter.get("integrate_user_narrative"),
        preserve_user_details=frontmatter.get("preserve_user_details"),
        sensitive_themes=frontmatter.get("sensitive_themes", []),
        hard_limits=frontmatter.get("hard_limits"),
        response_length=frontmatter.get("response_length"),
        include_writing_principles=frontmatter.get("include_writing_principles", True),
        include_npc_framework=frontmatter.get("include_npc_framework", True),
        include_output_format=frontmatter.get("include_output_format", True),
        body=file_body.strip() if file_body and file_body.strip() else None,
    )
    return resp


@router.get("/guidelines/system-prompt")
async def get_system_prompt(
    rp_folder: str = Query(...),
    assembler: PromptAssembler = Depends(get_prompt_assembler),
):
    """Return a structured system prompt with writing rules, NPC framework, and RP conventions."""
    sections = assembler.get_sections(rp_folder)
    system_prompt = assembler.assemble_static_prompt(sections)
    return {"system_prompt": system_prompt, "sections": sections}
