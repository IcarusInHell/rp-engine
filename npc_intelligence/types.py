"""NPC behavioral intelligence types — the 5 trigger-dimension enums, the behavioral-pattern category taxonomy, and the BehavioralSignature / InjectionPayload dataclasses."""

from dataclasses import dataclass, field
from enum import Enum
# Re-export shared types from base package
from pattern_intelligence.types import (
    CorrectionPair,
    Direction,
    ExtractionResult,
    FeedbackInput,
    Pattern,
    ScoredPattern,
)


# === TRIGGER DIMENSION ENUMS (NPC-specific) ===

class Archetype(str, Enum):
    """NPC archetype — the behavioral-template trigger dimension (power-holder, transactional, common-people, ...)."""

    POWER_HOLDER = "power_holder"
    TRANSACTIONAL = "transactional"
    COMMON_PEOPLE = "common_people"
    OPPOSITION = "opposition"
    SPECIALIST = "specialist"
    PROTECTOR = "protector"
    OUTSIDER = "outsider"

class Modifier(str, Enum):
    """Personality modifier — an intensifying behavioral overlay (obsessive, paranoid, sadistic, ...); zero or more apply per NPC."""

    OBSESSIVE = "obsessive"
    PARANOID = "paranoid"
    FANATICAL = "fanatical"
    NARCISSISTIC = "narcissistic"
    SADISTIC = "sadistic"
    ADDICTED = "addicted"
    HONOR_BOUND = "honor_bound"
    GRIEF_CONSUMED = "grief_consumed"
    SOCIOPATHIC = "sociopathic"

class TrustStage(str, Enum):
    """Trust stage — the 8-band label derived from a -50..50 trust score (hostile → devoted)."""

    HOSTILE = "hostile"
    ANTAGONISTIC = "antagonistic"
    SUSPICIOUS = "suspicious"
    WARY = "wary"
    NEUTRAL = "neutral"
    FAMILIAR = "familiar"
    TRUSTED = "trusted"
    DEVOTED = "devoted"

class InteractionType(str, Enum):
    """Interaction type — the kind of exchange detected from the scene prompt (negotiation, confrontation, deception, ...)."""

    INFORMATION_REQUEST = "information_request"
    COOPERATION_REQUEST = "cooperation_request"
    CONFRONTATION = "confrontation"
    NEGOTIATION = "negotiation"
    HOSTILE_ENCOUNTER = "hostile_encounter"
    SOCIAL_INTERACTION = "social_interaction"
    DECEPTION_ATTEMPT = "deception_attempt"

class SceneSignal(str, Enum):
    """Scene signal — an ambient scene quality (danger, combat, intimate, ...) admitted when its score is >= 0.3."""

    DANGER = "danger"
    COMBAT = "combat"
    EMOTIONAL = "emotional"
    INTIMATE = "intimate"
    INVESTIGATION = "investigation"
    SOCIAL = "social"


# === PATTERN ENUMS ===

class BehavioralCategory(str, Enum):
    """Behavioral pattern category — the learned-pattern taxonomy (self-interest, archetype-voice, trust-mechanics, ...)."""

    SELF_INTEREST = "self_interest"
    ARCHETYPE_VOICE = "archetype_voice"
    TRUST_MECHANICS = "trust_mechanics"
    ESCALATION = "escalation"
    MODIFIER_FIDELITY = "modifier_fidelity"
    INTERNAL_EXTERNAL_GAP = "internal_external_gap"
    HOSTILITY_PERSISTENCE = "hostility_persistence"
    COOPERATION_CEILING = "cooperation_ceiling"
    SOCIAL_HIERARCHY = "social_hierarchy"
    HYBRID_FRICTION = "hybrid_friction"
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"


# === DATACLASSES ===

@dataclass
class BehavioralSignature:
    """The full 5-dimension behavioral fingerprint for one NPC moment — archetype + modifiers + trust stage + interaction type + scene signals."""

    archetype: Archetype
    modifiers: list[Modifier]
    trust_stage: TrustStage
    interaction_type: InteractionType
    scene_signals: list[SceneSignal]

    def all_trigger_values(self) -> set[str]:
        """Flatten all dimension values into a set for overlap matching."""
        values = {self.archetype.value, self.trust_stage.value,
                  self.interaction_type.value}
        values.update(m.value for m in self.modifiers)
        values.update(s.value for s in self.scene_signals)
        return values

    @classmethod
    def from_dict(cls, d: dict) -> "BehavioralSignature":
        """
        Build from a dict with string values. Missing keys get defaults.
        Accepts partial dicts for RP Engine integration.
        """
        return cls(
            archetype=Archetype(d.get("archetype", "common_people")),
            modifiers=[Modifier(m) for m in d.get("modifiers", [])],
            trust_stage=TrustStage(d.get("trust_stage", "neutral")),
            interaction_type=InteractionType(d.get("interaction_type", "social_interaction")),
            scene_signals=[SceneSignal(s) for s in d.get("scene_signals", [])],
        )


@dataclass
class InjectionPayload:
    """The formatted text block ready for system prompt insertion."""
    text: str
    token_count: int
    patterns_included: list[str]  # pattern IDs
    behavioral_signature: BehavioralSignature
    npc_name: str = ""
