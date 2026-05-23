"""Agent SDK chat endpoint — Claude as narrator via Agent SDK + MCP tools.

POST /api/chat/agent — streaming SSE endpoint. Claude calls rp-engine MCP tools
(get_scene_context, save_exchange, etc.) autonomously. Same SSE format as
/api/chat for frontend compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from rp_engine.constants.agent_prompts import build_system_prompt
from rp_engine.database import Database
from rp_engine.dependencies import get_db, get_guidelines_service, require_chat_mode
from rp_engine.services.guidelines_service import GuidelinesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat/agent", tags=["agent-chat"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class AgentChatRequest(BaseModel):
    user_message: str
    session_id: str | None = None


class AgentStatusResponse(BaseModel):
    available: bool
    reason: str | None = None


def _sse_token(chunk: str) -> str:
    return f'data: {{"type": "token", "content": {json.dumps(chunk)}}}\n\n'


def _sse_done(**fields: int | str) -> str:
    payload = {"type": "done", **fields}
    return f"data: {json.dumps(payload)}\n\n"


def _sse_error(message: str) -> str:
    return f'data: {{"type": "error", "content": {json.dumps(message)}}}\n\n'


_sdk_ready = False  # cached after first successful resolution


def _ensure_sdk() -> str | None:
    """Ensure claude-agent-sdk is importable. Returns error message or None.

    The SDK may be installed in the system Python but not in this venv.
    If a direct import fails, we ask a system Python for its full sys.path
    (which includes .pth expansions like pywin32) and merge those paths
    into our process so the SDK and all its transitive deps resolve.
    """
    global _sdk_ready

    if _sdk_ready:
        return None

    # Try direct import first (works if SDK is in venv or sys.path already patched)
    try:
        __import__("claude_agent_sdk")
        _sdk_ready = True
        return None
    except Exception:
        pass

    # Ask a system Python for its full sys.path (includes .pth expansions).
    # When running inside an activated venv, shutil.which("python") returns
    # the venv's python. We also probe known system Python locations directly.
    venv_real = os.path.realpath(sys.executable)
    candidates: list[str] = []
    for cmd in ("python", "python3", "py"):
        p = shutil.which(cmd)
        if p:
            candidates.append(p)
    # Also probe common system Python locations (venv activation hides these)
    for p in (r"C:\Python314\python.exe", r"C:\Python313\python.exe",
              r"C:\Python312\python.exe",
              os.path.expanduser(r"~\AppData\Local\Programs\Python\Python314\python.exe"),
              os.path.expanduser(r"~\AppData\Local\Programs\Python\Python313\python.exe"),
              os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312\python.exe"),
              r"C:\Windows\py.exe"):
        if os.path.isfile(p) and p not in candidates:
            candidates.append(p)
    for py_path in candidates:
        # Skip if this resolves to our own venv python
        if os.path.realpath(py_path) == venv_real:
            continue
        try:
            result = subprocess.run(
                [py_path, "-c",
                 "import sys,json; import claude_agent_sdk; print(json.dumps(sys.path))"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                continue
            system_paths = json.loads(result.stdout.strip())
            for p in system_paths:
                if p and p not in sys.path:
                    sys.path.append(p)
            # Verify the import now works
            __import__("claude_agent_sdk")
            _sdk_ready = True
            logger.info(
                "Resolved claude-agent-sdk from system Python (%s), "
                "added %d paths to sys.path",
                py_path, len(system_paths),
            )
            return None
        except ImportError:
            # Paths added but import still fails — undo and try next
            continue
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue

    # Nothing worked — give a helpful error
    if shutil.which("claude"):
        return (
            "claude CLI found in PATH but claude-agent-sdk Python package "
            "is not reachable. Install it system-wide: pip install claude-agent-sdk"
        )
    return (
        "Claude Code not found. Install: "
        "npm install -g @anthropic-ai/claude-code && pip install claude-agent-sdk"
    )


@router.get("/status", response_model=AgentStatusResponse)
async def agent_status():
    """Check if Agent SDK chat is available."""
    err = _ensure_sdk()
    if err:
        return AgentStatusResponse(available=False, reason=err)
    return AgentStatusResponse(available=True)



async def _build_guidelines_text(
    rp_folder: str,
    guidelines_svc: GuidelinesService,
) -> str:
    """Build a readable text from guidelines for the system prompt."""
    from rp_engine.constants.prompt_guidance import (
        RESPONSE_LENGTH_GUIDANCE,
        SCENE_PACING_GUIDANCE,
        build_content_boundaries,
        build_pov_section,
        build_user_narrative_guidance,
    )

    resp = guidelines_svc.get_guidelines(rp_folder)
    if resp is None:
        return ""

    parts: list[str] = []

    # Compact metadata with expanded guidance
    meta: list[str] = []
    if resp.pov_mode:
        meta.append(f"POV: {resp.pov_mode}")
    if resp.narrative_voice:
        meta.append(f"Voice: {resp.narrative_voice}")
    if resp.tense:
        meta.append(f"Tense: {resp.tense}")

    pacing = resp.scene_pacing
    if pacing:
        desc = SCENE_PACING_GUIDANCE.get(pacing, pacing)
        meta.append(f"Pacing: {pacing} ({desc})")

    length = resp.response_length
    if length:
        desc = RESPONSE_LENGTH_GUIDANCE.get(length, length)
        meta.append(f"Length: {length} ({desc})")

    tone = resp.tone
    if tone:
        meta.append(f"Tone: {', '.join(tone) if isinstance(tone, list) else tone}")

    dual_chars = getattr(resp, "dual_characters", None) or []
    if dual_chars:
        meta.append(f"Dual: {', '.join(dual_chars)}")

    if meta:
        parts.append(" | ".join(meta))

    # POV instructions
    pov = build_pov_section(
        pov_mode=resp.pov_mode,
        pov_character=getattr(resp, "pov_character", None) or None,
        dual_characters=dual_chars,
    )
    if pov:
        parts.append(pov)

    # Content boundaries
    boundaries = build_content_boundaries(
        sensitive_themes=resp.sensitive_themes,
        hard_limits=resp.hard_limits,
    )
    if boundaries:
        parts.append(boundaries)

    # User-narrative integration
    narrative = build_user_narrative_guidance(
        integrate_user_narrative=bool(getattr(resp, "integrate_user_narrative", False)),
        preserve_user_details=bool(getattr(resp, "preserve_user_details", False)),
    )
    if narrative:
        parts.append(narrative)

    # Body (user's custom guidelines text)
    if resp.body:
        parts.append(resp.body)

    return "\n\n".join(parts)


async def _agent_stream(
    user_message: str,
    rp_folder: str,
    branch: str,
    guidelines_text: str,
    resume_session_id: str | None = None,
) -> AsyncIterator[str]:
    """Run Agent SDK query and yield SSE events."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    system_prompt = build_system_prompt(rp_folder, branch, guidelines_text)
    python_exe = sys.executable

    if resume_session_id:
        opts = ClaudeAgentOptions(resume=resume_session_id)
    else:
        opts = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={
                "rp-engine": {
                    "type": "stdio",
                    "command": python_exe,
                    "args": ["-m", "rp_engine.mcp_wrapper"],
                    "env": {
                        "RP_ENGINE_URL": f"http://localhost:{os.environ.get('PORT', '3000')}",
                        "RP_FOLDER": rp_folder,
                    },
                }
            },
            allowed_tools=["mcp__rp-engine__*"],
            permission_mode="bypassPermissions",
        )

    agent_session_id = None
    streamed_any = False

    try:
        async for message in query(prompt=user_message, options=opts):
            if isinstance(message, ResultMessage):
                if message.session_id:
                    agent_session_id = message.session_id
                if message.result and not streamed_any:
                    # Only emit result text if we didn't already stream it
                    # via AssistantMessage blocks (avoids duplicate output)
                    yield _sse_token(message.result)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield _sse_token(block.text)
                        streamed_any = True

        done_fields: dict[str, int | str] = {}
        if agent_session_id:
            done_fields["agent_session_id"] = agent_session_id
        yield _sse_done(**done_fields)

    except Exception as e:
        logger.error("Agent SDK chat error: %s", e, exc_info=True)
        yield _sse_error(str(e))


@router.post("", dependencies=[Depends(require_chat_mode("sdk"))])
async def agent_chat(
    body: AgentChatRequest,
    db: Database = Depends(get_db),
    guidelines_svc: GuidelinesService = Depends(get_guidelines_service),
):
    """Stream an RP response via Claude Agent SDK.

    Same SSE format as POST /api/chat for frontend compatibility.
    Claude calls MCP tools (get_scene_context, save_exchange, etc.) autonomously.
    """
    # Preflight check — only verify SDK is installed; auth is handled at runtime
    sdk_err = _ensure_sdk()
    if sdk_err:
        raise HTTPException(503, detail=sdk_err)

    # Resolve active session to get rp_folder/branch
    session = await db.fetch_one(
        "SELECT id, rp_folder, branch FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    )
    if not session:
        raise HTTPException(400, detail="No active session. Create one via POST /api/sessions first.")

    rp_folder = session["rp_folder"]
    branch = session["branch"]

    guidelines_text = await _build_guidelines_text(rp_folder, guidelines_svc)

    gen = _agent_stream(
        user_message=body.user_message,
        rp_folder=rp_folder,
        branch=branch,
        guidelines_text=guidelines_text,
        resume_session_id=body.session_id,
    )
    return StreamingResponse(gen, media_type="text/event-stream", headers=_SSE_HEADERS)
