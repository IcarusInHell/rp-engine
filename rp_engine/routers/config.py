"""Config endpoints — read and write app configuration."""

from __future__ import annotations

import logging
import os
import time

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from rp_engine.config import PROJECT_ROOT, RPEngineConfig, get_config
from rp_engine.dependencies import get_auto_save_manager, get_llm_client
from rp_engine.services.auto_save import AutoSaveManager
from rp_engine.services.llm._client import LLMClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
_ENV_PATH = PROJECT_ROOT / ".env"


class ConfigResponse(BaseModel):
    """Full app-config view with secrets masked — one dict per config section (server/paths/llm/chat/context/search/trust/diagnostics/rp/prompt)."""

    server: dict
    paths: dict
    llm: dict
    chat: dict
    context: dict
    search: dict
    trust: dict
    diagnostics: dict
    rp: dict
    prompt: dict = {}


class ActiveRPResponse(BaseModel):
    """Auto-save status — whether auto-save is active plus the current open-session count."""

    active_rp: bool
    session_count: int


class ActiveRPToggle(BaseModel):
    """Request body to enable or disable auto-save for RP exchanges."""

    enabled: bool


class ConfigUpdate(BaseModel):
    """Partial config-update body — non-secret sections merge into config.yaml; `openrouter_api_key` is written to .env only, never config.yaml."""

    server: dict | None = None
    paths: dict | None = None
    llm: dict | None = None
    context: dict | None = None
    search: dict | None = None
    chat: dict | None = None
    trust: dict | None = None
    diagnostics: dict | None = None
    rp: dict | None = None
    # Phase 6: global prompt config (token_budget / example_dialogue / injection
    # defaults / trigger_stemming). Per-RP overrides live in Story_Guidelines.md;
    # these are the machine-global defaults.
    prompt: dict | None = None
    # Secret fields — written to .env only, never to config.yaml
    openrouter_api_key: str | None = None


def _config_to_dict(cfg: RPEngineConfig) -> dict:
    """Serialize config to a safe dict (no secrets)."""
    return {
        "server": cfg.server.model_dump(),
        "paths": cfg.paths.model_dump(),
        "llm": {
            **cfg.llm.model_dump(exclude={"api_key"}),
            "api_key": "***" if cfg.effective_api_key() else "",
        },
        "chat": cfg.chat.model_dump(),
        "context": cfg.context.model_dump(),
        "search": cfg.search.model_dump(),
        "trust": cfg.trust.model_dump(),
        "diagnostics": cfg.diagnostics.model_dump(),
        "rp": cfg.rp.model_dump(),
        "prompt": cfg.prompt.model_dump(),
    }


def _read_config_yaml() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            data = yaml.safe_load(f)
            return data if data else {}
    return {}


def _write_config_yaml(data: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _update_env_key(key: str, value: str) -> None:
    """Set or update a key in .env file."""
    env_path = _ENV_PATH
    lines: list[str] = []
    found = False

    if env_path.exists():
        with open(env_path) as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                lines[i] = f"{key}={value}\n"
                found = True
                break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)


@router.get("", response_model=ConfigResponse)
async def get_config_endpoint():
    """Return current app configuration (secrets masked)."""
    cfg = get_config()
    d = _config_to_dict(cfg)
    return ConfigResponse(**d)


@router.put("")
async def update_config(
    body: ConfigUpdate,
    llm: LLMClient = Depends(get_llm_client),
):
    """Update app configuration.

    Non-secret values are written to config.yaml.
    API keys are written to .env only.
    LLM provider changes are hot-reloaded without server restart.
    """
    try:
        yaml_data = _read_config_yaml()

        # Merge non-secret updates into yaml_data
        section_map = {
            "server": body.server,
            "paths": body.paths,
            "llm": body.llm,
            "chat": body.chat,
            "context": body.context,
            "search": body.search,
            "trust": body.trust,
            "diagnostics": body.diagnostics,
            "rp": body.rp,
            "prompt": body.prompt,
        }
        for section, updates in section_map.items():
            if updates is not None:
                existing = yaml_data.get(section, {}) or {}
                # Remove secret keys if someone tried to sneak them in
                updates.pop("api_key", None)
                existing.update(updates)
                yaml_data[section] = existing

        _write_config_yaml(yaml_data)

        # Write secrets to .env and update the running process env
        if body.openrouter_api_key is not None:
            _update_env_key("OPENROUTER_API_KEY", body.openrouter_api_key)
            os.environ["OPENROUTER_API_KEY"] = body.openrouter_api_key

        # Invalidate the cached config so next read picks up new values
        get_config.cache_clear()

        # Hot-reload LLM providers when relevant config changed
        msg = "Config updated."
        if body.llm is not None or body.openrouter_api_key is not None:
            fresh_config = get_config()
            await llm.reload_providers(fresh_config)
            msg += " LLM providers reloaded."

        return {"ok": True, "message": msg}

    except yaml.YAMLError as e:
        raise HTTPException(422, detail=f"Invalid YAML in config: {e}") from None
    except PermissionError as e:
        raise HTTPException(403, detail=f"Permission denied: {e}") from None
    except FileNotFoundError as e:
        raise HTTPException(404, detail=f"Config file not found: {e}") from None
    except Exception as e:
        logger.error("Config update failed: %s", e)
        raise HTTPException(500, detail=f"Config update failed: {e}") from e


# ---------------------------------------------------------------------------
# Active RP toggle (controls auto-save)
# ---------------------------------------------------------------------------


@router.get("/active-rp", response_model=ActiveRPResponse)
async def get_active_rp(
    auto_save: AutoSaveManager | None = Depends(get_auto_save_manager),
):
    """Check whether auto-save is enabled."""
    if auto_save is None:
        return ActiveRPResponse(active_rp=False, session_count=0)
    return ActiveRPResponse(
        active_rp=auto_save.is_active(),
        session_count=auto_save.session_count,
    )


@router.post("/active-rp", response_model=ActiveRPResponse)
async def set_active_rp(
    body: ActiveRPToggle,
    auto_save: AutoSaveManager | None = Depends(get_auto_save_manager),
):
    """Enable or disable auto-save for RP exchanges."""
    if auto_save is None:
        raise HTTPException(503, detail="Auto-save manager not available")
    auto_save.set_active(body.enabled)
    return ActiveRPResponse(
        active_rp=auto_save.is_active(),
        session_count=auto_save.session_count,
    )


# ---------------------------------------------------------------------------
# Provider test
# ---------------------------------------------------------------------------


class ProviderTestResult(BaseModel):
    """Result of an LLM-provider connectivity ping — ok/error status with optional latency and error detail."""

    provider: str
    status: str  # "ok" | "error"
    latency_ms: float | None = None
    error: str | None = None


@router.get("/test-provider", response_model=ProviderTestResult)
async def test_provider(
    provider: str = "openrouter",
    llm: LLMClient = Depends(get_llm_client),
):
    """Test connectivity to a configured LLM provider."""
    prov = llm._providers.get(provider)
    if prov is None:
        return ProviderTestResult(
            provider=provider,
            status="error",
            error=f"Provider '{provider}' not configured. Available: {list(llm._providers.keys())}",
        )

    start = time.perf_counter()
    try:
        await prov.generate(
            messages=[{"role": "user", "content": "ping"}],
            model=llm.fallback_model,
            max_tokens=1,
            temperature=0.0,
        )
        latency = (time.perf_counter() - start) * 1000
        return ProviderTestResult(
            provider=provider,
            status="ok",
            latency_ms=round(latency, 1),
        )
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return ProviderTestResult(
            provider=provider,
            status="error",
            latency_ms=round(latency, 1),
            error=str(e),
        )
