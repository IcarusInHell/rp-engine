<script lang="ts">
	import { Dialog } from 'bits-ui';
	import { setNarratorNote, clearNarratorNote } from '$lib/api/sessions';
	import { addToast } from '$lib/stores/ui';
	import type { SessionResponse } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';

	interface Props {
		session: SessionResponse;
		/** Called with the updated session after a save/clear so the parent can sync. */
		onUpdated?: (session: SessionResponse) => void;
	}

	let { session, onUpdated }: Props = $props();

	let open = $state(false);
	let note = $state('');
	let depth = $state(2);
	let saving = $state(false);

	// Seed the form from the session whenever the dialog opens.
	$effect(() => {
		if (open) {
			note = session.narrator_note ?? '';
			depth = session.narrator_note_depth ?? 2;
		}
	});

	let hasNote = $derived(!!session.narrator_note);

	async function save() {
		saving = true;
		try {
			const updated = await setNarratorNote(session.id, note, depth);
			onUpdated?.(updated);
			addToast('Narrator note saved', 'success');
			open = false;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to save note', 'error');
		} finally {
			saving = false;
		}
	}

	async function clear() {
		saving = true;
		try {
			const updated = await clearNarratorNote(session.id);
			onUpdated?.(updated);
			note = '';
			addToast('Narrator note cleared', 'success');
			open = false;
		} catch (e: any) {
			addToast(e.message ?? 'Failed to clear note', 'error');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Trigger
		class="text-xs px-2 py-1 rounded border transition-colors {hasNote
			? 'bg-accent/10 text-accent border-accent/30'
			: 'text-text-dim border-border-custom hover:bg-surface2/50'}"
		title="Session-persistent narrator's note (GM steering)"
	>
		{hasNote ? '● Narrator Note' : 'Narrator Note'}
	</Dialog.Trigger>
	<Dialog.Portal>
		<Dialog.Overlay class="fixed inset-0 bg-black/50 z-40" />
		<Dialog.Content
			class="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-[460px] max-w-[90vw] bg-surface border border-border-custom rounded-lg p-5 shadow-xl"
		>
			<Dialog.Title class="text-base font-semibold text-text">Narrator's Note</Dialog.Title>
			<Dialog.Description class="text-xs text-text-dim mt-1">
				Persistent GM steering injected every turn until cleared. Stacks with per-turn direction.
			</Dialog.Description>

			<div class="mt-4 space-y-3">
				<textarea
					bind:value={note}
					rows="4"
					class="w-full bg-surface2 border border-border-custom rounded-md px-3 py-2 text-sm text-text placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent resize-none"
					placeholder="e.g. Focus on the tension between Dante and Lilith; keep descriptions atmospheric."
				></textarea>

				<div class="flex items-center gap-2">
					<label for="narrator-depth" class="text-xs text-text-dim">Injection depth</label>
					<input
						id="narrator-depth"
						type="number"
						min="1"
						bind:value={depth}
						class="w-16 bg-surface2 border border-border-custom rounded-md px-2 py-1 text-xs text-text focus:outline-none focus:ring-1 focus:ring-accent"
					/>
					<span class="text-[11px] text-text-dim/60">exchange-pairs from the bottom (≥ 1)</span>
				</div>
			</div>

			<div class="flex items-center justify-between mt-5">
				<Btn small onclick={clear} disabled={saving || !hasNote}>Clear</Btn>
				<div class="flex gap-2">
					<Dialog.Close class="text-xs px-3 py-1.5 rounded-md border border-border-custom text-text-dim hover:bg-surface2/50">
						Cancel
					</Dialog.Close>
					<Btn primary small onclick={save} disabled={saving || !note.trim()}>
						{saving ? 'Saving...' : 'Save'}
					</Btn>
				</div>
			</div>
		</Dialog.Content>
	</Dialog.Portal>
</Dialog.Root>
