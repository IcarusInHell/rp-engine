"""Service: ContextEngine.get_context — the assembly the Phase 2 decomposition preserves.

The silent-drop risk here is a card that should be retrieved going missing from
the assembled context (the ``attach_card_ids`` family of bugs). We anchor the
presence assertions on the DETERMINISTIC retrieval paths — naming seeded
entities ("Alice", "The Salt Tavern") in the user message so they're pulled via
entity-extraction → keyword/graph resolution, not the hash-embedding semantic
ranker (whose query↔card similarity is effectively random under the test stub).

NPC reactions are turned off so the assembly stays LLM-free and deterministic;
their generation is covered in test_npc_engine.py.
"""

from __future__ import annotations

from rp_engine.models.context import ContextRequest, DetectedNPC, ExtractionResult
from tests.assertions import assert_nonempty, assert_present
from tests.factories import seed_trust_baseline


async def test_get_context_retrieves_named_cards(seeded_rp):
    """Entities named in the message are assembled into the context documents."""
    request = ContextRequest(
        user_message="Alice waits at The Salt Tavern, watching the door for Bob.",
        include_npc_reactions=False,
        pov_character="TestPC",
    )

    response = await seeded_rp.container.context_engine.get_context(
        request, seeded_rp.rp_folder, seeded_rp.main_branch
    )

    assert_nonempty(response.documents, label="assembled context documents")
    doc_names = {d.name for d in response.documents}
    assert_present("Alice", doc_names, label="context documents")


async def test_get_context_has_scene_state(seeded_rp):
    """The response always carries a scene_state block (never dropped to None)."""
    request = ContextRequest(
        user_message="Alice tends the bar.",
        include_npc_reactions=False,
        pov_character="TestPC",
    )

    response = await seeded_rp.container.context_engine.get_context(
        request, seeded_rp.rp_folder, seeded_rp.main_branch
    )

    assert response.scene_state is not None, "scene_state was dropped from the context"
    assert response.current_exchange is not None


async def test_get_context_documents_carry_content(seeded_rp):
    """A retrieved document must carry its card body, not an empty shell."""
    request = ContextRequest(
        user_message="Alice and Bob meet at The Salt Tavern.",
        include_npc_reactions=False,
        pov_character="TestPC",
    )

    response = await seeded_rp.container.context_engine.get_context(
        request, seeded_rp.rp_folder, seeded_rp.main_branch
    )

    alice_docs = [d for d in response.documents if d.name == "Alice"]
    assert_nonempty(alice_docs, label="Alice document in context")
    assert_nonempty(alice_docs[0].content, label="Alice card content")


async def test_npc_brief_pre_trust_is_directional_npc_to_pc(seeded_rp):
    """B1: an NPC brief's trust_score is the npc→pc direction only.

    The old code summed every trust pair touching the NPC (both directions, all
    partners), conflating unrelated relationships; the directional fix reads how
    much THIS NPC trusts the PC. Mara (brief-worthy) trusts the PC at 5 and Bob at
    10 — her brief must report 5, never 15 (the sum bug) and never 10 (Mara→Bob).
    """
    db = seeded_rp.db
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch
    pc = "TestPC"

    # Brief-worthy NPC card (importance ∈ BRIEF_IMPORTANCE). Cards are global per
    # rp_folder (no branch column); this is independent of the vault's Alice/Bob.
    fut = await db.enqueue_write(
        """INSERT INTO story_cards
               (id, rp_folder, file_path, card_type, name, importance, frontmatter)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ["mara-id", rp, "Characters/mara.md", "character", "Mara", "main", "{}"],
    )
    await fut

    # Directional baselines: Mara→PC = 5, Mara→Bob = 10 (distinct on purpose).
    await seed_trust_baseline(db, "Mara", pc, 5, rp_folder=rp, branch=branch)
    await seed_trust_baseline(db, "Mara", "Bob", 10, rp_folder=rp, branch=branch)

    extraction = ExtractionResult(
        active_npcs=[
            DetectedNPC(entity_id="mara-id", name="Mara", detection_reason="named")
        ],
    )

    npc_briefs, _flagged = await seeded_rp.container.context_engine._build_npc_briefs(
        extraction, {}, pc, rp, branch
    )

    mara = next((b for b in npc_briefs if b.character == "Mara"), None)
    assert mara is not None, "Mara (importance=main) must produce an NPC brief"
    assert mara.trust_score == 5, (
        "B1 DIRECTION: brief trust must be Mara→PC (5), not the sum-over-all-pairs "
        f"(15) or the Mara→Bob direction (10); got {mara.trust_score}"
    )
