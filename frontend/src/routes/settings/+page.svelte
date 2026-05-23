<script lang="ts">
	import { onMount } from 'svelte';
	import { getConfig, updateConfig, getActiveRP, setActiveRP, testProvider, getDiagnosticStatus, updateDiagnostics, clearDiagnostics, downloadDiagnostics, sendDiagnosticReport, type AppConfig, type ProviderTestResult } from '$lib/api/config';
	import type { DiagnosticStatus } from '$lib/types';
	import { checkHealth, type HealthResponse } from '$lib/api/health';
	import { getVectorStats, reindexExchanges } from '$lib/api/vectors';
	import { reindex } from '$lib/api/cards';
	import { addToast } from '$lib/stores/ui';
	import type { VectorStats, ReindexResponse } from '$lib/types';
	import Card from '$lib/components/ui/Card.svelte';
	import Btn from '$lib/components/ui/Btn.svelte';
	import InputField from '$lib/components/ui/InputField.svelte';
	import SectionLabel from '$lib/components/ui/SectionLabel.svelte';
	import SideNav from '$lib/components/ui/SideNav.svelte';
	import Toggle from '$lib/components/ui/Toggle.svelte';

	const sections = [
		{ id: 'general',     label: 'General'        },
		{ id: 'appearance',  label: 'Appearance'     },
		{ id: 'system',      label: 'System'         },
		{ id: 'maintenance', label: 'Maintenance'    },
		{ id: 'server',      label: 'Server'         },
		{ id: 'llm',         label: 'LLM Models'     },
		{ id: 'pacing',      label: 'Thread Pacing'  },
		{ id: 'search',      label: 'Search Tuning'  },
		{ id: 'trust',       label: 'Trust Tuning'   },
		{ id: 'context',     label: 'Context Tuning' },
		{ id: 'diagnostics', label: 'Diagnostics'    },
		{ id: 'apikeys',     label: 'API Keys'       },
	];

	let activeSection = $state('general');
	let loadError = $state('');
	let saveStatus: Record<string, 'idle' | 'saving' | 'saved' | 'error'> = $state({});

	// Server
	let serverHost = $state('0.0.0.0');
	let serverPort = $state(3000);

	// LLM
	let llmNpcReactions = $state('anthropic/claude-haiku');
	let llmAnalysis     = $state('google/gemini-2.0-flash-001');
	let llmCardGen      = $state('google/gemini-2.0-flash-001');
	let llmChat         = $state('');
	let chatMaxVariants = $state(10);
	let llmEmbeddings   = $state('openai/text-embedding-3-small');
	let llmFallback     = $state('google/gemini-2.0-flash-001');
	let llmDefaultPov   = $state('Lilith');
	let llmChatMode: 'provider' | 'sdk' = $state('provider');

	// Temperatures
	let tempChat = $state(0.7);
	let tempRegenBump = $state(0.05);
	let tempNpc = $state(0.6);
	let tempSummary = $state(0.5);

	// Search
	let searchVectorWeight = $state(0.7);
	let searchBm25Weight   = $state(0.3);
	let searchThreshold    = $state(0.7);
	let searchChunkSize    = $state(1000);
	let searchChunkOverlap = $state(200);

	// Trust
	let trustIncrease    = $state(1);
	let trustDecrease    = $state(2);
	let trustSessionGain = $state(8);
	let trustSessionLoss = $state(-15);
	let trustMin         = $state(-50);
	let trustMax         = $state(50);

	// Context
	let contextMaxDocs    = $state(5);
	let contextMaxHops    = $state(2);
	let contextStaleAfter = $state(8);

	// Active RP
	let activeRP = $state(false);
	let activeRPSessions = $state(0);
	let activeRPToggling = $state(false);

	// API Keys
	let openrouterKey = $state('');
	let keyVisible = $state(false);

	// Provider test
	let providerTestResult = $state<ProviderTestResult | null>(null);
	let providerTesting = $state(false);

	// Diagnostics
	let diagStatus = $state<DiagnosticStatus | null>(null);
	let diagEnabled = $state(false);
	let diagLevel = $state<'full' | 'metadata'>('full');
	let diagAutoReportEnabled = $state(false);
	let diagAutoReportUrl = $state('');
	let diagOnError = $state(true);
	let diagOnSessionEnd = $state(false);
	let diagReporterKey = $state('');
	let diagLoading = $state(false);
	let diagClearing = $state(false);
	let diagDownloading = $state(false);
	let diagSending = $state(false);
	let diagWarning = $state('');

	// Pacing
	let scenePacing = $state<'slow' | 'moderate' | 'fast'>('moderate');
	const PACING_DEFAULTS: Record<string, { gentle: number; moderate: number; strong: number }> = {
		slow:     { gentle: 8,  moderate: 15, strong: 20 },
		moderate: { gentle: 5,  moderate: 10, strong: 15 },
		fast:     { gentle: 3,  moderate: 5,  strong: 8  },
	};

	// Appearance (stored in localStorage)
	let selectedTheme = $state('sage');
	let fontSize: 'small' | 'medium' | 'large' = $state('medium');
	let uiDensity: 'compact' | 'comfortable' | 'spacious' = $state('comfortable');

	const THEME_PRESETS: { id: string; label: string; accent: string; bg: string; text: string }[] = [
		{ id: 'sage',    label: 'Sage',    accent: '#6d8c5e', bg: '#f4f0e8', text: '#38322b' },
		{ id: 'copper',  label: 'Copper',  accent: '#cb997e', bg: '#f1e4dc', text: '#2f1d13' },
		{ id: 'gold',    label: 'Gold',    accent: '#6b705c', bg: '#ddbea9', text: '#2f1d13' },
		{ id: 'berry',   label: 'Berry',   accent: '#c14074', bg: '#210a1b', text: '#eaf2ef' },
		{ id: 'rose',    label: 'Rose',    accent: '#ce6a85', bg: '#e1a3b3', text: '#2f0f18' },
		{ id: 'steel',   label: 'Steel',   accent: '#546e78', bg: '#c2cfd6', text: '#0d1416' },
		{ id: 'sakura',  label: 'Sakura',  accent: '#5d6d6f', bg: '#e1b7bd', text: '#240f12' },
		{ id: 'forest',  label: 'Forest',  accent: '#354f52', bg: '#84a98c', text: '#0a0f10' },
		{ id: 'dark',    label: 'Dark',    accent: '#7fa070', bg: '#1c2024', text: '#e8e4dc' },
	];

	function loadAppearance() {
		try {
			const stored = localStorage.getItem('rp-appearance');
			if (stored) {
				const parsed = JSON.parse(stored);
				selectedTheme = parsed.theme ?? parsed.accentColor ? themeFromLegacy(parsed.accentColor) : selectedTheme;
				fontSize = parsed.fontSize ?? fontSize;
				uiDensity = parsed.uiDensity ?? uiDensity;
			}
		} catch {}
	}

	function themeFromLegacy(accentColor: string): string {
		const match = THEME_PRESETS.find(t => t.accent === accentColor);
		return match?.id ?? 'sage';
	}

	function saveAppearance() {
		const prefs = { theme: selectedTheme, fontSize, uiDensity };
		localStorage.setItem('rp-appearance', JSON.stringify(prefs));
		applyAppearance();
		addToast('Appearance saved', 'success');
	}

	function applyAppearance() {
		const root = document.documentElement;
		if (selectedTheme === 'sage') {
			root.removeAttribute('data-theme');
		} else {
			root.dataset.theme = selectedTheme;
		}

		const fontSizes: Record<string, string> = { small: '13px', medium: '14px', large: '16px' };
		root.style.setProperty('font-size', fontSizes[fontSize] ?? '14px');

		const densityScale: Record<string, string> = { compact: '0.85', comfortable: '1', spacious: '1.2' };
		root.style.setProperty('--density-scale', densityScale[uiDensity] ?? '1');
	}

	// System Info
	let health: HealthResponse | null = $state(null);
	let configData: AppConfig | null = $state(null);

	// Maintenance
	let vectorStats: VectorStats | null = $state(null);
	let vectorStatsLoading = $state(false);
	let reindexing = $state(false);
	let reembedding = $state(false);

	onMount(async () => {
		loadAppearance();
		try {
			const [cfg, rpStatus, h, ds] = await Promise.all([getConfig(), getActiveRP(), checkHealth(), getDiagnosticStatus()]);
			configData = cfg;
			health = h;
			activeRP = rpStatus.active_rp;
			activeRPSessions = rpStatus.session_count;
			serverHost         = cfg.server.host;
			serverPort         = cfg.server.port;
			llmNpcReactions    = cfg.llm.models.npc_reactions;
			llmAnalysis        = cfg.llm.models.response_analysis;
			llmCardGen         = cfg.llm.models.card_generation;
			llmChat            = cfg.chat.model ?? '';
			chatMaxVariants    = cfg.chat.max_variants ?? 10;
			llmEmbeddings      = cfg.llm.models.embeddings;
			llmFallback        = cfg.llm.fallback_model;
			llmDefaultPov      = cfg.rp.default_pov_character;
			llmChatMode        = cfg.llm.mode?.chat ?? 'provider';
			tempChat           = cfg.llm.temperatures?.chat ?? 0.7;
			tempRegenBump      = cfg.llm.temperatures?.chat_regenerate_bump ?? 0.05;
			tempNpc            = cfg.llm.temperatures?.npc_reactions ?? 0.6;
			tempSummary        = cfg.llm.temperatures?.summary ?? 0.5;
			searchVectorWeight = cfg.search.vector_weight;
			searchBm25Weight   = cfg.search.bm25_weight;
			searchThreshold    = cfg.search.similarity_threshold;
			searchChunkSize    = cfg.search.chunk_size;
			searchChunkOverlap = cfg.search.chunk_overlap;
			trustIncrease      = cfg.trust.increase_value;
			trustDecrease      = cfg.trust.decrease_value;
			trustSessionGain   = cfg.trust.session_max_gain;
			trustSessionLoss   = cfg.trust.session_max_loss;
			trustMin           = cfg.trust.min_score;
			trustMax           = cfg.trust.max_score;
			contextMaxDocs     = cfg.context.max_documents;
			contextMaxHops     = cfg.context.max_graph_hops;
			contextStaleAfter  = cfg.context.stale_threshold_turns;
			diagStatus = ds;
			diagEnabled = ds.enabled;
			diagLevel = ds.level as 'full' | 'metadata';
			diagReporterKey = ds.reporter_key;
			diagAutoReportEnabled = ds.auto_report.enabled;
			diagAutoReportUrl = ds.auto_report.url;
			diagOnError = ds.auto_report.on_error;
			diagOnSessionEnd = ds.auto_report.on_session_end;
		} catch (e: any) {
			loadError = e.message;
		}
	});

	async function loadVectorStats() {
		vectorStatsLoading = true;
		try {
			vectorStats = await getVectorStats();
		} catch (e: any) {
			addToast(`Failed to load vector stats: ${e.message}`, 'error');
		} finally {
			vectorStatsLoading = false;
		}
	}

	async function handleReindex() {
		reindexing = true;
		try {
			const result: ReindexResponse = await reindex();
			addToast(`Reindexed ${result.entities} entities, ${result.connections} connections in ${result.duration_ms}ms`, 'success');
		} catch (e: any) {
			addToast(`Reindex failed: ${e.message}`, 'error');
		} finally {
			reindexing = false;
		}
	}

	async function handleReembed() {
		reembedding = true;
		try {
			const result = await reindexExchanges();
			addToast(`Re-embedded ${result.embedded}/${result.total_exchanges} exchanges (${result.failed} failed)`, 'success');
		} catch (e: any) {
			addToast(`Re-embed failed: ${e.message}`, 'error');
		} finally {
			reembedding = false;
		}
	}

	async function save(section: string, body: Record<string, unknown>) {
		saveStatus[section] = 'saving';
		try {
			await updateConfig({ [section]: body });
			saveStatus[section] = 'saved';
			setTimeout(() => { saveStatus[section] = 'idle'; }, 2000);
		} catch {
			saveStatus[section] = 'error';
			setTimeout(() => { saveStatus[section] = 'idle'; }, 3000);
		}
	}

	function saveServer() {
		save('server', { host: serverHost, port: serverPort });
	}
	function saveLlm() {
		save('llm', {
			models: {
				npc_reactions: llmNpcReactions,
				response_analysis: llmAnalysis,
				card_generation: llmCardGen,
				embeddings: llmEmbeddings,
			},
			fallback_model: llmFallback,
			mode: { chat: llmChatMode },
			temperatures: {
				chat: tempChat,
				chat_regenerate_bump: tempRegenBump,
				npc_reactions: tempNpc,
				summary: tempSummary,
			},
		});
		save('chat', { model: llmChat.trim() || null, max_variants: chatMaxVariants });
		save('rp', { default_pov_character: llmDefaultPov });
	}
	function resetTemperatures() {
		tempChat = 0.7;
		tempRegenBump = 0.05;
		tempNpc = 0.6;
		tempSummary = 0.5;
	}
	function saveSearch() {
		save('search', {
			vector_weight: searchVectorWeight,
			bm25_weight: searchBm25Weight,
			similarity_threshold: searchThreshold,
			chunk_size: searchChunkSize,
			chunk_overlap: searchChunkOverlap,
		});
	}
	function saveTrust() {
		save('trust', {
			increase_value: trustIncrease,
			decrease_value: trustDecrease,
			session_max_gain: trustSessionGain,
			session_max_loss: trustSessionLoss,
			min_score: trustMin,
			max_score: trustMax,
		});
	}
	function saveContext() {
		save('context', {
			max_documents: contextMaxDocs,
			max_graph_hops: contextMaxHops,
			stale_threshold_turns: contextStaleAfter,
		});
	}
	async function toggleActiveRP() {
		activeRPToggling = true;
		try {
			const result = await setActiveRP(!activeRP);
			activeRP = result.active_rp;
			activeRPSessions = result.session_count;
		} catch {
			// revert on failure
		} finally {
			activeRPToggling = false;
		}
	}

	async function saveDiagnostics() {
		diagLoading = true;
		diagWarning = '';
		try {
			const result = await updateDiagnostics({
				enabled: diagEnabled,
				level: diagLevel,
				reporter_key: diagReporterKey || undefined,
				auto_report: {
					enabled: diagAutoReportEnabled,
					url: diagAutoReportUrl,
					on_error: diagOnError,
					on_session_end: diagOnSessionEnd,
				},
			});
			if (result.warning) diagWarning = result.warning;
			diagStatus = await getDiagnosticStatus();
			addToast('Diagnostics settings saved', 'success');
		} catch (e: any) {
			addToast(`Failed to save diagnostics: ${e.message}`, 'error');
		} finally {
			diagLoading = false;
		}
	}

	async function handleClearDiagnostics() {
		diagClearing = true;
		try {
			const result = await clearDiagnostics();
			addToast(`Cleared ${result.files_removed} log file(s)`, 'success');
			diagStatus = await getDiagnosticStatus();
		} catch (e: any) {
			addToast(`Failed to clear logs: ${e.message}`, 'error');
		} finally {
			diagClearing = false;
		}
	}

	async function handleDownloadDiagnostics() {
		diagDownloading = true;
		try {
			await downloadDiagnostics();
			addToast('Diagnostics downloaded', 'success');
		} catch (e: any) {
			addToast(`Download failed: ${e.message}`, 'error');
		} finally {
			diagDownloading = false;
		}
	}

	async function handleSendReport() {
		diagSending = true;
		try {
			const result = await sendDiagnosticReport();
			if (result.ok) {
				addToast('Report sent successfully', 'success');
			} else {
				addToast(`Report failed: ${result.error}`, 'error');
			}
		} catch (e: any) {
			addToast(`Report failed: ${e.message}`, 'error');
		} finally {
			diagSending = false;
		}
	}

	async function saveApiKey() {
		if (!openrouterKey.trim()) return;
		saveStatus['apikeys'] = 'saving';
		try {
			await updateConfig({ openrouter_api_key: openrouterKey.trim() });
			openrouterKey = '';
			saveStatus['apikeys'] = 'saved';
			setTimeout(() => { saveStatus['apikeys'] = 'idle'; }, 2000);
		} catch {
			saveStatus['apikeys'] = 'error';
			setTimeout(() => { saveStatus['apikeys'] = 'idle'; }, 3000);
		}
	}

	async function handleTestProvider() {
		providerTesting = true;
		providerTestResult = null;
		try {
			providerTestResult = await testProvider(configData?.llm.provider ?? 'openrouter');
		} catch (e: any) {
			providerTestResult = {
				provider: configData?.llm.provider ?? 'openrouter',
				status: 'error',
				latency_ms: null,
				error: e.message ?? 'Connection failed',
			};
		} finally {
			providerTesting = false;
		}
	}
</script>

<div class="flex gap-3">
	<SideNav title="Settings" items={sections} bind:active={activeSection} />

	<!-- Section content -->
	<div class="flex-1 min-w-0 max-w-2xl">

		{#if loadError}
			<div class="bg-error/10 border border-error/30 rounded-[10px] px-4 py-2.5 text-sm text-error mb-4">
				Failed to load config: {loadError}
			</div>
		{/if}

		<!-- General -->
		{#if activeSection === 'general'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">General</h2>
					<p class="text-xs text-text-dim mt-0.5">Global engine settings.</p>
				</div>
				<div class="p-4 space-y-4">
					<div class="flex items-center justify-between">
						<div>
							<p class="text-sm font-medium text-text">Active RP</p>
							<p class="text-xs text-text-dim mt-0.5">
								When disabled, chat exchanges are not auto-saved. Use this while editing the UI or testing to avoid polluting your chat logs.
							</p>
							{#if activeRPSessions > 0}
								<p class="text-xs text-text-dim mt-1">{activeRPSessions} active session{activeRPSessions !== 1 ? 's' : ''}</p>
							{/if}
						</div>
						<button
							onclick={toggleActiveRP}
							disabled={activeRPToggling}
							class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200
								focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg
								disabled:opacity-50 disabled:cursor-not-allowed
								{activeRP ? 'bg-accent' : 'bg-bg-subtle'}"
							role="switch"
							aria-checked={activeRP}
							aria-label="Toggle active RP auto-save"
						>
							<span
								class="pointer-events-none inline-block h-5 w-5 rounded-full bg-surface-raised shadow transform transition duration-200
									{activeRP ? 'translate-x-5' : 'translate-x-0'}"
							></span>
						</button>
					</div>
				</div>
			</Card>

		<!-- Appearance -->
		{:else if activeSection === 'appearance'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Appearance</h2>
					<p class="text-xs text-text-dim mt-0.5">Customize the look and feel. Saved per-browser.</p>
				</div>
				<div class="p-4 space-y-6">
					<!-- Theme -->
					<div>
						<p class="text-xs font-medium text-text-dim mb-2">Theme</p>
						<div class="flex flex-wrap items-start gap-3">
							{#each THEME_PRESETS as theme}
								<button
									class="flex flex-col items-center gap-1.5 group"
									onclick={() => (selectedTheme = theme.id)}
									title={theme.label}
								>
									<div
										class="w-9 h-9 rounded-full border-2 transition-all shadow-sm
											{selectedTheme === theme.id ? 'border-text scale-110' : 'border-transparent hover:border-border-custom'}"
										style="background: linear-gradient(135deg, {theme.bg}, {theme.accent})"
									></div>
									<span class="text-[10px] text-text-dim group-hover:text-text transition-colors
										{selectedTheme === theme.id ? '!text-text font-medium' : ''}">{theme.label}</span>
								</button>
							{/each}
						</div>
					</div>

					<!-- Font Size -->
					<div>
						<p class="text-xs font-medium text-text-dim mb-2">Font Size</p>
						<div class="flex gap-2">
							{#each [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']] as [value, label]}
								<button
									class="px-4 py-2 rounded-[10px] text-sm transition-all border
										{fontSize === value
											? 'bg-accent/15 text-accent border-accent/30'
											: 'bg-bg-subtle text-text-dim border-border-custom/50 hover:text-text hover:border-border-custom'}"
									onclick={() => (fontSize = value as typeof fontSize)}
								>{label}</button>
							{/each}
						</div>
					</div>

					<!-- UI Density -->
					<div>
						<p class="text-xs font-medium text-text-dim mb-2">UI Density</p>
						<div class="flex gap-2">
							{#each [['compact', 'Compact'], ['comfortable', 'Comfortable'], ['spacious', 'Spacious']] as [value, label]}
								<button
									class="px-4 py-2 rounded-[10px] text-sm transition-all border
										{uiDensity === value
											? 'bg-accent/15 text-accent border-accent/30'
											: 'bg-bg-subtle text-text-dim border-border-custom/50 hover:text-text hover:border-border-custom'}"
									onclick={() => (uiDensity = value as typeof uiDensity)}
								>{label}</button>
							{/each}
						</div>
					</div>

					<div class="flex items-center justify-end gap-3 pt-2 border-t border-border-custom">
						<Btn primary onclick={saveAppearance}>Apply & Save</Btn>
					</div>
				</div>
			</Card>

		<!-- System Info -->
		{:else if activeSection === 'system'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">System</h2>
					<p class="text-xs text-text-dim mt-0.5">Read-only server and database information.</p>
				</div>
				<div class="p-4 space-y-3">
					{@render infoRow('Server Version', health?.version ?? '...')}
					{@render infoRow('Indexed Cards', health?.indexed_cards != null ? String(health.indexed_cards) : '...')}
					{@render infoRow('Vault Root', configData?.paths.vault_root ?? '...')}
					{@render infoRow('Database Path', configData?.paths.db_path ?? '...')}
					<div>
						<p class="text-xs font-medium text-text-dim mb-1">CORS Origins</p>
						{#if configData?.server.cors_origins?.length}
							<div class="flex flex-wrap gap-1.5">
								{#each configData.server.cors_origins as origin}
									<span class="px-2 py-0.5 bg-bg-subtle border border-border-custom rounded text-xs text-text font-mono">{origin}</span>
								{/each}
							</div>
						{:else}
							<p class="text-sm text-text-dim">None configured</p>
						{/if}
					</div>
				</div>
			</Card>

		<!-- Maintenance -->
		{:else if activeSection === 'maintenance'}
			<div class="space-y-4">
				<!-- Vector Stats -->
				<Card>
					<div class="px-4 py-3 border-b border-border-custom flex items-center justify-between">
						<div>
							<h2 class="text-sm font-semibold text-text">Vector Stats</h2>
							<p class="text-xs text-text-dim mt-0.5">Embedding and chunk statistics for the vector store.</p>
						</div>
						<button
							onclick={loadVectorStats}
							disabled={vectorStatsLoading}
							class="text-xs text-accent hover:text-accent/80 transition-colors disabled:opacity-50"
						>
							{vectorStatsLoading ? 'Loading...' : vectorStats ? 'Refresh' : 'Load Stats'}
						</button>
					</div>
					{#if vectorStats}
						<div class="p-4 grid grid-cols-2 gap-3">
							{@render statCell('Total Chunks', String(vectorStats.total_chunks))}
							{@render statCell('With Embeddings', String(vectorStats.chunks_with_embeddings))}
							{@render statCell('Without Embeddings', String(vectorStats.chunks_without_embeddings))}
							{@render statCell('Total Files', String(vectorStats.total_files))}
							{@render statCell('Avg Chunk Size', `${Math.round(vectorStats.avg_chunk_size)} chars`)}
							{@render statCell('Cards Without Vectors', String(vectorStats.cards_without_vectors.length))}
						</div>
						{#if vectorStats.cards_without_vectors.length > 0}
							<div class="px-4 pb-4">
								<p class="text-xs font-medium text-text-dim mb-1">Cards missing vectors:</p>
								<div class="flex flex-wrap gap-1">
									{#each vectorStats.cards_without_vectors as card}
										<span class="px-1.5 py-0.5 bg-warning/10 border border-warning/30 rounded text-xs text-warning">{card}</span>
									{/each}
								</div>
							</div>
						{/if}
					{:else if !vectorStatsLoading}
						<div class="p-4 text-sm text-text-dim">Click "Load Stats" to fetch vector store statistics.</div>
					{/if}
				</Card>

				<!-- Actions -->
				<Card>
					<div class="px-4 py-3 border-b border-border-custom">
						<h2 class="text-sm font-semibold text-text">Actions</h2>
						<p class="text-xs text-text-dim mt-0.5">Database maintenance operations.</p>
					</div>
					<div class="p-4 space-y-4">
						<div class="flex items-center justify-between">
							<div>
								<p class="text-sm font-medium text-text">Reindex Cards</p>
								<p class="text-xs text-text-dim mt-0.5">Re-scan all story card files and rebuild the index.</p>
							</div>
							<Btn primary small onclick={handleReindex} disabled={reindexing}>
								{reindexing ? 'Reindexing...' : 'Reindex'}
							</Btn>
						</div>
						<div class="border-t border-border-custom"></div>
						<div class="flex items-center justify-between">
							<div>
								<p class="text-sm font-medium text-text">Re-embed Exchanges</p>
								<p class="text-xs text-text-dim mt-0.5">Regenerate vector embeddings for all exchanges.</p>
							</div>
							<Btn primary small onclick={handleReembed} disabled={reembedding}>
								{reembedding ? 'Re-embedding...' : 'Re-embed'}
							</Btn>
						</div>
					</div>
				</Card>
			</div>

		<!-- Server -->
		{:else if activeSection === 'server'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Server</h2>
					<p class="text-xs text-text-dim mt-0.5">Changes to host or port require a server restart.</p>
				</div>
				<div class="p-4 space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="host">Host</label>
							<InputField id="host" bind:value={serverHost} />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="port">Port</label>
							<input id="port" type="number" bind:value={serverPort} min="1024" max="65535"
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
					</div>
					{@render saveButton('server', saveServer)}
				</div>
			</Card>

		<!-- LLM Models -->
		{:else if activeSection === 'llm'}
			<!-- Provider Status -->
			<Card>
				<div class="px-4 py-3 border-b border-border-custom flex items-center justify-between">
					<div>
						<h2 class="text-sm font-semibold text-text">Provider Status</h2>
						<p class="text-xs text-text-dim mt-0.5">Test connectivity to your configured LLM provider.</p>
					</div>
					<div class="flex items-center gap-3">
						{#if providerTestResult}
							<div class="flex items-center gap-1.5">
								<span
									class="h-2 w-2 rounded-full"
									style="background: {providerTestResult.status === 'ok' ? 'var(--color-success)' : 'var(--color-error)'};"
								></span>
								<span class="text-xs {providerTestResult.status === 'ok' ? 'text-success' : 'text-error'}">
									{providerTestResult.status === 'ok' ? 'Connected' : 'Error'}
								</span>
								{#if providerTestResult.latency_ms != null}
									<span class="text-[10px] text-text-dim">({Math.round(providerTestResult.latency_ms)}ms)</span>
								{/if}
							</div>
						{:else}
							<span class="h-2 w-2 rounded-full bg-text-dim/30"></span>
							<span class="text-xs text-text-dim">Not tested</span>
						{/if}
						<Btn small onclick={handleTestProvider} disabled={providerTesting}>
							{providerTesting ? 'Testing...' : 'Test Connection'}
						</Btn>
					</div>
				</div>
				{#if providerTestResult?.status === 'error' && providerTestResult.error}
					<div class="px-4 py-2 text-xs text-error bg-error/5">{providerTestResult.error}</div>
				{/if}
				<div class="px-4 py-3">
					<div class="flex items-center gap-2">
						<span class="text-xs text-text-dim">Provider:</span>
						<span class="text-xs font-medium text-text font-mono">{configData?.llm.provider ?? '...'}</span>
					</div>
				</div>
			</Card>

			<!-- Chat Backend -->
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Chat Backend</h2>
					<p class="text-xs text-text-dim mt-0.5">Controls which chat endpoint handles RP messages.</p>
				</div>
				<div class="p-4">
					<label class="block text-xs font-medium text-text-dim mb-1" for="llm-chat-mode">Mode</label>
					<select
						id="llm-chat-mode"
						class="w-full rounded border border-border-custom bg-bg-elevated px-3 py-2 text-sm text-text focus:outline-none focus:ring-1 focus:ring-accent"
						bind:value={llmChatMode}
					>
						<option value="provider">Provider (built-in LLM)</option>
						<option value="sdk">Agent SDK (Claude Code)</option>
					</select>
					<p class="text-[11px] text-text-dim/60 mt-1.5">
						{llmChatMode === 'sdk'
							? 'Claude orchestrates tool calls autonomously via Agent SDK.'
							: 'Chat goes through the configured LLM provider.'}
					</p>
				</div>
			</Card>

			<!-- Model Routing -->
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">LLM Models</h2>
					<p class="text-xs text-text-dim mt-0.5">Model IDs passed to provider. Use "provider:model" format for multi-provider routing.</p>
				</div>
				<div class="p-4 space-y-4">
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-npc">NPC Reactions</label>
						<InputField id="llm-npc" bind:value={llmNpcReactions} class="font-mono" />
					</div>
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-analysis">Response Analysis</label>
						<InputField id="llm-analysis" bind:value={llmAnalysis} class="font-mono" />
					</div>
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-cardgen">Card Generation</label>
						<InputField id="llm-cardgen" bind:value={llmCardGen} class="font-mono" />
					</div>
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-chat">Chat</label>
						<InputField id="llm-chat" bind:value={llmChat} placeholder="(uses fallback model)" class="font-mono" />
						<p class="text-[11px] text-text-dim/60 mt-1">Leave blank to use the fallback model for chat.</p>
					</div>
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-embed">Embeddings</label>
						<InputField id="llm-embed" bind:value={llmEmbeddings} class="font-mono" />
					</div>
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-fallback">Fallback Model</label>
						<InputField id="llm-fallback" bind:value={llmFallback} class="font-mono" />
					</div>
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="llm-pov">Default POV Character</label>
						<InputField id="llm-pov" bind:value={llmDefaultPov} />
					</div>
					{@render saveButton('llm', saveLlm)}
				</div>
			</Card>

			<!-- Temperatures -->
			<Card>
				<div class="px-4 py-3 border-b border-border-custom flex items-center justify-between">
					<div>
						<h2 class="text-sm font-semibold text-text">Temperatures</h2>
						<p class="text-xs text-text-dim mt-0.5">Lower = more consistent. Higher = more creative. Analytical roles are locked for accuracy.</p>
					</div>
					<button
						class="text-[11px] text-text-dim hover:text-accent transition-colors"
						onclick={resetTemperatures}
					>Reset to defaults</button>
				</div>
				<div class="p-4 space-y-4">
					<div>
						<div class="flex items-center justify-between mb-1">
							<label class="text-xs font-medium text-text-dim" for="temp-chat">Chat</label>
							<span class="text-xs font-mono text-text tabular-nums">{tempChat.toFixed(2)}</span>
						</div>
						<input id="temp-chat" type="range" min="0" max="1.5" step="0.05" bind:value={tempChat}
							class="w-full accent-accent" />
					</div>
					<div>
						<div class="flex items-center justify-between mb-1">
							<label class="text-xs font-medium text-text-dim" for="temp-npc">NPC Reactions</label>
							<span class="text-xs font-mono text-text tabular-nums">{tempNpc.toFixed(2)}</span>
						</div>
						<input id="temp-npc" type="range" min="0" max="1.5" step="0.05" bind:value={tempNpc}
							class="w-full accent-accent" />
					</div>
					<div>
						<div class="flex items-center justify-between mb-1">
							<label class="text-xs font-medium text-text-dim" for="temp-summary">Summary</label>
							<span class="text-xs font-mono text-text tabular-nums">{tempSummary.toFixed(2)}</span>
						</div>
						<input id="temp-summary" type="range" min="0" max="1" step="0.05" bind:value={tempSummary}
							class="w-full accent-accent" />
					</div>
					<div>
						<div class="flex items-center justify-between mb-1">
							<label class="text-xs font-medium text-text-dim" for="temp-regen-bump">Regenerate Bump</label>
							<span class="text-xs font-mono text-text tabular-nums">+{tempRegenBump.toFixed(2)}</span>
						</div>
						<input id="temp-regen-bump" type="range" min="0" max="0.3" step="0.01" bind:value={tempRegenBump}
							class="w-full accent-accent" />
					</div>
					<div>
						<div class="flex items-center justify-between mb-1">
							<label class="text-xs font-medium text-text-dim" for="max-variants">Max Regenerations</label>
							<span class="text-xs font-mono text-text tabular-nums">{chatMaxVariants}</span>
						</div>
						<input id="max-variants" type="range" min="3" max="50" step="1" bind:value={chatMaxVariants}
							class="w-full accent-accent" />
						<p class="text-xs text-text-dim mt-1">Maximum number of swipe variants per exchange</p>
					</div>
					{@render saveButton('llm', saveLlm)}
				</div>
			</Card>

		<!-- Thread Pacing -->
		{:else if activeSection === 'pacing'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Thread Pacing</h2>
					<p class="text-xs text-text-dim mt-0.5">Controls how quickly plot threads escalate. Set per-RP in guidelines (scene_pacing field).</p>
				</div>
				<div class="p-4 space-y-4">
					<div>
						<label class="block text-xs font-medium text-text-dim mb-2" for="pacing-mode">Pacing Mode</label>
						<div class="flex gap-2">
							{#each ['slow', 'moderate', 'fast'] as mode}
								<button
									class="px-4 py-2 rounded-[10px] text-sm transition-all border capitalize
										{scenePacing === mode
											? 'bg-accent/15 text-accent border-accent/30'
											: 'bg-bg-subtle text-text-dim border-border-custom/50 hover:text-text hover:border-border-custom'}"
									onclick={() => (scenePacing = mode as typeof scenePacing)}
								>{mode}</button>
							{/each}
						</div>
					</div>

					<div>
						<p class="text-xs font-medium text-text-dim mb-2">Default Thresholds</p>
						<p class="text-[11px] text-text-dim/60 mb-3">Number of exchanges before each thread escalation level triggers.</p>
						<div class="grid grid-cols-3 gap-3">
							{#each ['gentle', 'moderate', 'strong'] as level}
								{@const value = PACING_DEFAULTS[scenePacing]?.[level as keyof typeof PACING_DEFAULTS['moderate']] ?? 0}
								<div class="bg-bg-subtle rounded-md px-3 py-2.5 text-center">
									<p class="text-[11px] text-text-dim capitalize">{level}</p>
									<p class="text-lg font-semibold text-text mt-0.5">{value}</p>
									<p class="text-[10px] text-text-dim/60">exchanges</p>
								</div>
							{/each}
						</div>
					</div>

					<p class="text-[11px] text-text-dim/60 pt-2 border-t border-border-custom">
						Thread thresholds are read-only here. Set scene_pacing in your RP's guidelines to control which preset is used.
					</p>
				</div>
			</Card>

		<!-- Search Tuning -->
		{:else if activeSection === 'search'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Search Tuning</h2>
					<p class="text-xs text-text-dim mt-0.5">Controls chunking and retrieval weights. Requires server restart to take effect. Chunk size changes also require reindexing.</p>
				</div>
				<div class="p-4 space-y-4">
					<div class="grid grid-cols-3 gap-4">
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="vec-weight">Vector Weight</label>
							<input id="vec-weight" type="number" bind:value={searchVectorWeight}
								min="0" max="1" step="0.05"
								class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
									focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="bm25-weight">BM25 Weight</label>
							<input id="bm25-weight" type="number" bind:value={searchBm25Weight}
								min="0" max="1" step="0.05"
								class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
									focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="threshold">Similarity Threshold</label>
							<input id="threshold" type="number" bind:value={searchThreshold}
								min="0" max="1" step="0.05"
								class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
									focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
					</div>
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="chunk-size">Chunk Size (chars)</label>
							<input id="chunk-size" type="number" bind:value={searchChunkSize} min="100" step="50"
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="chunk-overlap">Chunk Overlap (chars)</label>
							<input id="chunk-overlap" type="number" bind:value={searchChunkOverlap} min="0" step="50"
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
					</div>
					{@render saveButton('search', saveSearch)}
				</div>
			</Card>

		<!-- Trust Tuning -->
		{:else if activeSection === 'trust'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Trust Tuning</h2>
					<p class="text-xs text-text-dim mt-0.5">Trust score changes per interaction. Requires server restart to take effect.</p>
				</div>
				<div class="p-4 space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="trust-inc">Increase Per Event</label>
							<input id="trust-inc" type="number" bind:value={trustIncrease}
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="trust-dec">Decrease Per Event</label>
							<input id="trust-dec" type="number" bind:value={trustDecrease}
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="gain-cap">Session Gain Cap</label>
							<input id="gain-cap" type="number" bind:value={trustSessionGain}
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="loss-cap">Session Loss Cap</label>
							<input id="loss-cap" type="number" bind:value={trustSessionLoss}
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="trust-min">Min Score</label>
							<input id="trust-min" type="number" bind:value={trustMin}
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="trust-max">Max Score</label>
							<input id="trust-max" type="number" bind:value={trustMax}
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
					</div>
					{@render saveButton('trust', saveTrust)}
				</div>
			</Card>

		<!-- Context Tuning -->
		{:else if activeSection === 'context'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">Context Tuning</h2>
					<p class="text-xs text-text-dim mt-0.5">Controls how much context the engine assembles per request. Requires server restart to take effect.</p>
				</div>
				<div class="p-4 space-y-4">
					<div class="grid grid-cols-3 gap-4">
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="max-docs">Max Documents</label>
							<input id="max-docs" type="number" bind:value={contextMaxDocs} min="1" max="50"
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="max-hops">Max Graph Hops</label>
							<input id="max-hops" type="number" bind:value={contextMaxHops} min="1" max="5"
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="stale">Stale After (exchanges)</label>
							<input id="stale" type="number" bind:value={contextStaleAfter} min="1"
							class="w-full bg-bg-subtle border border-border-custom rounded-md px-3 py-2 text-sm text-text
								focus:outline-none focus:ring-1 focus:ring-accent" />
						</div>
					</div>
					{@render saveButton('context', saveContext)}
				</div>
			</Card>

		<!-- Diagnostics -->
		{:else if activeSection === 'diagnostics'}
			<div class="space-y-4">
				<Card>
					<div class="px-4 py-3 border-b border-border-custom">
						<h2 class="text-sm font-semibold text-text">Diagnostic Logging</h2>
						<p class="text-xs text-text-dim mt-0.5">Structured logging for debugging. Writes JSONL files to the data directory.</p>
					</div>
					<div class="p-4 space-y-4">
						<!-- Enable toggle -->
						<div class="flex items-center justify-between">
							<div>
								<p class="text-sm font-medium text-text">Enable Diagnostics</p>
								<p class="text-xs text-text-dim mt-0.5">Log all requests, LLM calls, context assembly, analysis, and trust changes.</p>
							</div>
							<Toggle bind:checked={diagEnabled} />
						</div>

						<!-- Log level -->
						<div>
							<p class="text-xs font-medium text-text-dim mb-2">Log Level</p>
							<div class="flex gap-2">
								{#each [['metadata', 'Metadata Only'], ['full', 'Full Content']] as [value, label]}
									<button
										class="px-4 py-2 rounded-[10px] text-sm transition-all border
											{diagLevel === value
												? 'bg-accent/15 text-accent border-accent/30'
												: 'bg-bg-subtle text-text-dim border-border-custom/50 hover:text-text hover:border-border-custom'}"
										onclick={() => (diagLevel = value as typeof diagLevel)}
									>{label}</button>
								{/each}
							</div>
							{#if diagLevel === 'full'}
								<p class="text-xs text-warning mt-2">Full content logging records message text, prompts, and LLM responses. Switch to "Metadata Only" if this data is sensitive.</p>
							{/if}
						</div>

						{#if diagWarning}
							<div class="bg-warning/10 border border-warning/30 rounded-[10px] px-3 py-2 text-xs text-warning">
								{diagWarning}
							</div>
						{/if}

						<!-- Reporter key -->
						<div>
							<label class="block text-xs font-medium text-text-dim mb-1" for="diag-key">Reporter Name</label>
							<InputField id="diag-key" bind:value={diagReporterKey} placeholder="Auto-generated if empty" />
							<p class="text-xs text-text-dim mt-0.5">Identifies this instance in auto-reports. Auto-generated if blank.</p>
						</div>

						{@render saveButton('diagnostics', saveDiagnostics)}
					</div>
				</Card>

				<!-- Status -->
				{#if diagStatus}
					<Card>
						<div class="px-4 py-3 border-b border-border-custom">
							<h2 class="text-sm font-semibold text-text">Log Status</h2>
						</div>
						<div class="p-4 space-y-3">
							<div class="grid grid-cols-3 gap-3">
								{@render statCell('Entries', String(diagStatus.entry_count))}
								{@render statCell('File Size', diagStatus.file_size_bytes > 0 ? `${(diagStatus.file_size_bytes / 1024).toFixed(1)} KB` : '0 B')}
								{@render statCell('Archives', String(diagStatus.archive_count))}
							</div>
							{#if diagStatus.last_entry_ts}
								{@render infoRow('Last Entry', new Date(diagStatus.last_entry_ts).toLocaleString())}
							{/if}
							<div class="flex items-center gap-2 pt-2 border-t border-border-custom">
								<Btn small onclick={handleDownloadDiagnostics} disabled={diagDownloading || diagStatus.entry_count === 0}>
									{diagDownloading ? 'Downloading...' : 'Download Logs'}
								</Btn>
								<Btn small onclick={handleClearDiagnostics} disabled={diagClearing || diagStatus.entry_count === 0}>
									{diagClearing ? 'Clearing...' : 'Clear Logs'}
								</Btn>
							</div>
						</div>
					</Card>
				{/if}

				<!-- Auto-Report -->
				<Card>
					<div class="px-4 py-3 border-b border-border-custom">
						<h2 class="text-sm font-semibold text-text">Auto-Report</h2>
						<p class="text-xs text-text-dim mt-0.5">Automatically send logs to a webhook on errors or session end.</p>
					</div>
					<div class="p-4 space-y-4">
						<div class="flex items-center justify-between">
							<p class="text-sm font-medium text-text">Enable Auto-Report</p>
							<Toggle bind:checked={diagAutoReportEnabled} />
						</div>
						{#if diagAutoReportEnabled}
							<div>
								<label class="block text-xs font-medium text-text-dim mb-1" for="diag-url">Webhook URL</label>
								<InputField id="diag-url" bind:value={diagAutoReportUrl} placeholder="https://..." />
							</div>
							<div class="flex items-center gap-6">
								<label class="flex items-center gap-2 text-sm text-text cursor-pointer">
									<input type="checkbox" bind:checked={diagOnError} class="accent-accent" />
									On unhandled errors
								</label>
								<label class="flex items-center gap-2 text-sm text-text cursor-pointer">
									<input type="checkbox" bind:checked={diagOnSessionEnd} class="accent-accent" />
									On session end
								</label>
							</div>
							<div class="flex items-center gap-2 pt-2 border-t border-border-custom">
								<Btn small onclick={handleSendReport} disabled={diagSending || !diagAutoReportUrl}>
									{diagSending ? 'Sending...' : 'Send Report Now'}
								</Btn>
							</div>
						{/if}
						{@render saveButton('diagnostics', saveDiagnostics)}
					</div>
				</Card>
			</div>

		<!-- API Keys -->
		{:else if activeSection === 'apikeys'}
			<Card>
				<div class="px-4 py-3 border-b border-border-custom">
					<h2 class="text-sm font-semibold text-text">API Keys</h2>
					<p class="text-xs text-text-dim mt-0.5">Keys are write-only — values are never sent to the browser.</p>
				</div>
				<div class="p-4 space-y-4">
					<div>
						<label class="block text-xs font-medium text-text-dim mb-1" for="or-key">OpenRouter API Key</label>
						<div class="flex gap-2">
							{#if keyVisible}
								<InputField id="or-key" bind:value={openrouterKey}
									placeholder="sk-or-..." class="flex-1 font-mono" />
							{:else}
								<InputField id="or-key" type="password" bind:value={openrouterKey}
									placeholder="Enter new key to update..." class="flex-1 font-mono" />
							{/if}
							<Btn small onclick={() => (keyVisible = !keyVisible)}>
								{keyVisible ? 'Hide' : 'Show'}
							</Btn>
						</div>
						<p class="text-xs text-text-dim mt-1.5">Saved to .env only. Used for all LLM and embedding calls via OpenRouter.</p>
					</div>
					{@render saveButton('apikeys', saveApiKey)}
				</div>
			</Card>
		{/if}
	</div>
</div>

{#snippet infoRow(label: string, value: string)}
	<div class="flex items-baseline justify-between py-1">
		<span class="text-xs font-medium text-text-dim">{label}</span>
		<span class="text-sm text-text font-mono">{value}</span>
	</div>
{/snippet}

{#snippet statCell(label: string, value: string)}
	<div class="bg-bg-subtle rounded-md px-3 py-2">
		<p class="text-xs text-text-dim">{label}</p>
		<p class="text-sm font-medium text-text mt-0.5">{value}</p>
	</div>
{/snippet}

{#snippet saveButton(section: string, onSave: () => void)}
	<div class="flex items-center justify-end gap-3 pt-2 border-t border-border-custom">
		{#if saveStatus[section] === 'saved'}
			<span class="text-xs text-success">Saved</span>
		{:else if saveStatus[section] === 'error'}
			<span class="text-xs text-error">Save failed</span>
		{/if}
		<Btn primary onclick={onSave} disabled={saveStatus[section] === 'saving'}>
			{saveStatus[section] === 'saving' ? 'Saving...' : 'Save'}
		</Btn>
	</div>
{/snippet}
