export interface StoryCardSummary {
  id: string;
  name: string;
  card_type: string;
  importance: string | null;
  file_path: string;
  summary: string | null;
  aliases: string[];
  tags: string[];
  connection_count: number;
}

export interface ChunkRow {
  id: number;
  file_path: string | null;
  rp_folder: string | null;
  card_type: string | null;
  chunk_index: number;
  total_chunks: number;
  content: string;
  has_embedding: boolean;
  created_at: string | null;
}

export interface VectorStats {
  total_chunks: number;
  chunks_with_embeddings: number;
  chunks_without_embeddings: number;
  total_files: number;
  avg_chunk_size: number;
  cards_without_vectors: string[];
}

export interface DebugSearchResult {
  id: number;
  file_path: string | null;
  card_type: string | null;
  chunk_index: number;
  content: string;
  vector_score: number | null;
  bm25_score: number | null;
  fused_score: number;
  found_by: 'vector' | 'bm25' | 'both';
}
export interface EntityConnection {
  to_entity: string;
  connection_type: string;
  field: string | null;
  role: string | null;
}
export interface StoryCardDetail {
  name: string;
  card_type: string;
  file_path: string;
  importance: string | null;
  frontmatter: Record<string, unknown>;
  content: string;
  body: string;
  connections: EntityConnection[];
}
export interface SuggestCardResponse {
  entity_name: string;
  card_type: string;
  markdown: string;
  model_used: string;
}

export interface StoryCardCreate {
  name: string;
  frontmatter?: Record<string, unknown>;
  content?: string;
}
export interface StoryCardUpdate {
  frontmatter?: Record<string, unknown>;
  content?: string;
}
export interface CardListResponse {
  cards: StoryCardSummary[];
  total: number;
}
export interface ReindexResponse {
  entities: number;
  connections: number;
  aliases: number;
  keywords: number;
  duration_ms: number;
}
