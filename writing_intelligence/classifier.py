"""Writing task classifier — maps a prompt (+ preceding content) into a TaskSignature via keyword/punctuation heuristics, deterministic and zero-LLM."""

from __future__ import annotations

from .types import TaskSignature, Mode, Register, Intensity, Position, Element


class TaskClassifier:
    """Builds a TaskSignature from a writing prompt — detects mode/register/intensity/position/elements by keyword cues (override dict wins per-dimension)."""

    def __init__(self):
        pass

    def classify(self, prompt: str, preceding_content: str | None = None,
                 override: dict | None = None) -> TaskSignature:
        """
        Produce a TaskSignature. Steps:
        1. Combine prompt + preceding_content into analysis_text
        2. For each dimension, check if override provides it; if so use it
        3. Otherwise run the keyword detection method
        4. Return TaskSignature
        """
        analysis_text = prompt
        if preceding_content:
            analysis_text = preceding_content + "\n" + prompt
        override = override or {}

        mode = Mode(override["mode"]) if "mode" in override else self._detect_mode(prompt)
        register = Register(override["register"]) if "register" in override else self._detect_register(analysis_text)
        intensity = Intensity(override["intensity"]) if "intensity" in override else self._detect_intensity(analysis_text)
        position = Position(override["position"]) if "position" in override else self._detect_position(prompt)
        elements = [Element(e) for e in override["elements"]] if "elements" in override else self._detect_elements(analysis_text)

        return TaskSignature(mode=mode, register=register, intensity=intensity,
                             position=position, elements=elements)

    def _detect_mode(self, prompt: str) -> Mode:
        # Only looks at the prompt (not preceding content)
        prompt_lower = prompt.lower()
        # Order matters: check specific modes before DRAFTING since
        # "rewrite" contains "write", etc.
        keyword_map = [
            (Mode.REVISING, ["rewrite", "revise", "fix", "redo", "rework", "improve"]),
            (Mode.EXPANDING, ["expand", "more detail", "flesh out", "elaborate", "develop"]),
            (Mode.CONDENSING, ["tighten", "shorten", "condense", "trim", "cut down"]),
            (Mode.CONTINUING, ["continue", "keep going", "next", "what happens", "go on"]),
            (Mode.DRAFTING, ["write", "draft", "create", "compose", "generate"]),
        ]
        for mode, keywords in keyword_map:
            for kw in keywords:
                if kw in prompt_lower:
                    return mode
        return Mode.DRAFTING

    def _detect_register(self, text: str) -> Register:
        text_lower = text.lower()
        keyword_map = {
            Register.ACTION: ["fight", "slash", "dodge", "crash", "run", "hit", "attack",
                              "sword", "punch", "block", "sprint", "chase", "explode"],
            Register.DIALOGUE: ["said", "asked", "replied", "whispered", "shouted", "spoke", "told"],
            Register.INTROSPECTION: ["thought", "wondered", "realized", "felt", "remembered",
                                     "considered", "reflected", "mused", "mind", "memory"],
            Register.DESCRIPTION: ["sky", "room", "forest", "light", "shadow", "landscape",
                                   "building", "wore", "looked like", "appeared", "color"],
            Register.EXPOSITION: ["because", "history", "centuries", "tradition", "system",
                                  "according", "known as", "origin", "explanation"],
            Register.TRANSITION: ["meanwhile", "later", "elsewhere", "the next day",
                                  "hours passed", "by the time", "when they arrived"],
        }
        # Also check for quotation marks as dialogue indicator
        counts = {}
        for register, keywords in keyword_map.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if register == Register.DIALOGUE and '"' in text:
                count += 3  # Strong signal
            counts[register] = count

        best = max(counts, key=counts.get)
        if counts[best] > 0:
            return best
        return Register.DESCRIPTION

    def _detect_intensity(self, text: str) -> Intensity:
        text_lower = text.lower()
        words = text.split()

        high_keywords = ["death", "die", "kill", "blood", "scream", "desperate",
                         "terror", "agony", "panic", "fury"]
        low_keywords = ["calm", "quiet", "gentle", "peace", "still", "soft",
                        "slow", "silence", "rest", "ease"]

        high_count = sum(1 for kw in high_keywords if kw in text_lower)
        high_count += text.count("!")
        high_count += sum(1 for w in words if w.isupper() and len(w) > 1)

        low_count = sum(1 for kw in low_keywords if kw in text_lower)

        if high_count > 3 or (low_count > 0 and high_count > low_count * 2):
            return Intensity.HIGH
        if low_count > 3 or (high_count > 0 and low_count > high_count * 2):
            return Intensity.LOW
        if high_count > 0 and low_count == 0:
            if high_count > 3:
                return Intensity.HIGH
        if low_count > 0 and high_count == 0:
            if low_count > 3:
                return Intensity.LOW
        return Intensity.MEDIUM

    def _detect_position(self, prompt: str) -> Position:
        prompt_lower = prompt.lower()
        keyword_map = {
            Position.OPENING: ["opening", "begin", "start", "first scene", "chapter 1", "introduce"],
            Position.RISING: ["building", "escalate", "tension", "rising"],
            Position.CLIMAX: ["climax", "peak", "confrontation", "final battle", "showdown"],
            Position.FALLING: ["aftermath", "fallout", "consequences", "come down"],
            Position.CLOSING: ["ending", "final", "closing", "last scene", "conclude", "wrap up"],
        }
        for position, keywords in keyword_map.items():
            for kw in keywords:
                if kw in prompt_lower:
                    return position
        return Position.MID

    def _detect_elements(self, text: str) -> list[Element]:
        text_lower = text.lower()
        keyword_map = {
            Element.PHYSICAL_ACTION: ["fight", "run", "hit", "grab", "dodge", "jump", "throw"],
            Element.DIALOGUE: ["said", "asked", "replied", "whispered", "shouted"],
            Element.INTERNAL_THOUGHT: ["thought", "wondered", "realized", "felt"],
            Element.ENVIRONMENTAL_DESCRIPTION: ["room", "sky", "forest", "light", "building"],
            Element.CHARACTER_DESCRIPTION: ["wore", "tall", "hair", "eyes", "scar", "face"],
            Element.EMOTIONAL_BEAT: ["tears", "smiled", "anger", "grief", "joy", "fear", "love"],
            Element.SENSORY_DETAIL: ["smell", "taste", "sound", "cold", "heat", "pain", "texture"],
        }
        # Also check quotation marks for dialogue
        detected = []
        for element, keywords in keyword_map.items():
            matches = sum(1 for kw in keywords if kw in text_lower)
            if element == Element.DIALOGUE and '"' in text:
                matches += 1
            if matches >= 1:
                detected.append(element)

        if not detected:
            detected = [Element.ENVIRONMENTAL_DESCRIPTION]
        return detected
