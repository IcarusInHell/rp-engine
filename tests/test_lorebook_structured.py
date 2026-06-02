"""Phase 5b — structured-doc lorebook (the third usage pattern).

A TTRPG doc (``dwarf.json``) has no ST ``entries`` — it's section-addressable.
Hybrid model: drop-in COARSE (every section fires on the file-key) → REFINE any
section toward precise layered scope via a routing sidecar that keeps the content
file pure. Conditions in the sidecar are a condition LIST + match_mode (never a
nested expression — the evaluator is a flat parser).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rp_engine.config import ContextConfig
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.lorebook_indexer import LorebookIndexer
from rp_engine.services.lorebook_service import LorebookService
from rp_engine.services.trigger_evaluator import TriggerEvaluator

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parent / "fixtures" / "lorebooks"


def _patch_config(monkeypatch, *, budget=8000):
    ctx = ContextConfig()
    ctx.lorebook_enabled = True
    ctx.lorebook_budget_chars = budget
    cfg = SimpleNamespace(context=ctx, prompt=SimpleNamespace(trigger_stemming=True))
    monkeypatch.setattr("rp_engine.services.lorebook_service.get_config", lambda: cfg)
    monkeypatch.setattr("rp_engine.services.trigger_evaluator.get_config", lambda: cfg)


def _service(db, vault_root) -> LorebookService:
    return LorebookService(db, TriggerEvaluator(db), GuidelinesService(vault_root))


def _drop_dwarf(tmp_path: Path, *, sidecar: dict | None = None) -> Path:
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True, exist_ok=True)
    (lb_dir / "dwarf.json").write_text((FIXTURES / "dwarf.json").read_text())
    if sidecar is not None:
        meta = lb_dir / ".meta"
        meta.mkdir(exist_ok=True)
        (meta / "dwarf.lorebook.json").write_text(json.dumps(sidecar))
    return lb_dir


# Routing sidecar mirroring the plan's dwarf example — layered scope as a
# condition LIST (NOT a nested expression).
_DWARF_ROUTING = {
    "summary": "Dwarves are stout, bearded folk of stone and craft.",
    "sections": {
        "physical.expressiveness": {
            "conditions": [
                {"type": "expression", "expr": 'any("dwarf","dwarven")'},
                {"type": "expression", "expr": 'near("dwarf","anger",120)'},
            ],
            "match_mode": "all",
            "budget_weight": 5,
            "depth": 4,
        },
        "social.notable_relationships.elves": {
            "conditions": [
                {"type": "expression", "expr": 'any("dwarf","dwarven")'},
                {"type": "expression", "expr": 'any("elf","elves")'},
            ],
            "match_mode": "all",
        },
    },
}


async def test_structured_coarse_all_sections_fire(db, tmp_path, monkeypatch):
    """Drop-in, no sidecar: every top-level section fires on the file-key.
    (Budget raised so the ~15k of coarse content all fits — budget dropping is
    covered separately in test_lorebook_st.)"""
    _patch_config(monkeypatch, budget=50000)
    _drop_dwarf(tmp_path)
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = _service(db, tmp_path)

    hits = await svc.get_active_hits("rp1", "main", "a dwarf walked in", {})
    paths = await _section_paths(db, [h.entry_id for h in hits])
    assert "physical" in paths and "social" in paths and "mechanical" in paths
    assert "__summary__" in paths


async def test_structured_refined_routes_precisely(db, tmp_path, monkeypatch):
    """With the routing sidecar, the dwarf-bristles-at-elf scene injects
    expressiveness + elf-relationship (+ summary) and STAYS SILENT on
    stats/subraces/aging (coarse is off once sections are refined)."""
    _patch_config(monkeypatch)
    _drop_dwarf(tmp_path, sidecar=_DWARF_ROUTING)
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = _service(db, tmp_path)

    scene = "The dwarf glared at the elf, anger flaring hot in his chest."
    hits = await svc.get_active_hits("rp1", "main", scene, {})
    paths = set(await _section_paths(db, [h.entry_id for h in hits]))

    assert "physical.expressiveness" in paths, "anger near dwarf → expressiveness fires"
    assert "social.notable_relationships.elves" in paths, "elf present → relationship fires"
    assert "__summary__" in paths, "summary always coarse-fires on file-key"
    # Silent on everything not routed:
    assert "mechanical" not in paths and "subraces" not in paths
    assert "physical" not in paths, "whole-physical coarse entry suppressed by refinement"
    # depth metadata carried from the sidecar.
    expr_hit = next(h for h in hits if h.name.endswith("physical.expressiveness"))
    assert expr_hit.depth == 4


async def test_refinement_narrows_what_fires(db, tmp_path, monkeypatch):
    """Coarse→refine: the near("dwarf","anger") leg narrows expressiveness so a
    no-anger dwarf scene does NOT inject it. Mutation proof of the refinement:
    drop the near leg (coarse) and expressiveness would fire on any dwarf."""
    _patch_config(monkeypatch)
    _drop_dwarf(tmp_path, sidecar=_DWARF_ROUTING)
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = _service(db, tmp_path)

    calm = "The dwarf hammered a glowing blade at his forge."  # no anger, no elf
    hits = await svc.get_active_hits("rp1", "main", calm, {})
    paths = set(await _section_paths(db, [h.entry_id for h in hits]))
    assert "physical.expressiveness" not in paths, "no anger → refined expressiveness silent"
    assert "social.notable_relationships.elves" not in paths, "no elf → silent"
    assert paths == {"__summary__"}, "only the coarse summary fires on a plain dwarf mention"


async def test_multi_format_coexist(db, tmp_path, monkeypatch):
    """An ST world_info JSON and a structured dwarf.json in the SAME folder both
    index and fire."""
    _patch_config(monkeypatch, budget=50000)
    lb_dir = _drop_dwarf(tmp_path)
    (lb_dir / "st.json").write_text(json.dumps({
        "name": "st", "entries": {"0": {"keys": ["sword"], "content": "Swords are sharp."}},
    }))
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = _service(db, tmp_path)

    hits = await svc.get_active_hits("rp1", "main", "a dwarf drew a sword", {})
    contents = [h.content for h in hits]
    assert any("Swords are sharp." == c for c in contents), "ST entry fired"
    # The structured doc fired (entry names are prefixed with the file stem).
    assert any(h.name.startswith("dwarf") for h in hits), "structured dwarf sections fired"


async def test_markdown_lorebook(db, tmp_path, monkeypatch):
    """A markdown lorebook with ## headings → keyworded section entries."""
    _patch_config(monkeypatch)
    lb_dir = tmp_path / "rp1" / "Lorebooks"
    lb_dir.mkdir(parents=True)
    (lb_dir / "places.md").write_text(
        "# Places\n\n## Ravenport\nA fog-bound harbor town ruled by smugglers.\n\n"
        "## Highspire\nA mountain fortress of the eastern lords.\n"
    )
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = _service(db, tmp_path)

    hit = await svc.get_active_hits("rp1", "main", "they sailed into Ravenport at dusk", {})
    assert any("fog-bound harbor" in h.content for h in hit)
    miss_paths = await svc.get_active_hits("rp1", "main", "an empty plain", {})
    assert miss_paths == []


async def _section_paths(db, entry_ids: list[int]) -> list[str]:
    if not entry_ids:
        return []
    rows = await db.fetch_all(
        f"SELECT section_path FROM lorebook_entries WHERE id IN ({','.join('?' * len(entry_ids))})",
        entry_ids,
    )
    return [r["section_path"] for r in rows]
