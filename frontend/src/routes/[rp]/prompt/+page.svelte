<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { getGuidelines, updateGuidelines } from '$lib/api/context';
	import { previewPrompt } from '$lib/api/chat';
	import { addToast } from '$lib/stores/ui';
	import type { GuidelinesResponse, PromptPreview } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';
	import SelectField from '$lib/components/ui/SelectField.svelte';
	import InputField from '$lib/components/ui/InputField.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import Divider from '$lib/components/ui/Divider.svelte';
	import Toggle from '$lib/components/ui/Toggle.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import PromptStructurePanel from '$lib/components/PromptStructurePanel.svelte';
	import LorebookPanel from '$lib/components/LorebookPanel.svelte';

	let rpFolder = $derived($page.params.rp ?? '');

	// ── Form state ──
	let povMode = $state('single');
	let povCharacter = $state('');
	let dualCharacters = $state('');
	let narrativeVoice = $state('third');
	let tense = $state('past');
	let scenePacing = $state('moderate');
	let responseLength = $state('medium');
	let tone = $state('');
	let sensitiveThemes = $state('');
	let hardLimits = $state('');
	let includeWritingPrinciples = $state(true);
	let includeNpcFramework = $state(true);
	let includeOutputFormat = $state(true);
	let guidelinesBody = $state('');

	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);

	// ── Preview state ──
	let showPreview = $state(false);
	let preview = $state<PromptPreview | null>(null);
	let previewLoading = $state(false);

	onMount(async () => {
		try {
			const g = await getGuidelines(rpFolder);
			populateForm(g);
		} catch (e: any) {
			error = e.message ?? 'Failed to load guidelines';
		} finally {
			loading = false;
		}
	});

	function populateForm(g: GuidelinesResponse) {
		povMode = g.pov_mode ?? 'single';
		povCharacter = g.pov_character ?? '';
		dualCharacters = Array.isArray(g.dual_characters) ? g.dual_characters.join(', ') : '';
		narrativeVoice = g.narrative_voice ?? 'third';
		tense = g.tense ?? 'past';
		scenePacing = g.scene_pacing ?? 'moderate';
		responseLength = g.response_length ?? 'medium';
		tone = Array.isArray(g.tone) ? g.tone.join(', ') : (g.tone ?? '');
		sensitiveThemes = Array.isArray(g.sensitive_themes) ? g.sensitive_themes.join(', ') : '';
		hardLimits = Array.isArray(g.hard_limits) ? g.hard_limits.join(', ') : (g.hard_limits ?? '');
		includeWritingPrinciples = g.include_writing_principles ?? true;
		includeNpcFramework = g.include_npc_framework ?? true;
		includeOutputFormat = g.include_output_format ?? true;
		guidelinesBody = g.body ?? '';
	}

	async function handleSave() {
		saving = true;
		try {
			const body: Record<string, unknown> = {
				pov_mode: povMode,
				pov_character: povCharacter || null,
				dual_characters: dualCharacters ? dualCharacters.split(',').map(s => s.trim()).filter(Boolean) : [],
				narrative_voice: narrativeVoice,
				tense,
				scene_pacing: scenePacing,
				response_length: responseLength,
				tone: tone ? tone.split(',').map(s => s.trim()).filter(Boolean) : [],
				sensitive_themes: sensitiveThemes ? sensitiveThemes.split(',').map(s => s.trim()).filter(Boolean) : [],
				hard_limits: hardLimits || null,
				include_writing_principles: includeWritingPrinciples,
				include_npc_framework: includeNpcFramework,
				include_output_format: includeOutputFormat,
				body: guidelinesBody,
			};
			const updated = await updateGuidelines(rpFolder, body as Partial<GuidelinesResponse>);
			populateForm(updated);
			addToast('Guidelines saved', 'success');
			// Invalidate preview
			preview = null;
		} catch (e: any) {
			addToast(e.message ?? 'Save failed', 'error');
		} finally {
			saving = false;
		}
	}

	async function loadPreview() {
		previewLoading = true;
		try {
			preview = await previewPrompt(rpFolder);
		} catch (e: any) {
			addToast(e.message ?? 'Failed to load preview', 'error');
		} finally {
			previewLoading = false;
		}
	}

	function togglePreview() {
		showPreview = !showPreview;
		if (showPreview && !preview) {
			loadPreview();
		}
	}

	let bodyCharCount = $derived(guidelinesBody.length);
	let showStructure = $state(false);
	let showLorebooks = $state(false);

	// Effective injection depths from the preview, sorted shallow→deep for display.
	let injectionEnabled = $derived(preview?.injection?.enabled ?? false);
	let depthEntries = $derived(
		Object.entries(preview?.injection?.depths ?? {}).sort((a, b) => a[1] - b[1]),
	);
</script>

{#if loading}
	<div class="text-sm text-text-dim">Loading guidelines...</div>
{:else if error}
	<div class="text-sm text-error">{error}</div>
{:else}
	<div class="flex gap-3" style="height: calc(100vh - 120px)">
		<!-- ═══ LEFT: Form Fields ═══ -->
		<div class="w-[380px] shrink-0 bg-surface border border-border-custom rounded-[10px] overflow-hidden flex flex-col">
			<div class="px-4 py-3 border-b border-border-custom">
				<PageHeader title="Prompt & Guidelines" size="sm" />
			</div>

			<div class="flex-1 overflow-y-auto p-4 space-y-4">
				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">POV Mode</span>
					<SelectField bind:value={povMode} options={[{value: 'single', label: 'Single'}, {value: 'dual', label: 'Dual'}]} size="sm" />
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">POV Character</span>
					<InputField bind:value={povCharacter} placeholder="Character name" size="sm" />
				</div>

				{#if povMode === 'dual'}
					<div>
						<span class="block text-xs font-medium text-text-dim mb-1">Dual Characters</span>
						<InputField bind:value={dualCharacters} placeholder="Char A, Char B" size="sm" />
					</div>
				{/if}

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Narrative Voice</span>
					<SelectField bind:value={narrativeVoice} options={[{value: 'first', label: 'First Person'}, {value: 'third', label: 'Third Person'}]} size="sm" />
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Tense</span>
					<SelectField bind:value={tense} options={[{value: 'past', label: 'Past'}, {value: 'present', label: 'Present'}]} size="sm" />
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Scene Pacing</span>
					<SelectField bind:value={scenePacing} options={[{value: 'slow', label: 'Slow'}, {value: 'moderate', label: 'Moderate'}, {value: 'fast', label: 'Fast'}]} size="sm" />
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Response Length</span>
					<SelectField bind:value={responseLength} options={[{value: 'short', label: 'Short'}, {value: 'medium', label: 'Medium'}, {value: 'long', label: 'Long'}]} size="sm" />
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Tone</span>
					<InputField bind:value={tone} placeholder="dark, gothic, dramatic" size="sm" />
					<p class="text-[11px] text-text-dim/60 mt-0.5">Comma-separated</p>
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Sensitive Themes</span>
					<InputField bind:value={sensitiveThemes} placeholder="violence, abuse" size="sm" />
				</div>

				<div>
					<span class="block text-xs font-medium text-text-dim mb-1">Hard Limits</span>
					<InputField bind:value={hardLimits} placeholder="Content to never include" size="sm" />
				</div>

				<Divider />

				<div class="space-y-2.5">
					<Toggle label="Include Writing Principles" bind:checked={includeWritingPrinciples} />
					<Toggle label="Include NPC Framework" bind:checked={includeNpcFramework} />
					<Toggle label="Include Output Format" bind:checked={includeOutputFormat} />
				</div>

				<div class="pt-2">
					<Btn primary onclick={handleSave} disabled={saving}>
						{saving ? 'Saving...' : 'Save'}
					</Btn>
				</div>
			</div>
		</div>

		<!-- ═══ RIGHT: Body Editor + Preview ═══ -->
		<div class="flex-1 min-w-0 flex flex-col gap-3">
			<!-- Prompt structure: section order + injection depths -->
			<div class="bg-surface border border-border-custom rounded-[10px] overflow-hidden shrink-0">
				<button
					class="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-surface2/30 transition-colors"
					onclick={() => (showStructure = !showStructure)}
				>
					<span class="text-xs font-medium text-text-dim">Prompt Structure (section order + injection depth)</span>
					<span class="text-[10px] text-text-dim/60">{showStructure ? '▲' : '▼'}</span>
				</button>
				{#if showStructure}
					<div class="p-4 border-t border-border-custom max-h-96 overflow-y-auto">
						<PromptStructurePanel
							{rpFolder}
							{injectionEnabled}
							onSaved={() => { preview = null; if (showPreview) loadPreview(); }}
						/>
					</div>
				{/if}
			</div>

			<!-- Lorebooks: per-RP active-set + reindex -->
			<div class="bg-surface border border-border-custom rounded-[10px] overflow-hidden shrink-0">
				<button
					class="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-surface2/30 transition-colors"
					onclick={() => (showLorebooks = !showLorebooks)}
				>
					<span class="text-xs font-medium text-text-dim">Lorebooks (World Info active-set)</span>
					<span class="text-[10px] text-text-dim/60">{showLorebooks ? '▲' : '▼'}</span>
				</button>
				{#if showLorebooks}
					<div class="p-4 border-t border-border-custom max-h-96 overflow-y-auto">
						<LorebookPanel {rpFolder} />
					</div>
				{/if}
			</div>

			<!-- Body editor -->
			<div class="bg-surface border border-border-custom rounded-[10px] flex flex-col flex-1 min-h-0">
				<div class="px-4 py-2.5 border-b border-border-custom flex items-center justify-between">
					<SectionLabel>Guidelines Body</SectionLabel>
					<span class="text-[11px] text-text-dim font-mono">{bodyCharCount} chars</span>
				</div>
				<div class="flex-1 min-h-0 p-2">
					<textarea
						bind:value={guidelinesBody}
						class="w-full h-full px-3 py-2.5 rounded-lg border border-border-custom bg-surface2 text-[13px] text-text
							font-mono leading-relaxed focus:outline-none focus:ring-1 focus:ring-accent resize-none"
						placeholder="Markdown body content below frontmatter..."
						spellcheck="false"
					></textarea>
				</div>
			</div>

			<!-- Prompt preview (collapsible) -->
			<div class="bg-surface border border-border-custom rounded-[10px] overflow-hidden
				{showPreview ? '' : 'shrink-0'}">
				<button
					class="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-surface2/30 transition-colors"
					onclick={togglePreview}
				>
					<span class="text-xs font-medium text-text-dim">
						{showPreview ? 'Hide' : 'Show'} Prompt Preview
					</span>
					<span class="text-[10px] text-text-dim/60">{showPreview ? '▲' : '▼'}</span>
				</button>

				{#if showPreview}
					<div class="border-t border-border-custom">
						{#if previewLoading}
							<div class="p-4 text-xs text-text-dim">Loading preview...</div>
						{:else if preview}
							<div class="px-4 py-2 flex items-center gap-2 border-b border-border-custom/50 bg-surface2/30">
								<span class="text-[11px] text-text-dim">Sections:</span>
								{#each preview.sections as section}
									<Badge>{section}</Badge>
								{/each}
							</div>
							{#if preview.injection}
								<div class="px-4 py-2 border-b border-border-custom/50 bg-surface2/30">
									<div class="flex items-center gap-2 mb-1">
										<span class="text-[11px] text-text-dim">Injection depths:</span>
										<Badge color={injectionEnabled ? 'var(--color-success)' : 'var(--color-text-dim)'}>
											{injectionEnabled ? 'enabled' : 'disabled (all at depth 0)'}
										</Badge>
									</div>
									{#if depthEntries.length}
										<div class="flex flex-wrap gap-1.5">
											{#each depthEntries as [name, depth]}
												<span class="text-[11px] font-mono text-text-dim">
													{name}<span class="text-accent">@{depth}</span>
												</span>
											{/each}
										</div>
									{/if}
									<p class="text-[10px] text-text-dim/50 mt-1">
										Static preview shows the depth-0 system message; depths apply at runtime when injection is enabled.
									</p>
								</div>
							{/if}
							<div class="p-4 max-h-80 overflow-y-auto">
								<pre class="text-xs text-text whitespace-pre-wrap font-mono leading-relaxed">{preview.system_prompt}</pre>
							</div>
						{:else}
							<div class="p-4 text-xs text-text-dim">No preview available.</div>
						{/if}
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
