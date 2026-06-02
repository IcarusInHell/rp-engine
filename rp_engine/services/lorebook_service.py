"""Lorebook matching + budget + injection assembly (Phase 5b).

Runs at Stage 2.5 beside trigger evaluation. Loads active lorebook entries
(per-RP active-set from ``Story_Guidelines.md`` frontmatter + always-available
global library), routes each entry's condition through ``TriggerEvaluator``
(REUSE — no second matcher), then greedily fills a CHAR budget by entry weight.
Dropped-but-matched entries are logged/counted (silent-drop guard).

Budget is character-based, mirroring the Phase 3 tiered-context ``content[:cap]``
slicing — there is no token estimator to reuse here, just ``len()``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rp_engine.config import get_config
from rp_engine.database import Database
from rp_engine.models.context import LorebookEntryHit
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.trigger_evaluator import TriggerEvaluator
from rp_engine.utils.json_helpers import safe_parse_json_array
from rp_engine.utils.provenance import record_drop
from rp_engine.utils.stemmer import stem, tokenize

logger = logging.getLogger(__name__)


class LorebookService:
    """Active-set resolution → reuse-evaluator matching → char-budget fill."""

    def __init__(
        self,
        db: Database,
        trigger_evaluator: TriggerEvaluator,
        guidelines_service: GuidelinesService,
    ) -> None:
        self.db = db
        # Reuse is concrete in the DI graph: the lorebook holds the SAME
        # evaluator the triggers use.
        self.trigger_evaluator = trigger_evaluator
        self.guidelines_service = guidelines_service

    async def get_active_hits(
        self,
        rp_folder: str,
        branch: str,
        combined_text: str,
        signals: dict[str, float],
    ) -> list[LorebookEntryHit]:
        """Match active lorebook entries against the turn text and budget-fill.

        ``branch`` is forwarded to the evaluator for ``state`` conditions even
        though lorebook *storage* has no branch column (entries are RP-global /
        global-library reference content, visible on every branch).
        """
        cfg = get_config().context
        if not cfg.lorebook_enabled:
            return []

        # Active-set: an explicit ``lorebooks: [...]`` frontmatter list selects
        # per-RP files by stem; absent → ALL per-RP files active (file-drop-and-go).
        active_stems: list[str] | None = None
        guidelines = self.guidelines_service.get_guidelines(rp_folder)
        if guidelines is not None:
            active_stems = guidelines.lorebooks

        rp_rows = await self._load_entries("rp", rp_folder)
        if active_stems is not None:
            allow = set(active_stems)
            rp_rows = [r for r in rp_rows if Path(r["source_path"]).stem in allow]
        global_rows = await self._load_entries("global", None)

        rp_hits = await self._match_rows(rp_rows, combined_text, signals, rp_folder, branch)
        global_hits = await self._match_rows(global_rows, combined_text, signals, rp_folder, branch)

        kept = self._budget(rp_hits, cfg.lorebook_budget_chars, "rp")
        kept += self._budget(global_hits, cfg.lorebook_shared_budget_chars, "global")
        return kept

    async def _load_entries(self, scope: str, rp_folder: str | None) -> list[dict]:
        # NB: NO branch filter — a per-RP entry must appear on every branch.
        if scope == "rp":
            return await self.db.fetch_all(
                "SELECT * FROM lorebook_entries WHERE scope = 'rp' AND rp_folder = ? AND enabled = 1",
                [rp_folder],
            )
        return await self.db.fetch_all(
            "SELECT * FROM lorebook_entries WHERE scope = 'global' AND enabled = 1",
        )

    async def _match_rows(
        self,
        rows: list[dict],
        combined_text: str,
        signals: dict[str, float],
        rp_folder: str,
        branch: str,
    ) -> list[LorebookEntryHit]:
        hits: list[LorebookEntryHit] = []
        for row in rows:
            conditions, match_mode = self._effective_conditions(row)
            # Keyword-DERIVED entries (no authored conditions) get the
            # word-boundary precision gate below; explicit authored conditions
            # are respected verbatim (the author chose near/substring semantics).
            keyword_derived = not safe_parse_json_array(row.get("conditions"))
            keywords = [
                str(k) for k in (safe_parse_json_array(row.get("keywords")) or [])
                if str(k).strip()
            ]

            if row.get("always_on"):
                matched, matched_raw, descs = True, True, ["always-on"]
            elif conditions:
                matched, matched_raw, descs = await self.trigger_evaluator.evaluate_conditions(
                    conditions, match_mode, combined_text, signals, rp_folder, branch
                )
            else:
                # No always_on, no conditions, no keywords → unmatchable; skip.
                continue

            if not matched:
                continue

            # Word-boundary precision gate (delivers the stated deliverable
            # "art ∌ start" without forking the shared evaluator): a keyword-
            # derived hit that matched only as a substring INSIDE a larger word
            # is dropped. The evaluator still does the matching; this gates its
            # accepted keyword hits. Single-token keywords only — multi-word
            # phrases keep substring semantics (the evaluator's n-gram precedent).
            if (keyword_derived and keywords and not row.get("always_on")
                    and not self._keyword_word_boundary(keywords, combined_text)):
                logger.debug(
                    "Lorebook entry %s (%s) dropped: keyword matched only as a "
                    "substring inside a larger word (no word-boundary hit)",
                    row.get("id"), row.get("name"),
                )
                record_drop(
                    "lorebook.word_boundary", "lorebook_entry", "substring_only",
                    item_id=str(row.get("id")), detail=row.get("name"),
                )
                continue

            stem_only = bool(matched and not matched_raw)
            if stem_only:
                logger.warning(
                    "Lorebook entry %s (%s) matched ONLY via stemming "
                    "(exact match would miss): %s",
                    row.get("id"), row.get("name"), descs,
                )
            hits.append(LorebookEntryHit(
                entry_id=row["id"],
                name=row.get("name") or "entry",
                content=row["content"],
                scope=row["scope"],
                budget_weight=row.get("budget_weight", 1) or 1,
                depth=row.get("depth"),
                matched_conditions=descs,
                stem_only=stem_only,
            ))
        return hits

    @staticmethod
    def _effective_conditions(row: dict) -> tuple[list[dict], str]:
        """Resolve the condition list to feed the evaluator.

        Explicit ``conditions`` (refined entries) win. Otherwise derive a single
        ``any("k1","k2",...)`` keyword condition (coarse entries). Compound scope
        is always a condition LIST, never a nested expression.
        """
        stored = safe_parse_json_array(row.get("conditions"))
        if stored:
            return stored, row.get("match_mode", "all")
        keywords = safe_parse_json_array(row.get("keywords")) or []
        keywords = [str(k) for k in keywords if str(k).strip()]
        if keywords:
            quoted = ",".join(f'"{k}"' for k in keywords)
            return [{"type": "expression", "expr": f"any({quoted})"}], "any"
        return [], "any"

    @staticmethod
    def _keyword_word_boundary(keywords: list[str], text: str) -> bool:
        """True if any keyword matches ``text`` at a WORD BOUNDARY (delivers the
        Phase 5b "art ∌ start" precision criterion for keyword-derived entries).

        Single-token keywords match only as a whole token — raw token membership
        OR stemmed-token membership (so morphology still works: ``cat``↔``cats``).
        Multi-word keywords keep substring semantics (phrases stay exact, matching
        the evaluator's n-gram-exact precedent). Reuses ``utils/stemmer`` — the
        SAME tokenizer/stemmer the evaluator uses, not a second one.
        """
        text_tokens = set(tokenize(text))
        text_stems = {stem(t) for t in text_tokens}
        for kw in keywords:
            toks = tokenize(kw)
            if len(toks) == 1:
                if toks[0] in text_tokens or stem(toks[0]) in text_stems:
                    return True
            elif kw.lower() in text.lower():
                return True
        return False

    @staticmethod
    def _budget(
        hits: list[LorebookEntryHit], budget_chars: int, scope: str
    ) -> list[LorebookEntryHit]:
        """Greedily keep highest-weight entries within a char budget.

        Dropped-but-matched entries are logged/counted (fail-loud, not silent)."""
        # Highest weight first; stable so equal weights keep load order (recency).
        ordered = sorted(hits, key=lambda h: h.budget_weight, reverse=True)
        kept: list[LorebookEntryHit] = []
        used = 0
        dropped = 0
        for h in ordered:
            cost = len(h.content)
            if used + cost <= budget_chars:
                kept.append(h)
                used += cost
            else:
                dropped += 1
                record_drop(
                    "lorebook.budget", "lorebook_entry", "over_budget",
                    item_id=str(h.entry_id), score=h.budget_weight,
                    detail=f"{scope} cost={cost}",
                )
        if dropped:
            logger.info(
                "Lorebook budget (%s): kept %d, DROPPED %d matched entries "
                "(budget=%d chars, used=%d)",
                scope, len(kept), dropped, budget_chars, used,
            )
        return kept
