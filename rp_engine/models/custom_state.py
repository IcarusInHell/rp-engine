"""Custom state tracking models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CustomStateSchema(BaseModel):
    """Definition of a custom state field — data type, scope (scene vs per-character), and injection format."""
    id: str
    rp_folder: str | None = None
    category: str
    name: str
    data_type: str  # "number", "text", "list", "object"
    config: dict | None = None
    belongs_to: str | None = None  # NULL = scene-level, "character" = per-character
    inject_as: str = "hidden"  # "stat_block", "inventory_list", "note", "hidden"
    display_order: int = 0


class CustomStateSchemaCreate(BaseModel):
    """Create payload for a custom state schema — rp_folder is required here."""
    id: str
    rp_folder: str
    category: str
    name: str
    data_type: str
    config: dict | None = None
    belongs_to: str | None = None
    inject_as: str = "hidden"
    display_order: int = 0


class CustomStateValue(BaseModel):
    """A recorded value for a custom state field, with provenance (who/when/why)."""
    schema_id: str
    entity_id: str | None = None
    value: str | dict | list | int | float | None = None
    exchange_number: int | None = None
    changed_by: str | None = None
    reason: str | None = None


class CustomStateSet(BaseModel):
    """Request to set a custom state value on a branch."""
    schema_id: str
    rp_folder: str
    branch: str = "main"
    entity_id: str | None = None
    value: str | dict | list | int | float
    reason: str | None = None


class PresetInfo(BaseModel):
    """Summary of a custom-state preset — name, description, and schema count."""
    name: str
    description: str | None = None
    schema_count: int = 0


class CustomStateSnapshot(BaseModel):
    """All current custom state values for an RP."""
    schemas: list[CustomStateSchema] = []
    values: list[CustomStateValue] = []


class PCCustomStateItem(BaseModel):
    """One player-character custom state value with its display format."""
    schema_id: str
    category: str
    name: str
    data_type: str
    value: Any = None
    display_format: str  # inject_as


class PCStateResponse(BaseModel):
    """A player character's full current state — location, emotion, conditions, and custom fields."""
    character: str
    location: str | None = None
    emotional_state: str | None = None
    conditions: list[str] = []
    custom_state: list[PCCustomStateItem] = []
