"""NPC behavioral classifier — maps structured RP-Engine inputs (archetype/modifiers/trust/scene) into a BehavioralSignature, deterministic and zero-LLM."""

from __future__ import annotations

from .types import (
    BehavioralSignature, Archetype, Modifier, TrustStage,
    InteractionType, SceneSignal,
)


_INTERACTION_KEYWORDS = [
    (InteractionType.HOSTILE_ENCOUNTER, [
        "attack", "fight", "kill", "weapon", "gun", "knife", "ambush",
        "murder", "destroy", "slay"]),
    (InteractionType.CONFRONTATION, [
        "confront", "accuse", "demand", "challenge", "threaten",
        "draw weapon", "face off", "stand off"]),
    (InteractionType.DECEPTION_ATTEMPT, [
        "lie", "deceive", "trick", "manipulate", "pretend", "fake",
        "bluff", "disguise", "con"]),
    (InteractionType.NEGOTIATION, [
        "negotiate", "deal", "bargain", "trade", "offer", "terms",
        "compromise", "price", "exchange"]),
    (InteractionType.COOPERATION_REQUEST, [
        "help", "assist", "need you", "work together", "ally", "join",
        "please", "can you", "need your"]),
    (InteractionType.INFORMATION_REQUEST, [
        "tell me", "what do you know", "information", "where is",
        "who is", "explain", "heard about", "know anything"]),
]

_TRUST_RANGES = [
    (-50, -36, TrustStage.HOSTILE),
    (-35, -21, TrustStage.ANTAGONISTIC),
    (-20, -11, TrustStage.SUSPICIOUS),
    (-10,  -1, TrustStage.WARY),
    (  0,   9, TrustStage.NEUTRAL),
    ( 10,  19, TrustStage.FAMILIAR),
    ( 20,  34, TrustStage.TRUSTED),
    ( 35,  50, TrustStage.DEVOTED),
]


class BehavioralClassifier:
    """Builds a BehavioralSignature from RP inputs — normalizes archetype/modifier strings, derives trust stage from score, filters scene signals, detects interaction type from the prompt."""

    def __init__(self):
        pass

    def classify(
        self,
        archetype: str | None = None,
        modifiers: list[str] | None = None,
        trust_stage: str | None = None,
        trust_score: int = 0,
        scene_signals: dict[str, float] | None = None,
        scene_prompt: str = "",
        override: dict | None = None,
    ) -> BehavioralSignature:
        """
        Produce a BehavioralSignature from structured RP Engine data.

        1. Normalize archetype string -> Archetype enum
        2. Map modifiers list -> Modifier enums
        3. Compute trust stage from score or use provided stage
        4. Filter scene signals at >= 0.3 threshold
        5. Detect interaction type from scene prompt
        6. Apply override dict
        """
        override = override or {}

        # 1. Archetype
        if "archetype" in override:
            arch = Archetype(override["archetype"])
        else:
            arch = self._normalize_archetype(archetype)

        # 2. Modifiers
        if "modifiers" in override:
            mods = [Modifier(m) for m in override["modifiers"]]
        else:
            mods = self._normalize_modifiers(modifiers or [])

        # 3. Trust stage
        if "trust_stage" in override:
            ts = TrustStage(override["trust_stage"])
        elif trust_stage and trust_stage != "neutral":
            try:
                ts = TrustStage(trust_stage.lower().replace(" ", "_"))
            except ValueError:
                ts = self._trust_score_to_stage(trust_score)
        else:
            ts = self._trust_score_to_stage(trust_score)

        # 4. Scene signals
        if "scene_signals" in override:
            signals = [SceneSignal(s) for s in override["scene_signals"]]
        else:
            signals = self._filter_scene_signals(scene_signals or {})

        # 5. Interaction type
        if "interaction_type" in override:
            itype = InteractionType(override["interaction_type"])
        else:
            itype = self._detect_interaction_type(scene_prompt)

        return BehavioralSignature(
            archetype=arch,
            modifiers=mods,
            trust_stage=ts,
            interaction_type=itype,
            scene_signals=signals,
        )

    def _normalize_archetype(self, archetype: str | None) -> Archetype:
        if not archetype:
            return Archetype.COMMON_PEOPLE
        try:
            normalized = archetype.lower().replace(" ", "_").replace("-", "_")
            return Archetype(normalized)
        except ValueError:
            return Archetype.COMMON_PEOPLE

    def _normalize_modifiers(self, modifiers: list[str]) -> list[Modifier]:
        result = []
        for m in modifiers:
            try:
                normalized = m.lower().replace(" ", "_").replace("-", "_")
                result.append(Modifier(normalized))
            except ValueError:
                continue  # Skip unrecognized silently
        return result

    def _trust_score_to_stage(self, score: int) -> TrustStage:
        clamped = max(-50, min(50, score))
        for low, high, stage in _TRUST_RANGES:
            if low <= clamped <= high:
                return stage
        return TrustStage.NEUTRAL

    def _filter_scene_signals(self, signals: dict[str, float]) -> list[SceneSignal]:
        result = []
        for key, value in signals.items():
            if value >= 0.3:
                try:
                    result.append(SceneSignal(key.lower()))
                except ValueError:
                    continue
        return result

    def _detect_interaction_type(self, scene_prompt: str) -> InteractionType:
        if not scene_prompt:
            return InteractionType.SOCIAL_INTERACTION
        lower = scene_prompt.lower()
        for itype, keywords in _INTERACTION_KEYWORDS:
            for kw in keywords:
                if kw in lower:
                    return itype
        return InteractionType.SOCIAL_INTERACTION
