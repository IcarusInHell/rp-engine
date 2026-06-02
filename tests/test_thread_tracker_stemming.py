"""Plot-thread mention matching is stemmed on BOTH sides.

`ThreadTracker._check_mention` advances/resets a thread's "exchanges since last
mention" counter after every exchange. Before this guard it did raw substring
matching (`kw.lower() in text_lower`), which silently bypassed the shared
stemmer that `entity_extractor`/`scene_classifier` use — so an authored keyword
`running` never matched RP text saying `runs`, and the counter kept climbing as
if the thread were dormant.

These guards fail loud if either invariant breaks:
  - single-word keyword stemming becomes one-sided -> inflected text/keyword
    stops matching (the counter would never reset → false "neglected thread"
    alerts).
  - proper-noun (character/location) matching starts stemming -> a name matches
    a truncated form.
  - multi-word keyword phrases must stay exact (stemming a phrase is meaningless).

`_check_mention` is a pure staticmethod, so it's exercised directly — no DB,
fully deterministic.
"""

from __future__ import annotations

import json

from rp_engine.services.thread_tracker import ThreadTracker
from rp_engine.utils.stemmer import stem_tokens


def _check(thread_fields: dict, text: str) -> tuple[bool, str | None]:
    """Call _check_mention exactly as update_counters does."""
    thread = {k: json.dumps(v) for k, v in thread_fields.items()}
    return ThreadTracker._check_mention(thread, text.lower(), stem_tokens(text))


def test_inflected_text_matches_singular_keyword():
    # Authored keyword is the stem "prophecy"; the text only says "prophecies".
    # "prophecy" is NOT a substring of "prophecies", so the old substring matcher
    # MISSED this — locks the text (query) side being stemmed.
    mentioned, term = _check({"keywords": ["prophecy"]}, "The old prophecies came true.")
    assert mentioned, "'prophecies' must match the 'prophecy' keyword (text side stemmed)"
    assert term == "prophecy"


def test_inflected_keyword_matches_base_text():
    # Authored keyword is the INFLECTED form "running"; the text says "runs".
    # Both stem to "run". Substring "running" in "...runs..." is False, so this
    # only passes if the authored keyword is stemmed too — locks the keyword side.
    mentioned, _ = _check({"keywords": ["running"]}, "He runs through the gate.")
    assert mentioned, "'runs' must match the inflected 'running' keyword (keyword side stemmed)"


def test_unrelated_text_does_not_match():
    # Negative control: a keyword with no morphological relation must not fire.
    mentioned, term = _check({"keywords": ["sword"]}, "She ate breakfast quietly.")
    assert not mentioned and term is None


def test_substring_overmatch_is_avoided():
    # Token-based stemming is also more PRECISE than substring: "cat" must not
    # match "category". The old `kw in text_lower` matched it (false reset).
    mentioned, _ = _check({"keywords": ["cat"]}, "He opened the category list.")
    assert not mentioned, "'cat' keyword must not match the substring inside 'category'"


def test_proper_noun_character_is_not_stemmed():
    # Characters are proper nouns — matched exactly, never stemmed.
    hit, term = _check({"related_characters": ["Ross"]}, "Ross walked into the hall.")
    assert hit and term == "Ross"

    miss, _ = _check({"related_characters": ["Ross"]}, "The ros plant wilted.")
    assert not miss, "character 'Ross' must not be reachable via the stem 'ros'"


def test_proper_noun_location_is_not_stemmed():
    hit, term = _check({"related_locations": ["Winterhold"]}, "They rode to Winterhold.")
    assert hit and term == "Winterhold"


def test_multiword_keyword_phrase_stays_exact():
    # Multi-word phrases are matched as exact substrings (not stemmed).
    hit, term = _check({"keywords": ["ancient prophecy"]}, "An ancient prophecy was foretold.")
    assert hit and term == "ancient prophecy"

    miss, _ = _check({"keywords": ["ancient prophecy"]}, "An ancient ruin was found.")
    assert not miss
