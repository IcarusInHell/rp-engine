"""Unit tests for the example-dialogue parser (Phase 4, Feature D).

Pure parsing — no IO. Covers ``{{char}}`` substitution, section isolation,
malformed/odd marker handling, and the ``max_examples`` cap.
"""

from __future__ import annotations

from rp_engine.utils.dialogue_parser import (
    example_dialogue_messages,
    parse_example_dialogue,
)

BODY = """Dante is a ledger-keeper with a sharp tongue.

## Example Dialogue

{{user}}: What are you staring at?
{{char}}: *{{char}} doesn't look up.* "You. Wondering if you're worth the ink."

{{user}}: I need your help.
{{char}}: "Need. Such an honest word."

## Relationships
- Lilith — wary
"""


def test_parses_pairs_and_substitutes_char():
    pairs = parse_example_dialogue(BODY, "Dante")
    assert len(pairs) == 2
    assert pairs[0].user == "What are you staring at?"
    # {{char}} replaced with the real name; {{user}} left intact.
    assert pairs[0].assistant.startswith("*Dante doesn't look up.*")
    assert "{{char}}" not in pairs[0].assistant
    assert pairs[1].user == "I need your help."


def test_section_isolation_stops_at_next_heading():
    """Content under the *next* ## heading must not leak into examples."""
    pairs = parse_example_dialogue(BODY, "Dante")
    assert all("Lilith" not in p.assistant for p in pairs), "relationships section leaked"


def test_no_section_returns_empty():
    assert parse_example_dialogue("Just a bio, no examples.", "Dante") == []
    assert parse_example_dialogue("", "Dante") == []


def test_trailing_unmatched_user_is_dropped():
    body = "## Example Dialogue\n\n{{user}}: hello\n{{char}}: hi\n\n{{user}}: dangling\n"
    pairs = parse_example_dialogue(body, "Dante")
    assert len(pairs) == 1, "a user line with no following {{char}} reply must not emit a pair"


def test_case_insensitive_markers_and_heading():
    body = "## example dialogue\n\n{{USER}}: yo\n{{Char}}: hey\n"
    pairs = parse_example_dialogue(body, "Mara")
    assert len(pairs) == 1
    assert pairs[0].assistant == "hey"


def test_messages_flatten_and_cap():
    msgs = example_dialogue_messages(BODY, "Dante", max_examples=1)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert len(msgs) == 2  # one pair capped → two messages
    # Uncapped → both pairs.
    assert len(example_dialogue_messages(BODY, "Dante")) == 4
