"""Pydantic models for story card operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StoryCardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    card_type: str
    importance: str | None = None
    file_path: str
    summary: str | None = None
    aliases: list[str] = []
    tags: list[str] = []
    connection_count: int = 0


class EntityConnection(BaseModel):
    to_entity: str
    connection_type: str
    field: str | None = None
    role: str | None = None


class StoryCardDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    card_type: str
    file_path: str
    importance: str | None = None
    frontmatter: dict[str, Any]
    content: str
    body: str = ""
    connections: list[EntityConnection] = []


class StoryCardCreate(BaseModel):
    name: str
    frontmatter: dict[str, Any] = {}
    content: str = ""


class StoryCardUpdate(BaseModel):
    frontmatter: dict[str, Any] | None = None
    content: str | None = None


class CardListResponse(BaseModel):
    cards: list[StoryCardSummary]
    total: int


class ReindexResponse(BaseModel):
    entities: int
    connections: int
    aliases: int
    keywords: int
    chunks: int = 0
    duration_ms: float


class SuggestCardRequest(BaseModel):
    entity_name: str
    card_type: str = "character"
    rp_folder: str
    branch: str = "main"
    additional_context: str = ""
    related_entities: list[str] = []


class SuggestCardResponse(BaseModel):
    entity_name: str
    card_type: str
    markdown: str
    model_used: str


class AuditCardsRequest(BaseModel):
    rp_folder: str
    mode: str = "quick"
    session_id: str | None = None


class AuditGap(BaseModel):
    entity_name: str
    suggested_type: str | None = None
    mention_count: int
    exchanges: list[int] = []


class AuditCardsResponse(BaseModel):
    mode: str
    gaps: list[AuditGap]
    total_exchanges_scanned: int
    total_gaps: int


class GapExchangeRecord(BaseModel):
    exchange_number: int
    chunk_text: str | None = None
    mention_type: str = "peripheral"


class SceneEvidence(BaseModel):
    start: int
    end: int
    exchange_count: int
    exchanges: list[GapExchangeRecord]


class GapEvidenceResponse(BaseModel):
    entity_name: str
    rp_folder: str
    total_mentions: int
    primary_mentions: int
    scenes: list[SceneEvidence]


class GenerateCardNameRequest(BaseModel):
    card_type: str
    hints: str = ""
    count: int = 5


class GenerateCardNameResponse(BaseModel):
    suggestions: list[str]
    card_type: str


class CardValidateRequest(BaseModel):
    card_type: str
    frontmatter: dict[str, Any]


class DeleteCardResponse(BaseModel):
    name: str
    card_type: str
    file_deleted: bool


class RelationshipSyncEntry(BaseModel):
    card_name: str
    card_type: str
    relationship_added: dict[str, Any]


class RelationshipSyncResult(BaseModel):
    source_card: str
    updated_cards: list[RelationshipSyncEntry] = []
    errors: list[str] = []
