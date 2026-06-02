"""Characterization lock for the LLM HTTP providers.

The real provider HTTP/retry/rate-limit path has NO other coverage — the suite
fakes at ``container.build_providers``, landing the FakeProvider *below* LLMClient.
These tests pin the behavior of the current ``OpenRouterProvider`` /
``OpenAICompatProvider`` against a mocked httpx transport so the upcoming
``_BaseHTTPProvider`` extraction can be proven behavior-preserving.

OpenRouter is the confirmed-working anchor (tested live before the OS move).

Mocking: each provider builds its own ``httpx.AsyncClient`` in ``__init__``; we
swap ``provider._client`` for a ``MockTransport``-backed client post-construction.
No production code is modified to test it.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from rp_engine.services.llm._concurrency import AdjustableLimit
from rp_engine.services.llm._openai_compat import OpenAICompatProvider
from rp_engine.services.llm._openrouter import OpenRouterProvider
from rp_engine.services.llm._types import LLMError


class _Recorder:
    """Programmable MockTransport handler: records requests, replays queued responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: list = []  # queue of httpx.Response | callable(request)->Response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Recorder ran out of queued responses")
        item = self.responses.pop(0)
        return item(request) if callable(item) else item


def _attach(provider, recorder: _Recorder):
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    return provider


def _chat_ok(content: str = "hello world", model: str = "anthropic/claude") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


@pytest.fixture
def captured_waits(monkeypatch):
    """Capture every asyncio.sleep duration without actually sleeping."""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return waits


# --------------------------------------------------------------------------
# OpenRouter — the confirmed-working anchor
# --------------------------------------------------------------------------


async def test_openrouter_generate_happy_path():
    rec = _Recorder()
    rec.responses = [_chat_ok(content="a reply", model="anthropic/claude")]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    resp = await prov.generate(messages=[{"role": "user", "content": "hi"}], model="anthropic/claude")

    assert resp.content == "a reply"
    assert resp.model == "anthropic/claude"
    assert resp.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    # Hits the chat endpoint, not embeddings.
    assert rec.requests[0].url == OpenRouterProvider.CHAT_URL


async def test_openrouter_always_sends_authorization_even_with_empty_key():
    rec = _Recorder()
    rec.responses = [_chat_ok()]
    prov = _attach(OpenRouterProvider(api_key=""), rec)

    await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")

    # OR always sends Authorization, even on an empty key — distinct from compat.
    assert rec.requests[0].headers["Authorization"] == "Bearer "


async def test_openrouter_empty_content_raises_and_warns(caplog):
    rec = _Recorder()
    rec.responses = [_chat_ok(content="   ")]  # whitespace-only == empty
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    with caplog.at_level("WARNING"):
        with pytest.raises(LLMError, match="empty content"):
            await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")

    assert any("empty content" in r.message for r in caplog.records)


async def test_openrouter_no_choices_raises():
    rec = _Recorder()
    rec.responses = [httpx.Response(200, json={"model": "m", "choices": []})]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    with pytest.raises(LLMError, match="No choices"):
        await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")


async def test_openrouter_429_retries_then_succeeds_waiting_on_reset_header(captured_waits):
    # reset is an ABSOLUTE epoch ~10s in the future → wait ≈ 10 (clamped to [0.5, 30]).
    import time

    reset_at = time.time() + 10
    rec = _Recorder()
    rec.responses = [
        httpx.Response(429, headers={"x-ratelimit-reset": str(reset_at)}, text="rate limited"),
        _chat_ok(content="recovered"),
    ]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    resp = await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")

    assert resp.content == "recovered"
    assert len(rec.requests) == 2  # retried exactly once
    assert len(captured_waits) == 1
    # Derived from x-ratelimit-reset (epoch), NOT a fixed Retry-After default.
    assert 9.0 < captured_waits[0] <= 10.1


async def test_openrouter_400_raises_labeled_error():
    rec = _Recorder()
    rec.responses = [httpx.Response(400, text="bad request detail")]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    with pytest.raises(LLMError, match="OpenRouter API error 400") as exc:
        await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")
    assert exc.value.status_code == 400


async def test_openrouter_timeout_raises_llm_error():
    rec = _Recorder()

    def _raise_timeout(_):
        raise httpx.TimeoutException("slow")

    rec.responses = [_raise_timeout]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    with pytest.raises(LLMError, match="timed out"):
        await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")


async def test_openrouter_embed_sorts_by_index():
    rec = _Recorder()
    rec.responses = [
        httpx.Response(
            200,
            json={
                "data": [
                    {"index": 2, "embedding": [0.2]},
                    {"index": 0, "embedding": [0.0]},
                    {"index": 1, "embedding": [0.1]},
                ]
            },
        )
    ]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    vecs = await prov.embed(texts=["a", "b", "c"], model="embed")

    assert vecs == [[0.0], [0.1], [0.2]]
    assert rec.requests[0].url == OpenRouterProvider.EMBED_URL


async def test_openrouter_generate_stream_yields_chunks_until_done():
    rec = _Recorder()
    sse = "data: " + '{"choices":[{"delta":{"content":"He"}}]}' + "\n"
    sse += "data: " + '{"choices":[{"delta":{"content":"llo"}}]}' + "\n"
    sse += "data: [DONE]\n"
    rec.responses = [httpx.Response(200, text=sse)]
    prov = _attach(OpenRouterProvider(api_key="k"), rec)

    chunks = [
        c async for c in prov.generate_stream(messages=[{"role": "user", "content": "hi"}], model="m")
    ]
    assert chunks == ["He", "llo"]


# --------------------------------------------------------------------------
# OpenAICompat — the three real divergences to preserve through extraction
# --------------------------------------------------------------------------


async def test_compat_omits_authorization_when_no_key():
    rec = _Recorder()
    rec.responses = [_chat_ok()]
    prov = _attach(OpenAICompatProvider(base_url="http://local:1234/v1"), rec)

    await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")

    # Divergence from OR: no Authorization header when api_key is None.
    assert "Authorization" not in rec.requests[0].headers
    # URL derived from base_url.
    assert str(rec.requests[0].url) == "http://local:1234/v1/chat/completions"


async def test_compat_includes_authorization_when_key_set():
    rec = _Recorder()
    rec.responses = [_chat_ok()]
    prov = _attach(OpenAICompatProvider(base_url="http://local:1234/v1", api_key="sk-x"), rec)

    await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")
    assert rec.requests[0].headers["Authorization"] == "Bearer sk-x"


async def test_compat_429_waits_on_retry_after_seconds(captured_waits):
    rec = _Recorder()
    rec.responses = [
        httpx.Response(429, headers={"Retry-After": "7"}, text="rate limited"),
        _chat_ok(content="recovered"),
    ]
    prov = _attach(OpenAICompatProvider(base_url="http://local/v1"), rec)

    resp = await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")

    assert resp.content == "recovered"
    # Divergence from OR: Retry-After is relative SECONDS, used directly (clamped ≤ 30).
    assert captured_waits == [7.0]


async def test_compat_400_uses_plain_error_label():
    rec = _Recorder()
    rec.responses = [httpx.Response(400, text="nope")]
    prov = _attach(OpenAICompatProvider(base_url="http://local/v1"), rec)

    with pytest.raises(LLMError, match="API error 400") as exc:
        await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")
    # Plain "API error", not "OpenRouter API error".
    assert "OpenRouter" not in str(exc.value)
    assert exc.value.status_code == 400


async def test_compat_empty_content_raises():
    rec = _Recorder()
    rec.responses = [_chat_ok(content="")]
    prov = _attach(OpenAICompatProvider(base_url="http://local/v1"), rec)

    # Lock only that it RAISES — the empty-content *warning* is intentionally
    # unified to both-providers in the extraction, so don't pin its absence here.
    with pytest.raises(LLMError, match="empty content"):
        await prov.generate(messages=[{"role": "user", "content": "hi"}], model="m")


# --------------------------------------------------------------------------
# AdjustableLimit — the resizable concurrency gate (replaces the buggy
# object-swap semaphore resize). Deterministic, no real server.
# --------------------------------------------------------------------------


async def test_adjustable_limit_admits_up_to_limit():
    lim = AdjustableLimit(limit=2, ceiling=5)
    await lim.__aenter__()
    await lim.__aenter__()
    assert lim.in_use == 2
    # third acquire blocks until a slot frees
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lim.__aenter__(), timeout=0.05)
    await lim.__aexit__(None, None, None)
    await lim.__aexit__(None, None, None)


async def test_adjustable_limit_constructor_clamps_to_ceiling():
    lim = AdjustableLimit(limit=10, ceiling=3)
    assert lim.ceiling == 3
    assert lim.limit == 3  # clamped down at construction


async def test_set_limit_clamps_floor_and_ceiling():
    lim = AdjustableLimit(limit=3, ceiling=4)
    await lim.set_limit(100)
    assert lim.limit == 4  # never above ceiling
    await lim.set_limit(0)
    assert lim.limit == 1  # never below 1


async def test_shrink_does_not_evict_inflight_and_withholds_new_admits():
    lim = AdjustableLimit(limit=3, ceiling=5)
    await lim.__aenter__()
    await lim.__aenter__()
    assert lim.in_use == 2

    await lim.set_limit(1)  # shrink BELOW current in-use
    assert lim.limit == 1
    assert lim.in_use == 2  # in-flight work was NOT evicted (the original bug)

    # New admits are withheld while in_use (2) >= limit (1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lim.__aenter__(), timeout=0.05)

    await lim.__aexit__(None, None, None)  # in_use -> 1, still >= limit 1
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lim.__aenter__(), timeout=0.05)

    await lim.__aexit__(None, None, None)  # in_use -> 0 < limit 1
    await asyncio.wait_for(lim.__aenter__(), timeout=0.1)  # now admitted
    await lim.__aexit__(None, None, None)


async def test_grow_wakes_blocked_waiter():
    lim = AdjustableLimit(limit=1, ceiling=5)
    await lim.__aenter__()  # hold the only slot
    waiter = asyncio.create_task(lim.__aenter__())
    await asyncio.sleep(0.02)
    assert not waiter.done()  # genuinely blocked

    await lim.set_limit(2)  # grow → waiter should wake
    await asyncio.wait_for(waiter, timeout=0.2)
    assert lim.in_use == 2

    await lim.__aexit__(None, None, None)
    await lim.__aexit__(None, None, None)


async def test_release_wakes_blocked_waiter():
    lim = AdjustableLimit(limit=1, ceiling=5)
    await lim.__aenter__()
    waiter = asyncio.create_task(lim.__aenter__())
    await asyncio.sleep(0.02)
    assert not waiter.done()

    await lim.__aexit__(None, None, None)  # release → waiter wakes
    await asyncio.wait_for(waiter, timeout=0.2)
    assert lim.in_use == 1
    await lim.__aexit__(None, None, None)
