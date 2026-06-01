"""Endpoint: /api/npc/* — react, react-batch, list, trust.

LOCK the reaction endpoints' parse/return contract (the Phase 1 get_reaction
extraction must preserve it). These null the always-constructed DiagnosticLogger
first — the same isolation the Phase 0b service tests use — because the
diagnostic-payload bug (folded into Phase 1) would otherwise crash the single
react path / silently empty the batch path. That bug has its own xfail in
test_npc_engine.py; here we lock the routing + shaping around it.

xfail the ``/trust`` directional read until Phase 2: ``get_trust`` resolves via
``resolve_trust_for_pair``, which OR-merges both directions. So NPC→target and
target→NPC currently read back equal even though the vault seeds them differently
(Alice→Bob=5, Bob→Alice=10). Flips to XPASS when the directional read fix lands.
"""

from __future__ import annotations

import json

from tests.assertions import assert_present
from tests.conftest import RP_FOLDER

Q = {"rp_folder": RP_FOLDER, "branch": "main"}


def _reaction_json(character: str, dialogue: str) -> str:
    return json.dumps(
        {
            "character": character,
            "internalMonologue": "She weighs the stranger's intent.",
            "physicalAction": "Sets down the glass.",
            "dialogue": dialogue,
            "emotionalUndercurrent": "wary",
            "trustShift": {"direction": "neutral", "amount": 0, "reason": None},
        }
    )


async def test_react_returns_parsed_reaction(client, built_container, seeded_rp):
    # Diagnostics stay attached: the Phase 1 payload fix means the diagnostics-on
    # path must not crash/drop the reaction at the endpoint either.
    built_container.fake_provider.queue(_reaction_json("Alice", "What'll it be?"))

    resp = await client.post(
        "/api/npc/react",
        params=Q,
        json={"npc_name": "Alice", "scene_prompt": "A stranger enters the tavern."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["character"] == "Alice"
    assert body["dialogue"] == "What'll it be?", "the LLM dialogue was dropped"


async def test_react_batch_one_per_npc(client, built_container, seeded_rp):
    # Diagnostics stay attached — the payload bug previously emptied the batch to [].
    built_container.fake_provider.queue(
        _reaction_json("Alice", "Mind the step."),
        _reaction_json("Bob", "Evening."),
    )

    resp = await client.post(
        "/api/npc/react-batch",
        params=Q,
        json={"npc_names": ["Alice", "Bob"], "scene_prompt": "Two patrons size up."},
    )
    assert resp.status_code == 200, resp.text
    reactions = resp.json()
    assert len(reactions) == 2, f"batch dropped a reaction: {reactions}"
    assert {r["character"] for r in reactions} == {"Alice", "Bob"}


async def test_list_npcs(client, seeded_rp):
    resp = await client.get("/api/npcs", params=Q)
    assert resp.status_code == 200, resp.text
    names = [n["name"] for n in resp.json()]
    assert_present("Alice", names, label="npc list")
    assert_present("Bob", names, label="npc list")


async def test_npc_trust_is_directional(client, seeded_rp):
    # Phase 2 (landed): /api/npc/{name}/trust resolves via resolve_trust_for_pair,
    # whose modification SUM now reads the requested direction ONLY (utils/trust.py).
    # A one-directional modification (+20 on Alice->Bob) must NOT bleed into the
    # reverse read: Bob->Alice stays at its baseline 10.
    # Vault baselines: Alice->Bob=5, Bob->Alice=10. Add a modification on ONE
    # direction only (Alice->Bob += 20). It must NOT bleed into the reverse read.
    put = await client.put(
        "/api/state/relationships/Alice/Bob",
        params=Q,
        json={"trust_change": 20, "reason": "Alice's trust in Bob grows", "direction": "up"},
    )
    assert put.status_code == 200, put.text

    b_to_a = await client.get("/api/npc/Bob/trust", params={**Q, "target_name": "Alice"})
    assert b_to_a.status_code == 200, b_to_a.text
    assert b_to_a.json()["trust_score"] == 10, (
        "DIRECTION FLATTENED: Alice->Bob's +20 modification polluted Bob->Alice "
        f"(got {b_to_a.json()['trust_score']}, expected the unmodified baseline 10)."
    )
