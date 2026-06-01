"""Card admin/meta endpoints: reindex, schema, validate."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.dependencies import get_card_indexer
from rp_engine.models.frontmatter import FRONTMATTER_MODELS, validate_frontmatter
from rp_engine.models.story_card import CardValidateRequest, ReindexResponse
from rp_engine.services.card_indexer import CardIndexer

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.post("/reindex", response_model=ReindexResponse)
async def reindex_all(
    rp_folder: str | None = Query(None),
    indexer: CardIndexer = Depends(get_card_indexer),
):
    """Force a full reindex of all story cards."""
    if rp_folder:
        result = await indexer.full_index(rp_folder)
    else:
        folders = indexer.get_all_rp_folders()
        # Bug E: aggregate ALL fields the single-folder path returns, including
        # chunks and trust_baselines_seeded (previously dropped from the global
        # response). Use .get() so the empty-folder early-return shape is safe.
        totals = {
            "entities": 0,
            "connections": 0,
            "aliases": 0,
            "keywords": 0,
            "chunks": 0,
            "trust_baselines_seeded": 0,
            "duration_ms": 0.0,
        }
        for folder in folders:
            r = await indexer.full_index(folder)
            for k in ("entities", "connections", "aliases", "keywords",
                      "chunks", "trust_baselines_seeded", "duration_ms"):
                totals[k] += r.get(k, 0)
        result = totals

    return ReindexResponse(**result)


# IMPORTANT: /schema and /validate must be mounted BEFORE the /{card_type}
# catch-all (see __init__.py) to avoid route shadowing.
@router.get("/schema/{card_type}")
async def get_schema(card_type: str):
    """Return JSON schema for a card type's frontmatter."""
    model = FRONTMATTER_MODELS.get(card_type)
    if not model:
        raise HTTPException(400, detail=f"Unknown card type: {card_type}")
    return model.model_json_schema()


@router.post("/validate")
async def validate_card(body: CardValidateRequest):
    """Validate card frontmatter against schema."""
    valid, errors, warnings = validate_frontmatter(body.card_type, body.frontmatter)
    return {"valid": valid, "errors": errors, "warnings": warnings}
