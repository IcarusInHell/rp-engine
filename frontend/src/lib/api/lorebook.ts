import { apiFetch } from './client';
import type { LorebookListResponse, LorebookReindexResponse } from '$lib/types';

export async function getLorebooks(rp_folder: string): Promise<LorebookListResponse> {
	return apiFetch<LorebookListResponse>('/api/lorebook', {
		params: { rp_folder },
		skipRPContext: true,
	});
}

/**
 * Set the per-RP active-set (file stems). An empty list means *no* per-RP files
 * are active (distinct from the absent state, which activates all per-RP files).
 */
export async function setActiveLorebooks(
	rp_folder: string,
	lorebooks: string[],
): Promise<LorebookListResponse> {
	return apiFetch<LorebookListResponse>('/api/lorebook/active', {
		method: 'PUT',
		params: { rp_folder },
		body: JSON.stringify({ lorebooks }),
		skipRPContext: true,
	});
}

export async function reindexLorebooks(rp_folder: string): Promise<LorebookReindexResponse> {
	return apiFetch<LorebookReindexResponse>('/api/lorebook/reindex', {
		method: 'POST',
		params: { rp_folder },
		skipRPContext: true,
	});
}
