"""Pydantic models for exchange (chat message) storage."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, field_validator, model_validator


def validate_response_content(v: str) -> str:
    """Reject responses containing meta content that should be stripped before saving."""
    if re.search(r"<thinking>", v, re.IGNORECASE):
        raise ValueError("Response contains <thinking> tags. Strip before saving.")
    if re.search(r'\{"tool_calls":', v):
        raise ValueError("Response contains tool call blocks. Strip before saving.")
    if re.search(r"<system-reminder>", v, re.IGNORECASE):
        raise ValueError("Response contains system instructions. Strip before saving.")
    return v


class ExchangeSave(BaseModel):
    user_message: str
    assistant_response: str
    exchange_number: int | None = None
    idempotency_key: str | None = None
    parent_exchange_number: int | None = None
    session_id: str | None = None
    in_story_timestamp: str | None = None
    location: str | None = None
    metadata: dict | None = None

    @field_validator("assistant_response")
    @classmethod
    def validate_no_meta_content(cls, v: str) -> str:
        return validate_response_content(v)


class ExchangeUpdate(BaseModel):
    """Request body for editing an exchange's user message and/or assistant response."""
    user_message: str | None = None
    assistant_response: str | None = None
    re_embed: bool = True
    re_analyze: bool = False

    @field_validator("assistant_response")
    @classmethod
    def validate_no_meta_content(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_response_content(v)
        return v

    @model_validator(mode="after")
    def at_least_one_field(self) -> ExchangeUpdate:
        if self.user_message is None and self.assistant_response is None:
            raise ValueError("At least one of user_message or assistant_response must be set")
        return self


class ExchangeResponse(BaseModel):
    id: int
    exchange_number: int
    session_id: str
    created_at: str
    analysis_status: str = "pending"
    rewound_count: int | None = None
    new_branch: str | None = None
    idempotent_hit: bool | None = None


class DeleteResponse(BaseModel):
    """Standard response for delete operations."""

    deleted: bool = True


class ExchangeDetail(BaseModel):
    id: int
    exchange_number: int
    session_id: str
    branch: str | None = None
    user_message: str
    assistant_response: str
    in_story_timestamp: str | None = None
    location: str | None = None
    npcs_involved: list[str] | None = None
    message_mode: str = "rp"
    analysis_status: str = "pending"
    created_at: str
    metadata: dict | None = None
    has_variants: bool = False
    variant_count: int = 0
    continue_count: int = 0
    is_bookmarked: bool = False
    bookmark_name: str | None = None
    has_annotations: bool = False
    annotation_count: int = 0


class ExchangeListResponse(BaseModel):
    exchanges: list[ExchangeDetail]
    total_count: int


# --- Search ---

class SearchMode(StrEnum):
    semantic = "semantic"
    keyword = "keyword"
    hybrid = "hybrid"


class ExchangeSearchHit(BaseModel):
    exchange_number: int
    exchange_id: int
    user_message_snippet: str
    assistant_response_snippet: str
    relevance_score: float
    timestamp: str
    session_id: str | None = None
    npcs_mentioned: list[str] | None = None
    is_bookmarked: bool = False
    bookmark_name: str | None = None
    annotation_count: int = 0


class ExchangeSearchResponse(BaseModel):
    query: str
    mode: str
    total_results: int
    results: list[ExchangeSearchHit]


# --- Bookmarks ---

class BookmarkCreate(BaseModel):
    name: str | None = None
    note: str | None = None
    color: str = "default"


class BookmarkUpdate(BaseModel):
    name: str | None = None
    note: str | None = None
    color: str | None = None


class BookmarkResponse(BaseModel):
    id: int
    exchange_number: int
    exchange_id: int
    name: str
    note: str | None = None
    color: str = "default"
    created_at: str


class BookmarkListResponse(BaseModel):
    bookmarks: list[BookmarkResponse]
    total_count: int


# --- Annotations ---

class AnnotationCreate(BaseModel):
    content: str
    annotation_type: str = "note"
    include_in_context: bool = False


class AnnotationUpdate(BaseModel):
    content: str | None = None
    annotation_type: str | None = None
    include_in_context: bool | None = None


class AnnotationResponse(BaseModel):
    id: int
    exchange_number: int
    exchange_id: int
    content: str
    annotation_type: str = "note"
    include_in_context: bool = False
    resolved: bool = False
    created_at: str
    updated_at: str | None = None


class AnnotationListResponse(BaseModel):
    annotations: list[AnnotationResponse]
    total_count: int
