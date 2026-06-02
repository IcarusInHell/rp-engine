"""Card metadata sidecar format (.meta/{stem}.json + body-only .md).

Two formats coexist per-card. The risk this phase introduces is the *new* read
path: a body-only ``.md`` whose metadata lives in a JSON sidecar. If the indexer
ever falls back to ``parse_frontmatter`` on a body-only ``.md`` it gets
``(None, body)`` and the card is silently dropped — the signature failure here.
So the load-bearing tests seed a NEW-format card and prove its metadata + body
actually reach ``story_cards`` with no YAML left in ``content``.

Each guard below was mutation-proven (see the phase notes): break the sidecar
read or drop the sidecar from the hash and the matching assertion goes red.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import watchfiles

from rp_engine.services.card_indexer import CardIndexer
from rp_engine.services.file_watcher import FileWatcher
from rp_engine.utils.frontmatter import (
    find_sidecar,
    read_sidecar,
    write_card_files,
    write_sidecar,
)
from tests.assertions import assert_present
from tests.conftest import RP_FOLDER

SIDECAR_RP = "SidecarRP"


def _write_new_format_card(chars: Path, stem: str, frontmatter: dict, body: str) -> Path:
    """Write a body-only .md + .meta/{stem}.json pair, returning the .md path."""
    chars.mkdir(parents=True, exist_ok=True)
    return write_card_files(chars, stem, frontmatter, body)


# ---------------------------------------------------------------------------
# utils/frontmatter sidecar primitives
# ---------------------------------------------------------------------------


def test_write_card_files_splits_body_and_metadata(tmp_path):
    md = write_card_files(
        tmp_path,
        "Dante Moretti",
        {"type": "character", "card_id": "char_dante", "name": "Dante Moretti"},
        "Dante is a calm, dangerous man.\n",
    )
    # .md is pure body — no YAML delimiter, no metadata keys.
    md_text = md.read_text(encoding="utf-8")
    assert md_text == "Dante is a calm, dangerous man.\n"
    assert "---" not in md_text
    assert "card_id" not in md_text

    sidecar = tmp_path / ".meta" / "Dante Moretti.json"
    assert sidecar.exists(), "sidecar must be written under .meta/"
    assert json.loads(sidecar.read_text())["card_id"] == "char_dante"


def test_find_and_read_sidecar_roundtrip(tmp_path):
    md = tmp_path / "Foo.md"
    md.write_text("body", encoding="utf-8")
    assert find_sidecar(md) is None, "no sidecar yet"

    write_sidecar(tmp_path / ".meta" / "Foo.json", {"name": "Foo", "tags": ["x"]})
    found = find_sidecar(md)
    assert found is not None
    assert read_sidecar(found) == {"name": "Foo", "tags": ["x"]}


def test_read_sidecar_malformed_returns_none(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    assert read_sidecar(bad) is None, "malformed sidecar must degrade to None, not raise"


# ---------------------------------------------------------------------------
# CardIndexer._read_card_pair — format detection
# ---------------------------------------------------------------------------


def _bare_indexer(vault: Path) -> CardIndexer:
    # _read_card_pair / _compute_content_hash touch only the filesystem.
    return CardIndexer(db=None, vault_root=vault)  # type: ignore[arg-type]


def test_read_card_pair_new_format(tmp_path):
    chars = tmp_path / "Story Cards" / "Characters"
    md = _write_new_format_card(
        chars, "Nadia", {"type": "character", "name": "Nadia"}, "Nadia runs the docks.\n"
    )
    fm, body = _bare_indexer(tmp_path)._read_card_pair(md)
    assert fm == {"type": "character", "name": "Nadia"}, "metadata must come from the sidecar"
    assert body == "Nadia runs the docks.\n", "body is the .md verbatim"


def test_read_card_pair_old_format(tmp_path):
    md = tmp_path / "legacy.md"
    md.write_text("---\ntype: character\nname: Old\n---\nLegacy body.\n", encoding="utf-8")
    fm, body = _bare_indexer(tmp_path)._read_card_pair(md)
    assert fm == {"type": "character", "name": "Old"}
    assert body == "Legacy body.\n"


def test_read_card_pair_malformed_sidecar_falls_back_to_yaml(tmp_path):
    """A broken sidecar must not eat a card that still has inline YAML."""
    chars = tmp_path / "Characters"
    chars.mkdir(parents=True)
    md = chars / "Mixed.md"
    md.write_text("---\ntype: character\nname: Mixed\n---\nStill here.\n", encoding="utf-8")
    (chars / ".meta").mkdir()
    (chars / ".meta" / "Mixed.json").write_text("{ broken", encoding="utf-8")

    fm, body = _bare_indexer(tmp_path)._read_card_pair(md)
    assert fm == {"type": "character", "name": "Mixed"}, "fell through to YAML on bad sidecar"
    assert body == "Still here.\n"


# ---------------------------------------------------------------------------
# Content hash — both files participate
# ---------------------------------------------------------------------------


def test_content_hash_changes_when_sidecar_changes(tmp_path):
    chars = tmp_path / "Characters"
    md = _write_new_format_card(chars, "Hashable", {"name": "Hashable", "v": 1}, "Body.\n")
    indexer = _bare_indexer(tmp_path)

    h1 = indexer._compute_content_hash(md)

    # Same .md body, different sidecar → hash MUST change (else sidecar edits
    # never trigger re-index — a silent staleness drop).
    write_sidecar(chars / ".meta" / "Hashable.json", {"name": "Hashable", "v": 2})
    h2 = indexer._compute_content_hash(md)
    assert h1 != h2, "sidecar change must shift the content hash"


def test_content_hash_body_only_without_sidecar(tmp_path):
    """No sidecar → hash is exactly hash_content(.md text), and distinct bodies
    hash distinctly. (Not f(x)==f(x) — that would stay green for any constant.)"""
    from rp_engine.utils.text import hash_content

    md = tmp_path / "plain.md"
    md.write_text("Just a body.\n", encoding="utf-8")
    indexer = _bare_indexer(tmp_path)

    # The no-sidecar hash must equal the hash of the .md content itself.
    assert indexer._compute_content_hash(md) == hash_content("Just a body.\n")

    # A different body must produce a different hash (rules out a constant return).
    other = tmp_path / "other.md"
    other.write_text("A different body.\n", encoding="utf-8")
    assert indexer._compute_content_hash(md) != indexer._compute_content_hash(other)


# ---------------------------------------------------------------------------
# Integration: full_index over a mixed-format folder
# ---------------------------------------------------------------------------


async def test_full_index_mixed_format_coexist(built_container, primed_config):
    """One legacy + one sidecar card in the same folder both reach story_cards,
    and the sidecar card's stored ``content`` is body-only (no YAML leaked)."""
    vault = Path(primed_config.paths.vault_root)
    chars = vault / SIDECAR_RP / "Story Cards" / "Characters"

    # Legacy (YAML in .md)
    chars.mkdir(parents=True)
    (chars / "legacy.md").write_text(
        "---\ntype: character\nname: LegacyLina\n---\nLina keeps the old ledgers.\n",
        encoding="utf-8",
    )
    # New (sidecar + body-only .md)
    _write_new_format_card(
        chars,
        "char_sidney",
        {"type": "character", "card_id": "char_sidney", "name": "Sidney"},
        "Sidney smuggles secrets past the harbour watch.\n",
    )

    await built_container.card_indexer.full_index(SIDECAR_RP)

    rows = await built_container.db.fetch_all(
        "SELECT name, content FROM story_cards WHERE rp_folder = ?", [SIDECAR_RP]
    )
    by_name = {r["name"]: r["content"] for r in rows}

    # Both formats indexed.
    assert_present("LegacyLina", by_name, label="story_cards names")
    assert_present("Sidney", by_name, label="story_cards names")

    # The sidecar card's content is the prose only — no frontmatter delimiter,
    # no metadata key. If _read_card_pair had fallen back to parse_frontmatter on
    # the body-only .md, Sidney would be ABSENT (frontmatter None → dropped).
    sidney_content = by_name["Sidney"]
    assert "Sidney smuggles secrets" in sidney_content
    assert "---" not in sidney_content, "YAML delimiter leaked into a sidecar card's content"
    assert "card_id" not in sidney_content, "metadata leaked into a sidecar card's content"


# ---------------------------------------------------------------------------
# CRUD round-trip writes the new format to disk
# ---------------------------------------------------------------------------


async def test_create_card_writes_sidecar_pair(client, seeded_rp, primed_config):
    vault = Path(primed_config.paths.vault_root)
    resp = await client.post(
        "/api/cards/lore",
        params={"rp_folder": RP_FOLDER, "sync_relationships": "false"},
        json={"name": "The Beacon", "frontmatter": {}, "content": "A beacon on the cliff."},
    )
    assert resp.status_code == 201, resp.text
    card_id = resp.json()["frontmatter"]["card_id"]

    md_path = vault / RP_FOLDER / "Story Cards" / "Lore" / f"{card_id}.md"
    sidecar = md_path.parent / ".meta" / f"{card_id}.json"

    assert md_path.exists(), "card .md must be written"
    assert sidecar.exists(), "card sidecar must be written under .meta/"

    md_text = md_path.read_text(encoding="utf-8")
    assert md_text.strip() == "A beacon on the cliff.", ".md must be body-only"
    assert "---" not in md_text, "no YAML frontmatter in a new-format .md"
    assert json.loads(sidecar.read_text())["card_id"] == card_id


async def test_delete_card_removes_both_files(client, seeded_rp, primed_config):
    vault = Path(primed_config.paths.vault_root)
    create = await client.post(
        "/api/cards/lore",
        params={"rp_folder": RP_FOLDER, "sync_relationships": "false"},
        json={"name": "Doomed Lore", "frontmatter": {}, "content": "To be deleted."},
    )
    assert create.status_code == 201, create.text
    card_id = create.json()["frontmatter"]["card_id"]
    md_path = vault / RP_FOLDER / "Story Cards" / "Lore" / f"{card_id}.md"
    sidecar = md_path.parent / ".meta" / f"{card_id}.json"
    assert md_path.exists() and sidecar.exists()

    deleted = await client.delete("/api/cards/lore/Doomed Lore")
    assert deleted.status_code == 200, deleted.text

    assert not md_path.exists(), "delete must remove the .md"
    assert not sidecar.exists(), "delete must remove the sidecar too (else orphan metadata)"


# ---------------------------------------------------------------------------
# Export/import bundle the .meta/ sidecars
# ---------------------------------------------------------------------------


async def test_export_import_roundtrips_sidecar(built_container, primed_config, tmp_path):
    """A sidecar card survives export → import: the .meta/{stem}.json is bundled
    and re-extracted. Without the export rglob or the import .json branch, the
    sidecar would vanish and the imported card would be body-only with no metadata."""
    from rp_engine.services.export_service import export_rp
    from rp_engine.services.import_service import import_rp

    vault = Path(primed_config.paths.vault_root)
    chars = vault / RP_FOLDER / "Story Cards" / "Characters"
    _write_new_format_card(
        chars,
        "char_quinn",
        {"type": "character", "card_id": "char_quinn", "name": "Quinn"},
        "Quinn ferries messages no one else will carry.\n",
    )

    buf = await export_rp(built_container.db, vault, RP_FOLDER)

    # Import into a fresh vault (import refuses to overwrite an existing folder).
    target_vault = tmp_path / "imported_vault"
    target_vault.mkdir()
    imported_folder, _stats = await import_rp(built_container.db, target_vault, buf.getvalue())

    imported_sidecar = (
        target_vault / imported_folder / "Story Cards" / "Characters" / ".meta" / "char_quinn.json"
    )
    imported_md = imported_sidecar.parent.parent / "char_quinn.md"
    assert imported_md.exists(), "body-only .md must be imported"
    assert imported_sidecar.exists(), "sidecar must be bundled and re-extracted on import"
    assert json.loads(imported_sidecar.read_text())["card_id"] == "char_quinn"


# ---------------------------------------------------------------------------
# File watcher: sidecar change re-indexes the paired .md; deletion warns loudly
# ---------------------------------------------------------------------------


async def test_sidecar_modify_reindexes_paired_md(built_container, primed_config):
    """A .meta/*.json change re-indexes its paired .md (the watcher's new branch)."""
    vault = Path(primed_config.paths.vault_root)
    chars = vault / SIDECAR_RP / "Story Cards" / "Characters"
    _write_new_format_card(
        chars,
        "char_wren",
        {"type": "character", "card_id": "char_wren", "name": "Wren"},
        "Wren watches the harbour gate.\n",
    )
    sidecar = chars / ".meta" / "char_wren.json"

    watcher = FileWatcher(built_container.card_indexer, vault, [SIDECAR_RP])
    await watcher._handle_sidecar_change(
        SIDECAR_RP, sidecar, watchfiles.Change.modified, watchfiles
    )

    row = await built_container.db.fetch_one(
        "SELECT name FROM story_cards WHERE rp_folder = ? AND name = ?",
        [SIDECAR_RP, "Wren"],
    )
    assert row is not None, "sidecar modify must re-index the paired .md into story_cards"


async def test_sidecar_deletion_orphan_warns_loudly(built_container, primed_config, caplog):
    """Sidecar gone + body-only .md left behind → can't parse as a card. The watcher
    must WARN (not swallow) so the stale-index orphan is observable. Mutation-proven:
    drop the warning and this guard reddens."""
    vault = Path(primed_config.paths.vault_root)
    chars = vault / SIDECAR_RP / "Story Cards" / "Characters"
    chars.mkdir(parents=True, exist_ok=True)
    md = chars / "char_orphan.md"
    md.write_text("Just a body, no metadata here.\n", encoding="utf-8")  # body-only, no sidecar
    sidecar = chars / ".meta" / "char_orphan.json"  # already deleted (does not exist)

    watcher = FileWatcher(built_container.card_indexer, vault, [SIDECAR_RP])
    with caplog.at_level(logging.WARNING):
        await watcher._handle_sidecar_change(
            SIDECAR_RP, sidecar, watchfiles.Change.deleted, watchfiles
        )

    assert any("Sidecar removed" in r.message for r in caplog.records), (
        "an orphaned body-only .md after sidecar deletion must warn loudly (silent-drop guard)"
    )


# ---------------------------------------------------------------------------
# cards.auto_migrate — opt-in startup conversion of legacy cards
# ---------------------------------------------------------------------------


def test_auto_migrate_defaults_off():
    """The escape hatch exists and is OFF by default (un-migrated cards keep YAML)."""
    import rp_engine.config as rp_config

    rp_config.get_config.cache_clear()
    assert rp_config.get_config().cards.auto_migrate is False


async def test_auto_migrate_converts_legacy_cards_on_startup(primed_config, monkeypatch):
    """With cards.auto_migrate=true, ServiceContainer.build converts legacy YAML
    cards to body-only + sidecar on disk before indexing — so no YAML reaches the
    prompt for ANY card, not just new/migrated ones. Mutation-proven: gate the
    container hook off and the legacy .md still has its frontmatter (red)."""
    import rp_engine.config as rp_config
    from tests.conftest import FakeProvider, RP_FOLDER

    vault = Path(primed_config.paths.vault_root)
    chars = vault / RP_FOLDER / "Story Cards" / "Characters"
    chars.mkdir(parents=True, exist_ok=True)
    legacy = chars / "legacy_hank.md"
    legacy.write_text(
        "---\ntype: character\nname: Hank\n---\nHank guards the vault.\n", encoding="utf-8"
    )

    monkeypatch.setenv("RP_ENGINE_CARDS__AUTO_MIGRATE", "true")
    rp_config.get_config.cache_clear()
    cfg = rp_config.get_config()
    assert cfg.cards.auto_migrate is True, "env override must enable auto_migrate"

    fake = FakeProvider(dimension=cfg.search.embedding_dimension)
    monkeypatch.setattr(
        "rp_engine.container.build_providers", lambda config: {config.llm.provider: fake}
    )

    from rp_engine.container import ServiceContainer

    container = await ServiceContainer.build(cfg)
    try:
        # The legacy .md is now body-only on disk, with a sidecar holding metadata.
        assert legacy.read_text(encoding="utf-8").strip() == "Hank guards the vault."
        assert "---" not in legacy.read_text(encoding="utf-8")
        sidecar = chars / ".meta" / "legacy_hank.json"
        assert sidecar.exists(), "auto_migrate must write the sidecar on startup"
        assert json.loads(sidecar.read_text())["name"] == "Hank"
    finally:
        await container.close()
        rp_config.get_config.cache_clear()
