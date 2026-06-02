"""Parse ``## Example Dialogue`` few-shot blocks from character card bodies.

SillyTavern-compatible ``{{user}}:`` / ``{{char}}:`` syntax (Feature D). Examples
live in the card's markdown body — the single source of truth (see the roadmap's
SillyTavern Design Decisions; a DB column was rejected to avoid a second voice
source). Parsing happens at prompt time; ``{{char}}`` is replaced with the
character's actual name. The parser is pure (no IO) so it unit-tests cleanly.

Example card body section::

    ## Example Dialogue

    {{user}}: What are you staring at?
    {{char}}: *He doesn't look up.* "You. Wondering if you're worth the ink."

    {{user}}: I need your help.
    {{char}}: "Need. Such an honest word."

yields ``[{"user": "What are you staring at?", "assistant": "*He doesn't ..."}]``
with ``{{char}}`` already substituted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The "## Example Dialogue" heading, case-insensitive, through the next ``##``
# heading (or end of body). DOTALL so the body spans newlines.
_SECTION_RE = re.compile(
    r"^\#\#\s+Example\s+Dialogue\s*$(.*?)(?=^\#\#\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

# A speaker marker at line start: {{user}}: or {{char}}: (SillyTavern syntax).
# Case-insensitive on the role name; captures the role so we can route the text.
_MARKER_RE = re.compile(r"^\s*\{\{\s*(user|char)\s*\}\}\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class DialoguePair:
    """One few-shot exchange: a user line and the character's reply."""
    user: str
    assistant: str


def _split_on_markers(text: str) -> list[tuple[str, str]]:
    """Return ``(role, content)`` segments split on ``{{user}}:`` / ``{{char}}:``.

    Text before the first marker is discarded (it's section prose, not dialogue).
    """
    segments: list[tuple[str, str]] = []
    role: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if role is not None:
            segments.append((role, "\n".join(buf).strip()))

    for line in text.splitlines():
        m = _MARKER_RE.match(line)
        if m:
            _flush()
            role = m.group(1).lower()
            buf = [line[m.end():].lstrip()]
        elif role is not None:
            buf.append(line)
    _flush()
    return segments


def parse_example_dialogue(body: str, char_name: str) -> list[DialoguePair]:
    """Extract example dialogue pairs from a card body.

    Returns an empty list when there is no ``## Example Dialogue`` section or no
    well-formed ``{{user}}``→``{{char}}`` pairs. ``{{char}}`` is replaced with
    ``char_name`` and ``{{user}}`` left intact (the runtime user has no fixed
    name here). Only complete user→assistant pairs are emitted; a trailing
    unmatched marker is dropped.
    """
    if not body:
        return []
    section = _SECTION_RE.search(body)
    if not section:
        return []

    def _subst(s: str) -> str:
        return re.sub(r"\{\{\s*char\s*\}\}", char_name, s, flags=re.IGNORECASE)

    pairs: list[DialoguePair] = []
    pending_user: str | None = None
    for role, content in _split_on_markers(section.group(1)):
        if role == "user":
            pending_user = content
        elif role == "char" and pending_user is not None:
            pairs.append(DialoguePair(user=_subst(pending_user), assistant=_subst(content)))
            pending_user = None
    return pairs


def example_dialogue_messages(
    body: str, char_name: str, max_examples: int | None = None,
) -> list[dict]:
    """Parse and flatten example dialogue into LLM ``user``/``assistant`` messages.

    Caps to the first ``max_examples`` pairs (oldest-first, as authored) when
    given. Returns ``[]`` when there are no parseable examples.
    """
    pairs = parse_example_dialogue(body, char_name)
    if max_examples is not None:
        pairs = pairs[:max_examples]
    messages: list[dict] = []
    for pair in pairs:
        messages.append({"role": "user", "content": pair.user})
        messages.append({"role": "assistant", "content": pair.assistant})
    return messages
