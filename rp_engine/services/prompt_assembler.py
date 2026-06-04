"""Prompt assembler — builds LLM-ready message lists from static + dynamic context.

Combines three inputs:
1. Static system prompt (writing rules, guidelines, NPC framework, output format)
2. Dynamic context (scene state, NPC briefs, plot threads, lore)
3. Recent exchanges (conversation history)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rp_engine.config import ChatConfig, PromptConfig, get_config
from rp_engine.constants.prompt_guidance import (
    RESPONSE_LENGTH_GUIDANCE,
    SCENE_PACING_GUIDANCE,
    build_content_boundaries,
    build_pov_section,
    build_user_narrative_guidance,
)
from rp_engine.database import Database
from rp_engine.models.context import ContextResponse
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.utils.dialogue_parser import example_dialogue_messages
from rp_engine.utils.frontmatter import parse_file
from rp_engine.utils.json_helpers import safe_parse_json
from rp_engine.utils.provenance import record_drop, record_injected
from rp_engine.utils.token_utils import estimate_messages_tokens, resolve_token_counter

logger = logging.getLogger(__name__)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# All reorderable depth-0 section names (static + dynamic). Used to tell an
# absent-this-turn section (or one injected at depth>0) from a genuine typo in a
# user's ``prompt_order`` list. Static names match ``_assemble_static_units``;
# dynamic names match ``_build_dynamic_sections``.
_STATIC_SECTION_NAMES = frozenset({
    "writing_principles", "rp_guidelines", "npc_framework", "output_format",
})
_DYNAMIC_SECTION_NAMES = frozenset({
    "scene_context", "character_states", "custom_state", "npc_briefs",
    "knowledge_boundaries", "plot_threads", "relevant_documents",
    "triggered_notes", "world_info", "past_exchanges", "card_gaps", "warnings",
    "writing_constraints",
})
_ALL_SECTION_NAMES = _STATIC_SECTION_NAMES | _DYNAMIC_SECTION_NAMES

# Canonical depth-0 section order — static units (``_assemble_static_units``)
# followed by dynamic units (``_build_dynamic_sections``). This is what a
# ``prompt_order`` of None reproduces; exposed for the /api/prompt/order GET.
DEFAULT_PROMPT_ORDER: list[str] = [
    "writing_principles", "rp_guidelines", "npc_framework", "output_format",
    "scene_context", "character_states", "custom_state", "npc_briefs",
    "knowledge_boundaries", "plot_threads", "relevant_documents",
    "triggered_notes", "world_info", "past_exchanges", "card_gaps", "warnings",
    "writing_constraints",
]


def _strip_comments(text: str) -> str:
    """Remove HTML comments from markdown text."""
    return _COMMENT_RE.sub("", text).strip()


class PromptAssembler:
    """Assembles LLM-ready prompts from static + dynamic context + exchanges."""

    def __init__(
        self,
        vault_root: Path,
        db: Database,
        guidelines_service: GuidelinesService,
        config: ChatConfig | None = None,
        ancestry_resolver: AncestryResolver | None = None,
        prompt_config: PromptConfig | None = None,
    ) -> None:
        self.vault_root = vault_root
        self.db = db
        self.guidelines_service = guidelines_service
        self._config_override: ChatConfig | None = config
        self.ancestry_resolver = ancestry_resolver
        self._prompt_config_override: PromptConfig | None = prompt_config

    @property
    def config(self) -> ChatConfig:
        """Read chat config dynamically so hot-reloaded changes take effect.

        Tests can pass a custom ChatConfig at construction to override.
        Production code passes None; the property reads from get_config().
        """
        if self._config_override is not None:
            return self._config_override
        return get_config().chat

    @property
    def prompt_config(self) -> PromptConfig:
        """Read prompt config (injection/token-budget/example-dialogue) dynamically.

        Mirrors ``config``: tests pass a PromptConfig at construction; production
        passes None and the property reads the hot-reloaded ``get_config().prompt``.
        """
        if self._prompt_config_override is not None:
            return self._prompt_config_override
        return get_config().prompt

    # ------------------------------------------------------------------
    # Static section builders (extracted from routers/context.py)
    # ------------------------------------------------------------------

    def _build_writing_section(self) -> dict:
        """Extract key sections from the writing guide into a structured dict."""
        section: dict = {
            "core_pillars": [
                "Show Don't Tell: NEVER report emotions directly. ALWAYS show through physical reactions.",
                "Specificity Over Generality: AVOID generic descriptors. REQUIRE unique, memorable details.",
                "Strong Verbs: AVOID passive constructions. PREFER visceral, active verbs.",
                "Dynamic Rhythm: Match sentence structure to emotional state. Tension = short/clipped. Calm = longer/flowing.",
                "Friction and Consequence: Every significant action has physical cost. No painless combat or effortless movement.",
            ],
            "emotion_physical_map": {
                "Fear": "Throat tightening, shallow breath, cold sweat, trembling hands, stomach dropping",
                "Anger": "Jaw clenching, knuckles whitening, heat in chest, narrowed eyes, rigid posture",
                "Sadness": "Chest heaviness, burning behind eyes, tight throat, slumped shoulders",
                "Anxiety": "Restless fingers, racing heartbeat, skin prickling, dry mouth",
                "Shame": "Heat in face, gaze dropping, shoulders curling inward, urge to shrink",
                "Longing": "Ache in chest, reaching impulse, breath catching, hollow feeling",
                "Relief": "Shoulders dropping, breath releasing, tension draining, steadying",
            },
            "banned_patterns": [
                "Sequential pairs (', then'): Rewrite as fluid motion",
                "Vague interiority ('something' + verb): Name what's happening",
                "Anthropomorphized silence ('silence/air' + verb): Show effect through behavior",
                "Negation formula ('not X, but Y'): Commit to what it is",
                "Hedged reactions ('isn't quite'): Describe actual gesture",
                "Meta-narrative ('scene's not over'): Stay in character POV",
                "Atmospheric opening: Start with character in action, not weather",
                "Participle pileup: Max 2 ', [verb]ing' in a row",
            ],
            "ai_vocabulary": [
                "Abstract nouns: tapestry, landscape, interplay, intricacies, nuance, multifaceted, dynamics, framework, paradigm",
                "Verbs: delve, foster, garner, underscore, showcase, highlight, navigate (emotions), unpack (ideas)",
                "Adjectives: pivotal, crucial, vital, vibrant, intricate, profound, compelling, poignant, evocative, palpable",
                "Adverbs: seemingly, arguably, notably, importantly, ultimately, fundamentally, inherently, undeniably",
            ],
        }

        return section

    def _build_npc_framework_section(self) -> dict:
        """Return hardcoded NPC framework info (stable constants)."""
        return {
            "archetypes": {
                "POWER_HOLDER": "Authority figures, crime bosses, politicians. Expect deference, trade favors, punish disrespect.",
                "TRANSACTIONAL": "Deal-makers, fixers, brokers. Everything has a price. Loyal to profit.",
                "COMMON_PEOPLE": "Regular folks, bystanders, service workers. React realistically, avoid heroics.",
                "OPPOSITION": "Antagonists, rivals, enemies. Actively work against player goals.",
                "SPECIALIST": "Experts, doctors, hackers. Defined by competence. Speak in their field's language.",
                "PROTECTOR": "Bodyguards, loyal allies, mentors. Priority is safety of their charge.",
                "OUTSIDER": "Strangers, newcomers, unknowns. Limited knowledge, fresh perspective.",
            },
            "modifiers": [
                "OBSESSIVE", "SADISTIC", "PARANOID", "FANATICAL", "NARCISSISTIC",
                "SOCIOPATHIC", "ADDICTED", "HONOR_BOUND", "GRIEF_CONSUMED",
            ],
            "trust_stages": {
                "hostile": {"range": [-50, -36], "description": "Actively hostile, will harm if able"},
                "antagonistic": {"range": [-35, -21], "description": "Openly opposed, will obstruct"},
                "suspicious": {"range": [-20, -11], "description": "Distrustful, assumes worst"},
                "wary": {"range": [-10, -1], "description": "Cautious, keeps distance"},
                "neutral": {"range": [0, 9], "description": "Default state, no strong feelings"},
                "familiar": {"range": [10, 19], "description": "Friendly, will help within limits"},
                "trusted": {"range": [20, 34], "description": "Strong bond, will take risks"},
                "devoted": {"range": [35, 50], "description": "Deep loyalty, will sacrifice"},
            },
        }

    def _build_output_format_section(self) -> dict:
        """Return output format rules for RP responses."""
        return {
            "rules": [
                "RP responses contain ONLY narrative text.",
                "No meta commentary, OOC text, or system messages in the response.",
                "Strip thinking blocks and tool call content.",
                "No summaries or recaps unless explicitly requested.",
                "End on action, decision, or consequence — not summary or false profundity.",
            ],
            "response_structure": [
                "1. Acknowledge player's action with consequences.",
                "2. Advance scene with NPC reactions / environmental changes.",
                "3. Open opportunities for player's next action.",
                "4. End at natural pause point (not mid-action).",
            ],
        }

    # ------------------------------------------------------------------
    # Section assembly
    # ------------------------------------------------------------------

    def get_sections(self, rp_folder: str) -> dict:
        """Build all static sections for a system prompt."""
        sections: dict = {}

        # RP-specific guidelines (parsed first — toggles control other sections)
        frontmatter = None
        guidelines_path = self.vault_root / rp_folder / "RP State" / "Story_Guidelines.md"
        if guidelines_path.exists():
            frontmatter, body = parse_file(guidelines_path)
            if frontmatter:
                sections["rp_guidelines"] = {
                    "pov_mode": frontmatter.get("pov_mode"),
                    "pov_character": frontmatter.get("pov_character"),
                    "dual_characters": frontmatter.get("dual_characters", []),
                    "narrative_voice": frontmatter.get("narrative_voice"),
                    "tense": frontmatter.get("tense"),
                    "tone": frontmatter.get("tone"),
                    "scene_pacing": frontmatter.get("scene_pacing"),
                    "response_length": frontmatter.get("response_length"),
                    "integrate_user_narrative": frontmatter.get("integrate_user_narrative", False),
                    "preserve_user_details": frontmatter.get("preserve_user_details", False),
                    "sensitive_themes": frontmatter.get("sensitive_themes", []),
                    "hard_limits": frontmatter.get("hard_limits"),
                }
            if body and body.strip():
                cleaned = _strip_comments(body)
                if cleaned:
                    sections["rp_guidelines_body"] = cleaned

        # Auto-section toggles from frontmatter (default: all on)
        include_writing = frontmatter.get("include_writing_principles", True) if frontmatter else True
        include_npc = frontmatter.get("include_npc_framework", True) if frontmatter else True
        include_output = frontmatter.get("include_output_format", True) if frontmatter else True

        include_emotion_map = frontmatter.get("include_emotion_map", True) if frontmatter else True

        if include_writing:
            writing = self._build_writing_section()
            if not include_emotion_map:
                # Drop just the emotion→physical table, keep the rest of the section.
                writing.pop("emotion_physical_map", None)
            sections["writing_principles"] = writing
        if include_npc:
            sections["npc_framework"] = self._build_npc_framework_section()
        if include_output:
            sections["output_format"] = self._build_output_format_section()

        return sections

    def assemble_static_prompt(self, sections: dict) -> str:
        """Combine all static sections into a formatted system prompt string.

        Thin join over ``_assemble_static_units`` — kept for the static-preview
        endpoints, which always render the default order/skeleton.
        """
        return "\n".join(u for _, u in self._assemble_static_units(sections))

    def _assemble_static_units(self, sections: dict) -> list[tuple[str, str]]:
        """Render each static section as a ``(name, content)`` pair, in default order.

        Splitting the previously-monolithic ``parts`` list into per-section groups
        is byte-safe: the old output was one ``"\\n".join(parts)``, and
        ``"\\n".join`` is associative, so joining the per-section ``"\\n".join``
        strings with ``"\\n"`` reproduces it exactly. This lets ``build_system_prompt``
        reorder the named static units alongside the depth-0 dynamic ones
        (``prompt_order``) while the default order stays byte-identical
        (locked by the Phase 4 golden snapshot).
        """
        writing_parts: list[str] = []
        guide_parts: list[str] = []
        npc_parts: list[str] = []
        output_parts: list[str] = []

        # --- Writing Principles ---
        writing = sections.get("writing_principles")
        if writing:
            parts = writing_parts
            parts.append("# Writing Principles\n")
            parts.append("## Core Pillars")
            for pillar in writing.get("core_pillars", []):
                parts.append(f"- {pillar}")

            emotion_map = writing.get("emotion_physical_map", {})
            if emotion_map:
                parts.append("\n## Emotion to Physical Reaction Map")
                parts.append("Never report feelings directly. Always show through the body.\n")
                parts.append("| Emotion | Physical Manifestations |")
                parts.append("|---------|------------------------|")
                for emotion, manifestations in emotion_map.items():
                    parts.append(f"| **{emotion}** | {manifestations} |")

            banned = writing.get("banned_patterns", [])
            if banned:
                parts.append("\n## Banned Patterns")
                for pattern in banned:
                    parts.append(f"- {pattern}")

            ai_vocab = writing.get("ai_vocabulary", [])
            if ai_vocab:
                parts.append("\n## AI Vocabulary to Avoid")
                for category in ai_vocab:
                    parts.append(f"- {category}")

        # --- RP Guidelines ---
        rp_guide = sections.get("rp_guidelines")
        body = sections.get("rp_guidelines_body")
        if rp_guide or body:
            parts = guide_parts
            parts.append("\n\n# RP Guidelines\n")
            # Compact frontmatter metadata line (with expanded guidance)
            if rp_guide:
                meta_parts = []
                if rp_guide.get("pov_mode"):
                    meta_parts.append(f"POV: {rp_guide['pov_mode']}")
                if rp_guide.get("narrative_voice"):
                    meta_parts.append(f"Voice: {rp_guide['narrative_voice']}")
                if rp_guide.get("tense"):
                    meta_parts.append(f"Tense: {rp_guide['tense']}")

                # Expanded scene pacing
                pacing = rp_guide.get("scene_pacing")
                if pacing:
                    pacing_desc = SCENE_PACING_GUIDANCE.get(pacing, pacing)
                    meta_parts.append(f"Pacing: {pacing} ({pacing_desc})")

                # Expanded response length with word-count range
                length = rp_guide.get("response_length")
                if length:
                    length_desc = RESPONSE_LENGTH_GUIDANCE.get(length, length)
                    meta_parts.append(f"Length: {length} ({length_desc})")
                    # Dual mode note
                    if rp_guide.get("pov_mode") == "dual" and length != "variable":
                        meta_parts.append(
                            "Note: length target is per character section, not overall"
                        )

                if rp_guide.get("tone"):
                    tone = rp_guide["tone"]
                    meta_parts.append(f"Tone: {', '.join(tone) if isinstance(tone, list) else tone}")
                if rp_guide.get("dual_characters"):
                    meta_parts.append(f"Dual: {', '.join(rp_guide['dual_characters'])}")
                if meta_parts:
                    parts.append(" | ".join(meta_parts))

                # POV instructions (after metadata, before body)
                pov_section = build_pov_section(
                    pov_mode=rp_guide.get("pov_mode"),
                    pov_character=rp_guide.get("pov_character") or None,
                    dual_characters=rp_guide.get("dual_characters"),
                )
                if pov_section:
                    parts.append(f"\n{pov_section}")

                # Content boundaries (sensitive themes + hard limits)
                boundaries = build_content_boundaries(
                    sensitive_themes=rp_guide.get("sensitive_themes"),
                    hard_limits=rp_guide.get("hard_limits"),
                )
                if boundaries:
                    parts.append(f"\n{boundaries}")

                # User-narrative integration
                narrative_line = build_user_narrative_guidance(
                    integrate_user_narrative=bool(rp_guide.get("integrate_user_narrative")),
                    preserve_user_details=bool(rp_guide.get("preserve_user_details")),
                )
                if narrative_line:
                    parts.append(f"\n{narrative_line}")

            # Full body content (user's custom prompt instructions)
            if body:
                parts.append("\n")
                parts.append(body)

        # --- NPC Framework ---
        npc = sections.get("npc_framework")
        if npc:
            parts = npc_parts
            parts.append("\n\n# NPC Framework\n")
            parts.append("## Archetypes")
            for archetype, desc in npc.get("archetypes", {}).items():
                parts.append(f"- **{archetype}:** {desc}")

            modifiers = npc.get("modifiers", [])
            if modifiers:
                parts.append(f"\n## Behavioral Modifiers\n{', '.join(modifiers)}")

            trust = npc.get("trust_stages", {})
            if trust:
                parts.append("\n## Trust Stages\n")
                parts.append("| Stage | Range | Description |")
                parts.append("|-------|-------|-------------|")
                for stage, info in trust.items():
                    r = info["range"]
                    parts.append(f"| {stage} | {r[0]} to {r[1]} | {info['description']} |")

        # --- Output Format ---
        output = sections.get("output_format")
        if output:
            parts = output_parts
            parts.append("\n\n# Output Format\n")
            for rule in output.get("rules", []):
                parts.append(f"- {rule}")
            structure = output.get("response_structure", [])
            if structure:
                parts.append("\n## Response Structure")
                for step in structure:
                    parts.append(f"- {step}")

        # rp_guidelines and rp_guidelines_body render as one "rp_guidelines" unit.
        units: list[tuple[str, str]] = []
        for name, section_parts in (
            ("writing_principles", writing_parts),
            ("rp_guidelines", guide_parts),
            ("npc_framework", npc_parts),
            ("output_format", output_parts),
        ):
            if section_parts:
                units.append((name, "\n".join(section_parts)))
        return units

    # ------------------------------------------------------------------
    # Dynamic context injection (Phase B of Plan 29)
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        rp_folder: str,
        context_response: ContextResponse | None = None,
        exclude_sections: set[str] | None = None,
    ) -> str:
        """Build a complete system prompt: static sections + dynamic context.

        Static content goes at the top (cached in LLM attention).
        Dynamic content at the bottom (strongest recency attention).

        ``exclude_sections`` omits named dynamic sections from the system message
        (used by the injection-depth path in ``build_messages``, which renders
        those sections as in-history system messages instead). With no excludes,
        the output is byte-identical to the pre-Phase-4 assembler — locked by
        ``tests/test_prompt_injection.py::test_disabled_path_matches_golden_snapshot``.

        When the RP's ``Story_Guidelines.md`` defines ``prompt_order`` (Phase 5a),
        the **depth-0** named sections (static + non-injected dynamic) are emitted
        in that order and any depth-0 section omitted from the list is dropped.
        Sections pulled to depth > 0 (``exclude_sections``) are unaffected. With no
        ``prompt_order``, the natural order is preserved byte-identically.
        """
        sections = self.get_sections(rp_folder)

        # NPC framework is only useful when NPCs are actually in the scene.
        # get_sections already honors the include_npc_framework toggle; here we
        # add the runtime condition (non-empty npc_briefs) so the ~207-word block
        # is dropped from scenes with no active NPCs. The static-preview endpoints
        # call get_sections/assemble_static_prompt directly and intentionally keep
        # showing the full skeleton.
        if not (context_response and context_response.npc_briefs):
            sections.pop("npc_framework", None)

        static_units = self._assemble_static_units(sections)

        if context_response is None:
            return "\n".join(u for _, u in static_units)

        exclude = exclude_sections or set()
        dynamic_units = [
            (name, content)
            for name, content in self._build_dynamic_sections(context_response)
            if name not in exclude
        ]
        dynamic_names = {name for name, _ in dynamic_units}

        prompt_order = self._effective_prompt_order(rp_folder)
        if prompt_order is None:
            # Default order — boundary then dynamic, byte-identical to legacy.
            ordered = [*static_units, ("__boundary__", "\n\n---\n"), *dynamic_units]
        else:
            ordered = self._apply_prompt_order(
                static_units, dynamic_units, dynamic_names, prompt_order,
            )
        return "\n".join(u for _, u in ordered)

    def _effective_prompt_order(self, rp_folder: str) -> list[str] | None:
        """This RP's ``prompt_order`` list, or None (default order)."""
        if self.guidelines_service is None:
            return None
        guidelines = self.guidelines_service.get_guidelines(rp_folder)
        return guidelines.prompt_order if guidelines else None

    def _apply_prompt_order(
        self,
        static_units: list[tuple[str, str]],
        dynamic_units: list[tuple[str, str]],
        dynamic_names: set[str],
        prompt_order: list[str],
    ) -> list[tuple[str, str]]:
        """Reorder depth-0 sections per ``prompt_order``.

        Named sections are emitted in list order; sections present this turn but
        omitted from the list are dropped. Unknown names warn. The ``---`` dynamic
        boundary is re-inserted before the first dynamic section in the result, so
        the static/dynamic visual split survives reordering.
        """
        available = {name: content for name, content in [*static_units, *dynamic_units]}
        ordered: list[tuple[str, str]] = []
        boundary_emitted = False
        for name in prompt_order:
            if name not in available:
                if name not in _ALL_SECTION_NAMES:
                    logger.warning("Unknown section %r in prompt_order — skipping", name)
                # else: a valid section absent this turn (or injected at depth>0)
                continue
            if name in dynamic_names and not boundary_emitted:
                ordered.append(("__boundary__", "\n\n---\n"))
                boundary_emitted = True
            ordered.append((name, available[name]))
        if not boundary_emitted:
            # No dynamic section survived ordering — keep the boundary so the
            # output still marks the dynamic context region (legacy always emits
            # it when there's a context_response).
            ordered.append(("__boundary__", "\n\n---\n"))
        # Provenance: a section present this turn but omitted from prompt_order is
        # dropped (additive — the reorder logic above is unchanged).
        emitted = {name for name, _ in ordered if name != "__boundary__"}
        for name in available:
            if name not in emitted:
                record_drop(
                    "prompt.section_order", "section", "omitted_from_prompt_order",
                    item_id=name,
                )
        return ordered

    def _build_dynamic_sections(
        self, context_response: ContextResponse,
    ) -> list[tuple[str, str]]:
        """Render each dynamic context section as a ``(name, content)`` pair.

        One formatting source for two assembly modes: ``build_system_prompt``
        joins these in order under the static prompt (legacy / injection
        disabled), while ``build_messages`` distributes named sections to
        configured depths (injection enabled). ``name`` matches the keys in
        ``PromptConfig.injection.depths``. Empty sections are omitted (mirrors
        the legacy ``if`` guards), which keeps the joined output byte-identical.

        Content strings reproduce exactly what the legacy code appended to its
        shared ``dynamic`` list — ``"\\n".join(items)`` per section equals the
        flat-list join because ``"\\n".join`` is associative over non-empty parts.
        """
        sections: list[tuple[str, str]] = []

        # Current Scene
        if context_response.scene_state:
            ss = context_response.scene_state
            scene_parts: list[str] = []
            if ss.location:
                scene_parts.append(f"- **Location:** {ss.location}")
            if ss.time_of_day:
                scene_parts.append(f"- **Time:** {ss.time_of_day}")
            if ss.mood:
                scene_parts.append(f"- **Mood:** {ss.mood}")
            if ss.in_story_timestamp:
                scene_parts.append(f"- **Timestamp:** {ss.in_story_timestamp}")
            if scene_parts:
                sections.append(("scene_context", "\n".join(["# Current Scene\n", *scene_parts])))

        # Character States
        if context_response.character_states:
            items: list[str] = ["\n\n# Character States\n"]
            for name, state in context_response.character_states.items():
                state_info: list[str] = []
                if state.location:
                    state_info.append(f"at {state.location}")
                if state.emotional_state:
                    state_info.append(f"feeling {state.emotional_state}")
                if state.conditions:
                    state_info.append(f"conditions: {', '.join(state.conditions)}")
                info_str = " | ".join(state_info) if state_info else "unknown state"
                items.append(f"- **{name}:** {info_str}")
            sections.append(("character_states", "\n".join(items)))

        # Custom State (PC + Scene)
        if context_response.custom_state:
            pc_blocks = [b for b in context_response.custom_state if b.belongs_to]
            scene_blocks = [b for b in context_response.custom_state if not b.belongs_to]
            items = []
            if pc_blocks:
                pc_name = pc_blocks[0].belongs_to
                items.append(f"\n\n# {pc_name} — Tracked State\n")
                for block in pc_blocks:
                    if block.display_format == "stat_block":
                        items.append(block.content)
                    elif block.display_format == "inventory_list":
                        items.append(f"## {block.schema_name}")
                        items.append(block.content)
                    elif block.display_format == "note":
                        items.append(f"*{block.schema_name}:* {block.content}")
            if scene_blocks:
                items.append("\n\n# Scene — Tracked State\n")
                for block in scene_blocks:
                    if block.display_format == "stat_block":
                        items.append(block.content)
                    elif block.display_format == "note":
                        items.append(f"*{block.schema_name}:* {block.content}")
            if items:
                sections.append(("custom_state", "\n".join(items)))

        # Active NPCs (briefs)
        if context_response.npc_briefs:
            items = ["\n\n# Active NPCs\n"]
            for brief in context_response.npc_briefs:
                items.append(f"## {brief.character}")
                if brief.archetype:
                    items.append(f"- Archetype: {brief.archetype}")
                items.append(f"- Trust: {brief.trust_stage} ({brief.trust_score})")
                if brief.emotional_state:
                    items.append(f"- Emotional state: {brief.emotional_state}")
                if brief.behavioral_direction:
                    items.append(f"- Direction: {brief.behavioral_direction}")
            sections.append(("npc_briefs", "\n".join(items)))

        # Knowledge Boundaries (per-character beliefs; reality only when known)
        if context_response.knowledge_boundaries:
            kb_lines: list[str] = []
            for char_name, entries in context_response.knowledge_boundaries.items():
                if not entries:
                    continue
                char_lines: list[str] = []
                for entry in entries:
                    conf_src = ", ".join(
                        p for p in (entry.confidence, entry.source) if p
                    )
                    suffix = f" ({conf_src})" if conf_src else ""
                    for belief in entry.believes:
                        char_lines.append(f"- Believes: {belief}{suffix}")
                    # reality is populated only when the character knows the truth
                    if entry.reality:
                        for truth in entry.reality:
                            char_lines.append(f"- Knows the truth: {truth}")
                # Skip a bare header for a character with no renderable content.
                if char_lines:
                    kb_lines.append(f"## {char_name}")
                    kb_lines.extend(char_lines)
            if kb_lines:
                sections.append(("knowledge_boundaries", "\n".join(["\n\n# Knowledge Boundaries\n", *kb_lines])))

        # Plot Thread Alerts
        if context_response.thread_alerts:
            items = ["\n\n# Plot Thread Alerts\n"]
            for alert in context_response.thread_alerts:
                items.append(f"- **{alert.name}** [{alert.level}]: counter {alert.counter}/{alert.threshold}")
                if alert.consequence:
                    items.append(f"  Consequence: {alert.consequence}")
            sections.append(("plot_threads", "\n".join(items)))

        # Relevant Context (documents) — formatted per injection tier (Phase 3):
        # full = complete body, brief = compact summary, reference = one-liner.
        # Documents arrive score-sorted, so full → brief → reference naturally.
        if context_response.documents:
            items = ["\n\n# Relevant Context\n"]
            # Cap applies to whatever story_cards.content holds: body-only for
            # sidecar/migrated cards (the cap is then a generous safety limit),
            # but still frontmatter+body for un-migrated LEGACY cards — which
            # therefore inject up to max_len chars of YAML until migrated
            # (via `migrate-cards` or cards.auto_migrate). Configurable, default 5000.
            # NOTE: the per-tier documents BUDGET (context.max_context_chars) is
            # applied UPSTREAM in context_engine._filter_sent_cards — before
            # context_sent recording — so a budget-dropped doc is never marked
            # "sent" (which would silently suppress it next turn). The assembler
            # only formats whatever survived; it must not re-budget here.
            max_len = get_config().context.max_card_content_length
            for doc in context_response.documents:
                if doc.injection_tier == "reference":
                    line = f"- {doc.name} ({doc.card_type})"
                    if doc.summary:
                        line += f" — {doc.summary}"
                    items.append(line)
                    continue
                items.append(f"## {doc.name} ({doc.card_type})")
                if doc.injection_tier == "full" and doc.content:
                    content = doc.content[:max_len] if len(doc.content) > max_len else doc.content
                    items.append(content)
                elif doc.summary:
                    items.append(f"*Summary:* {doc.summary}")
                elif doc.content:
                    content = doc.content[:max_len] if len(doc.content) > max_len else doc.content
                    items.append(content)
            sections.append(("relevant_documents", "\n".join(items)))

        # Triggered Notes
        if context_response.triggered_notes:
            items = ["\n\n# Triggered Notes\n"]
            for note in context_response.triggered_notes:
                items.append(f"- [{note.inject_type}] {note.content}")
            sections.append(("triggered_notes", "\n".join(items)))

        # World Info (lorebook hits — structural sibling of Triggered Notes).
        # Non-empty guarded so a lorebook-less RP keeps a byte-identical prompt.
        # This renders ONE flat block — used by the legacy/injection-disabled path
        # and the static-preview endpoints. When injection is enabled,
        # ``build_messages`` instead distributes entries per-entry by depth (ST
        # position/depth, fallback to the section ``world_info`` depth) and skips
        # this name in its section loop.
        if context_response.lorebook_entries:
            items = ["\n\n# World Info\n"]
            for hit in context_response.lorebook_entries:
                # Provenance inject-side (orphan flag): keyed to match the produce
                # side in context_engine. Recorded at render; if prompt_order later
                # omits world_info, that omission surfaces via its own section_order
                # drop event (not silent), so this stays an accepted edge.
                record_injected(f"lorebook:{hit.entry_id}")
                items.append(hit.content)
            sections.append(("world_info", "\n".join(items)))

        # Past Exchange Echoes
        if context_response.past_exchanges:
            items = ["\n\n# Relevant Past Moments\n"]
            for hit in context_response.past_exchanges:
                items.append(f"- (Exchange {hit.exchange_number}) {hit.text[:300]}")
            sections.append(("past_exchanges", "\n".join(items)))

        # Card Gaps (tells GM to improvise)
        if context_response.card_gaps:
            items = [
                "\n\n# Entities Without Cards\n",
                "These entities have been mentioned but lack story cards. Improvise consistently.\n",
            ]
            for gap in context_response.card_gaps:
                items.append(f"- {gap.entity_name} (seen {gap.seen_count}x)")
            sections.append(("card_gaps", "\n".join(items)))

        # Warnings
        if context_response.warnings:
            items = ["\n\n# Warnings\n"]
            for w in context_response.warnings:
                items.append(f"- Exchange {w.exchange} analysis failed at {w.failed_at}")
            sections.append(("warnings", "\n".join(items)))

        # Writing Constraints
        if context_response.writing_constraints:
            items = [
                "\n\n# Writing Constraints\n",
                f"- Task: {context_response.writing_constraints.task_context}",
                f"- Patterns: {', '.join(context_response.writing_constraints.patterns_included)}",
            ]
            sections.append(("writing_constraints", "\n".join(items)))

        return sections

    # ------------------------------------------------------------------
    # Exchange history retrieval (Phase C of Plan 29)
    # ------------------------------------------------------------------

    async def _history_where(
        self,
        rp_folder: str,
        branch: str,
        session_id: str | None,
        exclude_exchange_number: int | None,
    ) -> tuple[str, list]:
        """Build the shared WHERE clause + params for history queries.

        Three scopings: session-flat (sessions are branch-specific), ancestry-aware
        (child branches see parent history via the ancestry chain), or flat (no
        resolver). Factored out so ``get_recent_exchanges`` (LIMIT-bounded) and
        ``get_recent_exchanges_by_budget`` (token-bounded) share identical row
        scoping — the budget variant must NOT silently drop ancestry.
        """
        if session_id:
            # Sessions are branch-specific — flat query, no ancestry
            where = "rp_folder = ? AND branch = ? AND session_id = ?"
            params: list = [rp_folder, branch, session_id]
        elif self.ancestry_resolver:
            # Ancestry-aware: include parent branch exchanges
            chain = await self.ancestry_resolver.get_ancestry_chain(rp_folder, branch)
            where, params = AncestryResolver.build_ancestry_sql(rp_folder, chain)
        else:
            # Flat query (no ancestry resolver available)
            where = "rp_folder = ? AND branch = ?"
            params = [rp_folder, branch]

        if exclude_exchange_number is not None:
            where += " AND exchange_number != ?"
            params.append(exclude_exchange_number)
        return where, params

    async def get_recent_exchanges(
        self,
        rp_folder: str,
        branch: str,
        session_id: str | None = None,
        limit: int | None = None,
        exclude_exchange_number: int | None = None,
    ) -> list[dict]:
        """Fetch recent exchanges from DB, ordered oldest-first.

        When an ancestry_resolver is available and no session_id is given,
        walks the branch ancestry chain so child branches see parent history.
        Sessions are branch-specific, so ancestry is skipped when session_id is set.

        Returns list of dicts with 'user_message', 'assistant_response', and
        'message_mode' keys.
        """
        n = limit or self.config.exchange_window
        where, params = await self._history_where(
            rp_folder, branch, session_id, exclude_exchange_number,
        )
        rows = await self.db.fetch_all(
            f"""SELECT user_message, assistant_response, message_mode
                FROM exchanges WHERE {where}
                ORDER BY exchange_number DESC LIMIT ?""",
            params + [n],
        )
        # Reverse to oldest-first order
        return list(reversed([dict(r) for r in rows]))

    async def get_recent_exchanges_by_budget(
        self,
        rp_folder: str,
        branch: str,
        token_budget: int,
        session_id: str | None = None,
        exclude_exchange_number: int | None = None,
    ) -> list[dict]:
        """Token-aware history fill (SillyTavern Feature B), ordered oldest-first.

        Fills newest-first until ``token_budget`` is exhausted instead of a blind
        count, so short-exchange sessions get more history and long ones less.
        Shares ``_history_where`` with the count-based variant — only the stop
        condition differs (token sum vs ``LIMIT``). At least the most recent
        exchange is always included even if it alone exceeds the budget.
        """
        where, params = await self._history_where(
            rp_folder, branch, session_id, exclude_exchange_number,
        )
        counter = resolve_token_counter()
        collected: list[dict] = []
        tokens_used = 0
        offset = 0
        batch_size = 5
        while tokens_used < token_budget:
            rows = await self.db.fetch_all(
                f"""SELECT user_message, assistant_response, message_mode
                    FROM exchanges WHERE {where}
                    ORDER BY exchange_number DESC LIMIT ? OFFSET ?""",
                params + [batch_size, offset],
            )
            if not rows:
                break
            for r in rows:
                ex = dict(r)
                ex_tokens = counter(
                    (ex.get("user_message") or "") + (ex.get("assistant_response") or "")
                )
                # Stop once adding this exchange would overflow — but always keep
                # at least the most recent one (never return an empty history when
                # exchanges exist).
                if collected and tokens_used + ex_tokens > token_budget:
                    return list(reversed(collected))
                collected.append(ex)
                tokens_used += ex_tokens
            offset += batch_size
        return list(reversed(collected))

    # ------------------------------------------------------------------
    # Message assembly helpers (Phase 4: injection depth + token budget + examples)
    # ------------------------------------------------------------------

    def _effective_injection_depths(self, rp_folder: str) -> dict[str, int]:
        """Global injection depths merged with this RP's frontmatter override."""
        depths = dict(self.prompt_config.injection.depths)
        if self.guidelines_service is not None:
            guidelines = self.guidelines_service.get_guidelines(rp_folder)
            if guidelines and guidelines.injection_depths:
                depths.update(guidelines.injection_depths)
        return depths

    def injection_preview(self, rp_folder: str) -> dict:
        """Preview metadata for the static prompt view (Phase 6).

        Returns the effective per-section injection depths and whether the
        injection feature is enabled. When injection is **disabled** (the Phase 4
        default) every section renders at depth 0 (in the system message), so the
        static preview is already accurate; the depth map is the *configured*
        placement that would take effect if injection were enabled. This is a
        config view, not a runtime ``build_messages`` simulation.
        """
        return {
            "enabled": self.prompt_config.injection.enabled,
            "depths": self._effective_injection_depths(rp_folder),
        }

    def _history_token_budget(self) -> int:
        """Tokens allotted to exchange history under the token-budget feature."""
        tb = self.prompt_config.token_budget
        total = tb.model_context_window - self.config.max_tokens - tb.safety_margin
        total = max(total, 0)
        return int(total * tb.allocation.history)

    def _warn_if_static_overflows(self, static_messages: list[dict]) -> None:
        """Emit a diagnostic when the system prompt + examples blow their budget.

        Only the history leg is token-bounded; the static prompt is not. If it
        already exceeds its system+context allocation, the remaining history is
        silently squeezed — log it so the overflow is observable.
        """
        tb = self.prompt_config.token_budget
        total = max(tb.model_context_window - self.config.max_tokens - tb.safety_margin, 0)
        static_budget = int(total * (tb.allocation.system + tb.allocation.context))
        static_tokens = estimate_messages_tokens(static_messages, resolve_token_counter())
        if static_tokens > static_budget:
            logger.warning(
                "Static prompt + examples (%d tok) exceed their token budget (%d tok); "
                "history will be squeezed (model_context_window=%d).",
                static_tokens, static_budget, tb.model_context_window,
            )

    @staticmethod
    def _exchanges_to_blocks(exchanges: list[dict]) -> list[list[dict]]:
        """Group history into per-exchange message blocks (for depth injection).

        One block per exchange keeps a user/assistant pair (or a direction-mode
        system message) atomic, so depth injections land *between* exchanges, not
        inside a pair. Flattening these blocks in order reproduces the legacy flat
        message list exactly.
        """
        from rp_engine.utils.direction import build_direction_message
        blocks: list[list[dict]] = []
        for ex in exchanges:
            block: list[dict] = []
            mode = ex.get("message_mode", "rp")
            if mode == "direction" and ex.get("user_message"):
                # Direction exchanges: inject user message as system guidance
                # so the LLM remembers ongoing direction
                block.append(build_direction_message(ex["user_message"]))
            elif ex.get("user_message"):
                block.append({"role": "user", "content": ex["user_message"]})
            if ex.get("assistant_response"):
                block.append({"role": "assistant", "content": ex["assistant_response"]})
            if block:
                blocks.append(block)
        return blocks

    @staticmethod
    def _inject_at_depths(
        blocks: list[list[dict]], depth_content: dict[int, list[str]],
    ) -> list[list[dict]]:
        """Insert depth-keyed system messages between history blocks.

        Depth ``d`` lands the injection ``d`` blocks from the bottom (the current
        user turn is the depth-1 anchor — before it, matching the legacy
        ``insert(-1)`` direction behavior). Depths exceeding the available history
        clamp to the top. Injections that resolve to the same slot are collapsed
        into one system message, higher depth first.
        """
        if not depth_content:
            return blocks
        n = len(blocks)
        by_index: dict[int, list[str]] = {}
        for depth in sorted(depth_content.keys(), reverse=True):  # higher = further up
            idx = max(0, n - depth)
            by_index.setdefault(idx, []).extend(depth_content[depth])
        result: list[list[dict]] = []
        for i, block in enumerate(blocks):
            if i in by_index:
                result.append([{"role": "system", "content": "\n\n".join(by_index[i])}])
            result.append(block)
        return result

    async def _build_example_messages(
        self, rp_folder: str, context_response: ContextResponse, max_examples: int,
    ) -> list[dict]:
        """Few-shot example-dialogue messages from the first active NPC with examples.

        Iterates ``npc_briefs`` in order (active NPCs precede referenced ones —
        a rough relevance proxy, not a strict sort) and returns the first NPC
        whose card body has a parseable ``## Example Dialogue`` section. The card
        is fetched by ``card_id`` directly — it is excluded from
        ``context_response.documents`` by the Phase 3 NPC dedup, so the body must
        come from ``story_cards``, not the documents list. ``is_player_character``
        is read from the ``frontmatter`` JSON column (the indexer doesn't populate
        the dedicated column) — PC cards are skipped (their voice is user-driven).
        """
        for brief in context_response.npc_briefs:
            if not brief.card_id:
                continue
            card = await self.db.fetch_one(
                "SELECT content, frontmatter FROM story_cards WHERE id = ? AND rp_folder = ?",
                [brief.card_id, rp_folder],
            )
            if not card:
                continue
            fm = safe_parse_json(card.get("frontmatter"))
            if fm.get("is_player_character"):
                continue
            messages = example_dialogue_messages(
                card.get("content") or "", brief.character, max_examples,
            )
            if messages:
                return messages
        return []

    def _fit_examples_to_reserve(self, example_messages: list[dict]) -> list[dict]:
        """Trim example few-shot pairs to the token-budget ``reserve`` allocation.

        Feature D budget-coupling (corrections plan B1): example dialogue draws
        from the ``reserve`` slice of the token budget and is the most expendable
        prompt content — under pressure pairs are dropped **oldest first**, before
        any history is touched. The caller skips this when ``pin_examples`` is set
        (pinned examples always ship). Drops are **logged** (silent-drop guard).
        ``example_messages`` is a flat ``[user, assistant, user, assistant, …]``
        list, so one pair is two entries.
        """
        tb = self.prompt_config.token_budget
        total = max(tb.model_context_window - self.config.max_tokens - tb.safety_margin, 0)
        reserve_budget = int(total * tb.allocation.reserve)
        counter = resolve_token_counter()

        kept = list(example_messages)
        dropped_pairs = 0
        while kept and estimate_messages_tokens(kept, counter) > reserve_budget:
            kept = kept[2:]  # drop the oldest user/assistant pair (front of the list)
            dropped_pairs += 1
        if dropped_pairs:
            logger.warning(
                "Example dialogue exceeds the reserve budget (%d tok): dropped %d "
                "oldest pair(s), kept %d.",
                reserve_budget, dropped_pairs, len(kept) // 2,
            )
            record_drop(
                "prompt.example_reserve", "example_pair", "reserve_budget",
                budget_before=reserve_budget,
                detail=f"dropped {dropped_pairs} oldest pair(s), kept {len(kept) // 2}",
            )
        return kept

    async def build_messages(
        self,
        rp_folder: str,
        branch: str,
        user_message: str,
        context_response: ContextResponse | None = None,
        session_id: str | None = None,
        exchange_limit: int | None = None,
        exclude_exchange_number: int | None = None,
    ) -> list[dict]:
        """Build a complete LLM-ready message list.

        Layout (depending on Phase 4 config; all features default to a layout
        identical to the legacy assembler when an RP has no example-dialogue):

            [system]  (static + depth-0 dynamic context)
            [example_user/asst pairs]        — Feature D, before history
            [...history blocks, with depth-N system injections interleaved...]
            [current user message]

        With injection disabled, all dynamic context stays in the system message
        and history is flat — byte-identical to the previous behavior.
        """
        prompt_cfg = self.prompt_config

        # --- Feature A: decide which dynamic sections inject at depth > 0 ---
        inject_names: set[str] = set()
        depth_content: dict[int, list[str]] = {}
        if prompt_cfg.injection.enabled and context_response is not None:
            depths = self._effective_injection_depths(rp_folder)
            for name, content in self._build_dynamic_sections(context_response):
                if name == "world_info":
                    continue  # lorebook depth is handled per-entry below
                depth = depths.get(name, 0)
                if depth > 0:
                    inject_names.add(name)
                    depth_content.setdefault(depth, []).append(content)

            # World Info honors PER-ENTRY depth (ST position/depth), falling back
            # to the section-level ``world_info`` depth. Entries are grouped by
            # effective depth so each depth slot gets one ``# World Info`` block.
            # When the section depth is 0 the section stays in the top system
            # message (handled by build_system_prompt — world_info NOT excluded).
            section_depth = depths.get("world_info", 0)
            if context_response.lorebook_entries and section_depth > 0:
                inject_names.add("world_info")
                by_depth: dict[int, list[str]] = {}
                for hit in context_response.lorebook_entries:
                    d = hit.depth if (hit.depth and hit.depth > 0) else section_depth
                    by_depth.setdefault(d, []).append(hit.content)
                for d, contents in by_depth.items():
                    depth_content.setdefault(d, []).append(
                        "\n\n# World Info\n\n" + "\n".join(contents)
                    )

        # --- Phase 5a: narrator's note (session-persistent GM steering) ---
        # Injected at its own depth independent of the global injection toggle —
        # it's a distinct opt-in (set the note → it appears). Depth is clamped to
        # >= 1 because depth 0 never resolves to a real slot in _inject_at_depths.
        if session_id:
            note_row = await self.db.fetch_one(
                "SELECT narrator_note, narrator_note_depth FROM sessions WHERE id = ?",
                [session_id],
            )
            if note_row and note_row.get("narrator_note"):
                note = note_row["narrator_note"]
                nd = max(1, note_row.get("narrator_note_depth") or 2)
                depth_content.setdefault(nd, []).append(
                    f"\n\n# Narrator's Note\n\n{note}"
                )

        system_prompt = self.build_system_prompt(
            rp_folder, context_response, exclude_sections=inject_names or None,
        )
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        # --- Feature D: example dialogue few-shot, after system, before history ---
        if prompt_cfg.example_dialogue.enabled and context_response is not None:
            example_messages = await self._build_example_messages(
                rp_folder, context_response, prompt_cfg.example_dialogue.max_examples,
            )
            # Token-budget coupling (B1): examples draw from the `reserve`
            # allocation and are the most expendable content — dropped oldest-pair-
            # first before history is touched, unless pinned. With the budget off,
            # only the max_examples count cap (applied above) governs.
            if (
                example_messages
                and prompt_cfg.token_budget.enabled
                and not prompt_cfg.example_dialogue.pin_examples
            ):
                example_messages = self._fit_examples_to_reserve(example_messages)
            messages.extend(example_messages)

        # --- Feature B: token-aware history, else legacy count window ---
        if prompt_cfg.token_budget.enabled:
            # Overflow diagnostic: history is budget-bounded, but the static
            # system prompt + few-shot examples are not. Warn (don't truncate —
            # static content is load-bearing) when they alone exceed the
            # system+context allocation, so an oversized prompt is observable
            # rather than a silent history squeeze.
            self._warn_if_static_overflows(messages)
            exchanges = await self.get_recent_exchanges_by_budget(
                rp_folder, branch, self._history_token_budget(), session_id,
                exclude_exchange_number=exclude_exchange_number,
            )
        else:
            exchanges = await self.get_recent_exchanges(
                rp_folder, branch, session_id, exchange_limit,
                exclude_exchange_number=exclude_exchange_number,
            )

        # --- Assemble history blocks + current turn, with depth injections ---
        blocks = self._exchanges_to_blocks(exchanges)
        blocks.append([{"role": "user", "content": user_message}])  # current turn
        blocks = self._inject_at_depths(blocks, depth_content)
        for block in blocks:
            messages.extend(block)

        return messages
