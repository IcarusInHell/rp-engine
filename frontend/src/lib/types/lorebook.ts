// Phase 6: lorebook active-set management (Phase 5b /api/lorebook endpoints).
// File-drop model — these manage which lorebook *files* are active per RP and
// trigger re-indexing. They do NOT author entries (that is a deferred future UI).

export interface LorebookFile {
	stem: string;
	scope: 'rp' | 'global';
	path: string;
	active: boolean;
}

export interface LorebookListResponse {
	enabled: boolean;
	/** Explicit per-RP active-set (file stems), or null = ALL per-RP files active. */
	active_set: string[] | null;
	files: LorebookFile[];
}

export interface LorebookReindexResponse {
	reindexed: boolean;
	rp_entries: number;
	global_entries: number;
}
