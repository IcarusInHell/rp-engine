"""Pydantic models for the chat endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class SceneOverride(BaseModel):
    """Optional per-turn override of scene location and mood."""
    location: str | None = None
    mood: str | None = None


class ChatRequest(BaseModel):
    """A chat turn — user message, mode (rp/ooc/direction), streaming, and optional card attachments."""
    user_message: str
    stream: bool = False
    message_mode: Literal["rp", "ooc", "direction"] = "rp"
    ooc: bool = False  # deprecated — use message_mode="ooc" instead
    attach_card_ids: list[str] = []
    scene_override: SceneOverride | None = None

    @model_validator(mode="after")
    def _compat_ooc_flag(self) -> ChatRequest:
        """Map deprecated ooc=True to message_mode='ooc' for backward compat."""
        if self.ooc and self.message_mode == "rp":
            self.message_mode = "ooc"
        return self


class ChatResponse(BaseModel):
    """A completed chat turn — response text plus exchange/session identifiers."""
    response: str
    exchange_id: int
    exchange_number: int
    session_id: str
    context_summary: dict | None = None
    # True for OOC turns (not persisted as an exchange). Mirrors the streaming
    # done-event's ooc=True so both paths are detectable by the same flag.
    ooc: bool = False


class ChatStreamEvent(BaseModel):
    """One server-sent event in a streamed chat turn — token, done, or error."""
    type: str  # "token", "done", "error"
    content: str | None = None
    exchange_id: int | None = None
    exchange_number: int | None = None


# --- Regenerate / Swipe ---

class RegenerateRequest(BaseModel):
    """Request to regenerate an exchange's assistant response as a new variant."""
    exchange_number: int | None = None  # defaults to latest
    temperature: float | None = None
    model: str | None = None
    stream: bool = False


class RegenerateResponse(BaseModel):
    """A regenerated variant — response plus variant index and total count."""
    response: str
    exchange_id: int
    exchange_number: int
    session_id: str
    variant_id: int
    variant_index: int
    total_variants: int


class SwipeRequest(BaseModel):
    """Request to switch an exchange to a different existing variant."""
    exchange_number: int
    variant_index: int


class SwipeResponse(BaseModel):
    """Result of a swipe — the now-active variant and its response."""
    exchange_number: int
    active_variant: int
    total_variants: int
    response: str


class VariantInfo(BaseModel):
    """Metadata for one response variant — model, temperature, source, continue count."""
    id: int
    variant_index: int
    is_active: bool
    model_used: str | None = None
    temperature: float | None = None
    source: str = "llm"
    continue_count: int = 0
    created_at: str


class VariantsResponse(BaseModel):
    """All response variants for an exchange."""
    exchange_number: int
    exchange_id: int
    variants: list[VariantInfo]
    total: int


# --- Continue ---

class ContinueRequest(BaseModel):
    """Request to extend an exchange's existing response with more text."""
    exchange_number: int | None = None  # defaults to latest
    max_tokens: int | None = None
    stream: bool = False


class ContinueResponse(BaseModel):
    """A continued response — the new continuation plus the full combined text."""
    continuation: str
    full_response: str
    exchange_id: int
    exchange_number: int
    session_id: str
    continue_count: int
