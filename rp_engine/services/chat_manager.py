"""Chat manager — orchestrates the full RP chat pipeline.

Chains together: context retrieval -> prompt assembly -> LLM call -> exchange save.
Handles non-streaming, streaming, regeneration (swipe), and continuation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from rp_engine.config import ChatConfig, LLMTemperaturesConfig, get_config
from rp_engine.database import PRIORITY_EXCHANGE, Database
from rp_engine.models.chat import (
    ChatResponse,
    ContinueResponse,
    RegenerateResponse,
    SceneOverride,
    SwipeResponse,
)
from rp_engine.models.context import ContextRequest
from rp_engine.services.context_engine import ContextEngine
from rp_engine.services.exchange_writer import ExchangeWriter
from rp_engine.services.llm_client import LLMClient
from rp_engine.services.prompt_assembler import PromptAssembler
from rp_engine.services.state_entry_resolver import latest_exchange
from rp_engine.utils.direction import (
    build_direction_message,
    build_ooc_message,
    parse_direction_markers,
)

logger = logging.getLogger(__name__)


def _sse_token(chunk: str) -> str:
    """Format an SSE token event."""
    return f'data: {{"type": "token", "content": {json.dumps(chunk)}}}\n\n'


def _sse_done(**fields: int | str) -> str:
    """Format an SSE done event with arbitrary fields."""
    payload = {"type": "done", **fields}
    return f"data: {json.dumps(payload)}\n\n"


def _sse_error(message: str) -> str:
    """Format an SSE error event so the frontend can display it."""
    return f'data: {{"type": "error", "content": {json.dumps(message)}}}\n\n'


class ChatManager:
    """Orchestrates the full RP chat pipeline."""

    def __init__(
        self,
        db: Database,
        context_engine: ContextEngine,
        prompt_assembler: PromptAssembler,
        llm_client: LLMClient,
        exchange_writer: ExchangeWriter,
    ) -> None:
        self.db = db
        self.context_engine = context_engine
        self.prompt_assembler = prompt_assembler
        self.llm_client = llm_client
        self.exchange_writer = exchange_writer

    @property
    def _chat_config(self) -> ChatConfig:
        """Read chat config dynamically so hot-reloaded config changes take effect."""
        return get_config().chat

    @property
    def _temperatures(self) -> LLMTemperaturesConfig:
        """Read temperature config dynamically."""
        return get_config().llm.temperatures

    @property
    def _model(self) -> str:
        """Resolve the model to use — config override or provider fallback."""
        return self._chat_config.model or self.llm_client.fallback_model

    def _prepare_message(
        self, user_message: str, message_mode: str, ooc: bool = False,
    ) -> tuple[str, str, str]:
        """Resolve message mode, parse direction markers.

        Returns (message_mode, save_user_message, direction_text).
        """
        if ooc and message_mode == "rp":
            message_mode = "ooc"

        save_user_message = user_message
        direction_text = ""
        if message_mode == "rp":
            parsed = parse_direction_markers(user_message)
            if parsed.has_direction:
                save_user_message = parsed.rp_content
                direction_text = parsed.direction_content
        elif message_mode == "direction":
            direction_text = user_message

        return message_mode, save_user_message, direction_text

    # ── Chat ─────────────────────────────────────────────────────────

    async def chat(
        self,
        user_message: str,
        rp_folder: str,
        branch: str,
        session_id: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        message_mode: str = "rp",
        ooc: bool = False,
        attach_card_ids: list[str] | None = None,
        scene_override: SceneOverride | None = None,
    ) -> ChatResponse:
        """Full non-streaming chat pipeline."""
        message_mode, save_user_message, direction_text = self._prepare_message(
            user_message, message_mode, ooc,
        )

        messages = await self._build_pipeline_messages(
            user_message, rp_folder, branch, session_id,
            attach_card_ids=attach_card_ids,
            scene_override=scene_override,
        )

        # Inject mode-specific system messages
        self._inject_mode_messages(messages, message_mode, direction_text)

        llm_response = await self.llm_client.generate(
            messages=messages,
            model=self._model,
            temperature=temperature if temperature is not None else self._temperatures.chat,
            max_tokens=max_tokens if max_tokens is not None else self._chat_config.max_tokens,
        )

        if message_mode == "ooc":
            return ChatResponse(
                response=llm_response.content,
                exchange_id=0,
                exchange_number=0,
                session_id=session_id,
                context_summary=None,
            )

        exchange_id, exchange_number = await self._save_exchange(
            rp_folder=rp_folder,
            branch=branch,
            session_id=session_id,
            user_message=save_user_message,
            assistant_response=llm_response.content,
            message_mode=message_mode,
        )

        return ChatResponse(
            response=llm_response.content,
            exchange_id=exchange_id,
            exchange_number=exchange_number,
            session_id=session_id,
            context_summary=None,
        )

    async def chat_stream(
        self,
        user_message: str,
        rp_folder: str,
        branch: str,
        session_id: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        message_mode: str = "rp",
        ooc: bool = False,
        attach_card_ids: list[str] | None = None,
        scene_override: SceneOverride | None = None,
    ) -> AsyncIterator[str]:
        """Streaming chat pipeline. Yields SSE-formatted events."""
        try:
            message_mode, save_user_message, direction_text = self._prepare_message(
                user_message, message_mode, ooc,
            )

            messages = await self._build_pipeline_messages(
                user_message, rp_folder, branch, session_id,
                attach_card_ids=attach_card_ids,
                scene_override=scene_override,
            )

            # Inject mode-specific system messages
            self._inject_mode_messages(messages, message_mode, direction_text)

            collected: list[str] = []
            async for token in self._stream_and_collect(
                collected,
                messages=messages,
                model=self._model,
                temperature=temperature if temperature is not None else self._temperatures.chat,
                max_tokens=max_tokens if max_tokens is not None else self._chat_config.max_tokens,
            ):
                yield token

            if message_mode == "ooc":
                yield _sse_done(exchange_id=0, exchange_number=0, ooc=True)
                return

            response_text = "".join(collected)
            exchange_id, exchange_number = await self._save_exchange(
                rp_folder=rp_folder,
                branch=branch,
                session_id=session_id,
                user_message=save_user_message,
                assistant_response=response_text,
                message_mode=message_mode,
            )

            yield _sse_done(exchange_id=exchange_id, exchange_number=exchange_number)
        except Exception as e:
            logger.exception("chat_stream failed")
            yield _sse_error(str(e))

    # ── Regenerate ───────────────────────────────────────────────────

    async def regenerate(
        self,
        rp_folder: str,
        branch: str,
        session_id: str,
        exchange_number: int | None = None,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> RegenerateResponse:
        """Regenerate an exchange response and save as a new variant."""
        exchange = await self._get_exchange(rp_folder, branch, exchange_number)
        use_temp, use_model = self._resolve_regen_params(temperature, model)

        messages = await self._build_pipeline_messages(
            exchange["user_message"], rp_folder, branch, session_id,
            exclude_exchange_number=exchange["exchange_number"],
        )

        llm_response = await self.llm_client.generate(
            messages=messages,
            model=use_model,
            temperature=use_temp,
            max_tokens=self._chat_config.max_tokens,
        )

        variant_id, variant_index, total = await self._save_variant(
            exchange, llm_response.content, use_model, use_temp,
        )

        return RegenerateResponse(
            response=llm_response.content,
            exchange_id=exchange["id"],
            exchange_number=exchange["exchange_number"],
            session_id=session_id,
            variant_id=variant_id,
            variant_index=variant_index,
            total_variants=total,
        )

    async def regenerate_stream(
        self,
        rp_folder: str,
        branch: str,
        session_id: str,
        exchange_number: int | None = None,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Streaming regeneration. Yields SSE events."""
        try:
            exchange = await self._get_exchange(rp_folder, branch, exchange_number)
            use_temp, use_model = self._resolve_regen_params(temperature, model)

            messages = await self._build_pipeline_messages(
                exchange["user_message"], rp_folder, branch, session_id,
                exclude_exchange_number=exchange["exchange_number"],
            )

            collected: list[str] = []
            async for token in self._stream_and_collect(
                collected,
                messages=messages,
                model=use_model,
                temperature=use_temp,
                max_tokens=self._chat_config.max_tokens,
            ):
                yield token

            response_text = "".join(collected)
            variant_id, variant_index, total = await self._save_variant(
                exchange, response_text, use_model, use_temp,
            )

            yield _sse_done(
                exchange_id=exchange["id"],
                exchange_number=exchange["exchange_number"],
                variant_id=variant_id,
                variant_index=variant_index,
                total_variants=total,
            )
        except Exception as e:
            logger.exception("regenerate_stream failed")
            yield _sse_error(str(e))

    async def swipe(
        self,
        rp_folder: str,
        branch: str,
        exchange_number: int,
        variant_index: int,
    ) -> SwipeResponse:
        """Switch the active variant for an exchange."""
        exchange = await self._get_exchange(rp_folder, branch, exchange_number)

        variants = await self.db.fetch_all(
            """SELECT id, assistant_response FROM exchange_variants
               WHERE exchange_id = ? ORDER BY id ASC""",
            [exchange["id"]],
        )

        if not variants:
            raise ValueError(f"No variants exist for exchange {exchange_number}")
        if variant_index < 0 or variant_index >= len(variants):
            raise ValueError(
                f"variant_index {variant_index} out of range (0-{len(variants) - 1})"
            )

        target = variants[variant_index]
        await self._activate_variant(exchange["id"], target["id"])

        # Update the main exchanges table
        await self.exchange_writer.update_response(
            exchange["id"], target["assistant_response"],
        )

        return SwipeResponse(
            exchange_number=exchange_number,
            active_variant=variant_index,
            total_variants=len(variants),
            response=target["assistant_response"],
        )

    # ── Continue ─────────────────────────────────────────────────────

    async def continue_response(
        self,
        rp_folder: str,
        branch: str,
        session_id: str,
        exchange_number: int | None = None,
        *,
        max_tokens: int | None = None,
    ) -> ContinueResponse:
        """Continue generating from where an exchange left off."""
        exchange = await self._get_exchange(rp_folder, branch, exchange_number)
        existing_response = exchange["assistant_response"]

        messages = self._build_continue_messages(
            await self._build_pipeline_messages(
                exchange["user_message"], rp_folder, branch, session_id,
                exclude_exchange_number=exchange["exchange_number"],
            ),
            existing_response,
        )

        use_max_tokens = max_tokens or self._chat_config.continue_max_tokens

        llm_response = await self.llm_client.generate(
            messages=messages,
            model=self._model,
            temperature=self._temperatures.chat,
            max_tokens=use_max_tokens,
        )

        continuation = llm_response.content
        full_response = existing_response + continuation

        await self.exchange_writer.update_response(exchange["id"], full_response)
        continue_count = await self._increment_continue_count(exchange["id"])

        return ContinueResponse(
            continuation=continuation,
            full_response=full_response,
            exchange_id=exchange["id"],
            exchange_number=exchange["exchange_number"],
            session_id=session_id,
            continue_count=continue_count,
        )

    async def continue_stream(
        self,
        rp_folder: str,
        branch: str,
        session_id: str,
        exchange_number: int | None = None,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Streaming continuation. Yields SSE events with new tokens only."""
        try:
            exchange = await self._get_exchange(rp_folder, branch, exchange_number)
            existing_response = exchange["assistant_response"]

            messages = self._build_continue_messages(
                await self._build_pipeline_messages(
                    exchange["user_message"], rp_folder, branch, session_id,
                    exclude_exchange_number=exchange["exchange_number"],
                ),
                existing_response,
            )

            use_max_tokens = max_tokens or self._chat_config.continue_max_tokens

            collected: list[str] = []
            async for token in self._stream_and_collect(
                collected,
                messages=messages,
                model=self._model,
                temperature=self._temperatures.chat,
                max_tokens=use_max_tokens,
            ):
                yield token

            continuation = "".join(collected)
            full_response = existing_response + continuation

            await self.exchange_writer.update_response(exchange["id"], full_response)
            continue_count = await self._increment_continue_count(exchange["id"])

            yield _sse_done(
                exchange_id=exchange["id"],
                exchange_number=exchange["exchange_number"],
                continue_count=continue_count,
            )
        except Exception as e:
            logger.exception("continue_stream failed")
            yield _sse_error(str(e))

    # ── Variant queries ──────────────────────────────────────────────

    async def get_variants(
        self, rp_folder: str, branch: str, exchange_number: int,
    ) -> tuple[int, list[dict]]:
        """Get all variants for an exchange. Returns (exchange_id, variants)."""
        exchange = await self._get_exchange(rp_folder, branch, exchange_number)
        variants = await self.db.fetch_all(
            """SELECT id, is_active, model_used, temperature, continue_count, created_at
               FROM exchange_variants
               WHERE exchange_id = ? ORDER BY id ASC""",
            [exchange["id"]],
        )
        return exchange["id"], [dict(v) for v in variants]

    # ── Private helpers ──────────────────────────────────────────────

    async def _stream_and_collect(
        self,
        collected: list[str],
        *,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Stream LLM response, collecting chunks and yielding SSE tokens.

        Callers pass in a mutable ``collected`` list which is filled with raw
        chunks.  After iteration, ``"".join(collected)`` gives the full text.
        """
        async for chunk in self.llm_client.generate_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            collected.append(chunk)
            yield _sse_token(chunk)

    @staticmethod
    def _build_continue_messages(
        messages: list[dict], existing_response: str,
    ) -> list[dict]:
        """Add continuation instruction as a user message.

        Uses instruction-based approach instead of assistant prefill
        because Anthropic models via OpenRouter reject conversations
        ending with an assistant message.
        """
        tail = existing_response[-1500:] if len(existing_response) > 1500 else existing_response
        ellipsis = "..." if len(existing_response) > 1500 else ""
        messages.append({
            "role": "user",
            "content": (
                "[Continue the narrative from exactly where it left off. "
                "Do not repeat any text already written. Pick up mid-sentence "
                "or mid-paragraph if needed. Write only the new continuation.]\n\n"
                f"The story so far ends with:\n\n{ellipsis}{tail}"
            ),
        })
        return messages

    async def _build_pipeline_messages(
        self,
        user_message: str,
        rp_folder: str,
        branch: str,
        session_id: str,
        *,
        attach_card_ids: list[str] | None = None,
        scene_override: SceneOverride | None = None,
        exclude_exchange_number: int | None = None,
    ) -> list[dict]:
        """Run context pipeline and build prompt messages."""
        request = ContextRequest(
            user_message=user_message,
            include_npc_reactions=False,
        )
        context_response = await self.context_engine.get_context(
            request=request,
            rp_folder=rp_folder,
            branch=branch,
            session_id=session_id,
        )

        # Inject attached cards as extra context documents
        if attach_card_ids:
            for card_id in attach_card_ids:
                card = await self.db.fetch_one(
                    "SELECT * FROM story_cards WHERE card_id = ? AND rp_folder = ?",
                    [card_id, rp_folder],
                )
                if card:
                    from rp_engine.models.context import ContextDocument
                    context_response.documents.append(ContextDocument(
                        name=card.get("name", card_id),
                        card_type=card.get("card_type", "unknown"),
                        file_path=card.get("file_path", ""),
                        content=card.get("content", "")[:2000],
                        relevance_score=2.0,
                        source="attached",
                        status="new",
                    ))

        # Apply scene override
        if scene_override:
            if context_response.scene_state:
                if scene_override.location:
                    context_response.scene_state.location = scene_override.location
                if scene_override.mood:
                    context_response.scene_state.mood = scene_override.mood
            else:
                from rp_engine.models.context import SceneState
                context_response.scene_state = SceneState(
                    location=scene_override.location or "Unknown",
                    mood=scene_override.mood,
                )

        return await self.prompt_assembler.build_messages(
            rp_folder=rp_folder,
            branch=branch,
            user_message=user_message,
            context_response=context_response,
            session_id=session_id,
            exclude_exchange_number=exclude_exchange_number,
        )

    @staticmethod
    def _inject_mode_messages(
        messages: list[dict], message_mode: str, direction_text: str,
    ) -> None:
        """Inject direction/OOC system messages into the prompt.

        For direction and mixed (inline markers): inserts a direction system
        message just before the final user message so the LLM sees the guidance.
        For OOC: inserts an instruction telling the LLM the user is speaking OOC.
        """
        if message_mode == "ooc":
            # Insert OOC instruction before the last user message
            messages.insert(-1, build_ooc_message())
        elif direction_text:
            # Insert direction guidance before the last user message
            messages.insert(-1, build_direction_message(direction_text))

    async def _save_exchange(
        self,
        rp_folder: str,
        branch: str,
        session_id: str,
        user_message: str,
        assistant_response: str,
        message_mode: str = "rp",
    ) -> tuple[int, int]:
        """Save exchange to DB, enqueue for analysis, embed in vector store."""
        return await self.exchange_writer.save_exchange(
            session_id=session_id,
            rp_folder=rp_folder,
            branch=branch,
            user_message=user_message,
            assistant_response=assistant_response,
            message_mode=message_mode,
        )

    async def _get_exchange(
        self, rp_folder: str, branch: str, exchange_number: int | None,
    ) -> dict:
        """Fetch an exchange row. If exchange_number is None, gets the latest."""
        if exchange_number is not None:
            row = await self.db.fetch_one(
                """SELECT * FROM exchanges
                   WHERE rp_folder = ? AND branch = ? AND exchange_number = ?""",
                [rp_folder, branch, exchange_number],
            )
            row = dict(row) if row else None
        else:
            row = await latest_exchange(self.db, rp_folder, branch)
        if not row:
            num_str = str(exchange_number) if exchange_number is not None else "latest"
            raise ValueError(f"Exchange {num_str} not found in {rp_folder}/{branch}")
        return row

    def _resolve_regen_params(
        self, temperature: float | None, model: str | None,
    ) -> tuple[float, str]:
        """Resolve temperature and model for regeneration."""
        use_temp = temperature if temperature is not None else (
            self._temperatures.chat + self._temperatures.chat_regenerate_bump
        )
        use_model = model or self._model
        return use_temp, use_model

    async def _deactivate_all_variants(self, exchange_id: int) -> None:
        """Deactivate all variants for an exchange."""
        f = await self.db.enqueue_write(
            "UPDATE exchange_variants SET is_active = 0 WHERE exchange_id = ?",
            [exchange_id],
            priority=PRIORITY_EXCHANGE,
        )
        await f

    async def _activate_variant(self, exchange_id: int, variant_id: int) -> None:
        """Deactivate all variants for an exchange, activate the specified one."""
        await self._deactivate_all_variants(exchange_id)
        f = await self.db.enqueue_write(
            "UPDATE exchange_variants SET is_active = 1 WHERE id = ?",
            [variant_id],
            priority=PRIORITY_EXCHANGE,
        )
        await f

    async def _save_variant(
        self,
        exchange: dict,
        response: str,
        model: str,
        temperature: float,
    ) -> tuple[int, int, int]:
        """Save a new variant for an exchange. Returns (variant_id, variant_index, total)."""
        exchange_id = exchange["id"]

        # Check variant count
        count = await self.db.fetch_val(
            "SELECT COUNT(*) FROM exchange_variants WHERE exchange_id = ?",
            [exchange_id],
        )

        # If no variants yet, save the original response as variant 0
        if count == 0:
            f = await self.db.enqueue_write(
                """INSERT INTO exchange_variants
                   (exchange_id, rp_folder, branch, exchange_number,
                    assistant_response, model_used, temperature, source,
                    is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, 'llm', 0, ?)""",
                [
                    exchange_id, exchange["rp_folder"], exchange["branch"],
                    exchange["exchange_number"], exchange["assistant_response"],
                    exchange["created_at"],
                ],
                priority=PRIORITY_EXCHANGE,
            )
            await f
            count = 1

        if count >= self._chat_config.max_variants:
            raise ValueError(
                f"Maximum variants ({self._chat_config.max_variants}) reached for exchange "
                f"{exchange['exchange_number']}"
            )

        now = datetime.now(UTC).isoformat()
        is_active = 1 if self._chat_config.auto_activate_regeneration else 0

        # Deactivate existing variants before inserting new active one
        if self._chat_config.auto_activate_regeneration:
            await self._deactivate_all_variants(exchange_id)

        f = await self.db.enqueue_write(
            """INSERT INTO exchange_variants
               (exchange_id, rp_folder, branch, exchange_number,
                assistant_response, model_used, temperature, source,
                is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'llm', ?, ?)""",
            [
                exchange_id, exchange["rp_folder"], exchange["branch"],
                exchange["exchange_number"], response, model, temperature,
                is_active, now,
            ],
            priority=PRIORITY_EXCHANGE,
        )
        variant_id = await f

        total = count + 1
        variant_index = total - 1

        # If auto-activate, update the main exchange table
        if self._chat_config.auto_activate_regeneration:
            await self.exchange_writer.update_response(exchange_id, response)

        return variant_id, variant_index, total

    async def _increment_continue_count(self, exchange_id: int) -> int:
        """Increment continue_count on the active variant. Returns new count.

        If no variant row exists (exchange was never regenerated), returns 1
        without writing — continue count is only tracked when variants are in use.
        """
        active_variant = await self.db.fetch_one(
            """SELECT id, continue_count FROM exchange_variants
               WHERE exchange_id = ? AND is_active = 1""",
            [exchange_id],
        )
        if not active_variant:
            return 1

        new_count = active_variant["continue_count"] + 1
        f = await self.db.enqueue_write(
            "UPDATE exchange_variants SET continue_count = ? WHERE id = ?",
            [new_count, active_variant["id"]],
            priority=PRIORITY_EXCHANGE,
        )
        await f
        return new_count
