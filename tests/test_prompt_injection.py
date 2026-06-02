"""Phase 4 — injection depth, token budget, example dialogue.

The load-bearing guard here is the **byte-identity lock** (``test_disabled_path_*``):
the injection-depth refactor splits ``build_system_prompt``'s dynamic context into
per-section builders, and the #1 risk is silent whitespace/order drift in the path
that's supposed to match the pre-Phase-4 system exactly. The golden snapshot in
``golden/phase4_disabled_system_prompt.txt`` was captured from the pre-refactor
code; any drift in section order, the ``\\n\\n---\\n`` boundary, or the ``\\n``.join
reddens it.

Injection depth and token budget both default **off** (parallel to each other);
the enabled-path tests assert placement/behavior and are mutation-proven against
the "still in system message" / "blind window" regressions.
"""

from __future__ import annotations

from pathlib import Path

import json

from rp_engine.config import (
    ExampleDialogueConfig,
    InjectionConfig,
    PromptConfig,
    TokenBudgetConfig,
)
from rp_engine.models.context import (
    CardGap,
    CharacterState,
    ContextDocument,
    ContextResponse,
    CustomStateBlock,
    LorebookEntryHit,
    NPCBrief,
    PastExchangeHit,
    ResolvedKnowledge,
    SceneState,
    StalenessWarning,
    ThreadAlert,
    TriggeredNote,
    WritingConstraints,
)
from rp_engine.services.prompt_assembler import PromptAssembler
from tests.conftest import RP_FOLDER

GOLDEN = Path(__file__).parent / "golden" / "phase4_disabled_system_prompt.txt"


def _rich_context() -> ContextResponse:
    """A ContextResponse touching every dynamic section the assembler renders.

    Shared by the byte-identity lock and the injection-depth placement tests so
    both exercise the same section set.
    """
    return ContextResponse(
        current_exchange=12,
        scene_state=SceneState(location="The Ledger Room", time_of_day="night", mood="tense"),
        character_states={
            "Lilith": CharacterState(location="doorway", emotional_state="wary", conditions=["cold"]),
        },
        custom_state=[
            CustomStateBlock(schema_name="Stats", category="c", display_format="stat_block", content="HP: 10", belongs_to="Lilith"),
            CustomStateBlock(schema_name="Weather", category="c", display_format="note", content="storm rising", belongs_to=None),
        ],
        npc_briefs=[
            NPCBrief(character="Dante", card_id="rp:dante", archetype="POWER_HOLDER", trust_score=5, trust_stage="neutral", emotional_state="guarded", behavioral_direction="Test the player"),
        ],
        knowledge_boundaries={
            "Dante": [ResolvedKnowledge(card_id="kb1", topic="ledger", believes=["the ledger is clean"], reality=["it is forged"], confidence="high", source="direct", knows_reality=True)],
        },
        thread_alerts=[ThreadAlert(thread_id="t1", name="Debt", level="moderate", counter=3, threshold=5, consequence="Dante calls in the debt")],
        documents=[
            ContextDocument(name="The Ledger", card_type="lore", file_path="x.md", source="keyword", relevance_score=1.5, content="A heavy ledger bound in leather.", status="new", injection_tier="full"),
            ContextDocument(name="Side Alley", card_type="location", file_path="y.md", source="semantic", relevance_score=0.7, summary="A narrow alley.", status="new", injection_tier="brief"),
            ContextDocument(name="Old Coin", card_type="item", file_path="z.md", source="graph", relevance_score=0.3, summary="A worn coin.", status="new", injection_tier="reference"),
        ],
        triggered_notes=[TriggeredNote(trigger_id="tr1", trigger_name="Storm", inject_type="context_note", content="The storm intensifies.")],
        past_exchanges=[PastExchangeHit(exchange_number=4, speaker="Lilith", text="She drew the knife slowly.", score=0.8)],
        card_gaps=[CardGap(entity_name="Barkeep", seen_count=2)],
        warnings=[StalenessWarning(exchange=9, failed_at="trust")],
        writing_constraints=WritingConstraints(text="t", patterns_included=["tension"], task_context="combat"),
    )


def _assembler(prompt_config: PromptConfig | None = None) -> PromptAssembler:
    """A DB-free assembler. ``vault_root`` points nowhere so ``get_sections``
    falls back to the full default static skeleton (deterministic — independent
    of any sample-vault Story_Guidelines.md)."""
    return PromptAssembler(
        vault_root=Path("/nonexistent_vault"),
        db=None,  # type: ignore[arg-type]
        guidelines_service=None,  # type: ignore[arg-type]
        prompt_config=prompt_config,
    )


# ---------------------------------------------------------------------------
# Byte-identity lock — the refactor must not drift the disabled/legacy output
# ---------------------------------------------------------------------------


def test_disabled_path_matches_golden_snapshot():
    """``build_system_prompt`` output must be byte-identical to the pre-refactor
    snapshot. Mutation: reorder/space any dynamic section → red diff."""
    out = _assembler().build_system_prompt("any_rp", _rich_context())
    expected = GOLDEN.read_text()
    assert out == expected, (
        "build_system_prompt output drifted from the locked Phase-4 snapshot. "
        "If this change is intentional, regenerate golden/phase4_disabled_system_prompt.txt."
    )


def test_exclude_sections_removes_only_named_blocks():
    """The injection path's mechanism: excluded sections leave the system prompt,
    everything else stays. Mutation: ignore exclude_sections → scene reappears."""
    cr = _rich_context()
    full = _assembler().build_system_prompt("any_rp", cr)
    trimmed = _assembler().build_system_prompt(
        "any_rp", cr, exclude_sections={"scene_context", "triggered_notes"},
    )
    assert "# Current Scene" in full and "# Triggered Notes" in full
    assert "# Current Scene" not in trimmed, "excluded scene_context still in system prompt"
    assert "# Triggered Notes" not in trimmed, "excluded triggered_notes still in system prompt"
    # Non-excluded sections are untouched.
    assert "# Active NPCs" in trimmed and "# Plot Thread Alerts" in trimmed


# ---------------------------------------------------------------------------
# Helpers for assembler-level (DB-backed) tests
# ---------------------------------------------------------------------------


def _assembler_with(container, prompt_config: PromptConfig) -> PromptAssembler:
    """A PromptAssembler over the container's real DB/guidelines/ancestry but with
    a custom PromptConfig (so injection/token-budget/example-dialogue are toggled
    per test without mutating global config)."""
    base = container.prompt_assembler
    return PromptAssembler(
        vault_root=base.vault_root,
        db=container.db,
        guidelines_service=base.guidelines_service,
        ancestry_resolver=base.ancestry_resolver,
        prompt_config=prompt_config,
    )


async def _insert_card(db, *, card_id: str, name: str, content: str, frontmatter: dict) -> None:
    fut = await db.enqueue_write(
        """INSERT OR REPLACE INTO story_cards
           (id, rp_folder, file_path, card_type, name, importance, summary,
            frontmatter, content, content_hash, file_mtime, always_load, indexed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        [
            card_id, RP_FOLDER, f"{name}.md", "character", name, "main", "",
            json.dumps(frontmatter), content, "hash", 0, 0,
        ],
    )
    await fut


def _positions(messages: list[dict], needle: str) -> list[int]:
    return [i for i, m in enumerate(messages) if needle in (m.get("content") or "")]


# ---------------------------------------------------------------------------
# Feature B — token budget
# ---------------------------------------------------------------------------


async def test_budget_fetch_stops_at_budget(seeded_rp):
    """Token-aware fill returns a subset under a tiny budget, all under a large one."""
    pa = seeded_rp.container.prompt_assembler
    tiny = await pa.get_recent_exchanges_by_budget(
        seeded_rp.rp_folder, "main", token_budget=5, session_id=None,
    )
    large = await pa.get_recent_exchanges_by_budget(
        seeded_rp.rp_folder, "main", token_budget=100_000, session_id=None,
    )
    assert len(large) == seeded_rp.n_main_exchanges, "large budget must fetch all history"
    assert 1 <= len(tiny) < len(large), "tiny budget must stop early but keep >= 1 exchange"


async def test_budget_fetch_preserves_ancestry(seeded_rp):
    """The budget path must walk branch ancestry like the count path — a child
    branch sees its parent's exchanges. Mutation guard: a naive flat WHERE
    (branch = 'B') would return 0 here (B owns no exchanges of its own)."""
    pa = seeded_rp.container.prompt_assembler
    rows = await pa.get_recent_exchanges_by_budget(
        seeded_rp.rp_folder, seeded_rp.child_branch, token_budget=100_000, session_id=None,
    )
    assert len(rows) == seeded_rp.n_main_exchanges, (
        "child branch lost parent history under token budget — ancestry dropped"
    )


async def test_build_messages_routes_to_budget_when_enabled(seeded_rp):
    """The token_budget.enabled flag routes history through the budget fetch.
    Mutation guard: if build_messages ignored the flag it would always return the
    full count window, so the tiny-budget run would match the disabled run."""
    container = seeded_rp.container

    disabled = await container.prompt_assembler.build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=None, session_id=None,
    )
    # Tiny per-exchange budget: window barely above max_tokens+margin.
    cfg = PromptConfig(token_budget=TokenBudgetConfig(enabled=True, model_context_window=4520))
    enabled = await _assembler_with(container, cfg).build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=None, session_id=None,
    )
    n_disabled = sum(1 for m in disabled if m["role"] == "assistant")
    n_enabled = sum(1 for m in enabled if m["role"] == "assistant")
    assert n_disabled == seeded_rp.n_main_exchanges
    assert n_enabled < n_disabled, "token budget did not trim history when enabled"


async def test_static_overflow_emits_diagnostic(seeded_rp, caplog):
    """When the static prompt alone blows its system+context budget, a warning
    fires (the overflow isn't a silent history squeeze). Exercises the
    estimate_messages_tokens helper. Mutation: drop the warn call → no log."""
    import logging
    container = seeded_rp.container
    # model_context_window barely above max_tokens+margin → static_budget ≈ 0,
    # which the full default static skeleton easily exceeds.
    cfg = PromptConfig(token_budget=TokenBudgetConfig(enabled=True, model_context_window=4510))
    with caplog.at_level(logging.WARNING):
        await _assembler_with(container, cfg).build_messages(
            seeded_rp.rp_folder, "main", "now", context_response=None, session_id=None,
        )
    assert any("exceed their token budget" in r.message for r in caplog.records), (
        "expected a static-overflow diagnostic when the system prompt exceeds budget"
    )


# ---------------------------------------------------------------------------
# Feature A — injection depth placement
# ---------------------------------------------------------------------------


def _inject_ctx() -> ContextResponse:
    return ContextResponse(
        current_exchange=4,
        scene_state=SceneState(location="The Vault", mood="tense"),
        npc_briefs=[NPCBrief(character="Dante", trust_stage="neutral")],
        triggered_notes=[TriggeredNote(trigger_id="t", trigger_name="Storm", inject_type="context_note", content="The storm rises.")],
    )


async def test_injection_disabled_keeps_sections_in_system_message(seeded_rp):
    """Baseline / mutation anchor: with injection off, dynamic sections live in the
    top system message and there are NO extra in-history system injections."""
    container = seeded_rp.container
    cfg = PromptConfig(
        injection=InjectionConfig(enabled=False),
        example_dialogue=ExampleDialogueConfig(enabled=False),
    )
    messages = await _assembler_with(container, cfg).build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=_inject_ctx(), session_id=None,
    )
    assert "# Current Scene" in messages[0]["content"], "scene must stay in system msg when off"
    assert "# Triggered Notes" in messages[0]["content"]
    # No in-history system message carries the dynamic sections.
    later_system = [m for m in messages[1:] if m["role"] == "system"]
    assert not any("# Current Scene" in (m.get("content") or "") for m in later_system)


async def test_injection_enabled_moves_sections_to_depth(seeded_rp):
    """With injection on, depth-mapped sections leave the system message and appear
    as in-history system injections; depth ordering is honored (scene depth 4 sits
    above triggered-notes depth 2). Mutation: the enabled gate off → this reddens
    (sections would still be in messages[0], paired with the disabled test)."""
    container = seeded_rp.container
    cfg = PromptConfig(
        injection=InjectionConfig(enabled=True),
        example_dialogue=ExampleDialogueConfig(enabled=False),
    )
    messages = await _assembler_with(container, cfg).build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=_inject_ctx(), session_id=None,
    )
    sys_msg = messages[0]["content"]
    assert "# Current Scene" not in sys_msg, "scene_context not moved out of system msg"
    assert "# Active NPCs" not in sys_msg, "npc_briefs not moved out of system msg"
    assert "# Triggered Notes" not in sys_msg, "triggered_notes not moved out of system msg"

    scene_pos = _positions(messages, "# Current Scene")
    trig_pos = _positions(messages, "# Triggered Notes")
    assert scene_pos and trig_pos, "depth-injected sections missing from history"
    # depth 4 (scene) injects further up than depth 2 (triggered notes).
    assert scene_pos[0] < trig_pos[0], "depth ordering wrong: scene(4) should precede triggered(2)"
    # Both are in-history system messages (after the top system message).
    assert scene_pos[0] > 0 and trig_pos[0] > 0


async def test_world_info_honors_per_entry_depth(seeded_rp):
    """With injection on, lorebook entries distribute by PER-ENTRY depth (ST
    position/depth), falling back to the section ``world_info`` depth (2). An
    entry at depth 4 injects further up than one at the default depth 2.

    Mutation guard: if build_messages routed World Info as one flat block at the
    section depth (ignoring hit.depth), both entries would share a position and
    the strict ordering below would fail."""
    container = seeded_rp.container
    cfg = PromptConfig(
        injection=InjectionConfig(enabled=True),
        example_dialogue=ExampleDialogueConfig(enabled=False),
    )
    cr = ContextResponse(
        current_exchange=4,
        lorebook_entries=[
            LorebookEntryHit(entry_id=1, name="deep", content="WI_DEEP_ENTRY", scope="rp", depth=4),
            LorebookEntryHit(entry_id=2, name="shallow", content="WI_SHALLOW_ENTRY", scope="rp"),  # no depth → section 2
        ],
    )
    messages = await _assembler_with(container, cfg).build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=cr, session_id=None,
    )
    assert "WI_DEEP_ENTRY" not in messages[0]["content"], "world_info left the system msg"
    deep = _positions(messages, "WI_DEEP_ENTRY")
    shallow = _positions(messages, "WI_SHALLOW_ENTRY")
    assert deep and shallow, "both entries injected into history"
    assert deep[0] != shallow[0], "per-entry depth split them to different slots"
    # depth 4 sits further up (earlier index) than the depth-2 fallback.
    assert deep[0] < shallow[0], "depth-4 entry should precede the depth-2 entry"


async def test_injection_depth_frontmatter_override_merges(seeded_rp, monkeypatch):
    """A per-RP injection_depths override merges over the global defaults — moving
    scene_context to depth 0 keeps it in the system message even with injection on."""
    container = seeded_rp.container
    cfg = PromptConfig(
        injection=InjectionConfig(enabled=True),
        example_dialogue=ExampleDialogueConfig(enabled=False),
    )
    pa = _assembler_with(container, cfg)
    # Stub the guidelines lookup to supply an override (depth 0 = stay in system).
    monkeypatch.setattr(
        pa, "_effective_injection_depths",
        lambda rp: {"scene_context": 0, "triggered_notes": 2},
    )
    messages = await pa.build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=_inject_ctx(), session_id=None,
    )
    assert "# Current Scene" in messages[0]["content"], "depth-0 override should keep scene in system"
    assert "# Triggered Notes" not in messages[0]["content"], "triggered_notes should still inject"


# ---------------------------------------------------------------------------
# Feature D — example dialogue injection + PC skip
# ---------------------------------------------------------------------------


_EXAMPLE_BODY = (
    "A sharp-tongued ledger keeper.\n\n"
    "## Example Dialogue\n\n"
    "{{user}}: Who are you?\n"
    '{{char}}: "Someone you can\'t afford."\n'
)


async def test_example_dialogue_injected_for_npc(seeded_rp):
    """An NPC card's ## Example Dialogue becomes user/assistant pairs after the
    system message and before history."""
    container = seeded_rp.container
    await _insert_card(
        container.db, card_id=f"{RP_FOLDER}:dante_npc", name="DanteNPC",
        content=_EXAMPLE_BODY, frontmatter={"is_player_character": False},
    )
    cr = ContextResponse(
        current_exchange=4,
        npc_briefs=[NPCBrief(character="Dante", card_id=f"{RP_FOLDER}:dante_npc", trust_stage="neutral")],
    )
    cfg = PromptConfig(example_dialogue=ExampleDialogueConfig(enabled=True))
    messages = await _assembler_with(container, cfg).build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=cr, session_id=None,
    )
    # The example user line appears, and as a user message before any history.
    assert any(m["role"] == "user" and m.get("content") == "Who are you?" for m in messages)
    assert any(m["role"] == "assistant" and "afford" in (m.get("content") or "") for m in messages)
    # {{char}} was substituted with the brief's character name.
    assert not any("{{char}}" in (m.get("content") or "") for m in messages)


async def test_example_dialogue_skips_player_character(seeded_rp):
    """A PC card (is_player_character: true) is skipped — its voice is user-driven.
    Mutation guard: drop the PC check → the example line would appear."""
    container = seeded_rp.container
    await _insert_card(
        container.db, card_id=f"{RP_FOLDER}:lilith_pc", name="LilithPC",
        content=_EXAMPLE_BODY, frontmatter={"is_player_character": True},
    )
    cr = ContextResponse(
        current_exchange=4,
        npc_briefs=[NPCBrief(character="Lilith", card_id=f"{RP_FOLDER}:lilith_pc", trust_stage="neutral")],
    )
    cfg = PromptConfig(example_dialogue=ExampleDialogueConfig(enabled=True))
    messages = await _assembler_with(container, cfg).build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=cr, session_id=None,
    )
    assert not any(m.get("content") == "Who are you?" for m in messages), (
        "PC card example dialogue must not be injected"
    )


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_phase4_features_default_off_or_safe():
    """Backward-compat: injection + token budget default off; example dialogue on
    (it's additive — only fires when a card actually has examples)."""
    pc = PromptConfig()
    assert pc.injection.enabled is False
    assert pc.token_budget.enabled is False
    assert pc.example_dialogue.enabled is True
    # The default depth map encodes the roadmap's Prompt Structure decision.
    assert pc.injection.depths["scene_context"] == 4
    assert pc.injection.depths["triggered_notes"] == 2
    assert pc.injection.depths["direction"] == 1
