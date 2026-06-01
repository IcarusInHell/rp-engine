"""Pydantic models for the Phase 5 analysis pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM Extraction Schema (mirrors response-analyzer.js JSON output)
# ---------------------------------------------------------------------------


class PlotThreadExtracted(BaseModel):
    thread_name: str = Field(alias="threadName", default="")
    status: str = ""
    development: str = ""
    evidence: str = ""

    model_config = {"populate_by_name": True}


class MemoryExtracted(BaseModel):
    description: str = ""
    significance: str = ""
    characters: list[str] = []
    timestamp: str | None = None


class KnowledgeBoundary(BaseModel):
    who: str = ""
    learned: str = ""
    from_: str = Field(alias="from", default="")
    type: str = ""
    evidence: str = ""

    model_config = {"populate_by_name": True}


class NewCharacterExtracted(BaseModel):
    name: str = ""
    role: str = ""
    first_appearance: str = Field(alias="firstAppearance", default="")

    model_config = {"populate_by_name": True}


class NewLocationExtracted(BaseModel):
    name: str = ""
    description: str = ""
    first_mention: str = Field(alias="firstMention", default="")

    model_config = {"populate_by_name": True}


class NewConceptExtracted(BaseModel):
    name: str = ""
    significance: str = ""


class NewEntitiesExtracted(BaseModel):
    characters: list[NewCharacterExtracted] = []
    locations: list[NewLocationExtracted] = []
    concepts: list[NewConceptExtracted] = []


class RelationshipDynamic(BaseModel):
    characters: list[str] = []
    change_type: str = Field(alias="changeType", default="")
    evidence: str = ""

    model_config = {"populate_by_name": True}


class TrustMoment(BaseModel):
    type: str = ""
    action: str = ""
    with_character: str = Field(alias="withCharacter", default="")
    significance: str = ""

    model_config = {"populate_by_name": True}


class NPCInteractionExtracted(BaseModel):
    npc_name: str = Field(alias="npcName", default="")
    appeared_in_exchange: list[int] = Field(alias="appearedInExchange", default=[])
    actions: list[str] = []
    emotional_state: str = Field(alias="emotionalState", default="")
    trust_moments: list[TrustMoment] = Field(alias="trustMoments", default=[])
    behavior_notes: str = Field(alias="behaviorNotes", default="")

    model_config = {"populate_by_name": True}


class CharacterStateExtracted(BaseModel):
    location: str | None = None
    conditions: list[str] = []
    emotional_state: str = Field(alias="emotionalState", default="")

    model_config = {"populate_by_name": True}


class SceneContextExtracted(BaseModel):
    location: str | None = None
    time_of_day: str = Field(alias="timeOfDay", default="")
    mood: str = ""

    model_config = {"populate_by_name": True}


class SignificantEventExtracted(BaseModel):
    event: str = ""
    characters: list[str] = []
    significance: str = "medium"


class StoryStateExtracted(BaseModel):
    characters: dict[str, CharacterStateExtracted] = {}
    scene_context: SceneContextExtracted = Field(
        alias="sceneContext", default_factory=SceneContextExtracted
    )
    significant_events: list[SignificantEventExtracted] = Field(
        alias="significantEvents", default=[]
    )

    model_config = {"populate_by_name": True}


class SceneSignificance(BaseModel):
    score: int = 0
    categories: list[str] = []
    brief: str | None = None
    suggested_card_types: list[str] = Field(alias="suggestedCardTypes", default=[])
    in_story_timestamp: str | None = Field(alias="inStoryTimestamp", default=None)
    characters: list[str] = []

    model_config = {"populate_by_name": True}


class CustomStateChangeExtracted(BaseModel):
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
    entity_name: str
    suggested_type: str | None = None
    seen_count: int = 1
    first_seen: str | None = None
    last_seen: str | None = None


class CardGapResponse(BaseModel):
    gaps: list[CardGapItem] = []
    total: int = 0


# ---------------------------------------------------------------------------
# Card Suggest / Audit Models
# ---------------------------------------------------------------------------


class CardSuggestRequest(BaseModel):
    entity_name: str
    card_type: str
    rp_folder: str
    additional_context: str | None = None


class CardSuggestResponse(BaseModel):
    entity_name: str
    card_type: str
    markdown: str
    model_used: str | None = None


class CardAuditGapItem(BaseModel):
    entity_name: str
    suggested_type: str | None = None
    mention_count: int = 0
    exchanges: list[int] = []


class CardAuditRequest(BaseModel):
    rp_folder: str
    mode: str = "quick"
    session_id: str | None = None


class CardAuditResponse(BaseModel):
    mode: str
    gaps: list[CardAuditGapItem] = []
    total_exchanges_scanned: int = 0
    total_gaps: int = 0


# ---------------------------------------------------------------------------
# Thread Models
# ---------------------------------------------------------------------------


class ThreadEvidence(BaseModel):
    thread_id: str
    exchange_number: int
    keyword_matched: str | None = None
    chunk_text: str | None = None
    counter_before: int
    counter_after: int
    direction: str
    created_at: str = ""


class ThreadDetail(BaseModel):
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
    threads: list[ThreadDetail] = []
    total: int = 0


class ThreadCounterUpdate(BaseModel):
    counter: int


# ---------------------------------------------------------------------------
# Timestamp Models
# ---------------------------------------------------------------------------


class TimeAdvanceRequest(BaseModel):
    response_text: str | None = None
    override_minutes: int | None = None


class TimeAdvanceResponse(BaseModel):
    previous_timestamp: str | None = None
    new_timestamp: str | None = None
    elapsed_minutes: int = 0
    activities_detected: list[str] = []
    modifier_used: str | None = None


# ---------------------------------------------------------------------------
# Analysis Manifest Models (undo / redo / preview)
# ---------------------------------------------------------------------------


class ManifestEntryResponse(BaseModel):
    target_table: str
    target_id: int
    operation: str = "insert"


class ManifestResponse(BaseModel):
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
    manifests: list[ManifestResponse] = []
    total: int = 0


class AnalysisUndoResponse(BaseModel):
    exchange_number: int
    manifest_id: int
    status: str  # 'undone' | 'not_found' | 'already_undone'
    entries_removed: int = 0
    tables_affected: dict[str, int] = {}
    cascade_reanalyzed: list[int] = []


class AnalysisPreviewResponse(BaseModel):
    exchange_number: int
    manifest_id: int
    entries_count: int = 0
    tables_affected: dict[str, int] = {}
    cascade_exchanges: list[int] = []
