export interface SessionCreate {
  rp_folder: string;
  branch?: string;
}
export interface SessionResponse {
  id: string;
  rp_folder: string;
  branch: string;
  started_at: string;
  ended_at: string | null;
  metadata: Record<string, unknown> | null;
  // Phase 5a/6: session-persistent narrator's note (GM steering).
  narrator_note?: string | null;
  narrator_note_depth?: number;
}
export interface TrustChange {
  npc: string;
  delta: number;
  reason: string;
}
export interface NewEntity {
  name: string;
  type: string;
  first_mention_exchange: number | null;
}
export interface SceneProgression {
  first_timestamp: string | null;
  last_timestamp: string | null;
  locations_visited: string[];
}
export interface PlotThreadStatus {
  thread_id: string;
  name: string;
  start_counter: number;
  end_counter: number;
}
export interface SessionEndSummary {
  significant_events: string[];
  trust_changes: TrustChange[];
  new_entities: NewEntity[];
  scene_progression: SceneProgression | null;
  plot_thread_status: PlotThreadStatus[];
}
export interface SessionEndResponse {
  session: SessionResponse;
  summary: SessionEndSummary;
}

export interface SessionTimelineEntry {
  type: string;
  exchange_number: number | null;
  timestamp: string | null;
  title: string;
  detail: Record<string, unknown>;
  characters: string[];
}

export interface SessionTimelineResponse {
  session_id: string;
  branch: string;
  exchange_range: [number, number];
  entries: SessionTimelineEntry[];
  entry_counts: Record<string, number>;
}
