<script lang="ts">
	import { onMount } from 'svelte';
	import { activeRP } from '$lib/stores/rp';
	import { addToast } from '$lib/stores/ui';
	import { getGuidelines } from '$lib/api/context';
	import { listCards } from '$lib/api/cards';
	import { listThreads } from '$lib/api/threads';
	import { exportRP } from '$lib/api/rp';
	import type { GuidelinesResponse, StoryCardSummary, ThreadDetail } from '$lib/types';
	import { CARD_TYPES } from '$lib/types/enums';
	import Badge from '$lib/components/ui/Badge.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import ProgressBar from '$lib/components/ui/ProgressBar.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import TabBar from '$lib/components/ui/TabBar.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import ListItem from '$lib/components/ui/ListItem.svelte';
	import InfoRow from '$lib/components/ui/InfoRow.svelte';
	import CardSection from '$lib/components/ui/CardSection.svelte';
	import BackButton from '$lib/components/ui/BackButton.svelte';
	import Btn from '$lib/components/ui/Btn.svelte';

	// ── Data ─────────────────────────────────────────────────
	let guidelines = $state<GuidelinesResponse | null>(null);
	let cardCounts = $state<Record<string, number>>({});
	let totalCards = $state(0);
	let chapters = $state<StoryCardSummary[]>([]);
	let threads = $state<ThreadDetail[]>([]);
	let loading = $state(false);

	// ── Right panel state ─────────────────────────────────────
	let activeTab = $state<'chapters' | 'threads'>('chapters');
	let expandedChapter = $state<StoryCardSummary | null>(null);
	let expandedThread = $state<string | null>(null);

	onMount(async () => {
		if (!$activeRP) return;
		loading = true;
		try {
			const [gl, allCards, threadRes, chapterRes] = await Promise.all([
				getGuidelines($activeRP.rp_folder).catch(() => null),
				listCards().catch(() => ({ cards: [], total: 0 })),
				listThreads().catch(() => ({ threads: [], total: 0 })),
				listCards({ card_type: 'chapter_summary' }).catch(() => ({ cards: [], total: 0 })),
			]);

			guidelines = gl;
			totalCards = allCards.total;
			cardCounts = allCards.cards.reduce((acc, c) => {
				acc[c.card_type] = (acc[c.card_type] ?? 0) + 1;
				return acc;
			}, {} as Record<string, number>);
			threads = threadRes.threads;
			chapters = chapterRes.cards;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load RP data', 'error');
		} finally {
			loading = false;
		}
	});

	// ── Helpers ───────────────────────────────────────────────
	function toneArray(tone: string[] | string | null): string[] {
		if (!tone) return [];
		return Array.isArray(tone) ? tone : [tone];
	}

	function nextThreshold(thread: ThreadDetail): { label: string; value: number } | null {
		const entries = Object.entries(thread.thresholds)
			.map(([k, v]) => ({ label: k, value: v }))
			.sort((a, b) => a.value - b.value);
		return entries.find(e => e.value > thread.current_counter) ?? entries[entries.length - 1] ?? null;
	}

	function threadProgress(thread: ThreadDetail): number {
		const next = nextThreshold(thread);
		if (!next || next.value === 0) return 0;
		return Math.min(100, (thread.current_counter / next.value) * 100);
	}

	function priorityStyle(p: string | null) {
		if (p === 'plot_critical') return 'bg-error/20 text-error';
		if (p === 'important') return 'bg-warning/20 text-warning';
		return 'bg-bg-subtle text-text-dim';
	}

	function statusStyle(s: string) {
		if (s === 'active') return 'bg-success/20 text-success';
		if (s === 'dormant') return 'bg-warning/20 text-warning';
		return 'bg-bg-subtle text-text-dim';
	}

	function progressColor(pct: number) {
		if (pct >= 80) return 'var(--color-error)';
		if (pct >= 50) return 'var(--color-warm)';
		return 'var(--color-accent)';
	}

	const typeLabel: Record<string, string> = {
		npc: 'NPCs', character: 'Characters', location: 'Locations',
		memory: 'Memories', secret: 'Secrets', knowledge: 'Knowledge',
		organization: 'Organizations', plot_thread: 'Plot Threads',
		plot_arc: 'Plot Arcs', chapter_summary: 'Chapters',
		item: 'Items', lore: 'Lore',
	};

	let activeThreads   = $derived(threads.filter(t => t.status === 'active'));
	let dormantThreads  = $derived(threads.filter(t => t.status === 'dormant'));
	let resolvedThreads = $derived(threads.filter(t => t.status === 'resolved'));

	let exporting = $state(false);
	let includeOptional = $state(true);

	async function handleExport() {
		if (!$activeRP) return;
		exporting = true;
		try {
			const blob = await exportRP($activeRP.rp_folder, { include_optional: includeOptional });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `${$activeRP.rp_folder}_export.zip`;
			a.click();
			URL.revokeObjectURL(url);
			addToast('Export downloaded', 'success');
		} catch (e: any) {
			addToast(e.message ?? 'Export failed', 'error');
		} finally {
			exporting = false;
		}
	}
</script>

<div class="flex gap-4">

	<!-- ── Left: Story Bible + Card Health ──────────────────── -->
	<aside class="w-[280px] shrink-0 space-y-3 self-start">

		<!-- RP header -->
		<div class="bg-surface rounded-[10px] border border-border-custom px-4 py-3">
			<PageHeader title={$activeRP?.rp_folder ?? 'RP Overview'} size="md" subtitle="{totalCards} story card{totalCards !== 1 ? 's' : ''}">
				{#snippet actions()}
					<a
						href="/{$activeRP?.rp_folder}/dashboard?tool=settings"
						class="text-text-dim hover:text-accent transition-colors"
						title="RP Settings"
					>
						<svg class="w-4.5 h-4.5" viewBox="0 0 20 20" fill="currentColor">
							<path fill-rule="evenodd" d="M8.34 1.804A1 1 0 019.32 1h1.36a1 1 0 01.98.804l.295 1.473c.497.2.966.46 1.398.772l1.423-.47a1 1 0 011.164.39l.68 1.178a1 1 0 01-.184 1.194l-1.128 1.003a6.02 6.02 0 010 1.312l1.128 1.003a1 1 0 01.184 1.194l-.68 1.178a1 1 0 01-1.164.39l-1.423-.47a5.99 5.99 0 01-1.398.772l-.295 1.473a1 1 0 01-.98.804H9.32a1 1 0 01-.98-.804l-.295-1.473a5.99 5.99 0 01-1.398-.772l-1.423.47a1 1 0 01-1.164-.39l-.68-1.178a1 1 0 01.184-1.194l1.128-1.003a6.02 6.02 0 010-1.312L3.563 5.82a1 1 0 01-.184-1.194l.68-1.178a1 1 0 011.164-.39l1.423.47c.432-.312.9-.572 1.398-.772L8.34 1.804zM10 13a3 3 0 100-6 3 3 0 000 6z" clip-rule="evenodd" />
						</svg>
					</a>
				{/snippet}
			</PageHeader>
		</div>

		<!-- Guidelines (Story Bible) -->
		{#if guidelines}
			<CardSection title="Story Bible" compact>
				<div class="px-4 py-3 space-y-2 text-sm">
					{#if guidelines.pov_character}
						<InfoRow label="POV Character" value={guidelines.pov_character} />
					{/if}
					{#if guidelines.pov_mode}
						<InfoRow label="POV Mode"><span class="font-medium text-text capitalize">{guidelines.pov_mode}</span></InfoRow>
					{/if}
					{#if guidelines.narrative_voice}
						<InfoRow label="Narrative Voice"><span class="font-medium text-text capitalize">{guidelines.narrative_voice} person</span></InfoRow>
					{/if}
					{#if guidelines.tense}
						<InfoRow label="Tense"><span class="font-medium text-text capitalize">{guidelines.tense}</span></InfoRow>
					{/if}
					{#if guidelines.scene_pacing}
						<InfoRow label="Pacing"><span class="font-medium text-text capitalize">{guidelines.scene_pacing}</span></InfoRow>
					{/if}
					{#if guidelines.response_length}
						<InfoRow label="Response Length"><span class="font-medium text-text capitalize">{guidelines.response_length}</span></InfoRow>
					{/if}
					{#if toneArray(guidelines.tone).length}
						<div>
							<span class="text-text-dim block mb-1">Tone</span>
							<div class="flex flex-wrap gap-1">
								{#each toneArray(guidelines.tone) as t}
									<span class="text-xs bg-accent/10 text-accent px-1.5 py-0.5 rounded">{t}</span>
								{/each}
							</div>
						</div>
					{/if}
					{#if guidelines.sensitive_themes?.length}
						<div>
							<span class="text-text-dim block mb-1">Sensitive Themes</span>
							<div class="flex flex-wrap gap-1">
								{#each guidelines.sensitive_themes as theme}
									<span class="text-xs bg-warning/10 text-warning px-1.5 py-0.5 rounded">{theme}</span>
								{/each}
							</div>
						</div>
					{/if}
					{#if guidelines.dual_characters?.length}
						<div>
							<span class="text-text-dim block mb-1">Dual Characters</span>
							<div class="flex flex-wrap gap-1">
								{#each guidelines.dual_characters as c}
									<span class="text-xs bg-bg-subtle text-text px-1.5 py-0.5 rounded">{c}</span>
								{/each}
							</div>
						</div>
					{/if}
				</div>
			</CardSection>
		{:else if !loading}
			<div class="bg-surface rounded-[10px] border border-border-custom px-4 py-3 text-sm text-text-dim">
				No guidelines found. Create a <code class="text-xs font-mono">guidelines.md</code> in the RP folder.
			</div>
		{/if}

		<!-- Card health -->
		<CardSection title="Card Library" compact>
			{#if loading}
				<div class="px-4 py-3 text-sm text-text-dim">Loading...</div>
			{:else if totalCards === 0}
				<div class="px-4 py-3 text-sm text-text-dim">No story cards indexed yet.</div>
			{:else}
				<div class="divide-y divide-border-custom">
					{#each CARD_TYPES as type}
						{#if cardCounts[type]}
							<div class="flex items-center justify-between px-4 py-2 text-sm">
								<span class="text-text-dim">{typeLabel[type] ?? type}</span>
								<span class="text-text font-medium tabular-nums">{cardCounts[type]}</span>
							</div>
						{/if}
					{/each}
				</div>
			{/if}
		</CardSection>

		<!-- Export -->
		<CardSection title="Export" compact>
			<div class="px-4 py-3 space-y-3">
				<label class="flex items-center gap-2 text-sm text-text-dim cursor-pointer">
					<input type="checkbox" bind:checked={includeOptional} class="accent-[var(--color-accent)]" />
					Include optional data
				</label>
				<Btn onclick={handleExport} disabled={exporting} class="w-full">
					{exporting ? 'Exporting...' : 'Export RP'}
				</Btn>
			</div>
		</CardSection>
	</aside>

	<!-- ── Right: Chapters / Plot Threads ───────────────────── -->
	<div class="flex-1 min-w-0 space-y-3">

		<!-- Tab switcher + back button -->
		<div class="flex items-center gap-2">
			{#if expandedChapter || expandedThread}
				<div class="mr-2">
					<BackButton onclick={() => { expandedChapter = null; expandedThread = null; }} />
				</div>
			{/if}
			<TabBar
				items={[{id: 'chapters', label: 'Chapters'}, {id: 'threads', label: 'Plot Threads'}]}
				bind:active={activeTab}
				onselect={() => { expandedChapter = null; }}
			/>
			{#if activeTab === 'chapters'}
				<span class="text-xs text-text-dim">{chapters.length} chapter{chapters.length !== 1 ? 's' : ''}</span>
			{:else}
				<span class="text-xs text-text-dim">{activeThreads.length} active</span>
			{/if}
		</div>

		<!-- ── Chapters ─────────────────────────────────────── -->
		{#if activeTab === 'chapters'}
			{#if expandedChapter}
				<!-- Full chapter view -->
				<div class="bg-surface rounded-[10px] border border-border-custom p-5">
					<h2 class="text-base font-semibold text-text font-serif mb-3">{expandedChapter.name}</h2>
					{#if expandedChapter.summary}
						<p class="text-sm text-text whitespace-pre-wrap leading-relaxed">{expandedChapter.summary}</p>
					{:else}
						<p class="text-sm text-text-dim">No content preview available.</p>
					{/if}
				</div>
			{:else if chapters.length === 0}
				<EmptyState message={loading ? 'Loading chapters...' : 'No chapter summaries yet.'} variant={loading ? 'loading' : 'empty'} />
			{:else}
				<div class="space-y-2">
					{#each chapters as chapter, i}
						<ListItem variant="card" onclick={() => (expandedChapter = chapter)}>
							<div class="flex items-start justify-between gap-3">
								<div class="min-w-0">
									<div class="flex items-center gap-2 mb-1">
										<span class="text-xs text-text-dim font-mono">Ch. {i + 1}</span>
										{#if chapter.tags?.length}
											{#each chapter.tags.slice(0, 2) as tag}
												<Badge>{tag}</Badge>
											{/each}
										{/if}
									</div>
									<p class="text-sm font-medium text-text">{chapter.name}</p>
									{#if chapter.summary}
										<p class="text-xs text-text-dim mt-1 line-clamp-2">{chapter.summary}</p>
									{/if}
								</div>
								<svg class="w-4 h-4 text-text-dim shrink-0 mt-0.5" viewBox="0 0 16 16" fill="none"
									stroke="currentColor" stroke-width="2">
									<path d="M6 4l4 4-4 4" />
								</svg>
							</div>
						</ListItem>
					{/each}
				</div>
			{/if}

		<!-- ── Plot Threads ──────────────────────────────────── -->
		{:else}
			{#if loading}
				<EmptyState message="Loading threads..." variant="loading" />
			{:else if threads.length === 0}
				<EmptyState message="No plot threads found." />
			{:else}
				<div class="space-y-4">
					<!-- Active threads -->
					{#if activeThreads.length}
						<div class="space-y-2">
							<div class="px-1"><SectionLabel>Active</SectionLabel></div>
							{#each activeThreads as thread}
								{@const pct = threadProgress(thread)}
								{@const next = nextThreshold(thread)}
								<div class="bg-surface rounded-[10px] border border-border-custom overflow-hidden">
									<button
										class="w-full flex items-start gap-3 px-4 py-3 hover:bg-bg-subtle transition-colors text-left"
										onclick={() => (expandedThread = expandedThread === thread.thread_id ? null : thread.thread_id)}
									>
										<div class="flex-1 min-w-0 space-y-2">
											<div class="flex items-center gap-2 flex-wrap">
												<span class="text-sm font-medium text-text">{thread.name}</span>
												{#if thread.priority}
													<span class="text-xs px-1.5 py-0.5 rounded {priorityStyle(thread.priority)}">{thread.priority.replaceAll('_', ' ')}</span>
												{/if}
												{#if thread.thread_type}
													<span class="text-xs bg-bg-subtle text-text-dim px-1.5 py-0.5 rounded">{thread.thread_type}</span>
												{/if}
											</div>
											<!-- Progress bar -->
											<div class="flex items-center gap-2">
												<div class="flex-1">
													<ProgressBar value={pct} color={progressColor(pct)} height={6} />
												</div>
												<span class="text-xs text-text-dim shrink-0 font-mono">
													{thread.current_counter}{next ? `/${next.value}` : ''}
												</span>
											</div>
										</div>
										<svg class="w-4 h-4 text-text-dim shrink-0 mt-0.5 transition-transform
												{expandedThread === thread.thread_id ? 'rotate-180' : ''}"
											viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
											<path d="M4 6l4 4 4-4" />
										</svg>
									</button>

									{#if expandedThread === thread.thread_id}
										<div class="border-t border-border-custom px-4 py-3 space-y-3 text-sm">
											{#if thread.related_characters?.length}
												<div>
													<p class="text-xs text-text-dim mb-1">Related Characters</p>
													<div class="flex flex-wrap gap-1">
														{#each thread.related_characters as c}
															<span class="text-xs bg-accent/10 text-accent px-1.5 py-0.5 rounded">{c}</span>
														{/each}
													</div>
												</div>
											{/if}
											{#if thread.keywords?.length}
												<div>
													<p class="text-xs text-text-dim mb-1">Keywords</p>
													<div class="flex flex-wrap gap-1">
														{#each thread.keywords as kw}
															<span class="text-xs bg-bg-subtle text-text-dim px-1.5 py-0.5 rounded">{kw}</span>
														{/each}
													</div>
												</div>
											{/if}
											{#if Object.keys(thread.consequences).length}
												<div>
													<p class="text-xs text-text-dim mb-1">Consequences</p>
													<div class="space-y-1">
														{#each Object.entries(thread.consequences) as [level, text]}
															<div class="flex gap-2 text-xs">
																<span class="text-text-dim shrink-0 font-mono">{level}:</span>
																<span class="text-text">{text}</span>
															</div>
														{/each}
													</div>
												</div>
											{/if}
										</div>
									{/if}
								</div>
							{/each}
						</div>
					{/if}

					<!-- Dormant threads -->
					{#if dormantThreads.length}
						<div class="space-y-2">
							<div class="px-1"><SectionLabel>Dormant</SectionLabel></div>
							{#each dormantThreads as thread}
								<div class="bg-surface rounded-[10px] border border-border-custom px-4 py-2.5 flex items-center gap-3">
									<span class="text-sm text-text-dim">{thread.name}</span>
									{#if thread.priority}
										<span class="text-xs px-1.5 py-0.5 rounded {priorityStyle(thread.priority)}">{thread.priority.replaceAll('_', ' ')}</span>
									{/if}
									<span class="text-xs text-text-dim ml-auto font-mono">#{thread.current_counter}</span>
								</div>
							{/each}
						</div>
					{/if}

					<!-- Resolved threads (collapsed) -->
					{#if resolvedThreads.length}
						<details class="bg-surface rounded-[10px] border border-border-custom overflow-hidden">
							<summary class="flex items-center justify-between px-4 py-2.5 cursor-pointer
								hover:bg-bg-subtle transition-colors list-none text-sm text-text-dim">
								<span>Resolved ({resolvedThreads.length})</span>
								<svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
									<path d="M4 6l4 4 4-4" />
								</svg>
							</summary>
							<div class="border-t border-border-custom divide-y divide-border-custom">
								{#each resolvedThreads as thread}
									<div class="px-4 py-2 flex items-center gap-2 text-sm text-text-dim">
										<span class="line-through">{thread.name}</span>
										<span class="ml-auto"><Badge color="var(--color-success)" bg="var(--color-success-soft)">resolved</Badge></span>
									</div>
								{/each}
							</div>
						</details>
					{/if}
				</div>
			{/if}
		{/if}
	</div>
</div>
