"""Service container — async factory that owns construction + lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from npc_intelligence import NPCIntelligence
    from writing_intelligence import WritingIntelligence

from rp_engine.config import RPEngineConfig
from rp_engine.database import Database
from rp_engine.services.analysis_pipeline import AnalysisPipeline
from rp_engine.services.ancestry_resolver import AncestryResolver
from rp_engine.services.auto_save import AutoSaveManager
from rp_engine.services.branch_manager import BranchManager
from rp_engine.services.card_indexer import CardIndexer
from rp_engine.services.chat_manager import ChatManager
from rp_engine.services.context_engine import ContextEngine
from rp_engine.services.custom_state_manager import CustomStateManager
from rp_engine.services.diagnostic_logger import DiagnosticLogger
from rp_engine.services.entity_extractor import EntityExtractor
from rp_engine.services.exchange_writer import ExchangeWriter
from rp_engine.services.file_watcher import FileWatcher
from rp_engine.services.graph_resolver import GraphResolver
from rp_engine.services.guidelines_service import GuidelinesService
from rp_engine.services.lance_store import LanceStore
from rp_engine.services.llm import LLMClient, build_providers
from rp_engine.services.npc_brief_builder import NPCBriefBuilder
from rp_engine.services.npc_engine import NPCEngine
from rp_engine.services.prompt_assembler import PromptAssembler
from rp_engine.services.recap_builder import RecapBuilder
from rp_engine.services.response_analyzer import ResponseAnalyzer
from rp_engine.services.scene_classifier import SceneClassifier
from rp_engine.services.state_manager import StateManager
from rp_engine.services.summary_builder import SummaryBuilder
from rp_engine.services.thread_tracker import ThreadTracker
from rp_engine.services.timestamp_tracker import TimestampTracker
from rp_engine.services.trigger_evaluator import TriggerEvaluator
from rp_engine.services.vector_search import VectorSearch
from rp_engine.utils.embedding import has_real_embedding

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    """Holds all service instances. Built via async build() classmethod."""

    db: Database
    card_indexer: CardIndexer
    vault_root: Path
    file_watcher: FileWatcher
    entity_extractor: EntityExtractor
    scene_classifier: SceneClassifier
    graph_resolver: GraphResolver
    vector_search: VectorSearch
    trigger_evaluator: TriggerEvaluator
    context_engine: ContextEngine
    llm_client: LLMClient
    npc_engine: NPCEngine
    npc_intelligence: NPCIntelligence | None
    writing_intelligence: WritingIntelligence | None
    ancestry_resolver: AncestryResolver
    state_manager: StateManager
    branch_manager: BranchManager
    thread_tracker: ThreadTracker
    timestamp_tracker: TimestampTracker
    response_analyzer: ResponseAnalyzer
    lance_store: LanceStore
    custom_state_manager: CustomStateManager
    guidelines_service: GuidelinesService
    npc_brief_builder: NPCBriefBuilder
    prompt_assembler: PromptAssembler
    analysis_pipeline: AnalysisPipeline
    exchange_writer: ExchangeWriter
    auto_save_manager: AutoSaveManager
    summary_builder: SummaryBuilder
    recap_builder: RecapBuilder
    chat_manager: ChatManager
    continuity_checker: object  # ContinuityChecker | None (optional)
    diagnostic_logger: DiagnosticLogger

    @classmethod
    async def build(cls, config: RPEngineConfig) -> ServiceContainer:
        """Construct all services in dependency-tier order."""
        vault_root = Path(config.paths.vault_root).resolve()

        # ---- Tier 0: Foundation ----
        db = Database(config.paths.db_path)
        await db.initialize()

        data_dir = Path(config.paths.db_path).parent
        diagnostic_logger = DiagnosticLogger(config=config.diagnostics, data_dir=data_dir)

        # ---- Tier 1: Stateless services ----
        card_indexer = CardIndexer(db, vault_root)
        rp_folders = card_indexer.get_all_rp_folders()
        for folder in rp_folders:
            result = await card_indexer.full_index(folder)
            logger.info("Indexed %s: %d entities", folder, result["entities"])

        # Ensure main branch exists for every RP folder
        branch_mgr_early = BranchManager(db=db)
        for folder in rp_folders:
            await branch_mgr_early.ensure_main_branch(folder)

        file_watcher = FileWatcher(card_indexer, vault_root, rp_folders)
        entity_extractor = EntityExtractor(db)
        scene_classifier = SceneClassifier(db)
        graph_resolver = GraphResolver(db)
        trigger_evaluator = TriggerEvaluator(db)
        guidelines_service = GuidelinesService(vault_root)
        npc_brief_builder = NPCBriefBuilder()

        # ---- Tier 2: Config-dependent ----

        # Build LLM providers via shared factory
        providers = build_providers(config)

        llm_client = LLMClient(
            providers=providers,
            default_provider=config.llm.provider,
            models=config.llm.models,
            fallback_model=config.llm.fallback_model,
            embedding_fallback=config.llm.embedding_fallback_provider,
        )
        # VectorSearch uses LLMClient.embed as its embed_fn — single key holder
        vector_search = VectorSearch(
            db,
            embed_fn=llm_client.embed,
            embedding_model=config.llm.models.embeddings,
        )

        # Late-bind vector_search to card_indexer (created in Tier 1, needs Tier 2 dep)
        card_indexer.vector_search = vector_search

        # Populate SQLite vectors table if no real embeddings exist
        # (zero-vector placeholders from failed API calls don't count)
        has_real = await db.fetch_one(
            "SELECT COUNT(*) as cnt FROM vectors WHERE embedding IS NOT NULL AND LENGTH(embedding) > 0"
        )
        needs_reindex = True
        if has_real and has_real["cnt"] > 0:
            # Check if at least one vector is non-zero
            sample = await db.fetch_one("SELECT embedding FROM vectors WHERE embedding IS NOT NULL LIMIT 1")
            if sample and sample["embedding"]:
                needs_reindex = not has_real_embedding(sample["embedding"])

        if needs_reindex:
            # Clear stale zero-vector rows before reindexing
            future = await db.enqueue_write("DELETE FROM vectors")
            await future
            for folder in rp_folders:
                result = await vector_search.reindex_all(folder)
                logger.info("Vectorized %s: %d files, %d chunks", folder, result["files"], result["chunks"])

        # LanceDB vector store for exchange embeddings + card vectors
        lance_path = Path(config.paths.db_path).parent / "vectors"
        lance_store = LanceStore(
            db_path=lance_path,
            embed_fn=llm_client.embed,
            dimension=config.search.embedding_dimension,
            embedding_model=config.llm.models.embeddings,
        )
        await lance_store.initialize()

        ancestry_resolver = AncestryResolver(db)

        prompt_assembler = PromptAssembler(
            vault_root=vault_root,
            db=db,
            guidelines_service=guidelines_service,
            ancestry_resolver=ancestry_resolver,
        )
        state_manager = StateManager(db=db, resolver=ancestry_resolver)

        # NPC/Writing Intelligence (optional deps)
        npc_intelligence = None
        try:
            from npc_intelligence import NPCIntelligence
            npc_intel_db = str(Path(config.paths.db_path).parent / "npc_intelligence.db")
            npc_intelligence = NPCIntelligence(db_path=npc_intel_db)
            logger.info("npc_intelligence loaded (db=%s)", npc_intel_db)
        except ImportError as e:
            logger.info("npc_intelligence not available — %s", e)
        except Exception:
            logger.exception("npc_intelligence failed to initialize")

        writing_intelligence = None
        try:
            from writing_intelligence import WritingIntelligence
            writing_intel_db = str(Path(config.paths.db_path).parent / "writing_intelligence.db")
            writing_intelligence = WritingIntelligence(db_path=writing_intel_db)
            logger.info("writing_intelligence loaded (db=%s)", writing_intel_db)
        except ImportError as e:
            logger.info("writing_intelligence not available — %s", e)
        except Exception:
            logger.exception("writing_intelligence failed to initialize")

        custom_state_manager = CustomStateManager(db)

        # ---- Tier 3: Composite services ----
        npc_engine = NPCEngine(
            db=db,
            llm_client=llm_client,
            graph_resolver=graph_resolver,
            vector_search=vector_search,
            config=config,
            vault_root=vault_root,
            npc_intelligence=npc_intelligence,
            scene_classifier=scene_classifier,
            resolver=ancestry_resolver,
            lance_store=lance_store,
        )

        context_engine = ContextEngine(
            db=db,
            entity_extractor=entity_extractor,
            scene_classifier=scene_classifier,
            graph_resolver=graph_resolver,
            vector_search=vector_search,
            trigger_evaluator=trigger_evaluator,
            vault_root=vault_root,
            guidelines_service=guidelines_service,
            npc_brief_builder=npc_brief_builder,
            npc_engine=npc_engine,
            lance_store=lance_store,
            custom_state_manager=custom_state_manager,
        )

        branch_manager = BranchManager(db=db, state_manager=state_manager, resolver=ancestry_resolver)

        # Late-binding for circular deps
        context_engine.configure(
            branch_manager=branch_manager,
            writing_intelligence=writing_intelligence,
        )
        custom_state_manager.configure(branch_manager=branch_manager)

        thread_tracker = ThreadTracker(db)
        timestamp_tracker = TimestampTracker(db, state_manager)
        response_analyzer = ResponseAnalyzer(db, llm_client)
        # Late-bind response_analyzer to card_indexer for alias cache invalidation
        card_indexer.response_analyzer = response_analyzer
        # Continuity checker (optional — disabled by default)
        continuity_checker = None
        if config.continuity.enabled:
            from rp_engine.services.continuity_checker import ContinuityChecker
            continuity_checker = ContinuityChecker(
                db=db,
                llm_client=llm_client,
                config=config.continuity,
                lance_store=lance_store,
            )

        analysis_pipeline = AnalysisPipeline(
            db=db,
            response_analyzer=response_analyzer,
            state_manager=state_manager,
            thread_tracker=thread_tracker,
            timestamp_tracker=timestamp_tracker,
            lance_store=lance_store,
            continuity_checker=continuity_checker,
            custom_state_manager=custom_state_manager,
        )

        exchange_writer = ExchangeWriter(db=db, analysis_pipeline=analysis_pipeline, lance_store=lance_store)
        auto_save_manager = AutoSaveManager(db=db, exchange_writer=exchange_writer)
        if config.auto_save.enabled:
            auto_save_manager.set_active(True)

        # Inject diagnostic logger into services for structured logging
        for svc in (
            llm_client, context_engine, analysis_pipeline,
            npc_engine, state_manager, exchange_writer, file_watcher,
        ):
            svc.diagnostic_logger = diagnostic_logger

        summary_builder = SummaryBuilder(db=db, lance_store=lance_store, llm_client=llm_client)
        recap_builder = RecapBuilder(
            db=db,
            lance_store=lance_store,
            llm_client=llm_client,
            state_manager=state_manager,
            thread_tracker=thread_tracker,
        )
        chat_manager = ChatManager(
            db=db,
            context_engine=context_engine,
            prompt_assembler=prompt_assembler,
            llm_client=llm_client,
            exchange_writer=exchange_writer,
        )

        return cls(
            db=db,
            card_indexer=card_indexer,
            vault_root=vault_root,
            file_watcher=file_watcher,
            entity_extractor=entity_extractor,
            scene_classifier=scene_classifier,
            graph_resolver=graph_resolver,
            vector_search=vector_search,
            trigger_evaluator=trigger_evaluator,
            context_engine=context_engine,
            llm_client=llm_client,
            npc_engine=npc_engine,
            npc_intelligence=npc_intelligence,
            writing_intelligence=writing_intelligence,
            ancestry_resolver=ancestry_resolver,
            state_manager=state_manager,
            branch_manager=branch_manager,
            thread_tracker=thread_tracker,
            timestamp_tracker=timestamp_tracker,
            response_analyzer=response_analyzer,
            lance_store=lance_store,
            custom_state_manager=custom_state_manager,
            guidelines_service=guidelines_service,
            npc_brief_builder=npc_brief_builder,
            prompt_assembler=prompt_assembler,
            analysis_pipeline=analysis_pipeline,
            exchange_writer=exchange_writer,
            auto_save_manager=auto_save_manager,
            summary_builder=summary_builder,
            recap_builder=recap_builder,
            chat_manager=chat_manager,
            continuity_checker=continuity_checker,
            diagnostic_logger=diagnostic_logger,
        )

    async def start(self) -> None:
        """Start background tasks (file watcher, analysis pipeline)."""
        self.file_watcher.start()
        self.analysis_pipeline.start()
        logger.info("Service container started")

    async def close(self) -> None:
        """Graceful shutdown in reverse order."""
        await self.analysis_pipeline.stop()
        # Clear in-memory caches to free resources
        self.auto_save_manager.clear_all()
        self.vector_search.clear_cache()
        self.response_analyzer.invalidate_alias_cache()
        await self.llm_client.close()
        await self.lance_store.close()
        await self.file_watcher.stop()
        if self.npc_intelligence:
            self.npc_intelligence.close()
        if self.writing_intelligence:
            self.writing_intelligence.close()
        await self.db.close()
        logger.info("Service container closed")
