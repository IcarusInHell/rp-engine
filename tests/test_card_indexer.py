"""Service: CardIndexer.full_index / index_file.

``full_index`` returns a counts dict; a silent drop here looks like a missing key
or a zero where work was actually done (the Phase 6a Bug-E ``reindex_all``
aggregation gap is the router-level cousin of this). We lock that the per-folder
indexer returns ALL six count fields and that the knowable counts are exact:

* entities = the cards on disk,
* trust_baselines_seeded = one per ``npc_trust_levels`` entry (directional),
* chunks > 0 (cards with bodies were actually embedded).

``index_file`` must be honestly idempotent: an unchanged file re-indexes to
``False`` (a swallowed no-op would silently re-chunk), a changed one to ``True``.
"""

from __future__ import annotations

from pathlib import Path

from tests.assertions import assert_count_exact, assert_nonempty

OTHER_RP = "OtherRP"

REQUIRED_COUNT_KEYS = {
    "entities",
    "connections",
    "aliases",
    "keywords",
    "chunks",
    "trust_baselines_seeded",
    "duration_ms",
}


def _write_other_vault(vault: Path) -> None:
    cards = vault / OTHER_RP / "Story Cards"
    chars = cards / "Characters"
    chars.mkdir(parents=True)
    (cards / "Locations").mkdir()

    (chars / "carol.md").write_text(
        "---\ntype: character\nname: Carol\nimportance: high\n"
        "npc_trust_levels:\n  Dave: 7\n---\n"
        "Carol is a sharp-eyed smuggler who trusts almost no one.\n",
        encoding="utf-8",
    )
    (chars / "dave.md").write_text(
        "---\ntype: character\nname: Dave\nimportance: medium\n"
        "npc_trust_levels:\n  Carol: -3\n---\n"
        "Dave is a nervous informant who fears Carol.\n",
        encoding="utf-8",
    )
    (cards / "Locations" / "dock.md").write_text(
        "---\ntype: location\nname: The Black Dock\n---\n"
        "A rotting pier where contraband changes hands after midnight.\n",
        encoding="utf-8",
    )


async def test_full_index_returns_all_count_fields(built_container, primed_config):
    """All six count keys present; the knowable counts are exact, chunks non-zero."""
    vault = Path(primed_config.paths.vault_root)
    _write_other_vault(vault)

    counts = await built_container.card_indexer.full_index(OTHER_RP)

    missing = REQUIRED_COUNT_KEYS - set(counts)
    assert not missing, f"full_index dropped count keys: {missing}"

    assert_count_exact(counts["entities"], 3, label="indexed entities (2 char + 1 loc)")
    assert_count_exact(
        counts["trust_baselines_seeded"], 2, label="directional trust baselines seeded"
    )
    assert counts["chunks"] > 0, "cards with bodies must produce vector chunks (none did)"


async def test_full_index_seeds_directional_trust(built_container, primed_config):
    """Carol→Dave (7) and Dave→Carol (-3) land as two distinct rows, not merged."""
    vault = Path(primed_config.paths.vault_root)
    _write_other_vault(vault)
    await built_container.card_indexer.full_index(OTHER_RP)

    cd = await built_container.db.fetch_val(
        """SELECT baseline_score FROM trust_baselines
           WHERE rp_folder = ? AND branch = 'main'
             AND character_a = 'Carol' AND character_b = 'Dave'""",
        [OTHER_RP],
    )
    dc = await built_container.db.fetch_val(
        """SELECT baseline_score FROM trust_baselines
           WHERE rp_folder = ? AND branch = 'main'
             AND character_a = 'Dave' AND character_b = 'Carol'""",
        [OTHER_RP],
    )
    assert cd == 7, "Carol→Dave baseline must read its own row"
    assert dc == -3, "Dave→Carol baseline must read its own row"


async def test_index_file_is_idempotent(built_container, primed_config):
    """A new file indexes True; unchanged re-index is False; a content change is True."""
    vault = Path(primed_config.paths.vault_root)
    chars = vault / OTHER_RP / "Story Cards" / "Characters"
    chars.mkdir(parents=True, exist_ok=True)
    erin = chars / "erin.md"
    erin.write_text(
        "---\ntype: character\nname: Erin\n---\nErin keeps the harbour ledgers.\n",
        encoding="utf-8",
    )

    first = await built_container.card_indexer.index_file(OTHER_RP, erin)
    assert first is True, "a brand-new card must index (True)"

    again = await built_container.card_indexer.index_file(OTHER_RP, erin)
    assert again is False, "an unchanged card must be a no-op (False), not re-chunked"

    erin.write_text(
        "---\ntype: character\nname: Erin\n---\nErin secretly skims from the ledgers.\n",
        encoding="utf-8",
    )
    changed = await built_container.card_indexer.index_file(OTHER_RP, erin)
    assert changed is True, "a changed card must re-index (True)"


async def test_full_index_indexes_cards(built_container, primed_config):
    """Sanity: the cards actually reach story_cards (non-empty), not just the counts."""
    vault = Path(primed_config.paths.vault_root)
    _write_other_vault(vault)
    await built_container.card_indexer.full_index(OTHER_RP)

    rows = await built_container.db.fetch_all(
        "SELECT name FROM story_cards WHERE rp_folder = ?", [OTHER_RP]
    )
    assert_nonempty(rows, label="story_cards for OtherRP")
    names = {r["name"] for r in rows}
    assert {"Carol", "Dave", "The Black Dock"} <= names
