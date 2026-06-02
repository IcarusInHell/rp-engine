"""Conservative stemmer + shared tokenizer (utils/stemmer.py).

These expectations are derived from the *actual* stem() output, not from prose:
this is a suffix-stripping + silent-e stemmer with NO irregular/vowel-change
handling. So `running -> run` but `ran -> ran` (never collapses to `run`), and
`make`/`making` both land on `mak` (silent-e dropped). The `-ss` allow-list is
the real over-stem guard (`miss`, `business` survive intact).

The consistency invariant — both sides of every comparison must be stemmed with
the same function — is what the Layer A wiring depends on, so the collapse cases
below double as a contract: if stem() stops collapsing them, matching silently
under-fires and these go red.
"""

from __future__ import annotations

from rp_engine.utils.stemmer import stem, stem_tokens, tokenize


# --- Regular-inflection collapses (the recall win) -------------------------

def test_ing_collapses_to_base():
    assert stem("running") == "run"
    assert stem("running") == stem("runs") == stem("run")


def test_plural_collapses():
    assert stem("swords") == "sword"
    assert stem("wounds") == "wound"


def test_ed_and_ing_share_base():
    # attacked / attacking / attack all reduce to the same key
    assert stem("attacked") == stem("attacking") == stem("attack") == "attack"


def test_ied_ies_normalize_to_y():
    assert stem("cried") == stem("cries") == stem("cry") == "cry"
    assert stem("carried") == stem("carries") == stem("carry") == "carry"


def test_silent_e_normalized_so_make_matches_making():
    # Both land on the same stem (here: "mak") — the point is equality, not the
    # surface form. This is why over-stemming is acceptable for matching.
    assert stem("making") == stem("make")


# --- Over-stem / irregular guards (the precision floor) --------------------

def test_irregular_past_does_not_collapse():
    # No irregular map: "ran" must NOT fold into "run". Locked as a negative so
    # nobody "improves" the stemmer with an irregular table and silently turns a
    # false merge into a false keyword trigger.
    assert stem("ran") == "ran"
    assert stem("ran") != stem("run")


def test_double_s_words_are_not_truncated():
    assert stem("miss") == "miss"
    assert stem("business") == "business"
    assert stem("kiss") == "kiss"


def test_short_words_unchanged():
    # Length guards keep tiny tokens whole (no spurious -s / silent-e stripping).
    assert stem("is") == "is"
    assert stem("as") == "as"


# --- Tokenizer ------------------------------------------------------------

def test_tokenize_lowercases_splits_and_keeps_intraword_marks():
    assert tokenize("The well-known swords don't run!") == [
        "the",
        "well-known",
        "swords",
        "don't",
        "run",
    ]


def test_tokenize_drops_empties():
    assert tokenize("  ...  ") == []


def test_stem_tokens_dedups_inflections():
    # "swords" and "sword" collapse to one entry; set is deduplicated.
    assert stem_tokens("swords and a sword") == {"sword", "and", "a"}
