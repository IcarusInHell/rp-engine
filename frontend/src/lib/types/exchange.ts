export interface ExchangeSave {
  user_message: string;
  assistant_response: string;
  exchange_number?: number;
  idempotency_key?: string;
  parent_exchange_number?: number;
  session_id?: string;
  in_story_timestamp?: string;
  location?: string;
  metadata?: Record<string, unknown>;
}
export interface ExchangeResponse {
  id: number;
  exchange_number: number;
  session_id: string;
  created_at: string;
  analysis_status: string;
  rewound_count: number | null;
  idempotent_hit: boolean | null;
}
export interface ExchangeDetail {
  id: number;
  exchange_number: number;
  session_id: string;
  branch: string | null;
  user_message: string;
  assistant_response: string;
  in_story_timestamp: string | null;
  location: string | null;
  npcs_involved: string[] | null;
  message_mode: 'rp' | 'ooc' | 'direction';
  analysis_status: string;
  created_at: string;
  metadata: Record<string, unknown> | null;
  has_variants: boolean;
  variant_count: number;
  continue_count: number;
  is_bookmarked: boolean;
  bookmark_name: string | null;
  has_annotations: boolean;
  annotation_count: number;
}
export interface ExchangeListResponse {
  exchanges: ExchangeDetail[];
  total_count: number;
}

// --- Search ---

export interface ExchangeSearchHit {
  exchange_number: number;
  exchange_id: number;
  user_message_snippet: string;
  assistant_response_snippet: string;
  relevance_score: number;
  timestamp: string;
  session_id: string | null;
  npcs_mentioned: string[] | null;
  is_bookmarked: boolean;
  bookmark_name: string | null;
  annotation_count: number;
}

export interface ExchangeSearchResponse {
  query: string;
  mode: string;
  total_results: number;
  results: ExchangeSearchHit[];
}

// --- Bookmarks ---

export interface BookmarkCreate {
  name?: string;
  note?: string;
  color?: string;
}

export interface BookmarkUpdate {
  name?: string;
  note?: string;
  color?: string;
}

export interface BookmarkResponse {
  id: number;
  exchange_number: number;
  exchange_id: number;
  name: string;
  note: string | null;
  color: string;
  created_at: string;
}

export interface BookmarkListResponse {
  bookmarks: BookmarkResponse[];
  total_count: number;
}

// --- Edit ---

export interface ExchangeUpdate {
  user_message?: string;
  assistant_response?: string;
  re_embed?: boolean;
  re_analyze?: boolean;
}

// --- Annotations ---

export type AnnotationType = 'note' | 'correction' | 'todo' | 'gm_note';

export interface AnnotationCreate {
  content: string;
  annotation_type?: AnnotationType;
  include_in_context?: boolean;
}

export interface AnnotationUpdate {
  content?: string;
  annotation_type?: AnnotationType;
  include_in_context?: boolean;
}

export interface AnnotationResponse {
  id: number;
  exchange_number: number;
  exchange_id: number;
  content: string;
  annotation_type: AnnotationType;
  include_in_context: boolean;
  resolved: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface AnnotationListResponse {
  annotations: AnnotationResponse[];
  total_count: number;
}
