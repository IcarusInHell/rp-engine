"""Endpoint: /api/state/* — snapshot, graph, characters, relationships, scene, events.

These LOCK current behavior the Phase 3a/3b StateManager extraction + facade split
must preserve (the public HTTP surface is unchanged across that work).

The directional-relationships assertion is locked NOW (not xfailed): the
``/relationships`` endpoint rides ``get_all_relationships``, which Phase 0b already
proved directional-correct. Contrast the NPC ``/trust`` endpoint, which rides the
OR-merging ``resolve_trust_for_pair`` and is therefore xfailed until Phase 2 (see
test_npc_router.py).
"""

from __future__ import annotations

from tests.assertions import assert_directional, assert_present
from tests.conftest import RP_FOLDER

Q = {"rp_folder": RP_FOLDER, "branch": "main"}


async def test_get_full_state_snapshot(client, seeded_rp):
    # Characters live in the runtime ledger (separate from cards) — populate one so
    # the snapshot has something real to carry, then assert it survives into it.
    upd = await client.put(
        "/api/state/characters/Alice", params=Q, json={"location": "The Salt Tavern"}
    )
    assert upd.status_code == 200, upd.text

    resp = await client.get("/api/state", params=Q)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("characters", "scene", "events"):
        assert key in body, f"state snapshot dropped section {key!r}: {body.keys()}"
    assert_present("Alice", body["characters"], label="snapshot characters")


async def test_relationship_graph_has_character_nodes(client, seeded_rp):
    resp = await client.get("/api/state/relationship-graph", params=Q)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {n.get("name") for n in body.get("nodes", [])}
    assert_present("Alice", names, label="relationship-graph nodes")
    assert_present("Bob", names, label="relationship-graph nodes")


async def test_list_characters(client, seeded_rp):
    # Runtime-ledger characters: empty until written. Create two, then list.
    for name in ("Alice", "Bob"):
        upd = await client.put(
            f"/api/state/characters/{name}", params=Q, json={"emotional_state": "wary"}
        )
        assert upd.status_code == 200, upd.text

    resp = await client.get("/api/state/characters", params=Q)
    assert resp.status_code == 200, resp.text
    chars = resp.json()["characters"]
    assert_present("Alice", chars, label="character list")
    assert_present("Bob", chars, label="character list")


async def test_relationships_are_directional(client, seeded_rp):
    """A→B and B→A trust must read back as distinct entries (not OR-merged).

    Seed the two directions with different magnitudes via the PUT endpoint, then
    read the list back. If they collapse to one value, a direction was flattened.
    This rides ``get_all_relationships`` (directional-correct), so it's a LOCK.
    """
    r1 = await client.put(
        "/api/state/relationships/Alice/Bob",
        params=Q,
        json={"trust_change": 5, "reason": "Alice warms to Bob", "direction": "up"},
    )
    assert r1.status_code == 200, r1.text
    r2 = await client.put(
        "/api/state/relationships/Bob/Alice",
        params=Q,
        json={"trust_change": 11, "reason": "Bob trusts Alice", "direction": "up"},
    )
    assert r2.status_code == 200, r2.text

    resp = await client.get("/api/state/relationships", params=Q)
    assert resp.status_code == 200, resp.text
    rels = resp.json()["relationships"]

    def live(a: str, b: str) -> int:
        for r in rels:
            if r["character_a"].lower() == a.lower() and r["character_b"].lower() == b.lower():
                return r["live_trust_score"]
        raise AssertionError(f"SILENT DROP: no {a}->{b} relationship in {rels}")

    assert_directional(live("Alice", "Bob"), live("Bob", "Alice"), label="live trust")


async def test_get_and_update_scene(client, seeded_rp):
    resp = await client.get("/api/state/scene", params=Q)
    assert resp.status_code == 200, resp.text

    put = await client.put(
        "/api/state/scene", params=Q, json={"location": "The Salt Tavern", "mood": "tense"}
    )
    assert put.status_code == 200, put.text
    assert put.json()["location"] == "The Salt Tavern"


async def test_create_and_list_events(client, seeded_rp):
    create = await client.post(
        "/api/state/events",
        params=Q,
        json={"event": "Alice and Bob strike a bargain", "characters": ["Alice", "Bob"]},
    )
    assert create.status_code == 201, create.text

    listed = await client.get("/api/state/events", params=Q)
    assert listed.status_code == 200, listed.text
    events = [e["event"] for e in listed.json()["events"]]
    assert_present("Alice and Bob strike a bargain", events, label="events")
