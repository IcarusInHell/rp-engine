"""FastAPI dependency injection helpers.

All service lookups go through the ServiceContainer stored on app.state.services.
Individual getters are thin wrappers for use in Depends().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request

from rp_engine.config import get_config
from rp_engine.container import ServiceContainer
from rp_engine.database import Database
from rp_engine.services.analysis_pipeline import AnalysisPipeline
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.auto_save import AutoSaveManager
from rp_engine.services.branch_manager import BranchManager
from rp_engine.services.card_authoring import CardAuthoringService
from rp_engine.services.card_indexer import CardIndexer
from rp_engine.services.chat_manager import ChatManager
from rp_engine.services.context_engine import ContextEngine
from rp_engine.services.custom_state_manager import CustomStateManager
from rp_engine.services.diagnostic_logger import DiagnosticLogger
from rp_engine.services.entity_extractor import EntityExtractor
from rp_engine.services.exchange_search_service import ExchangeSearchService
from rp_engine.services.exchange_writer import ExchangeWriter
from rp_engine.services.graph_resolver import GraphResolver
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.knowledge_resolver import KnowledgeResolver
from rp_engine.services.llm_client import LLMClient
from rp_engine.services.lorebook_indexer import LorebookIndexer
from rp_engine.services.lorebook_service import LorebookService
from rp_engine.services.npc_brief_builder import NPCBriefBuilder
from rp_engine.services.npc_engine import NPCEngine
from rp_engine.services.prompt_assembler import PromptAssembler
from rp_engine.services.recap_builder import RecapBuilder
from rp_engine.services.response_analyzer import ResponseAnalyzer
from rp_engine.services.rewind_service import RewindService
from rp_engine.services.scene_classifier import SceneClassifier
from rp_engine.services.state_manager import StateManager
from rp_engine.services.summary_builder import SummaryBuilder
from rp_engine.services.thread_tracker import ThreadTracker
from rp_engine.services.timestamp_tracker import TimestampTracker
from rp_engine.services.trigger_evaluator import TriggerEvaluator
from rp_engine.services.vector_search import VectorSearch


def _get(request: Request, attr: str) -> Any:
    """Look up a service from the ServiceContainer on app.state."""
    container = getattr(request.app.state, "services", None)
    if container is not None:
        return getattr(container, attr)
    # Fallback for test setups that put services directly on app.state
    return getattr(request.app.state, attr)


def get_services(request: Request) -> ServiceContainer:
    """Get the full service container."""
    return request.app.state.services


def get_db(request: Request) -> Database:
    return _get(request, "db")


def get_card_indexer(request: Request) -> CardIndexer:
    return _get(request, "card_indexer")


def get_card_authoring_service(request: Request) -> CardAuthoringService:
    return _get(request, "card_authoring_service")


def get_vault_root(request: Request) -> Path:
    return _get(request, "vault_root")


def get_entity_extractor(request: Request) -> EntityExtractor:
    return _get(request, "entity_extractor")


def get_scene_classifier(request: Request) -> SceneClassifier:
    return _get(request, "scene_classifier")


def get_graph_resolver(request: Request) -> GraphResolver:
    return _get(request, "graph_resolver")


def get_vector_search(request: Request) -> VectorSearch:
    return _get(request, "vector_search")


def get_trigger_evaluator(request: Request) -> TriggerEvaluator:
    return _get(request, "trigger_evaluator")


def get_context_engine(request: Request) -> ContextEngine:
    return _get(request, "context_engine")


def get_knowledge_resolver(request: Request) -> KnowledgeResolver:
    return _get(request, "knowledge_resolver")


def get_lorebook_indexer(request: Request) -> LorebookIndexer:
    return _get(request, "lorebook_indexer")


def get_lorebook_service(request: Request) -> LorebookService:
    return _get(request, "lorebook_service")


def get_llm_client(request: Request) -> LLMClient:
    return _get(request, "llm_client")


def get_npc_engine(request: Request) -> NPCEngine:
    return _get(request, "npc_engine")


def get_ancestry_resolver(request: Request) -> AncestryResolver:
    return _get(request, "ancestry_resolver")


def get_state_manager(request: Request) -> StateManager:
    return _get(request, "state_manager")


def get_thread_tracker(request: Request) -> ThreadTracker:
    return _get(request, "thread_tracker")


def get_timestamp_tracker(request: Request) -> TimestampTracker:
    return _get(request, "timestamp_tracker")


def get_response_analyzer(request: Request) -> ResponseAnalyzer:
    return _get(request, "response_analyzer")


def get_analysis_pipeline(request: Request) -> AnalysisPipeline | None:
    container = getattr(request.app.state, "services", None)
    if container is not None:
        return getattr(container, "analysis_pipeline", None)
    return getattr(request.app.state, "analysis_pipeline", None)


def get_exchange_writer(request: Request) -> ExchangeWriter:
    return _get(request, "exchange_writer")


def get_rewind_service(request: Request) -> RewindService:
    return _get(request, "rewind_service")


def get_exchange_search_service(request: Request) -> ExchangeSearchService:
    return _get(request, "exchange_search_service")


def get_branch_manager(request: Request) -> BranchManager:
    return _get(request, "branch_manager")


def get_auto_save_manager(request: Request) -> AutoSaveManager | None:
    container = getattr(request.app.state, "services", None)
    if container is not None:
        return getattr(container, "auto_save_manager", None)
    return getattr(request.app.state, "auto_save_manager", None)


def get_lance_store(request: Request):
    """Get the LanceStore for immediate exchange embedding."""
    container = getattr(request.app.state, "services", None)
    if container is not None:
        return getattr(container, "lance_store", None)
    return getattr(request.app.state, "lance_store", None)


def get_custom_state_manager(request: Request) -> CustomStateManager:
    return _get(request, "custom_state_manager")


def get_guidelines_service(request: Request) -> GuidelinesService:
    return _get(request, "guidelines_service")


def get_npc_brief_builder(request: Request) -> NPCBriefBuilder:
    return _get(request, "npc_brief_builder")


def get_prompt_assembler(request: Request) -> PromptAssembler:
    return _get(request, "prompt_assembler")


def get_summary_builder(request: Request) -> SummaryBuilder:
    return _get(request, "summary_builder")


def get_recap_builder(request: Request) -> RecapBuilder:
    return _get(request, "recap_builder")


def get_chat_manager(request: Request) -> ChatManager:
    return _get(request, "chat_manager")


def get_continuity_checker(request: Request):
    """Get the ContinuityChecker (optional — may be None if disabled)."""
    container = getattr(request.app.state, "services", None)
    if container is not None:
        return getattr(container, "continuity_checker", None)
    return getattr(request.app.state, "continuity_checker", None)


def get_diagnostic_logger(request: Request) -> DiagnosticLogger:
    return _get(request, "diagnostic_logger")


# ── Chat mode guard ────────────────────────────────────────────────────


def require_chat_mode(expected: str):
    """Dependency that rejects requests if the configured chat mode doesn't match."""
    async def _guard():
        cfg = get_config()
        actual = cfg.llm.mode.chat
        if actual != expected:
            other = "sdk" if expected == "provider" else "provider"
            endpoint = "/api/chat/agent" if actual == "sdk" else "/api/chat"
            raise HTTPException(
                status_code=409,
                detail=f"Chat mode is '{actual}'. Use {endpoint} instead.",
            )
    return _guard


# ── Session resolution ──────────────────────────────────────────────────


async def resolve_active_session(
    db: Database, rp_folder: str, branch: str,
) -> tuple[str, str, str]:
    """Resolve the active session for a given RP folder and branch.

    Returns (rp_folder, branch, session_id).
    Raises HTTPException 400 if no active session exists.
    """
    session = await db.fetch_one(
        """SELECT id, rp_folder, branch FROM sessions
           WHERE ended_at IS NULL AND rp_folder = ? AND branch = ?
           ORDER BY started_at DESC LIMIT 1""",
        [rp_folder, branch],
    )
    if not session:
        raise HTTPException(
            400,
            detail=f"No active session for {rp_folder}/{branch}. Create one via POST /api/sessions first.",
        )
    return session["rp_folder"], session["branch"], session["id"]
