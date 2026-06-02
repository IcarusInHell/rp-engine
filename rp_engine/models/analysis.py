"""Pydantic models for the Phase 5 analysis pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM Extraction Schema (mirrors response-analyzer.js JSON output)
# ---------------------------------------------------------------------------


class PlotThreadExtracted(BaseModel):
    """An LLM-extracted plot-thread development — name, status, and evidence."""
    thread_name: str = Field(alias="threadName", default="")
    status: str = ""
    development: str = ""
    evidence: str = ""

    model_config = {"populate_by_name": True}


class MemoryExtracted(BaseModel):
    """An LLM-extracted memory — description, significance, and characters."""
    description: str = ""
    significance: str = ""
    characters: list[str] = []
    timestamp: str | None = None


class KnowledgeBoundary(BaseModel):
    """An LLM-extracted knowledge-boundary event — who learned what, from where."""
    who: str = ""
    learned: str = ""
    from_: str = Field(alias="from", default="")
    type: str = ""
    evidence: str = ""

    model_config = {"populate_by_name": True}


class NewCharacterExtracted(BaseModel):
    """An LLM-extracted newly-introduced character."""
    name: str = ""
    role: str = ""
    first_appearance: str = Field(alias="firstAppearance", default="")

    model_config = {"populate_by_name": True}


class NewLocationExtracted(BaseModel):
    """An LLM-extracted newly-mentioned location."""
    name: str = ""
    description: str = ""
    first_mention: str = Field(alias="firstMention", default="")

    model_config = {"populate_by_name": True}


class NewConceptExtracted(BaseModel):
    """An LLM-extracted new concept with its significance."""
    name: str = ""
    significance: str = ""


class NewEntitiesExtracted(BaseModel):
    """LLM-extracted new entities — characters, locations, and concepts."""
    characters: list[NewCharacterExtracted] = []
    locations: list[NewLocationExtracted] = []
    concepts: list[NewConceptExtracted] = []


class RelationshipDynamic(BaseModel):
    """An LLM-extracted relationship change between characters."""
    characters: list[str] = []
    change_type: str = Field(alias="changeType", default="")
    evidence: str = ""

    model_config = {"populate_by_name": True}


class TrustMoment(BaseModel):
    """An LLM-extracted trust-affecting moment with a character."""
    type: str = ""
    action: str = ""
    with_character: str = Field(alias="withCharacter", default="")
    significance: str = ""

    model_config = {"populate_by_name": True}


class NPCInteractionExtracted(BaseModel):
    """An LLM-extracted NPC interaction — actions, emotional state, and trust moments."""
    npc_name: str = Field(alias="npcName", default="")
    appeared_in_exchange: list[int] = Field(alias="appearedInExchange", default=[])
    actions: list[str] = []
    emotional_state: str = Field(alias="emotionalState", default="")
    trust_moments: list[TrustMoment] = Field(alias="trustMoments", default=[])
    behavior_notes: str = Field(alias="behaviorNotes", default="")

    model_config = {"populate_by_name": True}


class CharacterStateExtracted(BaseModel):
    """An LLM-extracted character state — location, conditions, emotion."""
    location: str | None = None
    conditions: list[str] = []
    emotional_state: str = Field(alias="emotionalState", default="")

    model_config = {"populate_by_name": True}


class SceneContextExtracted(BaseModel):
    """An LLM-extracted scene context — location, time of day, mood."""
    location: str | None = None
    time_of_day: str = Field(alias="timeOfDay", default="")
    mood: str = ""

    model_config = {"populate_by_name": True}


class SignificantEventExtracted(BaseModel):
    """An LLM-extracted significant event with characters and significance."""
    event: str = ""
    characters: list[str] = []
    significance: str = "medium"


class StoryStateExtracted(BaseModel):
    """LLM-extracted story state — character states, scene context, and events."""
    characters: dict[str, CharacterStateExtracted] = {}
    scene_context: SceneContextExtracted = Field(
        alias="sceneContext", default_factory=SceneContextExtracted
    )
    significant_events: list[SignificantEventExtracted] = Field(
        alias="significantEvents", default=[]
    )

    model_config = {"populate_by_name": True}


class SceneSignificance(BaseModel):
    """LLM-scored scene significance — score, categories, and suggested card types."""
    score: int = 0
    categories: list[str] = []
    brief: str | None = None
    suggested_card_types: list[str] = Field(alias="suggestedCardTypes", default=[])
    in_story_timestamp: str | None = Field(alias="inStoryTimestamp", default=None)
    characters: list[str] = []

    model_config = {"populate_by_name": True}


class CustomStateChangeExtracted(BaseModel):
    """An LLM-extracted custom-state change — schema, entity, action, and value."""
    schema_name: str = Field(alias="schemaName", default="")
    entity: str = ""              # character name or "" for scene-level
    action: str = ""              # "set", "add", "remove", "subtract"
    value: str | int | float | list | None = None

    model_config = {"populate_by_name": True}


class AnalysisLLMResult(BaseModel):
    """Top-level model matching the LLM extraction JSON schema."""

    plot_threads: list[PlotThreadExtracted] = Field(alias="plotThreads", default=[])
    memories: list[MemoryExtracted] = []
    knowledge_boundaries: list[KnowledgeBoundary] = Field(
        alias="knowledgeBoundaries", default=[]
    )
    new_entities: NewEntitiesExtracted = Field(
        alias="newEntities", default_factory=NewEntitiesExtracted
    )
    relationship_dynamics: list[RelationshipDynamic] = Field(
        alias="relationshipDynamics", default=[]
    )
    npc_interactions: list[NPCInteractionExtracted] = Field(
        alias="npcInteractions", default=[]
    )
    story_state: StoryStateExtracted = Field(
        alias="storyState", default_factory=StoryStateExtracted
    )
    scene_significance: SceneSignificance = Field(
        alias="sceneSignificance", default_factory=SceneSignificance
    )
    custom_state_changes: list[CustomStateChangeExtracted] = Field(
        alias="customStateChanges", default=[]
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------


class AnalysisResult(BaseModel):
    """Summary of an analysis pipeline run — per-domain counts and any error."""
    exchange_id: int
    status: str = "completed"
    characters_updated: int = 0
    trust_changes: int = 0
    events_added: int = 0
    memories_added: int = 0
    card_gaps_added: int = 0
    thread_alerts: int = 0
    continuity_warnings: int = 0
    custom_state_changes: int = 0
    timestamp_advanced: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Card Gap Models
# ---------------------------------------------------------------------------


class CardGapItem(BaseModel):
    """A detected card gap — an entity seen repeatedly with no card, and the suggested type."""
    entity_name: str
    suggested_type: str | None = None
    seen_count: int = 1
    first_seen: str | None = None
    last_seen: str | None = None


class CardGapResponse(BaseModel):
    """All detected card gaps with a total."""
    gaps: list[CardGapItem] = []
    total: int = 0


# ---------------------------------------------------------------------------
# Card Suggest / Audit Models
# ---------------------------------------------------------------------------


class CardSuggestRequest(BaseModel):
    """Request to AI-author a card for an entity."""
    entity_name: str
    card_type: str
    rp_folder: str
    additional_context: str | None = None


class CardSuggestResponse(BaseModel):
    """An AI-authored card suggestion — generated markdown and the model used."""
    entity_name: str
    card_type: str
    markdown: str
    model_used: str | None = None


class CardAuditGapItem(BaseModel):
    """A card-gap item in a card-audit response — entity, suggested type, and mention count.

    CONSOLIDATION NOTE: near-identical to ``AuditGap`` (models/story_card.py) — same
    fields, only ``mention_count`` default differs. The whole card-suggest/audit family
    is duplicated across analysis.py ∥ story_card.py: CardSuggestRequest∥SuggestCardRequest,
    CardSuggestResponse∥SuggestCardResponse, CardAuditRequest∥AuditCardsRequest,
    CardAuditResponse∥AuditCardsResponse. rdeps both families (analysis router vs cards
    router) to pick the canonical set before merging — don't assume either is dead.
    """
    entity_name: str
    suggested_type: str | None = None
    mention_count: int = 0
    exchanges: list[int] = []


class CardAuditRequest(BaseModel):
    """Request to audit an RP for missing cards."""
    rp_folder: str
    mode: str = "quick"
    session_id: str | None = None


class CardAuditResponse(BaseModel):
    """Card-audit results — detected gaps and scan counts."""
    mode: str
    gaps: list[CardAuditGapItem] = []
    total_exchanges_scanned: int = 0
    total_gaps: int = 0


# ---------------------------------------------------------------------------
# Thread Models
# ---------------------------------------------------------------------------


class ThreadEvidence(BaseModel):
    """Evidence for a plot-thread counter change — matched keyword and before/after counter."""
    thread_id: str
    exchange_number: int
    keyword_matched: str | None = None
    chunk_text: str | None = None
    counter_before: int
    counter_after: int
    direction: str
    created_at: str = ""


class ThreadDetail(BaseModel):
    """A plot thread's full detail — counter, thresholds, consequences, and evidence."""
    thread_id: str
    name: str
    thread_type: str | None = None
    priority: str | None = None
    status: str = "active"
    keywords: list[str] = []
    current_counter: int = 0
    thresholds: dict[str, int] = {}
    consequences: dict[str, str] = {}
    related_characters: list[str] = []
    evidence: list[ThreadEvidence] = []


class ThreadListResponse(BaseModel):
    """All plot threads with a total."""
    threads: list[ThreadDetail] = []
    total: int = 0


class ThreadCounterUpdate(BaseModel):
    """Request to set a plot thread's counter."""
    counter: int


# ---------------------------------------------------------------------------
# Timestamp Models
# ---------------------------------------------------------------------------


class TimeAdvanceRequest(BaseModel):
    """Request to advance the in-story clock from response text or an explicit override."""
    response_text: str | None = None
    override_minutes: int | None = None


class TimeAdvanceResponse(BaseModel):
    """Result of advancing the in-story clock — new timestamp and elapsed minutes."""
    previous_timestamp: str | None = None
    new_timestamp: str | None = None
    elapsed_minutes: int = 0
    activities_detected: list[str] = []
    modifier_used: str | None = None


# ---------------------------------------------------------------------------
# Analysis Manifest Models (undo / redo / preview)
# ---------------------------------------------------------------------------


class ManifestEntryResponse(BaseModel):
    """One row written by an analysis run — its table, id, and operation."""
    target_table: str
    target_id: int
    operation: str = "insert"


class ManifestResponse(BaseModel):
    """An analysis run's manifest — the rows it wrote, for undo/preview."""
    id: int
    exchange_number: int
    exchange_id: int
    session_id: str | None = None
    status: str
    model_used: str | None = None
    raw_response: str | None = None
    created_at: str
    undone_at: str | None = None
    entries: list[ManifestEntryResponse] = []
    entry_counts: dict[str, int] = {}


class ManifestListResponse(BaseModel):
    """All analysis manifests with a total."""
    manifests: list[ManifestResponse] = []
    total: int = 0


class AnalysisUndoResponse(BaseModel):
    """Result of undoing an analysis run — rows removed and any cascade re-analysis."""
    exchange_number: int
    manifest_id: int
    status: str  # 'undone' | 'not_found' | 'already_undone'
    entries_removed: int = 0
    tables_affected: dict[str, int] = {}
    cascade_reanalyzed: list[int] = []


class AnalysisPreviewResponse(BaseModel):
    """Preview of what undoing an analysis run would remove."""
    exchange_number: int
    manifest_id: int
    entries_count: int = 0
    tables_affected: dict[str, int] = {}
    cascade_exchanges: list[int] = []
