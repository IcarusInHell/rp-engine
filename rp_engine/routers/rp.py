"""RP management endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from rp_engine.config import SearchConfig
from rp_engine.database import Database
from rp_engine.dependencies import (
    get_card_indexer,
    get_db,
    get_guidelines_service,
    get_lance_store,
    get_vault_root,
)
from rp_engine.models.rp import (
    ChunkingConfig,
    ChunkingUpdate,
    ExportRequest,
    ImportResponse,
    RechunkResponse,
    RPCreate,
    RPInfo,
    RPResponse,
)
from rp_engine.services.card_indexer import CARD_TYPE_DIRS, CardIndexer
from rp_engine.services.export_service import export_rp as do_export
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.import_service import ImportValidationError
from rp_engine.services.import_service import import_rp as do_import
from rp_engine.utils.chunking import get_effective_chunking
from rp_engine.utils.frontmatter import parse_file, serialize_frontmatter
from rp_engine.utils.image import (
    AVATAR_EXTENSIONS,
    AVATAR_MAX_BYTES,
    MEDIA_TYPES,
    find_avatar,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rp", tags=["rp"])

# Directories to scaffold under Story Cards/
SCAFFOLD_DIRS = list(CARD_TYPE_DIRS.values())


# ---------- Helpers ----------


def _require_rp_dir(vault_root: Path, name: str) -> Path:
    """Return the RP directory path, raising 404 if it doesn't exist."""
    rp_dir = vault_root / name
    if not rp_dir.is_dir():
        raise HTTPException(404, detail=f"RP folder not found: {name}")
    return rp_dir


def _resolve_avatar_path(vault_root: Path, rp_folder: str, guidelines_svc: GuidelinesService | None = None) -> Path | None:
    """Find the avatar image for an RP, checking frontmatter then convention filenames."""
    rp_dir = vault_root / rp_folder
    avatar_field: str | None = None
    if guidelines_svc:
        guidelines = guidelines_svc.get_guidelines(rp_folder)
        if guidelines:
            avatar_field = guidelines.avatar
    return find_avatar(rp_dir, frontmatter_avatar=avatar_field)


def _build_guidelines(body: RPCreate, vault_root: Path) -> str:
    """Build Story_Guidelines.md content from RPCreate + optional template."""
    template_path = vault_root / "z_templates" / "Story_Guidelines.template.md"
    if template_path.exists():
        frontmatter, body_text = parse_file(template_path)
        frontmatter = frontmatter or {}
    else:
        frontmatter, body_text = {}, ""

    # Override template values with user's choices
    frontmatter["pov_mode"] = body.pov_mode
    frontmatter["narrative_voice"] = body.narrative_voice
    frontmatter["tense"] = body.tense
    frontmatter["scene_pacing"] = body.scene_pacing
    frontmatter["response_length"] = body.response_length
    frontmatter.setdefault("pov_character", "")
    frontmatter.setdefault("integrate_user_narrative", True)
    frontmatter.setdefault("preserve_user_details", True)
    if body.tone:
        frontmatter["tone"] = [body.tone] if isinstance(body.tone, str) else body.tone
    if body.dual_characters:
        frontmatter["dual_characters"] = body.dual_characters

    return serialize_frontmatter(frontmatter, body_text)


async def _get_effective_chunking(
    db: Database, rp_folder: str, config_search: SearchConfig | None = None
) -> ChunkingConfig:
    """Get the effective chunking config for an RP (per-RP override > global).

    Delegates to the shared ``get_effective_chunking()`` utility and wraps
    the result in a Pydantic ``ChunkingConfig`` for API responses.
    """
    params = await get_effective_chunking(db, rp_folder, fallback_config=config_search)
    return ChunkingConfig(
        strategy=params.strategy,
        chunk_size=params.chunk_size,
        chunk_overlap=params.chunk_overlap,
    )


# ---------- RP CRUD ----------


@router.post("", response_model=RPResponse, status_code=201)
async def create_rp(
    body: RPCreate,
    vault_root: Path = Depends(get_vault_root),
):
    """Create a new RP with scaffolded folder structure."""
    rp_dir = vault_root / body.rp_name
    if rp_dir.exists():
        raise HTTPException(409, detail=f"RP folder already exists: {body.rp_name}")

    created_files: list[str] = []

    # Create Story Cards subdirectories
    for dir_name in SCAFFOLD_DIRS:
        d = rp_dir / "Story Cards" / dir_name
        d.mkdir(parents=True, exist_ok=True)
        created_files.append(f"Story Cards/{dir_name}/")

    # Create RP State directory + Story_Guidelines.md
    rp_state = rp_dir / "RP State"
    rp_state.mkdir(parents=True, exist_ok=True)

    guidelines_path = rp_state / "Story_Guidelines.md"
    guidelines_content = _build_guidelines(body, vault_root)
    guidelines_path.write_text(guidelines_content, encoding="utf-8")
    created_files.append("RP State/Story_Guidelines.md")

    return RPResponse(rp_folder=body.rp_name, created_files=created_files)


@router.get("", response_model=list[RPInfo])
async def list_rps(
    db: Database = Depends(get_db),
    indexer: CardIndexer = Depends(get_card_indexer),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """List all discovered RP folders with basic info."""
    folders = indexer.get_all_rp_folders()
    if not folders:
        return []

    # Batch queries instead of 2N individual queries
    card_count_rows = await db.fetch_all(
        "SELECT rp_folder, COUNT(*) as cnt FROM story_cards GROUP BY rp_folder"
    )
    card_counts = {r["rp_folder"]: r["cnt"] for r in card_count_rows}

    branch_rows = await db.fetch_all(
        "SELECT rp_folder, name FROM branches"
    )
    branch_map: dict[str, list[str]] = {}
    for r in branch_rows:
        branch_map.setdefault(r["rp_folder"], []).append(r["name"])

    result = []
    for folder in folders:
        card_count = card_counts.get(folder, 0)
        guidelines_path = vault_root / folder / "RP State" / "Story_Guidelines.md"
        result.append(RPInfo(
            rp_folder=folder,
            has_story_cards=card_count > 0,
            card_count=card_count,
            has_guidelines=guidelines_path.exists(),
            has_avatar=_resolve_avatar_path(vault_root, folder, guidelines_svc) is not None,
            branches=branch_map.get(folder, []),
        ))
    return result


@router.get("/{name}", response_model=RPInfo)
async def get_rp(
    name: str,
    db: Database = Depends(get_db),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Get info about a specific RP."""
    _require_rp_dir(vault_root, name)

    card_count = await db.fetch_val(
        "SELECT COUNT(*) FROM story_cards WHERE rp_folder = ?", [name]
    ) or 0

    guidelines_path = vault_root / name / "RP State" / "Story_Guidelines.md"
    has_guidelines = guidelines_path.exists()

    branch_rows = await db.fetch_all(
        "SELECT name FROM branches WHERE rp_folder = ?", [name]
    )
    branches = [r["name"] for r in branch_rows]

    return RPInfo(
        rp_folder=name,
        has_story_cards=card_count > 0,
        card_count=card_count,
        has_guidelines=has_guidelines,
        has_avatar=_resolve_avatar_path(vault_root, name, guidelines_svc) is not None,
        branches=branches,
    )


# ---------- Avatar endpoints ----------


@router.get("/{name}/avatar")
async def get_avatar(
    name: str,
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Serve the RP's avatar image."""
    _require_rp_dir(vault_root, name)

    avatar_path = _resolve_avatar_path(vault_root, name, guidelines_svc)
    if avatar_path is None:
        raise HTTPException(404, detail="No avatar image found")

    # Validate extension
    if avatar_path.suffix.lower() not in AVATAR_EXTENSIONS:
        raise HTTPException(400, detail=f"Unsupported image type: {avatar_path.suffix}")

    # Validate size
    if avatar_path.stat().st_size > AVATAR_MAX_BYTES:
        raise HTTPException(413, detail="Avatar image exceeds 5 MB limit")

    media_type = MEDIA_TYPES.get(avatar_path.suffix.lower(), "application/octet-stream")

    return FileResponse(
        avatar_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/{name}/avatar")
async def upload_avatar(
    name: str,
    file: UploadFile,
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Upload an avatar image for the RP. Updates Story_Guidelines.md frontmatter."""
    rp_dir = _require_rp_dir(vault_root, name)

    # Validate extension
    if not file.filename:
        raise HTTPException(400, detail="No filename provided")
    ext = Path(file.filename).suffix.lower()
    if ext not in AVATAR_EXTENSIONS:
        raise HTTPException(400, detail=f"Unsupported image type: {ext}")

    # Read and validate size
    content = await file.read()
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(413, detail="Avatar image exceeds 5 MB limit")

    # Save as cover{ext} in RP root
    avatar_filename = f"cover{ext}"
    avatar_path = rp_dir / avatar_filename
    avatar_path.write_bytes(content)

    # Update frontmatter
    guidelines_path = rp_dir / "RP State" / "Story_Guidelines.md"
    if guidelines_path.exists():
        frontmatter, body = parse_file(guidelines_path)
        if frontmatter is None:
            frontmatter = {}
        frontmatter["avatar"] = avatar_filename
        guidelines_path.write_text(
            serialize_frontmatter(frontmatter, body), encoding="utf-8"
        )
        guidelines_svc.invalidate(name)

    return {"status": "ok", "filename": avatar_filename}


# ---------- Chunking endpoints ----------


@router.get("/{name}/chunking", response_model=ChunkingConfig)
async def get_chunking(
    name: str,
    db: Database = Depends(get_db),
    vault_root: Path = Depends(get_vault_root),
):
    """Get the effective chunking configuration for an RP."""
    _require_rp_dir(vault_root, name)
    return await _get_effective_chunking(db, name)


@router.put("/{name}/chunking", response_model=ChunkingConfig)
async def update_chunking(
    name: str,
    body: ChunkingUpdate,
    db: Database = Depends(get_db),
    vault_root: Path = Depends(get_vault_root),
):
    """Update the per-RP chunking configuration."""
    _require_rp_dir(vault_root, name)

    valid_strategies = {"fixed", "by_character"}
    if body.strategy and body.strategy not in valid_strategies:
        raise HTTPException(400, detail=f"Invalid strategy. Must be one of: {valid_strategies}")

    # Get current effective config as base
    current = await _get_effective_chunking(db, name)
    new_strategy = body.strategy or current.strategy
    new_chunk_size = body.chunk_size or current.chunk_size
    new_chunk_overlap = body.chunk_overlap or current.chunk_overlap

    # Upsert into rp_chunking_config
    future = await db.enqueue_write(
        """INSERT INTO rp_chunking_config (rp_folder, strategy, chunk_size, chunk_overlap)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(rp_folder) DO UPDATE SET
               strategy = excluded.strategy,
               chunk_size = excluded.chunk_size,
               chunk_overlap = excluded.chunk_overlap,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
        [name, new_strategy, new_chunk_size, new_chunk_overlap],
    )
    await future

    return ChunkingConfig(
        strategy=new_strategy,
        chunk_size=new_chunk_size,
        chunk_overlap=new_chunk_overlap,
    )


@router.post("/{name}/rechunk", response_model=RechunkResponse)
async def rechunk(
    name: str,
    db: Database = Depends(get_db),
    vault_root: Path = Depends(get_vault_root),
    lance_store=Depends(get_lance_store),
):
    """Selectively rechunk all exchanges for an RP using its current chunking config."""
    _require_rp_dir(vault_root, name)

    if lance_store is None:
        raise HTTPException(503, detail="LanceDB not available")

    chunking = await _get_effective_chunking(db, name)

    result = await lance_store.reindex_exchanges(
        db=db,
        rp_folder=name,
        branch="main",
        chunking_strategy=chunking.strategy,
        chunk_size=chunking.chunk_size,
        chunk_overlap=chunking.chunk_overlap,
        selective=True,
    )

    return RechunkResponse(**result)


# ---------- Export / Import ----------


@router.post("/{name}/export")
async def export_rp(
    name: str,
    body: ExportRequest | None = None,
    db: Database = Depends(get_db),
    vault_root: Path = Depends(get_vault_root),
):
    """Export the RP as a ZIP archive."""
    _require_rp_dir(vault_root, name)

    include_optional = body.include_optional if body else True
    buf = await do_export(db, vault_root, name, include_optional=include_optional)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_export_{timestamp}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=ImportResponse)
async def import_rp_endpoint(
    file: UploadFile,
    db: Database = Depends(get_db),
    vault_root: Path = Depends(get_vault_root),
    lance_store=Depends(get_lance_store),
    indexer: CardIndexer = Depends(get_card_indexer),
):
    """Import an RP from a ZIP archive."""
    zip_bytes = await file.read()

    try:
        rp_folder, stats = await do_import(db, vault_root, zip_bytes)
    except ImportValidationError as e:
        raise HTTPException(400, detail=str(e)) from None

    # Trigger reindexing
    warnings = list(stats.warnings)
    try:
        await indexer.full_index(rp_folder)
    except Exception as e:
        logger.warning("Card reindex after import failed: %s", e)
        warnings.append(f"Card reindex failed: {e}")

    if lance_store is not None:
        try:
            await lance_store.reindex_exchanges(db=db, rp_folder=rp_folder, branch="main")
        except Exception as e:
            logger.warning("Exchange reindex after import failed: %s", e)
            warnings.append(f"Exchange reindex failed: {e}")

    stats.warnings = warnings

    return ImportResponse(status="ok", rp_folder=rp_folder, stats=stats)
