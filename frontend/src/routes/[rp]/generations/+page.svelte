<script lang="ts">
	import { page } from '$app/stores';
	import { suggestCard, listCards, createCard, reindex } from '$lib/api/cards';
	import { addToast } from '$lib/stores/ui';
	import yaml from 'js-yaml';
	import type { StoryCardSummary, SuggestCardResponse, CardType } from '$lib/types';
	import { CARD_TYPES } from '$lib/types';
	import Btn from '$lib/components/ui/Btn.svelte';
	import InputField from '$lib/components/ui/InputField.svelte';
	import SelectField from '$lib/components/ui/SelectField.svelte';
	import Card from '$lib/components/ui/Card.svelte';

	import { ARCHETYPES, MODIFIERS } from '$lib/constants/archetypes';

	let rpFolder = $derived($page.params.rp ?? '');

	// Form inputs
	let entityName = $state('');
	let cardType = $state<CardType>('npc');
	let prompt = $state('');
	let buildOnPrevious = $state(false);
	let primaryArchetype = $state('');
	let secondaryArchetype = $state('');
	let selectedModifiers = $state<string[]>([]);

	// Relations
	let relations = $state<StoryCardSummary[]>([]);
	let relationSearch = $state('');
	let searchResults = $state<StoryCardSummary[]>([]);
	let showSearchDropdown = $state(false);
	let allCards = $state<StoryCardSummary[]>([]);
	let cardsLoaded = $state(false);

	// Generation state
	let generating = $state(false);
	let output = $state<SuggestCardResponse | null>(null);
	let previousOutput = $state<SuggestCardResponse | null>(null);

	// Save state
	let saving = $state(false);

	// Alter mode
	let altering = $state(false);

	// Frontmatter collapse
	let frontmatterOpen = $state(false);

	/** Split raw markdown into frontmatter and body */
	function splitFrontmatter(raw: string): { fm: string; body: string } {
		const match = raw.match(/^(---\r?\n[\s\S]*?\r?\n---)\r?\n?/);
		if (match) {
			return { fm: match[1], body: raw.slice(match[0].length).trim() };
		}
		return { fm: '', body: raw.trim() };
	}

	/** Strip wrapping ```markdown ... ``` code fences the LLM sometimes adds. */
	function stripCodeFences(raw: string): string {
		return raw.replace(/^```(?:markdown|md)?\s*\n?/, '').replace(/\n?```\s*$/, '');
	}

	// Debounce timer
	let searchTimer: ReturnType<typeof setTimeout> | null = null;

	async function ensureCardsLoaded() {
		if (cardsLoaded) return;
		try {
			const resp = await listCards();
			allCards = resp.cards;
			cardsLoaded = true;
		} catch (err: any) {
			addToast(err.message ?? 'Failed to load cards', 'error');
		}
	}

	function handleSearchInput() {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(async () => {
			await ensureCardsLoaded();
			const q = relationSearch.toLowerCase().trim();
			if (!q) {
				searchResults = [];
				showSearchDropdown = false;
				return;
			}
			const addedNames = new Set(relations.map((r) => r.name));
			searchResults = allCards
				.filter(
					(c) =>
						!addedNames.has(c.name) &&
						(c.name.toLowerCase().includes(q) ||
							c.card_type.toLowerCase().includes(q) ||
							c.aliases.some((a) => a.toLowerCase().includes(q)))
				)
				.slice(0, 10);
			showSearchDropdown = searchResults.length > 0;
		}, 300);
	}

	function addRelation(card: StoryCardSummary) {
		relations = [...relations, card];
		relationSearch = '';
		searchResults = [];
		showSearchDropdown = false;
	}

	function removeRelation(name: string) {
		relations = relations.filter((r) => r.name !== name);
	}

	function toggleModifier(mod: string) {
		if (selectedModifiers.includes(mod)) {
			selectedModifiers = selectedModifiers.filter(m => m !== mod);
		} else if (selectedModifiers.length < 3) {
			selectedModifiers = [...selectedModifiers, mod];
		}
	}

	function buildContext(): string {
		const parts: string[] = [];

		// Archetype/modifier context for NPC/character cards
		if ((cardType === 'npc' || cardType === 'character') && (primaryArchetype || selectedModifiers.length > 0)) {
			const lines: string[] = [];
			if (primaryArchetype) lines.push(`Primary archetype: ${primaryArchetype}`);
			if (secondaryArchetype) lines.push(`Secondary archetype: ${secondaryArchetype}`);
			if (selectedModifiers.length > 0) lines.push(`Behavioral modifiers: ${selectedModifiers.join(', ')}`);
			parts.push('Character framework:\n' + lines.join('\n'));
		}

		// Build on previous
		if (buildOnPrevious && previousOutput) {
			parts.push('Previous generation:\n' + previousOutput.markdown);
		}

		// User prompt
		if (prompt.trim()) {
			parts.push(prompt.trim());
		}

		return parts.join('\n\n');
	}

	async function generate() {
		if (!entityName.trim()) {
			addToast('Entity name is required', 'warning');
			return;
		}
		generating = true;
		try {
			const context = buildContext() || undefined;
			const relatedNames = relations.map((r) => r.name);
			const result = await suggestCard(entityName.trim(), cardType, context, rpFolder, relatedNames);
			result.markdown = stripCodeFences(result.markdown);
			// Store previous for "build on previous"
			if (output) previousOutput = output;
			output = result;
		} catch (err: any) {
			addToast(err.message ?? 'Generation failed', 'error');
		} finally {
			generating = false;
		}
	}

	async function alterOutput() {
		if (!output) {
			addToast('Nothing to alter — generate first', 'warning');
			return;
		}
		if (!prompt.trim()) {
			addToast('Enter alteration instructions in the prompt', 'warning');
			return;
		}
		altering = true;
		try {
			const alterContext =
				'Modify the following existing card based on these instructions. Do not regenerate from scratch:\n' +
				output.markdown +
				'\n\nInstructions: ' +
				prompt.trim();
			const relatedNames = relations.map((r) => r.name);
			const result = await suggestCard(entityName.trim(), cardType, alterContext, rpFolder, relatedNames);
			result.markdown = stripCodeFences(result.markdown);
			previousOutput = output;
			output = result;
		} catch (err: any) {
			addToast(err.message ?? 'Alteration failed', 'error');
		} finally {
			altering = false;
		}
	}

	/** Parse frontmatter from markdown so we send structured data to the API. */
	function parseFrontmatter(md: string): { frontmatter: Record<string, unknown>; body: string } {
		const match = md.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
		if (!match) return { frontmatter: {}, body: md };
		try {
			const fm = yaml.load(match[1]);
			return {
				frontmatter: (fm && typeof fm === 'object') ? fm as Record<string, unknown> : {},
				body: match[2].trim(),
			};
		} catch {
			return { frontmatter: {}, body: md };
		}
	}

	async function saveCard() {
		if (!output) return;
		saving = true;
		try {
			const { frontmatter, body } = parseFrontmatter(output.markdown);
			await createCard(output.card_type, {
				name: output.entity_name,
				frontmatter,
				content: body,
			});
			await reindex();
			addToast(`Card "${output.entity_name}" saved and indexed`, 'success');
			// Refresh card cache for relations search
			cardsLoaded = false;
		} catch (err: any) {
			addToast(err.message ?? 'Save failed', 'error');
		} finally {
			saving = false;
		}
	}

	function handleSearchKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			showSearchDropdown = false;
		}
	}

	function handleSearchBlur() {
		// Delay to allow click on dropdown item
		setTimeout(() => (showSearchDropdown = false), 200);
	}
</script>

<div class="flex gap-3 h-full">
	<!-- Left: Output panel -->
	<div class="flex-[3] min-w-0 flex flex-col gap-2">
		<div class="bg-surface rounded border border-border-custom p-3 flex-1 flex flex-col">
			<div class="flex items-center justify-between mb-2">
				<h2 class="text-xs font-semibold text-text">Output</h2>
				{#if output}
					<span class="text-xs text-text-dim">
						via {output.model_used}
					</span>
				{/if}
			</div>

			{#if generating || altering}
				<div class="flex-1 flex items-center justify-center">
					<div class="text-sm text-text-dim animate-pulse">
						{altering ? 'Altering...' : 'Generating...'}
					</div>
				</div>
			{:else if output}
				{@const parts = splitFrontmatter(output.markdown)}
				<div class="flex-1 overflow-auto">
					{#if parts.fm}
						<button
							class="flex items-center gap-1.5 text-[11px] text-text-dim hover:text-text transition-colors mb-2"
							onclick={() => (frontmatterOpen = !frontmatterOpen)}
						>
							<span class="inline-block transition-transform {frontmatterOpen ? 'rotate-90' : ''}"
								style="font-size: 10px">&#9654;</span>
							Frontmatter
						</button>
						{#if frontmatterOpen}
							<pre class="text-xs text-text-dim whitespace-pre-wrap break-words font-mono leading-relaxed bg-bg-subtle rounded-lg p-3 mb-3 border border-border-custom/50">{parts.fm}</pre>
						{/if}
					{/if}
					<pre class="text-sm text-text whitespace-pre-wrap break-words font-mono leading-relaxed">{parts.body}</pre>
				</div>
			{:else}
				<div class="flex-1 flex items-center justify-center">
					<p class="text-sm text-text-dim">No output yet. Configure and generate a card.</p>
				</div>
			{/if}

			{#if output && !generating && !altering}
				<div class="mt-2 pt-2 border-t border-border-custom">
					<Btn primary onclick={saveCard} disabled={saving}>
						{saving ? 'Saving...' : 'Save Card'}
					</Btn>
				</div>
			{/if}
		</div>
	</div>

	<!-- Right: Controls panel -->
	<div class="flex-[2] min-w-0 flex flex-col gap-2">
		<!-- Relations -->
		<div class="bg-surface rounded border border-border-custom p-3 space-y-2">
			<h3 class="text-xs font-semibold text-text">Relations</h3>

			<!-- Search -->
			<div class="relative">
				<input
					type="text"
					bind:value={relationSearch}
					oninput={handleSearchInput}
					onkeydown={handleSearchKeydown}
					onblur={handleSearchBlur}
					onfocus={handleSearchInput}
					placeholder="Search & add cards..."
					class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
						placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent"
				/>

				{#if showSearchDropdown}
					<div class="absolute z-10 top-full left-0 right-0 mt-1 bg-surface border border-border-custom rounded-md shadow-lg max-h-48 overflow-auto">
						{#each searchResults as card}
							<button
								class="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-bg-subtle transition-colors"
								onmousedown={(e: MouseEvent) => { e.preventDefault(); addRelation(card); }}
							>
								<span class="text-xs bg-bg-subtle text-text-dim px-1.5 py-0.5 rounded">{card.card_type}</span>
								<span class="text-sm text-text truncate">{card.name}</span>
							</button>
						{/each}
					</div>
				{/if}
			</div>

			<!-- Added relations chips -->
			{#if relations.length > 0}
				<div class="flex flex-wrap gap-1.5">
					{#each relations as rel}
						<span class="inline-flex items-center gap-1 text-xs bg-bg-subtle text-text px-2 py-1 rounded">
							<span class="text-text-dim">{rel.card_type}</span>
							<span>{rel.name}</span>
							<button
								class="text-text-dim hover:text-error transition-colors ml-0.5"
								onclick={() => removeRelation(rel.name)}
								aria-label="Remove {rel.name}"
							>&times;</button>
						</span>
					{/each}
				</div>
			{/if}
		</div>

		<!-- Card Type + Entity Name -->
		<div class="bg-surface rounded border border-border-custom p-3 space-y-2">
			<div class="space-y-1">
				<label for="card-type" class="text-xs font-semibold text-text">Card Type</label>
				<SelectField id="card-type" bind:value={cardType}
						options={CARD_TYPES.map(ct => ({value: ct, label: ct}))} />
			</div>

			<div class="space-y-1">
				<label for="entity-name" class="text-xs font-semibold text-text">Entity Name</label>
				<InputField id="entity-name" bind:value={entityName} placeholder="e.g. Marco, The Gilded Rose..." />
			</div>
		</div>

		<!-- Archetypes & Modifiers (NPC/character only) -->
		{#if cardType === 'npc' || cardType === 'character'}
			<div class="bg-surface rounded border border-border-custom p-3 space-y-2">
				<h3 class="text-xs font-semibold text-text">Archetypes</h3>
				<div class="grid grid-cols-2 gap-2">
					<div class="space-y-1">
						<label for="primary-arch" class="text-[11px] text-text-dim">Primary</label>
						<SelectField id="primary-arch" bind:value={primaryArchetype}
							options={ARCHETYPES} />
					</div>
					<div class="space-y-1">
						<label for="secondary-arch" class="text-[11px] text-text-dim">Secondary</label>
						<SelectField id="secondary-arch" bind:value={secondaryArchetype}
							options={ARCHETYPES} />
					</div>
				</div>

				<h3 class="text-xs font-semibold text-text mt-2">Modifiers <span class="text-text-dim font-normal">(up to 3)</span></h3>
				<div class="flex flex-wrap gap-1.5">
					{#each MODIFIERS as mod}
						<button
							class="px-2 py-1 rounded text-xs transition-all border
								{selectedModifiers.includes(mod)
									? 'bg-accent/15 text-accent border-accent/30'
									: 'bg-bg-subtle text-text-dim border-border-custom/50 hover:text-text hover:border-border-custom'}
								{selectedModifiers.length >= 3 && !selectedModifiers.includes(mod) ? 'opacity-40 cursor-not-allowed' : ''}"
							onclick={() => toggleModifier(mod)}
							disabled={selectedModifiers.length >= 3 && !selectedModifiers.includes(mod)}
						>
							{mod.replaceAll('_', ' ')}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<!-- Prompt -->
		<div class="bg-surface rounded border border-border-custom p-3 space-y-2">
			<label for="prompt" class="text-xs font-semibold text-text">Prompt</label>
			<textarea
				id="prompt"
				bind:value={prompt}
				placeholder="Additional context or instructions for the AI..."
				rows="5"
				class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
					placeholder:text-text-dim/50 focus:outline-none focus:ring-1 focus:ring-accent resize-none"
			></textarea>

		</div>

		<!-- Build on Previous + Action buttons -->
		<div class="flex flex-col gap-2">
			<button
				class="w-full flex items-center justify-between px-4 py-2 rounded-md text-sm font-medium transition-all border
					{buildOnPrevious
						? 'bg-accent/15 text-accent border-accent/30'
						: 'bg-bg-subtle text-text-dim border-border-custom/50 hover:text-text hover:border-border-custom'}
					{!previousOutput ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}"
				onclick={() => { if (previousOutput) buildOnPrevious = !buildOnPrevious; }}
				disabled={!previousOutput}
				title={previousOutput ? (buildOnPrevious ? 'Click to disable' : 'Click to build on previous output') : 'Generate first to enable'}
			>
				<span>Build on Previous</span>
				{#if buildOnPrevious && previousOutput}
					<span class="text-xs bg-accent/10 px-2 py-0.5 rounded-full truncate max-w-[140px]">
						{previousOutput.entity_name}
					</span>
				{:else}
					<span class="w-8 h-4 rounded-full transition-colors {buildOnPrevious ? 'bg-accent' : 'bg-bg-subtle border border-border-custom'}">
						<span class="block w-3 h-3 rounded-full bg-text mt-0.5 transition-transform {buildOnPrevious ? 'translate-x-4' : 'translate-x-0.5'}"></span>
					</span>
				{/if}
			</button>
			<Btn primary onclick={generate} disabled={generating || altering || !entityName.trim()}>
				{generating ? 'Generating...' : 'Generate'}
			</Btn>

			<Btn onclick={alterOutput} disabled={generating || altering || !output}>
				{altering ? 'Altering...' : 'Alter Output'}
			</Btn>
		</div>
	</div>
</div>
