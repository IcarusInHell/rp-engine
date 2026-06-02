"""Phase 5a — depth-0 section ordering (``prompt_order``) + narrator's note.

The byte-identity of the *default* (no ``prompt_order``) system prompt is locked
by ``test_prompt_injection.py::test_disabled_path_matches_golden_snapshot`` — the
static-unit refactor here runs under that golden. These tests lock the *new*
capability: custom ordering, omission-drops-a-depth-0-section, unknown-name
warnings, and Hygiene #1 (a section pulled to depth > 0 is NOT suppressed by
omission from ``prompt_order``). Plus the session-persistent narrator note,
which injects at its own depth independent of the global injection toggle.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from rp_engine.config import InjectionConfig, PromptConfig
from rp_engine.models.context import (
    ContextDocument,
    ContextResponse,
    NPCBrief,
    SceneState,
    ThreadAlert,
)
from rp_engine.services.prompt_assembler import (
    DEFAULT_PROMPT_ORDER,
    _ALL_SECTION_NAMES,
    PromptAssembler,
)
from tests.test_prompt_injection import _rich_context


class _FakeGuidelines:
    def __init__(self, *, prompt_order=None, injection_depths=None):
        self._po = prompt_order
        self._id = injection_depths

    def get_guidelines(self, rp_folder):
        return SimpleNamespace(prompt_order=self._po, injection_depths=self._id)


def _assembler(prompt_order=None, prompt_config=None) -> PromptAssembler:
    return PromptAssembler(
        vault_root=Path("/nonexistent_vault"),
        db=None,  # type: ignore[arg-type]
        guidelines_service=_FakeGuidelines(prompt_order=prompt_order),
        prompt_config=prompt_config,
    )


def _ctx() -> ContextResponse:
    return ContextResponse(
        current_exchange=1,
        scene_state=SceneState(location="The Hall", time_of_day="day"),
        npc_briefs=[NPCBrief(
            character="Dante", card_id="rp:d", archetype="POWER_HOLDER",
            trust_score=1, trust_stage="neutral",
        )],
        documents=[ContextDocument(
            name="Ledger", card_type="lore", file_path="x.md", source="keyword",
            relevance_score=1.5, content="A heavy ledger.", status="new",
            injection_tier="full",
        )],
        thread_alerts=[ThreadAlert(
            thread_id="t", name="Debt", level="moderate", counter=1, threshold=3,
            consequence="The debt is called in.",
        )],
    )


# ---------------------------------------------------------------------------
# prompt_order — reordering, omission, unknown names
# ---------------------------------------------------------------------------

def test_default_order_covers_all_dynamic_sections():
    """Drift guard: every section ``_build_dynamic_sections`` can emit must be in
    DEFAULT_PROMPT_ORDER and _ALL_SECTION_NAMES — otherwise a new section is
    silently dropped from any user prompt_order copied from the GET default (the
    project's signature silent-drop bug class). Mutation: add a section to
    _build_dynamic_sections without registering it here → this reddens."""
    names = {n for n, _ in _assembler()._build_dynamic_sections(_rich_context())}
    assert names, "rich context emitted no sections — fixture broken"
    missing_order = names - set(DEFAULT_PROMPT_ORDER)
    assert not missing_order, f"dynamic section(s) absent from DEFAULT_PROMPT_ORDER: {missing_order}"
    missing_known = names - _ALL_SECTION_NAMES
    assert not missing_known, f"dynamic section(s) absent from _ALL_SECTION_NAMES: {missing_known}"


def test_default_order_static_before_dynamic():
    """No prompt_order → default order: writing before output, static before the
    `---` boundary before dynamic."""
    out = _assembler().build_system_prompt("rp", _ctx())
    assert out.index("# Writing Principles") < out.index("# Output Format")
    boundary = out.index("\n\n---\n")
    assert out.index("# Output Format") < boundary < out.index("# Current Scene")


def test_custom_order_reorders_static_sections():
    """prompt_order can move a static section ahead of another. Mutation:
    _effective_prompt_order returning None (ignore the list) → output stays in
    default order and this reddens."""
    order = [
        "output_format", "writing_principles", "npc_framework",
        "scene_context", "npc_briefs", "plot_threads", "relevant_documents",
    ]
    out = _assembler(prompt_order=order).build_system_prompt("rp", _ctx())
    assert out.index("# Output Format") < out.index("# Writing Principles"), (
        "prompt_order did not move output_format ahead of writing_principles"
    )


def test_omitted_depth0_section_is_dropped():
    """A depth-0 section present this turn but omitted from a *present* prompt_order
    is dropped. Here npc_framework (static), scene_context and plot_threads are
    omitted; npc_briefs is kept."""
    order = ["writing_principles", "output_format", "npc_briefs", "relevant_documents"]
    out = _assembler(prompt_order=order).build_system_prompt("rp", _ctx())
    assert "# NPC Framework" not in out, "omitted static section not dropped"
    assert "# Current Scene" not in out, "omitted scene_context not dropped"
    assert "# Plot Thread Alerts" not in out, "omitted plot_threads not dropped"
    # Kept sections survive.
    assert "# Active NPCs" in out and "# Relevant Context" in out


def test_unknown_section_name_warns(caplog):
    """A bogus name in prompt_order warns (typo would otherwise silently shrink
    the prompt). Valid sections are still emitted."""
    order = ["bogus_section", "writing_principles", "npc_briefs"]
    with caplog.at_level(logging.WARNING):
        out = _assembler(prompt_order=order).build_system_prompt("rp", _ctx())
    assert any("bogus_section" in r.message for r in caplog.records), (
        "unknown prompt_order section did not warn"
    )
    assert "# Writing Principles" in out


def test_boundary_emitted_when_only_static_ordered():
    """If prompt_order names only static sections, the `---` dynamic boundary is
    still emitted (legacy always emits it with a context_response)."""
    out = _assembler(prompt_order=["writing_principles"]).build_system_prompt("rp", _ctx())
    assert "\n\n---\n" in out


# ---------------------------------------------------------------------------
# Hygiene #1 — a depth>0 section is NOT suppressed by omission from prompt_order
# ---------------------------------------------------------------------------

async def test_depth_section_survives_prompt_order_omission(seeded_rp):
    """With injection ON, scene_context is pulled to depth 4 (out of the system
    message) and relevant_documents stays depth-0. A prompt_order that omits BOTH
    must: drop relevant_documents (depth-0) from the system message, but keep
    scene_context — it's injected at its depth, not governed by prompt_order."""
    base = seeded_rp.container.prompt_assembler
    pa = PromptAssembler(
        vault_root=base.vault_root,
        db=seeded_rp.container.db,
        guidelines_service=_FakeGuidelines(prompt_order=["writing_principles", "output_format"]),
        ancestry_resolver=base.ancestry_resolver,
        prompt_config=PromptConfig(injection=InjectionConfig(enabled=True)),
    )
    messages = await pa.build_messages(
        seeded_rp.rp_folder, "main", "now", context_response=_ctx(), session_id=None,
    )
    system = messages[0]["content"]
    whole = "\n".join(m.get("content") or "" for m in messages)
    assert "# Relevant Context" not in system, (
        "depth-0 relevant_documents omitted from prompt_order should be dropped"
    )
    assert "# Current Scene" in whole, (
        "depth>0 scene_context wrongly suppressed by omission from prompt_order (Hygiene #1)"
    )
    assert "# Current Scene" not in system, "scene_context should be depth-injected, not in system"


# ---------------------------------------------------------------------------
# Narrator's note
# ---------------------------------------------------------------------------

async def _set_note(db, session_id, note, depth=2):
    fut = await db.enqueue_write(
        "UPDATE sessions SET narrator_note = ?, narrator_note_depth = ? WHERE id = ?",
        [note, depth, session_id],
    )
    await fut


async def test_narrator_note_injected_and_persists(seeded_rp):
    """The narrator note injects as a mid-history system message even with the
    global injection toggle OFF (default), and persists across turns. Mutation:
    drop the depth_content.setdefault for the note → it never appears."""
    pa = seeded_rp.container.prompt_assembler  # default config: injection OFF
    await _set_note(seeded_rp.db, "sess-1", "Keep the tension high.", depth=2)

    msgs1 = await pa.build_messages(
        seeded_rp.rp_folder, "main", "hi", context_response=None, session_id="sess-1",
    )
    note_msgs = [
        m for m in msgs1[1:]
        if m["role"] == "system" and "# Narrator's Note" in (m.get("content") or "")
    ]
    assert note_msgs, "narrator note did not inject (injection toggle is off — it must still appear)"
    assert "# Narrator's Note" not in msgs1[0]["content"], "note must be depth-injected, not in system"

    msgs2 = await pa.build_messages(
        seeded_rp.rp_folder, "main", "again", context_response=None, session_id="sess-1",
    )
    assert any(
        "# Narrator's Note" in (m.get("content") or "") for m in msgs2[1:]
    ), "narrator note did not persist across turns"


async def test_no_narrator_note_no_injection(seeded_rp):
    """No note set → no narrator section appears (precision)."""
    pa = seeded_rp.container.prompt_assembler
    msgs = await pa.build_messages(
        seeded_rp.rp_folder, "main", "hi", context_response=None, session_id="sess-1",
    )
    assert all("# Narrator's Note" not in (m.get("content") or "") for m in msgs)
