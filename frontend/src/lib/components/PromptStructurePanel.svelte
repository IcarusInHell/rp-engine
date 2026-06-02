<script lang="ts">
	import { onMount } from 'svelte';
	import { getPromptOrder, updatePromptOrder, resetPromptOrder } from '$lib/api/prompt';
	import { getGuidelines, updateGuidelines } from '$lib/api/context';
	import { addToast } from '$lib/stores/ui';
	import type { PromptOrderResponse, GuidelinesResponse } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import Divider from '$lib/components/ui/Divider.svelte';

	interface Props {
		rpFolder: string;
		/** Whether the global injection feature is enabled (from the preview). */
		injectionEnabled?: boolean;
		/** Called after any save so the parent can refresh its prompt preview. */
		onSaved?: () => void;
	}

	let { rpFolder, injectionEnabled = false, onSaved }: Props = $props();

	// ── Section order ──
	let orderInfo = $state<PromptOrderResponse | null>(null);
	let order = $state<string[]>([]);
	let savingOrder = $state(false);

	// Sections known to the assembler but not currently in the order (removable
	// → addable back). Derived from the known set minus the active order.
	let removed = $derived(
		(orderInfo?.known_sections ?? []).filter((s) => !order.includes(s)),
	);

	// ── Injection depths (power-user) ──
	let depths = $state<Record<string, number>>({});
	let savingDepths = $state(false);

	let loading = $state(true);

	onMount(load);

	async function load() {
		loading = true;
		try {
			const [o, g] = await Promise.all([getPromptOrder(rpFolder), getGuidelines(rpFolder)]);
			orderInfo = o;
			order = [...o.order];
			depths = { ...(g.injection_depths ?? {}) };
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load prompt structure', 'error');
		} finally {
			loading = false;
		}
	}

	function move(i: number, dir: -1 | 1) {
		const j = i + dir;
		if (j < 0 || j >= order.length) return;
		const next = [...order];
		[next[i], next[j]] = [next[j], next[i]];
		order = next;
	}

	function remove(section: string) {
		order = order.filter((s) => s !== section);
	}

	function add(section: string) {
		order = [...order, section];
	}

	async function saveOrder() {
		savingOrder = true;
		try {
			orderInfo = await updatePromptOrder(rpFolder, order);
			order = [...orderInfo.order];
			addToast('Section order saved', 'success');
			onSaved?.();
		} catch (e: any) {
			addToast(e.message ?? 'Failed to save order', 'error');
		} finally {
			savingOrder = false;
		}
	}

	async function reset() {
		savingOrder = true;
		try {
			orderInfo = await resetPromptOrder(rpFolder);
			order = [...orderInfo.order];
			addToast('Reset to default order', 'success');
			onSaved?.();
		} catch (e: any) {
			addToast(e.message ?? 'Failed to reset', 'error');
		} finally {
			savingOrder = false;
		}
	}

	async function saveDepths() {
		savingDepths = true;
		try {
			// Drop empty/zero entries so the map stays minimal (0 = system message,
			// the default for any unlisted section).
			const cleaned: Record<string, number> = {};
			for (const [k, v] of Object.entries(depths)) {
				if (Number.isFinite(v) && v > 0) cleaned[k] = Math.trunc(v);
			}
			await updateGuidelines(rpFolder, { injection_depths: cleaned } as Partial<GuidelinesResponse>);
			depths = cleaned;
			addToast('Injection depths saved', 'success');
			onSaved?.();
		} catch (e: any) {
			addToast(e.message ?? 'Failed to save depths', 'error');
		} finally {
			savingDepths = false;
		}
	}

	function depthFor(section: string): number {
		return depths[section] ?? 0;
	}

	function setDepth(section: string, value: number) {
		const v = Math.max(0, Math.trunc(value || 0));
		depths = { ...depths, [section]: v };
	}
</script>

{#if loading}
	<div class="text-xs text-text-dim">Loading prompt structure...</div>
{:else}
	<div class="space-y-4">
		<!-- ── Section order ── -->
		<div>
			<div class="flex items-center justify-between mb-2">
				<SectionLabel>Section Order</SectionLabel>
				{#if orderInfo && !orderInfo.is_custom}
					<span class="text-[10px] text-text-dim/60">default</span>
				{:else}
					<span class="text-[10px] text-accent">custom</span>
				{/if}
			</div>
			<p class="text-[11px] text-text-dim/60 mb-2">
				Order of depth-0 prompt sections. Removed sections are dropped from the prompt.
			</p>
			<ul class="space-y-1">
				{#each order as section, i (section)}
					<li class="flex items-center gap-1.5 bg-surface2 border border-border-custom rounded-md px-2 py-1">
						<span class="flex-1 text-xs font-mono text-text truncate">{section}</span>
						<button
							class="text-text-dim hover:text-text disabled:opacity-30 text-xs px-1"
							onclick={() => move(i, -1)}
							disabled={i === 0}
							aria-label="Move {section} up"
						>↑</button>
						<button
							class="text-text-dim hover:text-text disabled:opacity-30 text-xs px-1"
							onclick={() => move(i, 1)}
							disabled={i === order.length - 1}
							aria-label="Move {section} down"
						>↓</button>
						<button
							class="text-text-dim hover:text-error text-xs px-1"
							onclick={() => remove(section)}
							aria-label="Remove {section}"
						>✕</button>
					</li>
				{/each}
			</ul>

			{#if removed.length}
				<div class="mt-2">
					<span class="block text-[11px] text-text-dim/60 mb-1">Add back:</span>
					<div class="flex flex-wrap gap-1">
						{#each removed as section}
							<button
								class="text-[11px] font-mono bg-surface2 hover:bg-accent/10 hover:text-accent border border-border-custom rounded px-1.5 py-0.5 text-text-dim"
								onclick={() => add(section)}
							>+ {section}</button>
						{/each}
					</div>
				</div>
			{/if}

			<div class="flex gap-2 mt-3">
				<Btn primary small onclick={saveOrder} disabled={savingOrder}>
					{savingOrder ? 'Saving...' : 'Save Order'}
				</Btn>
				<Btn small onclick={reset} disabled={savingOrder}>Reset to Default</Btn>
			</div>
		</div>

		<Divider />

		<!-- ── Injection depths (power-user) ── -->
		<div>
			<SectionLabel>Injection Depths</SectionLabel>
			<p class="text-[11px] text-text-dim/60 mt-1 mb-2">
				Depth N injects a section N exchange-pairs from the bottom (0 = system
				message). {#if !injectionEnabled}<span class="text-warning">Injection is disabled globally — these take effect only when enabled in Settings.</span>{/if}
			</p>
			<ul class="space-y-1">
				{#each (orderInfo?.known_sections ?? []) as section}
					<li class="flex items-center gap-2">
						<span class="flex-1 text-xs font-mono text-text-dim truncate">{section}</span>
						<input
							type="number"
							min="0"
							class="w-16 bg-surface2 border border-border-custom rounded-md px-2 py-1 text-xs text-text focus:outline-none focus:ring-1 focus:ring-accent"
							value={depthFor(section)}
							oninput={(e) => setDepth(section, e.currentTarget.valueAsNumber)}
						/>
					</li>
				{/each}
			</ul>
			<div class="mt-3">
				<Btn primary small onclick={saveDepths} disabled={savingDepths}>
					{savingDepths ? 'Saving...' : 'Save Depths'}
				</Btn>
			</div>
		</div>
	</div>
{/if}
