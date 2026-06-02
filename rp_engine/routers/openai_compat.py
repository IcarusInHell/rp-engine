"""OpenAI-compatible endpoint — /v1/chat/completions, /v1/models."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.responses import StreamingResponse

from rp_engine.config import get_config
from rp_engine.database import Database
from rp_engine.dependencies import get_chat_manager, get_db, resolve_active_session
from rp_engine.models.openai_compat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelInfo,
    ModelListResponse,
)
from rp_engine.services.chat_manager import ChatManager

logger = logging.getLogger(__name__)


async def _verify_api_key(request: Request):
    """Verify Bearer token if server.api_key is configured."""
    config = get_config()
    if not config.server.api_key:
        return
    # Skip auth for localhost
    if request.client and request.client.host in ("127.0.0.1", "::1"):
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {config.server.api_key}":
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
        )


router = APIRouter(prefix="/v1", tags=["openai-compat"], dependencies=[Depends(_verify_api_key)])


def _extract_last_user_message(messages: list[dict]) -> str:
    """Extract the last user message from OpenAI-format message list."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            # Handle content arrays (multimodal) — extract text parts
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
    raise HTTPException(
        400,
        detail={"error": {"message": "No user message found", "type": "invalid_request_error"}},
    )


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
    chat_manager: ChatManager = Depends(get_chat_manager),
):
    """OpenAI-compatible chat completion — maps the last user message into an RP turn (streaming or single response)."""
    user_message = _extract_last_user_message(body.messages)
    rp_folder, branch, session_id = await resolve_active_session(db, rp_folder, branch)
    created = int(time.time())

    if body.stream:
        return StreamingResponse(
            _stream_response(
                chat_manager, user_message, rp_folder, branch, session_id, created,
                temperature=body.temperature, max_tokens=body.max_tokens,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = await chat_manager.chat(
        user_message=user_message,
        rp_folder=rp_folder,
        branch=branch,
        session_id=session_id,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )

    return ChatCompletionResponse(
        id=f"chatcmpl-rpe-{result.exchange_id}",
        created=created,
        choices=[ChatCompletionChoice(
            message={"role": "assistant", "content": result.response},
        )],
    )


async def _stream_response(
    chat_manager: ChatManager,
    user_message: str,
    rp_folder: str,
    branch: str,
    session_id: str,
    created: int,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
):
    """Translate ChatManager SSE events to OpenAI delta format."""
    completion_id = "chatcmpl-rpe-0"

    async for raw_event in chat_manager.chat_stream(
        user_message=user_message,
        rp_folder=rp_folder,
        branch=branch,
        session_id=session_id,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        if not raw_event.startswith("data: "):
            continue
        try:
            event = json.loads(raw_event[6:].strip())
        except (ValueError, TypeError):
            continue

        if event.get("type") == "token":
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": "rp-engine",
                "choices": [{"index": 0, "delta": {"content": event["content"]}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"

        elif event.get("type") == "error":
            error_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": "rp-engine",
                "choices": [{"index": 0, "delta": {"content": f"\n\n[Error: {event.get('content', 'Unknown error')}]"}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        elif event.get("type") == "done":
            completion_id = f"chatcmpl-rpe-{event.get('exchange_id', 0)}"
            final = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": "rp-engine",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"


@router.get("/models")
async def list_models():
    """List available models (OpenAI-compatible) — advertises the single `rp-engine` model."""
    return ModelListResponse(
        data=[ModelInfo(id="rp-engine", created=int(time.time()))],
    )
