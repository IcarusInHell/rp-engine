"""Pydantic models for situational trigger management."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class TriggerCondition(BaseModel):
    """One trigger condition — an expression, tracked-state check, or signal threshold."""
    type: Literal["expression", "state", "signal"]
    expr: str | None = None
    path: str | None = None
    operator: str | None = None
    value: Any | None = None
    values: list[Any] | None = None
    signal: str | None = None


class TriggerCreate(BaseModel):
    """Request to create a situational trigger — conditions, match mode, and what to inject."""
    name: str
    description: str | None = None
    rp_folder: str
    inject_type: Literal["context_note", "card_reference", "state_alert"]
    inject_content: str | None = None
    inject_card_path: str | None = None
    conditions: list[TriggerCondition]
    match_mode: Literal["any", "all"] = "any"
    priority: int = 0
    cooldown_turns: int = 0


class TriggerUpdate(BaseModel):
    """Partial update to a trigger (all fields optional, including enabled)."""
    name: str | None = None
    description: str | None = None
    inject_type: Literal["context_note", "card_reference", "state_alert"] | None = None
    inject_content: str | None = None
    inject_card_path: str | None = None
    conditions: list[TriggerCondition] | None = None
    match_mode: Literal["any", "all"] | None = None
    priority: int | None = None
    cooldown_turns: int | None = None
    enabled: bool | None = None


class TriggerResponse(BaseModel):
    """A stored trigger with its full definition and fire state."""
    id: str
    name: str
    description: str | None = None
    rp_folder: str
    inject_type: str
    inject_content: str | None = None
    inject_card_path: str | None = None
    conditions: list[TriggerCondition] = []
    match_mode: str = "any"
    priority: int = 0
    cooldown_turns: int = 0
    last_fired_turn: int | None = None
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class TriggerTestRequest(BaseModel):
    """Request to test a trigger against sample text."""
    trigger_id: str
    sample_text: str


class ConditionResult(BaseModel):
    """Per-condition evaluation result in a trigger test."""
    condition_index: int
    condition_type: str
    matched: bool
    detail: str


class TriggerTestResult(BaseModel):
    """Result of a trigger test — would-fire verdict, per-condition results, and signal scores."""
    would_fire: bool
    conditions_evaluated: list[ConditionResult] = []
    signals: dict[str, float] = {}
