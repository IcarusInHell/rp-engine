"""Chat endpoint — send text, get RP narrative back.

Includes regenerate (swipe), continue, and variant listing.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import StreamingResponse

from rp_engine.database import Database
from rp_engine.dependencies import get_chat_manager, get_db, get_prompt_assembler, require_chat_mode, resolve_active_session
from rp_engine.models.chat import (
    ChatRequest,
    ChatResponse,
    ContinueRequest,
    ContinueResponse,
    RegenerateRequest,
    RegenerateResponse,
    SwipeRequest,
    SwipeResponse,
    VariantInfo,
    VariantsResponse,
)
from rp_engine.services.chat_manager import ChatManager
from rp_engine.services.prompt_assembler import PromptAssembler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _stream(gen: AsyncIterator[str]) -> StreamingResponse:
    """Wrap an SSE generator in a StreamingResponse."""
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("", response_model=ChatResponse, dependencies=[Depends(require_chat_mode("provider"))])
async def chat(
    body: ChatRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    chat_manager: ChatManager = Depends(get_chat_manager),
):
    """Send an RP message and get a narrative response."""
    rp_folder, branch, session_id = await resolve_active_session(db, rp_folder, branch)

    kwargs = dict(
        user_message=body.user_message,
        rp_folder=rp_folder, branch=branch, session_id=session_id,
        message_mode=body.message_mode,
        attach_card_ids=body.attach_card_ids,
        scene_override=body.scene_override,
    )

    if body.stream:
        return _stream(chat_manager.chat_stream(**kwargs))

    return await chat_manager.chat(**kwargs)


@router.post("/regenerate", response_model=RegenerateResponse)
async def regenerate(
    body: RegenerateRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    chat_manager: ChatManager = Depends(get_chat_manager),
):
    """Regenerate an exchange's response. Saves as a new variant."""
    rp_folder, branch, session_id = await resolve_active_session(db, rp_folder, branch)

    try:
        if body.stream:
            return _stream(chat_manager.regenerate_stream(
                rp_folder=rp_folder, branch=branch, session_id=session_id,
                exchange_number=body.exchange_number,
                temperature=body.temperature, model=body.model,
            ))

        return await chat_manager.regenerate(
            rp_folder=rp_folder, branch=branch, session_id=session_id,
            exchange_number=body.exchange_number,
            temperature=body.temperature, model=body.model,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from None


@router.post("/swipe", response_model=SwipeResponse)
async def swipe(
    body: SwipeRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    chat_manager: ChatManager = Depends(get_chat_manager),
):
    """Switch the active variant for an exchange."""
    rp_folder, branch, _ = await resolve_active_session(db, rp_folder, branch)

    try:
        return await chat_manager.swipe(
            rp_folder=rp_folder, branch=branch,
            exchange_number=body.exchange_number,
            variant_index=body.variant_index,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from None


@router.post("/continue", response_model=ContinueResponse)
async def continue_response(
    body: ContinueRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    chat_manager: ChatManager = Depends(get_chat_manager),
):
    """Continue generating from where a response left off."""
    rp_folder, branch, session_id = await resolve_active_session(db, rp_folder, branch)

    try:
        if body.stream:
            return _stream(chat_manager.continue_stream(
                rp_folder=rp_folder, branch=branch, session_id=session_id,
                exchange_number=body.exchange_number,
                max_tokens=body.max_tokens,
            ))

        return await chat_manager.continue_response(
            rp_folder=rp_folder, branch=branch, session_id=session_id,
            exchange_number=body.exchange_number,
            max_tokens=body.max_tokens,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from None


@router.get("/variants/{exchange_number}", response_model=VariantsResponse)
async def list_variants(
    exchange_number: int,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    chat_manager: ChatManager = Depends(get_chat_manager),
):
    """List all variants for an exchange."""
    rp_folder, branch, _ = await resolve_active_session(db, rp_folder, branch)

    try:
        exchange_id, variants = await chat_manager.get_variants(
            rp_folder, branch, exchange_number,
        )
    except ValueError as e:
        raise HTTPException(404, detail=str(e)) from None

    return VariantsResponse(
        exchange_number=exchange_number,
        exchange_id=exchange_id,
        variants=[
            VariantInfo(
                id=v["id"],
                variant_index=i,
                is_active=bool(v["is_active"]),
                model_used=v.get("model_used"),
                temperature=v.get("temperature"),
                source=v.get("source", "llm"),
                continue_count=v.get("continue_count", 0),
                created_at=v["created_at"],
            )
            for i, v in enumerate(variants)
        ],
        total=len(variants),
    )


@router.get("/preview-prompt")
async def preview_prompt(
    rp_folder: str = Query(...),
    assembler: PromptAssembler = Depends(get_prompt_assembler),
):
    """Preview the assembled system prompt for an RP folder."""
    sections = assembler.get_sections(rp_folder)
    system_prompt = assembler.assemble_static_prompt(sections)
    return {
        "system_prompt": system_prompt,
        "token_estimate": len(system_prompt) // 4,
        "sections": list(sections.keys()),
    }
