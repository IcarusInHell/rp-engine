"""AI-driven card authoring endpoints: suggest, generate-name.

Thin router wrappers — the business logic (LLM calls, evidence gathering,
reciprocal-relationship sync) lives in ``CardAuthoringService`` (Phase 6b). The
reciprocal sync itself is invoked from ``crud.create_card`` via the same service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from rp_engine.dependencies import get_card_authoring_service
from rp_engine.models.story_card import (
    GenerateCardNameRequest,
    GenerateCardNameResponse,
    SuggestCardRequest,
    SuggestCardResponse,
)
from rp_engine.services.card_authoring import CardAuthoringService

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.post("/suggest", response_model=SuggestCardResponse)
async def suggest_card(
    body: SuggestCardRequest,
    svc: CardAuthoringService = Depends(get_card_authoring_service),
):
    """Generate a draft story card for an entity by searching exchanges and using LLM."""
    return await svc.suggest_card(body)


@router.post("/generate-name", response_model=GenerateCardNameResponse)
async def generate_card_name(
    body: GenerateCardNameRequest,
    rp_folder: str = Query(...),
    svc: CardAuthoringService = Depends(get_card_authoring_service),
):
    """Generate name suggestions for a new story card using LLM."""
    return await svc.generate_card_name(body, rp_folder)
