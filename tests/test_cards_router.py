"""Endpoint: /api/cards/* — CRUD, suggest, audit, reindex, connections.

LOCKs the HTTP surface the Phase 6a file-split + 6b CardAuthoringService extraction
must preserve. The observable Bug-E (reindex aggregation) and Bug-B (audit branch
scoping) corrected behaviors are now asserted as hard passes (Phase 6a applied the
fixes; the former strict-xfails were converted).

Bug-C (``suggest_card`` fallback branch scoping) is now tested directly: Phase 6b
extracted ``CardAuthoringService._gather_suggest_evidence``, so the evidence the LLM
would see is reachable without the canned-LLM harness. See
``test_suggest_evidence_is_branch_scoped``. The new ``create_card`` →
``sync_reciprocal_relationships`` call-site is covered by
``test_create_card_syncs_reciprocal_relationship``.
"""

from __future__ import annotations

import json

from rp_engine.utils.normalization import normalize_key
from tests import factories
from tests.assertions import assert_present
from tests.conftest import RP_FOLDER

Q = {"rp_folder": RP_FOLDER}

# A minimal valid card the fake LLM can "author" for the suggest path.
_SUGGESTED_CARD_MD = (
    "---\ntype: character\nname: Carol\nimportance: medium\n---\n"
    "Carol is a quiet dockhand who keeps to the shadows.\n"
)


async def test_list_cards_returns_seeded_cards(client, seeded_rp):
    resp = await client.get("/api/cards", params=Q)
    assert resp.status_code == 200, resp.text
    names = [c["name"] for c in resp.json()["cards"]]
    for expected in ("Alice", "Bob", "The Salt Tavern", "The Old Pact"):
        assert_present(expected, names, label="card list")


async def test_card_crud_roundtrip(client, seeded_rp):
    # Create (lore avoids the character reciprocal-sync LLM path).
    create = await client.post(
        "/api/cards/lore",
        params={**Q, "sync_relationships": "false"},
        json={"name": "The Lighthouse", "frontmatter": {}, "content": "A lighthouse on the cape."},
    )
    assert create.status_code == 201, create.text
    assert create.json()["name"] == "The Lighthouse"

    got = await client.get("/api/cards/lore/The Lighthouse")
    assert got.status_code == 200, got.text
    assert_present("lighthouse on the cape", got.json()["content"], label="card content")

    deleted = await client.delete("/api/cards/lore/The Lighthouse")
    assert deleted.status_code == 200, deleted.text

    gone = await client.get("/api/cards/lore/The Lighthouse")
    assert gone.status_code == 404, "card still resolvable after delete"


async def test_get_connections(client, seeded_rp):
    resp = await client.get("/api/cards/connections", params=Q)
    assert resp.status_code == 200, resp.text
    names = {n["name"] for n in resp.json()["nodes"]}
    assert_present("Alice", names, label="connection nodes")
    assert_present("Bob", names, label="connection nodes")


async def test_suggest_card_returns_markdown(client, built_container, seeded_rp):
    built_container.fake_provider.queue(_SUGGESTED_CARD_MD)
    resp = await client.post(
        "/api/cards/suggest",
        json={"entity_name": "Carol", "card_type": "character", "rp_folder": RP_FOLDER},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_name"] == "Carol"
    assert body["markdown"].strip(), "suggest returned empty markdown (LLM output dropped)"


async def test_audit_cards_returns_shape(client, seeded_rp):
    resp = await client.post(
        "/api/cards/audit", json={"rp_folder": RP_FOLDER, "mode": "quick"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("mode", "gaps", "total_exchanges_scanned", "total_gaps"):
        assert key in body, f"audit response dropped {key!r}: {body.keys()}"


async def test_reindex_single_folder_returns_chunks(client, seeded_rp):
    """LOCK: the single-folder reindex path returns real entity + chunk counts."""
    resp = await client.post("/api/cards/reindex", params=Q)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entities"] > 0, f"reindex reported 0 entities: {body}"
    assert body["chunks"] > 0, f"reindex dropped chunk count: {body}"


async def test_reindex_all_folders_keeps_chunks(client, seeded_rp):
    """Bug E (Phase 6a): the multi-folder reindex path now aggregates `chunks`."""
    resp = await client.post("/api/cards/reindex")  # no rp_folder → multi-folder path
    assert resp.status_code == 200, resp.text
    assert resp.json()["chunks"] > 0, "multi-folder reindex dropped the chunk count"


async def test_reindex_returns_trust_baselines_seeded(client, seeded_rp):
    """Bug E (Phase 6a): ReindexResponse now carries trust_baselines_seeded."""
    resp = await client.post("/api/cards/reindex", params=Q)
    assert resp.status_code == 200, resp.text
    assert "trust_baselines_seeded" in resp.json(), (
        "reindex dropped the trust_baselines_seeded count"
    )


async def test_audit_scopes_to_branch(client, seeded_rp):
    """Bug B (Phase 6a): audit (no session_id) scopes its scan to `branch` (default main)."""
    # A proper noun with no card, mentioned only on the child branch B. Needs >=2
    # exchanges (the audit's mention threshold) so it would actually surface as a gap.
    for n, line in (
        (99, "Zephyrine slipped through the door without a sound."),
        (100, "Zephyrine watched the harbour from the rafters."),
    ):
        await factories.insert_exchange(
            seeded_rp.db,
            n,
            branch=seeded_rp.child_branch,
            user_message="Who is that?",
            assistant_response=line,
        )
    resp = await client.post(
        "/api/cards/audit", json={"rp_folder": RP_FOLDER, "mode": "quick"}
    )
    assert resp.status_code == 200, resp.text
    gap_names = {g["entity_name"] for g in resp.json()["gaps"]}
    assert "Zephyrine" not in gap_names, (
        "BRANCH LEAK: a child-branch-only entity surfaced in the default-branch audit"
    )


async def test_suggest_evidence_is_branch_scoped(seeded_rp):
    """Bug C (Phase 6a fix, locked in 6b): suggest evidence-gathering filters by branch.

    The fallback exchange search (the path with no card_gap_exchanges rows) must not
    leak a sibling branch's exchanges. The positive control on ``main`` proves the
    query actually matches the token (so the negative isn't vacuous); the negative on
    child branch ``B`` proves the branch filter scopes it — pre-fix, the unfiltered
    fallback returned the main-branch exchange on every branch.
    """
    svc = seeded_rp.container.card_authoring_service

    # An entity mentioned only in a `main` exchange, with NO card_gap_exchanges row,
    # forces the fallback path (where Bug C lived). seeded_rp runs no analysis, so no
    # gap rows exist for this token.
    await factories.insert_exchange(
        seeded_rp.db,
        200,
        branch=seeded_rp.main_branch,
        user_message="Who handles the ledgers?",
        assistant_response="Mordecai counts coins by candlelight in the back room.",
    )

    evidence_main = await svc._gather_suggest_evidence(
        "Mordecai", RP_FOLDER, seeded_rp.main_branch
    )
    assert "Mordecai" in evidence_main, (
        "positive control failed: fallback evidence missed the on-branch exchange "
        "(the negative assertion below would be vacuous)"
    )

    evidence_other = await svc._gather_suggest_evidence(
        "Mordecai", RP_FOLDER, seeded_rp.child_branch
    )
    assert "Mordecai" not in evidence_other, (
        "BRANCH LEAK: suggest evidence pulled a main-branch exchange while drafting "
        "on branch B (Bug C — fallback search not branch-scoped)"
    )
    assert evidence_other == "", "fallback evidence for a branch with no matches should be empty"


async def test_create_card_syncs_reciprocal_relationship(client, built_container, seeded_rp):
    """The new create_card → CardAuthoringService.sync_reciprocal_relationships call-site.

    Phase 6b moved the reciprocal sync into a service and rewired create_card to call it
    through DI — brand-new code. The existing CRUD roundtrip deliberately avoids this path
    (lore card + sync_relationships=false), so without this test the highest-complexity
    moved code AND its new caller are green-by-omission (exactly the 6a failure mode).

    Creating a character that references Bob must write a reciprocal entry back onto Bob.
    """
    # The LLM "generates" Bob's reciprocal entry; `target` gets overwritten with the
    # new card's real id by the service, so its value here is irrelevant.
    built_container.fake_provider.queue(
        '{"target": "char_carol", "role": "wary acquaintance", '
        '"trust": 4, "status": "keeps an eye on the newcomer", "doesnt_know": []}'
    )
    resp = await client.post(
        "/api/cards/character",
        params={**Q, "sync_relationships": "true"},
        json={
            "name": "Carol",
            "frontmatter": {
                "initial_relationships": [
                    {"target": "Bob", "role": "fellow dockhand", "trust": 6, "status": "new contact"}
                ]
            },
            "content": "Carol is a quiet dockhand who keeps to the shadows.",
        },
    )
    assert resp.status_code == 201, resp.text

    # The sync must have written an initial_relationships entry back onto Bob's card.
    bob = await seeded_rp.db.fetch_one(
        "SELECT frontmatter FROM story_cards WHERE LOWER(name) = 'bob' AND rp_folder = ?",
        [RP_FOLDER],
    )
    bob_fm = json.loads(bob["frontmatter"])
    targets = [
        normalize_key(r.get("target", ""))
        for r in bob_fm.get("initial_relationships", [])
        if isinstance(r, dict)
    ]
    assert any("carol" in t for t in targets), (
        f"reciprocal sync did not write a Carol relationship onto Bob: {targets}"
    )


async def test_generate_card_name_returns_suggestions(client, built_container, seeded_rp):
    """The thinned generate-name endpoint → CardAuthoringService.generate_card_name.

    The service swallows LLM/parse failures to ``[]`` (silent drop), so a fail-loud
    test is needed: the dedup assertion proves the existing-names path actually ran
    (not just that strings came back). Bob is a seeded character, so it must be
    filtered out of the suggestions.
    """
    built_container.fake_provider.queue('{"names": ["Mara Quayle", "Bob", "Silas Vane"]}')
    resp = await client.post(
        "/api/cards/generate-name",
        params=Q,
        json={"card_type": "character", "count": 3},
    )
    assert resp.status_code == 200, resp.text
    names = resp.json()["suggestions"]
    assert names, "generate-name returned empty suggestions (LLM output dropped)"
    assert "Bob" not in names, "generate-name did not dedup against the seeded 'Bob' card"
