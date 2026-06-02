"""Heuristic scene signal classification — no LLM.

Scores text against weighted keyword clusters, then applies state-based
boosts from character conditions, emotional states, and scene mood.
Returns a dict of signal names → normalized scores (0.0-1.0).
"""

from __future__ import annotations

import logging

from rp_engine.database import Database
from rp_engine.services.state_entry_resolver import (
    latest_character_states_batch,
    latest_scene_state,
)
from rp_engine.utils.json_helpers import safe_parse_json_array
from rp_engine.utils.stemmer import stem, stem_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal keyword clusters: signal → {intensity: [words]}
# ---------------------------------------------------------------------------

SIGNAL_CLUSTERS: dict[str, dict[str, list[str]]] = {
    "danger": {
        "high": [
            "gun", "knife", "weapon", "shoot", "stab", "kill", "attack",
            "blood", "wound", "dead", "die", "murder", "bomb", "explosion",
        ],
        "medium": [
            "threat", "danger", "hurt", "hit", "punch", "fight", "scream",
            "run", "escape", "chase", "trapped", "cornered", "ambush",
        ],
        "low": [
            "dark", "alley", "shadow", "fear", "afraid", "nervous",
            "alone", "follow", "watch", "warning", "careful", "suspicious",
        ],
    },
    "combat": {
        "high": [
            "sword", "shield", "battle", "duel", "slash", "block",
            "parry", "dodge", "charge", "strike", "arrow", "armor",
        ],
        "medium": [
            "fight", "wrestle", "grapple", "tackle", "kick", "throw",
            "retreat", "advance", "flank", "defend", "counterattack",
        ],
        "low": [
            "stance", "guard", "ready", "tense", "circled", "opponent",
            "challenge", "confrontation", "standoff",
        ],
    },
    "emotional": {
        "high": [
            "cry", "tears", "sob", "weep", "heartbreak", "grief",
            "devastated", "shattered", "anguish", "despair", "agony",
        ],
        "medium": [
            "sad", "angry", "furious", "hurt", "betrayed", "lonely",
            "jealous", "guilty", "ashamed", "regret", "frustrated",
        ],
        "low": [
            "sigh", "frown", "quiet", "distant", "withdrawn", "pensive",
            "melancholy", "wistful", "nostalgic", "bittersweet",
        ],
    },
    "intimate": {
        "high": [
            "kiss", "embrace", "caress", "lips", "breath", "skin",
            "body", "held", "close", "touched", "naked", "bed",
        ],
        "medium": [
            "hand", "fingers", "cheek", "hair", "warmth", "gentle",
            "soft", "tender", "intimate", "whisper", "lean",
        ],
        "low": [
            "smile", "gaze", "eyes", "look", "blush", "shy",
            "nervous", "heart", "pulse", "butterflies", "aware",
        ],
    },
    "investigation": {
        "high": [
            "evidence", "clue", "discover", "reveal", "confession",
            "document", "proof", "witness", "testimony", "verdict",
        ],
        "medium": [
            "search", "investigate", "question", "interrogate", "examine",
            "inspect", "puzzle", "mystery", "suspect", "motive",
        ],
        "low": [
            "wonder", "curious", "notice", "observe", "strange",
            "unusual", "odd", "hidden", "secret", "trace",
        ],
    },
    "social": {
        "high": [
            "party", "gathering", "crowd", "celebration", "feast",
            "ceremony", "meeting", "conference", "negotiation",
        ],
        "medium": [
            "conversation", "chat", "discuss", "agree", "disagree",
            "introduce", "greet", "toast", "dance", "invite",
        ],
        "low": [
            "bar", "restaurant", "cafe", "lounge", "club",
            "table", "drink", "music", "atmosphere", "laughter",
        ],
    },
}

WEIGHT_MAP = {"high": 3, "medium": 2, "low": 1}

# Normalization constant: score / (score + K). K=6 gives smooth curve.
_NORM_K = 6

# ---------------------------------------------------------------------------
# State-based boosts: (db_table, json_field_or_column, value, signal, boost)
# ---------------------------------------------------------------------------

# Character condition boosts (from characters.conditions JSON array)
CONDITION_BOOSTS: list[tuple[str, str, float]] = [
    ("injured", "danger", 0.2),
    ("wounded", "danger", 0.2),
    ("bleeding", "danger", 0.3),
    ("held at gunpoint", "danger", 0.4),
    ("captive", "danger", 0.3),
    ("restrained", "danger", 0.2),
    ("armed", "combat", 0.2),
    ("drunk", "social", 0.1),
    ("crying", "emotional", 0.2),
    ("panicking", "danger", 0.2),
    ("undressed", "intimate", 0.2),
]

# Emotional state boosts (from characters.emotional_state)
EMOTION_BOOSTS: list[tuple[str, str, float]] = [
    ("grief", "emotional", 0.3),
    ("terrified", "danger", 0.2),
    ("furious", "emotional", 0.2),
    ("heartbroken", "emotional", 0.3),
    ("aroused", "intimate", 0.3),
    ("suspicious", "investigation", 0.2),
    ("anxious", "danger", 0.1),
    ("euphoric", "social", 0.1),
    ("devastated", "emotional", 0.3),
]

# Scene mood boosts (from scene_context.mood)
MOOD_BOOSTS: list[tuple[str, str, float]] = [
    ("tense", "danger", 0.1),
    ("romantic", "intimate", 0.2),
    ("somber", "emotional", 0.1),
    ("festive", "social", 0.2),
    ("hostile", "danger", 0.2),
    ("mysterious", "investigation", 0.2),
    ("eerie", "danger", 0.1),
    ("intimate", "intimate", 0.2),
    ("chaotic", "combat", 0.1),
]

# Precompute stemmed cluster keys at module load so both sides of the match
# are stemmed consistently. Single-word keys are stemmed and matched by token
# equality; multi-word keys keep substring matching (stemming a phrase is
# meaningless). Structure: signal -> intensity -> (stemmed_single_keys, multi_keys)
_STEMMED_CLUSTERS: dict[str, dict[str, tuple[set[str], list[str]]]] = {
    signal: {
        intensity: (
            {stem(kw) for kw in keywords if " " not in kw},
            [kw for kw in keywords if " " in kw],
        )
        for intensity, keywords in intensities.items()
    }
    for signal, intensities in SIGNAL_CLUSTERS.items()
}


class SceneClassifier:
    """Classify scene signals from text + character/scene state."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def classify(
        self,
        user_message: str,
        last_response: str | None,
        rp_folder: str,
        branch: str = "main",
        threshold: float = 0.3,
    ) -> dict[str, float]:
        """Return signal scores above threshold, normalized 0.0-1.0."""
        combined = user_message
        if last_response:
            combined = f"{last_response}\n{user_message}"

        # 1. Score text against keyword clusters
        raw_scores = self._score_text(combined)

        # 2. Normalize: score / (score + K)
        scores = {
            signal: raw / (raw + _NORM_K) for signal, raw in raw_scores.items() if raw > 0
        }

        # 3. Apply state boosts from DB
        scores = await self._apply_state_boosts(scores, rp_folder, branch)

        # 4. Clamp to 1.0 and filter by threshold
        return {
            signal: min(score, 1.0)
            for signal, score in scores.items()
            if score >= threshold
        }

    def _score_text(self, text: str) -> dict[str, float]:
        """Count weighted keyword matches per signal category.

        Single-word keys match stemmed text tokens (both sides stemmed, so
        ``swords`` hits a ``sword`` cluster); multi-word keys match by substring.
        """
        lower = text.lower()
        stemmed_words = stem_tokens(text)
        scores: dict[str, float] = {}

        for signal, intensities in _STEMMED_CLUSTERS.items():
            total = 0.0
            for intensity, (single_keys, multi_keys) in intensities.items():
                weight = WEIGHT_MAP[intensity]
                for kw in single_keys:
                    if kw in stemmed_words:
                        total += weight
                for kw in multi_keys:
                    if kw in lower:
                        total += weight
            if total > 0:
                scores[signal] = total

        return scores

    async def _apply_state_boosts(
        self, scores: dict[str, float], rp_folder: str, branch: str
    ) -> dict[str, float]:
        """Boost signals based on character conditions, emotions, scene mood."""
        result = dict(scores)

        # Character conditions + emotional states from CoW table
        # Fetch all card_ids first, then batch resolve
        all_card_ids = [
            r["card_id"] for r in await self.db.fetch_all(
                """SELECT DISTINCT card_id FROM character_state_entries
                   WHERE rp_folder = ? AND branch = ?""",
                [rp_folder, branch],
            )
        ]
        batch_states = await latest_character_states_batch(
            self.db, rp_folder, branch, all_card_ids
        ) if all_card_ids else {}
        chars = list(batch_states.values())
        for char in chars:
            # Parse conditions JSON array
            conditions_raw = char.get("conditions")
            if conditions_raw:
                conditions = safe_parse_json_array(conditions_raw)
                if isinstance(conditions, list):
                    for cond_val, signal, boost in CONDITION_BOOSTS:
                        if cond_val.lower() in [c.lower() for c in conditions if isinstance(c, str)]:
                            result[signal] = result.get(signal, 0) + boost

            # Emotional state
            emotional = char.get("emotional_state", "")
            if emotional:
                emotional_lower = emotional.lower()
                for emo_val, signal, boost in EMOTION_BOOSTS:
                    if emo_val in emotional_lower:
                        result[signal] = result.get(signal, 0) + boost

        # Scene mood from CoW table
        scene = await latest_scene_state(self.db, rp_folder, branch)
        if scene and scene.get("mood"):
            mood_lower = scene["mood"].lower()
            for mood_val, signal, boost in MOOD_BOOSTS:
                if mood_val in mood_lower:
                    result[signal] = result.get(signal, 0) + boost

        return result
