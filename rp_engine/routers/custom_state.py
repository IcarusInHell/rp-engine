"""Custom state API — manage custom tracked fields and their values."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException

from rp_engine.config import get_config
from rp_engine.dependencies import get_custom_state_manager, get_state_manager
from rp_engine.models.custom_state import (
    CustomStateSchema,
    CustomStateSchemaCreate,
    CustomStateSet,
    CustomStateSnapshot,
    CustomStateValue,
    PCCustomStateItem,
    PCStateResponse,
    PresetInfo,
)
from rp_engine.services.custom_state_manager import CustomStateManager
from rp_engine.services.state_manager import StateManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/custom-state", tags=["custom-state"])


@router.get("/schemas", response_model=list[CustomStateSchema])
async def list_schemas(
    rp_folder: str = Query(...),
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """List custom state schemas (tracked-field definitions) for an RP."""
    return await mgr.list_schemas(rp_folder)


@router.post("/schemas", response_model=CustomStateSchema)
async def create_schema(
    body: CustomStateSchemaCreate,
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """Create a custom state schema (define a new tracked field)."""
    schema = CustomStateSchema(**body.model_dump())
    return await mgr.create_schema(schema, body.rp_folder)


@router.delete("/schemas/{schema_id}")
async def delete_schema(
    schema_id: str,
    rp_folder: str = Query(...),
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """Delete a custom state schema by id."""
    await mgr.delete_schema(schema_id, rp_folder)
    return {"deleted": True}


@router.get("", response_model=CustomStateSnapshot)
async def get_all_state(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """Return a snapshot of all custom state values for an RP/branch."""
    return await mgr.get_snapshot(rp_folder, branch)


@router.get("/{schema_id}", response_model=CustomStateValue | None)
async def get_value(
    schema_id: str,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    entity_id: str | None = Query(None),
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """Get the current value of one custom state field (optionally entity-scoped)."""
    return await mgr.get_value(schema_id, rp_folder, branch, entity_id)


@router.post("/{schema_id}", response_model=CustomStateValue)
async def set_value(
    schema_id: str,
    body: CustomStateSet,
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """Set a custom state field's value, recording the change reason."""
    return await mgr.set_value(
        schema_id=schema_id,
        value=body.value,
        rp_folder=body.rp_folder,
        branch=body.branch,
        entity_id=body.entity_id,
        reason=body.reason,
    )


@router.get("/pc", response_model=PCStateResponse)
async def get_pc_state(
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    pc_name: str | None = Query(None),
    mgr: CustomStateManager = Depends(get_custom_state_manager),
    state_mgr: StateManager = Depends(get_state_manager),
):
    """Get combined PC state: character state + custom state in one call."""
    character = pc_name or get_config().rp.default_pov_character

    char_detail = await state_mgr.get_character(character, rp_folder, branch)

    schemas = await mgr.list_schemas(rp_folder)
    char_schemas = [s for s in schemas if s.belongs_to == "character"]

    custom_items = []
    for schema in sorted(char_schemas, key=lambda s: s.display_order):
        val = await mgr.get_value(schema.id, rp_folder, branch, entity_id=character)
        custom_items.append(PCCustomStateItem(
            schema_id=schema.id,
            category=schema.category,
            name=schema.name,
            data_type=schema.data_type,
            value=val.value if val else (schema.config or {}).get("default"),
            display_format=schema.inject_as,
        ))

    return PCStateResponse(
        character=character,
        location=char_detail.location if char_detail else None,
        emotional_state=char_detail.emotional_state if char_detail else None,
        conditions=char_detail.conditions if char_detail else [],
        custom_state=custom_items,
    )


@router.get("/presets/list", response_model=list[PresetInfo])
async def list_presets(
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """List available custom-state presets (predefined schema bundles)."""
    return mgr.list_presets()


@router.post("/presets/{preset_name}/apply", response_model=list[CustomStateSchema])
async def apply_preset(
    preset_name: str,
    rp_folder: str = Query(...),
    mgr: CustomStateManager = Depends(get_custom_state_manager),
):
    """Apply a named preset, creating its schemas for the RP."""
    try:
        return await mgr.apply_preset(preset_name, rp_folder)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
