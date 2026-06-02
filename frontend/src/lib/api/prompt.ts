import { apiFetch } from './client';
import type { PromptOrderResponse } from '$lib/types';

export async function getPromptOrder(rp_folder: string): Promise<PromptOrderResponse> {
	return apiFetch<PromptOrderResponse>('/api/prompt/order', {
		params: { rp_folder },
		skipRPContext: true,
	});
}

export async function updatePromptOrder(
	rp_folder: string,
	order: string[],
): Promise<PromptOrderResponse> {
	return apiFetch<PromptOrderResponse>('/api/prompt/order', {
		method: 'PUT',
		params: { rp_folder },
		body: JSON.stringify({ order }),
		skipRPContext: true,
	});
}

export async function resetPromptOrder(rp_folder: string): Promise<PromptOrderResponse> {
	return apiFetch<PromptOrderResponse>('/api/prompt/order/reset', {
		method: 'POST',
		params: { rp_folder },
		skipRPContext: true,
	});
}
