"""Card analytics endpoints: connections graph, gap evidence, gap audit."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from rp_engine.database import Database
from rp_engine.dependencies import get_db
from rp_engine.models.story_card import (
    AuditCardsRequest,
    AuditCardsResponse,
    AuditGap,
    GapEvidenceResponse,
    GapExchangeRecord,
    SceneEvidence,
)
from rp_engine.utils.scene_detection import group_into_scenes

router = APIRouter(prefix="/api/cards", tags=["cards"])

# Bug D: compile once at module load, not per-request inside audit_cards.
# Matches Title Case proper nouns (single or multi-word).
_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")

# Common short Title-Case words to ignore (sentence-start "The", "She", etc.)
_COMMON_WORDS = {
    "the", "and", "but", "for", "not", "you", "all", "can", "her", "was",
    "one", "our", "out", "his", "has", "its", "let", "say", "she", "too",
    "use", "him", "how", "man", "new", "now", "old", "see", "way",
    "who", "did", "get", "may", "any", "day",
}


@router.get("/connections")
async def get_connections(
    rp_folder: str = Query(...),
    db: Database = Depends(get_db),
):
    """Return graph data (nodes + edges) for all cards in an RP.

    Used by the Dashboard entity graph. Queries entity_connections and
    maps entity IDs back to card names. entity_connections is global by
    design (no branch column) — this is structural card-frontmatter data,
    not runtime trust state (see GET /api/state/relationship-graph for that).
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
        # Bug B: scope the "50 most recent" fallback to the requested branch so
        # branched RPs don't scan exchanges across all branches.
        rows = await db.fetch_all(
            "SELECT id, assistant_response FROM exchanges WHERE rp_folder = ? AND branch = ? ORDER BY id DESC LIMIT 50",
            [rp_folder, body.branch],
        )

    # Load known entity names
    card_rows = await db.fetch_all(
        "SELECT LOWER(name) as name FROM story_cards WHERE rp_folder = ?",
        [rp_folder],
    )
    known_names = {r["name"] for r in card_rows}

    entity_mentions: dict[str, list[int]] = {}

    for row in rows:
        text = row["assistant_response"]
        matches = _PROPER_NOUN_RE.findall(text)
        for match in matches:
            name_lower = match.lower()
            if name_lower in known_names:
                continue
            if name_lower in _COMMON_WORDS:
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
