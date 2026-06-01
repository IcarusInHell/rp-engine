"""Service: NPCEngine.get_reaction (+ batch) and the branched-history drop.

Two jobs:

* LOCK the public reaction API the Phase 1 extraction must preserve — a queued
  reaction JSON round-trips into an ``NPCReaction``; the batch variant returns
  one per NPC.
* xfail the High-priority ancestry bug (Phase 1): on a fresh child branch the
  NPC's recent-exchange load must see the *parent's* history. Today
  ``_load_recent_exchanges`` does a branch-scoped query with no ancestry walk
  (NPCEngine has no ``branch_manager`` yet), so it silently returns []. The
  signature is contract-preserved across the Phase 1 fix, so this strict xfail
  flips to a hard pass cleanly once ``branch_manager`` is wired in.
"""

from __future__ import annotations

import json

from rp_engine.models.npc import NPCReaction
from tests.assertions import assert_count_exact, assert_present


def _reaction_json(character: str, dialogue: str) -> str:
    return json.dumps(
        {
            "character": character,
            "internalMonologue": "She weighs the stranger's intent.",
            "physicalAction": "Sets down the glass she was drying.",
            "dialogue": dialogue,
            "emotionalUndercurrent": "wary",
            "trustShift": {"direction": "neutral", "amount": 0, "reason": None},
        }
    )


async def test_get_reaction_parses_into_model(seeded_rp):
    """A queued JSON reaction round-trips into a populated NPCReaction.

    Diagnostics stay attached (the always-injected DiagnosticLogger): the Phase 1
    payload fix means the diagnostics-on path must not drop the reaction.
    """
    fake = seeded_rp.container.fake_provider
    fake.queue(_reaction_json("Alice", "What'll it be, then?"))

    reaction = await seeded_rp.container.npc_engine.get_reaction(
        npc_name="Alice",
        scene_prompt="A stranger steps into the tavern.",
        pov_character="TestPC",
        rp_folder=seeded_rp.rp_folder,
        branch=seeded_rp.main_branch,
    )

    assert isinstance(reaction, NPCReaction)
    assert reaction.character == "Alice"
    assert reaction.dialogue == "What'll it be, then?", "the LLM dialogue was dropped"
    assert reaction.emotionalUndercurrent == "wary"


async def test_get_batch_reactions_one_per_npc(seeded_rp):
    """Batch returns one reaction per requested NPC, in correspondence.

    Diagnostics stay attached — the diagnostic-payload bug previously silently
    dropped every reaction to [] here (``return_exceptions=True`` + filter).
    """
    fake = seeded_rp.container.fake_provider
    fake.queue(
        _reaction_json("Alice", "Mind the step."),
        _reaction_json("Bob", "Evening, friend."),
    )

    reactions = await seeded_rp.container.npc_engine.get_batch_reactions(
        npc_names=["Alice", "Bob"],
        scene_prompt="Two patrons size each other up.",
        pov_character="TestPC",
        rp_folder=seeded_rp.rp_folder,
        branch=seeded_rp.main_branch,
    )

    assert_count_exact(len(reactions), 2, label="batch reactions returned")
    assert {r.character for r in reactions} == {"Alice", "Bob"}


async def test_get_reaction_diagnostic_payload_uses_real_fields(seeded_rp):
    """The diagnostics-on path must not drop a parsed reaction (Phase 1 fix).

    Previously ``get_reaction`` built its diagnostic-log payload from
    ``reaction.trust_delta`` / ``reaction.emotional_state`` — attributes that do
    not exist — so every parsed reaction raised AttributeError under the always-
    injected DiagnosticLogger (propagating in the single path, silently dropped to
    [] in batch). ``_log_npc_reaction`` now reads the real
    ``trustShift`` / ``emotionalUndercurrent`` fields. Logger stays attached.
    """
    fake = seeded_rp.container.fake_provider
    fake.queue(_reaction_json("Alice", "What'll it be, then?"))

    reaction = await seeded_rp.container.npc_engine.get_reaction(
        npc_name="Alice",
        scene_prompt="A stranger steps into the tavern.",
        pov_character="TestPC",
        rp_folder=seeded_rp.rp_folder,
        branch=seeded_rp.main_branch,
    )
    assert reaction.character == "Alice"
    assert reaction.dialogue == "What'll it be, then?", (
        "diagnostics-on path must not drop the parsed reaction"
    )


async def test_load_recent_exchanges_on_main(seeded_rp):
    """Happy path lock: on the branch that owns them, all N exchanges load."""
    rows = await seeded_rp.container.npc_engine._load_recent_exchanges(
        seeded_rp.rp_folder, seeded_rp.main_branch, limit=10
    )
    assert_count_exact(
        len(rows), seeded_rp.n_main_exchanges, label="recent exchanges on main"
    )


async def test_fresh_branch_npc_sees_parent_history(seeded_rp):
    """A child branch with no exchanges of its own must still see the parent's.

    The Phase 1 ancestry fallback: ``_load_recent_exchanges`` walks branch
    ancestry via the wired ``branch_manager`` instead of a strict branch-scoped
    query that would silently return [].
    """
    rows = await seeded_rp.container.npc_engine._load_recent_exchanges(
        seeded_rp.rp_folder, seeded_rp.child_branch, limit=10
    )
    assert_count_exact(
        len(rows),
        seeded_rp.n_main_exchanges,
        label="parent exchanges visible to NPC on fresh branch",
    )


async def test_search_npc_history_passes_ancestry_on_fresh_branch(seeded_rp):
    """The vector-history search must scope to branch *ancestry*, not just the
    child branch — otherwise a fresh branch loses all past-interaction memory.

    Embed ranking is non-deterministic under the stub, so assert the contract
    directly: ``search_exchanges`` is called with an ``ancestry_chain`` that
    includes the parent branch (the named silent-drop point in the roadmap).
    """
    npc = seeded_rp.container.npc_engine
    captured: dict = {}

    async def _spy(**kwargs):
        captured.update(kwargs)
        return []

    npc.lance_store.search_exchanges = _spy

    await npc._search_npc_history(
        "Alice", "a quiet word by the bar", seeded_rp.rp_folder, seeded_rp.child_branch
    )

    chain = captured.get("ancestry_chain")
    assert chain, "ancestry_chain was not passed to the vector search (silent drop)"
    branches = {b for (b, _) in chain}
    assert_present(
        seeded_rp.main_branch,
        branches,
        label="parent branch in NPC history ancestry chain",
    )
