#!/usr/bin/env python3
"""
MCP wrapper server for rp-engine REST API.

Proxies 16 MCP tools to the rp-engine FastAPI backend via httpx.
All intelligence lives in the API -- this is a thin translation layer
from MCP tool calls to HTTP requests.

Usage:
    python -m rp_engine.mcp_wrapper

Environment variables:
    RP_ENGINE_URL  - Base URL of the rp-engine API (default: http://localhost:3000)
    RP_FOLDER      - Default RP folder name (optional)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

if sys.platform == "win32" and sys.version_info < (3, 16):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL = os.environ.get("RP_ENGINE_URL", "http://localhost:3000")

server = Server("rp-engine")

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def api_get(path: str, params: dict | None = None) -> dict:
    """Send a GET request to the rp-engine API."""
    async with httpx.AsyncClient(base_url=API_URL, timeout=60.0) as client:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


async def api_post(path: str, json_body: dict, params: dict | None = None) -> dict:
    """Send a POST request to the rp-engine API."""
    async with httpx.AsyncClient(base_url=API_URL, timeout=60.0) as client:
        resp = await client.post(path, json=json_body, params=params)
        resp.raise_for_status()
        return resp.json()


def _rp_params(args: dict) -> dict:
    """Extract common rp_folder/branch query params."""
    params = {}
    rp_folder = args.get("rp_folder") or os.environ.get("RP_FOLDER", "")
    if rp_folder:
        params["rp_folder"] = rp_folder
    branch = args.get("branch", "main")
    if branch:
        params["branch"] = branch
    return params


def _json_result(data) -> list[TextContent]:
    """Wrap an API response as a JSON TextContent list."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return [TextContent(type="text", text=text)]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="get_scene_context",
        description=(
            "Get all context needed for the current RP turn. Call this FIRST at the "
            "start of every RP exchange. Returns: relevant story cards, NPC behavioral "
            "briefs, scene state, character conditions, plot thread alerts, triggered "
            "notes, and current_exchange number (needed for save_exchange). The API does "
            "all intelligence -- entity extraction, keyword matching, graph traversal, "
            "semantic search. You just send the raw user message."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_message": {
                    "type": "string",
                    "description": "The raw user message for this RP turn.",
                },
                "last_response": {
                    "type": "string",
                    "description": "The assistant's previous RP response (optional, improves context).",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for trust cap tracking.",
                },
                "include_npc_reactions": {
                    "type": "boolean",
                    "description": "Include inline NPC reactions in context response.",
                },
                "skip_guidelines": {
                    "type": "boolean",
                    "description": "Omit guidelines from the response (use when guidelines are already in the system prompt).",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["user_message"],
        },
    ),
    Tool(
        name="save_exchange",
        description=(
            "Save a completed RP exchange to the database. Call this after EVERY RP "
            "narrative response to persist the exchange. "
            "IMPORTANT: assistant_response must contain ONLY the RP narrative "
            "text -- strip all thinking blocks, tool call results, and meta commentary. "
            "Do NOT call this for meta discussions, system questions, or out-of-character "
            "chat. Pass exchange_number = current_exchange + 1 from the get_scene_context "
            "response."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "user_message": {"type": "string", "description": "The user's RP message."},
                "assistant_response": {
                    "type": "string",
                    "description": "The assistant's RP narrative response (clean text only).",
                },
                "exchange_number": {
                    "type": "integer",
                    "description": "Exchange number (current_exchange + 1 from get_scene_context).",
                },
                "session_id": {"type": "string", "description": "Session ID (optional)."},
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["user_message", "assistant_response", "exchange_number"],
        },
    ),
    Tool(
        name="get_npc_reaction",
        description=(
            "Get a single NPC's full reaction to a scene moment via LLM. Returns: "
            "internal monologue, physical action, dialogue, emotional undercurrent, "
            "trust shift. Only call this for important NPC moments -- for routine NPC "
            "presence, use the npc_briefs from get_scene_context instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "npc_name": {"type": "string", "description": "Name of the NPC."},
                "scene_prompt": {
                    "type": "string",
                    "description": "What just happened in the scene that the NPC should react to.",
                },
                "pov_character": {
                    "type": "string",
                    "description": "Name of the POV character (default: Lilith).",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["npc_name", "scene_prompt"],
        },
    ),
    Tool(
        name="batch_npc_reactions",
        description=(
            "Get reactions from multiple NPCs in a single call. More efficient than "
            "calling get_npc_reaction multiple times."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "npc_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of NPC names to get reactions from.",
                },
                "scene_prompt": {
                    "type": "string",
                    "description": "What just happened in the scene.",
                },
                "pov_character": {
                    "type": "string",
                    "description": "Name of the POV character (default: Lilith).",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["npc_names", "scene_prompt"],
        },
    ),
    Tool(
        name="check_trust_level",
        description=(
            "Check the current trust score, stage, and modification history between "
            "an NPC and a target character."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "npc_name": {"type": "string", "description": "Name of the NPC."},
                "target_name": {
                    "type": "string",
                    "description": "Name of the character to check trust with (default: Lilith).",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["npc_name"],
        },
    ),
    Tool(
        name="list_npcs",
        description=(
            "List all NPCs in the current RP with their archetypes, importance levels, "
            "and current trust scores."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_state",
        description=(
            "Get a full state snapshot: all characters (locations, conditions, emotional "
            "states), relationships (trust scores), scene context, and recent events."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": [],
        },
    ),
    Tool(
        name="get_continuity_brief",
        description=(
            "Get a condensed continuity summary. Good to call every 5-8 turns or when "
            "the scene shifts significantly."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scene_summary": {
                    "type": "string",
                    "description": "Optional brief description of the current scene.",
                },
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional areas to prioritize (e.g. 'knowledge_boundaries', 'plot_threads').",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": [],
        },
    ),
    Tool(
        name="resolve_context",
        description=(
            "Explicitly resolve entity connections via graph traversal. For edge cases "
            "where get_scene_context doesn't surface the right connections."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Key entities/concepts to resolve (names, places, events).",
                },
                "scene_description": {
                    "type": "string",
                    "description": "What's happening in the scene.",
                },
                "max_hops": {
                    "type": "integer",
                    "description": "How many relationship hops to follow (1-3, default: 2).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 15).",
                },
                "skip_guidelines": {
                    "type": "boolean",
                    "description": "Omit guidelines from the response (use when guidelines are already in the system prompt).",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["keywords"],
        },
    ),
    Tool(
        name="audit_story_cards",
        description=(
            "Scan recent exchanges for entity mentions that don't have story cards. "
            "Returns gap report."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "mode": {
                    "type": "string",
                    "description": "Scan mode: 'quick' or 'deep' (default: quick).",
                },
                "session_id": {"type": "string", "description": "Session to scan (optional)."},
            },
            "required": [],
        },
    ),
    Tool(
        name="suggest_card",
        description=(
            "Generate a draft story card for an entity using LLM and exchange evidence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_name": {
                    "type": "string",
                    "description": "Name of the entity to create a card for.",
                },
                "card_type": {
                    "type": "string",
                    "description": "Type of story card (e.g. 'npc', 'location', 'memory', 'secret').",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "additional_context": {
                    "type": "string",
                    "description": "Optional extra instructions or context for card generation.",
                },
                "related_entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names of existing cards to load as context. The LLM will see their full content and stay consistent with them.",
                },
            },
            "required": ["entity_name", "card_type"],
        },
    ),
    Tool(
        name="list_existing_cards",
        description="List all story cards, optionally filtered by type.",
        inputSchema={
            "type": "object",
            "properties": {
                "card_type": {
                    "type": "string",
                    "description": "Filter by card type (e.g. 'character', 'npc', 'location').",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
            },
            "required": [],
        },
    ),
    Tool(
        name="end_session",
        description=(
            "End the current RP session. Returns accumulated session data: significant "
            "events, trust changes, new entities, plot thread status. Use this data to "
            "write chapter summaries and memory/knowledge cards, then call create_card "
            "to persist them."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID to end.",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="list_sessions",
        description=(
            "List RP sessions, optionally filtered by rp_folder. Returns session "
            "IDs, start/end times, and status (active or ended)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rp_folder": {"type": "string", "description": "Filter by RP folder name."},
                "branch": {"type": "string", "description": "Filter by branch name."},
            },
            "required": [],
        },
    ),
    Tool(
        name="create_session",
        description=(
            "Start a new RP session. Returns the session ID needed for "
            "save_exchange and end_session calls."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "rp_folder": {"type": "string", "description": "RP folder name."},
                "branch": {"type": "string", "description": "Branch name (default: main)."},
            },
            "required": ["rp_folder"],
        },
    ),
    Tool(
        name="create_card",
        description=(
            "Create a new story card. Writes the .md file and indexes it. Use after "
            "end_session to persist chapter summaries, memory cards, and knowledge cards."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "card_type": {
                    "type": "string",
                    "description": "Type of card (e.g. 'memory', 'knowledge', 'chapter', 'secret').",
                },
                "name": {
                    "type": "string",
                    "description": "Name/title of the card.",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content of the card (body text, after frontmatter).",
                },
                "frontmatter": {
                    "type": "object",
                    "description": "Optional YAML frontmatter fields as a dict.",
                },
                "rp_folder": {"type": "string", "description": "RP folder name."},
            },
            "required": ["card_type", "name", "content"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def handle_get_scene_context(args: dict) -> list[TextContent]:
    """POST /api/context -- get all context for the current RP turn."""
    body: dict = {"user_message": args["user_message"]}
    if args.get("last_response"):
        body["last_response"] = args["last_response"]
    params = _rp_params(args)
    if args.get("session_id"):
        params["session_id"] = args["session_id"]
    if args.get("include_npc_reactions") is not None:
        params["include_npc_reactions"] = str(args["include_npc_reactions"]).lower()
    if args.get("skip_guidelines"):
        params["skip_guidelines"] = "true"
    data = await api_post("/api/context", json_body=body, params=params)
    return _json_result(data)


async def handle_save_exchange(args: dict) -> list[TextContent]:
    """POST /api/exchanges -- save a completed RP exchange."""
    body: dict = {
        "user_message": args["user_message"],
        "assistant_response": args["assistant_response"],
        "exchange_number": args["exchange_number"],
    }
    if args.get("session_id"):
        body["session_id"] = args["session_id"]
    # exchanges endpoint doesn't use rp_folder/branch query params;
    # the session resolves those.
    data = await api_post("/api/exchanges", json_body=body)
    return _json_result(data)


async def handle_get_npc_reaction(args: dict) -> list[TextContent]:
    """POST /api/npc/react -- get a single NPC reaction."""
    body: dict = {
        "npc_name": args["npc_name"],
        "scene_prompt": args["scene_prompt"],
    }
    if args.get("pov_character"):
        body["pov_character"] = args["pov_character"]
    params = _rp_params(args)
    data = await api_post("/api/npc/react", json_body=body, params=params)
    return _json_result(data)


async def handle_batch_npc_reactions(args: dict) -> list[TextContent]:
    """POST /api/npc/react-batch -- batch NPC reactions."""
    body: dict = {
        "npc_names": args["npc_names"],
        "scene_prompt": args["scene_prompt"],
    }
    if args.get("pov_character"):
        body["pov_character"] = args["pov_character"]
    params = _rp_params(args)
    data = await api_post("/api/npc/react-batch", json_body=body, params=params)
    return _json_result(data)


async def handle_check_trust_level(args: dict) -> list[TextContent]:
    """GET /api/npc/{name}/trust -- check trust level."""
    npc_name = args["npc_name"]
    params = _rp_params(args)
    if args.get("target_name"):
        params["target_name"] = args["target_name"]
    data = await api_get(f"/api/npc/{npc_name}/trust", params=params)
    return _json_result(data)


async def handle_list_npcs(args: dict) -> list[TextContent]:
    """GET /api/npcs -- list all NPCs."""
    params = _rp_params(args)
    data = await api_get("/api/npcs", params=params)
    return _json_result(data)


async def handle_get_state(args: dict) -> list[TextContent]:
    """GET /api/state -- full state snapshot."""
    params = _rp_params(args)
    data = await api_get("/api/state", params=params)
    return _json_result(data)


async def handle_get_continuity_brief(args: dict) -> list[TextContent]:
    """GET /api/context/continuity -- condensed continuity summary."""
    params = _rp_params(args)
    if args.get("scene_summary"):
        params["scene_summary"] = args["scene_summary"]
    if args.get("focus_areas"):
        # Pass as comma-separated for query param
        params["focus_areas"] = ",".join(args["focus_areas"])
    data = await api_get("/api/context/continuity", params=params)
    return _json_result(data)


async def handle_resolve_context(args: dict) -> list[TextContent]:
    """POST /api/context/resolve -- explicit entity resolution."""
    body: dict = {"keywords": args["keywords"]}
    if args.get("scene_description"):
        body["scene_description"] = args["scene_description"]
    if args.get("max_hops") is not None:
        body["max_hops"] = args["max_hops"]
    if args.get("max_results") is not None:
        body["max_results"] = args["max_results"]
    params = _rp_params(args)
    data = await api_post("/api/context/resolve", json_body=body, params=params)
    return _json_result(data)


async def handle_audit_story_cards(args: dict) -> list[TextContent]:
    """POST /api/cards/audit -- scan for missing story cards."""
    body: dict = {}
    # rp_folder goes in body for this endpoint
    rp_folder = args.get("rp_folder") or os.environ.get("RP_FOLDER", "")
    if rp_folder:
        body["rp_folder"] = rp_folder
    if args.get("mode"):
        body["mode"] = args["mode"]
    if args.get("session_id"):
        body["session_id"] = args["session_id"]
    data = await api_post("/api/cards/audit", json_body=body)
    return _json_result(data)


async def handle_suggest_card(args: dict) -> list[TextContent]:
    """POST /api/cards/suggest -- generate a draft story card."""
    body: dict = {
        "entity_name": args["entity_name"],
        "card_type": args["card_type"],
    }
    # rp_folder goes in body for this endpoint
    rp_folder = args.get("rp_folder") or os.environ.get("RP_FOLDER", "")
    if rp_folder:
        body["rp_folder"] = rp_folder
    if args.get("additional_context"):
        body["additional_context"] = args["additional_context"]
    if args.get("related_entities"):
        body["related_entities"] = args["related_entities"]
    data = await api_post("/api/cards/suggest", json_body=body)
    return _json_result(data)


async def handle_list_existing_cards(args: dict) -> list[TextContent]:
    """GET /api/cards -- list story cards."""
    params = {}
    if args.get("card_type"):
        params["card_type"] = args["card_type"]
    rp_folder = args.get("rp_folder") or os.environ.get("RP_FOLDER", "")
    if rp_folder:
        params["rp_folder"] = rp_folder
    data = await api_get("/api/cards", params=params)
    return _json_result(data)


async def handle_end_session(args: dict) -> list[TextContent]:
    """POST /api/sessions/{id}/end -- end the current RP session."""
    session_id = args["session_id"]
    data = await api_post(f"/api/sessions/{session_id}/end", json_body={})
    return _json_result(data)


async def handle_list_sessions(args: dict) -> list[TextContent]:
    """GET /api/sessions -- list RP sessions."""
    params = {}
    rp_folder = args.get("rp_folder") or os.environ.get("RP_FOLDER", "")
    if rp_folder:
        params["rp_folder"] = rp_folder
    if args.get("branch"):
        params["branch"] = args["branch"]
    data = await api_get("/api/sessions", params=params)
    return _json_result(data)


async def handle_create_session(args: dict) -> list[TextContent]:
    """POST /api/sessions -- start a new RP session."""
    body: dict = {
        "rp_folder": args["rp_folder"],
        "branch": args.get("branch", "main"),
    }
    data = await api_post("/api/sessions", json_body=body)
    return _json_result(data)


async def handle_create_card(args: dict) -> list[TextContent]:
    """POST /api/cards/{card_type} -- create a new story card."""
    card_type = args["card_type"]
    body: dict = {
        "name": args["name"],
        "content": args["content"],
    }
    if args.get("frontmatter"):
        body["frontmatter"] = args["frontmatter"]
    params = {}
    rp_folder = args.get("rp_folder") or os.environ.get("RP_FOLDER", "")
    if rp_folder:
        params["rp_folder"] = rp_folder
    data = await api_post(f"/api/cards/{card_type}", json_body=body, params=params)
    return _json_result(data)


# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------

# Handler dispatch table — import at module scope after all handler defs
from collections.abc import Callable  # noqa: E402

_HANDLERS: dict[str, Callable] = {
    "get_scene_context": handle_get_scene_context,
    "save_exchange": handle_save_exchange,
    "get_npc_reaction": handle_get_npc_reaction,
    "batch_npc_reactions": handle_batch_npc_reactions,
    "check_trust_level": handle_check_trust_level,
    "list_npcs": handle_list_npcs,
    "get_state": handle_get_state,
    "get_continuity_brief": handle_get_continuity_brief,
    "resolve_context": handle_resolve_context,
    "audit_story_cards": handle_audit_story_cards,
    "suggest_card": handle_suggest_card,
    "list_existing_cards": handle_list_existing_cards,
    "end_session": handle_end_session,
    "list_sessions": handle_list_sessions,
    "create_session": handle_create_session,
    "create_card": handle_create_card,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return all available tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a tool call to its handler."""
    try:
        handler = _HANDLERS.get(name)
        if handler is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        return await handler(arguments)
    except httpx.HTTPStatusError as e:
        error_body = e.response.text
        return [TextContent(type="text", text=f"API error ({e.response.status_code}): {error_body}")]
    except httpx.ConnectError:
        return [TextContent(type="text", text=f"Connection error: could not reach rp-engine at {API_URL}. Is the server running?")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e!s}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
