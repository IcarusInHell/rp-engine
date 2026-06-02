"""Writing feedback processor — thin subclass of shared base."""

from typing import Any, Callable, Optional

from pattern_intelligence.feedback import BaseFeedbackProcessor
from .types import PatternCategory, TaskSignature, FeedbackInput
from .db import PatternDB


class FeedbackProcessor(BaseFeedbackProcessor):
    """Extracts writing patterns from a correction — builds the LLM extraction prompt over the task signature, defaulting to the word_choice category."""

    _category_enum = PatternCategory

    def __init__(self, db: PatternDB, llm_call: Optional[Callable[[str], str]] = None):
        super().__init__(db, llm_call)

    def _default_category(self) -> str:
        return "word_choice"

    def _build_extraction_prompt(self, feedback: FeedbackInput, signature: Any) -> str:
        sig_desc = "Not provided"
        if signature:
            sig: TaskSignature = signature
            sig_desc = (f"Mode: {sig.mode.value}, Register: {sig.register.value}, "
                        f"Intensity: {sig.intensity.value}, Position: {sig.position.value}, "
                        f"Elements: {[e.value for e in sig.elements]}")

        return f"""You are a writing pattern analyzer. Given an original LLM output and a user's correction, extract distinct writing patterns that should be tracked.

ORIGINAL OUTPUT:
{feedback.original_output}

USER'S REWRITE:
{feedback.user_rewrite or "Not provided"}

USER'S FEEDBACK:
{feedback.user_feedback or "Not provided"}

SCENE CONTEXT:
{sig_desc}

For each pattern you identify, return a JSON array of objects with:
- "category": one of: figurative_language, narrative_distance, information_ordering, detail_selection, reader_trust, pacing, dialogue, word_choice, structure
- "subcategory": a specific subcategory string
- "description": what the pattern is (1-2 sentences)
- "direction": "avoid" or "prefer"
- "compressed_rule": the rule in under 30 words
- "context_triggers": list of applicable triggers from:
  Modes: drafting, continuing, revising, expanding, condensing
  Registers: action, dialogue, introspection, description, exposition, transition
  Intensity: high, medium, low
  Position: opening, rising, climax, falling, closing, mid
  Elements: physical_action, dialogue, internal_thought, environmental_description, character_description, emotional_beat, sensory_detail
- "original_excerpt": the specific passage from the original (if applicable)
- "revised_excerpt": the corresponding revised passage (if applicable)

Return ONLY a JSON array. No explanation outside the JSON."""
