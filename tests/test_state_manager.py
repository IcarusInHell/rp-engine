"""Service: StateManager across character / trust / graph / scene / events.

Behavior locks for the Phase 3 refactor target (method extractions + facade
split must preserve every public result). The load-bearing silent-drop guard
here is DIRECTION: the sample vault seeds Alice→Bob=5 and Bob→Alice=10, and
``get_all_relationships`` must hand those back as two distinct, unmerged rows.

We anchor the directional assertion on ``get_all_relationships`` (and the graph
edges) rather than ``get_relationship`` on purpose: ``get_relationship`` is
renamed to ``_get_relationship`` in Phase 3a, which would turn an xfail into a
silently-swallowed AttributeError. ``get_all_relationships`` is already
directional today and survives the whole roadmap, so it locks cleanly now. The
foundation directional contract for the trust *utilities* lives in test_trust.py.
"""

from __future__ import annotations

from rp_engine.models.state import CharacterUpdate, SceneUpdate
from tests.assertions import assert_directional, assert_nonempty, assert_present


def _by_pair(relationships, a, b):
    for rel in relationships:
        if rel.character_a == a and rel.character_b == b:
            return rel
    return None


async def test_get_all_relationships_is_directional(seeded_rp):
    """Alice→Bob (5) and Bob→Alice (10) read back as two distinct rows."""
    sm = seeded_rp.container.state_manager
    rels = await sm.get_all_relationships(seeded_rp.rp_folder, seeded_rp.main_branch)

    ab = _by_pair(rels, "Alice", "Bob")
    ba = _by_pair(rels, "Bob", "Alice")
    assert ab is not None, "Alice→Bob relationship dropped"
    assert ba is not None, "Bob→Alice relationship dropped"
    assert ab.live_trust_score == 5, "Alice→Bob must reflect its own baseline (5)"
    assert ba.live_trust_score == 10, "Bob→Alice must reflect its own baseline (10)"
    assert_directional(
        ab.live_trust_score, ba.live_trust_score, label="live relationship trust"
    )


async def test_update_trust_accumulates_directionally(seeded_rp):
    """A +3 Alice→Bob modification stacks on the 5 baseline → 8; Bob→Alice untouched."""
    sm = seeded_rp.container.state_manager
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    returned = await sm.update_trust(
        "Alice", "Bob", change=3, direction="increase", reason="Bob covered her tab",
        rp_folder=rp, branch=branch, bypass_session_cap=True,
    )

    # B2 lock: update_trust's RETURN value rides the (now-private) directional
    # relationship read. It must report the written a→b direction (Alice→Bob=8),
    # never silently flip to Bob→Alice (10) the way the old reverse-swap could.
    assert returned.character_a == "Alice" and returned.character_b == "Bob"
    assert returned.live_trust_score == 8, "update_trust must return the a→b direction it wrote"

    rels = await sm.get_all_relationships(rp, branch)
    ab = _by_pair(rels, "Alice", "Bob")
    ba = _by_pair(rels, "Bob", "Alice")
    assert ab.live_trust_score == 8, "Alice→Bob = baseline 5 + modification 3"
    assert ba.live_trust_score == 10, "Bob→Alice must be unaffected by the Alice→Bob change"


async def test_character_state_roundtrip(seeded_rp):
    """update_character then get_character returns the written location/emotion."""
    sm = seeded_rp.container.state_manager
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    await sm.update_character(
        "Alice",
        CharacterUpdate(location="The Salt Tavern", emotional_state="guarded"),
        rp, branch,
    )

    detail = await sm.get_character("Alice", rp, branch)
    assert detail is not None, "Alice's state was dropped after update"
    assert detail.location == "The Salt Tavern"
    assert detail.emotional_state == "guarded"


async def test_scene_roundtrip(seeded_rp):
    sm = seeded_rp.container.state_manager
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    await sm.update_scene(
        SceneUpdate(location="The Salt Tavern", mood="tense"), rp, branch
    )

    scene = await sm.get_scene(rp, branch)
    assert scene.location == "The Salt Tavern"
    assert scene.mood == "tense"


async def test_events_roundtrip(seeded_rp):
    sm = seeded_rp.container.state_manager
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    await sm.add_event(
        event="Alice slid a knife across the bar",
        characters=["Alice", "Bob"],
        significance="high",
        rp_folder=rp, branch=branch,
    )

    events = await sm.get_events(rp, branch)
    assert_nonempty(events, label="events after add_event")
    assert_present("Alice slid a knife across the bar", [e.event for e in events],
                   label="recorded events")


async def test_get_full_state_snapshot(seeded_rp):
    """The aggregate snapshot pulls characters, relationships, scene, and branch.

    ``characters`` is sourced from the character ledger (populated by an
    ``update_character``), not raw cards — so we create a ledger entry first,
    then assert it surfaces in the snapshot alongside the card-seeded relationships.
    """
    sm = seeded_rp.container.state_manager
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    await sm.update_character("Alice", CharacterUpdate(location="The Salt Tavern"), rp, branch)
    snap = await sm.get_full_state(rp, branch)

    assert snap.branch == branch
    assert_nonempty(snap.characters, label="snapshot characters")
    assert "Alice" in snap.characters, "an updated character must appear in the snapshot"
    assert_nonempty(snap.relationships, label="snapshot relationships")


async def test_relationship_graph_has_nodes_and_directional_edges(seeded_rp):
    """The graph exposes both characters as nodes and keeps edge directions apart."""
    sm = seeded_rp.container.state_manager
    graph = await sm.get_relationship_graph(seeded_rp.rp_folder, seeded_rp.main_branch)

    node_names = {n.name for n in graph.nodes}
    assert {"Alice", "Bob"} <= node_names, f"graph dropped a character node: {node_names}"
    assert_nonempty(graph.edges, label="relationship graph edges")

    scores = {
        (e.from_char, e.to_char): e.trust_score for e in graph.edges
    }
    assert scores.get(("Alice", "Bob")) == 5, "Alice→Bob edge must carry its own trust"
    assert scores.get(("Bob", "Alice")) == 10, "Bob→Alice edge must carry its own trust"

    # B1 lock: a node's trust_score is the abs-max across BOTH its relationship
    # rows (Alice→Bob=5, Bob→Alice=10), iterated relationships-then-card-edges.
    # This locks the _max_trust_for_character path that replaces the type("_Rel")
    # hack — a regression (taking the first only, summing, or 0) would fail here.
    nodes = {n.name: n for n in graph.nodes}
    assert nodes["Alice"].trust_score == 10, "Alice node max-trust spans both directional edges"
