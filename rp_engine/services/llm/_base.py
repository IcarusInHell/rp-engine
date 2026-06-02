"""Shared HTTP-provider skeleton for OpenAI-style chat/complet/embed backends.

``OpenRouterProvider`` and ``OpenAICompatProvider`` differ only at a few points —
URL construction, header policy, and 429 rate-limit handling. Everything else
(body building, the concurrency gate, response parsing, the retry loop) is
identical. That shared skeleton lives here; subclasses fill the hooks.

Hooks a subclass overrides:
    _chat_url()          -> the chat/completions URL
    _embed_url()         -> the embeddings URL
    _headers()           -> request headers (auth policy lives here)
    _error_label         -> class attr, the >=400 error message prefix
    _rate_limit_wait()   -> seconds to wait on a 429 (header-derived)
    _on_response()       -> async hook after each response (adaptive throttle, etc.)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from rp_engine.services.llm._concurrency import AdjustableLimit
from rp_engine.services.llm._sse import parse_sse_line
from rp_engine.services.llm._types import LLMError, LLMResponse

logger = logging.getLogger(__name__)


class _BaseHTTPProvider:
    """Skeleton for OpenAI-compatible HTTP providers. Not used directly."""

    #: Prefix for >=400 error messages (e.g. "OpenRouter API error").
    _error_label: str = "API error"

    def __init__(self, api_key: str | None, timeout: float, max_concurrency: int) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)
        self._api_key = api_key
        # max_concurrency is the user's hard ceiling; adaptive logic only pulls down.
        self._gate = AdjustableLimit(limit=max_concurrency, ceiling=max_concurrency)

    # ---- subclass hooks -------------------------------------------------

    def _chat_url(self) -> str:
        raise NotImplementedError

    def _embed_url(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict:
        raise NotImplementedError

    def _rate_limit_wait(self, resp: httpx.Response) -> float:
        """Seconds to wait before retrying a 429, derived from response headers."""
        raise NotImplementedError

    async def _on_response(self, resp: httpx.Response) -> None:
        """Hook run after every (non-exception) response. Default: no-op.

        OpenRouter overrides this to drive adaptive concurrency from rate-limit
        headers; backends without such headers leave it inert.
        """
        return None

    # ---- shared implementation ------------------------------------------

    async def generate(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.6,
        max_tokens: int = 1500,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Send a chat completion request and return a normalized LLMResponse."""
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        async with self._gate:
            resp = await self._request(body, self._chat_url())

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMError("No choices in response", status_code=resp.status_code)

        content = choices[0].get("message", {}).get("content", "")
        if not content or not content.strip():
            logger.warning("LLM returned empty content for model %s", model)
            raise LLMError("LLM returned empty content", status_code=resp.status_code)

        return LLMResponse(
            content=content,
            model=data.get("model", model),
            usage=data.get("usage", {}),
        )

    async def generate_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.6,
        max_tokens: int = 1500,
    ) -> AsyncIterator[str]:
        """Stream a chat completion response, yielding content chunks."""
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with self._gate, self._client.stream(
            "POST", self._chat_url(), json=body, headers=self._headers()
        ) as resp:
            if resp.status_code >= 400:
                error_body = await resp.aread()
                raise LLMError(
                    f"{self._error_label} {resp.status_code}: {error_body.decode()[:500]}",
                    status_code=resp.status_code,
                )

            async for line in resp.aiter_lines():
                content, is_done = parse_sse_line(line)
                if is_done:
                    break
                if content:
                    yield content

    async def embed(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        """Generate embeddings, returned in input order."""
        body = {"model": model, "input": texts}

        async with self._gate:
            resp = await self._request(body, self._embed_url())

        data = resp.json()
        embeddings = data.get("data", [])
        return [item["embedding"] for item in sorted(embeddings, key=lambda x: x["index"])]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        body: dict,
        url: str,
        max_retries: int = 1,
    ) -> httpx.Response:
        """POST with one retry on 429; wait/headers/label come from subclass hooks."""
        headers = self._headers()
        for attempt in range(max_retries + 1):
            try:
                resp = await self._client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMError(f"Request timed out: {e}") from e

            await self._on_response(resp)

            if resp.status_code == 429 and attempt < max_retries:
                wait = self._rate_limit_wait(resp)
                logger.warning("Rate limited (429), waiting %.1fs before retry", wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise LLMError(
                    f"{self._error_label} {resp.status_code}: {resp.text[:500]}",
                    status_code=resp.status_code,
                )

            return resp

        raise LLMError("Max retries exceeded")
