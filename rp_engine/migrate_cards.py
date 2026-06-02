"""Convert story cards between legacy (YAML frontmatter) and sidecar formats.

Sidecar format: a body-only ``.md`` plus ``.meta/{stem}.json`` holding metadata.
Legacy format: a single ``.md`` with YAML frontmatter.

Forward migration (default) strips frontmatter out of each ``.md`` into a sidecar.
``--reverse`` re-serializes sidecar + body back into a single ``.md`` and removes
the sidecar. Both directions are lossless. This tool only rewrites files on disk —
the running server re-indexes changed cards on its next start (content hash shifts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rp_engine.utils.frontmatter import (
    find_sidecar,
    parse_frontmatter,
    read_sidecar,
    serialize_frontmatter,
    write_card_files,
)


@dataclass(slots=True)
class MigrationReport:
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.migrated)} migrated, "
            f"{len(self.skipped)} skipped, {len(self.errors)} errored"
        )


def _iter_card_md(vault_root: Path, rp_folder: str | None) -> list[Path]:
    """All card .md files under one RP folder, or every RP folder when None."""
    if rp_folder:
        roots = [vault_root / rp_folder / "Story Cards"]
    else:
        roots = [
            child / "Story Cards"
            for child in vault_root.iterdir()
            if child.is_dir() and (child / "Story Cards").is_dir()
        ]
    md_files: list[Path] = []
    for root in roots:
        if root.is_dir():
            md_files.extend(root.rglob("*.md"))
    return md_files


def migrate_cards(
    vault_root: Path,
    rp_folder: str | None = None,
    *,
    dry_run: bool = False,
    reverse: bool = False,
) -> MigrationReport:
    """Convert cards forward (legacy → sidecar) or ``--reverse`` (sidecar → legacy)."""
    report = MigrationReport()

    for md_path in _iter_card_md(vault_root, rp_folder):
        rel = str(md_path)
        try:
            sidecar = find_sidecar(md_path)
            if reverse:
                if sidecar is None:
                    report.skipped.append(f"{rel} (no sidecar)")
                    continue
                frontmatter = read_sidecar(sidecar)
                if frontmatter is None:
                    report.errors.append(f"{rel} (unreadable sidecar)")
                    continue
                body = md_path.read_text(encoding="utf-8")
                if not dry_run:
                    md_path.write_text(
                        serialize_frontmatter(frontmatter, body), encoding="utf-8"
                    )
                    sidecar.unlink(missing_ok=True)
                report.migrated.append(rel)
            else:
                if sidecar is not None:
                    report.skipped.append(f"{rel} (already sidecar)")
                    continue
                frontmatter, body = parse_frontmatter(md_path.read_text(encoding="utf-8"))
                if frontmatter is None:
                    report.skipped.append(f"{rel} (no frontmatter)")
                    continue
                if not dry_run:
                    write_card_files(md_path.parent, md_path.stem, frontmatter, body)
                report.migrated.append(rel)
        except Exception as exc:  # noqa: BLE001 — report, don't abort the batch
            report.errors.append(f"{rel} ({exc})")

    return report
