// Phase 6: prompt section-ordering (Phase 5a /api/prompt/order endpoints).

export interface PromptOrderResponse {
	/** Effective order — the custom order if set, else the default. */
	order: string[];
	/** True when the RP has a custom prompt_order in its guidelines frontmatter. */
	is_custom: boolean;
	/** The default depth-0 section order. */
	default: string[];
	/** All section names known to the assembler (for validation / the picker). */
	known_sections: string[];
}
