"""Canonical enum types for all story card frontmatter fields.

Uses ``Literal`` types (not ``enum.Enum``) for native Pydantic compatibility.
Every enum value used across card schemas is defined here as the single source
of truth.
"""

from __future__ import annotations

from typing import Literal

# === Card System ===
CardType = Literal[
    "character", "npc", "location", "secret", "memory",
    "knowledge", "organization", "plot_thread", "item",
    "lore", "plot_arc", "chapter_summary",
]

# Shared importance (used by character, location, lore, etc.)
Importance = Literal[
    "critical", "pov", "love_interest", "antagonist", "main",
    "recurring", "supporting", "important", "minor",
    "one_time", "background", "extra",
]

# === Character / NPC ===
CharacterRole = Literal[
    "protagonist", "love_interest", "pov_character",
    "npc", "supporting", "antagonist", "minor",
]

RelationshipRole = Literal[
    "friend", "enemy", "family", "rival",
    "love_interest", "professional", "ally",
]

TrustModifierType = Literal[
    "slow_to_trust", "quick_to_trust", "volatile", "grudge_holder",
]

TrustModifierSeverity = Literal["mild", "moderate", "severe"]

# NPC Behavioral Framework (from rp_engine/framework/)
Archetype = Literal[
    "POWER_HOLDER", "TRANSACTIONAL", "COMMON_PEOPLE",
    "OPPOSITION", "SPECIALIST", "PROTECTOR", "OUTSIDER",
]

BehavioralModifier = Literal[
    "OBSESSIVE", "SADISTIC", "PARANOID", "FANATICAL",
    "NARCISSISTIC", "SOCIOPATHIC", "ADDICTED",
    "HONOR_BOUND", "GRIEF_CONSUMED",
]

CooperationDefault = Literal["minimal", "moderate", "significant", "major"]
EscalationStyle = Literal["cautious", "standard", "aggressive", "explosive"]

# === Guidelines ===
PovMode = Literal["single", "dual"]
NarrativeVoice = Literal["first", "third"]
Tense = Literal["present", "past"]
ScenePacing = Literal["slow", "moderate", "fast"]
ResponseLength = Literal["short", "medium", "long", "variable"]

# === Memory ===
EmotionalTone = Literal["positive", "negative", "complex", "neutral", "traumatic"]
MemoryImportance = Literal["critical", "high", "medium", "low"]
MemoryCategory = Literal[
    # Positive
    "first_encounter", "achievement", "intimate", "promise",
    "rescue", "reunion", "triumph",
    # Negative
    "betrayal", "loss", "threat", "deception", "humiliation", "farewell",
    # Complex
    "confession", "discovery", "revelation", "confrontation",
    "realization", "sacrifice",
]

# === Secret ===
SecretCategory = Literal[
    "identity", "crime", "betrayal", "origin", "relationship",
    "knowledge", "possession", "ability", "location", "plan",
]
DiscoveryRisk = Literal["low", "medium", "high", "critical"]
Difficulty = Literal["trivial", "easy", "moderate", "hard", "nearly_impossible"]
SecretSignificance = Literal["critical", "major", "minor"]
RevelationPlanned = Literal["yes", "no", "maybe", "gradual"]
SecretStatus = Literal["active", "partially_revealed", "fully_revealed", "resolved"]

# === Location ===
LocationCategory = Literal[
    # Settlements
    "city", "town", "village", "neighborhood", "district",
    # Structures
    "building", "room", "estate", "shop", "tavern", "temple", "fortress",
    # Nature
    "wilderness", "forest", "cave", "underground", "lake", "mountain",
    # Other
    "landmark", "ship", "ruins", "dungeon", "hidden",
]
LocationAccess = Literal["public", "private", "restricted", "secret"]
LocationStatus = Literal["active", "destroyed", "abandoned", "under_construction", "changing"]

# === Organization ===
OrgCategory = Literal[
    # Power
    "government", "military", "nobility", "council",
    # Faith
    "religion", "cult", "temple", "order",
    # Commerce
    "guild", "merchant", "criminal", "syndicate",
    # Social
    "faction", "secret_society", "rebellion", "alliance",
]
OrgStatus = Literal["active", "disbanded", "underground", "rising", "declining"]
Influence = Literal["dominant", "major", "moderate", "minor", "negligible"]
Scope = Literal["global", "national", "regional", "local", "cell-based"]
PublicStance = Literal["respected", "feared", "hated", "unknown", "controversial"]
LeadershipType = Literal["autocratic", "council", "democratic", "religious", "hereditary"]
Hierarchy = Literal["rigid", "moderate", "loose", "flat"]
OrgSize = Literal["massive", "large", "medium", "small", "handful"]
Recruitment = Literal["open", "selective", "invitation", "hereditary", "secret"]

# === Plot Thread ===
ThreadPriority = Literal["plot_critical", "important", "background"]
ThreadStatus = Literal["active", "dormant", "resolved"]
ThreadPhase = Literal["emerging", "developing", "escalating", "climax", "resolving"]
TrackingMode = Literal["counter", "time", "both"]

# === Plot Arc ===
ArcCategory = Literal[
    # Story
    "main_plot", "subplot", "side_quest",
    # Character
    "character_arc", "relationship", "rivalry", "redemption",
    # Genre
    "mystery", "romance", "revenge", "survival",
    "political", "heist", "quest", "discovery",
]
ArcPriority = Literal["critical", "high", "medium", "low"]
ArcStatus = Literal["planned", "active", "paused", "completed", "abandoned"]
ArcPhase = Literal["setup", "rising_action", "climax", "resolution"]

# === Lore ===
LoreCategory = Literal[
    # Systems
    "magic_system", "technology", "economy", "politics", "natural_law",
    # Concepts
    "metaphysics", "prophecy", "cosmology", "afterlife",
]
LoreScope = Literal["universal", "world", "region", "local"]
LoreImportance = Literal["critical", "high", "medium", "flavor"]

# === Knowledge ===
Confidence = Literal["certain", "strong", "moderate", "uncertain", "suspicion"]

# === Item ===
ItemCategory = Literal[
    "weapon", "armor", "accessory", "tool", "consumable",
    "document", "key_item", "currency", "artifact", "clothing",
]
ItemRarity = Literal["common", "uncommon", "rare", "legendary", "unique"]
ItemStatus = Literal["intact", "damaged", "destroyed", "lost", "hidden", "abandoned"]

# === Chapter ===
ChapterStatus = Literal["complete", "in_progress"]
