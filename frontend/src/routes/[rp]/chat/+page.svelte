<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { activeRP, activeBranch } from '$lib/stores/rp';
	import { addToast } from '$lib/stores/ui';
	import { getFullState } from '$lib/api/state';
	import { streamChat, streamRegenerate, streamContinue, swipe, listVariants, streamAgentChat, getAgentStatus, type ChatMessage, type ChatOptions, type AgentStatus } from '$lib/api/chat';
	import { getConfig } from '$lib/api/config';
	import { getActiveSession, startSession, endSession } from '$lib/api/sessions';
	import { listExchanges, searchExchanges, editExchange, createBookmark, deleteBookmark, listExchangeAnnotations, createAnnotation, deleteAnnotation, toggleAnnotationResolved } from '$lib/api/exchanges';
	import { listCards } from '$lib/api/cards';
	import { createBranch } from '$lib/api/branches';
	import type { StateSnapshot, SessionResponse, ExchangeDetail, ChatStreamEvent, AnnotationResponse, CardListResponse, SceneOverride } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import InfoRow from '$lib/components/ui/InfoRow.svelte';
	import { formatTime } from '$lib/utils/format';

	// ── Chat state ────────────────────────────────────────────
	let messages: ChatMessage[] = $state([]);
	let userInput = $state('');
	let sending = $state(false);
	let streamingContent = $state('');
	let chatLog: HTMLDivElement = $state() as HTMLDivElement;

	// ── Session state ─────────────────────────────────────────
	let session: SessionResponse | null = $state(null);
	let sessionLoading = $state(true);
	let sessionStarting = $state(false);
	let sessionEnding = $state(false);

	// ── Exchange pagination ───────────────────────────────────
	let totalExchanges = $state(0);
	let loadingMore = $state(false);
	let allLoaded = $state(false);
	const PAGE_SIZE = 30;
	const SEARCH_DEBOUNCE_MS = 300;
	const HIGHLIGHT_DURATION_MS = 2000;

	// ── Stats panel ───────────────────────────────────────────
	let showStats = $state(true);
	let stateSnapshot: StateSnapshot | null = $state(null);
	let loadingState = $state(false);

	// ── Error state ───────────────────────────────────────────
	let chatError: string | null = $state(null);

	// ── Regenerate / Continue state ───────────────────────────
	let regenerating = $state(false);
	let continuing = $state(false);
	let regenerateStreamContent = $state('');
	let continueStreamContent = $state('');
	let activeStreamExchange: number | null = $state(null);

	// ── Search state ──────────────────────────────────────────
	let showSearch = $state(false);
	let searchQuery = $state('');
	let searchMode: 'semantic' | 'keyword' | 'hybrid' = $state('semantic');
	let searchResults: import('$lib/types').ExchangeSearchHit[] = $state([]);
	let searching = $state(false);
	let searchDebounce: ReturnType<typeof setTimeout>;

	// ── Annotation state ──────────────────────────────────────
	let expandedAnnotations: Set<number> = $state(new Set());
	let annotationCache: Map<number, AnnotationResponse[]> = $state(new Map());
	let annotationInput = $state('');
	let annotationType: import('$lib/types').AnnotationType = $state('note');
	let creatingAnnotation = $state(false);

	// ── Edit state ───────────────────────────────────────────
	let editingExchange: { number: number; role: 'user' | 'assistant' } | null = $state(null);
	let editContent = $state('');
	let editSaving = $state(false);

	// ── Branch-from-exchange state ───────────────────────────
	let branchingExchange: number | null = $state(null);
	let branchName = $state('');
	let branchDescription = $state('');
	let branchCreating = $state(false);

	// ── Agent SDK state ──────────────────────────────────────
	let chatMode = $state<'provider' | 'sdk'>('provider');
	let useAgentSDK = $derived(chatMode === 'sdk');
	let agentAvailable: AgentStatus | null = $state(null);
	let agentSessionId: string | null = $state(null);

	// ── Advanced options state ────────────────────────────────
	let showAdvanced = $state(false);
	type MessageMode = 'rp' | 'ooc' | 'direction';
	let messageMode = $state<MessageMode>('rp');
	let hasInlineDirection = $derived(/\(\(.*?\)\)|\/\/\s/s.test(userInput));
	let sceneLocation = $state('');
	let sceneMood = $state('');
	let attachedCardIds: string[] = $state([]);
	let availableCards: { card_id: string; name: string; card_type: string }[] = $state([]);
	let showCardPicker = $state(false);

	function exchangesToMessages(exchanges: ExchangeDetail[]): ChatMessage[] {
		// Deduplicate: if current branch has an exchange with the same number as
		// an inherited parent exchange, keep only the current branch's version.
		const branch = $activeBranch;
		const currentBranchNumbers = new Set(
			exchanges.filter((ex) => ex.branch === branch).map((ex) => ex.exchange_number),
		);
		const deduped = exchanges.filter(
			(ex) => ex.branch === branch || !currentBranchNumbers.has(ex.exchange_number),
		);
		return deduped.flatMap((ex) => [
			{ role: 'user' as const, content: ex.user_message, timestamp: ex.created_at, exchange_id: ex.id, exchange_number: ex.exchange_number, branch: ex.branch, message_mode: ex.message_mode ?? 'rp' },
			{ role: 'assistant' as const, content: ex.assistant_response, timestamp: ex.created_at, exchange_id: ex.id, exchange_number: ex.exchange_number, branch: ex.branch, message_mode: ex.message_mode ?? 'rp', has_variants: ex.has_variants, variant_count: ex.variant_count, continue_count: ex.continue_count, is_bookmarked: ex.is_bookmarked, bookmark_name: ex.bookmark_name, has_annotations: ex.has_annotations, annotation_count: ex.annotation_count },
		]);
	}

	onMount(async () => {
		await checkSession();
		// Load chat mode from config
		getConfig()
			.then((cfg) => {
				chatMode = cfg.llm.mode?.chat ?? 'provider';
			})
			.catch((err) => console.warn('[Config] failed to load chat mode:', err));
		// Check Agent SDK availability in background
		getAgentStatus()
			.then((s) => {
				console.log('[Agent SDK] status:', s);
				agentAvailable = s;
			})
			.catch((err) => console.warn('[Agent SDK] status check failed:', err));
	});

	// Reload exchanges when branch changes
	let prevBranch = $state($activeBranch);
	$effect(() => {
		const branch = $activeBranch;
		if (branch !== prevBranch) {
			prevBranch = branch;
			messages = [];
			allLoaded = false;
			totalExchanges = 0;
			checkSession();
		}
	});

	async function checkSession() {
		sessionLoading = true;
		try {
			session = await getActiveSession();
			if (session) {
				await loadExchanges();
				loadState();
			}
		} catch (e: any) {
			addToast(e.message ?? 'Failed to check session', 'error');
		} finally {
			sessionLoading = false;
		}
	}

	async function handleStartSession() {
		const rp = $activeRP;
		if (!rp) {
			addToast('No RP selected', 'error');
			return;
		}
		sessionStarting = true;
		try {
			session = await startSession({ rp_folder: rp.rp_folder, branch: $activeBranch });
			messages = [];
			allLoaded = true;
			totalExchanges = 0;
			loadState();
		} catch (e: any) {
			addToast(e.message ?? 'Failed to start session', 'error');
		} finally {
			sessionStarting = false;
		}
	}

	async function handleEndSession() {
		if (!session) return;
		sessionEnding = true;
		try {
			await endSession(session.id);
			session = null;
			addToast('Session ended', 'info');
		} catch (e: any) {
			addToast(e.message ?? 'Failed to end session', 'error');
		} finally {
			sessionEnding = false;
		}
	}

	async function loadExchanges() {
		try {
			const res = await listExchanges({ limit: PAGE_SIZE, offset: 0 });
			totalExchanges = res.total_count;
			// Reverse: API returns DESC (newest first), we want chronological (oldest first)
			messages = exchangesToMessages(res.exchanges.reverse());
			allLoaded = res.exchanges.length >= totalExchanges;
			await tick();
			scrollToBottom();
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load exchanges', 'error');
		}
	}

	async function loadMoreExchanges() {
		if (loadingMore || allLoaded) return;
		loadingMore = true;
		const prevScrollHeight = chatLog?.scrollHeight ?? 0;
		try {
			const currentCount = Math.floor(messages.length / 2);
			const res = await listExchanges({ limit: PAGE_SIZE, offset: currentCount });
			if (res.exchanges.length === 0) {
				allLoaded = true;
				return;
			}
			// Reverse for chronological order, then prepend (older messages go to top)
			messages = [...exchangesToMessages(res.exchanges.reverse()), ...messages];
			allLoaded = Math.floor(messages.length / 2) >= totalExchanges;
			await tick();
			if (chatLog) {
				chatLog.scrollTop = chatLog.scrollHeight - prevScrollHeight;
			}
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load more exchanges', 'error');
		} finally {
			loadingMore = false;
		}
	}

	function handleScroll() {
		if (chatLog && chatLog.scrollTop < 100 && !allLoaded && !loadingMore && messages.length > 0) {
			loadMoreExchanges();
		}
	}

	function autoGrowTextarea(e: Event) {
		const el = e.target as HTMLTextAreaElement;
		el.style.height = 'auto';
		el.style.height = el.scrollHeight + 'px';
	}

	async function loadState() {
		loadingState = true;
		try {
			stateSnapshot = await getFullState();
		} catch {
			// Stats panel is optional
		} finally {
			loadingState = false;
		}
	}

	async function handleSend() {
		const msg = userInput.trim();
		if (!msg || sending) return;

		if (!session) {
			addToast('No active session — start one first', 'error');
			return;
		}

		chatError = null;
		const mode = messageMode;
		const modeLabel = mode === 'ooc' ? '[OOC] ' : mode === 'direction' ? '[Direction] ' : '';
		messages = [...messages, { role: 'user', content: `${modeLabel}${msg}`, timestamp: new Date().toISOString(), message_mode: mode }];
		userInput = '';
		sending = true;
		streamingContent = '';

		await tick();
		scrollToBottom();

		// Build advanced options
		const opts: ChatOptions = {};
		if (mode !== 'rp') opts.message_mode = mode;
		if (attachedCardIds.length > 0) opts.attach_card_ids = [...attachedCardIds];
		if (sceneLocation || sceneMood) {
			opts.scene_override = {};
			if (sceneLocation) opts.scene_override.location = sceneLocation;
			if (sceneMood) opts.scene_override.mood = sceneMood;
		}

		try {
			let finalEvent: ChatStreamEvent = { type: 'done' };

			const stream = useAgentSDK
				? streamAgentChat(msg, agentSessionId ?? undefined)
				: streamChat(msg, opts);

			for await (const event of stream) {
				if (event.type === 'token' && event.content) {
					streamingContent += event.content;
					await tick();
					scrollToBottom();
				} else if (event.type === 'done') {
					finalEvent = event;
					// Capture agent session ID for resume
					if (useAgentSDK && (event as any).agent_session_id) {
						agentSessionId = (event as any).agent_session_id;
					}
				} else if (event.type === 'error') {
					chatError = event.content ?? 'Unknown streaming error';
					break;
				}
			}

			if (chatError) {
				messages = messages.slice(0, -1);
				userInput = msg;
			} else {
				messages = [...messages, {
					role: 'assistant',
					content: mode === 'ooc' ? `[OOC] ${streamingContent}` : streamingContent,
					timestamp: new Date().toISOString(),
					exchange_id: finalEvent.exchange_id,
					exchange_number: finalEvent.exchange_number,
					message_mode: mode,
				}];
				if (mode !== 'ooc') loadState();
			}
		} catch (e: any) {
			chatError = e.message ?? 'Chat failed';
			messages = messages.slice(0, -1);
			userInput = msg;
		} finally {
			sending = false;
			streamingContent = '';
			// Clear per-message options after send
			attachedCardIds = [];
			sceneLocation = '';
			sceneMood = '';
			await tick();
			scrollToBottom();
		}
	}

	// ── Regenerate ─────────────────────────────────────────────

	async function handleRegenerate(exchangeNumber: number) {
		if (regenerating || sending || continuing) return;
		regenerating = true;
		activeStreamExchange = exchangeNumber;
		regenerateStreamContent = '';

		try {
			let finalEvent: ChatStreamEvent = { type: 'done' };
			let receivedAny = false;

			for await (const event of streamRegenerate(exchangeNumber)) {
				receivedAny = true;
				if (event.type === 'token' && event.content) {
					regenerateStreamContent += event.content;
					await tick();
					scrollToBottom();
				} else if (event.type === 'done') {
					finalEvent = event;
				} else if (event.type === 'error') {
					addToast(event.content ?? 'Regeneration failed', 'error');
					break;
				}
			}

			if (!receivedAny) {
				addToast('Regeneration failed — no response from server', 'error');
			} else if (finalEvent.exchange_number) {
				messages = messages.map((m) => {
					if (m.role === 'assistant' && m.exchange_number === exchangeNumber) {
						return {
							...m,
							content: regenerateStreamContent,
							has_variants: true,
							variant_count: finalEvent.total_variants ?? (m.variant_count ?? 0) + 1,
						};
					}
					return m;
				});
				loadState();
			}
		} catch (e: any) {
			addToast(e.message ?? 'Regeneration failed', 'error');
		} finally {
			regenerating = false;
			activeStreamExchange = null;
			regenerateStreamContent = '';
		}
	}

	// ── Swipe ──────────────────────────────────────────────────

	async function handleSwipe(exchangeNumber: number, direction: 'prev' | 'next') {
		if (regenerating || sending || continuing) return;

		try {
			const variantsRes = await listVariants(exchangeNumber);
			if (variantsRes.total === 0) return;

			const activeIdx = variantsRes.variants.findIndex((v) => v.is_active);
			let targetIdx: number;

			if (direction === 'next') {
				targetIdx = activeIdx + 1 >= variantsRes.total ? 0 : activeIdx + 1;
			} else {
				targetIdx = activeIdx - 1 < 0 ? variantsRes.total - 1 : activeIdx - 1;
			}

			const res = await swipe(exchangeNumber, targetIdx);

			messages = messages.map((m) => {
				if (m.role === 'assistant' && m.exchange_number === exchangeNumber) {
					return { ...m, content: res.response, variant_count: res.total_variants };
				}
				return m;
			});
			loadState();
		} catch (e: any) {
			addToast(e.message ?? 'Swipe failed', 'error');
		}
	}

	// ── Continue ───────────────────────────────────────────────

	async function handleContinue(exchangeNumber: number) {
		if (continuing || sending || regenerating) return;
		continuing = true;
		activeStreamExchange = exchangeNumber;
		continueStreamContent = '';

		try {
			let receivedAny = false;
			for await (const event of streamContinue(exchangeNumber)) {
				receivedAny = true;
				if (event.type === 'token' && event.content) {
					continueStreamContent += event.content;
					await tick();
					scrollToBottom();
				} else if (event.type === 'done') {
					const existingMsg = messages.find(
						(m) => m.role === 'assistant' && m.exchange_number === exchangeNumber,
					);
					if (existingMsg) {
						messages = messages.map((m) => {
							if (m.role === 'assistant' && m.exchange_number === exchangeNumber) {
								return {
									...m,
									content: m.content + continueStreamContent,
									continue_count: event.continue_count ?? (m.continue_count ?? 0) + 1,
								};
							}
							return m;
						});
					}
					loadState();
				} else if (event.type === 'error') {
					addToast(event.content ?? 'Continue failed', 'error');
					break;
				}
			}
			if (!receivedAny) {
				addToast('Continue failed — no response from server', 'error');
			}
		} catch (e: any) {
			addToast(e.message ?? 'Continue failed', 'error');
		} finally {
			continuing = false;
			activeStreamExchange = null;
			continueStreamContent = '';
		}
	}

	// ── Edit ──────────────────────────────────────────────────

	function startEdit(exchangeNumber: number, role: 'user' | 'assistant') {
		// Prefer current branch's version over inherited parent messages
		let msg = messages.find(
			(m) => m.exchange_number === exchangeNumber && m.role === role && m.branch === $activeBranch,
		);
		if (!msg) {
			msg = messages.find(
				(m) => m.exchange_number === exchangeNumber && m.role === role,
			);
		}
		if (!msg) return;
		editingExchange = { number: exchangeNumber, role };
		editContent = msg.content;
	}

	function cancelEdit() {
		editingExchange = null;
		editContent = '';
	}

	async function saveEdit() {
		if (!editingExchange || editSaving) return;
		editSaving = true;
		try {
			const body = editingExchange.role === 'user'
				? { user_message: editContent }
				: { assistant_response: editContent };
			const updated = await editExchange(editingExchange.number, body);

			messages = messages.map((m) => {
				if (m.exchange_number === editingExchange!.number && m.role === editingExchange!.role && m.branch === $activeBranch) {
					return {
						...m,
						content: editingExchange!.role === 'user' ? updated.user_message : updated.assistant_response,
						has_variants: updated.has_variants,
						variant_count: updated.variant_count,
					};
				}
				return m;
			});
			addToast('Exchange updated', 'success');
		} catch (e: any) {
			addToast(e.message ?? 'Edit failed', 'error');
		} finally {
			editSaving = false;
			editingExchange = null;
			editContent = '';
		}
	}

	// ── Branch from exchange ──────────────────────────────────

	function startBranch(exchangeNumber: number) {
		branchingExchange = exchangeNumber;
		branchName = exchangeNumber === 0 ? 'branch-fresh' : `branch-ex-${exchangeNumber}`;
		branchDescription = '';
	}

	function cancelBranch() {
		branchingExchange = null;
		branchName = '';
		branchDescription = '';
	}

	async function confirmBranch() {
		if (branchingExchange === null || !branchName.trim() || branchCreating) return;
		const rp = $activeRP;
		if (!rp) return;
		branchCreating = true;
		try {
			await createBranch({
				name: branchName.trim(),
				rp_folder: rp.rp_folder,
				branch_point_exchange: branchingExchange,
			});
			$activeBranch = branchName.trim();
			addToast(`Branch "${branchName.trim()}" created`, 'success');
			branchingExchange = null;
			branchName = '';
			branchDescription = '';
		} catch (e: any) {
			addToast(e.message ?? 'Branch creation failed', 'error');
		} finally {
			branchCreating = false;
		}
	}

	// ── Search ─────────────────────────────────────────────────

	function handleSearchInput() {
		clearTimeout(searchDebounce);
		if (searchQuery.length < 2) {
			searchResults = [];
			return;
		}
		searchDebounce = setTimeout(() => doSearch(), SEARCH_DEBOUNCE_MS);
	}

	async function doSearch() {
		if (!searchQuery.trim()) return;
		searching = true;
		try {
			const res = await searchExchanges({ q: searchQuery, mode: searchMode, limit: 20 });
			searchResults = res.results;
		} catch (e: any) {
			addToast(e.message ?? 'Search failed', 'error');
		} finally {
			searching = false;
		}
	}

	function jumpToExchange(exchangeNumber: number) {
		showSearch = false;
		// Find the message in the chat log and scroll to it
		const idx = messages.findIndex((m) => m.exchange_number === exchangeNumber && m.role === 'assistant');
		if (idx >= 0) {
			const el = chatLog?.querySelectorAll('[data-exchange]')?.[idx];
			if (el) {
				(el as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'center' });
				(el as HTMLElement).classList.add('ring-2', 'ring-accent/50');
				setTimeout(() => (el as HTMLElement).classList.remove('ring-2', 'ring-accent/50'), HIGHLIGHT_DURATION_MS);
			}
		}
	}

	// ── Bookmarks ──────────────────────────────────────────────

	async function handleToggleBookmark(msg: ChatMessage) {
		if (!msg.exchange_number) return;
		try {
			if (msg.is_bookmarked) {
				await deleteBookmark(msg.exchange_number);
				messages = messages.map((m) => {
					if (m.exchange_number === msg.exchange_number && m.role === 'assistant') {
						return { ...m, is_bookmarked: false, bookmark_name: null };
					}
					return m;
				});
			} else {
				const bm = await createBookmark(msg.exchange_number);
				messages = messages.map((m) => {
					if (m.exchange_number === msg.exchange_number && m.role === 'assistant') {
						return { ...m, is_bookmarked: true, bookmark_name: bm.name };
					}
					return m;
				});
			}
		} catch (e: any) {
			addToast(e.message ?? 'Bookmark failed', 'error');
		}
	}

	// ── Annotations ────────────────────────────────────────────

	async function toggleAnnotations(exchangeNumber: number) {
		const newSet = new Set(expandedAnnotations);
		if (newSet.has(exchangeNumber)) {
			newSet.delete(exchangeNumber);
		} else {
			newSet.add(exchangeNumber);
			if (!annotationCache.has(exchangeNumber)) {
				await loadAnnotations(exchangeNumber);
			}
		}
		expandedAnnotations = newSet;
	}

	async function loadAnnotations(exchangeNumber: number) {
		try {
			const res = await listExchangeAnnotations(exchangeNumber);
			const newCache = new Map(annotationCache);
			newCache.set(exchangeNumber, res.annotations);
			annotationCache = newCache;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load annotations', 'error');
		}
	}

	async function handleCreateAnnotation(exchangeNumber: number) {
		if (!annotationInput.trim()) return;
		creatingAnnotation = true;
		try {
			await createAnnotation(exchangeNumber, {
				content: annotationInput.trim(),
				annotation_type: annotationType,
			});
			annotationInput = '';
			await loadAnnotations(exchangeNumber);
			// Update message annotation count
			messages = messages.map((m) => {
				if (m.exchange_number === exchangeNumber && m.role === 'assistant') {
					return { ...m, has_annotations: true, annotation_count: (m.annotation_count ?? 0) + 1 };
				}
				return m;
			});
		} catch (e: any) {
			addToast(e.message ?? 'Failed to create annotation', 'error');
		} finally {
			creatingAnnotation = false;
		}
	}

	async function handleDeleteAnnotation(annotationId: number, exchangeNumber: number) {
		try {
			await deleteAnnotation(annotationId);
			await loadAnnotations(exchangeNumber);
			const count = annotationCache.get(exchangeNumber)?.length ?? 0;
			messages = messages.map((m) => {
				if (m.exchange_number === exchangeNumber && m.role === 'assistant') {
					return { ...m, has_annotations: count > 0, annotation_count: count };
				}
				return m;
			});
		} catch (e: any) {
			addToast(e.message ?? 'Failed to delete annotation', 'error');
		}
	}

	async function handleToggleResolved(annotationId: number, exchangeNumber: number) {
		try {
			await toggleAnnotationResolved(annotationId);
			await loadAnnotations(exchangeNumber);
		} catch (e: any) {
			addToast(e.message ?? 'Failed to toggle resolved', 'error');
		}
	}

	// ── Card picker ────────────────────────────────────────────

	async function loadAvailableCards() {
		try {
			const res = await listCards();
			availableCards = res.cards.map((c: any) => ({ card_id: c.card_id, name: c.name, card_type: c.card_type }));
		} catch {
			// Non-critical
		}
	}

	function toggleCardAttachment(cardId: string) {
		if (attachedCardIds.includes(cardId)) {
			attachedCardIds = attachedCardIds.filter((id) => id !== cardId);
		} else {
			attachedCardIds = [...attachedCardIds, cardId];
		}
	}

	// ── Helpers ─────────────────────────────────────────────────

	function isTruncated(content: string): boolean {
		if (!content) return false;
		const trimmed = content.trimEnd();
		const endChars = ['.', '!', '?', '"', '*', "'", ')', ']', '—'];
		return !endChars.some((c) => trimmed.endsWith(c));
	}

	function dismissError() {
		chatError = null;
	}

	function scrollToBottom() {
		if (chatLog) chatLog.scrollTop = chatLog.scrollHeight;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			if (!isBusy) handleSend();
		}
	}

	const ANNOTATION_COLORS: Record<string, string> = {
		note: 'bg-info-soft border-info-border text-info',
		correction: 'bg-warm-soft border-warm-border text-warm-deep',
		todo: 'bg-purple-soft border-purple-border text-purple',
		gm_note: 'bg-accent-soft border-accent-border text-accent',
	};

	const BOOKMARK_COLORS: Record<string, string> = {
		default: 'text-text-dim',
		gold: 'text-gold',
		red: 'text-error',
		blue: 'text-info',
		green: 'text-success',
	};

	let isBusy = $derived(sending || regenerating || continuing || branchCreating);
</script>

<div class="relative h-[calc(100vh-160px)] flex flex-col bg-surface border border-border-custom rounded-lg overflow-hidden">

	<!-- Floating stats panel (top-left overlay) -->
	{#if showStats && session}
		<div class="absolute top-[50px] left-3 z-10 bg-surface/95 backdrop-blur-sm border border-border-custom rounded-[10px] shadow-lg w-56 max-h-[70%] overflow-y-auto">
			<div class="px-3 py-2.5 border-b border-border-custom flex items-center justify-between sticky top-0 bg-surface/95 backdrop-blur-sm">
				<SectionLabel>Stats</SectionLabel>
				<button
					class="text-xs text-text-dim hover:text-text transition-colors"
					onclick={() => (showStats = false)}
				>Hide</button>
			</div>
			<div class="p-3 space-y-3">
				{#if loadingState}
					<p class="text-xs text-text-dim">Loading...</p>
				{:else if stateSnapshot}
					<!-- Scene -->
					<div>
						<div class="mb-1"><SectionLabel>Scene</SectionLabel></div>
						<div class="space-y-1 text-xs">
							{#if stateSnapshot.scene.location}
								<InfoRow label="Location" value={stateSnapshot.scene.location} />
							{/if}
							{#if stateSnapshot.scene.time_of_day}
								<InfoRow label="Time" value={stateSnapshot.scene.time_of_day} />
							{/if}
							{#if stateSnapshot.scene.mood}
								<InfoRow label="Mood" value={stateSnapshot.scene.mood} />
							{/if}
						</div>
					</div>

					<!-- Characters -->
					{#if Object.keys(stateSnapshot.characters).length > 0}
						<div>
							<div class="mb-1"><SectionLabel>Characters</SectionLabel></div>
							<div class="space-y-1.5">
								{#each Object.entries(stateSnapshot.characters) as [name, char]}
									<div class="text-xs">
										<span class="text-text font-medium">{name}</span>
										{#if char.emotional_state}
											<span class="text-text-dim italic ml-1">{char.emotional_state}</span>
										{/if}
										{#if char.conditions.length > 0}
											<div class="flex flex-wrap gap-0.5 mt-0.5">
												{#each char.conditions as cond}
													<span class="bg-bg-subtle px-1 py-0.5 rounded text-[10px] text-text-dim">{cond}</span>
												{/each}
											</div>
										{/if}
									</div>
								{/each}
							</div>
						</div>
					{/if}
				{:else}
					<p class="text-xs text-text-dim">No state data available.</p>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Search drawer (right side overlay) -->
	{#if showSearch}
		<div class="absolute top-[50px] right-3 z-10 bg-surface/95 backdrop-blur-sm border border-border-custom rounded-[10px] shadow-lg w-80 max-h-[80%] flex flex-col">
			<div class="px-3 py-2.5 border-b border-border-custom flex items-center justify-between shrink-0">
				<SectionLabel>Search</SectionLabel>
				<button class="text-xs text-text-dim hover:text-text transition-colors" onclick={() => (showSearch = false)}>Close</button>
			</div>
			<div class="p-3 space-y-2 shrink-0">
				<input
					type="text"
					bind:value={searchQuery}
					oninput={handleSearchInput}
					placeholder="Search exchanges..."
					class="w-full bg-surface2 border border-border-custom rounded-md px-3 py-1.5 text-sm text-text placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent"
				/>
				<div class="flex gap-1">
					{#each ['semantic', 'keyword', 'hybrid'] as mode}
						<button
							class="text-[10px] px-2 py-0.5 rounded-full transition-colors {searchMode === mode ? 'bg-accent text-text-on-accent' : 'bg-surface2 text-text-dim hover:text-text'}"
							onclick={() => { searchMode = mode as typeof searchMode; if (searchQuery.length >= 2) doSearch(); }}
						>{mode}</button>
					{/each}
				</div>
			</div>
			<div class="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
				{#if searching}
					<p class="text-xs text-text-dim animate-pulse">Searching...</p>
				{:else if searchResults.length === 0 && searchQuery.length >= 2}
					<p class="text-xs text-text-dim">No results found.</p>
				{:else}
					{#each searchResults as hit}
						<button
							class="w-full text-left bg-surface2 border border-border-custom rounded-md p-2 hover:border-accent/30 transition-colors"
							onclick={() => jumpToExchange(hit.exchange_number)}
						>
							<div class="flex items-center justify-between mb-1">
								<span class="text-[10px] text-accent font-medium">#{hit.exchange_number}</span>
								<span class="text-[10px] text-text-dim">{(hit.relevance_score * 100).toFixed(0)}%</span>
							</div>
							<p class="text-xs text-text-dim line-clamp-2">{hit.user_message_snippet}</p>
							<p class="text-xs text-text line-clamp-2 mt-0.5">{hit.assistant_response_snippet}</p>
							{#if hit.npcs_mentioned && hit.npcs_mentioned.length > 0}
								<div class="flex gap-1 mt-1">
									{#each hit.npcs_mentioned as npc}
										<span class="text-[9px] bg-accent/10 text-accent px-1 rounded">{npc}</span>
									{/each}
								</div>
							{/if}
							{#if hit.is_bookmarked}
								<span class="text-[9px] text-amber-500 mt-0.5 inline-block">&#9733; {hit.bookmark_name}</span>
							{/if}
						</button>
					{/each}
				{/if}
			</div>
		</div>
	{/if}

	<!-- Header -->
	<div class="px-4 py-2.5 border-b border-border-custom shrink-0">
		<PageHeader title={$activeRP?.rp_folder ?? 'Chat'} size="sm">
			{#snippet before()}
				<button
					class="text-xs px-2 py-1 rounded transition-colors {showStats
						? 'bg-accent/20 text-accent'
						: 'text-text-dim hover:text-text'}"
					onclick={() => (showStats = !showStats)}
				>Stats</button>
				<button
					class="text-xs px-2 py-1 rounded transition-colors {showSearch
						? 'bg-accent/20 text-accent'
						: 'text-text-dim hover:text-text'}"
					onclick={() => (showSearch = !showSearch)}
				>Search</button>
				<span
					class="text-xs px-2 py-1 rounded {useAgentSDK
						? 'bg-info-soft text-info border border-info-border'
						: 'text-text-dim'}"
					title={useAgentSDK ? 'Chat mode: Agent SDK (change in Settings)' : 'Chat mode: Provider (change in Settings)'}
				>{useAgentSDK ? 'Agent SDK' : 'Provider'}</span>
			{/snippet}
			{#snippet badges()}
				{#if session}
					<span class="text-xs text-text-dim">
						{Math.floor(messages.length / 2)} exchange{Math.floor(messages.length / 2) !== 1 ? 's' : ''}
					</span>
				{/if}
			{/snippet}
			{#snippet actions()}
				{#if session}
					<Btn onclick={handleEndSession} disabled={sessionEnding}>
						{sessionEnding ? 'Ending...' : 'End Session'}
					</Btn>
				{/if}
			{/snippet}
		</PageHeader>
	</div>

	<!-- Main content area -->
	{#if sessionLoading}
		<div class="flex-1 flex items-center justify-center">
			<p class="text-sm text-text-dim animate-pulse">Checking session...</p>
		</div>
	{:else if !$activeRP}
		<div class="flex-1 flex items-center justify-center">
			<div class="text-center space-y-2">
				<p class="text-sm text-text-dim">No RP selected.</p>
				<p class="text-xs text-text-dim">Select an RP from the home page to start chatting.</p>
			</div>
		</div>
	{:else if !session}
		<div class="flex-1 flex items-center justify-center">
			<div class="text-center space-y-4">
				<div class="space-y-1">
					<p class="text-sm text-text">No active session</p>
					<p class="text-xs text-text-dim">Start a session to begin roleplaying in <strong>{$activeRP.rp_folder}</strong>.</p>
				</div>
				<Btn primary onclick={handleStartSession} disabled={sessionStarting}>
					{sessionStarting ? 'Starting...' : 'Start Session'}
				</Btn>
			</div>
		</div>
	{:else}
		<!-- Chat log (centered) -->
		<div
			bind:this={chatLog}
			class="flex-1 overflow-y-auto p-4"
			onscroll={handleScroll}
		>
			<div class="max-w-[620px] mx-auto space-y-4">
				<!-- Load more indicator -->
				{#if loadingMore}
					<div class="text-center py-2">
						<p class="text-xs text-text-dim animate-pulse">Loading earlier messages...</p>
					</div>
				{:else if !allLoaded && messages.length > 0}
					<div class="text-center py-2">
						<button
							class="text-xs text-accent hover:text-accent/80 transition-colors"
							onclick={loadMoreExchanges}
						>Load earlier messages</button>
					</div>
				{/if}

				{#if messages.length === 0 && !sending}
					<div class="flex items-center justify-center h-full text-sm text-text-dim" style="min-height: 200px">
						Session active. Type a message below to begin.
					</div>
				{/if}

				{#if messages.length > 0 && branchingExchange === null}
					<div class="flex justify-center py-1">
						<button
							class="text-[10px] text-text-dim hover:text-accent transition-colors flex items-center gap-1"
							onclick={() => startBranch(0)}
							disabled={isBusy}
						>
							<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/>
							</svg>
							Branch from start
						</button>
					</div>
				{/if}

				{#if branchingExchange === 0}
					<div class="bg-accent/5 border border-accent/20 rounded-lg p-3 mx-4 mb-2">
						<p class="text-xs text-text-dim mb-2">Fresh start — new root branch with no parent, no inherited state</p>
						<div class="flex items-center gap-2">
							<input
								type="text"
								bind:value={branchName}
								class="flex-1 text-sm bg-bg border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-accent"
								placeholder="Branch name"
							/>
							<button
								class="text-xs text-accent hover:text-accent/80 font-medium disabled:opacity-30"
								onclick={confirmBranch}
								disabled={branchCreating || !branchName.trim()}
							>{branchCreating ? 'Creating...' : 'Create'}</button>
							<button
								class="text-xs text-text-dim hover:text-text"
								onclick={cancelBranch}
								disabled={branchCreating}
							>Cancel</button>
						</div>
					</div>
				{/if}

				{#each messages as msg, i}
					{@const isInherited = msg.branch != null && msg.branch !== $activeBranch}
					{@const isDirection = msg.message_mode === 'direction'}
					<div class="flex gap-3 {msg.role === 'user' ? 'justify-end' : ''} {isInherited ? 'opacity-60' : ''}">
						{#if msg.role === 'user'}
							<div class="max-w-[80%] group">
								<div class="{isDirection ? 'bg-blue-500/5 border border-blue-400/20' : 'bg-gradient-to-br from-accent/10 to-accent/5 border border-accent/20'} rounded-[14px] rounded-br-[4px] px-4 py-3">
									{#if editingExchange != null && editingExchange.number === msg.exchange_number && editingExchange.role === 'user'}
										<textarea
											bind:value={editContent}
											class="w-full text-sm text-text bg-transparent border border-accent/30 rounded-lg px-2 py-1.5 resize-y focus:outline-none focus:ring-1 focus:ring-accent min-h-[60px]"
											rows="3"
										></textarea>
										<div class="flex items-center gap-1.5 mt-1.5">
											<button
												class="text-[10px] text-accent hover:text-accent/80 font-medium disabled:opacity-30"
												onclick={saveEdit}
												disabled={editSaving || !editContent.trim()}
											>{editSaving ? 'Saving...' : 'Save'}</button>
											<button
												class="text-[10px] text-text-dim hover:text-text"
												onclick={cancelEdit}
												disabled={editSaving}
											>Cancel</button>
										</div>
									{:else}
										{#if isDirection}
											<p class="text-[10px] text-blue-400 font-medium mb-1">Author Direction</p>
										{/if}
										<p class="text-sm whitespace-pre-wrap {isDirection ? 'text-text-dim italic' : 'text-text'}">{msg.content}</p>
										<div class="flex items-center gap-1.5 mt-1.5">
											<p class="text-[10px] text-text-dim">{formatTime(msg.timestamp)}</p>
											{#if isInherited}
												<span class="text-[9px] bg-text-dim/10 text-text-dim px-1 rounded">from {msg.branch}</span>
											{/if}
										</div>
									{/if}
								</div>
								<!-- User message edit button -->
								{#if msg.exchange_number && session && !editingExchange && !isInherited}
									<div class="flex justify-end mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
										<button
											class="p-1 rounded text-text-dim hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-30"
											onclick={() => startEdit(msg.exchange_number!, 'user')}
											disabled={isBusy}
											title="Edit message"
										>
											<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
										</button>
									</div>
								{/if}
							</div>
						{:else}
							<div class="max-w-[80%] group" data-exchange={msg.exchange_number}>
								<!-- Bookmark indicator -->
								{#if msg.is_bookmarked}
									<div class="text-[10px] {BOOKMARK_COLORS[msg.bookmark_name ? 'gold' : 'default']} mb-0.5 flex items-center gap-1">
										<span>&#9733;</span>
										<span>{msg.bookmark_name ?? 'Bookmarked'}</span>
									</div>
								{/if}

								<div class="bg-surface-raised border border-border-custom/50 rounded-[14px] rounded-bl-[4px] px-4 py-3 shadow-[0_1px_4px_rgba(0,0,0,0.04)] {msg.is_bookmarked ? 'border-l-2 border-l-gold' : ''}">
									<!-- Main response text -->
									{#if editingExchange != null && editingExchange.number === msg.exchange_number && editingExchange.role === 'assistant'}
										<textarea
											bind:value={editContent}
											class="w-full text-sm text-text font-serif bg-transparent border border-border-custom/50 rounded-lg px-2 py-1.5 resize-y focus:outline-none focus:ring-1 focus:ring-accent min-h-[120px]"
											rows="6"
										></textarea>
										<div class="flex items-center gap-1.5 mt-1.5">
											<button
												class="text-[10px] text-accent hover:text-accent/80 font-medium disabled:opacity-30"
												onclick={saveEdit}
												disabled={editSaving || !editContent.trim()}
											>{editSaving ? 'Saving...' : 'Save'}</button>
											<button
												class="text-[10px] text-text-dim hover:text-text"
												onclick={cancelEdit}
												disabled={editSaving}
											>Cancel</button>
										</div>
									{:else if regenerating && activeStreamExchange === msg.exchange_number}
										<p class="text-sm text-text whitespace-pre-wrap font-serif">{regenerateStreamContent || 'Regenerating...'}</p>
									{:else}
										<p class="text-sm text-text whitespace-pre-wrap font-serif">{msg.content}</p>
									{/if}

									<!-- Continue stream appended -->
									{#if continuing && activeStreamExchange === msg.exchange_number && continueStreamContent}
										<div class="mt-1 pt-1 border-t border-accent/20">
											<p class="text-sm text-text whitespace-pre-wrap font-serif">{continueStreamContent}</p>
										</div>
									{/if}

									<div class="flex items-center gap-1.5 mt-1.5">
										<p class="text-[10px] text-text-dim">{formatTime(msg.timestamp)}</p>
										{#if isInherited}
											<span class="text-[9px] bg-text-dim/10 text-text-dim px-1 rounded">from {msg.branch}</span>
										{/if}
									</div>
								</div>

								<!-- Controls row -->
								{#if msg.exchange_number && session}
									<div class="flex items-center gap-1.5 mt-1 opacity-0 group-hover:opacity-100 transition-opacity {msg.has_variants || msg.is_bookmarked || (msg.annotation_count ?? 0) > 0 ? '!opacity-100' : ''}">
										<!-- Swipe controls -->
										{#if msg.has_variants && (msg.variant_count ?? 0) > 1}
											<button
												class="p-1 rounded text-text-dim hover:text-text hover:bg-bg-subtle transition-colors disabled:opacity-30"
												onclick={() => handleSwipe(msg.exchange_number!, 'prev')}
												disabled={isBusy}
												title="Previous variant"
											>
												<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
											</button>
											<span class="text-[10px] text-text-dim tabular-nums min-w-[20px] text-center">
												{msg.variant_count}
											</span>
											<button
												class="p-1 rounded text-text-dim hover:text-text hover:bg-bg-subtle transition-colors disabled:opacity-30"
												onclick={() => handleSwipe(msg.exchange_number!, 'next')}
												disabled={isBusy}
												title="Next variant"
											>
												<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
											</button>
											<div class="w-px h-3 bg-border-custom mx-0.5"></div>
										{/if}

										<!-- Regenerate -->
										<button
											class="p-1 rounded text-text-dim hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-30"
											onclick={() => handleRegenerate(msg.exchange_number!)}
											disabled={isBusy}
											title="Regenerate response"
										>
											<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
										</button>

										<!-- Continue -->
										<button
											class="p-1 rounded text-text-dim hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-30"
											onclick={() => handleContinue(msg.exchange_number!)}
											disabled={isBusy}
											title={isTruncated(msg.content) ? 'Continue (may be truncated)' : 'Continue generating'}
										>
											<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/></svg>
										</button>

										<div class="w-px h-3 bg-border-custom mx-0.5"></div>

										<!-- Bookmark toggle -->
										<button
											class="p-1 rounded transition-colors disabled:opacity-30 {msg.is_bookmarked ? 'text-gold hover:text-warm-deep' : 'text-text-dim hover:text-gold hover:bg-gold-soft'}"
											onclick={() => handleToggleBookmark(msg)}
											disabled={isBusy}
											title={msg.is_bookmarked ? 'Remove bookmark' : 'Bookmark'}
										>
											<svg class="w-3.5 h-3.5" fill={msg.is_bookmarked ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/></svg>
										</button>

										<!-- Annotation toggle -->
										<button
											class="p-1 rounded transition-colors {(msg.annotation_count ?? 0) > 0 ? 'text-info hover:text-info/80' : 'text-text-dim hover:text-info hover:bg-info-soft'}"
											onclick={() => toggleAnnotations(msg.exchange_number!)}
											title="{(msg.annotation_count ?? 0) > 0 ? `${msg.annotation_count} annotation(s)` : 'Add annotation'}"
										>
											<svg class="w-3.5 h-3.5" fill={(msg.annotation_count ?? 0) > 0 ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z"/></svg>
										</button>

										<!-- Edit -->
										<button
											class="p-1 rounded text-text-dim hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-30"
											onclick={() => startEdit(msg.exchange_number!, 'assistant')}
											disabled={isBusy || !!editingExchange}
											title="Edit response"
										>
											<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
										</button>

										<!-- Branch from exchange -->
										<button
											class="p-1 rounded text-text-dim hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-30"
											onclick={() => startBranch(msg.exchange_number!)}
											disabled={isBusy || branchingExchange != null}
											title="Branch from this exchange"
										>
											<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>
										</button>

										<!-- Truncation hint -->
										{#if isTruncated(msg.content)}
											<span class="text-[9px] text-warning ml-0.5" title="Response may be truncated">truncated?</span>
										{/if}
									</div>
								{/if}


								<!-- Branch name popover -->
								{#if branchingExchange === msg.exchange_number}
									<div class="mt-1.5 p-2 bg-bg-elevated border border-border-custom rounded-lg shadow-sm">
										<label class="block text-[10px] font-medium text-text-dim mb-1" for="branch-name-input">Branch Name</label>
										<input
											id="branch-name-input"
											type="text"
											bind:value={branchName}
											class="w-full text-xs bg-bg-subtle border border-border-custom rounded px-2 py-1 text-text placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent"
											onkeydown={(e) => { if (e.key === 'Enter') confirmBranch(); }}
										/>
										<div class="flex items-center gap-1.5 mt-1.5">
											<button
												class="text-[10px] text-accent hover:text-accent/80 font-medium disabled:opacity-30"
												onclick={confirmBranch}
												disabled={branchCreating || !branchName.trim()}
											>{branchCreating ? 'Creating...' : 'Create Branch'}</button>
											<button
												class="text-[10px] text-text-dim hover:text-text"
												onclick={cancelBranch}
												disabled={branchCreating}
											>Cancel</button>
										</div>
									</div>
								{/if}

								<!-- Annotations panel (expandable) -->
								{#if msg.exchange_number && expandedAnnotations.has(msg.exchange_number)}
									<div class="mt-2 space-y-2">
										<!-- Existing annotations -->
										{#each annotationCache.get(msg.exchange_number) ?? [] as ann}
											<div class="text-xs border rounded-md p-2 {ANNOTATION_COLORS[ann.annotation_type] ?? ANNOTATION_COLORS.note}">
												<div class="flex items-center justify-between mb-1">
													<div class="flex items-center gap-1.5">
														<span class="font-medium uppercase text-[10px]">{ann.annotation_type}</span>
														{#if ann.annotation_type === 'todo' || ann.annotation_type === 'correction'}
															<button
																class="text-[10px] underline opacity-70 hover:opacity-100"
																onclick={() => handleToggleResolved(ann.id, msg.exchange_number!)}
															>{ann.resolved ? 'Unresolve' : 'Resolve'}</button>
															{#if ann.resolved}
																<span class="text-[10px] opacity-60 line-through">resolved</span>
															{/if}
														{/if}
													</div>
													<button
														class="text-[10px] opacity-50 hover:opacity-100"
														onclick={() => handleDeleteAnnotation(ann.id, msg.exchange_number!)}
													>Delete</button>
												</div>
												<p class="whitespace-pre-wrap {ann.resolved ? 'line-through opacity-50' : ''}">{ann.content}</p>
											</div>
										{/each}

										<!-- New annotation form -->
										<div class="flex gap-1.5 items-start">
											<select
												bind:value={annotationType}
												class="text-[10px] bg-surface2 border border-border-custom rounded px-1 py-1"
											>
												<option value="note">note</option>
												<option value="correction">correction</option>
												<option value="todo">todo</option>
												<option value="gm_note">gm note</option>
											</select>
											<input
												type="text"
												bind:value={annotationInput}
												placeholder="Add annotation..."
												class="flex-1 text-xs bg-surface2 border border-border-custom rounded px-2 py-1 text-text placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent"
												onkeydown={(e) => { if (e.key === 'Enter') handleCreateAnnotation(msg.exchange_number!); }}
											/>
											<button
												class="text-xs text-accent hover:text-accent/80 px-2 py-1 disabled:opacity-30"
												onclick={() => handleCreateAnnotation(msg.exchange_number!)}
												disabled={creatingAnnotation || !annotationInput.trim()}
											>Add</button>
										</div>
									</div>
								{/if}
							</div>
						{/if}
					</div>
				{/each}

				<!-- Streaming bubble (new message) -->
				{#if sending}
					<div class="flex gap-3">
						<div class="max-w-[80%] bg-surface-raised border border-border-custom/50 rounded-[14px] rounded-bl-[4px] px-4 py-3 shadow-[0_1px_4px_rgba(0,0,0,0.04)]">
							{#if streamingContent}
								<p class="text-sm text-text whitespace-pre-wrap font-serif">{streamingContent}</p>
							{:else}
								<p class="text-sm text-text-dim animate-pulse font-serif">Generating...</p>
							{/if}
						</div>
					</div>
				{/if}

				<!-- Error inline -->
				{#if chatError}
					<div class="bg-error-soft border border-error-border rounded-lg px-4 py-3 flex items-start justify-between gap-3">
						<div>
							<p class="text-sm text-error font-medium">Failed to send</p>
							<p class="text-xs text-error/80 mt-0.5">{chatError}</p>
						</div>
						<button
							class="text-xs text-error/70 hover:text-error shrink-0"
							onclick={dismissError}
						>Dismiss</button>
					</div>
				{/if}
			</div>
		</div>

		<!-- Input bar (centered) -->
		<div class="border-t border-border-custom shrink-0">
			<!-- Advanced options panel -->
			{#if showAdvanced}
				<div class="max-w-[620px] mx-auto px-4 pt-2 pb-1 space-y-2 border-b border-border-custom/50">
					<div class="flex items-center gap-3 flex-wrap">
						<!-- Message mode selector -->
						<div class="flex items-center bg-surface2 rounded-md border border-border-custom overflow-hidden">
							{#each [['rp', 'RP'], ['ooc', 'OOC'], ['direction', 'Dir']] as [mode, label]}
								<button
									class="px-2.5 py-1 text-xs transition-colors {messageMode === mode ? 'bg-accent text-white' : 'text-text-dim hover:text-text hover:bg-bg-subtle'}"
									onclick={() => messageMode = mode as MessageMode}
								>{label}</button>
							{/each}
						</div>

						<!-- Scene override -->
						<div class="flex items-center gap-1.5">
							<input
								type="text"
								bind:value={sceneLocation}
								placeholder="Location override"
								class="text-xs bg-surface2 border border-border-custom rounded px-2 py-1 w-32 text-text placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent"
							/>
							<input
								type="text"
								bind:value={sceneMood}
								placeholder="Mood override"
								class="text-xs bg-surface2 border border-border-custom rounded px-2 py-1 w-28 text-text placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent"
							/>
						</div>

						<!-- Card attachment -->
						<button
							class="text-xs px-2 py-1 rounded transition-colors {attachedCardIds.length > 0 ? 'bg-accent/20 text-accent' : 'text-text-dim hover:text-text hover:bg-bg-subtle'}"
							onclick={() => { showCardPicker = !showCardPicker; if (!showCardPicker) return; loadAvailableCards(); }}
						>
							{attachedCardIds.length > 0 ? `${attachedCardIds.length} card(s)` : 'Attach card'}
						</button>
					</div>

					<!-- Card picker dropdown -->
					{#if showCardPicker}
						<div class="bg-surface2 border border-border-custom rounded-md p-2 max-h-32 overflow-y-auto">
							{#if availableCards.length === 0}
								<p class="text-xs text-text-dim">No cards available</p>
							{:else}
								{#each availableCards as card}
									<label class="flex items-center gap-2 text-xs py-0.5 cursor-pointer hover:bg-bg-subtle rounded px-1">
										<input
											type="checkbox"
											checked={attachedCardIds.includes(card.card_id)}
											onchange={() => toggleCardAttachment(card.card_id)}
											class="accent-accent"
										/>
										<span class="text-text">{card.name}</span>
										<span class="text-text-dim text-[10px]">{card.card_type}</span>
									</label>
								{/each}
							{/if}
						</div>
					{/if}

					<!-- Mode indicator -->
					{#if messageMode === 'ooc'}
						<p class="text-[10px] text-amber-600">OOC mode: message will not be saved as an exchange</p>
					{:else if messageMode === 'direction'}
						<p class="text-[10px] text-blue-500">Direction mode: guidance for the LLM, saved but not narrated</p>
					{:else if hasInlineDirection}
						<p class="text-[10px] text-blue-400">Contains inline direction markers (( )) or //</p>
					{/if}
					<!-- Agent SDK indicator -->
					{#if useAgentSDK}
						<p class="text-[10px] text-info">Claude Agent SDK: Claude orchestrates tool calls autonomously</p>
					{/if}
				</div>
			{/if}

			<div class="max-w-[620px] mx-auto px-4 py-3 flex items-end gap-2">
				<!-- Advanced options toggle -->
				<button
					class="shrink-0 p-2 rounded transition-colors {showAdvanced ? 'text-accent bg-accent/10' : 'text-text-dim hover:text-text hover:bg-bg-subtle'}"
					onclick={() => (showAdvanced = !showAdvanced)}
					title="Advanced options"
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
				</button>

				<textarea
					bind:value={userInput}
					onkeydown={handleKeydown}
					oninput={autoGrowTextarea}
					placeholder={messageMode === 'ooc' ? 'Type an OOC message...' : messageMode === 'direction' ? 'Type author direction...' : 'Type a message...'}
					rows="3"
					class="flex-1 bg-bg-subtle border border-border-custom rounded-lg px-3 py-2 text-sm text-text
						placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent resize-none
						max-h-64 overflow-y-auto {messageMode === 'ooc' ? 'border-warm' : messageMode === 'direction' ? 'border-blue-400/50' : ''}"
				></textarea>
				<Btn primary onclick={handleSend} disabled={isBusy || !userInput.trim()}>
					{sending ? '...' : messageMode === 'ooc' ? 'OOC' : messageMode === 'direction' ? 'Direct' : 'Send'}
				</Btn>
			</div>
		</div>
	{/if}
</div>
