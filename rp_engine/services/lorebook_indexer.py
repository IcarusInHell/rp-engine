"""Lorebook file ingest + indexing (Phase 5b).

Files are the SOURCE OF TRUTH (story-card model): SillyTavern ``world_info``
JSON, structured TTRPG docs, and markdown dropped into a per-RP ``Lorebooks/``
folder or a configurable global-library path are parsed into ``lorebook_entries``
(an index/cache, rebuilt from files). The API never modifies the files. Format
is detected PER-FILE (like card legacy/sidecar).

Matching REUSES ``TriggerEvaluator`` (no second matcher): each entry stores a
``conditions`` JSON list (or NULL = keyword-only, condition derived at match
time) combined via ``match_mode``. Compound/layered scope is a condition LIST +
``match_mode='all'`` — NEVER a nested expression string (the evaluator is a flat
parser that mis-parses ``all(any(...), near(...))``; confirmed Phase 5b).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rp_engine.database import Database
from rp_engine.utils.text import hash_content

logger = logging.getLogger(__name__)


@dataclass
class LorebookEntry:
    """Normalized internal entry shape — the on-disk format is invisible past here."""
    source_path: str
    name: str
    content: str
    keywords: list[str] = field(default_factory=list)
    conditions: list[dict] | None = None
    match_mode: str = "any"
    section_path: str | None = None
    budget_weight: int = 1
    always_on: bool = False
    depth: int | None = None
    enabled: bool = True


class LorebookIndexer:
    """Parses lorebook files into the ``lorebook_entries`` cache table."""

    def __init__(self, db: Database, vault_root: Path) -> None:
        self.db = db
        self.vault_root = vault_root

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def rp_lorebook_dir(self, rp_folder: str) -> Path:
        return self.vault_root / rp_folder / "Lorebooks"

    @staticmethod
    def _discover(folder: Path) -> list[Path]:
        if not folder.is_dir():
            return []
        files: list[Path] = []
        for p in sorted(folder.rglob("*")):
            # skip routing sidecars (.meta/*.lorebook.json) — they're an overlay,
            # not a content file; the indexer reads them while parsing the paired file.
            if p.parent.name == ".meta":
                continue
            if p.suffix in (".json", ".md"):
                files.append(p)
        return files

    # ------------------------------------------------------------------
    # Full index (startup)
    # ------------------------------------------------------------------

    async def index_rp(self, rp_folder: str) -> int:
        """Index a per-RP ``Lorebooks/`` folder. Returns entry count."""
        folder = self.rp_lorebook_dir(rp_folder)
        total = 0
        for path in self._discover(folder):
            total += await self.index_file("rp", rp_folder, path)
        return total

    async def index_global(self, global_path: str | None) -> int:
        """Index the global-library folder (scope='global'). Returns entry count."""
        if not global_path:
            return 0
        folder = Path(global_path)
        total = 0
        for path in self._discover(folder):
            total += await self.index_file("global", None, path)
        return total

    # ------------------------------------------------------------------
    # Per-file index (delete-by-source + reinsert; file-hash re-index guard)
    # ------------------------------------------------------------------

    async def index_file(self, scope: str, rp_folder: str | None, path: Path) -> int:
        """Parse one file and upsert its entries. Skips when the file hash is
        unchanged (re-index guard). Returns the number of entries written (0 if
        skipped or the file yielded none)."""
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Lorebook file unreadable %s: %s", path, e)
            return 0

        file_hash = hash_content(raw)
        source = str(path)

        existing = await self.db.fetch_all(
            "SELECT DISTINCT content_hash FROM lorebook_entries WHERE source_path = ?",
            [source],
        )
        existing_hashes = {r["content_hash"] for r in existing}
        if existing_hashes and existing_hashes == {file_hash}:
            logger.debug("Lorebook unchanged, skipping reindex: %s", path)
            return 0

        try:
            entries = self._parse_file(path, raw)
        except Exception as e:
            logger.warning("Failed to parse lorebook %s: %s", path, e)
            return 0

        # Delete-then-insert so a removed/renamed entry doesn't linger.
        await (await self.db.enqueue_write(
            "DELETE FROM lorebook_entries WHERE source_path = ?", [source]
        ))

        now = datetime.now(UTC).isoformat()
        written = 0
        for e in entries:
            await (await self.db.enqueue_write(
                """INSERT INTO lorebook_entries
                   (source_path, section_path, scope, rp_folder, name, keywords,
                    conditions, match_mode, content, budget_weight, always_on,
                    depth, enabled, content_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    e.source_path, e.section_path, scope, rp_folder, e.name,
                    json.dumps(e.keywords) if e.keywords else None,
                    json.dumps(e.conditions) if e.conditions else None,
                    e.match_mode, e.content, e.budget_weight,
                    1 if e.always_on else 0, e.depth, 1 if e.enabled else 0,
                    file_hash, now, now,
                ],
            ))
            written += 1
        logger.info("Indexed lorebook %s: %d entries (scope=%s)", path, written, scope)
        return written

    async def remove_file(self, path: Path) -> None:
        await (await self.db.enqueue_write(
            "DELETE FROM lorebook_entries WHERE source_path = ?", [str(path)]
        ))

    # ------------------------------------------------------------------
    # Parsing — per-file format detection
    # ------------------------------------------------------------------

    def _parse_file(self, path: Path, raw: str) -> list[LorebookEntry]:
        if path.suffix == ".json":
            data = json.loads(raw)
            if isinstance(data, dict) and "entries" in data:
                return self._parse_st_world_info(path, data)
            # Structured doc (no top-level ``entries``) — Increment 4.
            return self._parse_structured_doc(path, data)
        if path.suffix == ".md":
            return self._parse_markdown(path, raw)
        return []

    def _parse_st_world_info(self, path: Path, data: dict) -> list[LorebookEntry]:
        """SillyTavern ``world_info``: ``entries`` dict|list of keyworded entries."""
        entries_raw = data.get("entries")
        if isinstance(entries_raw, dict):
            items = list(entries_raw.values())
        elif isinstance(entries_raw, list):
            items = entries_raw
        else:
            items = []

        out: list[LorebookEntry] = []
        for e in items:
            if not isinstance(e, dict):
                continue
            content = (e.get("content") or "").strip()
            if not content:
                continue
            keys = e.get("keys") or e.get("key") or []
            keywords = [str(k).strip() for k in keys if str(k).strip()]
            constant = bool(e.get("constant"))
            # A keyless, non-constant ST entry can never fire — skip it (don't
            # silently store an unmatchable row).
            if not keywords and not constant:
                continue
            enabled = bool(e.get("enabled", True)) and not bool(e.get("disable", False))
            weight = e.get("priority") or e.get("order") or e.get("insertion_order") or 1
            depth = e.get("depth") if isinstance(e.get("depth"), int) else None
            name = (str(e.get("comment") or e.get("name") or "").strip()
                    or (keywords[0] if keywords else "entry"))
            out.append(LorebookEntry(
                source_path=str(path),
                name=name,
                content=content,
                keywords=keywords,
                conditions=None,        # keyword-only — derived at match time
                match_mode="any",
                budget_weight=int(weight) if isinstance(weight, (int, float)) else 1,
                always_on=constant,
                depth=depth,
                enabled=enabled,
            ))
        return out

    # ------------------------------------------------------------------
    # Structured docs → section-addressable entries (hybrid: coarse + refine)
    # ------------------------------------------------------------------

    def _parse_structured_doc(self, path: Path, data) -> list[LorebookEntry]:
        """Structured TTRPG doc (e.g. ``dwarf.json``) → section entries.

        Hybrid model:
        - **Coarse (no sidecar):** one entry per top-level section, condition =
          file-key ``any(...)``. Works the instant the file is dropped in.
        - **Refined (routing sidecar):** ``.meta/{stem}.lorebook.json`` maps a
          (possibly DEEP, dotted) section path → a condition LIST + match_mode +
          budget/depth. When ``sections`` are present they REPLACE the coarse
          top-level entries (so a refined file injects only the matching sections,
          staying silent on the rest); set ``coarse: true`` to keep un-routed
          top-level sections firing coarsely too.
        - **Summary tier:** an always-coarse short entry (explicit ``summary`` or
          derived) — "a dwarf is present → general appearance only."

        Conditions in the sidecar are stored VERBATIM as a list (never a nested
        expression — the evaluator is a flat parser; layered scope = list + all).
        """
        if not isinstance(data, dict):
            return []
        routing = self._load_routing_sidecar(path) or {}
        file_keys = [str(k).strip() for k in (routing.get("file_keys") or self._file_keys(path, data)) if str(k).strip()]

        entries: list[LorebookEntry] = []

        # Summary tier (coarse — fires on file-key, not literally always-on).
        summary = routing.get("summary") or self._derive_summary(data)
        if summary:
            entries.append(LorebookEntry(
                source_path=str(path), section_path="__summary__",
                name=f"{path.stem} (summary)", content=str(summary),
                keywords=file_keys, conditions=None, match_mode="any",
            ))

        sections_routing = routing.get("sections") or {}
        for spath, rule in sections_routing.items():
            if not isinstance(rule, dict):
                continue
            subtree = self._get_path(data, str(spath))
            if subtree is None:
                logger.warning("Lorebook routing path %r not found in %s", spath, path)
                continue
            conds = rule.get("conditions")
            entries.append(LorebookEntry(
                source_path=str(path), section_path=str(spath),
                name=f"{path.stem}.{spath}",
                content=self._render_subtree(subtree),
                # When explicit conditions are given they win; else fall back to
                # the coarse file-key keyword condition for this section.
                keywords=[] if conds else file_keys,
                conditions=conds if isinstance(conds, list) else None,
                match_mode=str(rule.get("match_mode", "all")),
                budget_weight=int(rule.get("budget_weight", 1)),
                depth=rule.get("depth") if isinstance(rule.get("depth"), int) else None,
            ))

        # Coarse top-level sections: default ON only when no sidecar sections
        # refined the file (else the refined set defines what injects).
        coarse = routing.get("coarse", not sections_routing)
        if coarse:
            for key, val in data.items():
                if key == "name":
                    continue
                # skip trivial scalars (short strings / numbers) — not worth an entry
                if not isinstance(val, (dict, list)) and not (isinstance(val, str) and len(val) > 40):
                    continue
                entries.append(LorebookEntry(
                    source_path=str(path), section_path=str(key),
                    name=f"{path.stem}.{key}",
                    content=self._render_subtree(val, label=str(key)),
                    keywords=file_keys, conditions=None, match_mode="any",
                ))
        return entries

    def _load_routing_sidecar(self, path: Path) -> dict | None:
        """Load ``.meta/{stem}.lorebook.json`` (routing overlay, content stays pure)."""
        sidecar = path.parent / ".meta" / f"{path.stem}.lorebook.json"
        if not sidecar.exists():
            return None
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning("Malformed lorebook routing sidecar %s: %s", sidecar, e)
            return None

    @staticmethod
    def _file_keys(path: Path, data: dict) -> list[str]:
        """Derive coarse file-keys from the filename stem + optional ``name``."""
        keys = [path.stem.lower()]
        name = data.get("name")
        if isinstance(name, str):
            for tok in re.split(r"\W+", name.lower()):
                if tok and tok not in keys:
                    keys.append(tok)
        return keys

    @staticmethod
    def _get_path(data: dict, dotted: str):
        """Resolve a dotted key path (``physical.expressiveness``) into the tree."""
        node = data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        return node

    def _derive_summary(self, data: dict) -> str | None:
        """Fallback summary: ``description`` → ``physical.build`` → first long str."""
        for path in ("description", "physical.build", "build"):
            val = self._get_path(data, path)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in data.values():
            if isinstance(val, str) and len(val.strip()) > 40:
                return val.strip()
        return None

    def _render_subtree(self, val, label: str | None = None, depth: int = 0) -> str:
        """Flatten a JSON subtree into readable key/value text for injection."""
        pad = "  " * depth
        lines: list[str] = []
        if isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    lines.append(self._render_subtree(v, depth=depth + 1))
                else:
                    lines.append(f"{pad}{k}: {v}")
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, (dict, list)):
                    lines.append(self._render_subtree(item, depth=depth + 1))
                else:
                    lines.append(f"{pad}- {item}")
        else:
            lines.append(f"{pad}{val}")
        body = "\n".join(line for line in lines if line.strip())
        return f"{label}:\n{body}" if label and depth == 0 else body

    # ------------------------------------------------------------------
    # Markdown lorebook (heading-sectioned; whole-file fallback)
    # ------------------------------------------------------------------

    def _parse_markdown(self, path: Path, raw: str) -> list[LorebookEntry]:
        """Markdown lorebook: ``## Heading`` sections become keyworded entries
        (keywords derived from the heading); a file with no headings becomes one
        always-on entry."""
        parts = re.split(r"^##\s+(.+)$", raw, flags=re.M)
        if len(parts) <= 1:
            body = raw.strip()
            if not body:
                return []
            return [LorebookEntry(
                source_path=str(path), name=path.stem, content=body,
                always_on=True, match_mode="any",
            )]
        out: list[LorebookEntry] = []
        it = iter(parts[1:])
        for head, body in zip(it, it):
            head, body = head.strip(), body.strip()
            if not body:
                continue
            kws = [w for w in re.split(r"\W+", head.lower()) if len(w) > 2] or [head.lower()]
            out.append(LorebookEntry(
                source_path=str(path), section_path=head, name=head,
                content=f"## {head}\n{body}", keywords=kws, match_mode="any",
            ))
        return out
