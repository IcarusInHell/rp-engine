"""Export service — generates a ZIP archive of all RP state and card files."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from rp_engine.database import Database
from rp_engine.utils.frontmatter import parse_file
from rp_engine.utils.image import find_avatar

logger = logging.getLogger(__name__)

# Tables that contain critical, non-rebuildable state.
# Each spec: name, file, filter type, optional jsonl flag, optional stat_key.
_CRITICAL_TABLES: list[dict] = [
    {"name": "sessions", "file": "sessions.json", "filter": "rp_folder", "stat_key": "session_count"},
    {"name": "exchanges", "file": "exchanges.jsonl", "filter": "rp_folder", "jsonl": True, "stat_key": "exchange_count"},
    {"name": "branches", "file": "branches.json", "filter": "rp_folder", "stat_key": "branch_count"},
    {"name": "trust_modifications", "file": "trust_modifications.json", "filter": "rp_folder"},
    {"name": "character_state_entries", "file": "character_state_entries.json", "filter": "rp_folder"},
    {"name": "scene_state_entries", "file": "scene_state_entries.json", "filter": "rp_folder"},
    {"name": "plot_threads", "file": "plot_threads.json", "filter": "rp_folder"},
    {"name": "thread_counters", "file": "thread_counters.json", "filter": "rp_folder"},
    {"name": "thread_counter_entries", "file": "thread_counter_entries.json", "filter": "rp_folder"},
    {"name": "thread_status_entries", "file": "thread_status_entries.json", "filter": "rp_folder"},
    {"name": "events", "file": "events.json", "filter": "rp_folder"},
    {"name": "character_ledger", "file": "character_ledger.json", "filter": "rp_folder"},
    {"name": "custom_state_schemas", "file": "custom_state_schemas.json", "filter": "rp_folder"},
    {"name": "custom_state_entries", "file": "custom_state_entries.json", "filter": "rp_folder"},
    {"name": "situational_triggers", "file": "situational_triggers.json", "filter": "rp_folder"},
    {"name": "exchange_variants", "file": "exchange_variants.json", "filter": "rp_folder", "stat_key": "variant_count"},
    {"name": "exchange_bookmarks", "file": "exchange_bookmarks.json", "filter": "rp_folder", "stat_key": "bookmark_count"},
    {"name": "exchange_annotations", "file": "exchange_annotations.json", "filter": "rp_folder", "stat_key": "annotation_count"},
    {"name": "analysis_manifests", "file": "analysis_manifests.json", "filter": "rp_folder"},
    {"name": "analysis_manifest_entries", "file": "analysis_manifest_entries.json", "filter": "manifest"},
    {"name": "rp_chunking_config", "file": "rp_chunking_config.json", "filter": "rp_folder"},
]

# Tables that are useful but can be rebuilt or are supplementary
_OPTIONAL_TABLES: list[dict] = [
    {"name": "card_gaps", "file": "card_gaps.json", "filter": "rp_folder"},
    {"name": "card_gap_exchanges", "file": "card_gap_exchanges.json", "filter": "rp_folder"},
    {"name": "thread_evidence", "file": "thread_evidence.json", "filter": "rp_folder"},
    {"name": "session_summaries", "file": "session_summaries.json", "filter": "rp_folder"},
    {"name": "session_recaps", "file": "session_recaps.json", "filter": "rp_folder"},
    {"name": "trust_baselines", "file": "trust_baselines.json", "filter": "rp_folder"},
    {"name": "continuity_warnings", "file": "continuity_warnings.json", "filter": "rp_folder"},
    {"name": "extracted_memories", "file": "extracted_memories.json", "filter": "rp_folder"},
]


async def export_rp(
    db: Database,
    vault_root: Path,
    rp_folder: str,
    *,
    include_optional: bool = True,
) -> io.BytesIO:
    """Generate a ZIP archive containing all RP state and card files.

    Returns an in-memory BytesIO containing the ZIP.
    """
    buf = io.BytesIO()
    checksums: dict[str, str] = {}
    stats: dict[str, int] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ---- Card files ----
        rp_dir = vault_root / rp_folder
        card_count = 0
        if rp_dir.is_dir():
            # Story Cards/**/*.md plus their .meta/*.json sidecars
            cards_dir = rp_dir / "Story Cards"
            if cards_dir.is_dir():
                for md_file in cards_dir.rglob("*.md"):
                    rel = md_file.relative_to(rp_dir)
                    zf.writestr(f"cards/{rel.as_posix()}", md_file.read_bytes())
                    card_count += 1
                for meta_file in cards_dir.rglob(".meta/*.json"):
                    rel = meta_file.relative_to(rp_dir)
                    zf.writestr(f"cards/{rel.as_posix()}", meta_file.read_bytes())

            # Story_Guidelines.md
            guidelines_path = rp_dir / "RP State" / "Story_Guidelines.md"
            if guidelines_path.is_file():
                zf.writestr("guidelines.md", guidelines_path.read_bytes())

            # Avatar image — check frontmatter then convention filenames
            avatar_fm: str | None = None
            if guidelines_path.is_file():
                try:
                    frontmatter, _ = parse_file(guidelines_path)
                    if frontmatter:
                        avatar_fm = frontmatter.get("avatar")
                except Exception:
                    pass
            avatar_path = find_avatar(rp_dir, frontmatter_avatar=avatar_fm)
            if avatar_path is not None:
                zf.writestr(f"avatar{avatar_path.suffix}", avatar_path.read_bytes())

        # ---- Critical tables ----
        for spec in _CRITICAL_TABLES:
            data, checksum, row_count = await _export_table(db, rp_folder, spec)
            if data is not None:
                arc_path = f"state/{spec['file']}"
                zf.writestr(arc_path, data)
                checksums[spec["file"]] = f"sha256:{checksum}"
                if spec.get("stat_key"):
                    stats[spec["stat_key"]] = row_count

        # ---- Optional tables ----
        if include_optional:
            for spec in _OPTIONAL_TABLES:
                data, checksum, row_count = await _export_table(db, rp_folder, spec)
                if data is not None:
                    arc_path = f"optional/{spec['file']}"
                    zf.writestr(arc_path, data)
                    checksums[spec["file"]] = f"sha256:{checksum}"

        # ---- Manifest ----
        version = "1.0.0"
        try:
            version_path = Path(__file__).resolve().parents[2] / ".claude" / "VERSION"
            if version_path.is_file():
                version = version_path.read_text().strip()
        except Exception:
            pass

        manifest = {
            "format_version": "1.0",
            "rp_engine_version": version,
            "rp_folder": rp_folder,
            "exported_at": datetime.now(UTC).isoformat(),
            "export_options": {"include_optional": include_optional},
            "stats": {
                "exchange_count": stats.get("exchange_count", 0),
                "session_count": stats.get("session_count", 0),
                "card_count": card_count,
                "branch_count": stats.get("branch_count", 0),
                "bookmark_count": stats.get("bookmark_count", 0),
                "annotation_count": stats.get("annotation_count", 0),
                "variant_count": stats.get("variant_count", 0),
            },
            "checksums": checksums,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    buf.seek(0)
    return buf


async def _export_table(
    db: Database,
    rp_folder: str,
    spec: dict,
) -> tuple[str | None, str, int]:
    """Query a table and return (serialized data, SHA-256 hex digest, row count)."""
    table = spec["name"]
    filter_type = spec.get("filter", "rp_folder")
    is_jsonl = spec.get("jsonl", False)

    try:
        if filter_type == "manifest":
            # analysis_manifest_entries — join through manifests
            rows = await db.fetch_all(
                f"SELECT e.* FROM {table} e "
                f"JOIN analysis_manifests m ON e.manifest_id = m.id "
                f"WHERE m.rp_folder = ?",
                [rp_folder],
            )
        else:
            rows = await db.fetch_all(
                f"SELECT * FROM {table} WHERE rp_folder = ?",
                [rp_folder],
            )
    except Exception as exc:
        # Table may not exist if migrations haven't run
        logger.debug("Skipping table %s: %s", table, exc)
        return None, "", 0

    if not rows:
        return None, "", 0

    row_count = len(rows)

    if is_jsonl:
        lines = [json.dumps(row, default=str) for row in rows]
        data = "\n".join(lines)
    else:
        data = json.dumps(rows, indent=2, default=str)

    checksum = hashlib.sha256(data.encode()).hexdigest()
    return data, checksum, row_count
