"""Writing injection formatter — thin subclass of shared base."""

from typing import Any, Callable, Optional

from pattern_intelligence.injection import BaseInjectionFormatter
from .types import TaskSignature, InjectionPayload, ScoredPattern


class InjectionFormatter(BaseInjectionFormatter):
    """Formats scored patterns into the `[WRITING CONSTRAINTS]` prompt block — token-budgeted, with an intensity/register/position/elements header."""

    def __init__(self, token_budget: int = 600,
                 count_tokens: Optional[Callable[[str], int]] = None):
        super().__init__(token_budget, count_tokens)

    def format(self, scored_patterns: list[ScoredPattern],
               task_sig: TaskSignature) -> InjectionPayload:
        """Format scored patterns into an InjectionPayload, wrapping the base formatter's text/token/id triple with the task signature."""
        full_text, actual_tokens, included_ids = super().format(
            scored_patterns, task_sig)
        return InjectionPayload(
            text=full_text,
            token_count=actual_tokens,
            patterns_included=included_ids,
            task_signature=task_sig,
        )

    def _build_header(self, signature: Any, **kwargs: Any) -> str:
        sig: TaskSignature = signature
        element_names = {
            "physical_action": "physical",
            "dialogue": "dialogue",
            "internal_thought": "internal thought",
            "environmental_description": "environmental",
            "character_description": "character",
            "emotional_beat": "emotional",
            "sensory_detail": "sensory",
        }

        parts = [f"{sig.intensity.value.capitalize()}-intensity {sig.register.value}"]

        if sig.position != sig.position.MID:
            parts[0] += f", {sig.position.value}"

        if sig.elements:
            element_strs = [element_names.get(e.value, e.value) for e in sig.elements]
            if len(element_strs) == 1:
                parts.append(f"{element_strs[0]} focus.")
            else:
                parts.append(" + ".join(element_strs) + " focus.")
        else:
            parts[0] += "."

        return " ".join(parts) if len(parts) > 1 else parts[0]

    def _build_wrapper(self, signature: Any, **kwargs: Any) -> tuple[str, str, str]:
        return (
            "[WRITING CONSTRAINTS — THIS PASSAGE]",
            "Scene context: ",
            "[END WRITING CONSTRAINTS]",
        )
