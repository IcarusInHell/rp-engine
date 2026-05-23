"""Centralized exchange persistence — atomic insert, embedding, analysis."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from rp_engine.database import PRIORITY_EXCHANGE, Database
from rp_engine.services.analysis_pipeline import AnalysisPipeline
from rp_engine.services.lance_store import LanceStore
from rp_engine.utils.chunking import get_effective_chunking
from rp_engine.utils.text import hash_content

logger = logging.getLogger(__name__)


class ExchangeWriter:
    """Centralized exchange persistence — atomic insert, embedding, analysis."""

    def __init__(
        self,
        db: Database,
        analysis_pipeline: AnalysisPipeline | None = None,
        lance_store: LanceStore | None = None,
    ) -> None:
        self.db = db
        self.analysis_pipeline = analysis_pipeline
        self.lance_store = lance_store
        self._bg_tasks: set[asyncio.Task] = set()
        self.diagnostic_logger = None  # injected by container

    async def _re_embed_exchange(
        self,
        exchange_number: int,
        user_message: str,
        assistant_response: str,
        rp_folder: str,
        branch: str,
        session_id: str,
        *,
        delete_existing: bool = False,
    ) -> None:
        """Re-embed an exchange in the background. Respects per-RP chunking config.

        If delete_existing is True, removes old vectors first (for updates).
        """
        if self.lance_store is None:
            return

        async def _embed():
            try:
                chunking = await get_effective_chunking(self.db, rp_folder)
                if delete_existing:
                    await self.lance_store.delete_exchange_vectors(
                        rp_folder, branch, exchange_number,
                    )
                await self.lance_store.embed_exchange(
                    exchange_number=exchange_number,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    rp_folder=rp_folder,
                    branch=branch,
                    session_id=session_id,
                    chunking_strategy=chunking.strategy,
                    chunk_size=chunking.chunk_size,
                    chunk_overlap=chunking.chunk_overlap,
                )
            except Exception as e:
                logger.warning("Exchange %d embedding failed: %s", exchange_number, e)

        task = asyncio.create_task(_embed())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def save_exchange(
        self,
        session_id: str,
        rp_folder: str,
        branch: str,
        user_message: str,
        assistant_response: str,
        *,
        exchange_number: int | None = None,
        in_story_timestamp: str | None = None,
        location: str | None = None,
        metadata: dict | None = None,
        idempotency_key: str | None = None,
        embed: bool = True,
        analyze: bool = True,
        message_mode: str = "rp",
    ) -> tuple[int, int]:
        """Insert exchange, return (exchange_id, exchange_number).

        If exchange_number is None, assigns atomically via MAX+1.
        """
        now = datetime.now(UTC).isoformat()

        if not idempotency_key:
            idempotency_key = hash_content(user_message + assistant_response)[:16]

        import json
        metadata_json = json.dumps(metadata) if metadata else None

        if exchange_number is not None:
            # Explicit number (rewind case)
            future = await self.db.enqueue_write(
                """INSERT INTO exchanges (session_id, rp_folder, branch, exchange_number,
                   user_message, assistant_response, in_story_timestamp, location,
                   analysis_status, created_at, metadata, idempotency_key, message_mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                [
                    session_id, rp_folder, branch, exchange_number,
                    user_message, assistant_response,
                    in_story_timestamp, location,
                    now, metadata_json, idempotency_key, message_mode,
                ],
                priority=PRIORITY_EXCHANGE,
            )
        else:
            # Atomic exchange number assignment — considers both existing
            # exchanges on this branch AND the branch_point_exchange from
            # the parent so new branches continue numbering from the fork.
            future = await self.db.enqueue_write(
                """INSERT INTO exchanges (session_id, rp_folder, branch, exchange_number,
                   user_message, assistant_response, in_story_timestamp, location,
                   analysis_status, created_at, metadata, idempotency_key, message_mode)
                   VALUES (?, ?, ?, COALESCE(
                       (SELECT MAX(exchange_number) FROM exchanges WHERE rp_folder = ? AND branch = ?),
                       (SELECT branch_point_exchange FROM branches WHERE name = ? AND rp_folder = ?),
                       0
                   ) + 1, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                [
                    session_id, rp_folder, branch,
                    rp_folder, branch,
                    branch, rp_folder,
                    user_message, assistant_response,
                    in_story_timestamp, location,
                    now, metadata_json, idempotency_key, message_mode,
                ],
                priority=PRIORITY_EXCHANGE,
            )

        exchange_id = await future

        # Retrieve the assigned exchange number
        actual_number = exchange_number
        if actual_number is None:
            actual_number = await self.db.fetch_val(
                "SELECT exchange_number FROM exchanges WHERE id = ?",
                [exchange_id],
            )

        # Background vector embedding (respects per-RP chunking config)
        if embed:
            await self._re_embed_exchange(
                actual_number, user_message, assistant_response,
                rp_folder, branch, session_id,
            )

        # Enqueue for analysis
        if analyze and self.analysis_pipeline is not None:
            await self.analysis_pipeline.enqueue(exchange_id, rp_folder, branch)

        logger.info(
            "Exchange %d (id=%d) saved for session %s",
            actual_number, exchange_id, session_id,
        )

        if self.diagnostic_logger:
            self.diagnostic_logger.log(
                category="state",
                event="exchange_saved",
                data={
                    "exchange_id": exchange_id,
                    "exchange_number": actual_number,
                    "session_id": session_id,
                    "rp_folder": rp_folder,
                    "branch": branch,
                    "embed": embed,
                    "analyze": analyze,
                },
                content={
                    "user_message": user_message[:500],
                    "assistant_response": assistant_response[:500],
                },
            )

        return exchange_id, actual_number

    async def update_response(
        self,
        exchange_id: int,
        new_response: str,
        *,
        re_embed: bool = True,
        re_analyze: bool = True,
    ) -> None:
        """Update an exchange's assistant_response in-place. Re-embeds and re-analyzes."""
        exchange = await self.db.fetch_one(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id],
        )
        if not exchange:
            raise ValueError(f"Exchange {exchange_id} not found")

        future = await self.db.enqueue_write(
            "UPDATE exchanges SET assistant_response = ? WHERE id = ?",
            [new_response, exchange_id],
            priority=PRIORITY_EXCHANGE,
        )
        await future

        rp_folder = exchange["rp_folder"]
        branch = exchange["branch"]
        session_id = exchange["session_id"]
        exchange_number = exchange["exchange_number"]
        user_message = exchange["user_message"]

        # Re-embed (respects per-RP chunking config)
        if re_embed:
            await self._re_embed_exchange(
                exchange_number, user_message, new_response,
                rp_folder, branch, session_id,
                delete_existing=True,
            )

        # Re-analyze
        if re_analyze and self.analysis_pipeline is not None:
            await self.analysis_pipeline.undo_exchange_analysis(
                exchange_number, rp_folder, branch, cascade=False,
            )
            await self.analysis_pipeline.enqueue(exchange_id, rp_folder, branch)

        logger.info("Exchange %d (id=%d) response updated", exchange_number, exchange_id)

    async def update_exchange(
        self,
        exchange_number: int,
        rp_folder: str,
        branch: str,
        *,
        user_message: str | None = None,
        assistant_response: str | None = None,
        re_embed: bool = True,
        re_analyze: bool = False,
    ) -> dict:
        """Edit an exchange's user message and/or assistant response.

        For assistant_response: creates a new variant with source='manual_edit',
        marks it active, updates the main exchange, and optionally re-analyzes.
        For user_message: direct DB update + re-embed.

        Returns the updated exchange row.
        """
        exchange = await self.db.fetch_one(
            "SELECT * FROM exchanges WHERE rp_folder = ? AND branch = ? AND exchange_number = ?",
            [rp_folder, branch, exchange_number],
        )
        if not exchange:
            raise ValueError(
                f"Exchange {exchange_number} not found in {rp_folder}/{branch}"
            )

        exchange_id = exchange["id"]
        updated_user = user_message if user_message is not None else exchange["user_message"]
        updated_response = assistant_response if assistant_response is not None else exchange["assistant_response"]

        # --- Update user_message ---
        if user_message is not None:
            f = await self.db.enqueue_write(
                "UPDATE exchanges SET user_message = ? WHERE id = ?",
                [user_message, exchange_id],
                priority=PRIORITY_EXCHANGE,
            )
            await f

        # --- Update assistant_response (via variant) ---
        if assistant_response is not None:
            # Ensure original response is saved as variant 0 if no variants exist
            variant_count = await self.db.fetch_val(
                "SELECT COUNT(*) FROM exchange_variants WHERE exchange_id = ?",
                [exchange_id],
            )
            if variant_count == 0:
                f = await self.db.enqueue_write(
                    """INSERT INTO exchange_variants
                       (exchange_id, rp_folder, branch, exchange_number,
                        assistant_response, model_used, temperature, source,
                        is_active, created_at)
                       VALUES (?, ?, ?, ?, ?, NULL, NULL, 'llm', 0, ?)""",
                    [
                        exchange_id, rp_folder, branch,
                        exchange_number, exchange["assistant_response"],
                        exchange["created_at"],
                    ],
                    priority=PRIORITY_EXCHANGE,
                )
                await f

            # Deactivate all existing variants
            f = await self.db.enqueue_write(
                "UPDATE exchange_variants SET is_active = 0 WHERE exchange_id = ?",
                [exchange_id],
                priority=PRIORITY_EXCHANGE,
            )
            await f

            # Insert new variant with source='manual_edit'
            now = datetime.now(UTC).isoformat()
            f = await self.db.enqueue_write(
                """INSERT INTO exchange_variants
                   (exchange_id, rp_folder, branch, exchange_number,
                    assistant_response, model_used, temperature, source,
                    is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, 'manual_edit', 1, ?)""",
                [
                    exchange_id, rp_folder, branch,
                    exchange_number, assistant_response, now,
                ],
                priority=PRIORITY_EXCHANGE,
            )
            await f

            # Update main exchange table
            f = await self.db.enqueue_write(
                "UPDATE exchanges SET assistant_response = ? WHERE id = ?",
                [assistant_response, exchange_id],
                priority=PRIORITY_EXCHANGE,
            )
            await f

        # --- Re-embed ---
        if re_embed:
            await self._re_embed_exchange(
                exchange_number, updated_user, updated_response,
                rp_folder, branch, exchange["session_id"],
                delete_existing=True,
            )

        # --- Re-analyze (undo old + enqueue new) ---
        if re_analyze and assistant_response is not None and self.analysis_pipeline is not None:
            await self.analysis_pipeline.undo_exchange_analysis(
                exchange_number, rp_folder, branch, cascade=False,
            )
            await self.analysis_pipeline.enqueue(exchange_id, rp_folder, branch)

        logger.info(
            "Exchange %d edited (user=%s, assistant=%s, re_analyze=%s)",
            exchange_number,
            user_message is not None,
            assistant_response is not None,
            re_analyze,
        )

        if self.diagnostic_logger:
            self.diagnostic_logger.log(
                category="state",
                event="exchange_edited",
                data={
                    "exchange_id": exchange_id,
                    "exchange_number": exchange_number,
                    "rp_folder": rp_folder,
                    "branch": branch,
                    "edited_user": user_message is not None,
                    "edited_assistant": assistant_response is not None,
                    "re_embed": re_embed,
                    "re_analyze": re_analyze,
                },
            )

        # Return updated exchange
        return await self.db.fetch_one(
            "SELECT * FROM exchanges WHERE id = ?", [exchange_id],
        )
