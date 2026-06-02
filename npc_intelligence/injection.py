"""NPC behavioral injection formatter — thin subclass of shared base."""

from typing import Any, Callable, Optional

from pattern_intelligence.injection import BaseInjectionFormatter
from .types import BehavioralSignature, InjectionPayload, ScoredPattern


class BehavioralInjectionFormatter(BaseInjectionFormatter):
    """Formats scored patterns into the `[NPC BEHAVIORAL CONSTRAINTS]` prompt block — token-budgeted, with an archetype/trust/modifier/scene header."""

    def __init__(self, token_budget: int = 500,
                 count_tokens: Optional[Callable[[str], int]] = None):
        super().__init__(token_budget, count_tokens)

    def format(self, scored_patterns: list[ScoredPattern],
               behavioral_sig: BehavioralSignature,
               npc_name: str = "") -> InjectionPayload:
        """Format scored patterns into an InjectionPayload, wrapping the base formatter's text/token/id triple with the behavioral signature and NPC name."""
        full_text, actual_tokens, included_ids = super().format(
            scored_patterns, behavioral_sig, npc_name=npc_name)
        return InjectionPayload(
            text=full_text,
            token_count=actual_tokens,
            patterns_included=included_ids,
            behavioral_signature=behavioral_sig,
            npc_name=npc_name,
        )

    def _build_header(self, signature: Any, **kwargs: Any) -> str:
        sig: BehavioralSignature = signature
        arch = sig.archetype.value.replace("_", " ").title()
        trust = sig.trust_stage.value.replace("_", " ").title()
        mods = ", ".join(m.value.replace("_", " ").title() for m in sig.modifiers) or "None"
        signals = ", ".join(s.value.title() for s in sig.scene_signals) or "None"
        return f"{arch} | Trust: {trust} | Modifiers: {mods} | Scene: {signals}"

    def _build_wrapper(self, signature: Any, **kwargs: Any) -> tuple[str, str, str]:
        npc_name = kwargs.get("npc_name", "")
        return (
            f"[NPC BEHAVIORAL CONSTRAINTS — {npc_name}]",
            "Context: ",
            "[END NPC BEHAVIORAL CONSTRAINTS]",
        )
