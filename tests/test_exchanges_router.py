"""Endpoint: /api/exchanges/* + /api/bookmarks + /api/annotations.

LOCKs the HTTP surface the Phase 7a flat-split + 7b RewindService/ExchangeSearchService
extraction must preserve. xfails the rewind Bug-A (rewound_count) and Bug-B (new_branch)
corrected behaviors until Phase 7a.

Search: keyword mode is SQLite LIKE (deterministic), so it's asserted to actually find
a hit. Semantic/hybrid ride hash-embed similarity (near-zero, non-deterministic under
the stub — see Phase 0b notes), so those are locked on shape/mode only, not ranking.
"""

from __future__ import annotations

import pytest

from tests.assertions import assert_nonempty, assert_present
from tests.conftest import RP_FOLDER

Q = {"rp_folder": RP_FOLDER, "branch": "main"}
SESSION = "sess-1"


async def test_save_exchange(client, seeded_rp):
    resp = await client.post(
        "/api/exchanges",
        json={
            "session_id": SESSION,
            "user_message": "The door creaks open.",
            "assistant_response": "Alice looks up sharply.",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["exchange_number"] == seeded_rp.n_main_exchanges + 1, (
        f"expected next exchange number, got {body['exchange_number']}"
    )


async def test_edit_exchange(client, seeded_rp):
    resp = await client.put(
        "/api/exchanges/1",
        params=Q,
        json={"assistant_response": "Alice studies the newcomer with open suspicion."},
    )
    assert resp.status_code == 200, resp.text
    assert_present("open suspicion", resp.json()["assistant_response"], label="edited response")


async def test_list_exchanges(client, seeded_rp):
    resp = await client.get(
        "/api/exchanges", params={**Q, "session_id": SESSION}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_count"] == seeded_rp.n_main_exchanges, (
        f"listed {body['total_count']} exchanges, expected {seeded_rp.n_main_exchanges}"
    )


async def test_search_keyword_finds_hit(client, seeded_rp):
    """Keyword search is deterministic — it MUST find the seeded 'tavern' line."""
    resp = await client.get(
        "/api/exchanges/search", params={**Q, "q": "tavern", "mode": "keyword"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "keyword"
    assert_nonempty(body["results"], label="keyword search results")


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
async def test_search_other_modes_return_shape(client, seeded_rp, mode):
    """Semantic/hybrid: lock the response shape + echoed mode (ranking is non-det)."""
    resp = await client.get(
        "/api/exchanges/search", params={**Q, "q": "tavern", "mode": mode}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == mode
    assert "results" in body


async def test_bookmark_crud(client, seeded_rp):
    create = await client.post(
        "/api/exchanges/1/bookmark", params=Q, json={"name": "First meeting", "color": "gold"}
    )
    assert create.status_code == 201, create.text

    listed = await client.get("/api/bookmarks", params=Q)
    assert listed.status_code == 200, listed.text
    names = [b["name"] for b in listed.json()["bookmarks"]]
    assert_present("First meeting", names, label="bookmark list")

    deleted = await client.delete("/api/exchanges/1/bookmark", params=Q)
    assert deleted.status_code == 200, deleted.text


async def test_annotation_crud(client, seeded_rp):
    create = await client.post(
        "/api/exchanges/1/annotations",
        params=Q,
        json={"content": "Foreshadows the betrayal", "annotation_type": "note"},
    )
    assert create.status_code == 201, create.text
    annotation_id = create.json()["id"]

    listed = await client.get("/api/exchanges/1/annotations", params=Q)
    assert listed.status_code == 200, listed.text
    contents = [a["content"] for a in listed.json()["annotations"]]
    assert_present("Foreshadows the betrayal", contents, label="annotation list")

    deleted = await client.delete(f"/api/annotations/{annotation_id}")
    assert deleted.status_code == 200, deleted.text


# --- Rewind: Bug-A (rewound_count) + Bug-B (new_branch), fixed in Phase 7a ----

async def test_rewind_reports_count(client, seeded_rp):
    """Bug A: rewound_count must reflect the OLD branch's orphaned exchanges.

    Before the fix it queried the freshly-created branch (snapshotted at
    rewind_point-1, zero matching rows) and always reported 0.
    """
    resp = await client.post(
        "/api/exchanges",
        json={
            "session_id": SESSION,
            "exchange_number": 2,  # conflicts with an existing exchange → rewind
            "user_message": "Actually, she draws her blade.",
            "assistant_response": "Steel rings out.",
        },
    )
    assert resp.status_code == 201, resp.text
    # Old 'main' had exchanges 2 and 3 past the rewind point → 2 orphaned.
    assert resp.json()["rewound_count"] == 2, (
        f"rewound_count should reflect the OLD branch's orphans, got {resp.json()['rewound_count']}"
    )


async def test_rewind_returns_new_branch(client, seeded_rp):
    """Bug B: a rewind moves the caller to a new branch — report its name."""
    resp = await client.post(
        "/api/exchanges",
        json={
            "session_id": SESSION,
            "exchange_number": 2,
            "user_message": "Actually, she draws her blade.",
            "assistant_response": "Steel rings out.",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json().get("new_branch"), "rewind did not return the new branch name"


async def test_rewind_save_lands_on_new_branch(client, seeded_rp):
    """Phase 7b: the rewind save must be written to the NEW branch.

    The Bug-A/Bug-B tests above only assert ``rewound_count`` and that
    ``new_branch`` is truthy — neither checks the saved exchange actually landed
    on the new branch. That ``branch = result.new_branch`` re-wire is the
    highest-risk line in the RewindService extraction (drop it and the save
    collides back on the old branch); this locks it. Mutation-proven: removing
    that assignment in ``save_exchange`` turns this red.
    """
    resp = await client.post(
        "/api/exchanges",
        json={
            "session_id": SESSION,
            "exchange_number": 2,  # conflicts → rewind
            "user_message": "REWIND-MARKER she draws her blade.",
            "assistant_response": "Steel rings out.",
        },
    )
    assert resp.status_code == 201, resp.text
    new_branch = resp.json()["new_branch"]
    assert new_branch, "rewind did not return a new branch"

    # Scope strictly to the new branch (no ancestry walk) → only its OWN rows.
    listed = await client.get(
        "/api/exchanges",
        params={"rp_folder": RP_FOLDER, "branch": new_branch, "include_ancestry": "false"},
    )
    assert listed.status_code == 200, listed.text
    marked = [r for r in listed.json()["exchanges"] if "REWIND-MARKER" in r["user_message"]]
    assert_nonempty(marked, label=f"rewind save on branch {new_branch!r}")
    assert all(r["branch"] == new_branch for r in marked), (
        "rewind exchange did not land on the new branch: "
        f"{[(r['exchange_number'], r['branch']) for r in marked]}"
    )


# --- Bug C: insert row id captured from the write future, not a racy re-query --

async def test_create_annotation_returns_its_own_row_not_latest(client, seeded_rp, monkeypatch):
    """Bug C: the created annotation must be fetched by ITS inserted id.

    The old handler re-queried ``ORDER BY id DESC LIMIT 1`` scoped to the
    exchange, so a higher-id annotation landing on the same exchange between the
    insert and the fetch (which is exactly what a concurrent second insert does)
    would be returned to the wrong caller. We reproduce that interleaving
    deterministically: patch ``fetch_one`` to sneak a higher-id 'DECOY'
    annotation onto the same exchange right before the handler's post-insert
    SELECT. The fix (``WHERE id = <own>``) ignores it; the bug returns it.
    """
    db = seeded_rp.container.db
    real_fetch_one = db.fetch_one
    state = {"tripped": False}

    async def fetch_one_with_decoy(sql, params=None):
        if not state["tripped"] and "exchange_annotations" in sql:
            state["tripped"] = True
            # Insert a higher-id decoy on the same exchange BEFORE the real fetch.
            from rp_engine.database import PRIORITY_EXCHANGE
            future = await db.enqueue_write(
                """INSERT INTO exchange_annotations
                   (rp_folder, branch, exchange_id, exchange_number, content,
                    annotation_type, include_in_context, created_at)
                   SELECT rp_folder, branch, id, exchange_number, 'DECOY', 'note', 0, ?
                   FROM exchanges WHERE rp_folder = ? AND branch = ? AND exchange_number = ?""",
                ["2099-01-01T00:00:00", RP_FOLDER, "main", 1],
                priority=PRIORITY_EXCHANGE,
            )
            await future
        return await real_fetch_one(sql, params)

    monkeypatch.setattr(db, "fetch_one", fetch_one_with_decoy)

    resp = await client.post(
        "/api/exchanges/1/annotations",
        params=Q,
        json={"content": "REAL-ANNOTATION", "annotation_type": "note"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["content"] == "REAL-ANNOTATION", (
        "create_annotation returned a different row than the one it inserted "
        f"(got {resp.json()['content']!r} — the racy DESC re-query picked the decoy)"
    )


# --- Bug D: auto-named bookmarks use MAX+1, not COUNT (no collision on delete) -

async def test_bookmark_auto_name_avoids_collision_after_delete(client, seeded_rp):
    """Bug D: deleting a bookmark must not let the next auto-name collide.

    With COUNT-based naming, deleting one bookmark drops the count so the next
    auto-name reuses a number still held by an existing bookmark. MAX+1 over the
    existing 'Bookmark #N' suffixes avoids that.
    """
    # Auto-name bookmarks on exchanges 1, 2, 3 → "Bookmark #1/#2/#3".
    for ex in (1, 2, 3):
        r = await client.post(f"/api/exchanges/{ex}/bookmark", params=Q, json={})
        assert r.status_code == 201, r.text
    names_before = {
        b["name"] for b in (await client.get("/api/bookmarks", params=Q)).json()["bookmarks"]
    }
    assert names_before == {"Bookmark #1", "Bookmark #2", "Bookmark #3"}, names_before

    # Delete #1's exchange-1 bookmark, then re-create an auto-named one there.
    assert (await client.delete("/api/exchanges/1/bookmark", params=Q)).status_code == 200
    recreated = await client.post("/api/exchanges/1/bookmark", params=Q, json={})
    assert recreated.status_code == 201, recreated.text
    new_name = recreated.json()["name"]

    # MAX(#2,#3)+1 = #4. COUNT-based would have produced "#3" — a collision with
    # the surviving exchange-3 bookmark.
    assert new_name == "Bookmark #4", f"expected MAX+1 auto-name, got {new_name!r}"
    assert new_name not in {"Bookmark #2", "Bookmark #3"}, (
        f"auto-name {new_name!r} collides with a surviving bookmark"
    )
