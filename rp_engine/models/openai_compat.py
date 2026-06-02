"""Pydantic models for OpenAI-compatible endpoint."""

from __future__ import annotations

from pydantic import BaseModel


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request — messages plus sampling params."""
    model: str = "rp-engine"
    messages: list[dict]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class ChatCompletionChoice(BaseModel):
    """One choice in an OpenAI-compatible completion response."""
    index: int = 0
    message: dict  # {"role": "assistant", "content": "..."}
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response — choices plus token usage."""
    id: str
    object: str = "chat.completion"
    created: int
    model: str = "rp-engine"
    choices: list[ChatCompletionChoice]
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class ModelInfo(BaseModel):
    """OpenAI-compatible model descriptor."""
    id: str = "rp-engine"
    object: str = "model"
    created: int = 0
    owned_by: str = "rp-engine"


class ModelListResponse(BaseModel):
    """OpenAI-compatible /v1/models listing."""
    object: str = "list"
    data: list[ModelInfo]
