"""Pydantic models for RP management and guidelines."""

from __future__ import annotations

from pydantic import BaseModel

from rp_engine.models.enums import (
    NarrativeVoice,
    PovMode,
    ResponseLength,
    ScenePacing,
    Tense,
)


class ChunkingConfig(BaseModel):
    strategy: str = "fixed"
    chunk_size: int = 1000
    chunk_overlap: int = 200


class ChunkingUpdate(BaseModel):
    strategy: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class RechunkResponse(BaseModel):
    status: str
    total_exchanges: int = 0
    embedded: int = 0
    skipped: int = 0
    failed: int = 0


class RPCreate(BaseModel):
    rp_name: str
    pov_mode: PovMode = "single"
    dual_characters: list[str] = []
    tone: str = ""
    scene_pacing: ScenePacing = "moderate"
    narrative_voice: NarrativeVoice = "third"
    tense: Tense = "past"
    response_length: ResponseLength = "medium"


class RPResponse(BaseModel):
    rp_folder: str
    created_files: list[str] = []


class RPInfo(BaseModel):
    rp_folder: str
    has_story_cards: bool
    card_count: int
    has_guidelines: bool
    has_avatar: bool = False
    branches: list[str] = []


class ExportRequest(BaseModel):
    include_optional: bool = True
    branches: list[str] | None = None  # None = all branches


class ExportStats(BaseModel):
    exchange_count: int = 0
    session_count: int = 0
    card_count: int = 0
    branch_count: int = 0
    bookmark_count: int = 0
    annotation_count: int = 0
    variant_count: int = 0


class ImportStats(BaseModel):
    sessions_imported: int = 0
    exchanges_imported: int = 0
    branches_imported: int = 0
    cards_written: int = 0
    trust_modifications_imported: int = 0
    bookmarks_imported: int = 0
    annotations_imported: int = 0
    variants_imported: int = 0
    warnings: list[str] = []


class ImportResponse(BaseModel):
    status: str
    rp_folder: str
    stats: ImportStats


class GuidelinesResponse(BaseModel):
    pov_mode: PovMode | None = None
    dual_characters: list[str] = []
    narrative_voice: NarrativeVoice | None = None
    tense: Tense | None = None
    tone: list[str] | str | None = None
    scene_pacing: ScenePacing | None = None
    integrate_user_narrative: bool | None = None
    pov_character: str | None = None
    preserve_user_details: bool | None = None
    sensitive_themes: list[str] = []
    hard_limits: str | list[str] | None = None
    response_length: ResponseLength | None = None
    include_writing_principles: bool = True
    include_npc_framework: bool = True
    include_output_format: bool = True
    # Optional per-RP override of dynamic-context injection depths (Phase 4).
    # Merged over the global PromptConfig.injection.depths defaults at prompt
    # assembly time. None = no override (use global defaults).
    injection_depths: dict[str, int] | None = None
    # Optional per-RP ordering of depth-0 prompt sections (Phase 5a). A list of
    # section names; named sections are emitted in this order and any depth-0
    # section omitted from a present list is dropped. None = default order.
    # Sections pulled to depth > 0 (injection) are unaffected.
    prompt_order: list[str] | None = None
    # Optional per-RP lorebook active-set (Phase 5b). A list of lorebook file
    # stems in the RP's ``Lorebooks/`` folder to activate. None/absent = ALL
    # per-RP lorebook files are active (file-drop-and-go). Global-library
    # lorebooks are always available regardless of this list.
    lorebooks: list[str] | None = None
    avatar: str | None = None
    body: str | None = None
