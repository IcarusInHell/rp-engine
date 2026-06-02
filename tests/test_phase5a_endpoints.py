"""Phase 5a — narrator-note + prompt-order endpoint wiring (DI round-trips)."""

from __future__ import annotations

from tests.conftest import RP_FOLDER

Q = {"rp_folder": RP_FOLDER}


# ---------------------------------------------------------------------------
# Narrator note endpoints
# ---------------------------------------------------------------------------

async def test_narrator_note_set_and_clear(client, seeded_rp):
    put = await client.put(
        "/api/sessions/sess-1/narrator-note",
        json={"note": "Raise the stakes.", "depth": 3},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["narrator_note"] == "Raise the stakes."
    assert body["narrator_note_depth"] == 3

    cleared = await client.delete("/api/sessions/sess-1/narrator-note")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["narrator_note"] is None


async def test_narrator_note_depth_clamped_to_one(client, seeded_rp):
    """depth 0 would never be emitted — the endpoint clamps to >= 1."""
    put = await client.put(
        "/api/sessions/sess-1/narrator-note",
        json={"note": "x", "depth": 0},
    )
    assert put.status_code == 200, put.text
    assert put.json()["narrator_note_depth"] == 1


async def test_narrator_note_missing_session_404(client, seeded_rp):
    resp = await client.put(
        "/api/sessions/nope/narrator-note", json={"note": "x"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Prompt order endpoints
# ---------------------------------------------------------------------------

def _write_guidelines(seeded_rp) -> None:
    vault = seeded_rp.container.prompt_assembler.vault_root
    path = vault / RP_FOLDER / "RP State" / "Story_Guidelines.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ninclude_npc_framework: true\n---\nGuidelines body.\n", encoding="utf-8",
    )


async def test_prompt_order_get_default(client, seeded_rp):
    _write_guidelines(seeded_rp)
    resp = await client.get("/api/prompt/order", params=Q)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_custom"] is False
    assert body["order"] == body["default"]
    assert "writing_principles" in body["known_sections"]


async def test_prompt_order_put_and_reset(client, seeded_rp):
    _write_guidelines(seeded_rp)
    order = ["output_format", "writing_principles", "npc_briefs"]
    put = await client.put("/api/prompt/order", params=Q, json={"order": order})
    assert put.status_code == 200, put.text
    assert put.json()["is_custom"] is True
    assert put.json()["order"] == order

    # Persisted — a fresh GET reflects it.
    got = await client.get("/api/prompt/order", params=Q)
    assert got.json()["order"] == order

    reset = await client.post("/api/prompt/order/reset", params=Q)
    assert reset.status_code == 200, reset.text
    assert reset.json()["is_custom"] is False


async def test_prompt_order_put_unknown_rejected(client, seeded_rp):
    _write_guidelines(seeded_rp)
    resp = await client.put(
        "/api/prompt/order", params=Q,
        json={"order": ["writing_principles", "totally_bogus"]},
    )
    assert resp.status_code == 422, resp.text
    assert "totally_bogus" in resp.text
