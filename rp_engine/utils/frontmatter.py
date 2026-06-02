"""YAML frontmatter parser and serializer for story card .md files.

Two card-on-disk formats coexist:

- **Old format** — a single ``.md`` file with YAML frontmatter + prose body.
- **New (sidecar) format** — a body-only ``.md`` plus a ``.meta/{stem}.json``
  sidecar holding the metadata. The ``.md`` is exactly what the LLM sees; the
  ``.meta/`` folder is dot-prefixed so Obsidian hides it.

Detection is per-card: a sidecar at ``.meta/{stem}.json`` means new format,
otherwise old format. Both are read transparently by the indexer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def parse_frontmatter(content: str) -> tuple[dict | None, str]:
    """Split markdown content into frontmatter dict and body text.

    Frontmatter is YAML between opening and closing ``---`` delimiters.
    Returns ``(None, content)`` if no valid frontmatter is found.
    """
    if not content.startswith("---"):
        return None, content

    # Find closing delimiter (skip the opening ---)
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return None, content

    yaml_text = content[3:end_idx].strip()
    if not yaml_text:
        return None, content

    try:
        frontmatter = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None, content

    # frontmatter should be a dict
    if not isinstance(frontmatter, dict):
        return None, content

    # Body is everything after the closing ---
    body = content[end_idx + 3:]
    if body.startswith("\n"):
        body = body[1:]

    return frontmatter, body


def parse_file(file_path: Path) -> tuple[dict | None, str]:
    """Read a markdown file and parse its frontmatter.

    Returns ``(frontmatter, body)`` or ``(None, content)`` on failure.
    """
    text = file_path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def serialize_frontmatter(frontmatter: dict, body: str) -> str:
    """Serialize a frontmatter dict and body back into markdown with ``---`` delimiters."""
    yaml_text = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    # Ensure body has leading newline separation
    if body and not body.startswith("\n"):
        body = "\n" + body
    return f"---\n{yaml_text}---{body}"


# ----------------------------------------------------------------------
# Sidecar format (.meta/{stem}.json + body-only .md)
# ----------------------------------------------------------------------


def find_sidecar(md_path: Path) -> Path | None:
    """Return the sidecar path for a card ``.md`` if one exists, else ``None``.

    The join key is the filename stem: ``Dante Moretti.md`` pairs with
    ``.meta/Dante Moretti.json`` in the same directory.
    """
    sidecar_path = md_path.parent / ".meta" / f"{md_path.stem}.json"
    return sidecar_path if sidecar_path.exists() else None


def read_sidecar(sidecar_path: Path) -> dict | None:
    """Read and parse a JSON sidecar. Returns ``None`` on missing/invalid JSON.

    A malformed sidecar logs a warning and returns ``None`` so the caller can
    fall back to old-format parsing rather than silently dropping the card.
    """
    if not sidecar_path.exists():
        return None
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Invalid sidecar %s: %s", sidecar_path, exc)
        return None
    return data if isinstance(data, dict) else None


def write_sidecar(sidecar_path: Path, frontmatter: dict) -> None:
    """Write a metadata dict to a JSON sidecar (pretty-printed, unicode-safe)."""
    sidecar_path.parent.mkdir(exist_ok=True)
    sidecar_path.write_text(
        json.dumps(frontmatter, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_card_files(
    card_dir: Path, filename_stem: str, frontmatter: dict, body: str
) -> Path:
    """Write a card as a body-only ``.md`` + ``.meta/{stem}.json`` sidecar pair.

    Returns the ``.md`` path. Lives in this neutral module because both
    ``routers/cards/crud.py`` (a router) and ``services/card_authoring.py`` (a
    service) need it, and services must not import from routers.
    """
    md_path = card_dir / f"{filename_stem}.md"
    md_path.write_text(body, encoding="utf-8")

    sidecar_path = card_dir / ".meta" / f"{filename_stem}.json"
    write_sidecar(sidecar_path, frontmatter)

    return md_path
