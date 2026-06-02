"""Pydantic models for the context engine pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rp_engine.models.npc import NPCReaction
from rp_engine.models.rp import GuidelinesResponse

# ---------------------------------------------------------------------------
# API Request / Response
# ---------------------------------------------------------------------------


class ContextRequest(BaseModel):
    """Request to build context for a turn — message, prior response, POV, NPC-reaction toggle."""
    user_message: str
    last_response: str | None = None
    include_npc_reactions: bool = True
    pov_character: str | None = None


class ContextDocument(BaseModel):
    """A story card injected into context — match source, relevance score, and tiered injection depth."""
    name: str
    card_type: str
    file_path: str
    source: Literal["keyword", "semantic", "graph", "trigger", "always_load", "attached"]
    relevance_score: float
    content: str | None = None
    summary: str | None = None
    status: Literal["new", "updated"]
    # Relevance-based injection depth (Phase 3 tiered context). "full" = complete
    # body, "brief" = compact summary, "reference" = one-line. Assigned in the
    # context engine from the relevance score; the prompt assembler formats each
    # tier differently. Defaults to "full" so directly-constructed/attached docs
    # (e.g. chat_manager's attach_card_ids path) keep their complete body.
    injection_tier: Literal["full", "brief", "reference"] = "full"


class ContextReference(BaseModel):
    """A card already loaded in a prior turn, referenced rather than re-sent."""
    name: str
    card_type: str
    status: Literal["already_loaded"] = "already_loaded"
    sent_at_turn: int


class NPCBrief(BaseModel):
    """A deterministic behavioral brief for an active NPC — trust, state, and scene signals."""
    character: str
    card_id: str | None = None  # story_cards.id — used to dedup the NPC's card from documents
    importance: str | None = None
    archetype: str | None = None
    secondary_archetype: str | None = None
    behavioral_modifiers: list[str] = []
    trust_score: int = 0
    trust_stage: str | None = None
    emotional_state: str | None = None
    conditions: list[str] = []
    behavioral_direction: str = ""
    scene_signals: list[str] = []


class FlaggedNPC(BaseModel):
    """An NPC flagged as relevant but not given a full brief, with the reason."""
    character: str
    importance: str | None = None
    reason: str


class ResolvedKnowledge(BaseModel):
    """A character's resolved knowledge entry: what they believe about a topic.

    Produced by ``KnowledgeResolver`` from a character's ``knowledge_refs`` →
    knowledge card. ``believes`` is what the LLM should portray the character as
    thinking. ``reality`` (the actual truth) is populated **only** when the
    character's ref sets ``knows_reality: true`` — otherwise it stays ``None`` to
    prevent the LLM leaking truths the character doesn't know (knowledge bleed).
    """
    card_id: str
    topic: str | None = None
    believes: list[str] = []
    reality: list[str] | None = None  # only set when knows_reality is true
    confidence: str | None = None
    source: str | None = None
    knows_reality: bool = False


class SceneState(BaseModel):
    """The current scene — location, time of day, mood, and in-story timestamp."""
    location: str | None = None
    time_of_day: str | None = None
    mood: str | None = None
    in_story_timestamp: str | None = None


class CharacterState(BaseModel):
    """A character's minimal live state — location, conditions, emotion."""
    location: str | None = None
    conditions: list[str] = []
    emotional_state: str | None = None


class CustomStateBlock(BaseModel):
    """A rendered custom-state block ready for prompt injection in its display format."""
    schema_name: str
    category: str
    display_format: str  # inject_as: "stat_block", "inventory_list", "note"
    content: str
    belongs_to: str | None = None  # character name or None (scene-level)


class ThreadAlert(BaseModel):
    """A plot-thread alert — counter vs threshold, escalation level, and consequence."""
    thread_id: str
    name: str
    level: Literal["gentle", "moderate", "strong"]
    counter: int
    threshold: int
    consequence: str
    evidence_snippets: list[str] = []


class TriggeredNote(BaseModel):
    """A fired situational trigger's injected note or state alert."""
    trigger_id: str
    trigger_name: str
    inject_type: Literal["context_note", "state_alert"]
    content: str
    priority: int = 0
    signals_matched: list[str] = []


class LorebookEntryHit(BaseModel):
    """A lorebook entry that matched at Stage 2.5 and survived the budget fill.

    Mirrors ``TriggeredNote`` (fired triggers → ``# Triggered Notes``): matched
    lorebook entries → ``# World Info``. Matching reuses ``TriggerEvaluator``.
    """
    entry_id: int
    name: str
    content: str
    scope: Literal["rp", "global"]
    budget_weight: int = 1
    depth: int | None = None
    matched_conditions: list[str] = []
    stem_only: bool = False  # fired only due to stemmed matching (observability)


class CardGap(BaseModel):
    """A repeatedly-seen entity with no card yet — a suggested-card gap."""
    entity_name: str
    seen_count: int
    suggested_type: str | None = None


class StalenessWarning(BaseModel):
    """A warning that an exchange's async analysis failed, leaving stale fields."""
    type: str = "stale_analysis"
    exchange: int
    failed_at: str
    stale_fields: list[str] = []


class WritingConstraints(BaseModel):
    """Assembled writing-style constraints with the patterns included and token count."""
    text: str
    patterns_included: list[str] = []
    task_context: str = ""
    token_count: int = 0


class PastExchangeHit(BaseModel):
    """A semantically-retrieved past exchange — speaker, text, and relevance score."""
    exchange_number: int
    session_id: str | None = None
    speaker: str
    text: str
    score: float
    in_story_timestamp: str | None = None


class ExtractedMemoryHit(BaseModel):
    """A retrieved extracted memory — description, significance, and characters."""
    description: str
    significance: str | None = None
    characters: list[str] = []
    exchange_number: int | None = None
    in_story_timestamp: str | None = None


class AutoSaveResult(BaseModel):
    """Identifiers for an exchange saved by the auto-save path."""
    exchange_id: int
    exchange_number: int
    session_id: str


class ContextResponse(BaseModel):
    """The full assembled context for a turn — documents, NPC briefs/reactions, state, alerts, and retrieval hits."""
    current_exchange: int
    documents: list[ContextDocument] = []
    references: list[ContextReference] = []
    npc_briefs: list[NPCBrief] = []
    npc_reactions: list[NPCReaction] = []
    flagged_npcs: list[FlaggedNPC] = []
    knowledge_boundaries: dict[str, list[ResolvedKnowledge]] = {}  # keyed by character display name
    guidelines: GuidelinesResponse | None = None
    scene_state: SceneState = SceneState()
    character_states: dict[str, CharacterState] = {}
    thread_alerts: list[ThreadAlert] = []
    triggered_notes: list[TriggeredNote] = []
    lorebook_entries: list[LorebookEntryHit] = []
    card_gaps: list[CardGap] = []
    past_exchanges: list[PastExchangeHit] = []
    extracted_memories: list[ExtractedMemoryHit] = []
    custom_state: list[CustomStateBlock] = []
    warnings: list[StalenessWarning] = []
    writing_constraints: WritingConstraints | None = None
    auto_saved: AutoSaveResult | None = None


# ---------------------------------------------------------------------------
# Internal Types (used between services, not in API response)
# ---------------------------------------------------------------------------


class MatchedEntity(BaseModel):
    """An entity matched in the user message — by name, alias, or keyword, with a score."""
    entity_id: str
    match_source: Literal["alias", "keyword", "name"]
    match_term: str
    score: float  # name=1.0, alias=0.8, keyword=0.5


class DetectedNPC(BaseModel):
    """An NPC detected as active or referenced in the scene, with the detection reason."""
    entity_id: str
    name: str
    detection_reason: str


class ExtractionResult(BaseModel):
    """Entity-extraction output — matched entities, active/referenced NPCs, and locations."""
    matched_entities: list[MatchedEntity] = []
    active_npcs: list[DetectedNPC] = []
    referenced_npcs: list[DetectedNPC] = []
    detected_locations: list[str] = []


# ---------------------------------------------------------------------------
# Graph resolution request (for /api/context/resolve)
# ---------------------------------------------------------------------------


class ResolveRequest(BaseModel):
    """Request to resolve the relationship graph from a scene — keywords, hops, and result cap."""
    scene_description: str
    keywords: list[str] = []
    max_hops: int = 2
    max_results: int = 15
