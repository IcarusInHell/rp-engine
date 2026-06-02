"""LLM client router -- dispatches to providers based on model spec."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from rp_engine.config import LLMModelsConfig, RPEngineConfig
from rp_engine.services.llm._types import LLMError, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

KNOWN_EMBEDDING_MODELS = {
    "openai/text-embedding-3-small",
    "openai/text-embedding-3-large",
    "openai/text-embedding-ada-002",
}


class LLMClient:
    """Router that dispatches to providers. Drop-in replacement for the old monolithic client."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        default_provider: str,
        models: LLMModelsConfig,
        fallback_model: str,
        embedding_fallback: str | None = None,
    ) -> None:
        if default_provider not in providers:
            raise ValueError(
                f"Default provider '{default_provider}' not in providers: {list(providers)}"
            )
        self._providers = providers
        self._default_provider_name = default_provider
        self._models = models
        self._fallback = fallback_model
        self._embedding_fallback = embedding_fallback
        self.diagnostic_logger = None  # injected by container

        if models.embeddings not in KNOWN_EMBEDDING_MODELS:
            logger.warning(
                "Embedding model '%s' is not a known embedding model. "
                "Embeddings may fail. Known models: %s",
                models.embeddings,
                sorted(KNOWN_EMBEDDING_MODELS),
            )

    @property
    def models(self) -> LLMModelsConfig:
        return self._models

    @property
    def fallback_model(self) -> str:
        """Public access to fallback model. Replaces private _fallback access."""
        return self._fallback

    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.6,
        max_tokens: int = 1500,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Route to the resolved provider, generate a completion, and log timing/usage diagnostics."""
        provider, resolved_model = self._resolve(model)
        start = time.perf_counter()
        try:
            result = await provider.generate(
                messages=messages,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            if self.diagnostic_logger:
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.diagnostic_logger.log(
                    category="llm",
                    event="generate",
                    data={
                        "provider": self._default_provider_name,
                        "model": resolved_model,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "prompt_tokens": result.usage.get("prompt_tokens"),
                        "completion_tokens": result.usage.get("completion_tokens"),
                        "elapsed_ms": round(elapsed_ms, 1),
                        "streaming": False,
                    },
                    content={
                        "messages": messages,
                        "response": result.content[:2000] if result.content else None,
                    },
                )
            return result
        except Exception as exc:
            if self.diagnostic_logger:
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.diagnostic_logger.log_error(
                    event="generate_error",
                    data={
                        "provider": self._default_provider_name,
                        "model": resolved_model,
                        "elapsed_ms": round(elapsed_ms, 1),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
            raise

    async def generate_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.6,
        max_tokens: int = 1500,
    ) -> AsyncIterator[str]:
        """Route to the resolved provider and stream completion chunks."""
        provider, resolved_model = self._resolve(model)
        async for chunk in provider.generate_stream(
            messages=messages,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        """Embed texts via the resolved provider, with fallback-provider retry on LLMError."""
        resolved_model = model or self._models.embeddings
        provider, final_model = self._resolve(resolved_model)
        start = time.perf_counter()
        try:
            result = await provider.embed(texts=texts, model=final_model)
            if self.diagnostic_logger:
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.diagnostic_logger.log(
                    category="llm",
                    event="embed",
                    data={
                        "provider": self._default_provider_name,
                        "model": final_model,
                        "text_count": len(texts),
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                )
            return result
        except LLMError:
            if self._embedding_fallback and self._embedding_fallback != self._default_provider_name:
                fallback = self._providers.get(self._embedding_fallback)
                if fallback:
                    logger.warning(
                        "Embedding failed on %s, falling back to %s",
                        self._default_provider_name, self._embedding_fallback,
                    )
                    return await fallback.embed(texts=texts, model=final_model)
            if self.diagnostic_logger:
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.diagnostic_logger.log_error(
                    event="embed_error",
                    data={
                        "provider": self._default_provider_name,
                        "model": final_model,
                        "text_count": len(texts),
                        "elapsed_ms": round(elapsed_ms, 1),
                    },
                )
            raise

    async def reload_providers(self, config: RPEngineConfig) -> None:
        """Hot-swap providers from updated config. Closes old clients."""
        from rp_engine.services.llm._factory import build_providers

        new_providers = build_providers(config)
        default = config.llm.provider

        if default not in new_providers:
            logger.error(
                "Default provider '%s' not in new providers %s — aborting reload",
                default, list(new_providers),
            )
            for p in new_providers.values():
                await p.close()
            return

        # Swap new providers in before closing old ones so in-flight requests
        # that already grabbed a provider reference still work.
        old_providers = self._providers
        self._providers = new_providers
        self._default_provider_name = default
        self._models = config.llm.models
        self._fallback = config.llm.fallback_model
        self._embedding_fallback = config.llm.embedding_fallback_provider

        for name, provider in old_providers.items():
            try:
                await provider.close()
            except Exception:
                logger.warning("Error closing old provider %s", name, exc_info=True)

        logger.info("LLM providers reloaded: %s (default=%s)", list(new_providers), default)

    async def close(self) -> None:
        """Close every underlying provider client."""
        for name, provider in self._providers.items():
            try:
                await provider.close()
            except Exception:
                logger.warning("Error closing provider %s", name, exc_info=True)

    def _resolve(self, model: str | None) -> tuple[LLMProvider, str]:
        """Resolve a model spec to (provider, model_name).

        Formats:
        - None              -> (default_provider, fallback_model)
        - "model-name"      -> (default_provider, "model-name")
        - "provider:model"  -> (named_provider, "model")
          Falls through to default if left side of ":" isn't a known provider name.
          OpenRouter-style names like "anthropic/claude-haiku" use "/" not ":" so
          they pass through safely.
        """
        if model is None:
            return self._providers[self._default_provider_name], self._fallback

        if ":" in model:
            provider_name, model_name = model.split(":", 1)
            if provider_name in self._providers:
                return self._providers[provider_name], model_name

        return self._providers[self._default_provider_name], model
