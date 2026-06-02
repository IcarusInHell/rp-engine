<script lang="ts">
	import { onMount } from 'svelte';
	import { getLorebooks, setActiveLorebooks, reindexLorebooks } from '$lib/api/lorebook';
	import { addToast } from '$lib/stores/ui';
	import type { LorebookListResponse, LorebookFile } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';

	interface Props {
		rpFolder: string;
	}

	let { rpFolder }: Props = $props();

	let info = $state<LorebookListResponse | null>(null);
	let loading = $state(true);
	let saving = $state(false);
	let reindexing = $state(false);

	// Active stems for per-RP files, edited locally then saved as an explicit list.
	let activeStems = $state<Set<string>>(new Set());
	// True when the RP loaded with NO explicit active-set (absent → all active,
	// "file-drop-and-go"). Saving an explicit list pins it, so newly-dropped files
	// would stop auto-activating — we avoid that on a no-op save below.
	let wasImplicitAll = $state(false);

	let rpFiles = $derived((info?.files ?? []).filter((f) => f.scope === 'rp'));
	let globalFiles = $derived((info?.files ?? []).filter((f) => f.scope === 'global'));

	onMount(load);

	async function load() {
		loading = true;
		try {
			info = await getLorebooks(rpFolder);
			wasImplicitAll = info.active_set === null;
			activeStems = new Set(info.files.filter((f) => f.scope === 'rp' && f.active).map((f) => f.stem));
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load lorebooks', 'error');
		} finally {
			loading = false;
		}
	}

	function toggle(file: LorebookFile) {
		const next = new Set(activeStems);
		if (next.has(file.stem)) next.delete(file.stem);
		else next.add(file.stem);
		activeStems = next;
	}

	async function saveActive() {
		// No-op guard: if the RP is in implicit file-drop-and-go mode (no explicit
		// list) and every per-RP file is still active, skip the write — pinning an
		// explicit list here would stop newly-dropped files from auto-activating.
		if (wasImplicitAll && rpFiles.every((f) => activeStems.has(f.stem))) {
			addToast('Already file-drop-and-go (all per-RP files active) — nothing to pin', 'info');
			return;
		}
		saving = true;
		try {
			info = await setActiveLorebooks(rpFolder, [...activeStems]);
			wasImplicitAll = info.active_set === null;
			activeStems = new Set(info.files.filter((f) => f.scope === 'rp' && f.active).map((f) => f.stem));
			addToast('Active lorebooks saved', 'success');
		} catch (e: any) {
			addToast(e.message ?? 'Failed to save active set', 'error');
		} finally {
			saving = false;
		}
	}

	async function reindex() {
		reindexing = true;
		try {
			const r = await reindexLorebooks(rpFolder);
			addToast(`Reindexed: ${r.rp_entries} RP + ${r.global_entries} global entries`, 'success');
			await load();
		} catch (e: any) {
			addToast(e.message ?? 'Reindex failed', 'error');
		} finally {
			reindexing = false;
		}
	}
</script>

{#if loading}
	<div class="text-xs text-text-dim">Loading lorebooks...</div>
{:else if info}
	<div class="space-y-4">
		{#if !info.enabled}
			<p class="text-[11px] text-warning">
				Lorebook is disabled globally (<code>context.lorebook_enabled</code>). Files below are managed but won't inject until enabled.
			</p>
		{/if}

		<!-- Per-RP files -->
		<div>
			<div class="flex items-center justify-between mb-2">
				<SectionLabel>Per-RP Lorebooks</SectionLabel>
				<span class="text-[10px] text-text-dim/60">drop files in this RP's Lorebooks/ folder</span>
			</div>
			{#if rpFiles.length === 0}
				<p class="text-[11px] text-text-dim/60">No per-RP lorebook files found.</p>
			{:else}
				<ul class="space-y-1">
					{#each rpFiles as file (file.stem)}
						<li class="flex items-center gap-2 bg-surface2 border border-border-custom rounded-md px-2 py-1">
							<input
								type="checkbox"
								class="accent-accent"
								checked={activeStems.has(file.stem)}
								onchange={() => toggle(file)}
								aria-label="Activate {file.stem}"
							/>
							<span class="flex-1 text-xs font-mono text-text truncate">{file.stem}</span>
						</li>
					{/each}
				</ul>
				<p class="text-[10px] text-text-dim/50 mt-1">
					All unchecked = no per-RP files active. (An empty active-set is distinct from "all active".)
				</p>
				<div class="mt-2">
					<Btn primary small onclick={saveActive} disabled={saving}>
						{saving ? 'Saving...' : 'Save Active Set'}
					</Btn>
				</div>
			{/if}
		</div>

		<!-- Global library (read-only here) -->
		{#if globalFiles.length}
			<div>
				<SectionLabel>Global Library</SectionLabel>
				<p class="text-[11px] text-text-dim/60 mt-1 mb-2">Always available to every RP.</p>
				<div class="flex flex-wrap gap-1">
					{#each globalFiles as file (file.stem)}
						<span class="text-[11px] font-mono bg-surface2 border border-border-custom rounded px-1.5 py-0.5 text-text-dim">{file.stem}</span>
					{/each}
				</div>
			</div>
		{/if}

		<div class="border-t border-border-custom pt-3">
			<Btn small onclick={reindex} disabled={reindexing}>
				{reindexing ? 'Reindexing...' : 'Reindex Lorebooks'}
			</Btn>
		</div>
	</div>
{/if}
