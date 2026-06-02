"""Entity extractor morphology: keyword matching is stemmed on BOTH sides.

The keyword leg of entity matching (`entity_keywords`, score 0.5) is the most
behaviour-changing part of the Layer A wiring: the index key is stemmed at load
and the runtime token is stemmed at lookup, so plural/inflected RP text matches
a singular authored keyword. Aliases and names (`entity_aliases`, story_cards
.name — proper nouns) are deliberately NOT stemmed.

These guards fail loud if either invariant breaks:
  - keyword stemming becomes one-sided  -> the plural text stops matching
  - alias stemming creeps in            -> a proper noun matches a truncated form
"""

from __future__ import annotations

import pytest

from rp_engine.services.entity_extractor import EntityExtractor

_RP = "test-rp"


async def _seed_card(db, entity_id: str, name: str) -> None:
    fut = await db.enqueue_write(
        """INSERT INTO story_cards (id, rp_folder, file_path, card_type, name, frontmatter)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [entity_id, _RP, f"Characters/{name}.md", "character", name, "{}"],
    )
    await fut


async def _seed_keyword(db, entity_id: str, keyword: str) -> None:
    fut = await db.enqueue_write(
        "INSERT INTO entity_keywords (keyword, entity_id) VALUES (?, ?)",
        [keyword, entity_id],
    )
    await fut


async def _seed_alias(db, entity_id: str, alias: str) -> None:
    fut = await db.enqueue_write(
        "INSERT INTO entity_aliases (alias, entity_id) VALUES (?, ?)",
        [alias, entity_id],
    )
    await fut


@pytest.mark.asyncio
async def test_keyword_query_side_is_stemmed(db):
    # Authored keyword is the stem "sword"; the message only ever says "swords".
    # Locks the QUERY side: un-stemming the runtime token breaks this match.
    await _seed_card(db, f"{_RP}:blade", "Blade")
    await _seed_keyword(db, f"{_RP}:blade", "sword")

    result = await EntityExtractor(db).extract(
        user_message="She unsheathed two swords.",
        last_response=None,
        rp_folder=_RP,
    )
    matched = {m.entity_id: m for m in result.matched_entities}
    assert f"{_RP}:blade" in matched, "plural 'swords' must match the 'sword' keyword"
    assert matched[f"{_RP}:blade"].match_source == "keyword"


@pytest.mark.asyncio
async def test_keyword_index_side_is_stemmed(db):
    # Authored keyword is the INFLECTED form "running"; the message says "runs".
    # Both stem to "run" only if the index key is stemmed at load — locks the
    # INDEX side: un-stemming the map key (leaving "running") breaks this match.
    await _seed_card(db, f"{_RP}:runner", "Runner")
    await _seed_keyword(db, f"{_RP}:runner", "running")

    result = await EntityExtractor(db).extract(
        user_message="He runs through the gate.",
        last_response=None,
        rp_folder=_RP,
    )
    assert any(m.entity_id == f"{_RP}:runner" for m in result.matched_entities), (
        "'runs' must match the inflected 'running' keyword (index side stemmed)"
    )


@pytest.mark.asyncio
async def test_proper_noun_alias_is_not_stemmed(db):
    # Alias "Ross" must match exactly and must NOT be reachable via "Ros" — locks
    # the "aliases unstemmed" decision (stemming proper nouns is harmful).
    await _seed_card(db, f"{_RP}:ross", "Ross")
    await _seed_alias(db, f"{_RP}:ross", "ross")

    hit = await EntityExtractor(db).extract(
        user_message="Ross walked in.", last_response=None, rp_folder=_RP
    )
    assert any(m.entity_id == f"{_RP}:ross" for m in hit.matched_entities)

    miss = await EntityExtractor(db).extract(
        user_message="The ros plant wilted.", last_response=None, rp_folder=_RP
    )
    assert not any(m.entity_id == f"{_RP}:ross" for m in miss.matched_entities), (
        "alias 'ross' must not be stemmed to 'ros'"
    )
