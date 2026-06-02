"""Phase 5b — watcher routing, management endpoints, export/import, and the
documented word-boundary deviation (lorebook inherits the evaluator's substring
matching by reuse)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import watchfiles

from rp_engine.config import ContextConfig
from rp_engine.services.file_watcher import FileWatcher
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.lorebook_indexer import LorebookIndexer
from rp_engine.services.lorebook_service import LorebookService
from rp_engine.services.trigger_evaluator import TriggerEvaluator
from tests.conftest import RP_FOLDER

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# File watcher routing
# ---------------------------------------------------------------------------

def _watcher(db, vault, *, global_path=None) -> FileWatcher:
    idx = LorebookIndexer(db, vault)
    return FileWatcher(
        card_indexer=None, vault_root=vault, rp_folders=["rp1"],
        lorebook_indexer=idx, lorebook_global_path=global_path,
    )


async def test_watcher_scope_classification(db, tmp_path):
    fw = _watcher(db, tmp_path, global_path=str(tmp_path / "global_lib"))
    rp_file = tmp_path / "rp1" / "Lorebooks" / "book.json"
    glob_file = tmp_path / "global_lib" / "shared.json"
    card_file = tmp_path / "rp1" / "Story Cards" / "Foo.md"

    assert fw._lorebook_scope(rp_file) == ("rp", "rp1")
    assert fw._lorebook_scope(glob_file) == ("global", None)
    assert fw._lorebook_scope(card_file) is None, "a card is not a lorebook"


async def test_watcher_reindexes_content_and_routing_sidecar(db, tmp_path):
    fw = _watcher(db, tmp_path)
    lb = tmp_path / "rp1" / "Lorebooks"
    lb.mkdir(parents=True)
    book = lb / "book.json"
    book.write_text(json.dumps({"name": "b", "entries": {"0": {"keys": ["x"], "content": "C"}}}))

    # Content add → indexed.
    await fw._handle_lorebook_change("rp", "rp1", book, watchfiles.Change.added, watchfiles)
    rows = await db.fetch_all("SELECT * FROM lorebook_entries WHERE source_path = ?", [str(book)])
    assert len(rows) == 1

    # Routing sidecar change → re-indexes the paired content file (not the sidecar).
    meta = lb / ".meta"
    meta.mkdir()
    sidecar = meta / "book.lorebook.json"
    sidecar.write_text("{}")
    # mutate content so the reindex guard doesn't skip
    book.write_text(json.dumps({"name": "b", "entries": {
        "0": {"keys": ["x"], "content": "C"}, "1": {"keys": ["y"], "content": "D"}}}))
    await fw._handle_lorebook_change("rp", "rp1", sidecar, watchfiles.Change.modified, watchfiles)
    rows2 = await db.fetch_all("SELECT * FROM lorebook_entries WHERE source_path = ?", [str(book)])
    assert len(rows2) == 2, "routing sidecar change re-indexed the paired content file"

    # Content deletion → entries removed.
    await fw._handle_lorebook_change("rp", "rp1", book, watchfiles.Change.deleted, watchfiles)
    rows3 = await db.fetch_all("SELECT * FROM lorebook_entries WHERE source_path = ?", [str(book)])
    assert rows3 == []


async def test_watcher_lorebook_dir_in_watch_paths(db, tmp_path):
    lb = tmp_path / "rp1" / "Lorebooks"
    lb.mkdir(parents=True)
    fw = _watcher(db, tmp_path)
    assert lb in fw._get_watch_paths()


# ---------------------------------------------------------------------------
# Management endpoints
# ---------------------------------------------------------------------------

def _drop_into_seeded(seeded_rp) -> Path:
    vault = seeded_rp.container.vault_root
    lb = vault / RP_FOLDER / "Lorebooks"
    lb.mkdir(parents=True, exist_ok=True)
    (lb / "creatures.json").write_text(
        json.dumps({"name": "c", "entries": {"0": {"keys": ["goblin"], "content": "Goblins are sneaky."}}})
    )
    (lb / "places.md").write_text("## Town\nA bustling market town.\n")
    gl = vault / RP_FOLDER / "RP State" / "Story_Guidelines.md"
    gl.parent.mkdir(parents=True, exist_ok=True)
    gl.write_text("---\ninclude_npc_framework: true\n---\nBody.\n", encoding="utf-8")
    return lb


async def test_endpoint_list_lorebooks(client, seeded_rp):
    _drop_into_seeded(seeded_rp)
    resp = await client.get("/api/lorebook", params={"rp_folder": RP_FOLDER})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    stems = {f["stem"] for f in body["files"]}
    assert {"creatures", "places"} <= stems
    # No explicit active-set → all per-RP files active.
    assert all(f["active"] for f in body["files"] if f["scope"] == "rp")


async def test_endpoint_set_active_set_writes_frontmatter(client, seeded_rp):
    _drop_into_seeded(seeded_rp)
    resp = await client.put(
        "/api/lorebook/active", params={"rp_folder": RP_FOLDER},
        json={"lorebooks": ["creatures"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_set"] == ["creatures"]
    active = {f["stem"]: f["active"] for f in body["files"] if f["scope"] == "rp"}
    assert active["creatures"] is True
    assert active["places"] is False, "places excluded from the active-set"


async def test_endpoint_reindex(client, seeded_rp):
    _drop_into_seeded(seeded_rp)
    resp = await client.post("/api/lorebook/reindex", params={"rp_folder": RP_FOLDER})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reindexed"] is True


# ---------------------------------------------------------------------------
# Export / import round-trip
# ---------------------------------------------------------------------------

async def test_export_import_bundles_lorebook_files(db, tmp_path):
    from rp_engine.services.export_service import export_rp
    from rp_engine.services.import_service import import_rp

    vault = tmp_path / "vault"
    lb = vault / "MyRP" / "Lorebooks"
    lb.mkdir(parents=True)
    (lb / "book.json").write_text(json.dumps({"name": "b", "entries": {"0": {"keys": ["x"], "content": "C"}}}))
    meta = lb / ".meta"
    meta.mkdir()
    (meta / "book.lorebook.json").write_text(json.dumps({"summary": "s"}))
    (vault / "MyRP" / "RP State").mkdir(parents=True)
    (vault / "MyRP" / "RP State" / "Story_Guidelines.md").write_text("---\n---\nBody.\n")

    buf = await export_rp(db, vault, "MyRP")
    names = []
    import zipfile, io
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        names = zf.namelist()
    assert any(n.endswith("Lorebooks/book.json") for n in names), "content file bundled"
    assert any("book.lorebook.json" in n for n in names), "routing sidecar bundled"

    # Import into a fresh vault.
    dest = tmp_path / "dest"
    dest.mkdir()
    folder, _stats = await import_rp(db, dest, buf.getvalue())
    assert (dest / folder / "Lorebooks" / "book.json").exists()
    assert (dest / folder / "Lorebooks" / ".meta" / "book.lorebook.json").exists()


async def test_empty_lorebooks_list_means_none_active(db, tmp_path, monkeypatch):
    """An EXPLICIT ``lorebooks: []`` disables per-RP lorebooks (distinct from
    absent → all active). Confirms the empty-means-none semantics."""
    ctx = ContextConfig()
    ctx.lorebook_enabled = True
    cfg = SimpleNamespace(context=ctx, prompt=SimpleNamespace(trigger_stemming=False))
    monkeypatch.setattr("rp_engine.services.lorebook_service.get_config", lambda: cfg)
    monkeypatch.setattr("rp_engine.services.trigger_evaluator.get_config", lambda: cfg)

    lb = tmp_path / "rp1" / "Lorebooks"
    lb.mkdir(parents=True)
    (lb / "book.json").write_text(json.dumps({"name": "b", "entries": {"0": {"keys": ["dragon"], "content": "C"}}}))
    gl = tmp_path / "rp1" / "RP State" / "Story_Guidelines.md"
    gl.parent.mkdir(parents=True)
    gl.write_text("---\nlorebooks: []\n---\nBody.\n", encoding="utf-8")

    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = LorebookService(db, TriggerEvaluator(db), GuidelinesService(tmp_path))

    hits = await svc.get_active_hits("rp1", "main", "a dragon roared", {})
    assert hits == [], "explicit lorebooks: [] → no per-RP files active"


# ---------------------------------------------------------------------------
# Documented deviation: lorebook keyword matching is SUBSTRING (evaluator reuse),
# NOT word-boundary. The plan's verification asked for word-boundary, but the
# keyword-derived lorebook hits are gated by a WORD-BOUNDARY post-filter (the
# stated Phase 5b deliverable, "art ∌ start"), applied on the lorebook side AFTER
# the reused evaluator — no fork of the shared matcher, no change to the 5a union.
# ---------------------------------------------------------------------------

async def test_keyword_matching_is_word_boundary_not_substring(db, tmp_path, monkeypatch):
    """The delivered criterion: a keyword matches only as a WHOLE WORD. 'art' must
    NOT fire inside 'start', but MUST fire on a standalone 'art'. Mutation guard:
    disabling the _keyword_word_boundary gate reddens the substring case."""
    ctx = ContextConfig()
    ctx.lorebook_enabled = True
    cfg = SimpleNamespace(context=ctx, prompt=SimpleNamespace(trigger_stemming=True))
    monkeypatch.setattr("rp_engine.services.lorebook_service.get_config", lambda: cfg)
    monkeypatch.setattr("rp_engine.services.trigger_evaluator.get_config", lambda: cfg)

    lb = tmp_path / "rp1" / "Lorebooks"
    lb.mkdir(parents=True)
    (lb / "b.json").write_text(json.dumps({"name": "b", "entries": {"0": {"keys": ["art"], "content": "ART"}}}))
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = LorebookService(db, TriggerEvaluator(db), GuidelinesService(tmp_path))

    # substring INSIDE a larger word → dropped (the stated deliverable).
    no_hit = await svc.get_active_hits("rp1", "main", "they made a fresh start", {})
    assert no_hit == [], "'art' must NOT match inside 'start' (word-boundary)"
    # standalone whole word → fires.
    hit = await svc.get_active_hits("rp1", "main", "she studied the art of war", {})
    assert [h.content for h in hit] == ["ART"], "'art' as a whole word must fire"


async def test_keyword_word_boundary_keeps_morphology(db, tmp_path, monkeypatch):
    """Word-boundary must not break stemming: keyword 'cat' still fires on 'cats'
    (whole-token stem match), it just won't fire inside 'category'."""
    ctx = ContextConfig()
    ctx.lorebook_enabled = True
    cfg = SimpleNamespace(context=ctx, prompt=SimpleNamespace(trigger_stemming=True))
    monkeypatch.setattr("rp_engine.services.lorebook_service.get_config", lambda: cfg)
    monkeypatch.setattr("rp_engine.services.trigger_evaluator.get_config", lambda: cfg)

    lb = tmp_path / "rp1" / "Lorebooks"
    lb.mkdir(parents=True)
    (lb / "b.json").write_text(json.dumps({"name": "b", "entries": {"0": {"keys": ["cat"], "content": "CAT"}}}))
    idx = LorebookIndexer(db, tmp_path)
    await idx.index_rp("rp1")
    svc = LorebookService(db, TriggerEvaluator(db), GuidelinesService(tmp_path))

    assert [h.content for h in await svc.get_active_hits("rp1", "main", "three cats sat", {})] == ["CAT"], \
        "stemmed whole-token 'cats'→'cat' must still fire"
    assert await svc.get_active_hits("rp1", "main", "a category error", {}) == [], \
        "'cat' must NOT fire inside 'category'"
