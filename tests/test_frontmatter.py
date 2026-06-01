"""Foundation: YAML frontmatter parse/serialize.

A malformed or absent header must degrade to ``(None, content)`` — never raise,
never silently drop the body text.
"""

from __future__ import annotations

from rp_engine.utils.frontmatter import parse_frontmatter, serialize_frontmatter


def test_parse_basic():
    fm, body = parse_frontmatter("---\ntype: character\nname: Alice\n---\nBody text here.\n")
    assert fm == {"type": "character", "name": "Alice"}
    assert body == "Body text here.\n"


def test_no_frontmatter_returns_full_content():
    content = "Just a plain note with no header.\n"
    fm, body = parse_frontmatter(content)
    assert fm is None
    assert body == content, "body must be preserved verbatim when there is no header"


def test_malformed_yaml_does_not_raise_and_keeps_content():
    content = "---\nname: : : broken\n  - bad: [unclosed\n---\nBody survives.\n"
    fm, body = parse_frontmatter(content)
    assert fm is None
    assert body == content


def test_non_dict_frontmatter_rejected():
    # A YAML list, not a mapping — not valid card frontmatter.
    fm, body = parse_frontmatter("---\n- a\n- b\n---\nBody.\n")
    assert fm is None
    assert body == "---\n- a\n- b\n---\nBody.\n"


def test_round_trip():
    fm = {"type": "character", "name": "Bob", "importance": "high"}
    body = "Bob is a merchant.\n"
    text = serialize_frontmatter(fm, body)
    parsed_fm, parsed_body = parse_frontmatter(text)
    assert parsed_fm == fm
    assert parsed_body.strip() == body.strip()
