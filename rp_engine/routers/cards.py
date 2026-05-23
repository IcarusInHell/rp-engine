"""Story card CRUD endpoints."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import Database
from rp_engine.dependencies import (
    get_card_indexer,
    get_db,
    get_guidelines_service,
    get_llm_client,
    get_vault_root,
)
from rp_engine.models.frontmatter import FRONTMATTER_MODELS, validate_frontmatter
from rp_engine.models.story_card import (
    AuditCardsRequest,
    AuditCardsResponse,
    AuditGap,
    CardListResponse,
    CardValidateRequest,
    DeleteCardResponse,
    EntityConnection,
    GapEvidenceResponse,
    GapExchangeRecord,
    GenerateCardNameRequest,
    GenerateCardNameResponse,
    ReindexResponse,
    RelationshipSyncResult,
    SceneEvidence,
    StoryCardCreate,
    StoryCardDetail,
    StoryCardSummary,
    StoryCardUpdate,
    SuggestCardRequest,
    SuggestCardResponse,
)
from rp_engine.services.card_indexer import CARD_TYPE_DIRS, CardIndexer
from rp_engine.services.llm_client import LLMClient
from rp_engine.utils.frontmatter import parse_frontmatter, serialize_frontmatter
from rp_engine.utils.json_helpers import safe_parse_json
from rp_engine.utils.normalization import generate_card_id, normalize_key
from rp_engine.utils.scene_detection import group_into_scenes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["cards"])

# Pattern matches all Obsidian Templater expressions: <% ... %>
_TEMPLATER_RE = re.compile(r"<%.*?%>")

# Pattern to strip markdown code fences wrapping the entire response
_CODE_FENCE_RE = re.compile(r"^```(?:markdown|yaml|md)?\s*\n(.*?)```\s*$", re.DOTALL)


def _sanitize_llm_card(markdown: str) -> str:
    """Clean up common LLM generation artifacts in card markdown.

    Handles:
    - Markdown code fences wrapping the entire response
    - Multiple duplicate frontmatter blocks (keeps the most complete one)
    - Stray 'yaml' / 'markdown' text between blocks (code fence leakage)
    """
    text = markdown.strip()

    # Strip wrapping code fences (```markdown ... ```)
    m = _CODE_FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()

    if not text.startswith("---"):
        return text

    # Extract ALL frontmatter blocks from the beginning of the text.
    # Pattern: ---\n<yaml>\n--- possibly followed by more blocks with
    # optional stray 'yaml'/'markdown' lines between them.
    best_fm = ""
    body = text
    _STRAY_RE = re.compile(r"^(?:yaml|markdown)\r?\n", re.IGNORECASE)

    while body.startswith("---"):
        end_idx = body.find("---", 3)
        if end_idx == -1:
            break
        fm_block = body[3:end_idx].strip()
        after = body[end_idx + 3:].lstrip("\n")

        # Keep the longest (most complete) frontmatter block
        if len(fm_block) > len(best_fm):
            best_fm = fm_block

        body = after
        # Strip stray code fence language markers between blocks
        m_stray = _STRAY_RE.match(body)
        if m_stray:
            body = body[m_stray.end():]

    if best_fm:
        text = f"---\n{best_fm}\n---\n{body}"

    return text


def _strip_templater(
    template: str, entity_name: str, rp_folder: str, card_type: str
) -> str:
    """Replace Obsidian Templater expressions with concrete values for LLM prompts."""
    card_id = generate_card_id(card_type, entity_name)
    trigger = entity_name.lower()

    # Named replacements first
    result = template.replace("<% tp.user.rp_helpers.getTitle() %>", entity_name)
    result = result.replace("<% tp.user.rp_helpers.getId() %>", card_id)
    result = result.replace("<% tp.user.rp_helpers.getRpName() %>", rp_folder)
    result = result.replace("<% tp.user.rp_helpers.getTrigger() %>", trigger)

    # Remove any remaining Templater expressions (cursor, date, etc.)
    result = _TEMPLATER_RE.sub("", result)
    return result


def _normalize_url_name(name: str) -> str:
    """Normalize a URL path name — convert hyphens to spaces, then normalize."""
    return normalize_key(name.replace("-", " "))


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


@router.get("/connections")
async def get_connections(
    rp_folder: str = Query(...),
    db: Database = Depends(get_db),
):
    """Return graph data (nodes + edges) for all cards in an RP.

    Used by the Dashboard entity graph. Queries entity_connections and
    maps entity IDs back to card names.
    """
    card_rows = await db.fetch_all(
        "SELECT id, name, card_type, importance FROM story_cards WHERE rp_folder = ?",
        [rp_folder],
    )
    if not card_rows:
        return {"nodes": [], "edges": []}

    id_to_card = {row["id"]: row for row in card_rows}

    conn_rows = await db.fetch_all(
        """SELECT ec.from_entity, ec.to_entity, ec.connection_type
           FROM entity_connections ec
           JOIN story_cards sc ON ec.from_entity = sc.id
           WHERE sc.rp_folder = ?""",
        [rp_folder],
    )

    nodes = [
        {
            "name": row["name"],
            "card_type": row["card_type"],
            "importance": row["importance"],
        }
        for row in card_rows
    ]

    edges = []
    for conn in conn_rows:
        from_card = id_to_card.get(conn["from_entity"])
        to_card = id_to_card.get(conn["to_entity"])
        if from_card and to_card:
            edges.append({
                "from": from_card["name"],
                "to": to_card["name"],
                "connection_type": conn["connection_type"],
            })

    return {"nodes": nodes, "edges": edges}


# IMPORTANT: /reindex must be defined BEFORE /{card_type} to avoid route shadowing
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
        totals = {"entities": 0, "connections": 0, "aliases": 0, "keywords": 0, "duration_ms": 0.0}
        for folder in folders:
            r = await indexer.full_index(folder)
            for k in ("entities", "connections", "aliases", "keywords"):
                totals[k] += r[k]
            totals["duration_ms"] += r.get("duration_ms", 0)
        result = totals

    return ReindexResponse(**result)


@router.post("/suggest", response_model=SuggestCardResponse)
async def suggest_card(
    body: SuggestCardRequest,
    db: Database = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    vault_root: Path = Depends(get_vault_root),
):
    """Generate a draft story card for an entity by searching exchanges and using LLM."""
    entity_name = body.entity_name
    card_type = body.card_type
    rp_folder = body.rp_folder
    additional_context = body.additional_context

    branch = body.branch

    # Try scene-aware evidence first (from card_gap_exchanges)
    gap_rows = await db.fetch_all(
        """SELECT exchange_number, chunk_text, mention_type
           FROM card_gap_exchanges
           WHERE LOWER(entity_name) = LOWER(?) AND rp_folder = ?
             AND branch = ?
           ORDER BY exchange_number""",
        [entity_name, rp_folder, branch],
    )

    if gap_rows:
        # Scene-aware path: group gap exchanges into scenes
        exchange_nums = [r["exchange_number"] for r in gap_rows]
        scenes = group_into_scenes(exchange_nums)
        chunks_by_num = {r["exchange_number"]: r for r in gap_rows}

        evidence_parts = []
        for i, scene in enumerate(scenes, 1):
            scene_chunks = []
            for num in scene.exchanges:
                row = chunks_by_num.get(num)
                if row and row["chunk_text"]:
                    label = "PRIMARY" if row["mention_type"] == "primary" else "peripheral"
                    scene_chunks.append(f"[Exchange {num}, {label}]\n{row['chunk_text']}")
            if scene_chunks:
                evidence_parts.append(
                    f"## Scene {i} (Exchanges {scene.start}-{scene.end})\n"
                    + "\n---\n".join(scene_chunks)
                )

        evidence = "\n\n".join(evidence_parts)
        primary_count = sum(1 for r in gap_rows if r["mention_type"] == "primary")
        evidence += (
            f"\n\nEntity mentioned in {len(gap_rows)} exchanges total, "
            f"{primary_count} as primary focus."
        )
    else:
        # Fallback: search exchanges directly (pre-existing gaps or no gap tracking)
        rows = await db.fetch_all(
            """SELECT exchange_number, user_message, assistant_response
               FROM exchanges
               WHERE rp_folder = ? AND (
                   LOWER(user_message) LIKE ? OR LOWER(assistant_response) LIKE ?
               )
               ORDER BY exchange_number DESC LIMIT 10""",
            [rp_folder, f"%{entity_name.lower()}%", f"%{entity_name.lower()}%"],
        )
        evidence = "\n---\n".join(
            f"Exchange {r['exchange_number']}:\n{r['assistant_response'][:500]}"
            for r in rows
        )

    # Load related card content
    related_cards_section = ""
    if body.related_entities:
        related_parts = []
        for rel_name in body.related_entities:
            row = await db.fetch_one(
                "SELECT name, card_type, content FROM story_cards WHERE LOWER(name) = LOWER(?) AND rp_folder = ?",
                [rel_name, rp_folder],
            )
            if row and row["content"]:
                related_parts.append(
                    f"### {row['name']} ({row['card_type']})\n{row['content']}"
                )
        if related_parts:
            related_cards_section = (
                "## Related Cards (established lore — do NOT contradict)\n\n"
                + "\n\n---\n\n".join(related_parts)
            )

    # Load template
    template_map = {
        "character": "Character Template.md",
        "npc": "NPC Template.md",
        "location": "Location Template.md",
        "secret": "Secret Template.md",
        "memory": "Memory Template.md",
        "knowledge": "Knowledge Template.md",
        "lore": "Lore Template.md",
        "organization": "Organization Template.md",
        "plot_thread": "Plot Thread Template.md",
        "plot_arc": "Plot Arc Template.md",
        "item": "Item Template.md",
        "chapter_summary": "Chapter Template.md",
    }
    template_name = template_map.get(card_type, "NPC Template.md")
    template_path = vault_root / "z_templates" / "Story Cards" / template_name
    template = ""
    if template_path.exists():
        raw = template_path.read_text(encoding="utf-8")
        template = _strip_templater(raw, entity_name, rp_folder, card_type)

    # Split template into frontmatter and body so the LLM doesn't duplicate the frontmatter
    template_frontmatter = ""
    template_body = template
    if template.startswith("---"):
        parts = template.split("---", 2)
        if len(parts) >= 3:
            template_frontmatter = parts[1].strip()
            template_body = parts[2].strip()

    prompt = f"""Create a story card for the entity "{entity_name}" (type: {card_type}).

The card MUST start with exactly ONE YAML frontmatter block (between --- delimiters) containing these fields:

{template_frontmatter}

Then use this body structure as a guide (only include sections where evidence supports them):
{template_body}

{related_cards_section}

Based on these narrative scenes where the entity appears:
{evidence}

{f"Additional context: {additional_context}" if additional_context else ""}

Instructions:
- Return ONLY the complete markdown card — one frontmatter block (between --- delimiters) followed by the body.
- Do NOT include two frontmatter blocks. There must be exactly one opening --- and one closing ---.
- The frontmatter MUST include all required fields (type, card_id, name, rp, triggers).
- All card_id references (in connected_locations, known_by, etc.) MUST use the proper type prefix (loc_, npc_, char_, item_, org_, etc.).
- Only populate body sections where the evidence supports it. Omit sections you have no information for rather than leaving them as empty placeholders.
- Keep the same heading structure (## and ###) as the template for sections you do include.
- You MUST be consistent with the related cards above. Do not invent facts that contradict established lore."""

    response = await llm.generate(
        messages=[{"role": "user", "content": prompt}],
        model=llm.models.card_generation,
        temperature=0.4,
        max_tokens=6000,
    )

    cleaned = _sanitize_llm_card(response.content)

    return SuggestCardResponse(
        entity_name=entity_name,
        card_type=card_type,
        markdown=cleaned,
        model_used=response.model,
    )


@router.get("/gaps/{entity_name}/evidence", response_model=GapEvidenceResponse)
async def get_gap_evidence(
    entity_name: str,
    rp_folder: str = Query(...),
    branch: str = Query("main"),
    db: Database = Depends(get_db),
):
    """Return scene-grouped evidence for a card gap entity."""
    rows = await db.fetch_all(
        """SELECT exchange_number, chunk_text, mention_type
           FROM card_gap_exchanges
           WHERE LOWER(entity_name) = LOWER(?) AND rp_folder = ? AND branch = ?
           ORDER BY exchange_number""",
        [entity_name, rp_folder, branch],
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No gap evidence found for '{entity_name}' in '{rp_folder}'",
        )

    exchange_nums = [r["exchange_number"] for r in rows]
    scenes = group_into_scenes(exchange_nums)
    chunks_by_num = {r["exchange_number"]: r for r in rows}

    scene_list = []
    for scene in scenes:
        records = []
        for num in scene.exchanges:
            row = chunks_by_num.get(num)
            if row:
                records.append(GapExchangeRecord(
                    exchange_number=num,
                    chunk_text=row["chunk_text"],
                    mention_type=row["mention_type"],
                ))
        scene_list.append(SceneEvidence(
            start=scene.start,
            end=scene.end,
            exchange_count=scene.size,
            exchanges=records,
        ))

    return GapEvidenceResponse(
        entity_name=entity_name,
        rp_folder=rp_folder,
        total_mentions=len(rows),
        primary_mentions=sum(1 for r in rows if r["mention_type"] == "primary"),
        scenes=scene_list,
    )


@router.post("/audit", response_model=AuditCardsResponse)
async def audit_cards(
    body: AuditCardsRequest,
    db: Database = Depends(get_db),
):
    """Audit exchanges for entity mentions missing story cards.

    Quick mode: regex proper noun extraction + cross-reference vs story_cards.
    """
    import re

    rp_folder = body.rp_folder
    mode = body.mode
    session_id = body.session_id

    # Load exchanges
    if session_id:
        rows = await db.fetch_all(
            "SELECT id, assistant_response FROM exchanges WHERE rp_folder = ? AND session_id = ?",
            [rp_folder, session_id],
        )
    else:
        rows = await db.fetch_all(
            "SELECT id, assistant_response FROM exchanges WHERE rp_folder = ? ORDER BY id DESC LIMIT 50",
            [rp_folder],
        )

    # Load known entity names
    card_rows = await db.fetch_all(
        "SELECT LOWER(name) as name FROM story_cards WHERE rp_folder = ?",
        [rp_folder],
    )
    known_names = {r["name"] for r in card_rows}

    # Extract proper nouns (Title Case words that aren't common words)
    common_words = {
        "the", "and", "but", "for", "not", "you", "all", "can", "her", "was",
        "one", "our", "out", "his", "has", "its", "let", "say", "she", "too",
        "use", "him", "how", "man", "new", "now", "old", "see", "way",
        "who", "did", "get", "may", "any", "day",
    }

    entity_mentions: dict[str, list[int]] = {}
    proper_noun_re = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")

    for row in rows:
        text = row["assistant_response"]
        matches = proper_noun_re.findall(text)
        for match in matches:
            name_lower = match.lower()
            if name_lower in known_names:
                continue
            if name_lower in common_words:
                continue
            if len(match) < 3:
                continue
            entity_mentions.setdefault(match, [])
            if row["id"] not in entity_mentions[match]:
                entity_mentions[match].append(row["id"])

    gaps = [
        AuditGap(
            entity_name=name,
            mention_count=len(exchanges),
            exchanges=exchanges[:5],
        )
        for name, exchanges in sorted(
            entity_mentions.items(), key=lambda x: len(x[1]), reverse=True
        )
        if len(exchanges) >= 2  # Only report entities mentioned multiple times
    ]

    return AuditCardsResponse(
        mode=mode,
        gaps=gaps,
        total_exchanges_scanned=len(rows),
        total_gaps=len(gaps),
    )


# IMPORTANT: /schema and /validate must be defined BEFORE /{card_type} to avoid route shadowing
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


@router.post("/generate-name", response_model=GenerateCardNameResponse)
async def generate_card_name(
    body: GenerateCardNameRequest,
    rp_folder: str = Query(...),
    db: Database = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    vault_root: Path = Depends(get_vault_root),
    guidelines_svc=Depends(get_guidelines_service),
):
    """Generate name suggestions for a new story card using LLM."""
    card_type = body.card_type
    hints = body.hints
    count = min(body.count, 10)

    # Get existing card names to avoid duplicates
    existing_rows = await db.fetch_all(
        "SELECT name FROM story_cards WHERE card_type = ? AND rp_folder = ? LIMIT 20",
        [card_type, rp_folder],
    )
    existing_names = {r["name"].lower() for r in existing_rows}
    existing_list = [r["name"] for r in existing_rows]

    # Get tone/setting from guidelines
    tone_context = ""
    guidelines = guidelines_svc.get_guidelines(rp_folder)
    if guidelines:
        parts = []
        if guidelines.tone:
            tone_str = ", ".join(guidelines.tone) if isinstance(guidelines.tone, list) else guidelines.tone
            parts.append(f"Tone: {tone_str}")
        if guidelines.scene_pacing:
            parts.append(f"Pacing: {guidelines.scene_pacing}")
        if parts:
            tone_context = "\n".join(parts)

    prompt = f"""Generate {count} creative name suggestions for a new "{card_type}" story card.

{f"Setting/tone context: {tone_context}" if tone_context else ""}
{f"Hints from the user: {hints}" if hints else ""}
{f"Existing {card_type} names (avoid duplicates): {', '.join(existing_list)}" if existing_list else ""}

Return a JSON object with a single key "names" containing an array of {count} name strings.
Names should be evocative, fit the card type, and be distinct from existing names.
Return ONLY the JSON object, no other text."""

    try:
        response = await llm.generate(
            messages=[{"role": "user", "content": prompt}],
            model=llm.models.card_generation,
            temperature=0.8,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        parsed = safe_parse_json(response.content)
        raw_names = parsed.get("names", []) if isinstance(parsed, dict) else []
        # Dedup against existing
        suggestions = [n for n in raw_names if isinstance(n, str) and n.lower() not in existing_names][:count]
    except Exception:
        logger.warning("Card name generation failed, returning empty suggestions")
        suggestions = []

    return GenerateCardNameResponse(suggestions=suggestions, card_type=card_type)


async def _find_card_row(
    db: Database,
    card_type: str,
    name: str,
    *,
    rp_folder: str | None = None,
) -> dict | None:
    """Look up a card row by type + name, falling back to alias search.

    If *rp_folder* is given, also tries entity ID lookup (``rp_folder:key``).
    Returns the DB row dict or ``None``.
    """
    normalized = _normalize_url_name(name)

    # Direct lookup by type + normalized name
    row = await db.fetch_one(
        "SELECT * FROM story_cards WHERE card_type = ? AND LOWER(name) = ?",
        [card_type, normalized],
    )
    if row:
        return row

    # Try entity ID if rp_folder provided (useful for card_id references)
    if rp_folder:
        entity_id = f"{rp_folder}:{normalized}"
        row = await db.fetch_one(
            "SELECT * FROM story_cards WHERE id = ?", [entity_id]
        )
        if row:
            return row

    # Fallback: alias lookup (try both normalized forms)
    for alias_key in (normalized, normalize_key(name)):
        row = await db.fetch_one(
            """SELECT sc.* FROM story_cards sc
               JOIN entity_aliases ea ON sc.id = ea.entity_id
               WHERE sc.card_type = ? AND ea.alias = ?""",
            [card_type, alias_key],
        )
        if row:
            return row

    return None


async def _find_card_any_type(
    db: Database,
    name: str,
    rp_folder: str,
) -> dict | None:
    """Look up a card row by name in any type within an RP folder.

    Tries normalized name, entity ID, then alias lookup.
    """
    key = normalize_key(name)
    entity_id = f"{rp_folder}:{key}"

    row = await db.fetch_one(
        "SELECT * FROM story_cards WHERE rp_folder = ? AND (LOWER(name) = ? OR id = ?)",
        [rp_folder, key, entity_id],
    )
    if row:
        return row

    # Alias fallback
    row = await db.fetch_one(
        """SELECT sc.* FROM story_cards sc
           JOIN entity_aliases ea ON sc.id = ea.entity_id
           WHERE sc.rp_folder = ? AND ea.alias = ?""",
        [rp_folder, key],
    )
    return row


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
    llm: LLMClient = Depends(get_llm_client),
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
        sync_result = await _sync_reciprocal_relationships(
            new_card_name=body.name,
            new_card_id=card_id,
            new_card_frontmatter=frontmatter,
            new_card_body=body.content,
            rp_folder=rp_folder,
            db=db,
            indexer=indexer,
            vault_root=vault_root,
            llm=llm,
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
        await indexer._remove_entity(row["id"])

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

    from rp_engine.utils.frontmatter import parse_frontmatter
    _, current_body = parse_frontmatter(row["content"] or "")
    new_body = body.content if body.content is not None else current_body

    content = serialize_frontmatter(current_fm, new_body)
    file_path.write_text(content, encoding="utf-8")

    rp_folder = row["rp_folder"]
    await indexer.index_file(rp_folder, file_path)

    return await get_card(card_type, row["name"], db)


# ---------------------------------------------------------------------------
# Reciprocal relationship sync
# ---------------------------------------------------------------------------

_SUMMARY_KEYS = ("role", "age", "gender", "occupation", "species")


def _build_card_summary(name: str, frontmatter: dict, body: str, max_body: int = 2000) -> str:
    """Build a brief text summary of a card for LLM prompts."""
    parts = [f"Name: {name}"]
    for key in _SUMMARY_KEYS:
        val = frontmatter.get(key)
        if val:
            parts.append(f"{key.title()}: {val}")
    if body:
        _, body_text = parse_frontmatter(body) if body.startswith("---") else ("", body)
        if body_text:
            parts.append(f"\nCard content:\n{body_text[:max_body]}")
    return "\n".join(parts)

async def _sync_reciprocal_relationships(
    *,
    new_card_name: str,
    new_card_id: str,
    new_card_frontmatter: dict,
    new_card_body: str,
    rp_folder: str,
    db: Database,
    indexer: CardIndexer,
    vault_root: Path,
    llm: LLMClient,
) -> RelationshipSyncResult:
    """After creating a card, update referenced cards with reciprocal relationships.

    For each target in ``initial_relationships``:
    1. Look up the target card in the DB
    2. Check if it already has a reciprocal relationship back to new_card_id
    3. If not, use LLM to generate the reciprocal entry from the target's perspective
    4. Update the target card's frontmatter + body and reindex
    """
    from rp_engine.models.story_card import RelationshipSyncEntry

    result = RelationshipSyncResult(source_card=new_card_name)

    relationships = new_card_frontmatter.get("initial_relationships") or []
    if not relationships or not isinstance(relationships, list):
        return result

    # Build a summary of the new card for the LLM
    new_card_summary = _build_card_summary(new_card_name, new_card_frontmatter, new_card_body)

    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        target_id = rel.get("target", "")
        if not target_id:
            continue

        try:
            # Find the target card in the DB
            target_row = await _find_card_any_type(db, target_id, rp_folder)
            if not target_row:
                # Target card doesn't exist yet — skip (forward reference)
                continue

            # Check if target already has a reciprocal relationship to new_card_id
            target_fm = safe_parse_json(target_row["frontmatter"])
            existing_rels = target_fm.get("initial_relationships") or []
            already_linked = any(
                isinstance(r, dict) and normalize_key(r.get("target", "")) == normalize_key(new_card_id)
                for r in existing_rels
            )
            if already_linked:
                continue

            # Use LLM to generate reciprocal relationship
            reciprocal = await _generate_reciprocal_relationship(
                llm=llm,
                new_card_name=new_card_name,
                new_card_id=new_card_id,
                new_card_summary=new_card_summary,
                new_card_rel=rel,
                target_name=target_row["name"],
                target_fm=target_fm,
                target_body=(target_row["content"] or ""),
            )

            if not reciprocal:
                result.errors.append(f"LLM failed to generate reciprocal for {target_row['name']}")
                continue

            # Extract knowledge boundaries before adding to relationships
            kb_items = reciprocal.pop("doesnt_know", [])

            # Update the target card's frontmatter
            if not isinstance(existing_rels, list):
                existing_rels = []
            existing_rels.append(reciprocal)
            target_fm["initial_relationships"] = existing_rels

            # Apply knowledge boundary updates if the LLM found any
            if isinstance(kb_items, list) and kb_items:
                target_kb = target_fm.get("knowledge_boundaries") or {}
                if not isinstance(target_kb, dict):
                    target_kb = {}
                doesnt_know = target_kb.get("doesnt_know") or []
                if not isinstance(doesnt_know, list):
                    doesnt_know = []
                for item in kb_items:
                    if isinstance(item, str) and item not in doesnt_know:
                        doesnt_know.append(item)
                target_kb["doesnt_know"] = doesnt_know
                target_fm["knowledge_boundaries"] = target_kb

            # Write updated target card
            _, target_body = parse_frontmatter(target_row["content"] or "")
            updated_content = serialize_frontmatter(target_fm, target_body)
            target_file = vault_root / target_row["file_path"]
            target_file.write_text(updated_content, encoding="utf-8")
            await indexer.index_file(rp_folder, target_file)

            result.updated_cards.append(RelationshipSyncEntry(
                card_name=target_row["name"],
                card_type=target_row["card_type"],
                relationship_added=reciprocal,
            ))

        except Exception as e:
            result.errors.append(f"Failed to sync {target_id}: {e}")
            logger.exception("Relationship sync error for target %s", target_id)

    return result


async def _generate_reciprocal_relationship(
    *,
    llm: LLMClient,
    new_card_name: str,
    new_card_id: str,
    new_card_summary: str,
    new_card_rel: dict,
    target_name: str,
    target_fm: dict,
    target_body: str,
) -> dict | None:
    """Use LLM to generate a reciprocal relationship entry from the target's perspective."""
    target_summary = _build_card_summary(target_name, target_fm, target_body)

    prompt = f"""A new character card was just created. Generate the RECIPROCAL relationship entry that should be added to an existing character's card.

NEW CHARACTER (just created):
{new_card_summary}

NEW CHARACTER'S RELATIONSHIP TO EXISTING CHARACTER:
- target: {new_card_rel.get("target", "")}
- role: {new_card_rel.get("role", "")}
- trust: {new_card_rel.get("trust", 0)}
- status: "{new_card_rel.get("status", "")}"

EXISTING CHARACTER (needs a reciprocal entry):
{target_summary}

Generate the reciprocal relationship entry FROM {target_name}'s perspective TOWARD {new_card_name}.
The trust score should reflect {target_name}'s feelings toward {new_card_name} (may differ from the reverse).
The role should be the inverse (e.g., if new char sees target as "older brother", target sees new char as "younger sister").

Also check: does the new card's content reveal anything that {target_name} DOESN'T KNOW about?
If so, include a "doesnt_know" array of brief descriptions.

Return ONLY a JSON object with these keys:
{{"target": "{new_card_id}", "role": "<inverse role>", "trust": <integer -50 to 50>, "status": "<brief current state from {target_name}'s perspective>", "doesnt_know": ["<thing {target_name} doesn't know>"] }}

If there are no knowledge gaps, set "doesnt_know" to an empty array [].
Return ONLY the JSON, no other text."""

    try:
        response = await llm.generate(
            messages=[{"role": "user", "content": prompt}],
            model=llm.models.card_generation,
            temperature=0.3,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        parsed = safe_parse_json(response.content)
        if isinstance(parsed, dict) and "target" in parsed:
            # Ensure target is set correctly
            parsed["target"] = new_card_id
            # Clamp trust
            if "trust" in parsed:
                parsed["trust"] = max(-50, min(50, int(parsed["trust"])))
            # doesnt_know is extracted separately, not part of the relationship entry
            return parsed
    except Exception as e:
        logger.warning("Reciprocal relationship generation failed: %s", e)

    return None


