"""KnowledgeResolver — resolve character knowledge_refs into prompt-ready beliefs.

Wires up the dormant knowledge-card system. Characters carry ``knowledge_refs``
in their frontmatter (each a ``{card_id, override?, knows_reality?}`` dict);
knowledge cards (``KnowledgeFrontmatter``) carry ``believes`` / ``reality``.
Nothing previously read these. This service:

- **resolve** (read, context path): for a character, load each referenced
  knowledge card and produce a ``ResolvedKnowledge`` per ref. The card's
  ``reality`` is exposed **only** when the ref sets ``knows_reality: true`` —
  otherwise the LLM would leak truths the character doesn't know.
- **apply** (write, analysis path): consume the already-extracted
  ``KnowledgeBoundary`` items (no new LLM call) and, when an exchange shows a
  character learning something, mark the matching *existing* ref as
  ``knows_reality: true``. Precision-first: matches only the character's own
  refs, applies only on a single clear winner, and logs every skip (a wrong
  write — leaking reality for the wrong belief — is worse than no write).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from rp_engine.models.context import ResolvedKnowledge
from rp_engine.utils.frontmatter import parse_frontmatter, write_card_files
from rp_engine.utils.json_helpers import safe_parse_json
from rp_engine.utils.stemmer import stem_tokens

if TYPE_CHECKING:
    from rp_engine.models.analysis import KnowledgeBoundary
    from rp_engine.services.card_indexer import CardIndexer
    from rp_engine.services.graph_resolver import GraphResolver

logger = logging.getLogger(__name__)

# Minimum shared-stem overlap for an extracted "learned X" boundary to count as
# matching one of a character's existing knowledge refs. Precision over recall:
# below this, or on a tie for top, we skip rather than risk a wrong write.
_MIN_MATCH_OVERLAP = 2


def _as_str_list(val) -> list[str]:
    """Coerce a believes/reality field (list | str | None) to list[str]."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val.strip() else []
    if isinstance(val, list):
        return [str(v) for v in val if str(v).strip()]
    return []


class KnowledgeResolver:
    """Resolves character knowledge refs and applies runtime knowledge changes."""

    def __init__(
        self,
        db,
        graph_resolver: GraphResolver,
        card_indexer: CardIndexer | None = None,
        vault_root: Path | None = None,
    ):
        self.db = db
        self.graph_resolver = graph_resolver
        # card_indexer + vault_root are only needed for the write (apply) path.
        self.card_indexer = card_indexer
        self.vault_root = vault_root

    # ------------------------------------------------------------------
    # Read path (context engine)
    # ------------------------------------------------------------------

    async def resolve_for_character(
        self, character_id: str, rp_folder: str
    ) -> list[ResolvedKnowledge]:
        """Resolve a character's ``knowledge_refs`` into ``ResolvedKnowledge``.

        ``character_id`` is the story_cards entity_id (``folder:name``). Returns
        an empty list if the character has no card or no refs. Missing knowledge
        cards are skipped gracefully (logged, never raised).
        """
        row = await self.db.fetch_one(
            "SELECT frontmatter FROM story_cards WHERE id = ?", [character_id]
        )
        if not row:
            return []
        fm = safe_parse_json(row.get("frontmatter"))
        return await self.resolve_refs(fm.get("knowledge_refs") or [], rp_folder)

    async def resolve_refs(
        self, knowledge_refs: list, rp_folder: str
    ) -> list[ResolvedKnowledge]:
        """Core resolution: each ref dict → a ``ResolvedKnowledge`` (or skip)."""
        resolved: list[ResolvedKnowledge] = []
        if not isinstance(knowledge_refs, list):
            return resolved

        for ref in knowledge_refs:
            if not isinstance(ref, dict):
                continue
            card_id = ref.get("card_id")
            if not card_id:
                continue

            card_fm = await self._load_knowledge_card(card_id, rp_folder)
            if card_fm is None:
                # Dangling reference — surfaced, not silently dropped.
                logger.warning(
                    "knowledge_ref %s for rp %s resolves to no card — skipped",
                    card_id, rp_folder,
                )
                continue

            override = ref.get("override")
            knows_reality = bool(ref.get("knows_reality"))

            believes = [override] if override else _as_str_list(card_fm.get("believes"))
            # Reality is gated on the structured knows_reality flag, NOT on the
            # presence of an override (an override can describe a *partial* belief
            # short of the truth — revealing reality there would leak it).
            reality = _as_str_list(card_fm.get("reality")) if knows_reality else None

            resolved.append(ResolvedKnowledge(
                card_id=str(card_id),
                topic=card_fm.get("topic") or card_fm.get("name"),
                believes=believes,
                reality=reality,
                confidence=card_fm.get("confidence"),
                source=card_fm.get("source"),
                knows_reality=knows_reality,
            ))

        return resolved

    # ------------------------------------------------------------------
    # Write path (analysis pipeline)
    # ------------------------------------------------------------------

    async def apply_knowledge_change(
        self, boundary: KnowledgeBoundary, rp_folder: str
    ) -> bool:
        """Mark a character's matching existing ref as ``knows_reality: true``.

        Returns True iff a card file was updated. Conservative by design:
        - only the character's *own* existing refs are candidates (no auto-create
          of cards or refs — Q3 deferred);
        - applies only when exactly one ref clearly matches the learned info;
        - any zero/ambiguous match is logged and skipped (a wrong write leaks the
          wrong reality, which is worse than missing one).
        """
        if self.card_indexer is None or self.vault_root is None:
            return False
        who = (boundary.who or "").strip()
        learned = (boundary.learned or "").strip()
        if not who or not learned:
            return False

        char_id = await self.graph_resolver.resolve_entity(who, rp_folder)
        if not char_id:
            logger.info("knowledge change: character %r not resolved — skipped", who)
            return False

        row = await self.db.fetch_one(
            "SELECT file_path, content, frontmatter FROM story_cards WHERE id = ?",
            [char_id],
        )
        if not row:
            return False
        fm = safe_parse_json(row.get("frontmatter"))
        refs = fm.get("knowledge_refs")
        if not isinstance(refs, list) or not refs:
            logger.info(
                "knowledge change for %r: no existing knowledge_refs — skipped "
                "(auto-create deferred)", who,
            )
            return False

        # Score each existing ref against the learned info by stemmed overlap.
        learned_tokens = stem_tokens(f"{learned} {boundary.evidence or ''}")
        scored: list[tuple[int, dict]] = []
        for ref in refs:
            if not isinstance(ref, dict) or not ref.get("card_id"):
                continue
            card_fm = await self._load_knowledge_card(ref["card_id"], rp_folder)
            if card_fm is None:
                continue
            card_text = " ".join([
                str(card_fm.get("topic") or ""),
                str(card_fm.get("name") or ""),
                " ".join(_as_str_list(card_fm.get("reality"))),
                " ".join(_as_str_list(card_fm.get("believes"))),
            ])
            overlap = len(learned_tokens & stem_tokens(card_text))
            if overlap >= _MIN_MATCH_OVERLAP:
                scored.append((overlap, ref))

        if not scored:
            logger.info("knowledge change for %r: no ref matched %r — skipped", who, learned)
            return False
        scored.sort(key=lambda s: s[0], reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            logger.info(
                "knowledge change for %r: ambiguous match for %r (tie) — skipped",
                who, learned,
            )
            return False

        target_ref = scored[0][1]
        if target_ref.get("knows_reality") is True:
            return False  # already knows — nothing to write
        target_ref["knows_reality"] = True
        target_ref["override"] = learned

        fm["knowledge_refs"] = refs
        _, body = parse_frontmatter(row.get("content") or "")
        card_file = self.vault_root / row["file_path"]
        write_card_files(card_file.parent, card_file.stem, fm, body)
        await self.card_indexer.index_file(rp_folder, card_file)
        logger.info("knowledge change applied: %r now knows reality of %s",
                    who, target_ref.get("card_id"))
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_knowledge_card(self, card_id: str, rp_folder: str) -> dict | None:
        """Resolve a knowledge card_id → its frontmatter dict, or None."""
        entity_id = await self.graph_resolver.resolve_entity(str(card_id), rp_folder)
        if not entity_id:
            return None
        row = await self.db.fetch_one(
            "SELECT frontmatter, card_type FROM story_cards WHERE id = ?", [entity_id]
        )
        if not row:
            return None
        return safe_parse_json(row.get("frontmatter"))
