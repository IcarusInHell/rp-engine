"""Entity extraction from raw text — no LLM, pure heuristics.

Tokenizes user + assistant messages, matches against known entity aliases
and keywords, and detects which NPCs are active (speaking/acting) vs.
merely referenced (mentioned in thoughts/memories).
"""

from __future__ import annotations

import logging
import re

from rp_engine.database import Database
from rp_engine.models.context import (
    DetectedNPC,
    ExtractionResult,
    MatchedEntity,
)
from rp_engine.utils.stemmer import stem, tokenize

logger = logging.getLogger(__name__)

# Dialogue markers — NPC name within ~200 chars of these is likely active
_DIALOGUE_MARKERS = re.compile(
    r"""(?:"|"|"|said|asked|replied|whispered|murmured|snapped|"""
    r"""growled|muttered|hissed|called|shouted|demanded|laughed|sighed)""",
    re.IGNORECASE,
)

# Dual-POV header pattern: === Name === or ### Name ###
_POV_HEADER = re.compile(r"(?:^|\n)\s*(?:===|###)\s*(.+?)\s*(?:===|###)")


class EntityExtractor:
    """Extract entities and detect active NPCs from raw text."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def extract(
        self,
        user_message: str,
        last_response: str | None,
        rp_folder: str,
    ) -> ExtractionResult:
        """Full extraction pipeline."""
        combined = user_message
        if last_response:
            combined = f"{last_response}\n{user_message}"

        # Tokenize into words + n-grams
        tokens = self._tokenize(combined)

        # Match against known entities
        matched = await self._match_entities(tokens, rp_folder)

        # Load NPC-type entities for active/referenced detection
        npc_entities = await self._load_npc_entities(rp_folder)

        # Detect active NPCs in last_response (who was acting/speaking)
        active: list[DetectedNPC] = []
        if last_response:
            active = self._detect_active_npcs(last_response, npc_entities)

        active_ids = {n.entity_id for n in active}

        # Detect referenced NPCs (in combined text but not active)
        referenced = self._detect_referenced_npcs(combined, npc_entities, active_ids)

        # Detect location mentions
        locations = await self._detect_locations(tokens, rp_folder)

        return ExtractionResult(
            matched_entities=matched,
            active_npcs=active,
            referenced_npcs=referenced,
            detected_locations=locations,
        )

    def _tokenize(self, text: str) -> list[str]:
        """Generate single words + bigrams + trigrams, lowercased.

        Uses the shared ``utils.stemmer.tokenize`` for word splitting (the same
        splitter scene_classifier uses); n-grams are left as raw surface phrases
        so multi-word keyword/alias keys still match exactly.
        """
        words = [w for w in tokenize(text) if len(w) > 1]

        tokens = set(words)
        for i in range(len(words) - 1):
            tokens.add(f"{words[i]} {words[i + 1]}")
        for i in range(len(words) - 2):
            tokens.add(f"{words[i]} {words[i + 1]} {words[i + 2]}")

        return list(tokens)

    async def _match_entities(
        self, tokens: list[str], rp_folder: str
    ) -> list[MatchedEntity]:
        """Batch match tokens against entity_aliases and entity_keywords."""
        # Load all aliases/keywords for this RP into memory (small dataset)
        alias_rows = await self.db.fetch_all(
            """SELECT ea.alias, ea.entity_id
               FROM entity_aliases ea
               JOIN story_cards sc ON ea.entity_id = sc.id
               WHERE sc.rp_folder = ?""",
            [rp_folder],
        )
        keyword_rows = await self.db.fetch_all(
            """SELECT ek.keyword, ek.entity_id
               FROM entity_keywords ek
               JOIN story_cards sc ON ek.entity_id = sc.id
               WHERE sc.rp_folder = ?""",
            [rp_folder],
        )

        # Build lookup dicts
        alias_map: dict[str, list[str]] = {}
        for row in alias_rows:
            alias_map.setdefault(row["alias"], []).append(row["entity_id"])

        # Stem single-word keyword keys so both sides match morphology
        # (``swords`` text hits a ``sword`` keyword). Multi-word phrase keys are
        # left exact — stemming a phrase is meaningless. Aliases/names are NOT
        # stemmed below (proper nouns: ``Ross`` must not become ``Ros``).
        keyword_map: dict[str, list[str]] = {}
        for row in keyword_rows:
            kw = row["keyword"]
            key = stem(kw) if " " not in kw else kw
            keyword_map.setdefault(key, []).append(row["entity_id"])

        # Match tokens
        results: dict[str, MatchedEntity] = {}
        token_set = set(tokens)

        for token in token_set:
            # Check aliases (score 0.8)
            if token in alias_map:
                for eid in alias_map[token]:
                    if eid not in results or results[eid].score < 0.8:
                        results[eid] = MatchedEntity(
                            entity_id=eid,
                            match_source="alias",
                            match_term=token,
                            score=0.8,
                        )

            # Check keywords (score 0.5) — stem single-word tokens to match the
            # stemmed keyword index; multi-word n-grams stay exact.
            kw_token = stem(token) if " " not in token else token
            if kw_token in keyword_map:
                for eid in keyword_map[kw_token]:
                    if eid not in results or results[eid].score < 0.5:
                        results[eid] = MatchedEntity(
                            entity_id=eid,
                            match_source="keyword",
                            match_term=token,
                            score=0.5,
                        )

        # Check direct entity_id / name matches (score 1.0)
        card_rows = await self.db.fetch_all(
            "SELECT id, name FROM story_cards WHERE rp_folder = ?",
            [rp_folder],
        )
        for row in card_rows:
            name_lower = row["name"].lower()
            if name_lower in token_set:
                eid = row["id"]
                if eid not in results or results[eid].score < 1.0:
                    results[eid] = MatchedEntity(
                        entity_id=eid,
                        match_source="name",
                        match_term=name_lower,
                        score=1.0,
                    )

        return sorted(results.values(), key=lambda m: m.score, reverse=True)

    async def _load_npc_entities(self, rp_folder: str) -> list[dict]:
        """Load NPC/character entities for active detection."""
        return await self.db.fetch_all(
            """SELECT id, name, card_type, importance
               FROM story_cards
               WHERE rp_folder = ? AND card_type IN ('character', 'npc')""",
            [rp_folder],
        )

    def _detect_active_npcs(
        self, text: str, npc_entities: list[dict]
    ) -> list[DetectedNPC]:
        """Detect NPCs that are actively speaking/acting in the text.

        Heuristics:
        - NPC name within ~200 chars of dialogue markers
        - NPC name at start of a sentence (action subject)
        - NPC in === Name === dual-POV header
        """
        active: dict[str, DetectedNPC] = {}

        # Check POV headers first
        for match in _POV_HEADER.finditer(text):
            header_name = match.group(1).strip().lower()
            for npc in npc_entities:
                if npc["name"].lower() == header_name:
                    active[npc["id"]] = DetectedNPC(
                        entity_id=npc["id"],
                        name=npc["name"],
                        detection_reason="pov_header",
                    )

        # Check dialogue proximity and action subjects
        text_lower = text.lower()
        for npc in npc_entities:
            if npc["id"] in active:
                continue

            name_lower = npc["name"].lower()
            # Also check first name only
            first_name = name_lower.split()[0] if " " in name_lower else name_lower

            for name_variant in {name_lower, first_name}:
                if name_variant not in text_lower:
                    continue

                # Find all occurrences
                start = 0
                while True:
                    idx = text_lower.find(name_variant, start)
                    if idx == -1:
                        break
                    start = idx + 1

                    # Check dialogue proximity (200 chars around name)
                    window_start = max(0, idx - 200)
                    window_end = min(len(text), idx + len(name_variant) + 200)
                    window = text[window_start:window_end]

                    if _DIALOGUE_MARKERS.search(window):
                        active[npc["id"]] = DetectedNPC(
                            entity_id=npc["id"],
                            name=npc["name"],
                            detection_reason="dialogue",
                        )
                        break

                    # Check if name starts a sentence (action subject)
                    before = text[max(0, idx - 2) : idx].strip()
                    if not before or before[-1] in ".!?\n":
                        active[npc["id"]] = DetectedNPC(
                            entity_id=npc["id"],
                            name=npc["name"],
                            detection_reason="action_subject",
                        )
                        break

                if npc["id"] in active:
                    break

        return list(active.values())

    def _detect_referenced_npcs(
        self,
        text: str,
        npc_entities: list[dict],
        active_ids: set[str],
    ) -> list[DetectedNPC]:
        """NPCs found in text but NOT flagged as active."""
        referenced: list[DetectedNPC] = []
        text_lower = text.lower()

        for npc in npc_entities:
            if npc["id"] in active_ids:
                continue

            name_lower = npc["name"].lower()
            first_name = name_lower.split()[0] if " " in name_lower else name_lower

            for name_variant in {name_lower, first_name}:
                if name_variant in text_lower:
                    referenced.append(
                        DetectedNPC(
                            entity_id=npc["id"],
                            name=npc["name"],
                            detection_reason="mentioned",
                        )
                    )
                    break

        return referenced

    async def _detect_locations(
        self, tokens: list[str], rp_folder: str
    ) -> list[str]:
        """Match tokens against known location entities."""
        loc_rows = await self.db.fetch_all(
            """SELECT sc.name
               FROM story_cards sc
               WHERE sc.rp_folder = ? AND sc.card_type = 'location'""",
            [rp_folder],
        )
        token_set = set(tokens)
        locations: list[str] = []
        for row in loc_rows:
            if row["name"].lower() in token_set:
                locations.append(row["name"])

        # Also check location aliases
        alias_rows = await self.db.fetch_all(
            """SELECT ea.alias, sc.name
               FROM entity_aliases ea
               JOIN story_cards sc ON ea.entity_id = sc.id
               WHERE sc.rp_folder = ? AND sc.card_type = 'location'""",
            [rp_folder],
        )
        seen = set(locations)
        for row in alias_rows:
            if row["alias"] in token_set and row["name"] not in seen:
                locations.append(row["name"])
                seen.add(row["name"])

        return locations
