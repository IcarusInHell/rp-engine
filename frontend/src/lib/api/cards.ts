import { apiFetch } from './client';
import type { CardListResponse, StoryCardDetail, StoryCardCreate, StoryCardUpdate, ReindexResponse, SuggestCardResponse, AuditResponse, GraphData } from '$lib/types';

export async function listCards(params?: { card_type?: string; importance?: string }): Promise<CardListResponse> {
	return apiFetch<CardListResponse>('/api/cards', { params });
}

export async function getCard(type: string, name: string): Promise<StoryCardDetail> {
	return apiFetch<StoryCardDetail>(`/api/cards/${encodeURIComponent(type)}/${encodeURIComponent(name)}`);
}

export async function createCard(type: string, body: StoryCardCreate): Promise<StoryCardDetail> {
	return apiFetch<StoryCardDetail>(`/api/cards/${encodeURIComponent(type)}`, {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export async function updateCard(type: string, name: string, body: StoryCardUpdate): Promise<StoryCardDetail> {
	return apiFetch<StoryCardDetail>(`/api/cards/${encodeURIComponent(type)}/${encodeURIComponent(name)}`, {
		method: 'PUT',
		body: JSON.stringify(body),
	});
}

export async function deleteCard(type: string, name: string): Promise<{ name: string; card_type: string; file_deleted: boolean }> {
	return apiFetch(`/api/cards/${encodeURIComponent(type)}/${encodeURIComponent(name)}`, {
		method: 'DELETE',
	});
}

export async function reindex(): Promise<ReindexResponse> {
	return apiFetch<ReindexResponse>('/api/cards/reindex', { method: 'POST' });
}

export async function suggestCard(
	entity_name: string,
	card_type: string,
	additional_context?: string,
	rp_folder?: string,
	related_entities?: string[]
): Promise<SuggestCardResponse> {
	return apiFetch<SuggestCardResponse>('/api/cards/suggest', {
		method: 'POST',
		body: JSON.stringify({ entity_name, card_type, additional_context, rp_folder, related_entities }),
	});
}

export async function auditCards(mode?: string): Promise<AuditResponse> {
	return apiFetch<AuditResponse>('/api/cards/audit', {
		method: 'POST',
		body: JSON.stringify({ mode: mode ?? 'quick' }),
	});
}

export async function getConnections(): Promise<GraphData> {
	return apiFetch<GraphData>('/api/cards/connections');
}

export async function validateCard(card_type: string, frontmatter: Record<string, unknown>): Promise<unknown> {
	return apiFetch('/api/cards/validate', {
		method: 'POST',
		body: JSON.stringify({ card_type, frontmatter }),
	});
}

export async function getSchema(card_type: string): Promise<unknown> {
	return apiFetch(`/api/cards/schema/${encodeURIComponent(card_type)}`);
}

export async function generateCardName(
	cardType: string,
	hints?: string,
	count?: number,
): Promise<{ suggestions: string[]; card_type: string }> {
	return apiFetch('/api/cards/generate-name', {
		method: 'POST',
		body: JSON.stringify({ card_type: cardType, hints: hints ?? '', count: count ?? 5 }),
	});
}
