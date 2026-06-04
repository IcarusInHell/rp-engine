"""LLM provider system -- multi-backend support with provider routing."""

from rp_engine.services.llm._client import LLMClient
from rp_engine.services.llm._concurrency import AdjustableLimit
from rp_engine.services.llm._factory import build_providers
from rp_engine.services.llm._openai_compat import OpenAICompatProvider
from rp_engine.services.llm._openrouter import OpenRouterProvider
from rp_engine.services.llm._types import LLMError, LLMProvider, LLMResponse

__all__ = [
    "AdjustableLimit",
    "LLMClient",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "OpenRouterProvider",
    "build_providers",
]
