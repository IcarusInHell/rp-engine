"""LLM-based response analysis — extracts state from RP narrative.

Ported from .claude/hooks/response-analyzer.js (buildAnalysisPrompt, lines 318-495).
Calls the LLM to extract characters, relationships, events, scene state, etc.
"""

from __future__ import annotations

import json
import logging

from rp_engine.database import Database
from rp_engine.models.analysis import AnalysisLLMResult
from rp_engine.services.llm_client import LLMClient
from rp_engine.utils.lru_cache import LRUCache

logger = logging.getLogger(__name__)

INVALID_NAMES = frozenset([
    "man", "woman", "person", "character", "stranger", "figure", "someone",
    "narrator", "claude", "he", "she", "they", "the man", "the woman",
    "unknown", "unnamed", "unknown man", "unnamed man", "other character",
    "the stranger", "ai", "assistant",
])


class ResponseAnalyzer:
    """Analyze exchange text via LLM to extract structured state."""

    def __init__(self, db: Database, llm: LLMClient) -> None:
        self.db = db
        self._alias_cache: LRUCache[str, dict[str, str]] = LRUCache(maxsize=16)
        self.llm = llm

    async def analyze(
        self,
        exchange_id: int,
        user_message: str,
        assistant_response: str,
        rp_folder: str,
        branch: str = "main",
        custom_schemas: list[dict] | None = None,
    ) -> AnalysisLLMResult:
        """Run LLM analysis on an exchange and return structured results."""
        # Load recent exchanges for context
        recent = await self.db.fetch_all(
            """SELECT exchange_number, user_message, assistant_response
               FROM exchanges
               WHERE rp_folder = ? AND branch = ? AND id <= ?
               ORDER BY exchange_number DESC LIMIT 5""",
            [rp_folder, branch, exchange_id],
        )
        recent.reverse()  # Chronological order

        # Build alias map for name resolution
        alias_map = await self._build_alias_map(rp_folder)

        # Build prompt
        prompt = self._build_prompt(recent, custom_schemas=custom_schemas)

        # Call LLM
        try:
            response = await self.llm.generate(
                messages=[{"role": "user", "content": prompt}],
                model=self.llm.models.response_analysis,
                temperature=0.3,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )
            result = self._parse_response(response.content, alias_map)
            result._raw_response = response.content
            result._model_used = self.llm.models.response_analysis
            return result
        except Exception as e:
            logger.error("Analysis LLM call failed for exchange %d: %s", exchange_id, e)
            return AnalysisLLMResult()

    async def _build_alias_map(self, rp_folder: str) -> dict[str, str]:
        """Build {alias_lower: canonical_name} from entity_aliases + story_cards.

        Cached per rp_folder; invalidated via invalidate_alias_cache().
        """
        cached = self._alias_cache.get(rp_folder)
        if cached is not None:
            return cached
        rows = await self.db.fetch_all(
            """SELECT ea.alias, sc.name
               FROM entity_aliases ea
               JOIN story_cards sc ON ea.entity_id = sc.id
               WHERE sc.rp_folder = ? AND sc.card_type IN ('character', 'npc')""",
            [rp_folder],
        )
        mapping: dict[str, str] = {}
        for row in rows:
            mapping[row["alias"].lower()] = row["name"]
        self._alias_cache.put(rp_folder, mapping)
        return mapping

    def invalidate_alias_cache(self, rp_folder: str | None = None) -> None:
        """Clear alias cache. Called when cards are reindexed."""
        if rp_folder:
            self._alias_cache.pop(rp_folder, None)
        else:
            self._alias_cache.clear()

    def _build_prompt(self, exchanges: list[dict], custom_schemas: list[dict] | None = None) -> str:
        """Build the analysis prompt — ported verbatim from response-analyzer.js."""
        conversation_parts: list[str] = []
        for i, ex in enumerate(exchanges, 1):
            conversation_parts.append(
                f"\nExchange {i}:\n"
                f"USER: {ex['user_message']}\n"
                f"NARRATOR: {ex['assistant_response']}\n"
            )
        conversation_text = "\n---\n".join(conversation_parts)

        return f"""You are analyzing a roleplay conversation to extract structured information.

IMPORTANT: The "NARRATOR" label indicates the AI writing the story - this is NOT a character named "Claude" or "Narrator". The narrator writes for NPCs (like Dante, Elysia, etc.) and describes scenes. Extract the ACTUAL CHARACTER NAMES from the narrative content, not the label "NARRATOR" or "Claude".

Analyze the following {len(exchanges)} exchanges and identify:

1. PLOT THREADS: Which plot threads were mentioned, developed, or resolved?
   - Thread name/ID
   - Status: introduced, developing, progressed, resolved, stalled
   - Key developments

2. MEMORIES: Important scenes or moments worth preserving
   - Description of the moment
   - Why it's significant
   - Characters involved

3. KNOWLEDGE BOUNDARIES: Information shared between characters
   - Who learned what
   - From whom (if applicable)
   - Type: secret revealed, information shared, observation made

4. NEW ENTITIES:
   - Characters: name, role, first appearance
   - Locations: name, description, first mention
   - Objects/Concepts: name, significance

5. RELATIONSHIP DYNAMICS:
   - Characters involved - CRITICAL RULES:
     * Use their ACTUAL CHARACTER NAMES from the story (e.g., "Dante Moretti", "Lilith Graves", "Elysia")
     * NEVER use "Claude", "Narrator", "AI", or "Assistant"
     * NEVER use generic descriptors like "Man", "The Man", "Unknown Man", "Unnamed Man", "Other Character", "The Stranger", "Figure", "Person"
     * If a character is referred to by a nickname (like "Beasty" for Dante), use their REAL name, not the nickname
     * If you can't identify the character's actual name, SKIP that entry entirely
     * ORDER MATTERS — trust is DIRECTIONAL. List exactly two names as [TRUSTER, TRUSTED]:
       the FIRST is the character whose feelings changed (the one doing the trusting/distrusting),
       the SECOND is the character they now trust more or less. Example: if Dante earns Lilith's
       trust by protecting her, write ["Lilith Graves", "Dante Moretti"] (Lilith → Dante), NOT the
       reverse. For a mutual change, emit two separate entries, one in each direction.
   - Change type - choose carefully:
     * trust_increase: Character genuinely helped, protected, showed vulnerability, or earned respect
     * trust_decrease: Character ACTUALLY BETRAYED, lied maliciously, or broke a promise (rare!)
     * conflict_introduced: Tension, power struggles, threats, arguments (this is NOT trust decrease!)
     * conflict_resolved: Tension de-escalated, understanding reached
     * alliance_formed: Characters agreed to work together
     * alliance_broken: Alliance ended
   - IMPORTANT: Flirtatious banter, power plays, teasing threats, and sexual tension are conflict_introduced, NOT trust_decrease. Trust decrease requires actual betrayal or malicious deception.
   - Evidence from text

6. NPC INTERACTIONS (Important for behavioral tracking):
   - Which NPCs appeared in the scene (use ACTUAL NAMES like "Dante Moretti", not "The Man" or "Beasty")
   - What actions/behaviors they displayed
   - Their apparent emotional state or motivation
   - Any trust-affecting moments (helped, betrayed, protected, threatened, etc.)
   - Whether their behavior seemed consistent with their role

7. STORY STATE (Current state of the world - IMPORTANT):
   - Character locations: Where is each character at the END? (Use ACTUAL NAMES only, never "Man" or "Unknown Character")
   - Character conditions: Any injuries, status effects, emotional states?
   - Scene context: Current location, time of day, atmosphere/mood
   - Significant events: What notable things happened that should be tracked?

8. SCENE SIGNIFICANCE (Flag card-worthy moments):
   - Score 1-10 (1=mundane, 5=notable, 8=major revelation, 10=pivotal)
   - Categories (one or more): secret, backstory, knowledge_shift, emotional_moment, future_plan, relationship_change, plot_development, character_growth
   - Brief: 1-2 sentence description of what's significant
   - Suggested card types: secret, memory, knowledge, lore
   - In-story timestamp if present in the exchanges

{"" if not custom_schemas else self._build_custom_state_prompt_section(custom_schemas)}Conversation:
{conversation_text}

Respond ONLY with valid JSON in this exact format:
{{
  "plotThreads": [
    {{
      "threadName": "string",
      "status": "introduced|developing|progressed|resolved|stalled",
      "development": "string description",
      "evidence": "relevant quote from conversation"
    }}
  ],
  "memories": [
    {{
      "description": "string",
      "significance": "string",
      "characters": ["string"],
      "timestamp": "string (from exchange)"
    }}
  ],
  "knowledgeBoundaries": [
    {{
      "who": "character name",
      "learned": "what information",
      "from": "source (character name or 'observation')",
      "type": "secret_revealed|information_shared|observation_made",
      "evidence": "relevant quote"
    }}
  ],
  "newEntities": {{
    "characters": [
      {{
        "name": "string",
        "role": "string",
        "firstAppearance": "exchange number"
      }}
    ],
    "locations": [
      {{
        "name": "string",
        "description": "string",
        "firstMention": "exchange number"
      }}
    ],
    "concepts": [
      {{
        "name": "string",
        "significance": "string"
      }}
    ]
  }},
  "relationshipDynamics": [
    {{
      "characters": ["string", "string"],
      "changeType": "trust_increase|trust_decrease|conflict_introduced|conflict_resolved|alliance_formed|alliance_broken",
      "evidence": "relevant quote or description"
    }}
  ],
  "npcInteractions": [
    {{
      "npcName": "string",
      "appearedInExchange": [1, 2],
      "actions": ["string description of what they did"],
      "emotionalState": "string (e.g., hostile, curious, protective, indifferent)",
      "trustMoments": [
        {{
          "type": "trust_building|trust_damaging|neutral",
          "action": "what they did",
          "withCharacter": "who it affected",
          "significance": "low|medium|high"
        }}
      ],
      "behaviorNotes": "string - any notable behavior patterns or inconsistencies"
    }}
  ],
  "storyState": {{
    "characters": {{
      "CharacterName": {{
        "location": "string - where they are at end of scene",
        "conditions": ["string - injuries, status effects, etc."],
        "emotionalState": "string - current emotional state"
      }}
    }},
    "sceneContext": {{
      "location": "string - current scene location",
      "timeOfDay": "string - morning/afternoon/evening/night/unknown",
      "mood": "string - scene atmosphere"
    }},
    "significantEvents": [
      {{
        "event": "string - what happened",
        "characters": ["who was involved"],
        "significance": "low|medium|high"
      }}
    ]
  }},
  "sceneSignificance": {{
    "score": 0,
    "categories": [],
    "brief": "string or null",
    "suggestedCardTypes": [],
    "inStoryTimestamp": "string or null",
    "characters": ["who was involved"]
  }},
  "customStateChanges": [
    {{
      "schemaName": "string (must match a tracked schema name)",
      "entity": "string (character name or empty for scene-level)",
      "action": "set|add|remove|subtract",
      "value": "the new/changed value"
    }}
  ]
}}

IMPORTANT: Only include items that are CLEARLY present in the conversation. If a category has no entries, use an empty array/object. Do not infer or speculate beyond what's explicitly in the text. For storyState.characters, only include characters who actually appeared in the exchanges."""

    @staticmethod
    def _build_custom_state_prompt_section(custom_schemas: list[dict]) -> str:
        """Build the custom state extraction section for the analysis prompt."""
        schema_lines = []
        for s in custom_schemas:
            entity_note = f" (per {s['belongs_to']})" if s.get("belongs_to") else " (scene-level)"
            schema_lines.append(f"- {s['name']} ({s['data_type']}){entity_note}: {s.get('description', '')}")

        return f"""
9. CUSTOM STATE CHANGES:
   The following custom state fields are being tracked. If the narrative indicates any changed, include them:
   {chr(10).join('   ' + line for line in schema_lines)}

   Actions by data type:
   - number: "set" (absolute), "add" (increase), "subtract" (decrease)
   - text: "set"
   - list: "add" (append item), "remove" (remove item), "set" (replace entire list)
   - object: "set"

   ONLY include changes that are CLEARLY stated in the narrative. Do not infer.

"""

    def _parse_response(
        self, content: str, alias_map: dict[str, str]
    ) -> AnalysisLLMResult:
        """Parse LLM JSON response, resolve names, filter invalids."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse analysis JSON: %s", e)
            return AnalysisLLMResult()

        # Resolve aliases in character names throughout
        data = self._resolve_names(data, alias_map)

        try:
            return AnalysisLLMResult.model_validate(data)
        except Exception as e:
            logger.error("Failed to validate analysis result: %s", e)
            return AnalysisLLMResult()

    def _resolve_names(self, data: dict, alias_map: dict[str, str]) -> dict:
        """Recursively resolve nicknames/aliases to canonical names and filter invalids."""
        # Resolve relationship dynamics characters
        for rd in data.get("relationshipDynamics", []):
            rd["characters"] = [
                self._resolve_name(c, alias_map) for c in rd.get("characters", [])
                if self._resolve_name(c, alias_map) is not None
            ]

        # Resolve storyState character keys
        story_state = data.get("storyState", {})
        if "characters" in story_state and isinstance(story_state["characters"], dict):
            new_chars = {}
            for name, state in story_state["characters"].items():
                resolved = self._resolve_name(name, alias_map)
                if resolved:
                    new_chars[resolved] = state
            story_state["characters"] = new_chars

        # Resolve event characters
        for event in story_state.get("significantEvents", []):
            event["characters"] = [
                self._resolve_name(c, alias_map) for c in event.get("characters", [])
                if self._resolve_name(c, alias_map) is not None
            ]

        # Resolve NPC interaction names
        for npc in data.get("npcInteractions", []):
            resolved = self._resolve_name(npc.get("npcName", ""), alias_map)
            if resolved:
                npc["npcName"] = resolved

        # Resolve scene significance characters
        sig = data.get("sceneSignificance", {})
        if "characters" in sig:
            sig["characters"] = [
                self._resolve_name(c, alias_map) for c in sig.get("characters", [])
                if self._resolve_name(c, alias_map) is not None
            ]

        # Resolve custom state change entity names
        for csc in data.get("customStateChanges", []):
            if csc.get("entity"):
                resolved = self._resolve_name(csc["entity"], alias_map)
                csc["entity"] = resolved or csc["entity"]

        return data

    @staticmethod
    def _resolve_name(name: str, alias_map: dict[str, str]) -> str | None:
        """Resolve a name via alias map, filter invalid names."""
        if not name:
            return None
        name_lower = name.strip().lower()
        if name_lower in INVALID_NAMES:
            return None
        # Check alias map
        if name_lower in alias_map:
            return alias_map[name_lower]
        # Return original (with title case preserved)
        return name.strip()
