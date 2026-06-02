"""Pydantic models for NPC reactions, trust, and listing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from rp_engine.models.enums import Archetype, BehavioralModifier


class TrustShift(BaseModel):
    """A trust delta from an NPC reaction — direction, amount, and reason."""
    direction: Literal["increase", "decrease", "neutral"]
    amount: int = 0
    reason: str | None = None


class NPCReaction(BaseModel):
    """A single NPC's reaction — monologue, action, dialogue, undercurrent, and trust shift."""
    character: str
    internalMonologue: str
    physicalAction: str
    dialogue: str | None = None
    emotionalUndercurrent: str
    trustShift: TrustShift


class NPCReactRequest(BaseModel):
    """Request to generate one NPC's reaction to a scene."""
    npc_name: str
    scene_prompt: str
    pov_character: str | None = None
    model_override: str | None = None


class NPCBatchRequest(BaseModel):
    """Request to generate reactions for several NPCs to the same scene."""
    npc_names: list[str]
    scene_prompt: str
    pov_character: str | None = None


class TrustEvent(BaseModel):
    """One dated entry in an NPC's trust history."""
    date: str
    change: int
    direction: str
    reason: str | None = None


class TrustInfo(BaseModel):
    """An NPC's directional trust toward a target — score, stage, session deltas, and history."""
    npc_name: str
    target: str
    trust_score: int
    trust_stage: str
    session_gains: int
    session_losses: int
    history: list[TrustEvent]


class NPCListItem(BaseModel):
    """Summary row for an NPC in a listing — archetypes, modifiers, trust, and current state."""
    name: str
    importance: str | None = None
    primary_archetype: Archetype | None = None
    secondary_archetype: Archetype | None = None
    behavioral_modifiers: list[BehavioralModifier] = []
    trust_score: int = 0
    trust_stage: str = "neutral"
    location: str | None = None
    emotional_state: str | None = None
