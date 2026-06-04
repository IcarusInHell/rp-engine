# RP Engine

REST API for managing roleplay sessions. **Smart API, dumb client** — the client sends raw text, the API handles entity extraction, context retrieval, NPC reactions, state management, and response analysis.

## Stack

- **Backend:** Python 3.12+ / FastAPI / SQLite (WAL mode) / LanceDB
- **Frontend:** Svelte 5 / SvelteKit / Tailwind v4 / Bits UI
- **LLM:** OpenRouter (configurable models per function)
- **Intelligence:** Pattern learning for NPC behavior and writing style

## Quick Start

```bash
# Install
cd rp-engine
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env: set OPENROUTER_API_KEY=your-key-here

# Run server
uvicorn rp_engine.main:app --reload --port 3000

# API docs
open http://localhost:3000/docs
```

### Frontend

```bash
cd rp-engine/frontend
npm install
npm run dev
```

## How It Works

The core loop per RP turn:

1. **`POST /api/context`** — Send the user's message. The API extracts entities, searches cards and memory, resolves the entity graph, and builds NPC briefs. Returns everything the LLM needs.
2. **LLM generates** the RP response using the returned context.
3. **`POST /api/exchanges`** — Save the exchange. Returns immediately. An async analysis pipeline extracts state changes, updates trust, fires thread alerts, and records card gaps in the background.

Alternatively, use **`POST /api/chat`** to let the API handle the full pipeline (context → prompt → LLM → save) with optional SSE streaming.

## API Endpoints (93+)

### Core Flow

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/context` | POST | Main intelligence endpoint — entities, cards, NPC briefs, state, alerts |
| `/api/context/resolve` | POST | Entity graph traversal (BFS) |
| `/api/context/continuity` | GET | Condensed continuity brief |
| `/api/context/guidelines` | GET/PUT | RP guidelines (reads/writes `Story_Guidelines.md`) |
| `/api/chat` | POST | Full chat pipeline with optional SSE streaming |
| `/api/exchanges` | POST | Save exchange (triggers async analysis) |
| `/api/exchanges` | GET | Retrieve past exchanges |

### NPCs & Trust

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/npc/react` | POST | Single NPC reaction (LLM) |
| `/api/npc/react-batch` | POST | Batch NPC reactions |
| `/api/npc/{name}/trust` | GET | Trust score, stage, and history |
| `/api/npcs` | GET | List all NPCs with archetypes and trust |

### Story Cards

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/cards` | GET | List story cards (by type, filtered) |
| `/api/cards/{type}` | POST/PUT | Create or update card `.md` files |
| `/api/cards/audit` | POST | Scan for missing cards |
| `/api/cards/suggest` | POST | Generate draft card (LLM) |
| `/api/cards/suggest/evidence` | GET | Scene-aware evidence for suggestions |

### State & World

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/state` | GET | Full state snapshot (characters, relationships, scene, events) |
| `/api/sessions` | POST | Start session |
| `/api/sessions/{id}/end` | POST | End session (generates summary) |
| `/api/sessions/{id}/recap` | GET | "Previously on..." recap |
| `/api/threads` | GET/POST | Plot thread tracking |
| `/api/threads/{id}/evidence` | GET | Evidence linked to thread |
| `/api/branches` | GET/POST | Branch management and checkpoints |
| `/api/timeline` | POST | Advance scene time |
| `/api/custom-state` | GET/POST/PUT/DELETE | Custom state variables |
| `/api/continuity` | GET/POST | Continuity contradiction checking |
| `/api/triggers` | GET/POST | Situational trigger evaluation |

### Intelligence & Search

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/analyze/{id}` | GET | Exchange analysis results |
| `/api/vectors` | GET/POST/DELETE | Vector search and management |
| `/api/writing` | GET/POST | Writing style intelligence |
| `/api/config` | GET/PUT | Runtime configuration |

Full interactive documentation at `http://localhost:3000/docs`.

## Architecture

```
Client (Svelte frontend / MCP / any HTTP client)
  │
  ▼
FastAPI (18 routers, 93+ endpoints)
  │
  ├─ Context Engine ──── Entity Extraction → Hybrid Search → Graph Resolution → NPC Briefs
  │                       (heuristic)        (BM25 + vector    (BFS over entity    (deterministic
  │                                           + RRF fusion)     connections)        brief builder)
  │
  ├─ Analysis Pipeline ── Response Analyzer → State Manager → Thread Tracker → Card Gap Detector
  │   (async, serialized)  (LLM extraction)   (CoW updates)   (counter/alerts)  (missing entities)
  │
  ├─ Pattern Intelligence
  │   ├─ NPC Intelligence ── behavioral signatures, archetype classification, feedback learning
  │   └─ Writing Intelligence ── style patterns, mode/register tracking, feedback learning
  │
  ├─ Chat Manager ──── Context → Prompt Assembly → LLM → Save (streaming or sync)
  │
  └─ Storage
      ├─ SQLite (WAL) ── state, exchanges, trust, threads, branches (CoW, branch-isolated)
      ├─ LanceDB ──────── vector embeddings for semantic search
      └─ Markdown ─────── story cards (source of truth, edited in Obsidian)
```

### Key Design Decisions

- **Copy-on-Write branching** — State entries are immutable snapshots resolved through branch ancestry. Every stateful table has a `branch` column.
- **Async write queue** — Writes are serialized, reads are parallel. Exchange save returns immediately.
- **Hybrid search** — BM25 + vector search fused via Reciprocal Rank Fusion (RRF).
- **Trust is ledger-based** — Card `.md` files define baselines. Runtime changes are recorded in `trust_modifications` and resolved through ancestry. Cards are never modified by the API.
- **Two-pass card indexing** — Story cards are parsed in two passes to build the entity connection graph.
- **Service container** — All services are wired via dependency injection at startup.

## Frontend (Svelte 5)

12+ pages built with Svelte 5 runes, SvelteKit, Tailwind v4, and Bits UI:

| Page | Purpose |
|------|---------|
| `/[rp]/chat` | Main chat interface |
| `/[rp]/dashboard` | Interactive force-graph visualization |
| `/[rp]/cards` | Story card browser and editor |
| `/[rp]/npc` | NPC management and briefing |
| `/[rp]/threads` | Plot thread tracker |
| `/[rp]/branches` | Branch management |
| `/[rp]/sessions` | Session history |
| `/[rp]/context` | Context/intelligence viewer |
| `/[rp]/generations` | Card generation history |
| `/[rp]/settings` | RP-level settings |

## MCP Integration

14 MCP tools for Claude Code / Claude Desktop integration:

```json
{
  "mcpServers": {
    "rp-engine": {
      "command": "python",
      "args": ["rp-engine/rp_engine/mcp_wrapper.py"],
      "env": {
        "RP_ENGINE_URL": "http://localhost:3000",
        "RP_FOLDER": "MyRP"
      }
    }
  }
}
```

Tools: `get_scene_context`, `save_exchange`, `get_npc_reaction`, `batch_npc_reactions`, `check_trust_level`, `list_npcs`, `get_state`, `get_continuity_brief`, `resolve_context`, `audit_story_cards`, `suggest_card`, `list_existing_cards`, `create_card`, `end_session`

## Configuration

Config via `config.yaml` with environment variable overrides (`RP_ENGINE_` prefix):

| Area | Key Settings |
|------|-------------|
| Server | `host`, `port`, `cors_origins`, `lan_access` |
| Paths | `vault_root` (Obsidian vault), `db_path` |
| LLM | `provider`, `api_key`, per-function model selection (`npc_reactions`, `response_analysis`, `card_generation`, `embeddings`) |
| Context | `max_documents`, `max_graph_hops`, `stale_threshold_turns`, `max_past_exchanges` |
| Chat | `exchange_window`, `model`, `temperature`, `max_tokens` |
| Search | `vector_weight`, `bm25_weight`, `similarity_threshold`, `chunk_size`, `embedding_dimension` |
| Trust | `session_max_gain` (+8), `session_max_loss` (-15), range -50 to +50, 8 stages |
| NPC | `history_search_limit`, `history_min_score` |
| Continuity | `enabled` (off by default), `max_search_results`, `min_similarity` |

## Running Tests

```bash
pytest                     # Full suite (~700 tests)
pytest -x                  # Stop on first failure
pytest -k "test_trust"     # By name pattern
```

## Docs

- [Client Integration Guide](docs/client-guide.md) — Per-turn flow and Python example
- [System Prompt Guide](docs/system-prompt-guide.md) — What to include in LLM system prompts
- [CLAUDE.md Template](docs/rp-claude-md-template.md) — Template for RP sessions using MCP tools

Internal developer docs: `project/docs/claude-ref/INDEX.md`

## License

Private project — not licensed for redistribution.
