"""Import service — restores an RP from a ZIP archive with ID remapping."""

from __future__ import annotations

import json
import logging
import re
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

from rp_engine.database import Database
from rp_engine.models.rp import ImportStats
from rp_engine.utils.image import AVATAR_EXTENSIONS

logger = logging.getLogger(__name__)

# Max ZIP size (500 MB)
_MAX_ZIP_BYTES = 500 * 1024 * 1024

# Allowed file extensions inside the ZIP
_ALLOWED_EXTENSIONS = {".md", ".json", ".jsonl", ".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Safe RP folder name pattern
_SAFE_NAME = re.compile(r"^[\w\s\-'()]+$")


class ImportError(Exception):
    """Raised when import validation fails."""


async def import_rp(
    db: Database,
    vault_root: Path,
    zip_bytes: bytes,
) -> tuple[str, ImportStats]:
    """Import an RP from a ZIP archive.

    Returns (rp_folder, stats).
    Raises ImportError on validation failure.
    """
    if len(zip_bytes) > _MAX_ZIP_BYTES:
        raise ImportError(f"ZIP file too large: {len(zip_bytes)} bytes (max {_MAX_ZIP_BYTES})")

    buf = BytesIO(zip_bytes)
    if not zipfile.is_zipfile(buf):
        raise ImportError("Not a valid ZIP file")

    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        # Validate all paths
        for info in zf.infolist():
            _validate_zip_entry(info)

        # Read manifest
        try:
            manifest_raw = zf.read("manifest.json")
            manifest = json.loads(manifest_raw)
        except KeyError:
            raise ImportError("Missing manifest.json in ZIP") from None
        except json.JSONDecodeError:
            raise ImportError("Invalid manifest.json") from None

        format_version = manifest.get("format_version", "")
        if not format_version.startswith("1."):
            raise ImportError(f"Unsupported format version: {format_version}")

        rp_folder = manifest.get("rp_folder", "")
        if not rp_folder or not _SAFE_NAME.match(rp_folder):
            raise ImportError(f"Invalid RP folder name in manifest: {rp_folder!r}")

        # Check for conflicts
        rp_dir = vault_root / rp_folder
        if rp_dir.exists():
            raise ImportError(f"RP folder already exists: {rp_folder}")

        stats = ImportStats()

        # ---- Write card files ----
        rp_dir.mkdir(parents=True, exist_ok=True)
        try:
            stats.cards_written = _write_card_files(zf, rp_dir)
            _write_lorebook_files(zf, rp_dir)

            # ---- Write guidelines ----
            if "guidelines.md" in zf.namelist():
                guidelines_dir = rp_dir / "RP State"
                guidelines_dir.mkdir(parents=True, exist_ok=True)
                (guidelines_dir / "Story_Guidelines.md").write_bytes(
                    zf.read("guidelines.md")
                )

            # ---- Write avatar (preserve exported filename) ----
            for entry in zf.namelist():
                if entry.startswith("avatar"):
                    p = PurePosixPath(entry)
                    if p.parent == PurePosixPath(".") and p.suffix.lower() in AVATAR_EXTENSIONS:
                        (rp_dir / p.name).write_bytes(zf.read(entry))
                        break

            # ---- Import database state ----
            await _import_state(db, zf, rp_folder, stats)

        except Exception:
            # Clean up on failure — remove partially created directory
            import shutil
            if rp_dir.exists():
                shutil.rmtree(rp_dir, ignore_errors=True)
            raise

    return rp_folder, stats


def _validate_zip_entry(info: zipfile.ZipInfo) -> None:
    """Reject dangerous ZIP entries."""
    name = info.filename

    # No absolute paths
    if name.startswith("/") or name.startswith("\\"):
        raise ImportError(f"Absolute path in ZIP: {name}")

    # No path traversal
    if ".." in name:
        raise ImportError(f"Path traversal in ZIP: {name}")

    # Skip directories
    if info.is_dir():
        return

    # Check extension
    p = PurePosixPath(name)
    if p.suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise ImportError(f"Disallowed file type in ZIP: {name}")


def _write_card_files(zf: zipfile.ZipFile, rp_dir: Path) -> int:
    """Write card .md files (and .meta/*.json sidecars) from the ZIP.

    Preserves directory structure. Only .md files count toward the returned card
    total; sidecars are extracted alongside but not counted (one card == one .md).
    """
    count = 0
    for entry in zf.namelist():
        if not entry.startswith("cards/"):
            continue
        is_md = entry.endswith(".md")
        is_sidecar = entry.endswith(".json") and "/.meta/" in entry
        if not (is_md or is_sidecar):
            continue
        # cards/Story Cards/Characters/Foo.md → Story Cards/Characters/Foo.md
        rel = entry[len("cards/"):]
        if not rel:
            continue
        target = rp_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(entry))
        if is_md:
            count += 1
    return count


def _write_lorebook_files(zf: zipfile.ZipFile, rp_dir: Path) -> int:
    """Write per-RP Lorebooks/ files (.json/.md + routing sidecars) from the ZIP.

    Returns the count of content files (.json/.md, excluding .meta/ sidecars).
    The server re-indexes lorebooks into lorebook_entries on next start."""
    count = 0
    for entry in zf.namelist():
        if not entry.startswith("lorebooks/"):
            continue
        if not (entry.endswith(".json") or entry.endswith(".md")):
            continue
        rel = entry[len("lorebooks/"):]
        if not rel:
            continue
        target = rp_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(entry))
        # Count content files only (sidecars live under .meta/ — overlay, not content).
        if "/.meta/" not in entry:
            count += 1
    return count


async def _import_table_batch(
    db: Database,
    zf: zipfile.ZipFile,
    table: str,
    rp_folder: str,
    *,
    prefix: str = "state",
    strip_id: bool = True,
    jsonl: bool = False,
    fk_remaps: dict[str, dict[int, int]] | None = None,
    build_id_map: bool = False,
) -> tuple[int, dict[int, int]]:
    """Import rows from a JSON/JSONL file in the ZIP into a database table.

    Args:
        db: Database instance.
        zf: Open ZipFile.
        table: Target table name (also used as ``{prefix}/{table}.json``).
        rp_folder: RP folder name to set on each row.
        prefix: ZIP directory prefix (``state`` or ``optional``).
        strip_id: Whether to pop the ``id`` column before insert (default True).
        jsonl: If True, read as JSONL instead of JSON array.
        fk_remaps: Dict of ``{column_name: id_map}`` for FK remapping.
        build_id_map: If True, capture old→new ID mapping and return it.

    Returns:
        (row_count, id_map) — id_map is empty dict when build_id_map is False.
    """
    ext = ".jsonl" if jsonl else ".json"
    reader = _read_jsonl if jsonl else _read_json
    rows = reader(zf, f"{prefix}/{table}{ext}")
    id_map: dict[int, int] = {}

    for row in rows:
        old_id = row.pop("id", None) if strip_id else None
        row["rp_folder"] = rp_folder

        if fk_remaps:
            for col, mapping in fk_remaps.items():
                _remap_fk(row, col, mapping)

        new_id = await _insert_row(db, table, row)
        if build_id_map and old_id is not None:
            id_map[int(old_id)] = new_id

    return len(rows), id_map


async def _import_state(
    db: Database,
    zf: zipfile.ZipFile,
    rp_folder: str,
    stats: ImportStats,
) -> None:
    """Import all database tables from the ZIP in FK-safe order."""
    # ID remapping: old auto-increment → new auto-increment
    exchange_id_map: dict[int, int] = {}
    manifest_id_map: dict[int, int] = {}

    # 1. Sessions (UUID PKs — no remapping needed)
    count, _ = await _import_table_batch(db, zf, "sessions", rp_folder, strip_id=False)
    stats.sessions_imported = count

    # 2. Branches (composite PK — no remapping needed)
    count, _ = await _import_table_batch(db, zf, "branches", rp_folder, strip_id=False)
    stats.branches_imported = count

    # 3. Exchanges (auto-increment ID — need remapping, stored as JSONL)
    count, exchange_id_map = await _import_table_batch(
        db, zf, "exchanges", rp_folder, jsonl=True, build_id_map=True,
    )
    stats.exchanges_imported = count

    # 4. Trust modifications (FK to exchanges.id via direct columns since migration 003)
    count, _ = await _import_table_batch(
        db, zf, "trust_modifications", rp_folder,
        fk_remaps={"exchange_id": exchange_id_map},
    )
    stats.trust_modifications_imported = count

    # 5. Tables with exchange_id FK (events, variants, bookmarks, annotations)
    for table, stat_field in [
        ("events", None),
        ("exchange_variants", "variants_imported"),
        ("exchange_bookmarks", "bookmarks_imported"),
        ("exchange_annotations", "annotations_imported"),
    ]:
        count, _ = await _import_table_batch(
            db, zf, table, rp_folder,
            fk_remaps={"exchange_id": exchange_id_map},
        )
        if stat_field:
            setattr(stats, stat_field, count)

    # 6. CoW state tables (no FK remapping — they use exchange_number, not exchange_id)
    for table in [
        "character_state_entries", "scene_state_entries", "character_ledger",
        "thread_counter_entries", "thread_status_entries",
        "custom_state_schemas", "custom_state_entries",
    ]:
        await _import_table_batch(db, zf, table, rp_folder)

    # 7. Tables with composite/text PKs (keep id column)
    for table in ["plot_threads", "thread_counters", "situational_triggers", "rp_chunking_config"]:
        await _import_table_batch(db, zf, table, rp_folder, strip_id=False)

    # 8. Analysis manifests (FK to exchanges.id, builds manifest_id_map)
    _, manifest_id_map = await _import_table_batch(
        db, zf, "analysis_manifests", rp_folder,
        fk_remaps={"exchange_id": exchange_id_map},
        build_id_map=True,
    )

    # 9. Manifest entries (FK to manifests.id, conditional target_id remap)
    manifest_entries = _read_json(zf, "state/analysis_manifest_entries.json")
    for row in manifest_entries:
        row.pop("id", None)
        _remap_fk(row, "manifest_id", manifest_id_map)
        if row.get("target_id") is not None and row.get("target_table") in (
            "exchanges", "events", "trust_modifications",
            "exchange_variants", "exchange_bookmarks", "exchange_annotations",
        ):
            _remap_fk(row, "target_id", exchange_id_map)
        await _insert_row(db, "analysis_manifest_entries", row)

    # 10. Optional tables (exchange_id FK where present)
    for table in [
        "card_gaps", "card_gap_exchanges", "thread_evidence",
        "session_summaries", "session_recaps", "trust_baselines",
        "continuity_warnings", "extracted_memories",
    ]:
        await _import_table_batch(
            db, zf, table, rp_folder,
            prefix="optional",
            fk_remaps={"exchange_id": exchange_id_map},
        )


def _read_json(zf: zipfile.ZipFile, path: str) -> list[dict]:
    """Read a JSON array from the ZIP, returning [] if not found."""
    if path not in zf.namelist():
        return []
    try:
        return json.loads(zf.read(path))
    except (json.JSONDecodeError, KeyError):
        return []


def _read_jsonl(zf: zipfile.ZipFile, path: str) -> list[dict]:
    """Read a JSONL file from the ZIP, returning [] if not found."""
    if path not in zf.namelist():
        return []
    try:
        text = zf.read(path).decode("utf-8")
        return [json.loads(line) for line in text.strip().split("\n") if line.strip()]
    except (json.JSONDecodeError, KeyError):
        return []


def _remap_fk(row: dict, key: str, id_map: dict[int, int]) -> None:
    """Remap a foreign key value using an ID map, in place."""
    val = row.get(key)
    if val is not None:
        row[key] = id_map.get(int(val), val)


async def _insert_row(db: Database, table: str, row: dict) -> int:
    """Insert a row and return lastrowid. Uses INSERT OR IGNORE."""
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    sql = f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"
    future = await db.enqueue_write(sql, list(row.values()))
    result = await future
    return result if isinstance(result, int) else 0
