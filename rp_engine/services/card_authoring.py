"""CardAuthoringService — AI-driven story-card authoring.

Owns the business logic behind the cards authoring endpoints (``/suggest``,
``/generate-name``) plus the reciprocal-relationship sync triggered when a
character/npc card is created. Extracted from ``routers/cards/authoring.py`` in
Phase 6b so the router handlers stay thin request-parse/response-shape wrappers.

The LLM mock in tests stays at the ``LLMClient`` level — this extraction does
not move it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rp_engine.constants.card_authoring_prompts import (
    build_generate_name_prompt,
    build_reciprocal_relationship_prompt,
    build_suggest_card_prompt,
)
from rp_engine.database import Database
from rp_engine.models.story_card import (
    GenerateCardNameRequest,
    GenerateCardNameResponse,
    RelationshipSyncEntry,
    RelationshipSyncResult,
    SuggestCardRequest,
    SuggestCardResponse,
)
from rp_engine.services.card_indexer import CardIndexer
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.llm_client import LLMClient
from rp_engine.utils.frontmatter import parse_frontmatter, write_card_files
from rp_engine.utils.json_helpers import safe_parse_json
from rp_engine.utils.normalization import generate_card_id, normalize_key
from rp_engine.utils.scene_detection import group_into_scenes

logger = logging.getLogger(__name__)

# Pattern matches all Obsidian Templater expressions: <% ... %>
_TEMPLATER_RE = re.compile(r"<%.*?%>")

# Pattern to strip markdown code fences wrapping the entire response
_CODE_FENCE_RE = re.compile(r"^```(?:markdown|yaml|md)?\s*\n(.*?)```\s*$", re.DOTALL)

_SUMMARY_KEYS = ("role", "age", "gender", "occupation", "species")

_TEMPLATE_MAP = {
    "character": "Character Template.md",
    "npc": "NPC Template.md",
    "location": "Location Template.md",
    "secret": "Secret Template.md",
    "memory": "Memory Template.md",
    "knowledge": "Knowledge Template.md",
    "lore": "Lore Template.md",
    "organization": "Organization Template.md",
    "plot_thread": "Plot Thread Template.md",
    "plot_arc": "Plot Arc Template.md",
    "item": "Item Template.md",
    "chapter_summary": "Chapter Template.md",
}


def _sanitize_llm_card(markdown: str) -> str:
    """Clean up common LLM generation artifacts in card markdown.

    Handles:
    - Markdown code fences wrapping the entire response
    - Multiple duplicate frontmatter blocks (keeps the most complete one)
    - Stray 'yaml' / 'markdown' text between blocks (code fence leakage)
    """
    text = markdown.strip()

    # Strip wrapping code fences (```markdown ... ```)
    m = _CODE_FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()

    if not text.startswith("---"):
        return text

    # Extract ALL frontmatter blocks from the beginning of the text.
    # Pattern: ---\n<yaml>\n--- possibly followed by more blocks with
    # optional stray 'yaml'/'markdown' lines between them.
    best_fm = ""
    body = text
    _STRAY_RE = re.compile(r"^(?:yaml|markdown)\r?\n", re.IGNORECASE)

    while body.startswith("---"):
        end_idx = body.find("---", 3)
        if end_idx == -1:
            break
        fm_block = body[3:end_idx].strip()
        after = body[end_idx + 3:].lstrip("\n")

        # Keep the longest (most complete) frontmatter block
        if len(fm_block) > len(best_fm):
            best_fm = fm_block

        body = after
        # Strip stray code fence language markers between blocks
        m_stray = _STRAY_RE.match(body)
        if m_stray:
            body = body[m_stray.end():]

    if best_fm:
        text = f"---\n{best_fm}\n---\n{body}"

    return text


def _strip_templater(
    template: str, entity_name: str, rp_folder: str, card_type: str
) -> str:
    """Replace Obsidian Templater expressions with concrete values for LLM prompts."""
    card_id = generate_card_id(card_type, entity_name)
    trigger = entity_name.lower()

    # Named replacements first
    result = template.replace("<% tp.user.rp_helpers.getTitle() %>", entity_name)
    result = result.replace("<% tp.user.rp_helpers.getId() %>", card_id)
    result = result.replace("<% tp.user.rp_helpers.getRpName() %>", rp_folder)
    result = result.replace("<% tp.user.rp_helpers.getTrigger() %>", trigger)

    # Remove any remaining Templater expressions (cursor, date, etc.)
    result = _TEMPLATER_RE.sub("", result)
    return result


def _build_card_summary(name: str, frontmatter: dict, body: str, max_body: int = 2000) -> str:
    """Build a brief text summary of a card for LLM prompts."""
    parts = [f"Name: {name}"]
    for key in _SUMMARY_KEYS:
        val = frontmatter.get(key)
        if val:
            parts.append(f"{key.title()}: {val}")
    if body:
        _, body_text = parse_frontmatter(body) if body.startswith("---") else ("", body)
        if body_text:
            parts.append(f"\nCard content:\n{body_text[:max_body]}")
    return "\n".join(parts)


class CardAuthoringService:
    """AI-driven card authoring: suggest, generate-name, reciprocal sync."""

    def __init__(
        self,
        db: Database,
        llm_client: LLMClient,
        card_indexer: CardIndexer,
        vault_root: Path,
        guidelines_service: GuidelinesService,
    ) -> None:
        self.db = db
        self.llm = llm_client
        self.indexer = card_indexer
        self.vault_root = vault_root
        self.guidelines_service = guidelines_service

    async def suggest_card(self, body: SuggestCardRequest) -> SuggestCardResponse:
        """Generate a draft story card for an entity by searching exchanges and using LLM."""
        entity_name = body.entity_name
        card_type = body.card_type
        rp_folder = body.rp_folder
        additional_context = body.additional_context

        evidence = await self._gather_suggest_evidence(entity_name, rp_folder, body.branch)

        # Load related card content
        related_cards_section = ""
        if body.related_entities:
            related_parts = []
            for rel_name in body.related_entities:
                row = await self.db.fetch_one(
                    "SELECT name, card_type, content FROM story_cards WHERE LOWER(name) = LOWER(?) AND rp_folder = ?",
                    [rel_name, rp_folder],
                )
                if row and row["content"]:
                    related_parts.append(
                        f"### {row['name']} ({row['card_type']})\n{row['content']}"
                    )
            if related_parts:
                related_cards_section = (
                    "## Related Cards (established lore — do NOT contradict)\n\n"
                    + "\n\n---\n\n".join(related_parts)
                )

        # Load template
        template_name = _TEMPLATE_MAP.get(card_type, "NPC Template.md")
        template_path = self.vault_root / "z_templates" / "Story Cards" / template_name
        template = ""
        if template_path.exists():
            raw = template_path.read_text(encoding="utf-8")
            template = _strip_templater(raw, entity_name, rp_folder, card_type)

        # Split template into frontmatter and body so the LLM doesn't duplicate the frontmatter
        template_frontmatter = ""
        template_body = template
        if template.startswith("---"):
            parts = template.split("---", 2)
            if len(parts) >= 3:
                template_frontmatter = parts[1].strip()
                template_body = parts[2].strip()

        prompt = build_suggest_card_prompt(
            entity_name=entity_name,
            card_type=card_type,
            template_frontmatter=template_frontmatter,
            template_body=template_body,
            related_cards_section=related_cards_section,
            evidence=evidence,
            additional_context=additional_context,
        )

        response = await self.llm.generate(
            messages=[{"role": "user", "content": prompt}],
            model=self.llm.models.card_generation,
            temperature=0.4,
            max_tokens=6000,
        )

        cleaned = _sanitize_llm_card(response.content)

        return SuggestCardResponse(
            entity_name=entity_name,
            card_type=card_type,
            markdown=cleaned,
            model_used=response.model,
        )

    async def _gather_suggest_evidence(
        self, entity_name: str, rp_folder: str, branch: str
    ) -> str:
        """Gather narrative evidence for a suggested card, scoped to one branch.

        Prefers scene-grouped gap-tracking evidence (``card_gap_exchanges``); when
        none exists, falls back to a direct exchange search. Both paths filter by
        ``branch`` (Bug C) so a draft on one branch never pulls evidence from a
        sibling branch.
        """
        # Try scene-aware evidence first (from card_gap_exchanges)
        gap_rows = await self.db.fetch_all(
            """SELECT exchange_number, chunk_text, mention_type
               FROM card_gap_exchanges
               WHERE LOWER(entity_name) = LOWER(?) AND rp_folder = ?
                 AND branch = ?
               ORDER BY exchange_number""",
            [entity_name, rp_folder, branch],
        )

        if gap_rows:
            # Scene-aware path: group gap exchanges into scenes
            exchange_nums = [r["exchange_number"] for r in gap_rows]
            scenes = group_into_scenes(exchange_nums)
            chunks_by_num = {r["exchange_number"]: r for r in gap_rows}

            evidence_parts = []
            for i, scene in enumerate(scenes, 1):
                scene_chunks = []
                for num in scene.exchanges:
                    row = chunks_by_num.get(num)
                    if row and row["chunk_text"]:
                        label = "PRIMARY" if row["mention_type"] == "primary" else "peripheral"
                        scene_chunks.append(f"[Exchange {num}, {label}]\n{row['chunk_text']}")
                if scene_chunks:
                    evidence_parts.append(
                        f"## Scene {i} (Exchanges {scene.start}-{scene.end})\n"
                        + "\n---\n".join(scene_chunks)
                    )

            evidence = "\n\n".join(evidence_parts)
            primary_count = sum(1 for r in gap_rows if r["mention_type"] == "primary")
            evidence += (
                f"\n\nEntity mentioned in {len(gap_rows)} exchanges total, "
                f"{primary_count} as primary focus."
            )
            return evidence

        # Fallback: search exchanges directly (pre-existing gaps or no gap tracking).
        # Bug C: filter by branch to match the gap-evidence path above.
        rows = await self.db.fetch_all(
            """SELECT exchange_number, user_message, assistant_response
               FROM exchanges
               WHERE rp_folder = ? AND branch = ? AND (
                   LOWER(user_message) LIKE ? OR LOWER(assistant_response) LIKE ?
               )
               ORDER BY exchange_number DESC LIMIT 10""",
            [rp_folder, branch, f"%{entity_name.lower()}%", f"%{entity_name.lower()}%"],
        )
        return "\n---\n".join(
            f"Exchange {r['exchange_number']}:\n{r['assistant_response'][:500]}"
            for r in rows
        )

    async def generate_card_name(
        self, body: GenerateCardNameRequest, rp_folder: str
    ) -> GenerateCardNameResponse:
        """Generate name suggestions for a new story card using LLM."""
        card_type = body.card_type
        hints = body.hints
        count = min(body.count, 10)

        # Get existing card names to avoid duplicates
        existing_rows = await self.db.fetch_all(
            "SELECT name FROM story_cards WHERE card_type = ? AND rp_folder = ? LIMIT 20",
            [card_type, rp_folder],
        )
        existing_names = {r["name"].lower() for r in existing_rows}
        existing_list = [r["name"] for r in existing_rows]

        # Get tone/setting from guidelines
        tone_context = ""
        guidelines = self.guidelines_service.get_guidelines(rp_folder)
        if guidelines:
            parts = []
            if guidelines.tone:
                tone_str = ", ".join(guidelines.tone) if isinstance(guidelines.tone, list) else guidelines.tone
                parts.append(f"Tone: {tone_str}")
            if guidelines.scene_pacing:
                parts.append(f"Pacing: {guidelines.scene_pacing}")
            if parts:
                tone_context = "\n".join(parts)

        prompt = build_generate_name_prompt(
            count=count,
            card_type=card_type,
            tone_context=tone_context,
            hints=hints,
            existing_list=existing_list,
        )

        try:
            response = await self.llm.generate(
                messages=[{"role": "user", "content": prompt}],
                model=self.llm.models.card_generation,
                temperature=0.8,
                max_tokens=500,
                response_format={"type": "json_object"},
            )
            parsed = safe_parse_json(response.content)
            raw_names = parsed.get("names", []) if isinstance(parsed, dict) else []
            # Dedup against existing
            suggestions = [n for n in raw_names if isinstance(n, str) and n.lower() not in existing_names][:count]
        except Exception:
            logger.warning("Card name generation failed, returning empty suggestions")
            suggestions = []

        return GenerateCardNameResponse(suggestions=suggestions, card_type=card_type)

    async def sync_reciprocal_relationships(
        self,
        *,
        new_card_name: str,
        new_card_id: str,
        new_card_frontmatter: dict,
        new_card_body: str,
        rp_folder: str,
    ) -> RelationshipSyncResult:
        """After creating a card, update referenced cards with reciprocal relationships.

        For each target in ``initial_relationships``:
        1. Look up the target card in the DB
        2. Check if it already has a reciprocal relationship back to new_card_id
        3. If not, use LLM to generate the reciprocal entry from the target's perspective
        4. Update the target card's frontmatter + body and reindex
        """
        result = RelationshipSyncResult(source_card=new_card_name)

        relationships = new_card_frontmatter.get("initial_relationships") or []
        if not relationships or not isinstance(relationships, list):
            return result

        # Build a summary of the new card for the LLM
        new_card_summary = _build_card_summary(new_card_name, new_card_frontmatter, new_card_body)

        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            target_id = rel.get("target", "")
            if not target_id:
                continue

            try:
                # Find the target card in the DB
                target_row = await self._find_card_any_type(target_id, rp_folder)
                if not target_row:
                    # Target card doesn't exist yet — skip (forward reference)
                    continue

                # Check if target already has a reciprocal relationship to new_card_id
                target_fm = safe_parse_json(target_row["frontmatter"])
                existing_rels = target_fm.get("initial_relationships") or []
                already_linked = any(
                    isinstance(r, dict) and normalize_key(r.get("target", "")) == normalize_key(new_card_id)
                    for r in existing_rels
                )
                if already_linked:
                    continue

                # Use LLM to generate reciprocal relationship
                reciprocal = await self._generate_reciprocal_relationship(
                    new_card_name=new_card_name,
                    new_card_id=new_card_id,
                    new_card_summary=new_card_summary,
                    new_card_rel=rel,
                    target_name=target_row["name"],
                    target_fm=target_fm,
                    target_body=(target_row["content"] or ""),
                )

                if not reciprocal:
                    result.errors.append(f"LLM failed to generate reciprocal for {target_row['name']}")
                    continue

                # Extract knowledge boundaries before adding to relationships
                kb_items = reciprocal.pop("doesnt_know", [])

                # Update the target card's frontmatter
                if not isinstance(existing_rels, list):
                    existing_rels = []
                existing_rels.append(reciprocal)
                target_fm["initial_relationships"] = existing_rels

                # Apply knowledge boundary updates if the LLM found any
                if isinstance(kb_items, list) and kb_items:
                    target_kb = target_fm.get("knowledge_boundaries") or {}
                    if not isinstance(target_kb, dict):
                        target_kb = {}
                    doesnt_know = target_kb.get("doesnt_know") or []
                    if not isinstance(doesnt_know, list):
                        doesnt_know = []
                    for item in kb_items:
                        if isinstance(item, str) and item not in doesnt_know:
                            doesnt_know.append(item)
                    target_kb["doesnt_know"] = doesnt_know
                    target_fm["knowledge_boundaries"] = target_kb

                # Write updated target card as body-only .md + sidecar.
                # content is body-only for sidecar cards; parse_frontmatter strips
                # YAML for legacy ones (no-op when there is none).
                _, target_body = parse_frontmatter(target_row["content"] or "")
                target_file = self.vault_root / target_row["file_path"]
                write_card_files(
                    target_file.parent, target_file.stem, target_fm, target_body
                )
                await self.indexer.index_file(rp_folder, target_file)

                result.updated_cards.append(RelationshipSyncEntry(
                    card_name=target_row["name"],
                    card_type=target_row["card_type"],
                    relationship_added=reciprocal,
                ))

            except Exception as e:
                result.errors.append(f"Failed to sync {target_id}: {e}")
                logger.exception("Relationship sync error for target %s", target_id)

        return result

    async def _generate_reciprocal_relationship(
        self,
        *,
        new_card_name: str,
        new_card_id: str,
        new_card_summary: str,
        new_card_rel: dict,
        target_name: str,
        target_fm: dict,
        target_body: str,
    ) -> dict | None:
        """Use LLM to generate a reciprocal relationship entry from the target's perspective."""
        target_summary = _build_card_summary(target_name, target_fm, target_body)

        prompt = build_reciprocal_relationship_prompt(
            new_card_name=new_card_name,
            new_card_id=new_card_id,
            new_card_summary=new_card_summary,
            new_card_rel=new_card_rel,
            target_name=target_name,
            target_summary=target_summary,
        )

        try:
            response = await self.llm.generate(
                messages=[{"role": "user", "content": prompt}],
                model=self.llm.models.card_generation,
                temperature=0.3,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            parsed = safe_parse_json(response.content)
            if isinstance(parsed, dict) and "target" in parsed:
                # Ensure target is set correctly
                parsed["target"] = new_card_id
                # Clamp trust
                if "trust" in parsed:
                    parsed["trust"] = max(-50, min(50, int(parsed["trust"])))
                # doesnt_know is extracted separately, not part of the relationship entry
                return parsed
        except Exception as e:
            logger.warning("Reciprocal relationship generation failed: %s", e)

        return None

    async def _find_card_any_type(self, name: str, rp_folder: str) -> dict | None:
        """Look up a card row by name in any type within an RP folder.

        Tries normalized name, entity ID, then alias lookup. (Moved out of the
        router ``_lookup`` package in Phase 6b — reciprocal sync is its only
        caller, and a service importing from a router package is backwards.)
        """
        key = normalize_key(name)
        entity_id = f"{rp_folder}:{key}"

        row = await self.db.fetch_one(
            "SELECT * FROM story_cards WHERE rp_folder = ? AND (LOWER(name) = ? OR id = ?)",
            [rp_folder, key, entity_id],
        )
        if row:
            return row

        # Alias fallback
        row = await self.db.fetch_one(
            """SELECT sc.* FROM story_cards sc
               JOIN entity_aliases ea ON sc.id = ea.entity_id
               WHERE sc.rp_folder = ? AND ea.alias = ?""",
            [rp_folder, key],
        )
        return row
