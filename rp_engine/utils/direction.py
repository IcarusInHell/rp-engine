"""Direction marker parsing and prompt injection for mixed OOC + RP messages.

Parses inline direction markers — (( ... )) and // ... — from message text,
separating RP content from author-level guidance. Used by ChatManager to strip
direction from saved exchanges and inject it into the LLM prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Match (( ... )) blocks (multiline, non-greedy)
_PAREN_PATTERN = re.compile(r"\(\(\s*(.*?)\s*\)\)", re.DOTALL)

# Match // ... to end-of-line
_SLASH_PATTERN = re.compile(r"//\s*(.*?)$", re.MULTILINE)


@dataclass(frozen=True)
class DirectionResult:
    """Result of parsing direction markers from a message."""
    rp_content: str
    direction_content: str
    has_direction: bool


def parse_direction_markers(text: str) -> DirectionResult:
    """Extract (( ... )) and // ... direction markers from message text.

    Returns a DirectionResult with:
    - rp_content: the message with all direction markers stripped
    - direction_content: the extracted direction text (joined with newlines)
    - has_direction: whether any direction markers were found
    """
    directions: list[str] = []

    # Extract (( ... )) blocks
    for match in _PAREN_PATTERN.finditer(text):
        directions.append(match.group(1).strip())

    # Extract // ... lines
    for match in _SLASH_PATTERN.finditer(text):
        directions.append(match.group(1).strip())

    if not directions:
        return DirectionResult(
            rp_content=text,
            direction_content="",
            has_direction=False,
        )

    # Strip markers from the original text
    stripped = _PAREN_PATTERN.sub("", text)
    stripped = _SLASH_PATTERN.sub("", stripped)
    # Clean up excess whitespace from removal
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()

    return DirectionResult(
        rp_content=stripped,
        direction_content="\n".join(directions),
        has_direction=True,
    )


# ── Prompt injection ─────────────────────────────────────────────

DIRECTION_INJECTION_TEMPLATE = (
    "[Author Direction — do not reference this in your response, "
    "treat as guidance for tone/action/pacing]\n{direction}"
)

OOC_INJECTION_TEMPLATE = (
    "[The user is speaking out-of-character. Respond OOC — do not write "
    "in-character narrative. This message will not be saved as an exchange.]"
)


def build_direction_message(direction_text: str) -> dict:
    """Build a system message containing author direction for LLM injection."""
    return {
        "role": "system",
        "content": DIRECTION_INJECTION_TEMPLATE.format(direction=direction_text),
    }


def build_ooc_message() -> dict:
    """Build a system message indicating the user is speaking OOC."""
    return {
        "role": "system",
        "content": OOC_INJECTION_TEMPLATE,
    }
