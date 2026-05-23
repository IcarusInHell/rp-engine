<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { activeRP, activeBranch } from '$lib/stores/rp';
	import { addToast } from '$lib/stores/ui';
	import {
		listBranches, createBranch, switchBranch, archiveBranch,
		listCheckpoints, createCheckpoint, restoreCheckpoint,
	} from '$lib/api/branches';
	import { listExchanges } from '$lib/api/exchanges';
	import type { BranchInfo, CheckpointInfo, ExchangeDetail } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';
	import InputField from '$lib/components/ui/InputField.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import SelectField from '$lib/components/ui/SelectField.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Divider from '$lib/components/ui/Divider.svelte';
	import TabBar from '$lib/components/ui/TabBar.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';

	// ── Tab state ─────────────────────────────────────────────
	let activeTab = $state<'sessions' | 'branches'>('sessions');

	// ── Branch state ──────────────────────────────────────────
	let branches = $state<BranchInfo[]>([]);
	let checkpointMap = $state<Record<string, CheckpointInfo[]>>({});
	let expandedBranch = $state<string | null>(null);
	let loadingBranches = $state(false);

	// Create branch form
	let showCreate = $state(false);
	let newName = $state('');
	let newDesc = $state('');
	let newFrom = $state('');
	let newExchangeNum = $state<number | null>(null);
	let creating = $state(false);

	// Checkpoint form
	let checkpointTarget = $state<string | null>(null);
	let cpName = $state('');
	let cpDesc = $state('');
	let creatingCp = $state(false);

	// Restore confirmation
	let restoreTarget = $state<{ branch: string; checkpoint: string } | null>(null);

	// ── Exchange state ────────────────────────────────────────
	let exchanges = $state<ExchangeDetail[]>([]);
	let totalExchanges = $state(0);
	let loadingExchanges = $state(false);
	let exchangeOffset = $state(0);
	const PAGE_SIZE = 30;
	let expandedExchangeId = $state<number | null>(null);
	let searchQuery = $state('');
	let showAllBranches = $state(false);

	// ── Branch tree state ─────────────────────────────────────
	let selectedBranchNode = $state<BranchInfo | null>(null);
	let branchNodeExchanges = $state<ExchangeDetail[]>([]);
	let loadingNodeExchanges = $state(false);
	let collapsedBranches = $state<Record<string, boolean>>({});

	function toggleCollapse(name: string) {
		collapsedBranches = { ...collapsedBranches, [name]: !collapsedBranches[name] };
	}

	// ── Init ─────────────────────────────────────────────────
	onMount(async () => {
		await Promise.all([loadBranches(), loadExchanges(true)]);
	});

	// ── Data loaders ─────────────────────────────────────────
	async function loadBranches() {
		if (!$activeRP) return;
		loadingBranches = true;
		try {
			const res = await listBranches($activeRP.rp_folder);
			branches = res.branches;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load branches', 'error');
		} finally {
			loadingBranches = false;
		}
	}

	async function loadExchanges(reset = false) {
		loadingExchanges = true;
		const offset = reset ? 0 : exchangeOffset;
		try {
			const res = await listExchanges({ limit: PAGE_SIZE, offset });
			exchanges = reset ? res.exchanges : [...exchanges, ...res.exchanges];
			totalExchanges = res.total_count;
			exchangeOffset = offset + res.exchanges.length;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load exchanges', 'error');
		} finally {
			loadingExchanges = false;
		}
	}

	async function loadBranchNodeExchanges(branch: BranchInfo) {
		if (!$activeRP || !branch.branch_point_exchange) return;
		loadingNodeExchanges = true;
		try {
			// Load a few exchanges around the branch point
			const targetExchange = branch.branch_point_exchange;
			const startOffset = Math.max(0, targetExchange - 3);
			const res = await listExchanges({ limit: 5, offset: startOffset });
			branchNodeExchanges = res.exchanges;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load branch exchanges', 'error');
		} finally {
			loadingNodeExchanges = false;
		}
	}

	// ── Branch actions ────────────────────────────────────────
	async function handleSwitchBranch(branch: BranchInfo) {
		if (!$activeRP || branch.is_active) return;
		try {
			await switchBranch(branch.name, $activeRP.rp_folder);
			activeBranch.set(branch.name);
			branches = branches.map(b => ({ ...b, is_active: b.name === branch.name }));
			expandedBranch = null;
			await loadExchanges(true);
		} catch (e: any) {
			addToast(e.message ?? 'Failed to switch branch', 'error');
		}
	}

	async function handleExpandBranch(branchName: string) {
		expandedBranch = expandedBranch === branchName ? null : branchName;
		if (expandedBranch && !checkpointMap[branchName] && $activeRP) {
			try {
				checkpointMap[branchName] = await listCheckpoints(branchName, $activeRP.rp_folder);
				checkpointMap = { ...checkpointMap };
			} catch (e: any) {
				addToast(e.message ?? 'Failed to load checkpoints', 'error');
			}
		}
	}

	function startBranchFrom(exchangeNumber: number | null) {
		activeTab = 'branches';
		showCreate = true;
		newExchangeNum = exchangeNumber;
		newFrom = $activeBranch ?? '';
		newName = '';
		newDesc = '';
	}

	async function handleCreateBranch() {
		if (!newName.trim() || !$activeRP || creating) return;
		creating = true;
		try {
			const b = await createBranch({
				name: newName.trim(),
				rp_folder: $activeRP.rp_folder,
				description: newDesc.trim() || undefined,
				branch_from: newFrom || undefined,
				branch_point_exchange: newExchangeNum ?? undefined,
			});
			branches = [...branches, b];
			addToast(`Branch "${b.name}" created`, 'success');
			showCreate = false;
			newName = ''; newDesc = ''; newFrom = ''; newExchangeNum = null;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to create branch', 'error');
		} finally {
			creating = false;
		}
	}

	async function handleCreateCheckpoint(branchName: string) {
		if (!cpName.trim() || !$activeRP || creatingCp) return;
		creatingCp = true;
		try {
			const cp = await createCheckpoint(branchName, $activeRP.rp_folder, cpName.trim(), cpDesc.trim() || undefined);
			checkpointMap[branchName] = [...(checkpointMap[branchName] ?? []), cp];
			checkpointMap = { ...checkpointMap };
			addToast(`Checkpoint "${cp.name}" created`, 'success');
			checkpointTarget = null;
			cpName = ''; cpDesc = '';
		} catch (e: any) {
			addToast(e.message ?? 'Failed to create checkpoint', 'error');
		} finally {
			creatingCp = false;
		}
	}

	async function handleRestore() {
		if (!restoreTarget || !$activeRP) return;
		try {
			const res = await restoreCheckpoint(restoreTarget.branch, $activeRP.rp_folder, restoreTarget.checkpoint);
			addToast(`Restored to "${restoreTarget.checkpoint}" (exchange ${res.exchange_number})`, 'success');
			restoreTarget = null;
			await Promise.all([loadBranches(), loadExchanges(true)]);
		} catch (e: any) {
			addToast(e.message ?? 'Failed to restore', 'error');
		}
	}

	let showArchived = $state(false);

	async function handleArchiveBranch(branch: BranchInfo, archived: boolean) {
		if (!$activeRP) return;
		try {
			const updated = await archiveBranch(branch.name, $activeRP.rp_folder, archived);
			branches = branches.map(b => b.name === updated.name ? updated : b);
			if (selectedBranchNode?.name === updated.name) selectedBranchNode = updated;
			addToast(`Branch "${branch.name}" ${archived ? 'archived' : 'unarchived'}`, 'success');
		} catch (e: any) {
			addToast(e.message ?? 'Failed to archive branch', 'error');
		}
	}

	function handleBranchNodeClick(branch: BranchInfo) {
		selectedBranchNode = selectedBranchNode?.name === branch.name ? null : branch;
		if (selectedBranchNode) {
			loadBranchNodeExchanges(branch);
		}
	}

	// ── Helpers ───────────────────────────────────────────────
	let filteredExchanges = $derived(
		searchQuery.trim()
			? exchanges.filter(e =>
				e.user_message.toLowerCase().includes(searchQuery.toLowerCase()) ||
				e.assistant_response.toLowerCase().includes(searchQuery.toLowerCase())
			)
			: exchanges
	);

	// Build tree structure from branches (filter archived unless toggled)
	let visibleBranches = $derived(showArchived ? branches : branches.filter(b => !b.is_archived));
	let branchTree = $derived(buildBranchTree(visibleBranches));

	function buildBranchTree(branches: BranchInfo[]): { branch: BranchInfo; children: BranchInfo[]; depth: number }[] {
		const childMap = new Map<string, BranchInfo[]>();
		const roots: BranchInfo[] = [];

		for (const b of branches) {
			if (b.created_from) {
				const siblings = childMap.get(b.created_from) ?? [];
				siblings.push(b);
				childMap.set(b.created_from, siblings);
			} else {
				roots.push(b);
			}
		}

		const result: { branch: BranchInfo; children: BranchInfo[]; depth: number }[] = [];
		function walk(branch: BranchInfo, depth: number) {
			const children = childMap.get(branch.name) ?? [];
			result.push({ branch, children, depth });
			for (const child of children) {
				walk(child, depth + 1);
			}
		}
		for (const root of roots) {
			walk(root, 0);
		}
		return result;
	}

	function fmt(iso: string) {
		return new Date(iso).toLocaleString(undefined, {
			month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
		});
	}

	function truncate(text: string, len = 140) {
		return text.length > len ? text.slice(0, len) + '...' : text;
	}
</script>

<div class="space-y-3">

	<!-- ── Tab bar ──────────────────────────────────────────── -->
	<div class="flex items-center gap-3">
		<TabBar
			items={[{id: 'sessions', label: 'Sessions'}, {id: 'branches', label: 'Branches'}]}
			bind:active={activeTab}
		/>
		{#if activeTab === 'sessions'}
			<span class="text-xs text-text-dim">{totalExchanges} exchange{totalExchanges !== 1 ? 's' : ''}</span>
		{:else}
			<span class="text-xs text-text-dim">{branches.length} branch{branches.length !== 1 ? 'es' : ''}</span>
		{/if}
	</div>

	<!-- ════════════════════════════════════════════════════════ -->
	<!-- SESSIONS TAB                                            -->
	<!-- ════════════════════════════════════════════════════════ -->
	{#if activeTab === 'sessions'}
		<!-- Search + filter bar -->
		<div class="flex items-center gap-2">
			<InputField bind:value={searchQuery} placeholder="Search exchanges..." />
			{#if searchQuery}
				<span class="text-xs text-text-dim shrink-0">{filteredExchanges.length} result{filteredExchanges.length !== 1 ? 's' : ''}</span>
				<button class="text-xs text-text-dim hover:text-text transition-colors" onclick={() => (searchQuery = '')}>Clear</button>
			{/if}
			<button
				class="px-3 py-2 rounded-lg text-xs transition-all border
					{showAllBranches
						? 'bg-accent/15 text-accent border-accent/30'
						: 'bg-surface text-text-dim border-border-custom hover:text-text hover:border-border-custom'}"
				onclick={() => (showAllBranches = !showAllBranches)}
				title={showAllBranches ? 'Showing all branches' : 'Showing current branch only'}
			>
				All branches
			</button>
			<button
				class="px-3 py-2 rounded-lg text-xs transition-all border bg-surface text-text-dim border-border-custom hover:text-accent hover:border-accent/30"
				onclick={() => startBranchFrom(0)}
				title="Create a new branch from scratch"
			>
				+ New Branch
			</button>
		</div>

		<!-- Exchange list -->
		{#if loadingExchanges && exchanges.length === 0}
			<EmptyState message="Loading exchanges..." variant="loading" />
		{:else if filteredExchanges.length === 0}
			<EmptyState message={searchQuery ? 'No exchanges match your search.' : 'No exchanges yet on this branch.'} />
		{:else}
			<div class="flex flex-col gap-1.5">
				{#each filteredExchanges as exchange}
					<div
						class="bg-surface rounded-[10px] border border-border-custom overflow-hidden text-left w-full transition-all hover:shadow-sm hover:border-accent/25 cursor-pointer"
						role="button"
						tabindex="0"
						onclick={() => (expandedExchangeId = expandedExchangeId === exchange.id ? null : exchange.id)}
						onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); expandedExchangeId = expandedExchangeId === exchange.id ? null : exchange.id; } }}
					>
						<div class="px-4 py-3">
							<div class="flex justify-between mb-1.5">
								<span class="text-xs text-text-dim/60">#{exchange.exchange_number}</span>
								<Badge>{$activeBranch ?? 'main'}</Badge>
							</div>
							<p class="text-[13px] font-medium m-0 mb-1 text-text">{truncate(exchange.user_message, 120)}</p>
							<p class="text-xs text-text-dim m-0 font-serif italic whitespace-nowrap overflow-hidden text-ellipsis">{truncate(exchange.assistant_response, 140)}</p>
						</div>

						{#if expandedExchangeId === exchange.id}
							<div class="border-t border-border-custom divide-y divide-border-custom">
								<div class="px-4 py-3 space-y-1">
									<SectionLabel>User</SectionLabel>
									<p class="text-sm text-text whitespace-pre-wrap">{exchange.user_message}</p>
								</div>
								<div class="px-4 py-3 space-y-1">
									<SectionLabel>Assistant</SectionLabel>
									<p class="text-sm text-text whitespace-pre-wrap font-serif">{exchange.assistant_response}</p>
								</div>
								{#if exchange.in_story_timestamp || exchange.analysis_status}
									<div class="px-4 py-2 flex items-center gap-3 text-xs text-text-dim bg-surface2/50">
										{#if exchange.in_story_timestamp}
											<span>Story time: {exchange.in_story_timestamp}</span>
										{/if}
										{#if exchange.analysis_status}
											<span class="ml-auto">Analysis: {exchange.analysis_status}</span>
										{/if}
									</div>
								{/if}
								<div class="px-4 py-2 border-t border-border-custom bg-surface2/30 flex items-center gap-4">
									<button
										class="text-xs text-accent hover:text-accent/80 transition-colors"
										onclick={(e: MouseEvent) => { e.stopPropagation(); startBranchFrom(exchange.exchange_number); }}
									>Branch from here</button>
									<button
										class="text-xs text-accent hover:text-accent/80 transition-colors"
										onclick={(e: MouseEvent) => { e.stopPropagation(); goto(`/${$page.params.rp}/sessions/${exchange.session_id}`); }}
									>View Timeline</button>
								</div>
							</div>
						{/if}
					</div>
				{/each}
			</div>

			{#if !searchQuery && exchangeOffset < totalExchanges}
				<button
					class="w-full py-2 text-sm text-text-dim hover:text-text hover:bg-surface2/50 rounded-[10px]
						border border-border-custom transition-colors disabled:opacity-50"
					onclick={() => loadExchanges(false)}
					disabled={loadingExchanges}
				>
					{loadingExchanges ? 'Loading...' : `Load more (${totalExchanges - exchangeOffset} remaining)`}
				</button>
			{/if}
		{/if}

	<!-- ════════════════════════════════════════════════════════ -->
	<!-- BRANCHES TAB                                            -->
	<!-- ════════════════════════════════════════════════════════ -->
	{:else}
		<div class="flex gap-3">
			<!-- Branch tree card (left, w-380) -->
			<div class="w-[380px] shrink-0 bg-surface border border-border-custom rounded-[10px] overflow-hidden self-start">
				<div class="px-3.5 py-2.5 border-b border-border-custom/60 flex items-center justify-between">
					<SectionLabel>Branch Tree</SectionLabel>
					<div class="flex items-center gap-2">
						{#if branches.some(b => b.is_archived)}
							<button
								class="text-[11px] transition-colors {showArchived ? 'text-accent' : 'text-text-dim hover:text-text'}"
								onclick={() => (showArchived = !showArchived)}
							>{showArchived ? 'Hide archived' : 'Show archived'}</button>
						{/if}
						<button
							class="text-xs text-accent hover:text-accent-hover transition-colors"
							onclick={() => (showCreate = !showCreate)}
						>
							{showCreate ? 'Cancel' : '+ New Branch'}
						</button>
					</div>
				</div>

				<!-- Create branch form -->
				{#if showCreate}
					<div class="px-3.5 py-3 border-b border-border-custom/60 space-y-2">
						<InputField bind:value={newName} placeholder="Branch name" />
						<InputField bind:value={newDesc} placeholder="Description (optional)" />
						<SelectField bind:value={newFrom} placeholder="Branch from: current"
							options={branches.map(b => ({value: b.name, label: b.name}))} />
						{#if newExchangeNum != null && newExchangeNum > 0}
							<div class="flex items-center gap-2 text-xs">
								<span class="text-text-dim">Branch point:</span>
								<span class="text-text font-medium">Exchange #{newExchangeNum}</span>
								<button
									class="text-text-dim hover:text-text transition-colors"
									onclick={() => (newExchangeNum = null)}
									title="Branch from latest instead"
								>&times;</button>
							</div>
						{:else if newExchangeNum === 0}
							<div class="flex items-center gap-2 text-xs">
								<span class="text-text-dim">Branch point:</span>
								<span class="text-accent font-medium">Fresh start (new root, no parent)</span>
								<button
									class="text-text-dim hover:text-text transition-colors"
									onclick={() => (newExchangeNum = null)}
									title="Branch from latest instead"
								>&times;</button>
							</div>
						{:else}
							<div class="flex items-center gap-2 text-xs text-text-dim">
								<span>Branch point: latest exchange</span>
								<button
									class="text-accent hover:text-accent/80 transition-colors"
									onclick={() => (newExchangeNum = 0)}
									title="Start with no inherited exchanges"
								>or start fresh</button>
							</div>
						{/if}
						<Btn primary small onclick={handleCreateBranch} disabled={creating || !newName.trim()}>{creating ? 'Creating...' : 'Create Branch'}</Btn>
					</div>
				{/if}

				<!-- Branch tree nodes -->
				<div class="p-2">
					{#if loadingBranches}
						<div class="p-6 text-center text-sm text-text-dim">Loading branches...</div>
					{:else if branches.length === 0}
						<div class="p-6 text-center text-sm text-text-dim">No branches yet.</div>
					{:else}
						{#each branchTree as { branch, children, depth }}
							<div style="margin-left: {depth * 20}px">
								<div class="flex items-center gap-0">
									<!-- Collapse chevron -->
									{#if children.length > 0}
										<button
											class="w-5 h-5 flex items-center justify-center text-text-dim/60 text-[10px] shrink-0 transition-transform border-none bg-transparent cursor-pointer"
											style="transform: {collapsedBranches[branch.name] ? 'rotate(-90deg)' : 'rotate(0)'}"
											onclick={(e: MouseEvent) => { e.stopPropagation(); toggleCollapse(branch.name); }}
										>&#9662;</button>
									{:else}
										<div class="w-5"></div>
									{/if}

									<!-- Branch row -->
									<button
										class="flex-1 flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all mb-1 border
											{selectedBranchNode?.name === branch.name
												? 'bg-accent/10 border-accent/25'
												: 'bg-transparent border-transparent hover:bg-surface2/50'}"
										onclick={() => handleBranchNodeClick(branch)}
									>
										<!-- Warm dot + line for child branches -->
										{#if depth > 0}
											<div class="flex items-center gap-1 mr-0.5">
												<div class="w-3 h-px" style="background: var(--color-border-warm, #d4c9b8)"></div>
												<div class="w-[5px] h-[5px] rounded-full shrink-0" style="background: var(--color-warm, #c4956a)"></div>
											</div>
										{/if}

										<!-- Name + description -->
										<div class="flex-1 min-w-0">
											<div class="flex items-center gap-1.5">
												<span class="font-serif text-[13px] font-medium truncate
													{selectedBranchNode?.name === branch.name ? 'text-accent' : 'text-text'}">
													{branch.name}
												</span>
												{#if branch.is_active}
													<Badge color="var(--color-success)" bg="var(--color-success-soft)">active</Badge>
												{/if}
											</div>
											{#if branch.description}
												<span class="text-[11px] text-text-dim truncate block">{branch.description}</span>
											{/if}
										</div>

										<!-- Exchange count -->
										<div class="text-right shrink-0">
											<div class="text-xs font-medium">{branch.exchange_count}</div>
											<div class="text-[10px] text-text-dim/60">exchanges</div>
										</div>
									</button>
								</div>

								<!-- Children with warm connector line -->
								{#if children.length > 0 && !collapsedBranches[branch.name]}
									<div class="ml-7" style="border-left: 1.5px solid var(--color-warm-border)">
										<!-- children rendered by the outer each loop -->
									</div>
								{/if}
							</div>

							<!-- Checkpoints (expanded inline) -->
							{#if expandedBranch === branch.name}
								<div class="rounded-lg border px-4 py-3 space-y-1.5 ml-6 mb-1"
									style="margin-left: {depth * 20 + 24}px; border-color: var(--color-gold-border); background: var(--color-gold-soft)">
									<SectionLabel>Checkpoints</SectionLabel>
									{#if checkpointMap[branch.name]?.length > 0}
										{#each checkpointMap[branch.name] as cp}
											<div class="flex items-center gap-2 text-xs py-1 group/cp">
												<span style="color: var(--color-warm-deep)">&#9670;</span>
												<span class="text-text flex-1 truncate" title={cp.description ?? cp.name}>{cp.name}</span>
												<span class="text-text-dim shrink-0 font-mono">#{cp.exchange_number}</span>
												<span class="text-text-dim shrink-0">{fmt(cp.created_at)}</span>
												<button
													class="text-warning hover:text-warning/80 opacity-0 group-hover/cp:opacity-100 transition-all shrink-0"
													onclick={(e: MouseEvent) => { e.stopPropagation(); restoreTarget = { branch: branch.name, checkpoint: cp.name }; }}
													title="Restore to this checkpoint"
												>Restore</button>
											</div>
										{/each}
									{:else}
										<p class="text-xs text-text-dim/60">No checkpoints yet</p>
									{/if}

									{#if checkpointTarget === branch.name}
										<div class="pt-1 space-y-1.5">
											<input type="text" bind:value={cpName} placeholder="Checkpoint name"
												class="w-full bg-surface border border-border-custom rounded-lg px-2 py-1.5 text-xs text-text
													focus:outline-none focus:ring-1 focus:ring-accent" />
											<input type="text" bind:value={cpDesc} placeholder="Description (optional)"
												class="w-full bg-surface border border-border-custom rounded-lg px-2 py-1.5 text-xs text-text
													focus:outline-none focus:ring-1 focus:ring-accent" />
											<div class="flex gap-1.5">
												<button
													class="flex-1 bg-accent text-text-on-accent text-xs py-1.5 rounded-lg disabled:opacity-50"
													onclick={(e: MouseEvent) => { e.stopPropagation(); handleCreateCheckpoint(branch.name); }}
													disabled={creatingCp || !cpName.trim()}
												>{creatingCp ? '...' : 'Save'}</button>
												<button
													class="text-xs text-text-dim hover:text-text px-2 py-1.5"
													onclick={(e: MouseEvent) => { e.stopPropagation(); checkpointTarget = null; cpName = ''; cpDesc = ''; }}
												>Cancel</button>
											</div>
										</div>
									{:else}
										<button
											class="text-xs text-accent hover:text-accent-hover transition-colors mt-1"
											onclick={(e: MouseEvent) => { e.stopPropagation(); checkpointTarget = branch.name; cpName = ''; cpDesc = ''; }}
										>+ Add checkpoint</button>
									{/if}
								</div>
							{/if}
						{/each}
					{/if}
				</div>
			</div>

			<!-- Branch detail card (right, flex-1) -->
			<div class="flex-1 min-w-0">
				{#if selectedBranchNode}
					{@const branch = selectedBranchNode}
					<div class="bg-surface border border-border-custom rounded-[10px] overflow-hidden">
						<div class="p-4 px-5">
							<!-- Header -->
							<div class="mb-2.5">
								<PageHeader title={branch.name} size="sm">
									{#snippet badges()}
										{#if branch.is_active}
											<Badge color="var(--color-success)" bg="var(--color-success-soft)">active</Badge>
										{/if}
										{#if branch.is_archived}
											<Badge color="var(--color-text-dim)" bg="var(--color-surface2)">archived</Badge>
										{/if}
									{/snippet}
								</PageHeader>
							</div>

							{#if selectedBranchNode.description}
								<p class="text-[13px] text-text-dim mb-3">{selectedBranchNode.description}</p>
							{/if}

							<!-- Info row -->
							<div class="flex gap-4 mb-3 text-xs">
								<div>
									<span class="text-text-dim">Exchanges: </span>
									<strong>{selectedBranchNode.exchange_count}</strong>
								</div>
								{#if selectedBranchNode.created_from}
									<div>
										<span class="text-text-dim">Forked: </span>
										<span class="text-accent font-medium">{selectedBranchNode.created_from}</span>
										{#if selectedBranchNode.branch_point_exchange}
											<span class="text-text-dim/60"> @ #{selectedBranchNode.branch_point_exchange}</span>
										{/if}
									</div>
								{/if}
								{#if selectedBranchNode.created_at}
									<div>
										<span class="text-text-dim">Created: </span>
										<span>{fmt(selectedBranchNode.created_at)}</span>
									</div>
								{/if}
							</div>

							<!-- Checkpoints with gold styling -->
							{#if checkpointMap[selectedBranchNode.name]?.length > 0}
								<Divider />
								<div class="mt-2 mb-1"><SectionLabel>Checkpoints</SectionLabel></div>
								<div class="flex gap-1.5 flex-wrap mt-2">
									{#each checkpointMap[selectedBranchNode.name] as cp}
										<div class="px-3 py-1.5 rounded-lg text-xs border"
											style="border-color: var(--color-gold-border); background: var(--color-gold-soft); color: var(--color-warm-deep)"
										>&#9670; {cp.name}</div>
									{/each}
								</div>
							{/if}

							<!-- Actions -->
							<div class="flex gap-2 mt-4">
								{#if !selectedBranchNode.is_active}
									<Btn primary small onclick={() => selectedBranchNode && handleSwitchBranch(selectedBranchNode)}>Switch to Branch</Btn>
								{/if}
								<Btn small onclick={() => selectedBranchNode && handleExpandBranch(selectedBranchNode.name)}>
									{expandedBranch === selectedBranchNode.name ? 'Hide Checkpoints' : 'View Checkpoints'}
								</Btn>
								{#if !selectedBranchNode.is_active}
									<Btn small onclick={() => selectedBranchNode && handleArchiveBranch(selectedBranchNode, !selectedBranchNode.is_archived)}>
										{selectedBranchNode.is_archived ? "Unarchive" : "Archive"}
									</Btn>
								{/if}
								<Btn small onclick={() => (selectedBranchNode = null)}>Close</Btn>
							</div>
						</div>
					</div>
				{:else}
					<div class="bg-surface border border-border-custom rounded-[10px] flex items-center justify-center" style="min-height: 200px">
						<span class="text-text-dim/60 text-[13px] font-serif italic">Select a branch</span>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<!-- ── Restore confirmation overlay ─────────────────────── -->
{#if restoreTarget}
	<div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
		<div class="bg-surface border border-border-custom rounded-lg p-6 max-w-sm w-full mx-4 space-y-4">
			<h3 class="text-base font-semibold text-text">Restore checkpoint?</h3>
			<p class="text-sm text-text-dim">
				This will restore branch <span class="text-text font-medium">"{restoreTarget.branch}"</span>
				to checkpoint <span class="text-text font-medium">"{restoreTarget.checkpoint}"</span>.
				Exchanges after that point will be rewound.
			</p>
			<div class="flex gap-3">
				<button
					class="flex-1 bg-error hover:bg-error/80 text-text-on-accent text-sm font-medium py-2 rounded-lg transition-colors"
					onclick={handleRestore}
				>Restore</button>
				<button
					class="flex-1 bg-bg-subtle hover:bg-bg-subtle/80 text-text text-sm py-2 rounded-lg border border-border-custom transition-colors"
					onclick={() => (restoreTarget = null)}
				>Cancel</button>
			</div>
		</div>
	</div>
{/if}
