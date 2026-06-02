"""Per-type Pydantic models for story card frontmatter validation.

Every card type has a model with typed fields and ``extra="allow"`` so
unknown fields don't break validation (legacy cards may have extra keys).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from rp_engine.models.enums import (
    ArcCategory,
    Archetype,
    ArcPhase,
    ArcPriority,
    ArcStatus,
    BehavioralModifier,
    ChapterStatus,
    CharacterRole,
    Confidence,
    CooperationDefault,
    Difficulty,
    DiscoveryRisk,
    EmotionalTone,
    EscalationStyle,
    Hierarchy,
    Importance,
    Influence,
    ItemCategory,
    ItemRarity,
    ItemStatus,
    LeadershipType,
    LocationAccess,
    LocationCategory,
    LocationStatus,
    LoreCategory,
    LoreImportance,
    LoreScope,
    MemoryCategory,
    MemoryImportance,
    OrgCategory,
    OrgSize,
    OrgStatus,
    PublicStance,
    Recruitment,
    RevelationPlanned,
    Scope,
    SecretCategory,
    SecretSignificance,
    SecretStatus,
    ThreadPhase,
    ThreadPriority,
    ThreadStatus,
    TrackingMode,
)

# ---------------------------------------------------------------------------
# Character (covers both PCs and NPCs)
# ---------------------------------------------------------------------------

class CharacterFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["character"] = "character"
    card_id: str | None = None
    name: str
    aliases: list[str] = []
    rp: str | None = None

    # Basic
    is_player_character: bool = False
    age: str | int | None = None
    gender: str | None = None
    role: CharacterRole | None = None
    species: str | None = None
    occupation: str | None = None
    status: str | None = "Active"

    # Importance (NPC-relevant)
    importance: Importance | None = None

    # Knowledge system
    knowledge_boundaries: dict | None = None  # legacy free-form dict (knows/doesnt_know); not read by KnowledgeResolver
    aware_of_secrets: list[str] = []
    # References to knowledge cards this character holds. Each entry is a dict:
    #   - card_id (required): str — the `card_id` of a knowledge card
    #   - override (optional): str — free-text belief that replaces the card's `believes`
    #   - knows_reality (optional): bool (default False) — when True, the character
    #       knows the truth, so the card's `reality` is injected into their prompt
    #       section. Leave False/omitted while the character still believes the lie
    #       (prevents knowledge bleed). Resolved by KnowledgeResolver.
    knowledge_refs: list[dict] | None = None

    # Linked cards
    memories: list[str] = []
    secrets: list[str] = []

    # Relationships
    initial_relationships: list[dict] | None = None

    # Trust modifiers
    trust_modifiers: list[dict] | None = None

    # Social presentation (PC-specific)
    social_presentation: dict | None = None

    # NPC Behavioral Framework (optional)
    primary_archetype: Archetype | None = None
    secondary_archetype: Archetype | None = None
    tertiary_archetype: Archetype | None = None
    behavioral_modifiers: list[BehavioralModifier] = []
    modifier_details: list[dict] | None = None
    cooperation_default: CooperationDefault | None = None
    escalation_style: EscalationStyle | None = None

    # NPC availability
    first_appearance: str | None = None
    last_appearance: str | None = None
    locations_found: list[str] = []

    # Metadata
    tags: list[str] = []
    triggers: list[str] = []
    arc_status: str | None = None
    plot_hooks: list[str] = []
    karma_ledger: list | None = None


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class MemoryFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["memory"] = "memory"
    card_id: str | None = None
    title: str | None = None
    name: str | None = None
    summary: str | None = None
    belongs_to: str | None = None
    rp: str | None = None

    characters_involved: list[str] = []
    location: str | None = None
    when: str | None = None

    emotional_tone: EmotionalTone | None = None
    importance: MemoryImportance | None = None
    category: MemoryCategory | None = None

    what_character_learned: list[str] | None = None
    what_character_still_doesnt_know: list[str] | None = None
    knowledge_created: list[dict] | None = None

    related_memories: list[str] = []
    who_else_remembers: dict | None = None
    shared_with: list[str] = []

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Secret
# ---------------------------------------------------------------------------

class SecretFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["secret"] = "secret"
    card_id: str | None = None
    title: str | None = None
    name: str | None = None
    summary: str | None = None
    belongs_to: str | None = None
    rp: str | None = None

    category: SecretCategory | None = None

    known_by: list[str] = []
    known_partially_by: dict | None = None
    suspected_by: list[str] = []

    discovery_risk: DiscoveryRisk | None = None
    difficulty_to_discover: Difficulty | None = None
    discovery_triggers: list[str] | None = None
    evidence_exists: list[str] | None = None

    connects_to_secrets: list[str] = []
    reveals_if_discovered: list[str] | None = None
    creates_knowledge_when_revealed: list[dict] | None = None
    consequences_if_discovered_by: dict | None = None

    significance: SecretSignificance | None = None
    revelation_planned: RevelationPlanned | None = None
    revelation_conditions: list[str] | None = None

    status: SecretStatus | None = None
    created: str | None = None

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class LocationFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["location"] = "location"
    card_id: str | None = None
    name: str
    rp: str | None = None

    category: LocationCategory | None = None
    region: str | None = None
    access: LocationAccess | None = None

    atmosphere: str | None = None
    lighting: str | None = None
    sounds: str | None = None
    smells: str | None = None

    status: LocationStatus | None = None
    importance: MemoryImportance | None = None  # same critical/high/medium/low scale
    first_appearance: str | None = None

    significant_events: list[str] = []
    regular_occupants: list[str] = []
    secrets_hidden_here: list[str] = []
    connected_locations: list[dict] | list[str] | None = None

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------

class OrganizationFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["organization"] = "organization"
    card_id: str | None = None
    name: str
    rp: str | None = None

    category: OrgCategory | None = None
    status: OrgStatus | None = None
    influence: Influence | None = None
    scope: Scope | None = None

    public_stance: PublicStance | None = None
    known_for: str | None = None

    leadership_type: LeadershipType | None = None
    hierarchy: Hierarchy | None = None
    size: OrgSize | None = None
    recruitment: Recruitment | None = None

    key_members: list[dict] = []
    headquarters: str | None = None
    territories: list[str] = []
    allies: list[str] = []
    rivals: list[str] = []
    related_secrets: list[str] = []

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Plot Thread
# ---------------------------------------------------------------------------

class PlotThreadFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["plot_thread"] = "plot_thread"
    card_id: str | None = None
    name: str
    rp: str | None = None
    summary: str | None = None

    thread_type: str | None = None
    priority: ThreadPriority | None = None
    status: ThreadStatus | None = None
    phase: ThreadPhase | None = None

    tracking_mode: TrackingMode | None = None
    counter_thresholds: dict | None = None
    consequences: dict | None = None
    time_triggers: list[dict] | None = None

    cluster: str | None = None
    related_threads: list[str] = []
    related_characters: list[str] = []
    related_locations: list[str] = []
    related_arcs: list[str] = []
    related_docs: list[str] = []
    keywords: list[str] = []

    created: str | None = None
    auto_created: bool = False
    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Plot Arc
# ---------------------------------------------------------------------------

class PlotArcFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["plot_arc"] = "plot_arc"
    card_id: str | None = None
    name: str
    rp: str | None = None

    category: ArcCategory | None = None
    priority: ArcPriority | None = None
    status: ArcStatus | None = None
    phase: ArcPhase | None = None

    introduced: str | None = None
    started: str | None = None
    current_chapter: str | int | None = None

    key_characters: dict | None = None
    key_locations: list[str] = []
    related_arcs: list[dict] = []
    related_memories: list[str] = []
    related_secrets: list[str] = []
    related_threads: list[str] = []

    beats_completed: list[str] | None = None
    beats_remaining: list[str] | None = None
    next_milestone: str | None = None

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------

class KnowledgeFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["knowledge"] = "knowledge"
    card_id: str | None = None
    topic: str | None = None
    name: str | None = None
    summary: str | None = None
    belongs_to: list[str] | str | None = None
    rp: str | None = None

    believes: list[str] | None = None
    reality: list[str] | None = None
    confidence: Confidence | None = None
    source: str | None = None

    formed: str | None = None
    last_updated: str | None = None
    challenged_by: list[str] | None = None

    related_to: list[str] = []
    contradicts: list[str] = []

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Lore
# ---------------------------------------------------------------------------

class LoreFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["lore"] = "lore"
    card_id: str | None = None
    name: str
    rp: str | None = None

    category: LoreCategory | None = None
    scope: LoreScope | None = None
    importance: LoreImportance | None = None

    common_knowledge: str | None = None
    expert_knowledge: str | None = None
    secret_knowledge: str | None = None

    related_knowledge_cards: list[str] = []
    related_locations: list[str] = []
    related_organizations: list[str] = []

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------

class ItemFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["item"] = "item"
    card_id: str | None = None
    name: str
    rp: str | None = None

    category: ItemCategory | None = None
    rarity: ItemRarity | None = None
    status: ItemStatus | None = None

    current_holder: str | None = None
    origin: str | None = None
    location: str | None = None

    known_by: list[str] = []
    related_secrets: list[str] = []

    description: str | None = None
    properties: str | None = None

    tags: list[str] = []
    triggers: list[str] = []


# ---------------------------------------------------------------------------
# Chapter Summary
# ---------------------------------------------------------------------------

class ChapterFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["chapter_summary"] = "chapter_summary"
    card_id: str | None = None
    chapter: int | str
    title: str
    rp: str | None = None

    timeline_start: str | None = None
    timeline_end: str | None = None
    duration: str | None = None

    pov_characters: list[str] = []
    npcs_featured: list[str] = []
    new_characters_introduced: list[str] = []
    locations: list[str] = []

    threads_active: list[str] = []
    threads_resolved: list[str] = []
    threads_introduced: list[str] = []

    source_session: str | None = None
    word_count: int | str | None = None
    quote_count: int | None = None

    status: ChapterStatus | None = None
    created: str | None = None
    last_updated: str | None = None


# ---------------------------------------------------------------------------
# Lookup + dispatch
# ---------------------------------------------------------------------------

FRONTMATTER_MODELS: dict[str, type[BaseModel]] = {
    "character": CharacterFrontmatter,
    "npc": CharacterFrontmatter,  # alias — NPCs use the unified character model
    "memory": MemoryFrontmatter,
    "secret": SecretFrontmatter,
    "location": LocationFrontmatter,
    "organization": OrganizationFrontmatter,
    "plot_thread": PlotThreadFrontmatter,
    "plot_arc": PlotArcFrontmatter,
    "knowledge": KnowledgeFrontmatter,
    "lore": LoreFrontmatter,
    "item": ItemFrontmatter,
    "chapter_summary": ChapterFrontmatter,
}


def validate_frontmatter(
    card_type: str, data: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """Validate a frontmatter dict against the schema for *card_type*.

    Returns ``(valid, errors, warnings)``.
    """
    model = FRONTMATTER_MODELS.get(card_type)
    if not model:
        return False, [f"Unknown card type: {card_type}"], []
    try:
        model.model_validate(data)
        return True, [], []
    except ValidationError as e:
        errors = [f"{err['loc']}: {err['msg']}" for err in e.errors()]
        return False, errors, []
