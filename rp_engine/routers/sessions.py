"""Session management endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from rp_engine.database import PRIORITY_EXCHANGE, Database
from rp_engine.dependencies import (
    get_auto_save_manager,
    get_branch_manager,
    get_db,
    get_diagnostic_logger,
    get_recap_builder,
    get_summary_builder,
)
from rp_engine.models.session import (
    NarratorNoteBody,
    NewEntity,
    PlotThreadStatus,
    Recap,
    SceneProgression,
    SessionCreate,
    SessionEndResponse,
    SessionEndSummary,
    SessionResponse,
    SessionSummary,
    SessionTimelineEntry,
    SessionTimelineResponse,
    TrustChange,
    UpdateSessionBody,
)
from rp_engine.services.auto_save import AutoSaveManager
from rp_engine.services.branch_manager import BranchManager
from rp_engine.services.diagnostic_logger import DiagnosticLogger
from rp_engine.services.recap_builder import RecapBuilder
from rp_engine.services.summary_builder import SummaryBuilder
from rp_engine.utils.json_helpers import safe_parse_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_from_row(row: dict) -> SessionResponse:
    metadata = safe_parse_json(row.get("metadata")) or None
    return SessionResponse(
        id=row["id"],
        rp_folder=row["rp_folder"],
        branch=row["branch"],
        started_at=row["started_at"],
        ended_at=row.get("ended_at"),
        metadata=metadata,
        narrator_note=row.get("narrator_note"),
        narrator_note_depth=row.get("narrator_note_depth") or 2,
    )


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreate,
    db: Database = Depends(get_db),
    branch_manager: BranchManager = Depends(get_branch_manager),
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
):
    """Start a new RP session."""
    # Auto-create "main" branch if needed
    if branch_manager:
        await branch_manager.ensure_main_branch(body.rp_folder)

    session_id = uuid4().hex[:12]
    now = datetime.now(UTC).isoformat()

    future = await db.enqueue_write(
        """INSERT INTO sessions (id, rp_folder, branch, started_at)
           VALUES (?, ?, ?, ?)""",
        [session_id, body.rp_folder, body.branch, now],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    if diag:
        diag.log(
            category="session",
            event="session_started",
            data={
                "session_id": session_id,
                "rp_folder": body.rp_folder,
                "branch": body.branch,
            },
        )

    return SessionResponse(
        id=session_id,
        rp_folder=body.rp_folder,
        branch=body.branch,
        started_at=now,
    )


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    rp_folder: str | None = None,
    branch: str | None = None,
    db: Database = Depends(get_db),
):
    """List sessions, optionally filtered by rp_folder and/or branch."""
    query = "SELECT * FROM sessions"
    conditions = []
    params_list: list = []
    if rp_folder:
        conditions.append("rp_folder = ?")
        params_list.append(rp_folder)
    if branch:
        conditions.append("branch = ?")
        params_list.append(branch)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC"
    rows = await db.fetch_all(query, params_list)
    return [_session_from_row(r) for r in rows]


@router.get("/active", response_model=SessionResponse)
async def get_active_session(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Get the most recent active (un-ended) session for a given RP/branch."""
    row = await db.fetch_one(
        """SELECT * FROM sessions WHERE ended_at IS NULL
           AND rp_folder = ? AND branch = ?
           ORDER BY started_at DESC LIMIT 1""",
        [rp_folder, branch],
    )
    if not row:
        raise HTTPException(404, detail=f"No active session for {rp_folder}/{branch}")
    return _session_from_row(row)


@router.put("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    body: UpdateSessionBody,
    db: Database = Depends(get_db),
):
    """Update session metadata."""
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")

    future = await db.enqueue_write(
        "UPDATE sessions SET metadata = ? WHERE id = ?",
        [json.dumps(body.metadata), session_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    updated = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    return _session_from_row(updated)


@router.put("/{session_id}/narrator-note", response_model=SessionResponse)
async def set_narrator_note(
    session_id: str,
    body: NarratorNoteBody,
    db: Database = Depends(get_db),
):
    """Set/replace the session's narrator note (session-persistent GM steering).

    Injected as a system message at ``depth`` near recent history, independent of
    the global ``prompt.injection`` toggle. ``depth`` is clamped to >= 1 (depth 0
    would never be emitted into the message list).
    """
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")

    depth = max(1, body.depth)
    future = await db.enqueue_write(
        "UPDATE sessions SET narrator_note = ?, narrator_note_depth = ? WHERE id = ?",
        [body.note, depth, session_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    updated = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    return _session_from_row(updated)


@router.delete("/{session_id}/narrator-note", response_model=SessionResponse)
async def clear_narrator_note(
    session_id: str,
    db: Database = Depends(get_db),
):
    """Clear the session's narrator note."""
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")

    future = await db.enqueue_write(
        "UPDATE sessions SET narrator_note = NULL WHERE id = ?",
        [session_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    updated = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    return _session_from_row(updated)


@router.post("/{session_id}/end", response_model=SessionEndResponse)
async def end_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
    summary_builder: SummaryBuilder = Depends(get_summary_builder),
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
    auto_save_manager: AutoSaveManager | None = Depends(get_auto_save_manager),
):
    """End a session. Returns accumulated summary data."""
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    if row["ended_at"]:
        raise HTTPException(400, detail="Session already ended")

    now = datetime.now(UTC).isoformat()
    future = await db.enqueue_write(
        "UPDATE sessions SET ended_at = ? WHERE id = ?",
        [now, session_id],
        priority=PRIORITY_EXCHANGE,
    )
    await future

    # Gather summary data — parallel batch instead of 8+ sequential queries
    rp_folder = row["rp_folder"]
    branch = row["branch"]

    (event_rows, trust_rows, gap_rows, first_exchange_num,
     thread_rows, first_exchange, last_exchange, location_rows) = await asyncio.gather(
        db.fetch_all(
            """SELECT e.event FROM events e
               JOIN exchanges ex ON e.exchange_id = ex.id
               WHERE ex.session_id = ?
               ORDER BY e.created_at""",
            [session_id],
        ),
        db.fetch_all(
            """SELECT tm.change, tm.reason,
                      tm.character_a, tm.character_b
               FROM trust_modifications tm
               JOIN exchanges ex ON tm.exchange_id = ex.id
               WHERE ex.session_id = ?
               ORDER BY tm.created_at""",
            [session_id],
        ),
        db.fetch_all(
            """SELECT cg.entity_name, cg.suggested_type, cg.seen_count,
                      (SELECT MIN(ex.exchange_number) FROM exchanges ex
                       WHERE ex.rp_folder = cg.rp_folder AND ex.branch = cg.branch
                         AND ex.created_at >= cg.first_seen) as first_exchange
               FROM card_gaps cg WHERE cg.rp_folder = ? AND cg.branch = ?""",
            [rp_folder, branch],
        ),
        db.fetch_val(
            "SELECT MIN(exchange_number) FROM exchanges WHERE session_id = ?",
            [session_id],
        ),
        db.fetch_all(
            """SELECT tc.thread_id, pt.name, tc.current_counter
               FROM thread_counters tc
               JOIN plot_threads pt ON tc.thread_id = pt.id AND tc.rp_folder = pt.rp_folder
               WHERE tc.rp_folder = ? AND tc.branch = ?""",
            [rp_folder, branch],
        ),
        db.fetch_one(
            """SELECT in_story_timestamp, location FROM exchanges
               WHERE session_id = ? ORDER BY exchange_number ASC LIMIT 1""",
            [session_id],
        ),
        db.fetch_one(
            """SELECT in_story_timestamp, location FROM exchanges
               WHERE session_id = ? ORDER BY exchange_number DESC LIMIT 1""",
            [session_id],
        ),
        db.fetch_all(
            """SELECT DISTINCT location FROM exchanges
               WHERE session_id = ? AND location IS NOT NULL""",
            [session_id],
        ),
    )

    events = [r["event"] for r in event_rows]
    trust_changes = [
        TrustChange(
            npc=r["character_b"],
            delta=r["change"],
            reason=r["reason"] or "",
        )
        for r in trust_rows
    ]

    session_resp = SessionResponse(
        id=row["id"],
        rp_folder=row["rp_folder"],
        branch=row["branch"],
        started_at=row["started_at"],
        ended_at=now,
    )

    new_entities = [
        NewEntity(
            name=g["entity_name"],
            type=g.get("suggested_type") or "unknown",
            first_mention_exchange=g.get("first_exchange"),
        )
        for g in gap_rows
    ]

    # Plot thread status — batch start_counter lookup
    start_counter_map: dict[int, int] = {}
    if thread_rows and first_exchange_num:
        thread_ids = [t["thread_id"] for t in thread_rows]
        tid_placeholders = ",".join("?" for _ in thread_ids)
        start_rows = await db.fetch_all(
            f"""SELECT thread_id, counter_value
                FROM thread_counter_entries tce
                WHERE thread_id IN ({tid_placeholders})
                  AND rp_folder = ? AND branch = ?
                  AND exchange_number < ?
                  AND exchange_number = (
                      SELECT MAX(exchange_number) FROM thread_counter_entries
                      WHERE thread_id = tce.thread_id AND rp_folder = tce.rp_folder
                        AND branch = tce.branch AND exchange_number < ?
                  )""",
            thread_ids + [rp_folder, branch, first_exchange_num, first_exchange_num],
        )
        for row in start_rows:
            start_counter_map[row["thread_id"]] = row["counter_value"]

    plot_thread_status = [
        PlotThreadStatus(
            thread_id=t["thread_id"],
            name=t["name"],
            start_counter=start_counter_map.get(t["thread_id"], 0),
            end_counter=t["current_counter"],
        )
        for t in thread_rows
    ]
    scene_progression = SceneProgression(
        first_timestamp=first_exchange.get("in_story_timestamp") if first_exchange else None,
        last_timestamp=last_exchange.get("in_story_timestamp") if last_exchange else None,
        locations_visited=[r["location"] for r in location_rows],
    )

    # Cards are read-only in CoW system — trust lives only in DB.
    # No writeback to card files.

    summary = SessionEndSummary(
        significant_events=events,
        trust_changes=trust_changes,
        new_entities=new_entities,
        plot_thread_status=plot_thread_status,
        scene_progression=scene_progression,
    )

    # Fire background task to generate narrative summary
    if summary_builder:
        background_tasks.add_task(
            summary_builder.build_session_summary,
            session_id,
            rp_folder,
            branch,
        )

    if diag:
        diag.log(
            category="session",
            event="session_ended",
            data={
                "session_id": session_id,
                "rp_folder": rp_folder,
                "branch": branch,
                "started_at": row["started_at"],
                "ended_at": now,
                "event_count": len(events),
                "trust_change_count": len(trust_changes),
                "new_entity_count": len(new_entities),
            },
        )
        # Auto-report on session end if configured
        if diag.config.auto_report.enabled and diag.config.auto_report.on_session_end:
            _report_task = asyncio.create_task(diag.send_report())  # noqa: RUF006

    # Clean up auto-save tracker for this session
    if auto_save_manager:
        auto_save_manager.cleanup_session(session_id)

    return SessionEndResponse(session=session_resp, summary=summary)


@router.get("/{session_id}/timeline", response_model=SessionTimelineResponse)
async def get_session_timeline(
    session_id: str,
    db: Database = Depends(get_db),
):
    """Get a unified timeline of all state changes during a session."""
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")

    rp_folder = row["rp_folder"]
    branch = row["branch"]

    # Get exchange range for this session
    range_row = await db.fetch_one(
        "SELECT MIN(exchange_number) as min_ex, MAX(exchange_number) as max_ex "
        "FROM exchanges WHERE session_id = ?",
        [session_id],
    )
    min_ex = range_row["min_ex"] if range_row and range_row["min_ex"] is not None else 0
    max_ex = range_row["max_ex"] if range_row and range_row["max_ex"] is not None else 0

    if min_ex == 0 and max_ex == 0:
        return SessionTimelineResponse(
            session_id=session_id,
            branch=branch,
            exchange_range=(0, 0),
            entries=[],
            entry_counts={},
        )

    # Query all 6 tables in parallel, filtered by rp_folder + branch + exchange range
    (event_rows, trust_rows, thread_rows, char_rows, scene_rows, warning_rows) = await asyncio.gather(
        db.fetch_all(
            """SELECT e.event, e.characters, e.significance, e.in_story_timestamp, e.created_at,
                      ex.exchange_number
               FROM events e
               JOIN exchanges ex ON e.exchange_id = ex.id
               WHERE ex.session_id = ?
               ORDER BY ex.exchange_number""",
            [session_id],
        ),
        db.fetch_all(
            """SELECT tm.character_a, tm.character_b, tm.change, tm.reason,
                      tm.created_at, tm.exchange_number
               FROM trust_modifications tm
               WHERE tm.rp_folder = ? AND tm.branch = ?
                 AND tm.exchange_number BETWEEN ? AND ?
               ORDER BY tm.exchange_number""",
            [rp_folder, branch, min_ex, max_ex],
        ),
        db.fetch_all(
            """SELECT tce.thread_id, tce.exchange_number, tce.counter_value, tce.created_at,
                      pt.name as thread_name
               FROM thread_counter_entries tce
               JOIN plot_threads pt ON tce.thread_id = pt.id AND tce.rp_folder = pt.rp_folder
               WHERE tce.rp_folder = ? AND tce.branch = ?
                 AND tce.exchange_number BETWEEN ? AND ?
               ORDER BY tce.exchange_number""",
            [rp_folder, branch, min_ex, max_ex],
        ),
        db.fetch_all(
            """SELECT cse.card_id, cse.exchange_number, cse.location, cse.emotional_state,
                      cse.conditions, cse.created_at, sc.name as character_name
               FROM character_state_entries cse
               JOIN story_cards sc ON cse.card_id = sc.id
               WHERE cse.rp_folder = ? AND cse.branch = ?
                 AND cse.exchange_number BETWEEN ? AND ?
               ORDER BY cse.exchange_number""",
            [rp_folder, branch, min_ex, max_ex],
        ),
        db.fetch_all(
            """SELECT exchange_number, location, time_of_day, mood,
                      in_story_timestamp, created_at
               FROM scene_state_entries
               WHERE rp_folder = ? AND branch = ?
                 AND exchange_number BETWEEN ? AND ?
               ORDER BY exchange_number""",
            [rp_folder, branch, min_ex, max_ex],
        ),
        db.fetch_all(
            """SELECT entity_name, category, current_claim, current_exchange,
                      past_claim, past_exchange, severity, explanation, created_at
               FROM continuity_warnings
               WHERE rp_folder = ? AND branch = ?
                 AND current_exchange BETWEEN ? AND ?
               ORDER BY current_exchange""",
            [rp_folder, branch, min_ex, max_ex],
        ),
    )

    entries: list[SessionTimelineEntry] = []

    # Events
    for r in event_rows:
        chars = safe_parse_json(r.get("characters")) or []
        if isinstance(chars, str):
            chars = [chars]
        entries.append(SessionTimelineEntry(
            type="event",
            exchange_number=r["exchange_number"],
            timestamp=r.get("created_at"),
            title=r["event"],
            detail={"significance": r.get("significance")},
            characters=chars,
        ))

    # Trust changes
    for r in trust_rows:
        sign = "+" if r["change"] >= 0 else ""
        entries.append(SessionTimelineEntry(
            type="trust_change",
            exchange_number=r["exchange_number"],
            timestamp=r.get("created_at"),
            title=f"{r['character_a']} → {r['character_b']}: {sign}{r['change']}",
            detail={"reason": r.get("reason", ""), "change": r["change"]},
            characters=[r["character_a"], r["character_b"]],
        ))

    # Thread updates
    for r in thread_rows:
        entries.append(SessionTimelineEntry(
            type="thread_update",
            exchange_number=r["exchange_number"],
            timestamp=r.get("created_at"),
            title=f"{r['thread_name']}: counter → {r['counter_value']}",
            detail={"thread_id": r["thread_id"], "counter_value": r["counter_value"]},
        ))

    # Character state updates
    for r in char_rows:
        name = r.get("character_name", r["card_id"])
        parts = []
        if r.get("location"):
            parts.append(f"location: {r['location']}")
        if r.get("emotional_state"):
            parts.append(f"mood: {r['emotional_state']}")
        title = f"{name} — {', '.join(parts)}" if parts else f"{name} state updated"
        entries.append(SessionTimelineEntry(
            type="character_update",
            exchange_number=r["exchange_number"],
            timestamp=r.get("created_at"),
            title=title,
            detail={
                "location": r.get("location"),
                "emotional_state": r.get("emotional_state"),
                "conditions": safe_parse_json(r.get("conditions")) or [],
            },
            characters=[name],
        ))

    # Scene changes
    for r in scene_rows:
        parts = []
        if r.get("location"):
            parts.append(r["location"])
        if r.get("time_of_day"):
            parts.append(r["time_of_day"])
        if r.get("mood"):
            parts.append(r["mood"])
        entries.append(SessionTimelineEntry(
            type="scene_change",
            exchange_number=r["exchange_number"],
            timestamp=r.get("created_at"),
            title=" · ".join(parts) if parts else "Scene updated",
            detail={
                "location": r.get("location"),
                "time_of_day": r.get("time_of_day"),
                "mood": r.get("mood"),
                "in_story_timestamp": r.get("in_story_timestamp"),
            },
        ))

    # Continuity warnings
    for r in warning_rows:
        entries.append(SessionTimelineEntry(
            type="continuity_warning",
            exchange_number=r["current_exchange"],
            timestamp=r.get("created_at"),
            title=f"{r['entity_name']}: {r['category']}",
            detail={
                "current_claim": r["current_claim"],
                "past_claim": r["past_claim"],
                "past_exchange": r["past_exchange"],
                "severity": r.get("severity"),
                "explanation": r.get("explanation"),
            },
            characters=[r["entity_name"]],
        ))

    # Sort by exchange_number then timestamp
    entries.sort(key=lambda e: (e.exchange_number or 0, e.timestamp or ""))

    # Count entries per type
    entry_counts: dict[str, int] = {}
    for e in entries:
        entry_counts[e.type] = entry_counts.get(e.type, 0) + 1

    return SessionTimelineResponse(
        session_id=session_id,
        branch=branch,
        exchange_range=(min_ex, max_ex),
        entries=entries,
        entry_counts=entry_counts,
    )


@router.get("/{session_id}/summary", response_model=SessionSummary)
async def get_session_summary(
    session_id: str,
    db: Database = Depends(get_db),
    summary_builder: SummaryBuilder = Depends(get_summary_builder),
):
    """Get the narrative summary for a session."""
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")

    summary = await summary_builder.get_summary(session_id)
    if not summary:
        raise HTTPException(404, detail="Summary not yet generated. It may still be processing.")
    return summary


@router.post("/{session_id}/summary", response_model=SessionSummary)
async def regenerate_session_summary(
    session_id: str,
    db: Database = Depends(get_db),
    summary_builder: SummaryBuilder = Depends(get_summary_builder),
):
    """Force regenerate a session summary."""
    row = await db.fetch_one("SELECT * FROM sessions WHERE id = ?", [session_id])
    if not row:
        raise HTTPException(404, detail=f"Session {session_id} not found")

    return await summary_builder.build_session_summary(
        session_id=session_id,
        rp_folder=row["rp_folder"],
        branch=row["branch"],
    )


@router.get("/recap", response_model=Recap)
async def get_session_recap(
    rp_folder: str,
    branch: str = "main",
    style: str = "standard",
    recap_builder: RecapBuilder = Depends(get_recap_builder),
):
    """Generate or retrieve a "Previously on..." recap for the current story state."""
    if style not in ("quick", "standard", "detailed"):
        raise HTTPException(400, detail="Style must be 'quick', 'standard', or 'detailed'")

    return await recap_builder.generate_recap(
        rp_folder=rp_folder,
        branch=branch,
        style=style,
    )
