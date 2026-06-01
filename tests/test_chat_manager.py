"""Service: ChatManager — OOC parity, regenerate, continue, attach_card_ids.

No prior test for ChatManager existed; this is the from-scratch coverage the Phase 5
tidy must preserve. Driven at the service level (built_container.chat_manager) — the
endpoint's require_chat_mode/resolve_active_session plumbing isn't what's under test.

xfails (corrected behavior, Phase 5):
- ``ChatResponse.ooc`` flag — added in Phase 5 (Bug C) so streaming and non-streaming
  OOC are detectable by the same flag. Today the non-streaming path has no such field
  (the streaming done-event already carries ``ooc=True``, which we LOCK).
- ``attach_card_ids`` injection — chat_manager queries ``WHERE card_id = ?`` with what
  is actually the DB ``id``, so attached cards never load (silent drop). Phase 5 aligns
  the query on ``WHERE id = ?``. Until then the attached card never reaches the prompt.
"""

from __future__ import annotations

import json

from tests import factories
from tests.assertions import assert_present

POV_MSG = "Let us proceed with the scene."


def _serialize(messages: list[dict]) -> str:
    return json.dumps(messages)


async def test_ooc_message_is_not_saved(built_container, seeded_rp):
    """An OOC turn returns a reply but persists NO exchange (number 0)."""
    cm = built_container.chat_manager
    before = await built_container.db.fetch_val(
        "SELECT COUNT(*) FROM exchanges WHERE rp_folder = ? AND branch = 'main'",
        [seeded_rp.rp_folder],
    )
    resp = await cm.chat(
        user_message="(meta) how long is this scene?",
        rp_folder=seeded_rp.rp_folder,
        branch="main",
        session_id="sess-1",
        message_mode="ooc",
    )
    assert resp.exchange_number == 0, "OOC reply was assigned a real exchange number"
    after = await built_container.db.fetch_val(
        "SELECT COUNT(*) FROM exchanges WHERE rp_folder = ? AND branch = 'main'",
        [seeded_rp.rp_folder],
    )
    assert after == before, f"OOC turn persisted an exchange ({before} -> {after})"


async def test_ooc_stream_done_event_flags_ooc(built_container, seeded_rp):
    """LOCK: the streaming OOC done-event carries ooc=True (the parity anchor)."""
    cm = built_container.chat_manager
    events = [
        chunk
        async for chunk in cm.chat_stream(
            user_message="(meta) pacing?",
            rp_folder=seeded_rp.rp_folder,
            branch="main",
            session_id="sess-1",
            message_mode="ooc",
        )
    ]
    done = [json.loads(e[len("data: "):]) for e in events if '"type": "done"' in e]
    assert done, f"no done event in OOC stream: {events}"
    assert done[-1].get("ooc") is True, f"OOC stream done-event missing ooc flag: {done[-1]}"


async def test_ooc_response_has_ooc_flag(built_container, seeded_rp):
    """Bug C (Phase 5): non-streaming OOC reply carries ooc=True, matching the
    streaming done-event so both paths are detectable by the same flag."""
    cm = built_container.chat_manager
    resp = await cm.chat(
        user_message="(meta) still OOC",
        rp_folder=seeded_rp.rp_folder,
        branch="main",
        session_id="sess-1",
        message_mode="ooc",
    )
    assert resp.ooc is True


async def test_regenerate_creates_variant(built_container, seeded_rp):
    cm = built_container.chat_manager
    built_container.fake_provider.queue("A freshly regenerated reply.")
    resp = await cm.regenerate(
        rp_folder=seeded_rp.rp_folder,
        branch="main",
        session_id="sess-1",
        exchange_number=seeded_rp.n_main_exchanges,
    )
    assert resp.response == "A freshly regenerated reply.", "regenerated text was dropped"
    # First regenerate lazily materializes variant 0 (the original) + the new one.
    assert resp.total_variants == 2, f"expected 2 variants after first regen, got {resp.total_variants}"


async def test_second_regenerate_and_variant_zero_metadata(built_container, seeded_rp):
    """Locks the _save_variant split (Phase 5's one non-verbatim edit): a second
    regenerate creates variant 2 (count>0, no lazy-create path), and variant 0 keeps
    the exchange's own created_at — the 'original as written' invariant (PoC-2)."""
    cm = built_container.chat_manager
    db = built_container.db
    exch = await db.fetch_one(
        """SELECT id, created_at FROM exchanges
           WHERE rp_folder = ? AND branch = 'main' AND exchange_number = ?""",
        [seeded_rp.rp_folder, seeded_rp.n_main_exchanges],
    )

    built_container.fake_provider.queue("First regen.")
    await cm.regenerate(
        rp_folder=seeded_rp.rp_folder, branch="main", session_id="sess-1",
        exchange_number=seeded_rp.n_main_exchanges,
    )
    built_container.fake_provider.queue("Second regen.")
    resp2 = await cm.regenerate(
        rp_folder=seeded_rp.rp_folder, branch="main", session_id="sess-1",
        exchange_number=seeded_rp.n_main_exchanges,
    )
    assert resp2.total_variants == 3, (
        f"second regen should yield variant 2 (total 3), got {resp2.total_variants}"
    )

    variants = await db.fetch_all(
        "SELECT model_used, temperature, created_at FROM exchange_variants "
        "WHERE exchange_id = ? ORDER BY id ASC",
        [exch["id"]],
    )
    assert len(variants) == 3, f"expected 3 variant rows, got {len(variants)}"
    v0 = variants[0]
    assert v0["created_at"] == exch["created_at"], (
        "variant 0 must carry the exchange's created_at (the original-as-written invariant)"
    )
    assert v0["model_used"] is None and v0["temperature"] is None, (
        "variant 0 (virtual original) must have NULL model/temperature"
    )


async def test_continue_appends(built_container, seeded_rp):
    cm = built_container.chat_manager
    built_container.fake_provider.queue(" She turns back toward the door.")
    resp = await cm.continue_response(
        rp_folder=seeded_rp.rp_folder,
        branch="main",
        session_id="sess-1",
        exchange_number=seeded_rp.n_main_exchanges,
    )
    assert resp.continuation.strip(), "continuation was empty (LLM output dropped)"
    assert_present(resp.continuation.strip(), resp.full_response, label="continued full_response")
    assert resp.continue_count >= 1, "continue_count was not incremented"


async def test_attach_card_ids_injects_into_prompt(built_container, seeded_rp):
    """Phase 5 attach_card_ids fix: attaching a card by its DB `id` injects it into
    the prompt. The old query used `WHERE card_id = ?` — but story_cards has no
    `card_id` column, so attach was silently broken. The fix aligns on `WHERE id = ?`.
    """
    db = built_container.db
    # A marker card the context pipeline won't surface on its own (no keyword/entity
    # match for POV_MSG, near-zero semantic similarity under the stub) — so it can only
    # appear in the prompt via the attach path. Insert with the real schema columns;
    # `id` is the TEXT primary key the frontend now sends as attach_card_ids.
    db_id = f"{seeded_rp.rp_folder}:markerlore"
    fut = await db.enqueue_write(
        """INSERT INTO story_cards
               (id, rp_folder, file_path, card_type, name, content, indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            db_id,
            seeded_rp.rp_folder,
            "TestRP/Story Cards/Lore/marker.md",
            "lore",
            "MarkerLore",
            "The secret passphrase is QZX_ATTACHMARKER_7731.",
            factories._now(),
        ],
    )
    await fut

    cm = built_container.chat_manager
    built_container.fake_provider.generate_calls.clear()
    await cm.chat(
        user_message=POV_MSG,
        rp_folder=seeded_rp.rp_folder,
        branch="main",
        session_id="sess-1",
        attach_card_ids=[db_id],
    )
    sent = _serialize(built_container.fake_provider.generate_calls[-1]["messages"])
    assert "QZX_ATTACHMARKER_7731" in sent, (
        "SILENT DROP: the attached card never reached the prompt"
    )
