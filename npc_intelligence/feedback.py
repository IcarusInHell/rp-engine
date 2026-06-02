"""NPC behavioral feedback processor — thin subclass of shared base."""

from typing import Any, Callable, Optional

from pattern_intelligence.feedback import BaseFeedbackProcessor
from .types import BehavioralCategory, BehavioralSignature, FeedbackInput
from .db import BehavioralPatternDB


class BehavioralFeedbackProcessor(BaseFeedbackProcessor):
    """Extracts NPC behavioral patterns from a correction — builds the LLM extraction prompt over the behavioral signature, defaulting to the self_interest category."""

    _category_enum = BehavioralCategory

    def __init__(self, db: BehavioralPatternDB, llm_call: Optional[Callable[[str], str]] = None):
        super().__init__(db, llm_call)

    def _default_category(self) -> str:
        return "self_interest"

    def _build_extraction_prompt(self, feedback: FeedbackInput, signature: Any) -> str:
        sig_desc = "Not provided"
        if signature:
            sig: BehavioralSignature = signature
            sig_desc = (f"Archetype: {sig.archetype.value}, "
                        f"Trust Stage: {sig.trust_stage.value}, "
                        f"Modifiers: {[m.value for m in sig.modifiers]}, "
                        f"Interaction: {sig.interaction_type.value}, "
                        f"Scene Signals: {[s.value for s in sig.scene_signals]}")

        return f"""You are an NPC behavioral pattern analyzer. Given an original NPC output and a user's correction, extract distinct behavioral patterns that should be tracked.

ORIGINAL OUTPUT:
{feedback.original_output}

USER'S REWRITE:
{feedback.user_rewrite or "Not provided"}

USER'S FEEDBACK:
{feedback.user_feedback or "Not provided"}

NPC CONTEXT:
{sig_desc}

For each pattern you identify, return a JSON array of objects with:
- "category": one of: self_interest, archetype_voice, trust_mechanics, escalation, modifier_fidelity, internal_external_gap, hostility_persistence, cooperation_ceiling, social_hierarchy, hybrid_friction, knowledge_boundary
- "subcategory": a specific subcategory string
- "description": what the pattern is (1-2 sentences)
- "direction": "avoid" or "prefer"
- "compressed_rule": the rule in under 30 words
- "context_triggers": list of applicable triggers from:
  Archetypes: power_holder, transactional, common_people, opposition, specialist, protector, outsider
  Modifiers: obsessive, paranoid, fanatical, narcissistic, sadistic, addicted, honor_bound, grief_consumed, sociopathic
  Trust Stages: hostile, antagonistic, suspicious, wary, neutral, familiar, trusted, devoted
  Interaction Types: information_request, cooperation_request, confrontation, negotiation, hostile_encounter, social_interaction, deception_attempt
  Scene Signals: danger, combat, emotional, intimate, investigation, social
- "original_excerpt": the specific passage from the original (if applicable)
- "revised_excerpt": the corresponding revised passage (if applicable)

Return ONLY a JSON array. No explanation outside the JSON."""
