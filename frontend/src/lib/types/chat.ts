export type MessageMode = 'rp' | 'ooc' | 'direction';

export interface ChatMessage {
	role: 'user' | 'assistant';
	content: string;
	timestamp: string;
	exchange_id?: number;
	exchange_number?: number;
	branch?: string | null;
	message_mode?: MessageMode;
	has_variants?: boolean;
	variant_count?: number;
	continue_count?: number;
	is_bookmarked?: boolean;
	bookmark_name?: string | null;
	has_annotations?: boolean;
	annotation_count?: number;
}

export interface ChatResponse {
	response: string;
	exchange_id: number;
	exchange_number: number;
	session_id: string;
	context_summary?: Record<string, unknown> | null;
}

export interface ChatStreamEvent {
	type: 'token' | 'done' | 'error';
	content?: string;
	exchange_id?: number;
	exchange_number?: number;
	variant_id?: number;
	variant_index?: number;
	total_variants?: number;
	continue_count?: number;
}

// --- Regenerate / Swipe ---

export interface RegenerateRequest {
	exchange_number?: number;
	temperature?: number;
	model?: string;
	stream?: boolean;
}

export interface RegenerateResponse {
	response: string;
	exchange_id: number;
	exchange_number: number;
	session_id: string;
	variant_id: number;
	variant_index: number;
	total_variants: number;
}

export interface SwipeRequest {
	exchange_number: number;
	variant_index: number;
}

export interface SwipeResponse {
	exchange_number: number;
	active_variant: number;
	total_variants: number;
	response: string;
}

export interface VariantInfo {
	id: number;
	variant_index: number;
	is_active: boolean;
	model_used: string | null;
	temperature: number | null;
	source: string;
	continue_count: number;
	created_at: string;
}

export interface VariantsResponse {
	exchange_number: number;
	exchange_id: number;
	variants: VariantInfo[];
	total: number;
}

// --- Continue ---

export interface ContinueRequest {
	exchange_number?: number;
	max_tokens?: number;
	stream?: boolean;
}

export interface ContinueResponse {
	continuation: string;
	full_response: string;
	exchange_id: number;
	exchange_number: number;
	session_id: string;
	continue_count: number;
}

// --- Advanced Chat Options ---

export interface SceneOverride {
	location?: string;
	mood?: string;
}
