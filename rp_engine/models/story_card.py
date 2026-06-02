"""Pydantic models for story card operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StoryCardSummary(BaseModel):
    """A story card list-row — identity, type, importance, and connection count."""
    model_config = ConfigDict(from_attributes=True)

    id: str  # DB primary key ("rp_folder:normalized_name"); used by chat attach
    name: str
    card_type: str
    importance: str | None = None
    file_path: str
    summary: str | None = None
    aliases: list[str] = []
    tags: list[str] = []
    connection_count: int = 0


class EntityConnection(BaseModel):
    """A connection from one card to another entity — type, field, and role."""
    to_entity: str
    connection_type: str
    field: str | None = None
    role: str | None = None


class StoryCardDetail(BaseModel):
    """A full story card — frontmatter, content, body, and resolved connections."""
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
    """Request to create a story card from frontmatter and content."""
    name: str
    frontmatter: dict[str, Any] = {}
    content: str = ""


class StoryCardUpdate(BaseModel):
    """Partial update to a story card's frontmatter and/or content."""
    frontmatter: dict[str, Any] | None = None
    content: str | None = None


class CardListResponse(BaseModel):
    """A page of story-card summaries with a total."""
    cards: list[StoryCardSummary]
    total: int


class ReindexResponse(BaseModel):
    """Counts from a card reindex — entities, connections, aliases, keywords, chunks."""
    entities: int
    connections: int
    aliases: int
    keywords: int
    chunks: int = 0
    trust_baselines_seeded: int = 0  # Bug E: surfaced in both single- and multi-folder paths
    duration_ms: float


class SuggestCardRequest(BaseModel):
    """Request to AI-author a card, with related entities and branch scope."""
    entity_name: str
    card_type: str = "character"
    rp_folder: str
    branch: str = "main"
    additional_context: str = ""
    related_entities: list[str] = []


class SuggestCardResponse(BaseModel):
    """An AI-authored card — generated markdown and the model used."""
    entity_name: str
    card_type: str
    markdown: str
    model_used: str


class AuditCardsRequest(BaseModel):
    """Request to audit a branch for missing cards."""
    rp_folder: str
    mode: str = "quick"
    session_id: str | None = None
    branch: str = "main"  # Bug B: scope the no-session "50 most recent" scan to one branch


class AuditGap(BaseModel):
    """A card-gap item in a card-audit response — entity, suggested type, and mention count.

    CONSOLIDATION NOTE: near-identical to ``CardAuditGapItem`` (models/analysis.py);
    see that class's note for the full duplicated card-suggest/audit family across
    analysis.py ∥ story_card.py.
    """
    entity_name: str
    suggested_type: str | None = None
    mention_count: int
    exchanges: list[int] = []


class AuditCardsResponse(BaseModel):
    """Card-audit results — detected gaps and scan counts."""
    mode: str
    gaps: list[AuditGap]
    total_exchanges_scanned: int
    total_gaps: int


class GapExchangeRecord(BaseModel):
    """One exchange where a gap entity was mentioned, with mention type."""
    exchange_number: int
    chunk_text: str | None = None
    mention_type: str = "peripheral"


class SceneEvidence(BaseModel):
    """A scene's worth of gap-entity mentions — exchange span and records."""
    start: int
    end: int
    exchange_count: int
    exchanges: list[GapExchangeRecord]


class GapEvidenceResponse(BaseModel):
    """Full evidence for a card gap — mention counts grouped into scenes."""
    entity_name: str
    rp_folder: str
    total_mentions: int
    primary_mentions: int
    scenes: list[SceneEvidence]


class GenerateCardNameRequest(BaseModel):
    """Request to generate candidate card names from hints."""
    card_type: str
    hints: str = ""
    count: int = 5


class GenerateCardNameResponse(BaseModel):
    """Generated card-name suggestions for a card type."""
    suggestions: list[str]
    card_type: str


class CardValidateRequest(BaseModel):
    """Request to validate frontmatter against a card type's schema."""
    card_type: str
    frontmatter: dict[str, Any]


class DeleteCardResponse(BaseModel):
    """Result of deleting a card — whether the .md file was removed."""
    name: str
    card_type: str
    file_deleted: bool


class RelationshipSyncEntry(BaseModel):
    """A reciprocal relationship added to a partner card during sync."""
    card_name: str
    card_type: str
    relationship_added: dict[str, Any]


class RelationshipSyncResult(BaseModel):
    """Result of reciprocal-relationship sync — updated cards and errors."""
    source_card: str
    updated_cards: list[RelationshipSyncEntry] = []
    errors: list[str] = []
