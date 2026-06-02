"""Conservative morphological stemmer + shared tokenizer — no LLM, zero deps.

Ported from the sibling Lazy-RP project (`hooks/lib/stemmer.py`). Strips only
`-ing/-ed/-s/-es/-ies` with consonant-undoubling and silent-e handling, keeping
a `_NATURAL_DOUBLES` allow-list. This is deliberately *not* full Porter/Snowball:
full Porter over-stems (`organization -> organ`), which turns a false token merge
into a false keyword trigger in precise clusters.

What this stemmer is for: making in-process **exact-token-equality** matchers
(scene_classifier signal clusters, entity_extractor keyword index, context_engine
memory overlap) morphology-aware so `swords` matches a `sword` keyword. It only
handles regular suffixes — irregular/vowel-change forms (`ran`, `was`, `mice`)
are intentionally NOT collapsed.

**Consistency invariant:** wherever this is applied, BOTH sides of a comparison
(authored keyword and runtime text) must be stemmed with the same function. A
one-sided stem silently breaks matching.

The DB keyword/BM25 leg (`vectors_fts`) is handled separately by the FTS5
`porter` tokenizer (migration 027) — a Python stemmer cannot touch SQL.
"""

from __future__ import annotations

import re

_CONSONANTS = set("bcdfghjklmnpqrstvwxyz")

# Words whose final doubled consonant is part of the base form, not an inflection.
# Without this allow-list, `_undouble` would corrupt them (e.g. miss -> mis).
_NATURAL_DOUBLES = frozenset({
    "add", "all", "ball", "bell", "bill", "bluff", "buff", "bull", "buzz",
    "call", "cell", "chill", "cliff", "cross", "cuff", "doll", "drill",
    "dull", "dwell", "err", "fall", "fill", "fizz", "fluff", "frill",
    "full", "grass", "grill", "gross", "gruff", "gull", "hall", "hell",
    "hill", "hiss", "hull", "ill", "jazz", "jell", "kill", "kiss",
    "loss", "mall", "mass", "mess", "mill", "miss", "moss", "mull",
    "null", "odd", "off", "pass", "pill", "poll", "press", "puff",
    "pull", "quill", "rill", "roll", "scroll", "sell", "shell", "skill",
    "skull", "small", "smell", "sniff", "spell", "spill", "staff",
    "stall", "stiff", "still", "stress", "stuff", "swell", "tall",
    "tell", "thrill", "till", "toll", "toss", "trill", "troll", "wall",
    "well", "will", "yell",
})

# Shared tokenizer: split on anything that isn't a word char, apostrophe, or hyphen.
# scene_classifier._WORD_SPLIT and entity_extractor._tokenize collapse onto this.
_WORD_SPLIT = re.compile(r"[^\w'-]+", re.UNICODE)


def _undouble(s: str) -> str:
    if len(s) >= 2 and s[-1] == s[-2] and s[-1] in _CONSONANTS:
        if s not in _NATURAL_DOUBLES:
            return s[:-1]
    return s


def _drop_silent_e(s: str) -> str:
    if len(s) > 3 and s[-1] == "e" and s[-2] in _CONSONANTS:
        return s[:-1]
    return s


def stem(word: str) -> str:
    """Stem a single word to its conservative base form.

    Only regular suffixes are stripped (`-ing/-ied/-ed/-ies/-es/-s`) with
    consonant-undoubling and silent-e normalization. Irregular forms are left
    as-is, so `stem('ran') == 'ran'` (never collapses to `run`).
    """
    w = word.lower().strip()

    # -ing (present participle / gerund)
    if w.endswith("ying") and len(w) > 5:
        s = _undouble(w[:-3])
        return _drop_silent_e(s)
    if w.endswith("ing") and len(w) > 5:
        s = _undouble(w[:-3])
        return _drop_silent_e(s)

    # -ied before general -ed (carried -> carry, cried -> cry)
    if w.endswith("ied") and len(w) > 4:
        return w[:-3] + "y"

    # -ed (past tense / past participle)
    if w.endswith("ed") and len(w) > 4:
        s = _undouble(w[:-2])
        return _drop_silent_e(s)

    # -ies before general -es (carries -> carry, cries -> cry)
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"

    # -es (plural / third person)
    if w.endswith("es") and len(w) > 4:
        return w[:-2]

    # -s (plural / third person, but not words ending in -ss)
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]

    # base form: normalize silent-e so stem("make") == stem("making")
    return _drop_silent_e(w)


def tokenize(text: str) -> list[str]:
    """Split text into lowercased word tokens (no stemming, no empties).

    Shared by scene_classifier and entity_extractor so tokenization is defined
    once. Tokens preserve apostrophes and hyphens (`don't`, `well-known`).
    """
    return [t for t in _WORD_SPLIT.split(text.lower()) if t]


def stem_tokens(text: str) -> set[str]:
    """Tokenize then stem each token, returning the deduplicated stem set."""
    return {stem(t) for t in tokenize(text)}
