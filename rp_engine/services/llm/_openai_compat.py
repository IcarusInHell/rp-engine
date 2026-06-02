"""OpenAI-compatible provider — base_url URLs, optional auth, Retry-After backoff."""

from __future__ import annotations

import httpx

from rp_engine.services.llm._base import _BaseHTTPProvider


class OpenAICompatProvider(_BaseHTTPProvider):
    """Provider for any OpenAI-compatible API (Ollama, LM Studio, vLLM, etc.).

    Thin subclass of :class:`_BaseHTTPProvider`; only the compat-specific hooks
    live here: URLs derived from ``base_url``, an ``Authorization`` header sent
    only when an API key is configured, and standard ``Retry-After`` 429 backoff.
    Has no rate-limit headers, so ``_on_response`` stays the inert base no-op.
    """

    _error_label = "API error"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_concurrency: int = 3,
    ) -> None:
        super().__init__(api_key=api_key, timeout=timeout, max_concurrency=max_concurrency)
        self._base_url = base_url.rstrip("/")

    def _chat_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _embed_url(self) -> str:
        return f"{self._base_url}/embeddings"

    def _headers(self) -> dict:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _rate_limit_wait(self, resp: httpx.Response) -> float:
        # Retry-After is relative seconds; use directly, clamped.
        retry_after = resp.headers.get("Retry-After", "2")
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            return 2.0
