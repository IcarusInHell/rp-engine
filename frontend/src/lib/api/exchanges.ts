import { apiFetch } from './client';
import type {
	ExchangeSave,
	ExchangeResponse,
	ExchangeDetail,
	ExchangeListResponse,
	ExchangeSearchResponse,
	ExchangeUpdate,
	BookmarkCreate,
	BookmarkUpdate,
	BookmarkResponse,
	BookmarkListResponse,
	AnnotationCreate,
	AnnotationUpdate,
	AnnotationResponse,
	AnnotationListResponse,
} from '$lib/types';

// --- Exchange CRUD ---

export async function listExchanges(params?: { session_id?: string; limit?: number; offset?: number }): Promise<ExchangeListResponse> {
	return apiFetch<ExchangeListResponse>('/api/exchanges', { params });
}

export async function saveExchange(body: ExchangeSave): Promise<ExchangeResponse> {
	return apiFetch<ExchangeResponse>('/api/exchanges', {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export async function editExchange(exchangeNumber: number, body: ExchangeUpdate): Promise<ExchangeDetail> {
	return apiFetch<ExchangeDetail>(`/api/exchanges/${exchangeNumber}`, {
		method: 'PUT',
		body: JSON.stringify(body),
	});
}

// --- Search ---

export async function searchExchanges(params: {
	q: string;
	mode?: 'semantic' | 'keyword' | 'hybrid';
	limit?: number;
	min_score?: number;
}): Promise<ExchangeSearchResponse> {
	return apiFetch<ExchangeSearchResponse>('/api/exchanges/search', { params });
}

// --- Bookmarks ---

export async function createBookmark(exchangeNumber: number, body: BookmarkCreate = {}): Promise<BookmarkResponse> {
	return apiFetch<BookmarkResponse>(`/api/exchanges/${exchangeNumber}/bookmark`, {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export async function getBookmark(exchangeNumber: number): Promise<BookmarkResponse> {
	return apiFetch<BookmarkResponse>(`/api/exchanges/${exchangeNumber}/bookmark`);
}

export async function updateBookmark(exchangeNumber: number, body: BookmarkUpdate): Promise<BookmarkResponse> {
	return apiFetch<BookmarkResponse>(`/api/exchanges/${exchangeNumber}/bookmark`, {
		method: 'PUT',
		body: JSON.stringify(body),
	});
}

export async function deleteBookmark(exchangeNumber: number): Promise<void> {
	await apiFetch(`/api/exchanges/${exchangeNumber}/bookmark`, { method: 'DELETE' });
}

export async function listBookmarks(params?: {
	color?: string;
	sort?: string;
	limit?: number;
	offset?: number;
}): Promise<BookmarkListResponse> {
	return apiFetch<BookmarkListResponse>('/api/bookmarks', { params });
}

// --- Annotations ---

export async function createAnnotation(exchangeNumber: number, body: AnnotationCreate): Promise<AnnotationResponse> {
	return apiFetch<AnnotationResponse>(`/api/exchanges/${exchangeNumber}/annotations`, {
		method: 'POST',
		body: JSON.stringify(body),
	});
}

export async function listExchangeAnnotations(exchangeNumber: number): Promise<AnnotationListResponse> {
	return apiFetch<AnnotationListResponse>(`/api/exchanges/${exchangeNumber}/annotations`);
}

export async function updateAnnotation(annotationId: number, body: AnnotationUpdate): Promise<AnnotationResponse> {
	return apiFetch<AnnotationResponse>(`/api/annotations/${annotationId}`, {
		method: 'PUT',
		body: JSON.stringify(body),
	});
}

export async function deleteAnnotation(annotationId: number): Promise<void> {
	await apiFetch(`/api/annotations/${annotationId}`, { method: 'DELETE' });
}

export async function toggleAnnotationResolved(annotationId: number): Promise<AnnotationResponse> {
	return apiFetch<AnnotationResponse>(`/api/annotations/${annotationId}/resolve`, {
		method: 'PATCH',
	});
}

export async function listAllAnnotations(params?: {
	annotation_type?: string;
	resolved?: boolean;
	limit?: number;
	offset?: number;
}): Promise<AnnotationListResponse> {
	return apiFetch<AnnotationListResponse>('/api/annotations', { params });
}
