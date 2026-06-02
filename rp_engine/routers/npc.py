"""NPC endpoints — reactions, trust, listing, and behavioral intelligence."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from rp_engine.config import get_config
from rp_engine.dependencies import get_npc_engine
from rp_engine.models.npc import (
    NPCBatchRequest,
    NPCListItem,
    NPCReaction,
    NPCReactRequest,
    TrustInfo,
)

logger = logging.getLogger(__name__)


def _get_npc_intelligence(request: Request):
    """Get NPC intelligence from app state, or None if not initialized."""
    return getattr(request.app.state, "npc_intelligence", None)


class FeedbackBody(BaseModel):
    """NPC-intelligence feedback submission — the original output plus optional user feedback/rewrite and an `accepted` flag."""

    original_output: str
    user_feedback: str | None = None
    user_rewrite: str | None = None
    accepted: bool = True

router = APIRouter(tags=["npc"])


@router.post("/api/npc/react", response_model=NPCReaction)
async def react(
    body: NPCReactRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    npc_engine=Depends(get_npc_engine),
):
    """Get a single NPC's reaction to a scene moment."""
    pov = body.pov_character or get_config().rp.default_pov_character
    try:
        return await npc_engine.get_reaction(
            npc_name=body.npc_name,
            scene_prompt=body.scene_prompt,
            pov_character=pov,
            rp_folder=rp_folder,
            branch=branch,
            model_override=body.model_override,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/api/npc/react-batch", response_model=list[NPCReaction])
async def react_batch(
    body: NPCBatchRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    npc_engine=Depends(get_npc_engine),
):
    """Get reactions from multiple NPCs in parallel."""
    pov = body.pov_character or get_config().rp.default_pov_character
    return await npc_engine.get_batch_reactions(
        npc_names=body.npc_names,
        scene_prompt=body.scene_prompt,
        pov_character=pov,
        rp_folder=rp_folder,
        branch=branch,
    )


@router.get("/api/npc/{name}/trust", response_model=TrustInfo)
async def get_trust(
    name: str,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    target_name: str | None = Query(None),
    npc_engine=Depends(get_npc_engine),
):
    """Check the current trust level between an NPC and a target character."""
    target = target_name or get_config().rp.default_pov_character
    return await npc_engine.get_trust(
        npc_name=name,
        target=target,
        rp_folder=rp_folder,
        branch=branch,
    )


@router.get("/api/npcs", response_model=list[NPCListItem])
async def list_npcs(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    pov_character: str | None = Query(None),
    npc_engine=Depends(get_npc_engine),
):
    """List all available NPCs in an RP with their state."""
    pov = pov_character or get_config().rp.default_pov_character
    return await npc_engine.list_npcs(
        rp_folder=rp_folder,
        branch=branch,
        pov_character=pov,
    )


# --- NPC Behavioral Intelligence endpoints ---


@router.get("/api/npc/intelligence/stats")
async def intelligence_stats(
    npc_intelligence=Depends(_get_npc_intelligence),
):
    """Get NPC behavioral intelligence statistics."""
    if npc_intelligence is None:
        raise HTTPException(status_code=503, detail="NPC intelligence not initialized")
    return npc_intelligence.get_stats()


@router.get("/api/npc/intelligence/patterns")
async def intelligence_patterns(
    category: str | None = Query(None),
    npc_intelligence=Depends(_get_npc_intelligence),
):
    """List behavioral patterns, optionally filtered by category."""
    if npc_intelligence is None:
        raise HTTPException(status_code=503, detail="NPC intelligence not initialized")
    patterns = npc_intelligence.list_patterns(category=category)
    return [
        {
            "id": p.id,
            "category": p.category.value,
            "subcategory": p.subcategory,
            "description": p.description,
            "direction": p.direction.value,
            "severity": p.severity,
            "proficiency": p.proficiency,
            "frequency": p.frequency,
            "correction_count": p.correction_count,
        }
        for p in patterns
    ]


@router.post("/api/npc/intelligence/feedback")
async def intelligence_feedback(
    body: FeedbackBody,
    npc_intelligence=Depends(_get_npc_intelligence),
):
    """Submit behavioral feedback for the NPC intelligence system."""
    if npc_intelligence is None:
        raise HTTPException(status_code=503, detail="NPC intelligence not initialized")

    from npc_intelligence.types import FeedbackInput

    if body.accepted:
        result = npc_intelligence.record_outcome(body.original_output, accepted=True)
    else:
        feedback = FeedbackInput(
            original_output=body.original_output,
            user_feedback=body.user_feedback,
            user_rewrite=body.user_rewrite,
        )
        result = npc_intelligence.record_outcome(
            body.original_output, accepted=False, feedback=feedback)

    return result
