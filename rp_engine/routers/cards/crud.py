"""Story card CRUD endpoints: list, get, create, update, delete.

The catch-all ``/{card_type}/{name}`` routes live here and MUST be mounted last
(see ``__init__.py``) so the specific paths in admin/analytics/authoring win.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import Database
from rp_engine.dependencies import (
    get_card_authoring_service,
    get_card_indexer,
    get_db,
    get_vault_root,
)
from rp_engine.models.frontmatter import validate_frontmatter
from rp_engine.models.story_card import (
    CardListResponse,
    DeleteCardResponse,
    EntityConnection,
    StoryCardCreate,
    StoryCardDetail,
    StoryCardSummary,
    StoryCardUpdate,
)
from rp_engine.services.card_authoring import CardAuthoringService
from rp_engine.services.card_indexer import CARD_TYPE_DIRS, CardIndexer
from rp_engine.utils.frontmatter import parse_frontmatter, serialize_frontmatter
from rp_engine.utils.json_helpers import safe_parse_json
from rp_engine.utils.normalization import generate_card_id

from ._lookup import _find_card_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["cards"])


@router.get("", response_model=CardListResponse)
async def list_cards(
    card_type: str | None = Query(None),
    rp_folder: str | None = Query(None),
    db: Database = Depends(get_db),
):
    """List all story cards, optionally filtered by type and RP folder."""
    conditions = []
    params: list = []

    if card_type:
        conditions.append("card_type = ?")
        params.append(card_type)
    if rp_folder:
        conditions.append("rp_folder = ?")
        params.append(rp_folder)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await db.fetch_all(
        f"SELECT id, rp_folder, file_path, card_type, name, importance, summary FROM story_cards{where}",
        params,
    )

    # Batch-fetch connection counts for all returned cards
    if rows:
        id_list = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(id_list))
        count_rows = await db.fetch_all(
            f"SELECT from_entity, COUNT(*) as cnt FROM entity_connections WHERE from_entity IN ({placeholders}) GROUP BY from_entity",
            id_list,
        )
        connection_counts = {r["from_entity"]: r["cnt"] for r in count_rows}
    else:
        connection_counts = {}

    # Batch-fetch aliases for all cards (fixes N+1)
    if rows:
        alias_rows = await db.fetch_all(
            f"SELECT entity_id, alias FROM entity_aliases WHERE entity_id IN ({placeholders})",
            id_list,
        )
        alias_map: dict[str, list[str]] = {}
        for ar in alias_rows:
            alias_map.setdefault(ar["entity_id"], []).append(ar["alias"])

        # Batch-fetch frontmatter for tags (single query instead of N)
        fm_rows = await db.fetch_all(
            f"SELECT id, frontmatter FROM story_cards WHERE id IN ({placeholders})",
            id_list,
        )
        tags_map: dict[str, list[str]] = {}
        for fr in fm_rows:
            fm = safe_parse_json(fr["frontmatter"])
            tags = fm.get("tags", [])
            tags_map[fr["id"]] = tags if isinstance(tags, list) else []
    else:
        alias_map = {}
        tags_map = {}

    cards = [
        StoryCardSummary(
            id=row["id"],
            name=row["name"],
            card_type=row["card_type"],
            importance=row["importance"],
            file_path=row["file_path"],
            summary=row["summary"],
            aliases=alias_map.get(row["id"], []),
            tags=tags_map.get(row["id"], []),
            connection_count=connection_counts.get(row["id"], 0),
        )
        for row in rows
    ]

    return CardListResponse(cards=cards, total=len(cards))


@router.get("/{card_type}/{name}", response_model=StoryCardDetail)
async def get_card(
    card_type: str,
    name: str,
    db: Database = Depends(get_db),
):
    """Get a single card with its frontmatter, content, and connections."""
    row = await _find_card_row(db, card_type, name)
    if not row:
        raise HTTPException(404, detail=f"Card not found: {card_type}/{name}")

    frontmatter = safe_parse_json(row["frontmatter"])

    conn_rows = await db.fetch_all(
        "SELECT to_entity, connection_type, field, role FROM entity_connections WHERE from_entity = ?",
        [row["id"]],
    )
    connections = [
        EntityConnection(
            to_entity=c["to_entity"],
            connection_type=c["connection_type"],
            field=c["field"],
            role=c["role"],
        )
        for c in conn_rows
    ]

    raw_content = row["content"] or ""
    _, body = parse_frontmatter(raw_content)

    return StoryCardDetail(
        name=row["name"],
        card_type=row["card_type"],
        file_path=row["file_path"],
        importance=row["importance"],
        frontmatter=frontmatter,
        content=raw_content,
        body=body.strip(),
        connections=connections,
    )


@router.post("/{card_type}", response_model=StoryCardDetail, status_code=201)
async def create_card(
    card_type: str,
    body: StoryCardCreate,
    rp_folder: str = Query(...),
    sync_relationships: bool = Query(True, description="Auto-update referenced cards with reciprocal relationships"),
    db: Database = Depends(get_db),
    indexer: CardIndexer = Depends(get_card_indexer),
    vault_root: Path = Depends(get_vault_root),
    card_authoring: CardAuthoringService = Depends(get_card_authoring_service),
):
    """Create a new story card. Writes .md file and indexes it.

    When sync_relationships=true (default), any cards referenced in
    initial_relationships will be updated with reciprocal relationship entries.
    """
    if card_type not in CARD_TYPE_DIRS:
        raise HTTPException(400, detail=f"Unknown card type: {card_type}")

    # Auto-generate card_id if not provided
    card_id = body.frontmatter.get("card_id")
    if not card_id:
        card_id = generate_card_id(card_type, body.name)

    dir_name = CARD_TYPE_DIRS[card_type]
    card_dir = vault_root / rp_folder / "Story Cards" / dir_name
    card_dir.mkdir(parents=True, exist_ok=True)

    # Use card_id as filename
    file_path = card_dir / f"{card_id}.md"
    if file_path.exists():
        raise HTTPException(409, detail=f"Card already exists: {card_id}")

    # If content arrives with embedded frontmatter, extract and merge it
    card_body = body.content
    if card_body.lstrip().startswith("---"):
        embedded_fm, stripped_body = parse_frontmatter(card_body.lstrip())
        if embedded_fm:
            # Merge embedded frontmatter (explicit body.frontmatter wins)
            body.frontmatter = {**embedded_fm, **body.frontmatter}
            card_body = stripped_body

    frontmatter = {"type": card_type, "card_id": card_id, "name": body.name, **body.frontmatter}
    frontmatter["card_id"] = card_id  # ensure card_id wins over any provided value

    # Validate frontmatter (warn but don't block)
    valid, errors, _warnings = validate_frontmatter(card_type, frontmatter)
    if not valid:
        logger.warning("Frontmatter validation errors for %s: %s", card_id, errors)

    content = serialize_frontmatter(frontmatter, card_body)
    file_path.write_text(content, encoding="utf-8")

    await indexer.index_file(rp_folder, file_path)

    # Sync reciprocal relationships to referenced cards
    if sync_relationships and card_type in ("character", "npc"):
        sync_result = await card_authoring.sync_reciprocal_relationships(
            new_card_name=body.name,
            new_card_id=card_id,
            new_card_frontmatter=frontmatter,
            new_card_body=body.content,
            rp_folder=rp_folder,
        )
        if sync_result.updated_cards:
            logger.info(
                "Synced reciprocal relationships for %s → %s",
                body.name,
                [e.card_name for e in sync_result.updated_cards],
            )
        if sync_result.errors:
            logger.warning("Relationship sync errors: %s", sync_result.errors)

    return await get_card(card_type, body.name, db)


@router.delete("/{card_type}/{name}", response_model=DeleteCardResponse)
async def delete_card(
    card_type: str,
    name: str,
    db: Database = Depends(get_db),
    indexer: CardIndexer = Depends(get_card_indexer),
    vault_root: Path = Depends(get_vault_root),
):
    """Delete a story card — removes the .md file and all index data."""
    row = await _find_card_row(db, card_type, name)
    if not row:
        raise HTTPException(404, detail=f"Card not found: {card_type}/{name}")

    # Delete the file
    file_path = vault_root / row["file_path"]
    file_deleted = False
    if file_path.exists():
        file_path.unlink()
        file_deleted = True

    # Remove from index (connections, aliases, keywords, story_cards)
    await indexer.remove_file(row["rp_folder"], vault_root / row["file_path"])
    # If remove_file didn't find it (file already gone), clean up by entity ID
    if not file_deleted:
        await indexer.remove_entity_by_id(row["id"])

    logger.info("Deleted card: %s/%s (file_deleted=%s)", card_type, row["name"], file_deleted)
    return DeleteCardResponse(name=row["name"], card_type=card_type, file_deleted=file_deleted)


@router.put("/{card_type}/{name}", response_model=StoryCardDetail)
async def update_card(
    card_type: str,
    name: str,
    body: StoryCardUpdate,
    db: Database = Depends(get_db),
    indexer: CardIndexer = Depends(get_card_indexer),
    vault_root: Path = Depends(get_vault_root),
):
    """Update an existing card. Writes .md file and reindexes."""
    row = await _find_card_row(db, card_type, name)
    if not row:
        raise HTTPException(404, detail=f"Card not found: {card_type}/{name}")

    file_path = vault_root / row["file_path"]
    if not file_path.exists():
        raise HTTPException(404, detail=f"Card file missing: {row['file_path']}")

    current_fm = safe_parse_json(row["frontmatter"])

    if body.frontmatter is not None:
        current_fm.update(body.frontmatter)

    _, current_body = parse_frontmatter(row["content"] or "")
    new_body = body.content if body.content is not None else current_body

    content = serialize_frontmatter(current_fm, new_body)
    file_path.write_text(content, encoding="utf-8")

    rp_folder = row["rp_folder"]
    await indexer.index_file(rp_folder, file_path)

    return await get_card(card_type, row["name"], db)
