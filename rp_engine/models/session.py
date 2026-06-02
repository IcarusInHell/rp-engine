"""Pydantic models for session management."""

from __future__ import annotations

from pydantic import BaseModel


class SessionCreate(BaseModel):
    """Request to start a session on a branch."""
    rp_folder: str
    branch: str = "main"


class SessionResponse(BaseModel):
    """A session's metadata — timing, branch, and narrator note with its injection depth."""
    id: str
    rp_folder: str
    branch: str
    started_at: str
    ended_at: str | None = None
    metadata: dict | None = None
    narrator_note: str | None = None
    narrator_note_depth: int = 2


class NarratorNoteBody(BaseModel):
    """Set/replace the session's narrator note (Phase 5a).

    ``depth`` is the injection depth (>= 1 — depth 0 would never be emitted into
    the message list); the note injects independent of the global
    ``prompt.injection`` toggle.
    """
    note: str
    depth: int = 2


class TrustChange(BaseModel):
    """An NPC trust delta recorded in a session-end summary."""
    npc: str
    delta: int
    reason: str


class NewEntity(BaseModel):
    """A newly-mentioned entity detected during a session."""
    name: str
    type: str
    first_mention_exchange: int | None = None


class SceneProgression(BaseModel):
    """Scene movement over a session — timestamp span and locations visited."""
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    locations_visited: list[str] = []


class PlotThreadStatus(BaseModel):
    """A plot thread's counter movement over a session."""
    thread_id: str
    name: str
    start_counter: int = 0
    end_counter: int = 0


class SessionEndSummary(BaseModel):
    """End-of-session rollup — events, trust changes, new entities, scene, and thread status."""
    significant_events: list[str] = []
    trust_changes: list[TrustChange] = []
    new_entities: list[NewEntity] = []
    scene_progression: SceneProgression | None = None
    plot_thread_status: list[PlotThreadStatus] = []


class SessionTimelineEntry(BaseModel):
    """One dated entry in a session timeline — typed event with detail and characters."""
    type: str  # "trust_change" | "event" | "thread_update" | "character_update" | "scene_change" | "continuity_warning"
    exchange_number: int | None = None
    timestamp: str | None = None
    title: str
    detail: dict = {}
    characters: list[str] = []


class SessionTimelineResponse(BaseModel):
    """A session's full timeline — entries plus per-type counts."""
    session_id: str
    branch: str
    exchange_range: tuple[int, int]
    entries: list[SessionTimelineEntry]
    entry_counts: dict[str, int] = {}


class UpdateSessionBody(BaseModel):
    """Request to update a session's metadata."""
    metadata: dict


class SessionEndResponse(BaseModel):
    """Response to ending a session — the session plus its end summary."""
    session: SessionResponse
    summary: SessionEndSummary


class SessionSummary(BaseModel):
    """An LLM-generated narrative summary of a session with key moments."""
    session_id: str
    rp_folder: str
    branch: str
    narrative_summary: str
    key_moments: list[dict] = []
    generated_at: str


class Recap(BaseModel):
    """A generated recap of an RP/session in a given style."""
    rp_folder: str
    branch: str
    session_id: str | None = None
    style: str = "standard"
    recap_text: str
    generated_at: str
