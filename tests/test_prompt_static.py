"""Static prompt trimming: NPC-framework gating, card-content cap, emotion map.

The NPC framework (~207 words) is hardcoded and was always injected. It must now
appear ONLY when NPCs are actually in the scene (non-empty ``npc_briefs``) — but
the existing ``include_npc_framework`` toggle still wins when off. The absent-case
is the load-bearing guard: a happy-path "with NPCs" test passes whether or not the
gate exists, so the gate is mutation-proven by the no-NPCs assertion (force-include
→ it goes red).

The card-content cap moved from a hard 2000-char truncation to a configurable
``context.max_card_content_length`` (default 5000); the 3000-char full-content
assertion reddens if the old 2000 cap returns.
"""

from __future__ import annotations

from pathlib import Path

from rp_engine.models.context import ContextDocument, ContextResponse, NPCBrief
from tests.conftest import RP_FOLDER

NPC_HEADER = "# NPC Framework"
EMOTION_HEADER = "Emotion to Physical Reaction Map"


def _ctx(*, npc: bool = False, documents=None) -> ContextResponse:
    return ContextResponse(
        current_exchange=1,
        npc_briefs=[NPCBrief(character="Mara", trust_stage="neutral")] if npc else [],
        documents=documents or [],
    )


def _doc(content: str) -> ContextDocument:
    return ContextDocument(
        name="The Ledger",
        card_type="lore",
        file_path="x.md",
        source="keyword",
        relevance_score=1.0,
        content=content,
        status="new",
    )


# ---------------------------------------------------------------------------
# NPC framework gating
# ---------------------------------------------------------------------------


def test_npc_framework_present_with_active_npcs(built_container):
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, _ctx(npc=True))
    assert NPC_HEADER in prompt, "NPC framework must appear when NPCs are active"


def test_npc_framework_absent_without_npcs(built_container):
    """The gate: no active NPCs → framework dropped. Mutation-proven — force the
    section back in (remove the pop) and this assertion fails."""
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, _ctx(npc=False))
    assert NPC_HEADER not in prompt, (
        "NPC framework leaked into a scene with no active NPCs (~207 wasted words)"
    )


def test_npc_framework_toggle_off_wins_over_active_npcs(built_container, primed_config):
    """include_npc_framework: false suppresses the section even with NPCs present."""
    vault = Path(primed_config.paths.vault_root)
    guidelines = vault / RP_FOLDER / "RP State" / "Story_Guidelines.md"
    guidelines.parent.mkdir(parents=True, exist_ok=True)
    guidelines.write_text("---\ninclude_npc_framework: false\n---\n", encoding="utf-8")

    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, _ctx(npc=True))
    assert NPC_HEADER not in prompt, "toggle off must suppress NPC framework regardless of NPCs"


# ---------------------------------------------------------------------------
# Card content cap (configurable, default 5000 — replaces hard 2000)
# ---------------------------------------------------------------------------


def test_card_content_not_truncated_below_cap(built_container):
    """3000 chars used to be cut at 2000; it must now survive in full."""
    body = "A" * 3000
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, _ctx(documents=[_doc(body)]))
    assert body in prompt, "content under the cap must not be truncated (old 2000 cap regressed?)"


def test_card_content_truncated_at_cap(built_container):
    """Content beyond the 5000-char default cap is trimmed."""
    body = "B" * 6000
    prompt = built_container.prompt_assembler.build_system_prompt(RP_FOLDER, _ctx(documents=[_doc(body)]))
    assert "B" * 5000 in prompt, "first 5000 chars must be present"
    assert "B" * 5001 not in prompt, "content must be capped at max_card_content_length"


# ---------------------------------------------------------------------------
# Emotion map toggle
# ---------------------------------------------------------------------------


def test_emotion_map_present_by_default(built_container):
    sections = built_container.prompt_assembler.get_sections(RP_FOLDER)
    static = built_container.prompt_assembler.assemble_static_prompt(sections)
    assert EMOTION_HEADER in static, "emotion map is on by default"


def test_emotion_map_toggle_off_removes_table(built_container, primed_config):
    """include_emotion_map: false drops just the table, keeping the rest of the
    writing section. Mutation-proven: ignore the toggle and this reddens."""
    vault = Path(primed_config.paths.vault_root)
    guidelines = vault / RP_FOLDER / "RP State" / "Story_Guidelines.md"
    guidelines.parent.mkdir(parents=True, exist_ok=True)
    guidelines.write_text("---\ninclude_emotion_map: false\n---\n", encoding="utf-8")

    sections = built_container.prompt_assembler.get_sections(RP_FOLDER)
    static = built_container.prompt_assembler.assemble_static_prompt(sections)
    assert EMOTION_HEADER not in static, "emotion map table must be gone when toggled off"
    assert "Core Pillars" in static, "the rest of Writing Principles must survive the toggle"
