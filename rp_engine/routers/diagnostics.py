"""Diagnostics endpoints — status, toggle, download, clear, report."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from rp_engine.config import DiagnosticConfig, get_config
from rp_engine.dependencies import get_diagnostic_logger
from rp_engine.services.diagnostic_logger import DiagnosticLogger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


class DiagnosticStatusResponse(BaseModel):
    """Diagnostic log status — enabled flag, level, current file size, entry/archive counts, last-entry timestamp, and auto-report config."""

    enabled: bool
    level: str
    file_size_bytes: int
    entry_count: int
    archive_count: int
    last_entry_ts: str | None
    reporter_key: str
    auto_report: dict


class DiagnosticUpdateRequest(BaseModel):
    """Partial update to diagnostic settings — every field optional; only supplied fields are merged."""

    enabled: bool | None = None
    level: str | None = None
    max_file_size_mb: int | None = None
    max_files: int | None = None
    auto_purge_days: int | None = None
    reporter_key: str | None = None
    auto_report: dict | None = None


class DiagnosticReportResponse(BaseModel):
    """Result of sending a log report to the configured webhook — ok flag with status code, error, and the reporter key used."""

    ok: bool
    status_code: int | None = None
    error: str | None = None
    reporter_key: str | None = None


class DiagnosticClearResponse(BaseModel):
    """Result of clearing logs — ok flag plus the number of files removed."""

    ok: bool
    files_removed: int


@router.get("", response_model=DiagnosticStatusResponse)
async def get_diagnostics(
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
):
    """Get diagnostic log status."""
    return DiagnosticStatusResponse(**diag.status())


@router.put("")
async def update_diagnostics(
    body: DiagnosticUpdateRequest,
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
):
    """Toggle logging on/off, change level, update settings."""
    current = diag.config.model_dump()

    # Merge updates
    if body.enabled is not None:
        current["enabled"] = body.enabled
    if body.level is not None:
        if body.level not in ("full", "metadata"):
            return {"ok": False, "error": "level must be 'full' or 'metadata'"}
        current["level"] = body.level
    if body.max_file_size_mb is not None:
        current["max_file_size_mb"] = body.max_file_size_mb
    if body.max_files is not None:
        current["max_files"] = body.max_files
    if body.auto_purge_days is not None:
        current["auto_purge_days"] = body.auto_purge_days
    if body.reporter_key is not None:
        current["reporter_key"] = body.reporter_key
    if body.auto_report is not None:
        current["auto_report"] = body.auto_report

    new_config = DiagnosticConfig(**current)
    diag.update_config(new_config)

    # Also persist to config.yaml via the config system
    from rp_engine.routers.config import _read_config_yaml, _write_config_yaml
    yaml_data = _read_config_yaml()
    yaml_data["diagnostics"] = new_config.model_dump()
    _write_config_yaml(yaml_data)
    get_config.cache_clear()

    result: dict = {"ok": True}
    # Warn when enabling full content logging
    if body.level == "full" or (body.enabled and new_config.level == "full"):
        result["warning"] = (
            "Full content logging is enabled. Message text, prompts, and LLM "
            "responses will be recorded in diagnostic logs. Disable or switch "
            "to 'metadata' level if this data is sensitive."
        )
    return result


@router.delete("", response_model=DiagnosticClearResponse)
async def clear_diagnostics(
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
):
    """Clear all log files."""
    result = diag.clear()
    return DiagnosticClearResponse(ok=True, **result)


@router.get("/download")
async def download_diagnostics(
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
):
    """Download current + archived logs as a .zip file."""
    zip_bytes = diag.export_zip()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=diagnostics.zip"},
    )


@router.post("/report", response_model=DiagnosticReportResponse)
async def send_report(
    diag: DiagnosticLogger = Depends(get_diagnostic_logger),
):
    """Manually trigger a log report to the configured webhook."""
    result = await diag.send_report()
    return DiagnosticReportResponse(**result)
