"""Key normalization utilities for entity matching.

Ported from .claude/scripts/lib/frontmatter-index.js lines 56-77.
"""

from __future__ import annotations

import re


def normalize_key(name: str) -> str:
    """Normalize an entity name to a canonical lowercase key."""
    if not name:
        return ""
    return str(name).lower().strip()


def make_entity_id(rp_folder: str, name: str) -> str:
    """Build the canonical entity_id / connection key for a card name.

    Entity rows (``story_cards.id``) and both endpoints of
    ``entity_connections`` (``from_entity`` / ``to_entity``) are stored as
    ``f"{rp_folder}:{normalize_key(name)}"`` — the ``rp_folder`` case is
    preserved, the name part is lowercased+stripped.

    Any cross-table join *into* ``entity_connections`` must build its lookup key
    through this function: comparing a bare display name (as stored in
    ``trust_baselines.character_a/b``) against a ``folder:key`` row silently
    misses every time (the relationship-role / ``dynamic`` drop). Centralising
    the format here keeps write-side and read-side from drifting apart.
    """
    return f"{rp_folder}:{normalize_key(name)}"


def file_to_key(filename: str) -> str:
    """Convert a filename (with or without .md) to a normalized key."""
    if not filename:
        return ""
    name = str(filename)
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.lower().strip()


def id_to_key(id_str: str) -> str:
    """Convert an ID string (underscored) to a normalized key.

    Example: ``memory_lilith_wakes_with_dante`` → ``memory lilith wakes with dante``
    """
    if not id_str:
        return ""
    return str(id_str).replace("_", " ").lower().strip()


def strip_parenthetical(name: str) -> str:
    """Remove trailing parenthetical notes from a name.

    Example: ``Dante Moretti (resident)`` → ``Dante Moretti``
    """
    if not name:
        return ""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(name)).strip()


# ---------------------------------------------------------------------------
# Card ID generation
# ---------------------------------------------------------------------------

CARD_TYPE_PREFIX: dict[str, str] = {
    "character": "char",
    "npc": "char",  # NPCs are characters
    "location": "loc",
    "memory": "mem",
    "secret": "sec",
    "knowledge": "know",
    "organization": "org",
    "plot_thread": "thread",
    "plot_arc": "arc",
    "item": "item",
    "lore": "lore",
    "chapter_summary": "ch",
}


def slugify(text: str) -> str:
    """Convert text to a snake_case slug.

    Example: ``'Dante Moretti'`` → ``'dante_moretti'``
    """
    s = str(text).lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def generate_card_id(card_type: str, name: str) -> str:
    """Generate a prefixed card_id from type and name.

    Example: ``('memory', 'First Kiss')`` → ``'mem_first_kiss'``
    """
    prefix = CARD_TYPE_PREFIX.get(card_type, card_type)
    slug = slugify(name)
    return f"{prefix}_{slug}"
