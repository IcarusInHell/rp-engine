"""Pydantic models for session management."""

from __future__ import annotations

from pydantic import BaseModel


class SessionCreate(BaseModel):
    rp_folder: str
    branch: str = "main"


class SessionResponse(BaseModel):
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
    npc: str
    delta: int
    reason: str


class NewEntity(BaseModel):
    name: str
    type: str
    first_mention_exchange: int | None = None


class SceneProgression(BaseModel):
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    locations_visited: list[str] = []


class PlotThreadStatus(BaseModel):
    thread_id: str
    name: str
    start_counter: int = 0
    end_counter: int = 0


class SessionEndSummary(BaseModel):
    significant_events: list[str] = []
    trust_changes: list[TrustChange] = []
    new_entities: list[NewEntity] = []
    scene_progression: SceneProgression | None = None
    plot_thread_status: list[PlotThreadStatus] = []


class SessionTimelineEntry(BaseModel):
    type: str  # "trust_change" | "event" | "thread_update" | "character_update" | "scene_change" | "continuity_warning"
    exchange_number: int | None = None
    timestamp: str | None = None
    title: str
    detail: dict = {}
    characters: list[str] = []


class SessionTimelineResponse(BaseModel):
    session_id: str
    branch: str
    exchange_range: tuple[int, int]
    entries: list[SessionTimelineEntry]
    entry_counts: dict[str, int] = {}


class UpdateSessionBody(BaseModel):
    metadata: dict


class SessionEndResponse(BaseModel):
    session: SessionResponse
    summary: SessionEndSummary


class SessionSummary(BaseModel):
    session_id: str
    rp_folder: str
    branch: str
    narrative_summary: str
    key_moments: list[dict] = []
    generated_at: str


class Recap(BaseModel):
    rp_folder: str
    branch: str
    session_id: str | None = None
    style: str = "standard"
    recap_text: str
    generated_at: str
