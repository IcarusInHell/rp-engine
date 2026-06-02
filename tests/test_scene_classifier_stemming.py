"""Scene classifier morphology: an inflected signal word must fire its cluster.

Before the stemmer wiring, ``_score_text`` matched single-word cluster keys by
exact token equality, so a message whose only combat signal was the plural
``swords`` scored nothing (the cluster key is ``sword``). Both sides are now
stemmed, so the plural fires combat. The negative control (``kiss``) guards
against the ``-ss`` over-stem that would mis-key the intimate cluster.
"""

from __future__ import annotations

from rp_engine.services.scene_classifier import SceneClassifier


def _clf() -> SceneClassifier:
    # _score_text is pure (no DB); db is only used by state-boost queries.
    return SceneClassifier(db=None)  # type: ignore[arg-type]


def test_plural_signal_word_fires_cluster():
    scores = _clf()._score_text("She drew her swords and braced.")
    assert "combat" in scores, "plural 'swords' must hit the 'sword' combat cluster"
    # Singular and plural land on the same score — proof both sides stem.
    assert scores["combat"] == _clf()._score_text("She drew her sword.")["combat"]


def test_unrelated_text_scores_nothing():
    # Negative control: no signal words → empty (the inflection fire above is a
    # real match, not the classifier firing on everything).
    assert _clf()._score_text("They discussed the weather quietly.") == {}


def test_double_s_keyword_not_overstemmed():
    # 'kiss' is a natural-double; it must still key the intimate cluster, not be
    # truncated to 'kis' on one side and silently miss.
    assert "intimate" in _clf()._score_text("a soft, lingering kiss")
