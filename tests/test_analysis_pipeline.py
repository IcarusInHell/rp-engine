"""Service: AnalysisPipeline._process_exchange — counting + memory persistence.

The pipeline's signature silent-drop is a *miscount*: extracted memories are
tallied into ``events_added`` (analysis_pipeline.py:327) and ``AnalysisResult``
has no ``memories_added`` field at all. We:

* LOCK the real persistence side (current, correct): N memories in the analysis
  result land as exactly N rows in ``extracted_memories`` — a loud exact-count
  guard against a swallowed insert.
* xfail the corrected COUNT contract (Phase 4): memories tally in
  ``memories_added``, significant events in ``events_added`` — they must not be
  conflated. Strict xfail flips to a hard pass once Phase 4 adds the field.

The ResponseAnalyzer is monkeypatched to return a constructed AnalysisLLMResult,
isolating the pipeline's aggregation logic from LLM/JSON parsing. (Confirmed:
neither thread_tracker nor timestamp_tracker calls the LLM, so with ``analyze``
stubbed ``_process_exchange`` makes zero model calls — fully deterministic.)
"""

from __future__ import annotations

from rp_engine.models.analysis import (
    AnalysisLLMResult,
    MemoryExtracted,
    NewCharacterExtracted,
    NewConceptExtracted,
    NewEntitiesExtracted,
    NewLocationExtracted,
    RelationshipDynamic,
    SignificantEventExtracted,
    StoryStateExtracted,
)
from rp_engine.utils.trust import fetch_trust_pair
from tests.assertions import assert_count_exact

N_MEMORIES = 2
M_EVENTS = 1  # deliberately != N so a conflated counter is detectable


def _canned_analysis() -> AnalysisLLMResult:
    """N memories + M significant events; nothing else (no trust/entity churn)."""
    return AnalysisLLMResult(
        memories=[
            MemoryExtracted(
                description=f"Alice remembers detail {i}",
                significance="high",
                characters=["Alice"],
            )
            for i in range(N_MEMORIES)
        ],
        story_state=StoryStateExtracted(
            significant_events=[
                SignificantEventExtracted(
                    event=f"A notable thing {j} happened",
                    characters=["Alice"],
                    significance="medium",
                )
                for j in range(M_EVENTS)
            ],
        ),
    )


async def _first_exchange_id(seeded_rp) -> int:
    eid = await seeded_rp.db.fetch_val(
        """SELECT id FROM exchanges
           WHERE rp_folder = ? AND branch = ? AND exchange_number = 1""",
        [seeded_rp.rp_folder, seeded_rp.main_branch],
    )
    assert eid is not None, "seeded RP must have exchange #1 on main"
    return eid


def _stub_analyze(monkeypatch, seeded_rp, result: AnalysisLLMResult) -> None:
    async def fake_analyze(*args, **kwargs):
        return result

    monkeypatch.setattr(
        seeded_rp.container.response_analyzer, "analyze", fake_analyze
    )


async def test_extracted_memories_persist_exactly_once(seeded_rp, monkeypatch):
    """N analyzed memories → exactly N extracted_memories rows (no swallowed insert)."""
    _stub_analyze(monkeypatch, seeded_rp, _canned_analysis())
    exchange_id = await _first_exchange_id(seeded_rp)

    await seeded_rp.container.analysis_pipeline._process_exchange(
        exchange_id, seeded_rp.rp_folder, seeded_rp.main_branch
    )

    rows = await seeded_rp.db.fetch_val(
        "SELECT COUNT(*) FROM extracted_memories WHERE exchange_id = ?", [exchange_id]
    )
    assert_count_exact(rows, N_MEMORIES, label="extracted_memories persisted")


async def test_process_exchange_returns_completed(seeded_rp, monkeypatch):
    """Behavior lock for the refactor target: the pipeline reports completion."""
    _stub_analyze(monkeypatch, seeded_rp, _canned_analysis())
    exchange_id = await _first_exchange_id(seeded_rp)

    result = await seeded_rp.container.analysis_pipeline._process_exchange(
        exchange_id, seeded_rp.rp_folder, seeded_rp.main_branch
    )

    assert result.status == "completed"
    assert result.exchange_id == exchange_id


async def test_relationship_dynamics_write_is_directional(seeded_rp, monkeypatch):
    """Phase 2 write-direction pin (the dominant, LLM-driven trust path).

    The analyzer emits ``relationshipDynamics.characters`` as ``[truster, trusted]``
    (pinned in the response_analyzer prompt). ``analysis_pipeline`` forwards that pair
    *in order* to ``update_trust``, which stores it un-normalized. Now that trust reads
    are directional, a flipped forward would record the change against the WRONG
    direction — silently, since no other test exercises the analysis write path.

    Canned dynamic: Alice → Bob trust_increase. The +change must land on Alice→Bob's
    modification sum and leave Bob→Alice at zero modifications.
    """
    analysis = AnalysisLLMResult(
        relationship_dynamics=[
            RelationshipDynamic(
                characters=["Alice", "Bob"],  # [truster, trusted]
                changeType="trust_increase",
                evidence="Bob took a blade meant for Alice",
            )
        ],
    )
    _stub_analyze(monkeypatch, seeded_rp, analysis)
    exchange_id = await _first_exchange_id(seeded_rp)
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    await seeded_rp.container.analysis_pipeline._process_exchange(
        exchange_id, rp, branch
    )

    _, ab_mod = await fetch_trust_pair(seeded_rp.db, rp, branch, "Alice", "Bob")
    _, ba_mod = await fetch_trust_pair(seeded_rp.db, rp, branch, "Bob", "Alice")
    assert ab_mod > 0, "Alice→Bob (truster→trusted) must record the trust_increase"
    assert ba_mod == 0, (
        "DIRECTION FLIPPED: the Alice→Bob increase leaked into Bob→Alice "
        f"(Bob→Alice mod_sum={ba_mod}, expected 0)"
    )


async def test_memories_counted_separately_from_events(seeded_rp, monkeypatch):
    """The corrected count contract (Phase 4): memories and events have separate counters.

    Before Phase 4 ``AnalysisResult`` had no ``memories_added`` and the N memories
    were mis-added to ``events_added`` (so events would read M+N). Now memories
    increment ``memories_added`` and events increment ``events_added`` independently.
    """
    _stub_analyze(monkeypatch, seeded_rp, _canned_analysis())
    exchange_id = await _first_exchange_id(seeded_rp)

    result = await seeded_rp.container.analysis_pipeline._process_exchange(
        exchange_id, seeded_rp.rp_folder, seeded_rp.main_branch
    )

    assert result.memories_added == N_MEMORIES, "memories must have their own counter"
    assert result.events_added == M_EVENTS, "events must not absorb the memory count"


async def test_new_entities_tracked_with_correct_gap_types(seeded_rp, monkeypatch):
    """Step-7 dedup loop + ``_apply_relationship_dynamics`` decrease/else branches.

    The 3-loops→typed-loop refactor and ``_track_new_entity`` have no other coverage
    (no test populates ``new_entities``). The silent-drop trap is the type mapping:
    a concept must record as ``"lore"``, NOT ``"concept"``. We also exercise the two
    ``_apply_relationship_dynamics`` branches the directional test misses: a
    ``trust_decrease`` (counts as a trust change) and a non-trust ``change_type``
    (falls through to ``add_event`` → ``events_added``).
    """
    analysis = AnalysisLLMResult(
        new_entities=NewEntitiesExtracted(
            characters=[NewCharacterExtracted(name="Zephyr")],
            locations=[NewLocationExtracted(name="The Sunken Vault")],
            concepts=[NewConceptExtracted(name="The Old Pact")],
        ),
        relationship_dynamics=[
            RelationshipDynamic(
                characters=["Alice", "Bob"],
                changeType="trust_decrease",
                evidence="Bob lied to Alice",
            ),
            RelationshipDynamic(
                characters=["Alice", "Carol"],
                changeType="conflict_introduced",
                evidence="A rift opens between them",
            ),
        ],
    )
    _stub_analyze(monkeypatch, seeded_rp, analysis)
    exchange_id = await _first_exchange_id(seeded_rp)
    rp, branch = seeded_rp.rp_folder, seeded_rp.main_branch

    result = await seeded_rp.container.analysis_pipeline._process_exchange(
        exchange_id, rp, branch
    )

    # All three new entities recorded as gaps, with the correct suggested_type.
    assert_count_exact(result.card_gaps_added, 3, label="card_gaps tracked")
    rows = await seeded_rp.db.fetch_all(
        "SELECT entity_name, suggested_type FROM card_gaps WHERE rp_folder = ?", [rp]
    )
    gap_types = {r["entity_name"]: r["suggested_type"] for r in rows}
    assert gap_types.get("Zephyr") == "character"
    assert gap_types.get("The Sunken Vault") == "location"
    assert gap_types.get("The Old Pact") == "lore", (
        "SILENT-DROP TRAP: a concept must record as 'lore', not 'concept' — "
        f"got {gap_types.get('The Old Pact')!r}"
    )

    # _apply_relationship_dynamics: decrease → trust change; non-trust → event.
    assert_count_exact(result.trust_changes, 1, label="trust_decrease counted")
    assert_count_exact(result.events_added, 1, label="non-trust dynamic → event")
    assert_count_exact(result.memories_added, 0, label="no memories in this result")
