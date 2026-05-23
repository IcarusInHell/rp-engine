"""Shared prompt guidance constants and builders.

Single source of truth for response length ranges, scene pacing descriptions,
POV instruction templates, content boundary formatting, and user-narrative
integration guidance. Used by both PromptAssembler (API path) and
agent_prompts (Agent SDK / rp-client path).
"""

from __future__ import annotations

# ------------------------------------------------------------------
# Response length guidance
# ------------------------------------------------------------------

RESPONSE_LENGTH_GUIDANCE: dict[str, str] = {
    "short": "300\u2013500 words \u2014 focused scenes, quick exchanges, tight action",
    "medium": "500\u2013800 words \u2014 standard scenes with room for detail and reaction",
    "long": "800\u20131200 words \u2014 expansive scenes, deep introspection, multi-character moments",
    "variable": "match length to scene demands \u2014 short for snappy dialogue, long for pivotal moments",
}

# ------------------------------------------------------------------
# Scene pacing guidance
# ------------------------------------------------------------------

SCENE_PACING_GUIDANCE: dict[str, str] = {
    "slow": "linger on detail, atmosphere, and internal reaction \u2014 let scenes breathe",
    "moderate": "balance action and reflection \u2014 advance the scene but allow moments to land",
    "fast": "tight prose, quick cuts, minimal introspection \u2014 momentum over atmosphere",
}


# ------------------------------------------------------------------
# POV section builder
# ------------------------------------------------------------------

def build_pov_section(
    pov_mode: str | None,
    pov_character: str | None = None,
    dual_characters: list[str] | None = None,
) -> str:
    """Build a formatted POV instruction section.

    Returns an empty string if pov_mode is not set.
    """
    if not pov_mode:
        return ""

    if pov_mode == "dual" and dual_characters and len(dual_characters) >= 2:
        names = ", ".join(dual_characters)
        char1, char2 = dual_characters[0], dual_characters[1]
        return (
            f"## POV \u2014 Dual ({names})\n"
            f"Responses alternate between character perspectives using clear headers:\n\n"
            f"=== {char1} ===\n"
            f"[{char1}'s perspective, thoughts, and actions in their voice]\n\n"
            f"=== {char2} ===\n"
            f"[{char2}'s perspective, thoughts, and actions in their voice]\n\n"
            f"Rules:\n"
            f"- Each section maintains that character's unique voice, vocabulary, and perception\n"
            f"- Both perspectives cover the SAME scene/moment \u2014 not separate scenes\n"
            f"- Internal thoughts are only visible within that character's section\n"
            f"- Body language and dialogue from the OTHER character should be described "
            f"from the observing character's POV"
        )

    # Single mode (or dual with insufficient characters)
    if pov_character:
        return (
            f"## POV \u2014 Single ({pov_character})\n"
            f"All narration follows {pov_character}'s perspective. We only know what "
            f"they perceive, think, and feel. Other characters' internal states must be "
            f"inferred from observable behavior \u2014 facial expressions, tone, body "
            f"language, actions."
        )

    # Single mode with no character specified
    return (
        "## POV \u2014 Single\n"
        "All narration follows the POV character's perspective. We only know what "
        "they perceive, think, and feel. Other characters' internal states must be "
        "inferred from observable behavior \u2014 facial expressions, tone, body "
        "language, actions."
    )


# ------------------------------------------------------------------
# Content boundaries builder
# ------------------------------------------------------------------

def build_content_boundaries(
    sensitive_themes: list[str] | None = None,
    hard_limits: str | list[str] | None = None,
) -> str:
    """Build a Content Boundaries section from sensitive themes and hard limits.

    Returns an empty string if both are empty/null.
    """
    parts: list[str] = []

    themes = sensitive_themes or []
    if themes:
        themes_str = ", ".join(themes)
        parts.append(
            f"**Permitted themes:** {themes_str}\n"
            f"These themes are explicitly allowed. Do not soften, fade to black, "
            f"or refuse to write them."
        )

    # hard_limits can be str or list[str]
    limits_str = ""
    if isinstance(hard_limits, list):
        limits_str = ", ".join(hard_limits)
    elif isinstance(hard_limits, str):
        limits_str = hard_limits.strip()

    if limits_str:
        parts.append(f"**Hard limits \u2014 NEVER write:** {limits_str}")

    if not parts:
        return ""

    return "## Content Boundaries\n" + "\n\n".join(parts)


# ------------------------------------------------------------------
# User-narrative integration guidance
# ------------------------------------------------------------------

def build_user_narrative_guidance(
    integrate_user_narrative: bool = False,
    preserve_user_details: bool = False,
) -> str:
    """Build user-narrative integration instruction.

    Returns an empty string if neither flag is set.
    """
    if not integrate_user_narrative and not preserve_user_details:
        return ""

    instructions: list[str] = []
    if integrate_user_narrative:
        instructions.append("weave the user's narrative direction into prose naturally")
    if preserve_user_details:
        instructions.append("echo back specific details from the user's input accurately")

    return (
        "When the user's prompt contains narrative direction: "
        + " and ".join(instructions)
        + "."
    )
