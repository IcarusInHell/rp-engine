"""Phase 3 — Tiered context injection.

Cards are injected at one of three depths based on their relevance score:
  full      — complete card body (always_load, or score >= tier_thresholds.full)
  brief     — compact summary    (tier_thresholds.brief <= score < full)
  reference — one-line mention    (score < tier_thresholds.brief)

Two silent-drop / budget risks are locked here:

1. **Briefed-NPC dedup must be PRE-slice.** An NPC that already has a structured
   brief must not also consume a `max_documents` slot — but the exclusion has to
   happen *before* the `[:max_documents]` cut so a lower-ranked card backfills the
   freed slot. A post-slice drop would silently ship fewer documents. The
   `_collect_and_rank` test proves both the drop AND the backfill; the full
   `get_context` test proves the seam (a briefed NPC reaching `documents` is the
   green-by-omission trap — it can only bite if the NPC is *also* a document
   candidate, so the test names a main-importance NPC that keyword-matches).

2. **Tier formatting must respect the tier.** The assembler renders full/brief/
   reference differently; a card mis-formatted (full body where a one-liner was
   intended) wastes budget. Mutation-proven by per-tier rendering assertions.
"""

from __future__ import annotations

from rp_engine.config import ContextConfig, TierThresholds
from rp_engine.models.context import (
    ContextDocument,
    ContextRequest,
    ContextResponse,
    ExtractionResult,
)
from tests.conftest import RP_FOLDER


# ---------------------------------------------------------------------------
# _assign_tier — score → tier (pure, config-driven)
# ---------------------------------------------------------------------------


def test_assign_tier_thresholds(built_container):
    """Default thresholds (full=1.0, brief=0.6) bucket the source scores: keyword
    1.0 → full, semantic 0.8 / trigger 0.9 / graph-1hop 0.6 → brief, graph-2hop
    0.3 → reference. The 0.8→brief assertion is load-bearing: if the boundary
    regressed to a looser `>= 0.6 → full`, it would go red."""
    ce = built_container.context_engine
    assert ce._assign_tier("keyword", 1.0) == "full"
    assert ce._assign_tier("trigger", 0.9) == "brief"
    assert ce._assign_tier("semantic", 0.8) == "brief"
    assert ce._assign_tier("graph", 0.6) == "brief"
    assert ce._assign_tier("graph", 0.3) == "reference"
    assert ce._assign_tier("graph", 0.59) == "reference"


def test_assign_tier_always_load_is_always_full(built_container):
    """always_load cards are FULL regardless of score — main characters need full
    detail every turn (design decision). A score below the brief cutoff must NOT
    demote them."""
    ce = built_container.context_engine
    assert ce._assign_tier("always_load", 0.0) == "full"
    assert ce._assign_tier("always_load", 2.0) == "full"


def test_assign_tier_reads_config_thresholds(built_container):
    """Tiers are threshold-driven, not hardcoded: lowering `full` to 0.5 promotes
    a 0.8 card to full. Proves the cutoff is read from config (hot-reloadable)."""
    ce = built_container.context_engine
    assert ce._assign_tier("semantic", 0.8) == "brief"  # default
    ce._config_override = ContextConfig(tier_thresholds=TierThresholds(full=0.5))
    assert ce._assign_tier("semantic", 0.8) == "full"


# ---------------------------------------------------------------------------
# _tier_fields — what (content, summary) a tier carries
# ---------------------------------------------------------------------------


def test_tier_fields_full_carries_body(built_container):
    ce = built_container.context_engine
    card = {"content": "Full body text.", "summary": "short"}
    content, summary = ce._tier_fields(card, "full")
    assert content == "Full body text."
    assert summary is None


def test_tier_fields_brief_uses_summary_not_body(built_container):
    """Brief tier drops the body, carrying the compact summary instead. Mutation:
    if brief returned the body in `content`, the first assertion reddens."""
    ce = built_container.context_engine
    card = {"content": "A" * 500, "summary": "compact summary"}
    content, summary = ce._tier_fields(card, "brief")
    assert content is None
    assert summary == "compact summary"


def test_tier_fields_brief_falls_back_to_body_head(built_container):
    """No summary → brief uses the first 200 chars of the body."""
    ce = built_container.context_engine
    card = {"content": "B" * 500, "summary": None}
    content, summary = ce._tier_fields(card, "brief")
    assert content is None
    assert summary == "B" * 200


def test_tier_fields_reference_is_one_short_line(built_container):
    """Reference tier collapses to a single line, ≤120 chars."""
    ce = built_container.context_engine
    card = {"content": "first line\nsecond line", "summary": None}
    content, summary = ce._tier_fields(card, "reference")
    assert content is None
    assert summary == "first line"

    long = {"content": None, "summary": "x" * 200}
    _, ref = ce._tier_fields(long, "reference")
    assert ref is not None and len(ref) == 120


# ---------------------------------------------------------------------------
# _collect_and_rank_cards — pre-slice dedup + backfill
# ---------------------------------------------------------------------------


def _kw_card(i: int) -> dict:
    return {
        "id": f"k{i}",
        "name": f"Card{i}",
        "card_type": "lore",
        "file_path": f"c{i}.md",
        "content": f"body {i}",
        "summary": None,
        "content_hash": f"h{i}",
    }


async def test_collect_and_rank_excludes_briefed_npc_and_backfills(seeded_rp):
    """Six keyword cards (all score 1.0), max_documents=5. Excluding k1 must drop
    it AND let k6 backfill the freed slot — proving the exclusion is PRE-slice.

    Mutation (remove the exclusion in _collect_and_rank_cards): k1 survives into
    the top-5 and k6 falls off the slice → both assertions go red."""
    ce = seeded_rp.container.context_engine
    keyword_cards = [_kw_card(i) for i in range(1, 7)]  # k1..k6

    ranked = await ce._collect_and_rank_cards(
        ExtractionResult(),  # no matched entities → no graph expansion
        [],  # always_load
        keyword_cards,
        [],  # semantic
        [],  # trigger
        {"k1"},  # exclude k1 (a briefed NPC's card)
    )

    ids = [eid for eid, _ in ranked]
    assert len(ids) == 5, f"max_documents=5 must be respected after dedup; got {len(ids)}"
    assert "k1" not in ids, "excluded (briefed) card must not appear in documents"
    assert "k6" in ids, "a lower-ranked card must backfill the freed slot (PRE-slice dedup)"


# ---------------------------------------------------------------------------
# _filter_sent_cards — the tier-assignment call-site (the phase's headline wiring)
# ---------------------------------------------------------------------------


def _ranked(eid: str, source: str, score: float) -> tuple:
    card = {
        "id": eid,
        "name": eid.upper(),
        "card_type": "lore",
        "file_path": f"{eid}.md",
        "content": "BODY",
        "summary": "SUM",
        "content_hash": f"h-{eid}",
    }
    return (eid, (card, source, score))


async def test_filter_sent_cards_assigns_tier_at_the_call_site(seeded_rp):
    """The load-bearing wiring: a real `_filter_sent_cards` run must assign a
    NON-full tier to lower-scored cards and null out their body accordingly.

    Deterministic on purpose — passes hand-built ranked tuples (no semantic
    randomness) and session_id=None (no context_sent suppression) so it exercises
    the live `_assign_tier`/`_tier_fields` call-sites. Mutation (hardcode
    `tier = "full"` at the call-site, i.e. a regression to pre-phase all-full
    behavior): the brief/reference tier and the content-is-None assertions go red.
    This is the discriminating check the unit/seam/formatting tests all dodge.
    """
    ce = seeded_rp.container.context_engine
    top_cards = [
        _ranked("a", "keyword", 1.0),   # → full
        _ranked("b", "semantic", 0.8),  # → brief
        _ranked("c", "graph", 0.3),     # → reference
    ]

    docs, _ = await ce._filter_sent_cards(top_cards, None, 1)

    by_name = {d.name: d for d in docs}
    assert by_name["A"].injection_tier == "full"
    assert by_name["B"].injection_tier == "brief"
    assert by_name["C"].injection_tier == "reference"

    # full keeps the body; brief/reference drop it for the compact summary.
    assert by_name["A"].content == "BODY"
    assert by_name["B"].content is None and by_name["B"].summary == "SUM"
    assert by_name["C"].content is None and by_name["C"].summary == "SUM"


# ---------------------------------------------------------------------------
# get_context seam — a briefed NPC is deduped from documents
# ---------------------------------------------------------------------------


async def test_get_context_dedups_briefed_npc_from_documents(seeded_rp):
    """A main-importance NPC named (and acting) in the message is detected as an
    active NPC → gets a brief, and also name-matches into the card pool. It must
    appear in npc_briefs but NOT in documents.

    This is the seam guard (advisor-caught green-by-omission risk): the NPC is
    deliberately a document candidate, so commenting out the `if eid not in
    exclude_ids` skip makes Vincent appear in BOTH and the dedup assertion reddens.
    """
    db = seeded_rp.db
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    fut = await db.enqueue_write(
        """INSERT INTO story_cards
               (id, rp_folder, file_path, card_type, name, importance, frontmatter, content, summary, content_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            "vincent-id", rp, "Characters/vincent.md", "character", "Vincent",
            "main", "{}", "Vincent is a brooding swordsman.", "A swordsman.", "vh1",
        ],
    )
    await fut

    request = ContextRequest(
        user_message="Vincent draws his sword and speaks to the room.",
        include_npc_reactions=False,
        pov_character="TestPC",
    )
    response = await seeded_rp.container.context_engine.get_context(request, rp, branch)

    brief_names = {b.character for b in response.npc_briefs}
    doc_names = {d.name for d in response.documents}
    assert "Vincent" in brief_names, "main-importance active NPC must get a brief"
    assert "Vincent" not in doc_names, (
        "a briefed NPC's card must be deduped from documents (covered by the brief)"
    )
    # And the brief carries the card_id used for the dedup.
    vincent = next(b for b in response.npc_briefs if b.character == "Vincent")
    assert vincent.card_id == "vincent-id"


# ---------------------------------------------------------------------------
# Prompt assembler — tier-aware formatting
# ---------------------------------------------------------------------------


def _doc(name: str, tier: str, *, content=None, summary=None) -> ContextDocument:
    return ContextDocument(
        name=name,
        card_type="lore",
        file_path=f"{name}.md",
        source="keyword",
        relevance_score=1.0,
        content=content,
        summary=summary,
        status="new",
        injection_tier=tier,
    )


def test_prompt_formats_each_tier_distinctly(built_container):
    """full → body, brief → `*Summary:*`, reference → `- Name (type) — …` bullet.

    Mutation (assembler ignores injection_tier and always prints content): the
    reference card's body would render under a `## Refcard` header instead of a
    one-line bullet, and the brief card's full content would leak — reddening the
    tier-specific assertions."""
    docs = [
        _doc("Fullcard", "full", content="THE-FULL-BODY-MARKER", summary="ignored"),
        _doc("Briefcard", "brief", summary="THE-BRIEF-SUMMARY-MARKER"),
        _doc("Refcard", "reference", summary="THE-REF-LINE-MARKER"),
    ]
    ctx = ContextResponse(current_exchange=1, documents=docs)
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, ctx)

    # Full: complete body under a section header.
    assert "## Fullcard (lore)" in prompt
    assert "THE-FULL-BODY-MARKER" in prompt

    # Brief: summary line, NOT a raw body dump.
    assert "*Summary:* THE-BRIEF-SUMMARY-MARKER" in prompt

    # Reference: a one-line bullet, never a `## Refcard` header.
    assert "- Refcard (lore) — THE-REF-LINE-MARKER" in prompt
    assert "## Refcard" not in prompt, "reference tier must not emit a section header"
