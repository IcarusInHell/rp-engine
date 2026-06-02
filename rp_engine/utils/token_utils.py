"""Shared token counting utilities for intelligence packages."""

from collections.abc import Callable


def default_token_counter(text: str) -> int:
    """Fallback: ~1.3 tokens per whitespace-delimited word for English prose."""
    return int(len(text.split()) * 1.3)


def try_tiktoken_counter(model: str = "gpt-4") -> Callable[[str], int] | None:
    """Return a tiktoken-based counter if tiktoken is installed, else None."""
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return lambda text: len(enc.encode(text))
    except ImportError:
        return None


def resolve_token_counter(model: str = "gpt-4") -> Callable[[str], int]:
    """Return tiktoken when installed, else the word-count fallback.

    Single entry point for prompt-assembly token budgeting (Phase 4): exact
    counts aren't needed — the goal is preventing overflow, not precision.
    """
    return try_tiktoken_counter(model) or default_token_counter


def estimate_messages_tokens(
    messages: list[dict],
    counter: Callable[[str], int] = default_token_counter,
) -> int:
    """Sum the token estimate over a message list's ``content`` fields."""
    return sum(counter(m.get("content") or "") for m in messages)
