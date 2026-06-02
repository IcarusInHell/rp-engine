"""Knowledge boundaries: resolution, prompt injection, and runtime updates.

Locks the Phase 2 contract for the knowledge-card system:
- a character's ``knowledge_refs`` resolve to ``ResolvedKnowledge`` beliefs;
- the card's ``reality`` is exposed ONLY when the ref sets ``knows_reality``
  (the knowledge-bleed guard — the load-bearing correctness property);
- the prompt grows a ``# Knowledge Boundaries`` section keyed by display name;
- the analysis pipeline's already-extracted ``KnowledgeBoundary`` items can mark
  a matching existing ref as known-truth, precision-first (no wrong writes).

Mutation-proven where noted: each guard goes red if its target is reverted.
"""

from __future__ import annotations

from rp_engine.models.analysis import KnowledgeBoundary
from rp_engine.models.context import (
    ContextResponse,
    DetectedNPC,
    ExtractionResult,
    NPCBrief,
    ResolvedKnowledge,
)
from rp_engine.utils.frontmatter import write_card_files
from tests.conftest import RP_FOLDER

_KB_CARD = {
    "type": "knowledge",
    "card_id": "kb_wall_monsters",
    "topic": "The Wall and What Lies Beyond",
    "name": "Wall Monsters",
    "believes": ["Monsters live beyond the walls"],
    "reality": ["There are no monsters beyond the walls"],
    "confidence": "high",
    "source": "childhood teachings",
}


async def _setup_alice_with_refs(container, refs, *, with_kb_card=True):
    """Write the knowledge card + rewrite Alice's card with the given refs,
    reindex both, and return (alice_entity_id)."""
    rp = RP_FOLDER
    cards = container.vault_root / rp / "Story Cards"
    if with_kb_card:
        kdir = cards / "Knowledge"
        kdir.mkdir(parents=True, exist_ok=True)
        write_card_files(kdir, "kb_wall_monsters", _KB_CARD, "Everyone fears the wall.")
        await container.card_indexer.index_file(rp, kdir / "kb_wall_monsters.md")

    chars = cards / "Characters"
    alice_fm = {
        "type": "character",
        "name": "Alice",
        "importance": "high",
        "knowledge_refs": refs,
    }
    write_card_files(chars, "alice", alice_fm, "Alice is a wary tavern keeper.")
    await container.card_indexer.index_file(rp, chars / "alice.md")

    return await container.graph_resolver.resolve_entity("Alice", rp)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def test_resolve_basic_hides_reality(built_container):
    """A plain ref (no knows_reality) → beliefs surface, reality stays hidden.

    KNOWLEDGE-BLEED GUARD. Mutation: make resolve_refs always set
    reality=card.reality and this assertion goes red.
    """
    alice_id = await _setup_alice_with_refs(
        built_container, [{"card_id": "kb_wall_monsters"}]
    )
    resolved = await built_container.knowledge_resolver.resolve_for_character(
        alice_id, RP_FOLDER
    )
    assert len(resolved) == 1
    entry = resolved[0]
    assert entry.believes == ["Monsters live beyond the walls"]
    assert entry.confidence == "high"
    assert entry.reality is None, (
        "reality must NOT be exposed when the character doesn't know the truth "
        "(knowledge bleed)"
    )
    assert entry.knows_reality is False


async def test_resolve_knows_reality_exposes_reality(built_container):
    """knows_reality: true → the card's reality is exposed. Positive control
    paired with test_resolve_basic_hides_reality (both sides of the flag)."""
    alice_id = await _setup_alice_with_refs(
        built_container, [{"card_id": "kb_wall_monsters", "knows_reality": True}]
    )
    resolved = await built_container.knowledge_resolver.resolve_for_character(
        alice_id, RP_FOLDER
    )
    assert resolved[0].reality == ["There are no monsters beyond the walls"]
    assert resolved[0].knows_reality is True


async def test_resolve_override_replaces_believes(built_container):
    """An override string replaces the card's believes list."""
    alice_id = await _setup_alice_with_refs(
        built_container,
        [{"card_id": "kb_wall_monsters", "override": "Suspects the wall is a lie"}],
    )
    resolved = await built_container.knowledge_resolver.resolve_for_character(
        alice_id, RP_FOLDER
    )
    assert resolved[0].believes == ["Suspects the wall is a lie"]
    # Override alone (a partial suspicion) must NOT leak reality.
    assert resolved[0].reality is None


async def test_resolve_missing_card_skipped(built_container):
    """A ref to a non-existent card is skipped gracefully (no error, no entry)."""
    alice_id = await _setup_alice_with_refs(
        built_container,
        [{"card_id": "kb_does_not_exist"}, {"card_id": "kb_wall_monsters"}],
        with_kb_card=True,
    )
    resolved = await built_container.knowledge_resolver.resolve_for_character(
        alice_id, RP_FOLDER
    )
    assert len(resolved) == 1, "dangling ref must be dropped, real ref kept"
    assert resolved[0].card_id == "kb_wall_monsters"


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------


def _ctx_with_knowledge(entries: dict) -> ContextResponse:
    return ContextResponse(
        current_exchange=1,
        npc_briefs=[NPCBrief(character="Alice", trust_stage="neutral")],
        knowledge_boundaries=entries,
    )


def test_prompt_shows_knowledge_section(built_container):
    """build_system_prompt grows a # Knowledge Boundaries section per character."""
    ctx = _ctx_with_knowledge({
        "Alice": [ResolvedKnowledge(
            card_id="kb_wall_monsters",
            topic="The Wall",
            believes=["Monsters live beyond the walls"],
            confidence="high",
            source="childhood teachings",
        )]
    })
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, ctx)
    assert "# Knowledge Boundaries" in prompt
    assert "## Alice" in prompt
    assert "Believes: Monsters live beyond the walls" in prompt
    assert "high, childhood teachings" in prompt


def test_prompt_hides_reality_without_knows(built_container):
    """The injected section must not print reality when knows_reality is false.

    BLEED GUARD at the prompt layer. Mutation: drop the `if entry.reality` gate
    in the assembler (always print reality) and this goes red.
    """
    ctx = _ctx_with_knowledge({
        "Alice": [ResolvedKnowledge(
            card_id="kb_wall_monsters",
            believes=["Monsters live beyond the walls"],
            reality=None,  # resolver withheld it
            knows_reality=False,
        )]
    })
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, ctx)
    assert "Knows the truth" not in prompt


def test_prompt_shows_reality_when_known(built_container):
    """Positive control: a known truth prints 'Knows the truth:'."""
    ctx = _ctx_with_knowledge({
        "Alice": [ResolvedKnowledge(
            card_id="kb_wall_monsters",
            believes=["The wall is a lie"],
            reality=["There are no monsters beyond the walls"],
            knows_reality=True,
        )]
    })
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, ctx)
    assert "Knows the truth: There are no monsters beyond the walls" in prompt


# ---------------------------------------------------------------------------
# Runtime updates (analysis pipeline step 6c)
# ---------------------------------------------------------------------------


async def test_apply_marks_matching_ref_as_known(built_container):
    """A learned-the-truth boundary marks the matching existing ref known.

    Verifies the whole write path: match → write card → reindex → re-resolve
    now exposes reality. Mutation: lower the match below threshold (rename the
    learned text to unrelated tokens) and the write doesn't happen.
    """
    alice_id = await _setup_alice_with_refs(
        built_container, [{"card_id": "kb_wall_monsters"}]
    )
    boundary = KnowledgeBoundary(
        who="Alice",
        learned="there are no monsters beyond the walls",
        evidence="she crossed the wall and saw empty plains",
        type="observation_made",
    )
    applied = await built_container.knowledge_resolver.apply_knowledge_change(
        boundary, RP_FOLDER
    )
    assert applied is True

    resolved = await built_container.knowledge_resolver.resolve_for_character(
        alice_id, RP_FOLDER
    )
    assert resolved[0].knows_reality is True
    assert resolved[0].reality == ["There are no monsters beyond the walls"]


async def test_apply_no_match_skips(built_container):
    """An unrelated learned fact matches nothing → no write, ref untouched.

    Precision guard: a wrong write would leak the wrong reality. The ref must
    stay knows_reality=false.
    """
    alice_id = await _setup_alice_with_refs(
        built_container, [{"card_id": "kb_wall_monsters"}]
    )
    boundary = KnowledgeBoundary(
        who="Alice",
        learned="the tavern serves excellent honey ale",
        evidence="a patron complimented the brew",
        type="information_shared",
    )
    applied = await built_container.knowledge_resolver.apply_knowledge_change(
        boundary, RP_FOLDER
    )
    assert applied is False

    resolved = await built_container.knowledge_resolver.resolve_for_character(
        alice_id, RP_FOLDER
    )
    assert resolved[0].knows_reality is False


async def test_apply_no_existing_refs_skips(built_container):
    """No existing refs → nothing to update (auto-create deferred, Q3)."""
    await _setup_alice_with_refs(built_container, [])
    boundary = KnowledgeBoundary(
        who="Alice",
        learned="there are no monsters beyond the walls",
        evidence="",
        type="observation_made",
    )
    applied = await built_container.knowledge_resolver.apply_knowledge_change(
        boundary, RP_FOLDER
    )
    assert applied is False


# ---------------------------------------------------------------------------
# Full-flow integration (the seam: _build_npc_briefs → knowledge_boundaries →
# prompt). The prompt-only tests above hand-build ContextResponse, so they don't
# exercise this wiring. This one does, end to end.
# ---------------------------------------------------------------------------


async def _setup_brief_npc_with_knowledge(container, refs):
    """Index a brief-worthy NPC (importance=main) carrying knowledge refs + the
    knowledge card, and return its entity_id."""
    rp = RP_FOLDER
    cards = container.vault_root / rp / "Story Cards"
    kdir = cards / "Knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    write_card_files(kdir, "kb_wall_monsters", _KB_CARD, "Everyone fears the wall.")
    await container.card_indexer.index_file(rp, kdir / "kb_wall_monsters.md")

    chars = cards / "Characters"
    write_card_files(chars, "sage", {
        "type": "character", "name": "Sage", "importance": "main",
        "knowledge_refs": refs,
    }, "Sage is an old seer who has crossed the wall.")
    await container.card_indexer.index_file(rp, chars / "sage.md")
    return await container.graph_resolver.resolve_entity("Sage", rp)


async def test_build_npc_briefs_populates_knowledge_and_reaches_prompt(built_container):
    """End-to-end seam: a briefed NPC's knowledge refs are resolved by
    _build_npc_briefs into the third return value, then render in the prompt.

    Mutation: comment out `knowledge_map[npc.name] = resolved` in
    _build_npc_briefs and this goes red (the field never populates, the prompt
    loses the section). Guards the exact silent-drop-at-a-seam this codebase
    is built to catch.
    """
    ce = built_container.context_engine
    sage_id = await _setup_brief_npc_with_knowledge(
        built_container, [{"card_id": "kb_wall_monsters", "knows_reality": True}]
    )
    extraction = ExtractionResult(
        active_npcs=[DetectedNPC(entity_id=sage_id, name="Sage", detection_reason="named")]
    )

    briefs, _flagged, knowledge_map = await ce._build_npc_briefs(
        extraction, {}, "TestPC", RP_FOLDER, "main"
    )

    assert any(b.character == "Sage" for b in briefs), "Sage (main) must get a brief"
    assert "Sage" in knowledge_map, "knowledge resolution must populate the seam"
    assert knowledge_map["Sage"][0].reality == [
        "There are no monsters beyond the walls"
    ]

    # The resolved map, carried on ContextResponse, must render in the prompt.
    response = ContextResponse(
        current_exchange=1, npc_briefs=briefs, knowledge_boundaries=knowledge_map,
    )
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, response)
    assert "# Knowledge Boundaries" in prompt
    assert "## Sage" in prompt
    assert "Knows the truth: There are no monsters beyond the walls" in prompt
