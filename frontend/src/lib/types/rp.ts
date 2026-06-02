export interface RPCreate {
  rp_name: string;
  pov_mode?: 'single' | 'dual';
  dual_characters?: string[];
  tone?: string;
  scene_pacing?: 'slow' | 'moderate' | 'fast';
}
export interface RPResponse {
  rp_folder: string;
  created_files: string[];
}
export interface RPInfo {
  rp_folder: string;
  has_story_cards: boolean;
  card_count: number;
  has_guidelines: boolean;
  has_avatar: boolean;
  branches: string[];
}
export interface GuidelinesResponse {
  pov_mode: 'single' | 'dual' | null;
  dual_characters: string[];
  narrative_voice: 'first' | 'third' | null;
  tense: 'present' | 'past' | null;
  tone: string[] | string | null;
  scene_pacing: 'slow' | 'moderate' | 'fast' | null;
  integrate_user_narrative: boolean | null;
  pov_character: string | null;
  preserve_user_details: boolean | null;
  sensitive_themes: string[];
  hard_limits: string | string[] | null;
  response_length: 'short' | 'medium' | 'long' | null;
  include_writing_principles?: boolean;
  include_npc_framework?: boolean;
  include_output_format?: boolean;
  // Phase 6: per-RP prompt-assembly overrides (Story_Guidelines.md frontmatter).
  injection_depths?: Record<string, number> | null;
  prompt_order?: string[] | null;
  lorebooks?: string[] | null;
  avatar?: string | null;
  body?: string | null;
}

export interface ChunkingConfig {
  strategy: string;
  chunk_size: number;
  chunk_overlap: number;
}

export interface RechunkResponse {
  status: string;
  total_exchanges: number;
  embedded: number;
  skipped: number;
  failed: number;
}

export interface PromptPreview {
  system_prompt: string;
  sections: string[];
  // Phase 6: effective per-section injection depths + whether injection is on.
  // When disabled (Phase 4 default), every section renders at depth 0; the map
  // is the configured placement that would apply if injection were enabled.
  injection?: { enabled: boolean; depths: Record<string, number> };
}

export interface ExportRequest {
  include_optional?: boolean;
  branches?: string[] | null;
}

export interface ExportStats {
  exchange_count: number;
  session_count: number;
  card_count: number;
  branch_count: number;
  bookmark_count: number;
  annotation_count: number;
  variant_count: number;
}

export interface ImportStats {
  sessions_imported: number;
  exchanges_imported: number;
  branches_imported: number;
  cards_written: number;
  trust_modifications_imported: number;
  bookmarks_imported: number;
  annotations_imported: number;
  variants_imported: number;
  warnings: string[];
}

export interface ImportResponse {
  status: string;
  rp_folder: string;
  stats: ImportStats;
}
