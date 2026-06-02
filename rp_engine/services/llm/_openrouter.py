"""OpenRouter LLM provider — fixed URLs, always-auth, reset-header backoff."""

from __future__ import annotations

import time

import httpx

from rp_engine.services.llm._base import _BaseHTTPProvider


class OpenRouterProvider(_BaseHTTPProvider):
    """Provider for the OpenRouter API.

    Thin subclass of :class:`_BaseHTTPProvider`; only the OpenRouter-specific
    hooks live here: fixed endpoint URLs, an always-present ``Authorization``
    header, and 429 backoff derived from the ``x-ratelimit-reset`` epoch.
    """

    CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
    EMBED_URL = "https://openrouter.ai/api/v1/embeddings"
    _error_label = "OpenRouter API error"

    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        max_concurrency: int = 5,
    ) -> None:
        super().__init__(api_key=api_key, timeout=timeout, max_concurrency=max_concurrency)

    def _chat_url(self) -> str:
        return self.CHAT_URL

    def _embed_url(self) -> str:
        return self.EMBED_URL

    def _headers(self) -> dict:
        # OpenRouter always sends Authorization, even on an empty key.
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _rate_limit_wait(self, resp: httpx.Response) -> float:
        # x-ratelimit-reset is an absolute epoch; wait until then, clamped.
        reset_at = resp.headers.get("x-ratelimit-reset")
        if reset_at:
            try:
                return min(max(0.5, float(reset_at) - time.time()), 30.0)
            except (ValueError, TypeError):
                return 2.0
        return 2.0
