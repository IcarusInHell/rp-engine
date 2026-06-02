"""Configuration loading from config.yaml + environment variable overrides."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 3000
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ]
    lan_access: bool = False
    api_key: str | None = None  # Bearer token for /v1/* endpoints; None = no auth


class PathsConfig(BaseModel):
    vault_root: str = ".."
    db_path: str = "data/rp-engine.db"


class LLMModelsConfig(BaseModel):
    npc_reactions: str = "anthropic/claude-haiku"
    response_analysis: str = "google/gemini-2.0-flash-001"
    card_generation: str = "google/gemini-2.0-flash-001"
    embeddings: str = "openai/text-embedding-3-small"


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""
    type: str = "openrouter"           # "openrouter" | "openai_compat"
    base_url: str | None = None        # Required for openai_compat
    api_key: str | None = None         # "env:VAR_NAME" or literal or None
    timeout: float = 30.0
    max_concurrency: int = 5


class LLMModeConfig(BaseModel):
    """Chat backend selection. Controls which endpoint accepts chat requests."""
    chat: Literal["provider", "sdk"] = "provider"


class LLMTemperaturesConfig(BaseModel):
    """User-tunable temperatures for creative roles. Analytical roles stay hardcoded."""
    chat: float = 0.7
    chat_regenerate_bump: float = 0.05
    npc_reactions: float = 0.6
    summary: float = 0.5


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    api_key: str = "env:OPENROUTER_API_KEY"
    providers: dict[str, ProviderConfig] = {}
    models: LLMModelsConfig = LLMModelsConfig()
    fallback_model: str = "google/gemini-2.0-flash-001"
    embedding_fallback_provider: str | None = None
    mode: LLMModeConfig = LLMModeConfig()
    temperatures: LLMTemperaturesConfig = LLMTemperaturesConfig()


class PacingPresets(BaseModel):
    fast: dict[str, int] = {"gentle": 3, "moderate": 5, "strong": 8}
    moderate: dict[str, int] = {"gentle": 5, "moderate": 10, "strong": 15}
    slow: dict[str, int] = {"gentle": 8, "moderate": 15, "strong": 20}


class TierThresholds(BaseModel):
    """Score cutoffs for relevance-based injection tiers (Phase 3 tiered context).

    A card scoring >= ``full`` (or sourced from always_load) gets its complete
    body; >= ``brief`` gets a compact summary; below ``brief`` gets a one-line
    reference. Defaults match the source-score layout: always_load=2.0 and
    keyword=1.0 land in full; trigger=0.9 / semantic=0.8 / graph-1hop=0.6 land in
    brief; graph-2hop=0.3 lands in reference.
    """
    full: float = 1.0
    brief: float = 0.6


class ContextConfig(BaseModel):
    max_documents: int = 5
    max_graph_hops: int = 2
    stale_threshold_turns: int = 8
    tier_thresholds: TierThresholds = TierThresholds()
    max_past_exchanges: int = 5
    exclude_recent_exchanges: int = 3
    past_exchange_min_score: float = 0.65
    max_extracted_memories: int = 10
    extracted_memory_min_score: float = 0.5
    include_custom_state: bool = True
    max_card_content_length: int = 5000
    pacing_presets: PacingPresets = PacingPresets()
    # Phase 5b — file-drop lorebook. Matching reuses TriggerEvaluator; budget is
    # char-based (mirrors the Phase 3 tiered-context content[:max_len] slicing —
    # no token estimator). Disabled by default (no # World Info section emitted).
    lorebook_enabled: bool = False
    lorebook_global_path: str | None = None   # global-library folder (machine-global)
    lorebook_budget_chars: int = 4000          # per-RP injection budget
    lorebook_shared_budget_chars: int = 2000   # global-library injection budget


class ChatConfig(BaseModel):
    exchange_window: int = 10
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4000
    max_variants: int = 15
    regenerate_temperature_bump: float = 0.05
    auto_activate_regeneration: bool = True
    continue_max_tokens: int = 2000
    auto_detect_truncation: bool = True


class InjectionConfig(BaseModel):
    """Per-section injection depth for dynamic context (SillyTavern Feature A).

    Depth N means the section is injected as a system message N exchange-pairs
    from the bottom of the message list (the current user turn is the depth-1
    anchor); depth 0 keeps the section in the top system message (legacy
    behavior). Only consulted when ``enabled`` is True — default off so Phase 4
    is a drop-in upgrade and existing prompts are byte-identical until opted in.

    The default ``depths`` map encodes the roadmap's Prompt Structure decision
    (scene/NPC/threads near recent messages at depth 4, triggered notes at 2,
    direction at 1). Sections absent from the map stay at depth 0. ``narrator_note``
    is forward-compat only — its producer ships in Phase 5.
    """
    enabled: bool = False
    depths: dict[str, int] = {
        "scene_context": 4,
        "character_states": 4,
        "custom_state": 4,
        "npc_briefs": 4,
        "knowledge_boundaries": 4,
        "plot_threads": 4,
        "card_gaps": 4,
        "triggered_notes": 2,
        "narrator_note": 2,
        "direction": 1,
    }


class TokenBudgetAllocation(BaseModel):
    """Fraction of the total token budget assigned to each prompt region."""
    system: float = 0.20
    context: float = 0.15
    history: float = 0.60
    reserve: float = 0.05


class TokenBudgetConfig(BaseModel):
    """Token-aware history windowing (SillyTavern Feature B).

    When ``enabled``, history is filled newest-first until the history budget is
    exhausted instead of the blind ``chat.exchange_window`` count. Default off —
    legacy ``exchange_window`` behavior is preserved until opted in.
    """
    enabled: bool = False
    model_context_window: int = 128000
    safety_margin: int = 500
    allocation: TokenBudgetAllocation = TokenBudgetAllocation()


class ExampleDialogueConfig(BaseModel):
    """Few-shot character dialogue examples (SillyTavern Feature D).

    Parsed from a card body's ``## Example Dialogue`` section at prompt time and
    injected as user/assistant pairs before real history. PC cards
    (``is_player_character: true``) are skipped — user-controlled voices need no
    demonstration. Enabled by default (standard message format, low risk).
    """
    enabled: bool = True
    max_examples: int = 3
    pin_examples: bool = False


class PromptConfig(BaseModel):
    """SillyTavern-inspired prompt assembly controls (Phase 4 / 5a)."""
    injection: InjectionConfig = InjectionConfig()
    token_budget: TokenBudgetConfig = TokenBudgetConfig()
    example_dialogue: ExampleDialogueConfig = ExampleDialogueConfig()
    # Phase 5a: morphology-aware trigger/lorebook keyword matching. Default ON
    # (a fire that matched before still matches — stemming only *adds* matches via
    # union with raw substring). Global kill-switch back to exact matching.
    trigger_stemming: bool = True


class SearchConfig(BaseModel):
    vector_weight: float = 0.7
    bm25_weight: float = 0.3
    similarity_threshold: float = 0.7
    chunk_size: int = 1000
    chunk_overlap: int = 200
    embedding_dimension: int = 1536
    chunking_strategy: str = "fixed"  # "fixed" or "by_character"
    vector_cache_max: int = 8  # LRU cache size for loaded embedding matrices


class NPCConfig(BaseModel):
    history_search_limit: int = 3
    history_min_score: float = 0.5


class ModifierTrustEffect(BaseModel):
    """Trust effects for a behavioral modifier (e.g., PARANOID, GRIEF_CONSUMED)."""
    ceiling_offset: int = 0
    gain_multiplier: float = 1.0
    loss_multiplier: float = 1.0
    instant_shifts: dict[str, int] = {}
    note: str = ""


class TrustConfig(BaseModel):
    increase_value: int = 1
    decrease_value: int = 2
    session_max_gain: int = 8
    session_max_loss: int = -15
    min_score: int = -50
    max_score: int = 50
    modifier_effects: dict[str, ModifierTrustEffect] = {}


class AutoReportConfig(BaseModel):
    enabled: bool = False
    url: str = ""                    # webhook URL to POST logs to
    on_error: bool = True            # auto-send on unhandled errors
    on_session_end: bool = False     # auto-send when a session ends


class DiagnosticConfig(BaseModel):
    enabled: bool = False
    level: str = "full"              # "full" | "metadata"
    max_file_size_mb: int = 50       # rotate after this size
    max_files: int = 10              # keep this many archived files
    auto_purge_days: int = 30        # purge files older than this
    auto_report: AutoReportConfig = AutoReportConfig()
    reporter_key: str = ""           # auto-generated UUID if empty


class AnalysisConfig(BaseModel):
    undo_cascade_depth: int = 5


class AutoSaveConfig(BaseModel):
    enabled: bool = False


class ContinuityConfig(BaseModel):
    enabled: bool = False
    max_search_results: int = 5
    min_similarity: float = 0.65


class RPConfig(BaseModel):
    default_pov_character: str = "Lilith"


class RPEngineConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RP_ENGINE_",
        env_file=(".env", "../.env"),
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = ServerConfig()
    paths: PathsConfig = PathsConfig()
    llm: LLMConfig = LLMConfig()
    context: ContextConfig = ContextConfig()
    chat: ChatConfig = ChatConfig()
    prompt: PromptConfig = PromptConfig()
    search: SearchConfig = SearchConfig()
    npc: NPCConfig = NPCConfig()
    trust: TrustConfig = TrustConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    auto_save: AutoSaveConfig = AutoSaveConfig()
    continuity: ContinuityConfig = ContinuityConfig()
    diagnostics: DiagnosticConfig = DiagnosticConfig()
    rp: RPConfig = RPConfig()

    # Standalone field — picks up OPENROUTER_API_KEY env var (no RP_ENGINE_ prefix)
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")

    def effective_api_key(self) -> str:
        """Return the best available API key."""
        return self.llm.api_key if self.llm.api_key != "env:OPENROUTER_API_KEY" else self.openrouter_api_key

    def resolve_paths(self) -> None:
        """Resolve vault_root and db_path relative to PROJECT_ROOT."""
        self.paths.vault_root = str((PROJECT_ROOT / self.paths.vault_root).resolve())
        self.paths.db_path = str((PROJECT_ROOT / self.paths.db_path).resolve())


def _load_yaml_defaults() -> dict:
    """Load default config from config.yaml."""
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
            return data if data else {}
    return {}


@lru_cache(maxsize=1)
def get_config() -> RPEngineConfig:
    """Get the singleton configuration instance."""
    yaml_data = _load_yaml_defaults()
    config = RPEngineConfig(**yaml_data)
    config.resolve_paths()
    logger.info("Configuration loaded (vault_root=%s)", config.paths.vault_root)
    return config
