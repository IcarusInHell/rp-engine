import { apiFetch, buildRPRequest, extractErrorDetail } from './client';
import type {
	ChatResponse,
	ChatStreamEvent,
	ContinueResponse,
	PromptPreview,
	RegenerateResponse,
	SceneOverride,
	SwipeResponse,
	VariantsResponse,
} from '$lib/types';

export type { ChatMessage, ChatResponse, ChatStreamEvent } from '$lib/types';

async function* _streamSSE(
	url: string,
	body: Record<string, unknown>,
): AsyncGenerator<ChatStreamEvent> {
	const res = await fetch(url, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	});

	if (!res.ok) {
		yield { type: 'error', content: await extractErrorDetail(res) };
		return;
	}

	const reader = res.body?.getReader();
	if (!reader) {
		yield { type: 'error', content: 'No response stream' };
		return;
	}

	const decoder = new TextDecoder();
	let buffer = '';

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;

		buffer += decoder.decode(value, { stream: true });
		const lines = buffer.split('\n');
		buffer = lines.pop() ?? '';

		for (const line of lines) {
			const trimmed = line.trim();
			if (!trimmed || !trimmed.startsWith('data: ')) continue;
			const data = trimmed.slice(6);
			if (data === '[DONE]') return;
			try {
				yield JSON.parse(data) as ChatStreamEvent;
			} catch {
				// Skip malformed SSE lines
			}
		}
	}
}

export type MessageMode = 'rp' | 'ooc' | 'direction';

export interface ChatOptions {
	message_mode?: MessageMode;
	ooc?: boolean; // deprecated — use message_mode="ooc"
	attach_card_ids?: string[];
	scene_override?: SceneOverride;
}

export async function* streamChat(message: string, opts?: ChatOptions): AsyncGenerator<ChatStreamEvent> {
	const { url, body } = buildRPRequest('/api/chat', {
		user_message: message,
		stream: true,
		...opts,
	});
	yield* _streamSSE(url, body!);
}

export async function* streamRegenerate(
	exchangeNumber?: number,
	opts?: { temperature?: number; model?: string },
): AsyncGenerator<ChatStreamEvent> {
	const { url, body } = buildRPRequest('/api/chat/regenerate', {
		exchange_number: exchangeNumber,
		stream: true,
		...opts,
	});
	yield* _streamSSE(url, body!);
}

export async function* streamContinue(
	exchangeNumber?: number,
	maxTokens?: number,
): AsyncGenerator<ChatStreamEvent> {
	const { url, body } = buildRPRequest('/api/chat/continue', {
		exchange_number: exchangeNumber,
		stream: true,
		max_tokens: maxTokens,
	});
	yield* _streamSSE(url, body!);
}

export async function regenerate(
	exchangeNumber?: number,
	opts?: { temperature?: number; model?: string },
): Promise<RegenerateResponse> {
	return apiFetch<RegenerateResponse>('/api/chat/regenerate', {
		method: 'POST',
		body: JSON.stringify({
			exchange_number: exchangeNumber,
			stream: false,
			...opts,
		}),
	});
}

export async function swipe(
	exchangeNumber: number,
	variantIndex: number,
): Promise<SwipeResponse> {
	return apiFetch<SwipeResponse>('/api/chat/swipe', {
		method: 'POST',
		body: JSON.stringify({
			exchange_number: exchangeNumber,
			variant_index: variantIndex,
		}),
	});
}

export async function continueGeneration(
	exchangeNumber?: number,
	maxTokens?: number,
): Promise<ContinueResponse> {
	return apiFetch<ContinueResponse>('/api/chat/continue', {
		method: 'POST',
		body: JSON.stringify({
			exchange_number: exchangeNumber,
			stream: false,
			max_tokens: maxTokens,
		}),
	});
}

export async function listVariants(exchangeNumber: number): Promise<VariantsResponse> {
	return apiFetch<VariantsResponse>(`/api/chat/variants/${exchangeNumber}`);
}

export async function previewPrompt(rpFolder: string): Promise<PromptPreview> {
	return apiFetch<PromptPreview>('/api/context/guidelines/system-prompt', {
		params: { rp_folder: rpFolder },
		skipRPContext: true,
	});
}

// ── Agent SDK Chat ──────────────────────────────────────────────

export interface AgentStatus {
	available: boolean;
	reason?: string;
}

export async function getAgentStatus(): Promise<AgentStatus> {
	return apiFetch<AgentStatus>('/api/chat/agent/status', { skipRPContext: true });
}

export async function* streamAgentChat(
	message: string,
	agentSessionId?: string,
): AsyncGenerator<ChatStreamEvent> {
	const body: Record<string, unknown> = { user_message: message };
	if (agentSessionId) body.session_id = agentSessionId;
	yield* _streamSSE('/api/chat/agent', body);
}
