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

from rp_engine.config import ChatConfig, get_config
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
from rp_engine.utils.frontmatter import parse_file

logger = logging.getLogger(__name__)

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


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
    ) -> None:
        self.vault_root = vault_root
        self.db = db
        self.guidelines_service = guidelines_service
        self._config_override: ChatConfig | None = config
        self.ancestry_resolver = ancestry_resolver

    @property
    def config(self) -> ChatConfig:
        """Read chat config dynamically so hot-reloaded changes take effect.

        Tests can pass a custom ChatConfig at construction to override.
        Production code passes None; the property reads from get_config().
        """
        if self._config_override is not None:
            return self._config_override
        return get_config().chat

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

        if include_writing:
            sections["writing_principles"] = self._build_writing_section()
        if include_npc:
            sections["npc_framework"] = self._build_npc_framework_section()
        if include_output:
            sections["output_format"] = self._build_output_format_section()

        return sections

    def assemble_static_prompt(self, sections: dict) -> str:
        """Combine all sections into a formatted system prompt string."""
        parts: list[str] = []

        # --- Writing Principles ---
        writing = sections.get("writing_principles")
        if writing:
            parts.append("# Writing Principles\n")
            parts.append("## Core Pillars")
            for pillar in writing.get("core_pillars", []):
                parts.append(f"- {pillar}")

            parts.append("\n## Emotion to Physical Reaction Map")
            parts.append("Never report feelings directly. Always show through the body.\n")
            parts.append("| Emotion | Physical Manifestations |")
            parts.append("|---------|------------------------|")
            for emotion, manifestations in writing.get("emotion_physical_map", {}).items():
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
            parts.append("\n\n# Output Format\n")
            for rule in output.get("rules", []):
                parts.append(f"- {rule}")
            structure = output.get("response_structure", [])
            if structure:
                parts.append("\n## Response Structure")
                for step in structure:
                    parts.append(f"- {step}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Dynamic context injection (Phase B of Plan 29)
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        rp_folder: str,
        context_response: ContextResponse | None = None,
    ) -> str:
        """Build a complete system prompt: static sections + dynamic context.

        Static content goes at the top (cached in LLM attention).
        Dynamic content at the bottom (strongest recency attention).
        """
        sections = self.get_sections(rp_folder)
        parts: list[str] = [self.assemble_static_prompt(sections)]

        if context_response is None:
            return parts[0]

        # --- Dynamic boundary ---
        dynamic: list[str] = ["\n\n---\n"]

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
                dynamic.append("# Current Scene\n")
                dynamic.extend(scene_parts)

        # Character States
        if context_response.character_states:
            dynamic.append("\n\n# Character States\n")
            for name, state in context_response.character_states.items():
                state_info: list[str] = []
                if state.location:
                    state_info.append(f"at {state.location}")
                if state.emotional_state:
                    state_info.append(f"feeling {state.emotional_state}")
                if state.conditions:
                    state_info.append(f"conditions: {', '.join(state.conditions)}")
                info_str = " | ".join(state_info) if state_info else "unknown state"
                dynamic.append(f"- **{name}:** {info_str}")

        # Custom State (PC + Scene)
        if context_response.custom_state:
            pc_blocks = [b for b in context_response.custom_state if b.belongs_to]
            scene_blocks = [b for b in context_response.custom_state if not b.belongs_to]

            if pc_blocks:
                pc_name = pc_blocks[0].belongs_to
                dynamic.append(f"\n\n# {pc_name} — Tracked State\n")
                for block in pc_blocks:
                    if block.display_format == "stat_block":
                        dynamic.append(block.content)
                    elif block.display_format == "inventory_list":
                        dynamic.append(f"## {block.schema_name}")
                        dynamic.append(block.content)
                    elif block.display_format == "note":
                        dynamic.append(f"*{block.schema_name}:* {block.content}")

            if scene_blocks:
                dynamic.append("\n\n# Scene — Tracked State\n")
                for block in scene_blocks:
                    if block.display_format == "stat_block":
                        dynamic.append(block.content)
                    elif block.display_format == "note":
                        dynamic.append(f"*{block.schema_name}:* {block.content}")

        # Active NPCs (briefs)
        if context_response.npc_briefs:
            dynamic.append("\n\n# Active NPCs\n")
            for brief in context_response.npc_briefs:
                dynamic.append(f"## {brief.character}")
                if brief.archetype:
                    dynamic.append(f"- Archetype: {brief.archetype}")
                dynamic.append(f"- Trust: {brief.trust_stage} ({brief.trust_score})")
                if brief.emotional_state:
                    dynamic.append(f"- Emotional state: {brief.emotional_state}")
                if brief.behavioral_direction:
                    dynamic.append(f"- Direction: {brief.behavioral_direction}")

        # Plot Thread Alerts
        if context_response.thread_alerts:
            dynamic.append("\n\n# Plot Thread Alerts\n")
            for alert in context_response.thread_alerts:
                dynamic.append(f"- **{alert.name}** [{alert.level}]: counter {alert.counter}/{alert.threshold}")
                if alert.consequence:
                    dynamic.append(f"  Consequence: {alert.consequence}")

        # Relevant Context (documents)
        if context_response.documents:
            dynamic.append("\n\n# Relevant Context\n")
            for doc in context_response.documents:
                dynamic.append(f"## {doc.name} ({doc.card_type})")
                if doc.content:
                    # Truncate very long content
                    content = doc.content[:2000] if len(doc.content) > 2000 else doc.content
                    dynamic.append(content)
                elif doc.summary:
                    dynamic.append(f"*Summary:* {doc.summary}")

        # Triggered Notes
        if context_response.triggered_notes:
            dynamic.append("\n\n# Triggered Notes\n")
            for note in context_response.triggered_notes:
                dynamic.append(f"- [{note.inject_type}] {note.content}")

        # Past Exchange Echoes
        if context_response.past_exchanges:
            dynamic.append("\n\n# Relevant Past Moments\n")
            for hit in context_response.past_exchanges:
                dynamic.append(f"- (Exchange {hit.exchange_number}) {hit.text[:300]}")

        # Card Gaps (tells GM to improvise)
        if context_response.card_gaps:
            dynamic.append("\n\n# Entities Without Cards\n")
            dynamic.append("These entities have been mentioned but lack story cards. Improvise consistently.\n")
            for gap in context_response.card_gaps:
                dynamic.append(f"- {gap.entity_name} (seen {gap.seen_count}x)")

        # Warnings
        if context_response.warnings:
            dynamic.append("\n\n# Warnings\n")
            for w in context_response.warnings:
                dynamic.append(f"- Exchange {w.exchange} analysis failed at {w.failed_at}")

        # Writing Constraints
        if context_response.writing_constraints:
            dynamic.append("\n\n# Writing Constraints\n")
            dynamic.append(f"- Task: {context_response.writing_constraints.task_context}")
            dynamic.append(f"- Patterns: {', '.join(context_response.writing_constraints.patterns_included)}")

        parts.extend(dynamic)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Exchange history retrieval (Phase C of Plan 29)
    # ------------------------------------------------------------------

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

        if session_id:
            # Sessions are branch-specific — flat query, no ancestry
            where = "rp_folder = ? AND branch = ? AND session_id = ?"
            params: list = [rp_folder, branch, session_id]
            if exclude_exchange_number is not None:
                where += " AND exchange_number != ?"
                params.append(exclude_exchange_number)
            rows = await self.db.fetch_all(
                f"""SELECT user_message, assistant_response, message_mode
                    FROM exchanges WHERE {where}
                    ORDER BY exchange_number DESC LIMIT ?""",
                params + [n],
            )
        elif self.ancestry_resolver:
            # Ancestry-aware: include parent branch exchanges
            chain = await self.ancestry_resolver.get_ancestry_chain(rp_folder, branch)
            ancestry_where, params = AncestryResolver.build_ancestry_sql(rp_folder, chain)
            if exclude_exchange_number is not None:
                ancestry_where += " AND exchange_number != ?"
                params.append(exclude_exchange_number)
            rows = await self.db.fetch_all(
                f"""SELECT user_message, assistant_response, message_mode
                    FROM exchanges WHERE {ancestry_where}
                    ORDER BY exchange_number DESC LIMIT ?""",
                params + [n],
            )
        else:
            # Flat query (no ancestry resolver available)
            where = "rp_folder = ? AND branch = ?"
            params = [rp_folder, branch]
            if exclude_exchange_number is not None:
                where += " AND exchange_number != ?"
                params.append(exclude_exchange_number)
            rows = await self.db.fetch_all(
                f"""SELECT user_message, assistant_response, message_mode
                    FROM exchanges WHERE {where}
                    ORDER BY exchange_number DESC LIMIT ?""",
                params + [n],
            )

        # Reverse to oldest-first order
        return list(reversed([dict(r) for r in rows]))

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

        Returns: [system, ...exchange_pairs, user_message]
        """
        system_prompt = self.build_system_prompt(rp_folder, context_response)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        # Add recent exchange history
        exchanges = await self.get_recent_exchanges(
            rp_folder, branch, session_id, exchange_limit,
            exclude_exchange_number=exclude_exchange_number,
        )
        for ex in exchanges:
            mode = ex.get("message_mode", "rp")
            if mode == "direction" and ex.get("user_message"):
                # Direction exchanges: inject user message as system guidance
                # so the LLM remembers ongoing direction
                from rp_engine.utils.direction import build_direction_message
                messages.append(build_direction_message(ex["user_message"]))
            elif ex.get("user_message"):
                messages.append({"role": "user", "content": ex["user_message"]})
            if ex.get("assistant_response"):
                messages.append({"role": "assistant", "content": ex["assistant_response"]})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        return messages
