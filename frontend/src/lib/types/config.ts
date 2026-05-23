export interface AppConfig {
	server: { host: string; port: number; cors_origins: string[] };
	paths: { vault_root: string; db_path: string };
	llm: {
		provider: string;
		api_key: string;
		models: {
			npc_reactions: string;
			response_analysis: string;
			card_generation: string;
			embeddings: string;
		};
		fallback_model: string;
		mode: { chat: 'provider' | 'sdk' };
		temperatures?: {
			chat: number;
			chat_regenerate_bump: number;
			npc_reactions: number;
			summary: number;
		};
	};
	chat: { exchange_window: number; model: string | null; temperature: number; max_tokens: number; max_variants: number };
	context: { max_documents: number; max_graph_hops: number; stale_threshold_turns: number };
	search: {
		vector_weight: number;
		bm25_weight: number;
		similarity_threshold: number;
		chunk_size: number;
		chunk_overlap: number;
	};
	trust: {
		increase_value: number;
		decrease_value: number;
		session_max_gain: number;
		session_max_loss: number;
		min_score: number;
		max_score: number;
	};
	diagnostics: DiagnosticConfig;
	rp: { default_pov_character: string };
}

export interface DiagnosticConfig {
	enabled: boolean;
	level: string;
	max_file_size_mb: number;
	max_files: number;
	auto_purge_days: number;
	auto_report: AutoReportConfig;
	reporter_key: string;
}

export interface AutoReportConfig {
	enabled: boolean;
	url: string;
	on_error: boolean;
	on_session_end: boolean;
}

export interface DiagnosticStatus {
	enabled: boolean;
	level: string;
	file_size_bytes: number;
	entry_count: number;
	archive_count: number;
	last_entry_ts: string | null;
	reporter_key: string;
	auto_report: AutoReportConfig;
}

export interface ConfigUpdate {
	server?: Partial<AppConfig['server']>;
	paths?: Partial<AppConfig['paths']>;
	llm?: Partial<AppConfig['llm']>;
	chat?: Partial<AppConfig['chat']>;
	context?: Partial<AppConfig['context']>;
	search?: Partial<AppConfig['search']>;
	trust?: Partial<AppConfig['trust']>;
	diagnostics?: Partial<AppConfig['diagnostics']>;
	rp?: Partial<AppConfig['rp']>;
	openrouter_api_key?: string;
}

export interface ActiveRPStatus {
	active_rp: boolean;
	session_count: number;
}

export interface ProviderTestResult {
	provider: string;
	status: 'ok' | 'error';
	latency_ms: number | null;
	error: string | null;
}
