<script lang="ts">
	/**
	 * RP-scoped settings: guidelines, characters, scene state, custom state.
	 * Embedded as a tool in the Dashboard page.
	 */
	import { onMount } from 'svelte';
	import { getGuidelines, updateGuidelines } from '$lib/api/context';
	import { getFullState, updateCharacter, updateScene } from '$lib/api/state';
	import { getCustomStateSnapshot, setCustomStateValue, listPresets, applyPreset, type CustomStateSnapshot, type PresetInfo } from '$lib/api/customState';
	import { addToast } from '$lib/stores/ui';
	import { activeBranch } from '$lib/stores/rp';
	import Btn from '$lib/components/ui/Btn.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import SelectField from '$lib/components/ui/SelectField.svelte';
	import Toggle from '$lib/components/ui/Toggle.svelte';
	import FormField from '$lib/components/ui/FormField.svelte';
	import InfoRow from '$lib/components/ui/InfoRow.svelte';
	import CardSection from '$lib/components/ui/CardSection.svelte';
	import TabBar from '$lib/components/ui/TabBar.svelte';
	import { get } from 'svelte/store';
	import type { GuidelinesResponse, StateSnapshot, CharacterDetail, SceneStateDetail } from '$lib/types';

	interface Props {
		rpFolder: string;
	}

	let { rpFolder }: Props = $props();

	let activeTab = $state('guidelines');
	const tabs = [
		{ id: 'guidelines',  label: 'Guidelines' },
		{ id: 'characters',  label: 'Characters' },
		{ id: 'scene',       label: 'Scene' },
		{ id: 'customstate', label: 'Custom State' },
	];

	// Guidelines
	let guidelines = $state<GuidelinesResponse | null>(null);
	let guidelinesLoading = $state(true);
	let guidelinesSaving = $state(false);
	let gPovMode = $state('single');
	let gPovCharacter = $state('');
	let gDualCharacters = $state('');
	let gNarrativeVoice = $state('third');
	let gTense = $state('past');
	let gTone = $state('');
	let gScenePacing = $state('moderate');
	let gResponseLength = $state('medium');
	let gIntegrateUserNarrative = $state(true);
	let gPreserveUserDetails = $state(true);
	let gSensitiveThemes = $state('');
	let gHardLimits = $state('');

	// State
	let stateSnapshot = $state<StateSnapshot | null>(null);
	let stateLoading = $state(true);
	let characters = $state<Record<string, CharacterDetail>>({});
	let scene = $state<SceneStateDetail>({ location: null, time_of_day: null, mood: null, in_story_timestamp: null });

	// Inline editing
	let editingChar = $state<string | null>(null);
	let editCharLocation = $state('');
	let editCharEmotional = $state('');
	let charSaving = $state(false);
	let sceneSaving = $state(false);

	// Scene editing
	let sceneLocation = $state('');
	let sceneTimeOfDay = $state('');
	let sceneMood = $state('');
	let sceneTimestamp = $state('');
	let sceneEditing = $state(false);

	// Custom State
	let customState = $state<CustomStateSnapshot | null>(null);
	let customStateLoading = $state(true);
	let presets = $state<PresetInfo[]>([]);
	let selectedPreset = $state('');
	let applyingPreset = $state(false);
	let editingValue = $state<{ schemaId: string; entityId: string | null } | null>(null);
	let editValueStr = $state('');
	let valueSaving = $state(false);

	onMount(async () => {
		await Promise.all([loadGuidelines(), loadState(), loadCustomState()]);
	});

	async function loadGuidelines() {
		guidelinesLoading = true;
		try {
			const g = await getGuidelines(rpFolder);
			guidelines = g;
			gPovMode = g.pov_mode ?? 'single';
			gPovCharacter = g.pov_character ?? '';
			gDualCharacters = (g.dual_characters ?? []).join(', ');
			gNarrativeVoice = g.narrative_voice ?? 'third';
			gTense = g.tense ?? 'past';
			gTone = Array.isArray(g.tone) ? g.tone.join(', ') : (g.tone ?? '');
			gScenePacing = g.scene_pacing ?? 'moderate';
			gResponseLength = g.response_length ?? 'medium';
			gIntegrateUserNarrative = g.integrate_user_narrative ?? true;
			gPreserveUserDetails = g.preserve_user_details ?? true;
			gSensitiveThemes = (g.sensitive_themes ?? []).join(', ');
			gHardLimits = Array.isArray(g.hard_limits) ? g.hard_limits.join(', ') : (g.hard_limits ?? '');
		} catch (e: any) {
			addToast(`Failed to load guidelines: ${e.message}`, 'error');
		} finally {
			guidelinesLoading = false;
		}
	}

	async function saveGuidelines() {
		guidelinesSaving = true;
		try {
			const body: Record<string, unknown> = {
				pov_mode: gPovMode,
				pov_character: gPovCharacter || null,
				dual_characters: gDualCharacters ? gDualCharacters.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
				narrative_voice: gNarrativeVoice,
				tense: gTense,
				tone: gTone ? gTone.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
				scene_pacing: gScenePacing,
				response_length: gResponseLength,
				integrate_user_narrative: gIntegrateUserNarrative,
				preserve_user_details: gPreserveUserDetails,
				sensitive_themes: gSensitiveThemes ? gSensitiveThemes.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
				hard_limits: gHardLimits ? gHardLimits.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
			};
			await updateGuidelines(rpFolder, body as Partial<GuidelinesResponse>);
			addToast('Guidelines saved', 'success');
		} catch (e: any) {
			addToast(`Failed to save guidelines: ${e.message}`, 'error');
		} finally {
			guidelinesSaving = false;
		}
	}

	async function loadState() {
		stateLoading = true;
		try {
			stateSnapshot = await getFullState();
			characters = stateSnapshot.characters;
			scene = stateSnapshot.scene;
		} catch (e: any) {
			addToast(`Failed to load state: ${e.message}`, 'error');
		} finally {
			stateLoading = false;
		}
	}

	function startEditChar(name: string) {
		const c = characters[name];
		editingChar = name;
		editCharLocation = c.location ?? '';
		editCharEmotional = c.emotional_state ?? '';
	}

	function cancelEditChar() {
		editingChar = null;
	}

	async function saveChar() {
		if (!editingChar) return;
		charSaving = true;
		try {
			const updated = await updateCharacter(editingChar, {
				location: editCharLocation || undefined,
				emotional_state: editCharEmotional || undefined,
			});
			characters[editingChar] = updated;
			characters = characters;
			editingChar = null;
			addToast('Character updated', 'success');
		} catch (e: any) {
			addToast(`Failed to update character: ${e.message}`, 'error');
		} finally {
			charSaving = false;
		}
	}

	function startEditScene() {
		sceneLocation = scene.location ?? '';
		sceneTimeOfDay = scene.time_of_day ?? '';
		sceneMood = scene.mood ?? '';
		sceneTimestamp = scene.in_story_timestamp ?? '';
		sceneEditing = true;
	}

	function cancelEditScene() {
		sceneEditing = false;
	}

	async function saveScene() {
		sceneSaving = true;
		try {
			const updated = await updateScene({
				location: sceneLocation || undefined,
				time_of_day: sceneTimeOfDay || undefined,
				mood: sceneMood || undefined,
				in_story_timestamp: sceneTimestamp || undefined,
			});
			scene = updated;
			sceneEditing = false;
			addToast('Scene updated', 'success');
		} catch (e: any) {
			addToast(`Failed to update scene: ${e.message}`, 'error');
		} finally {
			sceneSaving = false;
		}
	}

	async function loadCustomState() {
		customStateLoading = true;
		try {
			const branch = get(activeBranch) || 'main';
			const [snap, presetList] = await Promise.all([
				getCustomStateSnapshot(rpFolder, branch),
				listPresets(),
			]);
			customState = snap;
			presets = presetList;
		} catch (e: any) {
			addToast(`Failed to load custom state: ${e.message}`, 'error');
		} finally {
			customStateLoading = false;
		}
	}

	async function handleApplyPreset() {
		if (!selectedPreset) return;
		applyingPreset = true;
		try {
			await applyPreset(selectedPreset, rpFolder);
			addToast(`Preset "${selectedPreset}" applied`, 'success');
			await loadCustomState();
		} catch (e: any) {
			addToast(`Failed to apply preset: ${e.message}`, 'error');
		} finally {
			applyingPreset = false;
		}
	}

	function startEditValue(schemaId: string, entityId: string | null, currentValue: unknown) {
		editingValue = { schemaId, entityId };
		editValueStr = currentValue != null ? (typeof currentValue === 'object' ? JSON.stringify(currentValue) : String(currentValue)) : '';
	}

	function cancelEditValue() {
		editingValue = null;
	}

	async function saveValue() {
		if (!editingValue) return;
		valueSaving = true;
		try {
			const branch = get(activeBranch) || 'main';
			let parsedValue: unknown = editValueStr;
			try { parsedValue = JSON.parse(editValueStr); } catch { /* use as string */ }
			await setCustomStateValue(editingValue.schemaId, {
				rp_folder: rpFolder,
				branch,
				entity_id: editingValue.entityId ?? undefined,
				value: parsedValue,
			});
			editingValue = null;
			addToast('Value updated', 'success');
			await loadCustomState();
		} catch (e: any) {
			addToast(`Failed to save value: ${e.message}`, 'error');
		} finally {
			valueSaving = false;
		}
	}

	function getValueForSchema(schemaId: string, entityId: string | null = null): unknown {
		if (!customState) return null;
		const match = customState.values.find(
			(v) => v.schema_id === schemaId && v.entity_id === entityId,
		);
		return match?.value ?? null;
	}

	const inputClass = 'w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text focus:outline-none focus:ring-1 focus:ring-accent';
</script>

<div class="space-y-3">
	<TabBar items={tabs} bind:active={activeTab} />

	<!-- Guidelines -->
	{#if activeTab === 'guidelines'}
		<CardSection title="Story Guidelines" subtitle="Narrative style and content rules for this RP.">
			{#if guidelinesLoading}
				<div class="p-4 text-sm text-text-dim">Loading...</div>
			{:else}
				<div class="p-4 space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<FormField label="POV Mode" id="g-pov-mode" size="xs">
							<SelectField id="g-pov-mode" bind:value={gPovMode}
								options={[{value: "single", label: "Single"}, {value: "dual", label: "Dual"}]} />
						</FormField>
						<FormField label="POV Character" id="g-pov-char" size="xs">
							<input id="g-pov-char" type="text" bind:value={gPovCharacter} class={inputClass} />
						</FormField>
					</div>
					<FormField label="Dual Characters" id="g-dual-chars" size="xs">
						<input id="g-dual-chars" type="text" bind:value={gDualCharacters} placeholder="Name1, Name2" class={inputClass} />
						<p class="text-xs text-text-dim mt-0.5">Comma-separated names</p>
					</FormField>
					<div class="grid grid-cols-3 gap-4">
						<FormField label="Narrative Voice" id="g-voice" size="xs">
							<SelectField id="g-voice" bind:value={gNarrativeVoice}
								options={[{value: "first", label: "First person"}, {value: "third", label: "Third person"}]} />
						</FormField>
						<FormField label="Tense" id="g-tense" size="xs">
							<SelectField id="g-tense" bind:value={gTense}
								options={[{value: "present", label: "Present"}, {value: "past", label: "Past"}]} />
						</FormField>
						<FormField label="Scene Pacing" id="g-pacing" size="xs">
							<SelectField id="g-pacing" bind:value={gScenePacing}
								options={[{value: "slow", label: "Slow"}, {value: "moderate", label: "Moderate"}, {value: "fast", label: "Fast"}]} />
						</FormField>
					</div>
					<div class="grid grid-cols-2 gap-4">
						<FormField label="Response Length" id="g-length" size="xs">
							<SelectField id="g-length" bind:value={gResponseLength}
								options={[{value: "short", label: "Short"}, {value: "medium", label: "Medium"}, {value: "long", label: "Long"}, {value: "variable", label: "Variable"}]} />
						</FormField>
						<FormField label="Tone" id="g-tone" size="xs">
							<input id="g-tone" type="text" bind:value={gTone} placeholder="dark, gritty, atmospheric" class={inputClass} />
							<p class="text-xs text-text-dim mt-0.5">Comma-separated</p>
						</FormField>
					</div>
					<div class="flex items-center gap-6">
						<Toggle label="Integrate User Narrative" bind:checked={gIntegrateUserNarrative} />
						<Toggle label="Preserve User Details" bind:checked={gPreserveUserDetails} />
					</div>
					<FormField label="Sensitive Themes" id="g-themes" size="xs">
						<input id="g-themes" type="text" bind:value={gSensitiveThemes} placeholder="violence, substance_abuse" class={inputClass} />
						<p class="text-xs text-text-dim mt-0.5">Comma-separated</p>
					</FormField>
					<FormField label="Hard Limits" id="g-limits" size="xs">
						<input id="g-limits" type="text" bind:value={gHardLimits} placeholder="none" class={inputClass} />
						<p class="text-xs text-text-dim mt-0.5">Comma-separated</p>
					</FormField>
					<div class="flex items-center justify-end gap-3 pt-2 border-t border-border-custom">
						<Btn primary onclick={saveGuidelines} disabled={guidelinesSaving}>{guidelinesSaving ? 'Saving...' : 'Save'}</Btn>
					</div>
				</div>
			{/if}
		</CardSection>

	<!-- Characters -->
	{:else if activeTab === 'characters'}
		<CardSection title="Characters" subtitle="All characters in the current RP state.">
			{#snippet actions()}
				<button
					onclick={loadState}
					disabled={stateLoading}
					class="text-xs text-accent hover:text-accent/80 transition-colors disabled:opacity-50"
				>
					{stateLoading ? 'Loading...' : 'Refresh'}
				</button>
			{/snippet}
			{#if stateLoading}
				<div class="p-4 text-sm text-text-dim">Loading...</div>
			{:else if Object.keys(characters).length === 0}
				<div class="p-4 text-sm text-text-dim">No characters found.</div>
			{:else}
				<div class="divide-y divide-border-custom">
					{#each Object.entries(characters) as [name, char]}
						<div class="px-4 py-3">
							{#if editingChar === name}
								<div class="space-y-2">
									<p class="text-sm font-medium text-text">{name}</p>
									<div class="grid grid-cols-2 gap-3">
										<div>
											<label class="block text-xs text-text-dim mb-0.5" for="edit-loc-{name}">Location</label>
											<input id="edit-loc-{name}" type="text" bind:value={editCharLocation} class={inputClass} />
										</div>
										<div>
											<label class="block text-xs text-text-dim mb-0.5" for="edit-emo-{name}">Emotional State</label>
											<input id="edit-emo-{name}" type="text" bind:value={editCharEmotional} class={inputClass} />
										</div>
									</div>
									<div class="flex gap-2 justify-end">
										<button onclick={cancelEditChar} class="text-xs text-text-dim hover:text-text px-2 py-1">Cancel</button>
										<Btn primary small onclick={saveChar} disabled={charSaving}>{charSaving ? 'Saving...' : 'Save'}</Btn>
									</div>
								</div>
							{:else}
								<div class="flex items-start justify-between">
									<div class="space-y-0.5">
										<p class="text-sm font-medium text-text">{name}</p>
										<div class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-text-dim">
											{#if char.importance}
												<span>Importance: <span class="text-text">{char.importance}</span></span>
											{/if}
											{#if char.location}
												<span>Location: <span class="text-text">{char.location}</span></span>
											{/if}
											{#if char.emotional_state}
												<span>Emotional: <span class="text-text">{char.emotional_state}</span></span>
											{/if}
											{#if char.conditions?.length}
												<span>Conditions: <span class="text-text">{char.conditions.join(', ')}</span></span>
											{/if}
											{#if char.last_seen}
												<span>Last seen: <span class="text-text">{char.last_seen}</span></span>
											{/if}
										</div>
									</div>
									<button
										onclick={() => startEditChar(name)}
										class="text-xs text-accent hover:text-accent/80 shrink-0 ml-2"
									>
										Edit
									</button>
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</CardSection>

	<!-- Scene State -->
	{:else if activeTab === 'scene'}
		<CardSection title="Scene State" subtitle="Current scene context.">
			{#snippet actions()}
				{#if !sceneEditing}
					<button
						onclick={startEditScene}
						class="text-xs text-accent hover:text-accent/80 transition-colors"
					>
						Edit
					</button>
				{/if}
			{/snippet}
			{#if stateLoading}
				<div class="p-4 text-sm text-text-dim">Loading...</div>
			{:else if sceneEditing}
				<div class="p-4 space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<FormField label="Location" id="s-location" size="xs">
							<input id="s-location" type="text" bind:value={sceneLocation} class={inputClass} />
						</FormField>
						<FormField label="Time of Day" id="s-tod" size="xs">
							<input id="s-tod" type="text" bind:value={sceneTimeOfDay} class={inputClass} />
						</FormField>
						<FormField label="Mood" id="s-mood" size="xs">
							<input id="s-mood" type="text" bind:value={sceneMood} class={inputClass} />
						</FormField>
						<FormField label="In-Story Timestamp" id="s-ts" size="xs">
							<input id="s-ts" type="text" bind:value={sceneTimestamp} class={inputClass} />
						</FormField>
					</div>
					<div class="flex items-center justify-end gap-2 pt-2 border-t border-border-custom">
						<button onclick={cancelEditScene} class="text-xs text-text-dim hover:text-text px-2 py-1">Cancel</button>
						<Btn primary onclick={saveScene} disabled={sceneSaving}>{sceneSaving ? 'Saving...' : 'Save'}</Btn>
					</div>
				</div>
			{:else}
				<div class="p-4 space-y-2">
					<InfoRow label="Location" value={scene.location} />
					<InfoRow label="Time of Day" value={scene.time_of_day} />
					<InfoRow label="Mood" value={scene.mood} />
					<InfoRow label="In-Story Timestamp" value={scene.in_story_timestamp} />
				</div>
			{/if}
		</CardSection>

	<!-- Custom State -->
	{:else if activeTab === 'customstate'}
		<div class="space-y-4">
			<!-- Presets -->
			{#if presets.length > 0}
				<CardSection title="Presets" subtitle="Bootstrap common custom state schemas.">
					<div class="p-4 flex items-end gap-3">
						<div class="flex-1">
							<FormField label="Preset" id="cs-preset" size="xs">
								<SelectField id="cs-preset" bind:value={selectedPreset} placeholder="Select a preset..."
									options={presets.map(p => ({value: p.name, label: p.name + (p.description ? " — " + p.description : "") + " (" + p.schema_count + " schemas)"}))} />
							</FormField>
						</div>
						<Btn primary small onclick={handleApplyPreset} disabled={!selectedPreset || applyingPreset}>{applyingPreset ? 'Applying...' : 'Apply'}</Btn>
					</div>
				</CardSection>
			{/if}

			<!-- Schemas + Values -->
			<CardSection title="Custom State" subtitle="Custom tracked fields and their current values.">
				{#snippet actions()}
					<button
						onclick={loadCustomState}
						disabled={customStateLoading}
						class="text-xs text-accent hover:text-accent/80 transition-colors disabled:opacity-50"
					>
						{customStateLoading ? 'Loading...' : 'Refresh'}
					</button>
				{/snippet}
				{#if customStateLoading}
					<div class="p-4 text-sm text-text-dim">Loading...</div>
				{:else if !customState || customState.schemas.length === 0}
					<div class="p-4 text-sm text-text-dim">No custom state schemas defined. Use a preset above to get started.</div>
				{:else}
					<div class="divide-y divide-border-custom">
						{#each customState.schemas as schema}
							{@const value = getValueForSchema(schema.id)}
							<div class="px-4 py-3">
								{#if editingValue?.schemaId === schema.id && editingValue?.entityId === null}
									<div class="space-y-2">
										<div class="flex items-center gap-2">
											<p class="text-sm font-medium text-text">{schema.name}</p>
											<span class="px-1.5 py-0.5 bg-bg-subtle rounded text-xs text-text-dim">{schema.data_type}</span>
										</div>
										<input type="text" bind:value={editValueStr} class={inputClass} />
										<div class="flex gap-2 justify-end">
											<button onclick={cancelEditValue} class="text-xs text-text-dim hover:text-text px-2 py-1">Cancel</button>
											<Btn primary small onclick={saveValue} disabled={valueSaving}>{valueSaving ? 'Saving...' : 'Save'}</Btn>
										</div>
									</div>
								{:else}
									<div class="flex items-center justify-between">
										<div class="space-y-0.5">
											<div class="flex items-center gap-2">
												<p class="text-sm font-medium text-text">{schema.name}</p>
												<span class="px-1.5 py-0.5 bg-bg-subtle rounded text-xs text-text-dim">{schema.data_type}</span>
												<span class="px-1.5 py-0.5 bg-bg-subtle rounded text-xs text-text-dim">{schema.category}</span>
											</div>
											<p class="text-xs text-text-dim">
												Value: <span class="text-text font-mono">{value != null ? (typeof value === 'object' ? JSON.stringify(value) : String(value)) : '(not set)'}</span>
											</p>
										</div>
										<button
											onclick={() => startEditValue(schema.id, null, value)}
											class="text-xs text-accent hover:text-accent/80 shrink-0 ml-2"
										>
											Edit
										</button>
									</div>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</CardSection>
		</div>
	{/if}
</div>
