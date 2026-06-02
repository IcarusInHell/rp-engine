"""FTS5 porter tokenizer (migration 027) — morphology-aware keyword search.

Before 027, ``vectors_fts`` used the default unicode61 tokenizer (no stemming),
so a BM25 keyword search for ``running`` would not match content containing
``runs``. Porter stems both indexed content and query terms, so regular
morphological variants match. This locks two things:

  1. ``MATCH 'running'`` retrieves a row whose only relevant token is ``runs``
     (impossible under unicode61 — the whole point of the migration).
  2. Row-count parity between ``vectors`` and ``vectors_fts`` after the rebuild,
     and that the sync trigger keeps a fresh insert searchable.
"""

from __future__ import annotations

import pytest


async def _insert_vector(db, content: str) -> None:
    fut = await db.enqueue_write(
        "INSERT INTO vectors (content, embedding) VALUES (?, ?)",
        [content, b"\x00"],
    )
    await fut


async def _match(db, term: str) -> list[str]:
    rows = await db.fetch_all(
        "SELECT content FROM vectors_fts WHERE vectors_fts MATCH ?", [term]
    )
    return [r["content"] for r in rows]


@pytest.mark.asyncio
async def test_porter_query_matches_inflected_content(db):
    await _insert_vector(db, "she runs every morning")
    await _insert_vector(db, "the dog barked loudly")

    hits = await _match(db, "running")
    assert hits == ["she runs every morning"], (
        "porter must stem 'running' and 'runs' to a common root; "
        "unicode61 would return nothing here"
    )


@pytest.mark.asyncio
async def test_fts_row_parity_and_trigger_keeps_index_live(db):
    await _insert_vector(db, "connection established quickly")
    await _insert_vector(db, "horses galloped across the field")

    v_count = await db.fetch_val("SELECT count(*) FROM vectors")
    fts_count = await db.fetch_val("SELECT count(*) FROM vectors_fts")
    assert fts_count == v_count, "rebuilt FTS index must mirror the vectors table"

    # A row inserted AFTER the migration rebuild must still land in the index
    # via the vectors_ai trigger (DROP+CREATE by the same name keeps it valid),
    # and be findable by an inflected query term.
    assert await _match(db, "connected") == ["connection established quickly"]
    assert await _match(db, "gallop") == ["horses galloped across the field"]
