"""Phase 5b — SillyTavern world_info lorebook end-to-end.

Covers the ST format family in BOTH populations (same schema, verified):
keyword-matched selective entries AND ``constant`` always-on entries. Files →
LorebookIndexer → lorebook_entries → LorebookService (reuse evaluator + budget)
→ ContextResponse.lorebook_entries → ``# World Info`` prompt section.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rp_engine.config import ContextConfig
from rp_engine.models.context import ContextResponse, LorebookEntryHit
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.lorebook_indexer import LorebookIndexer
from rp_engine.services.lorebook_service import LorebookService
from rp_engine.services.trigger_evaluator import TriggerEvaluator

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parent / "fixtures" / "lorebooks"


def _patch_config(monkeypatch, *, enabled=True, budget=4000, shared=2000):
    """Patch the global config seen by both the lorebook service and the
    evaluator (stemming) so tests are order-independent."""
    ctx = ContextConfig()
    ctx.lorebook_enabled = enabled
    ctx.lorebook_budget_chars = budget
    ctx.lorebook_shared_budget_chars = shared
    cfg = SimpleNamespace(context=ctx, prompt=SimpleNamespace(trigger_stemming=True))
    monkeypatch.setattr("rp_engine.services.lorebook_service.get_config", lambda: cfg)
    monkeypatch.setattr("rp_engine.services.trigger_evaluator.get_config", lambda: cfg)


def _write_st(path: Path, entries: list[dict]) -> None:
    """Write a minimal ST world_info file (dict-keyed entries)."""
    path.write_text(json.dumps({
        "name": "test", "entries": {str(i): e for i, e in enumerate(entries)},
    }))


def _service(db, vault_root) -> LorebookService:
    return LorebookService(
        db=db,
        trigger_evaluator=TriggerEvaluator(db),
        guidelines_service=GuidelinesService(vault_root),
    )


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

async def test_indexer_parses_st_keyword_and_constant(db, tmp_path):
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True)
    _write_st(lb_dir / "book.json", [
        {"keys": ["dragon", "wyrm"], "content": "Dragons hoard gold.", "order": 50},
        {"keys": [], "content": "Always narrate vividly.", "constant": True},
        {"keys": [], "content": "unmatchable", "constant": False},  # skipped
    ])
    idx = LorebookIndexer(db, tmp_path)
    n = await idx.index_rp("rp1")
    assert n == 2, "keyless non-constant entry must be skipped (unmatchable)"

    rows = await db.fetch_all("SELECT * FROM lorebook_entries WHERE scope='rp'")
    by_content = {r["content"]: r for r in rows}
    assert json.loads(by_content["Dragons hoard gold."]["keywords"]) == ["dragon", "wyrm"]
    assert by_content["Dragons hoard gold."]["always_on"] == 0
    assert by_content["Always narrate vividly."]["always_on"] == 1
    # No branch column exists on the table at all.
    cols = [c["name"] for c in await db.fetch_all("PRAGMA table_info(lorebook_entries)")]
    assert "branch" not in cols


async def test_indexer_reindex_guard_skips_unchanged(db, tmp_path):
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True)
    _write_st(lb_dir / "book.json", [{"keys": ["x"], "content": "c"}])
    idx = LorebookIndexer(db, tmp_path)
    assert await idx.index_rp("rp1") == 1
    # Re-index with no file change → guard skips (returns 0 written).
    assert await idx.index_rp("rp1") == 0


# ---------------------------------------------------------------------------
# Service: match + always-on + disabled + budget
# ---------------------------------------------------------------------------

async def _seed(db, idx, tmp_path, entries):
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True, exist_ok=True)
    _write_st(lb_dir / "book.json", entries)
    await idx.index_rp("rp1")


async def test_service_matches_keyword_entry(db, tmp_path, monkeypatch):
    _patch_config(monkeypatch)
    idx = LorebookIndexer(db, tmp_path)
    await _seed(db, idx, tmp_path, [
        {"keys": ["dragon"], "content": "Dragons hoard gold."},
    ])
    svc = _service(db, tmp_path)

    hit = await svc.get_active_hits("rp1", "main", "a dragon appeared", {})
    assert [h.content for h in hit] == ["Dragons hoard gold."]
    miss = await svc.get_active_hits("rp1", "main", "a quiet meadow", {})
    assert miss == []


async def test_service_always_on_matches_anything(db, tmp_path, monkeypatch):
    _patch_config(monkeypatch)
    idx = LorebookIndexer(db, tmp_path)
    await _seed(db, idx, tmp_path, [
        {"keys": [], "content": "Narrate vividly.", "constant": True},
    ])
    svc = _service(db, tmp_path)
    hit = await svc.get_active_hits("rp1", "main", "unrelated text", {})
    assert [h.content for h in hit] == ["Narrate vividly."]


async def test_service_disabled_returns_nothing(db, tmp_path, monkeypatch):
    _patch_config(monkeypatch, enabled=False)
    idx = LorebookIndexer(db, tmp_path)
    await _seed(db, idx, tmp_path, [
        {"keys": [], "content": "Narrate vividly.", "constant": True},
    ])
    svc = _service(db, tmp_path)
    assert await svc.get_active_hits("rp1", "main", "anything", {}) == []


async def test_service_no_branch_filter_entry_on_every_branch(db, tmp_path, monkeypatch):
    """A per-RP entry must appear regardless of which branch is matching against
    (storage has no branch). Same entry fires on 'main' and a child branch."""
    _patch_config(monkeypatch)
    idx = LorebookIndexer(db, tmp_path)
    await _seed(db, idx, tmp_path, [{"keys": ["dragon"], "content": "Dragons."}])
    svc = _service(db, tmp_path)
    for branch in ("main", "child-branch", "some-other-branch"):
        hit = await svc.get_active_hits("rp1", branch, "a dragon", {})
        assert [h.content for h in hit] == ["Dragons."], f"missing on {branch}"


async def test_budget_drops_lower_weight_observable(db, tmp_path, monkeypatch, caplog):
    _patch_config(monkeypatch, budget=20)  # tiny budget
    idx = LorebookIndexer(db, tmp_path)
    await _seed(db, idx, tmp_path, [
        {"keys": ["dragon"], "content": "AAAAAAAAAAAAAAA", "order": 99},  # weight 99, 15 chars
        {"keys": ["dragon"], "content": "BBBBBBBBBBBBBBB", "order": 1},   # weight 1, dropped
    ])
    svc = _service(db, tmp_path)
    import logging
    with caplog.at_level(logging.INFO):
        hits = await svc.get_active_hits("rp1", "main", "a dragon", {})
    assert [h.content for h in hits] == ["AAAAAAAAAAAAAAA"], "highest weight kept"
    assert any("DROPPED" in r.message for r in caplog.records), "drop must be observable"


# ---------------------------------------------------------------------------
# Prompt assembly: # World Info section, non-empty guarded
# ---------------------------------------------------------------------------

async def test_prompt_world_info_section_present_and_guarded():
    # async (no awaits) to satisfy the module-level asyncio mark cleanly.
    from rp_engine.services.prompt_assembler import (
        DEFAULT_PROMPT_ORDER,
        _DYNAMIC_SECTION_NAMES,
        PromptAssembler,
    )
    assert "world_info" in _DYNAMIC_SECTION_NAMES
    assert "world_info" in DEFAULT_PROMPT_ORDER

    pa = PromptAssembler.__new__(PromptAssembler)  # no DB needed for this pure method

    # Empty → no section (keeps disabled-path byte-identical).
    empty = ContextResponse(current_exchange=1)
    names = [n for n, _ in pa._build_dynamic_sections(empty)]
    assert "world_info" not in names

    # Present → section rendered with content.
    resp = ContextResponse(current_exchange=1, lorebook_entries=[
        LorebookEntryHit(entry_id=1, name="Dragons", content="Dragons hoard gold.", scope="rp"),
    ])
    rendered = dict(pa._build_dynamic_sections(resp))
    assert "world_info" in rendered
    assert "# World Info" in rendered["world_info"]
    assert "Dragons hoard gold." in rendered["world_info"]


async def test_nsfl_shaped_entry_matches_on_primary_keys(db, tmp_path, monkeypatch):
    """The NSFL usage pattern: keyword-selective entries that ALSO carry
    ``keysecondary``/``selective``/``selectiveLogic`` AND-logic metadata, plus
    ``order``/``depth``/``position``. Secondary-key selective logic is DEFERRED
    (plan), so this proves the extra fields parse without breaking and the entry
    matches on its PRIMARY ``keys`` (secondary gracefully ignored)."""
    _patch_config(monkeypatch)
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True)
    _write_st(lb_dir / "nsfl_shape.json", [{
        "keys": ["tavern", "inn"],
        "keysecondary": ["drunk", "ale"],     # secondary AND-keys — deferred, ignored
        "selective": True,
        "selectiveLogic": 0,                   # AND_ANY — deferred
        "content": "The tavern is loud and smoky.",
        "constant": False,
        "order": 100, "depth": 4, "position": 1, "probability": 100,
    }])
    idx = LorebookIndexer(db, tmp_path)
    assert await idx.index_rp("rp1") == 1, "NSFL-shaped entry must parse despite extra fields"

    svc = _service(db, tmp_path)
    # Primary key present, NO secondary key → still fires (secondary logic deferred).
    hit = await svc.get_active_hits("rp1", "main", "they entered the tavern", {})
    assert [h.content for h in hit] == ["The tavern is loud and smoky."]
    # depth metadata carried through for Phase-4 injection.
    assert hit[0].depth == 4
    # Neither primary key present → no fire.
    assert await svc.get_active_hits("rp1", "main", "a silent forest", {}) == []


async def test_real_narration_styles_fixture_indexes(db, tmp_path):
    """The user's real ST always-on lorebook indexes (same schema as keyword
    books). Constant entries with no keys are stored as always-on."""
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True)
    (lb_dir / "narration.json").write_text(
        (FIXTURES / "narration_styles.json").read_text()
    )
    idx = LorebookIndexer(db, tmp_path)
    n = await idx.index_rp("rp1")
    assert n >= 1, "real narration_styles fixture should yield entries"
    rows = await db.fetch_all("SELECT always_on FROM lorebook_entries")
    assert any(r["always_on"] == 1 for r in rows), "constant entries → always_on"
