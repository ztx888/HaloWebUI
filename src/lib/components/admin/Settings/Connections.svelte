<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	const dispatch = createEventDispatcher();

	import { getOllamaConfig, updateOllamaConfig } from '$lib/apis/ollama';
	import { getOpenAIConfig, updateOpenAIConfig } from '$lib/apis/openai';
	import { getGeminiConfig, updateGeminiConfig } from '$lib/apis/gemini';
	import { getGrokConfig, updateGrokConfig } from '$lib/apis/grok';
	import { getAnthropicConfig, updateAnthropicConfig } from '$lib/apis/anthropic';
	import { getBackendConfig } from '$lib/apis';
	import { getConnectionsConfig, setConnectionsConfig } from '$lib/apis/configs';
	import { refreshModels as refreshModelsStore } from '$lib/services/models';
	import { revealExpandedSection } from '$lib/utils/expanded-section-scroll';

	import {
		config,
		settings,
		user,
		ollamaConfigCache,
		openaiConfigCache,
		geminiConfigCache,
		grokConfigCache,
		anthropicConfigCache,
		connectionsConfigCache
	} from '$lib/stores';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ModelIcon from '$lib/components/common/ModelIcon.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	import OpenAIConnection from './Connections/OpenAIConnection.svelte';
	import GeminiConnection from './Connections/GeminiConnection.svelte';
	import GrokConnection from './Connections/GrokConnection.svelte';
	import AnthropicConnection from './Connections/AnthropicConnection.svelte';
	import AddConnectionModal from '$lib/components/AddConnectionModal.svelte';
	import OllamaConnection from './Connections/OllamaConnection.svelte';

	const i18n: Writable<any> = getContext('i18n');

	// Connections can grow very large; allow folding like the Documents settings page.
	let expandedSections = {
		openai: true,
		gemini: true,
		grok: true,
		anthropic: true,
		ollama: true,
		advanced: true
	};
	let expandedSectionsInitialized = false;

	let sectionEl_openai: HTMLElement;
	let sectionEl_gemini: HTMLElement;
	let sectionEl_grok: HTMLElement;
	let sectionEl_anthropic: HTMLElement;
	let sectionEl_ollama: HTMLElement;
	let sectionEl_advanced: HTMLElement;

	const GROK_PROVIDER_ICON = '/static/connection-avatars/xai.svg';

	type SectionKey = keyof typeof expandedSections;
	const revealIfExpanded = async (section: SectionKey, sectionEl: HTMLElement | undefined) => {
		if (expandedSections[section]) {
			await revealExpandedSection(sectionEl);
		}
	};

	const AUTO_EXPAND_MAX_CONNECTIONS = 2;
	const getConnectionRenderKey = (url: string, key: string | undefined, config: any) =>
		config ?? `${url}::${key ?? ''}`;
	const getOllamaRenderKey = (url: string, config: any) => config ?? url;
	const SETTINGS_CONFLICT_DETAIL =
		'User settings were updated elsewhere. Please retry with the latest settings.';
	const formatSettingsSaveError = (error: unknown) => {
		const message =
			error instanceof Error
				? error.message
				: typeof error === 'string'
					? error
					: `${(error as any)?.detail ?? error ?? ''}`;
		return message.includes(SETTINGS_CONFLICT_DETAIL)
			? $i18n.t(
					'Settings changed in another tab. The latest settings have been reloaded; please review and save again.'
				)
			: message;
	};

	const initExpandedSections = () => {
		if (expandedSectionsInitialized) return;

		// Auto-collapse sections with many connections to avoid an excessively tall page.
		expandedSections.openai =
			OPENAI_API_BASE_URLS.length > 0 && OPENAI_API_BASE_URLS.length <= AUTO_EXPAND_MAX_CONNECTIONS;
		expandedSections.gemini =
			GEMINI_API_BASE_URLS.length > 0 && GEMINI_API_BASE_URLS.length <= AUTO_EXPAND_MAX_CONNECTIONS;
		expandedSections.grok =
			GROK_API_BASE_URLS.length > 0 && GROK_API_BASE_URLS.length <= AUTO_EXPAND_MAX_CONNECTIONS;
		expandedSections.anthropic =
			ANTHROPIC_API_BASE_URLS.length > 0 &&
			ANTHROPIC_API_BASE_URLS.length <= AUTO_EXPAND_MAX_CONNECTIONS;
		expandedSections.ollama =
			OLLAMA_BASE_URLS.length > 0 && OLLAMA_BASE_URLS.length <= AUTO_EXPAND_MAX_CONNECTIONS;
		expandedSections.advanced = true;

		expandedSectionsInitialized = true;
	};

	// External
	let OLLAMA_BASE_URLS = [''];
	let OLLAMA_API_CONFIGS: any = {};

	let OPENAI_API_KEYS = [''];
	let OPENAI_API_BASE_URLS = [''];
	let OPENAI_API_CONFIGS: any = {};

	let GEMINI_API_KEYS = [''];
	let GEMINI_API_BASE_URLS = [''];
	let GEMINI_API_CONFIGS: any = {};

	let GROK_API_KEYS = [''];
	let GROK_API_BASE_URLS = [''];
	let GROK_API_CONFIGS: any = {};

	let ANTHROPIC_API_KEYS = [''];
	let ANTHROPIC_API_BASE_URLS = [''];
	let ANTHROPIC_API_CONFIGS: any = {};

	let ENABLE_OPENAI_API: null | boolean = null;
	let ENABLE_OLLAMA_API: null | boolean = null;
	let ENABLE_GEMINI_API: null | boolean = null;
	let ENABLE_GROK_API: null | boolean = null;
	let ENABLE_ANTHROPIC_API: null | boolean = null;

	let connectionsConfig: any = null;

	let showAddOpenAIConnectionModal = false;
	let showAddGeminiConnectionModal = false;
	let showAddGrokConnectionModal = false;
	let showAddAnthropicConnectionModal = false;
	let showAddOllamaConnectionModal = false;

	let showDisableBaseModelsCacheConfirm = false;
	let suppressBaseModelsCacheSave = false;

	let showDisableOpenAIAPIConfirm = false;
	let showDisableGeminiAPIConfirm = false;
	let showDisableGrokAPIConfirm = false;
	let showDisableAnthropicAPIConfirm = false;
	let showDisableOllamaAPIConfirm = false;

	// Version counters to prevent stale responses from overwriting newer state
	let openaiUpdateVersion = 0;
	let ollamaUpdateVersion = 0;
	let geminiUpdateVersion = 0;
	let grokUpdateVersion = 0;
	let anthropicUpdateVersion = 0;

	const updateOpenAIHandler = async (refreshModels = true) => {
		if (ENABLE_OPENAI_API === null) return false;

		const thisVersion = ++openaiUpdateVersion;

		// Remove trailing slashes
		OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS.map((url) => url.replace(/\/$/, ''));

		// Check if API KEYS length is same than API URLS length
		if (OPENAI_API_KEYS.length !== OPENAI_API_BASE_URLS.length) {
			// if there are more keys than urls, remove the extra keys
			if (OPENAI_API_KEYS.length > OPENAI_API_BASE_URLS.length) {
				OPENAI_API_KEYS = OPENAI_API_KEYS.slice(0, OPENAI_API_BASE_URLS.length);
			}

			// if there are more urls than keys, add empty keys
			if (OPENAI_API_KEYS.length < OPENAI_API_BASE_URLS.length) {
				const diff = OPENAI_API_BASE_URLS.length - OPENAI_API_KEYS.length;
				for (let i = 0; i < diff; i++) {
					OPENAI_API_KEYS.push('');
				}
			}
		}

		const res = await updateOpenAIConfig(localStorage.token, {
			ENABLE_OPENAI_API: ENABLE_OPENAI_API,
			OPENAI_API_BASE_URLS: OPENAI_API_BASE_URLS,
			OPENAI_API_KEYS: OPENAI_API_KEYS,
			OPENAI_API_CONFIGS: OPENAI_API_CONFIGS
		}).catch((error) => {
			toast.error(formatSettingsSaveError(error));
			return null;
		});

		// A newer request was fired while this one was in-flight; discard stale response
		if (thisVersion !== openaiUpdateVersion) return false;
		if (!res) return false;

		// Server may normalize configs (e.g. prefix_id uniqueness, default names).
		ENABLE_OPENAI_API = res?.ENABLE_OPENAI_API ?? ENABLE_OPENAI_API;
		OPENAI_API_BASE_URLS = res?.OPENAI_API_BASE_URLS ?? OPENAI_API_BASE_URLS;
		OPENAI_API_KEYS = res?.OPENAI_API_KEYS ?? OPENAI_API_KEYS;
		OPENAI_API_CONFIGS = res?.OPENAI_API_CONFIGS ?? OPENAI_API_CONFIGS;

		// Legacy support, url as key
		for (const [idx, url] of OPENAI_API_BASE_URLS.entries()) {
			if (!OPENAI_API_CONFIGS[idx]) {
				OPENAI_API_CONFIGS[idx] = OPENAI_API_CONFIGS[url] || {};
			}
		}

		// Update cache with normalized config
		openaiConfigCache.set({
			ENABLE_OPENAI_API,
			OPENAI_API_BASE_URLS,
			OPENAI_API_KEYS,
			OPENAI_API_CONFIGS
		});
		toast.success($i18n.t('OpenAI API settings updated'));
		if (refreshModels) {
			await refreshModelsStore(localStorage.token, { force: true, reason: 'admin-connections' });
		}

		return true;
	};

	const updateOllamaHandler = async (refreshModels = true) => {
		if (ENABLE_OLLAMA_API === null) return false;

		const thisVersion = ++ollamaUpdateVersion;

		// Remove trailing slashes
		OLLAMA_BASE_URLS = OLLAMA_BASE_URLS.map((url) => url.replace(/\/$/, ''));

		const res = await updateOllamaConfig(localStorage.token, {
			ENABLE_OLLAMA_API: ENABLE_OLLAMA_API,
			OLLAMA_BASE_URLS: OLLAMA_BASE_URLS,
			OLLAMA_API_CONFIGS: OLLAMA_API_CONFIGS
		}).catch((error) => {
			toast.error(formatSettingsSaveError(error));
			return null;
		});

		// A newer request was fired while this one was in-flight; discard stale response
		if (thisVersion !== ollamaUpdateVersion) return false;
		if (!res) return false;

		// Server may normalize configs (e.g. prefix_id uniqueness, default names).
		ENABLE_OLLAMA_API = res?.ENABLE_OLLAMA_API ?? ENABLE_OLLAMA_API;
		OLLAMA_BASE_URLS = res?.OLLAMA_BASE_URLS ?? OLLAMA_BASE_URLS;
		OLLAMA_API_CONFIGS = res?.OLLAMA_API_CONFIGS ?? OLLAMA_API_CONFIGS;

		// Legacy support, url as key
		for (const [idx, url] of OLLAMA_BASE_URLS.entries()) {
			if (!OLLAMA_API_CONFIGS[idx]) {
				OLLAMA_API_CONFIGS[idx] = OLLAMA_API_CONFIGS[url] || {};
			}
		}

		// Update cache with normalized config
		ollamaConfigCache.set({
			ENABLE_OLLAMA_API,
			OLLAMA_BASE_URLS,
			OLLAMA_API_CONFIGS
		});
		toast.success($i18n.t('Ollama API settings updated'));
		if (refreshModels) {
			await refreshModelsStore(localStorage.token, { force: true, reason: 'admin-connections' });
		}

		return true;
	};

	const updateConnectionsHandler = async () => {
		const res = await setConnectionsConfig(localStorage.token, connectionsConfig).catch((error) => {
			toast.error(formatSettingsSaveError(error));
		});

		if (res) {
			connectionsConfigCache.set(null);
			toast.success($i18n.t('Connections settings updated'));
			await refreshModelsStore(localStorage.token, { force: true, reason: 'admin-connections' });
			await config.set(await getBackendConfig());
		}
	};

	const baseModelsCacheChangeHandler = async (enabled: boolean) => {
		if (suppressBaseModelsCacheSave) {
			suppressBaseModelsCacheSave = false;
			return;
		}

		if (!enabled) {
			// Don't actually disable until the user confirms; immediately revert the UI state.
			suppressBaseModelsCacheSave = true;
			connectionsConfig.ENABLE_BASE_MODELS_CACHE = true;
			showDisableBaseModelsCacheConfirm = true;
			return;
		}

		await updateConnectionsHandler();
	};

	const addOpenAIConnectionHandler = async (connection: any) => {
		expandedSections.openai = true;
		OPENAI_API_BASE_URLS = [...OPENAI_API_BASE_URLS, connection.url];
		OPENAI_API_KEYS = [...OPENAI_API_KEYS, connection.key];
		OPENAI_API_CONFIGS[OPENAI_API_BASE_URLS.length - 1] = connection.config;

		const ok = await updateOpenAIHandler(!!ENABLE_OPENAI_API);
		if (!ok) {
			throw new Error($i18n.t('Failed to save connections'));
		}
	};

	const addOllamaConnectionHandler = async (connection: any) => {
		expandedSections.ollama = true;
		OLLAMA_BASE_URLS = [...OLLAMA_BASE_URLS, connection.url];
		OLLAMA_API_CONFIGS[OLLAMA_BASE_URLS.length - 1] = {
			...connection.config,
			key: connection.key
		};

		const ok = await updateOllamaHandler(!!ENABLE_OLLAMA_API);
		if (!ok) {
			throw new Error($i18n.t('Failed to save connections'));
		}
	};

	const updateGeminiHandler = async (refreshModels = true) => {
		if (ENABLE_GEMINI_API === null) return false;

		const thisVersion = ++geminiUpdateVersion;

		// Remove trailing slashes
		GEMINI_API_BASE_URLS = GEMINI_API_BASE_URLS.map((url) => url.replace(/\/$/, ''));

		// Check if API KEYS length is same than API URLS length
		if (GEMINI_API_KEYS.length !== GEMINI_API_BASE_URLS.length) {
			if (GEMINI_API_KEYS.length > GEMINI_API_BASE_URLS.length) {
				GEMINI_API_KEYS = GEMINI_API_KEYS.slice(0, GEMINI_API_BASE_URLS.length);
			}
			if (GEMINI_API_KEYS.length < GEMINI_API_BASE_URLS.length) {
				const diff = GEMINI_API_BASE_URLS.length - GEMINI_API_KEYS.length;
				for (let i = 0; i < diff; i++) {
					GEMINI_API_KEYS.push('');
				}
			}
		}

		const res = await updateGeminiConfig(localStorage.token, {
			ENABLE_GEMINI_API: ENABLE_GEMINI_API,
			GEMINI_API_BASE_URLS: GEMINI_API_BASE_URLS,
			GEMINI_API_KEYS: GEMINI_API_KEYS,
			GEMINI_API_CONFIGS: GEMINI_API_CONFIGS
		}).catch((error) => {
			toast.error(formatSettingsSaveError(error));
			return null;
		});

		// A newer request was fired while this one was in-flight; discard stale response
		if (thisVersion !== geminiUpdateVersion) return false;
		if (!res) return false;

		// Server may normalize configs (e.g. prefix_id uniqueness, default names).
		ENABLE_GEMINI_API = res?.ENABLE_GEMINI_API ?? ENABLE_GEMINI_API;
		GEMINI_API_BASE_URLS = res?.GEMINI_API_BASE_URLS ?? GEMINI_API_BASE_URLS;
		GEMINI_API_KEYS = res?.GEMINI_API_KEYS ?? GEMINI_API_KEYS;
		GEMINI_API_CONFIGS = res?.GEMINI_API_CONFIGS ?? GEMINI_API_CONFIGS;

		for (const [idx, url] of GEMINI_API_BASE_URLS.entries()) {
			if (!GEMINI_API_CONFIGS[idx]) {
				GEMINI_API_CONFIGS[idx] = GEMINI_API_CONFIGS[url] || {};
			}
		}

		// Update cache with normalized config
		geminiConfigCache.set({
			ENABLE_GEMINI_API,
			GEMINI_API_BASE_URLS,
			GEMINI_API_KEYS,
			GEMINI_API_CONFIGS
		});
		toast.success($i18n.t('Gemini API settings updated'));
		if (refreshModels) {
			await refreshModelsStore(localStorage.token, { force: true, reason: 'admin-connections' });
		}

		return true;
	};

	const addGeminiConnectionHandler = async (connection: any) => {
		expandedSections.gemini = true;
		GEMINI_API_BASE_URLS = [...GEMINI_API_BASE_URLS, connection.url];
		GEMINI_API_KEYS = [...GEMINI_API_KEYS, connection.key];
		GEMINI_API_CONFIGS[GEMINI_API_BASE_URLS.length - 1] = connection.config;

		const ok = await updateGeminiHandler(!!ENABLE_GEMINI_API);
		if (!ok) {
			throw new Error($i18n.t('Failed to save connections'));
		}
	};

	const updateGrokHandler = async (refreshModels = true) => {
		if (ENABLE_GROK_API === null) return false;

		const thisVersion = ++grokUpdateVersion;

		GROK_API_BASE_URLS = GROK_API_BASE_URLS.map((url) => url.replace(/\/$/, ''));

		if (GROK_API_KEYS.length !== GROK_API_BASE_URLS.length) {
			if (GROK_API_KEYS.length > GROK_API_BASE_URLS.length) {
				GROK_API_KEYS = GROK_API_KEYS.slice(0, GROK_API_BASE_URLS.length);
			}
			if (GROK_API_KEYS.length < GROK_API_BASE_URLS.length) {
				const diff = GROK_API_BASE_URLS.length - GROK_API_KEYS.length;
				for (let i = 0; i < diff; i++) {
					GROK_API_KEYS.push('');
				}
			}
		}

		const res = await updateGrokConfig(localStorage.token, {
			ENABLE_GROK_API: ENABLE_GROK_API,
			GROK_API_BASE_URLS: GROK_API_BASE_URLS,
			GROK_API_KEYS: GROK_API_KEYS,
			GROK_API_CONFIGS: GROK_API_CONFIGS
		}).catch((error) => {
			toast.error(formatSettingsSaveError(error));
			return null;
		});

		if (thisVersion !== grokUpdateVersion) return false;
		if (!res) return false;

		ENABLE_GROK_API = res?.ENABLE_GROK_API ?? ENABLE_GROK_API;
		GROK_API_BASE_URLS = res?.GROK_API_BASE_URLS ?? GROK_API_BASE_URLS;
		GROK_API_KEYS = res?.GROK_API_KEYS ?? GROK_API_KEYS;
		GROK_API_CONFIGS = res?.GROK_API_CONFIGS ?? GROK_API_CONFIGS;

		for (const [idx, url] of GROK_API_BASE_URLS.entries()) {
			if (!GROK_API_CONFIGS[idx]) {
				GROK_API_CONFIGS[idx] = GROK_API_CONFIGS[url] || {};
			}
		}

		grokConfigCache.set({
			ENABLE_GROK_API,
			GROK_API_BASE_URLS,
			GROK_API_KEYS,
			GROK_API_CONFIGS
		});
		toast.success($i18n.t('Grok API settings updated'));
		if (refreshModels) {
			await refreshModelsStore(localStorage.token, { force: true, reason: 'admin-connections' });
		}

		return true;
	};

	const addGrokConnectionHandler = async (connection: any) => {
		expandedSections.grok = true;
		GROK_API_BASE_URLS = [...GROK_API_BASE_URLS, connection.url];
		GROK_API_KEYS = [...GROK_API_KEYS, connection.key];
		GROK_API_CONFIGS[GROK_API_BASE_URLS.length - 1] = connection.config;

		const ok = await updateGrokHandler(!!ENABLE_GROK_API);
		if (!ok) {
			throw new Error($i18n.t('Failed to save connections'));
		}
	};

	const updateAnthropicHandler = async (refreshModels = true) => {
		if (ENABLE_ANTHROPIC_API === null) return false;

		const thisVersion = ++anthropicUpdateVersion;

		// Remove trailing slashes
		ANTHROPIC_API_BASE_URLS = ANTHROPIC_API_BASE_URLS.map((url) => url.replace(/\/$/, ''));

		// Check if API KEYS length is same than API URLS length
		if (ANTHROPIC_API_KEYS.length !== ANTHROPIC_API_BASE_URLS.length) {
			if (ANTHROPIC_API_KEYS.length > ANTHROPIC_API_BASE_URLS.length) {
				ANTHROPIC_API_KEYS = ANTHROPIC_API_KEYS.slice(0, ANTHROPIC_API_BASE_URLS.length);
			}
			if (ANTHROPIC_API_KEYS.length < ANTHROPIC_API_BASE_URLS.length) {
				const diff = ANTHROPIC_API_BASE_URLS.length - ANTHROPIC_API_KEYS.length;
				for (let i = 0; i < diff; i++) {
					ANTHROPIC_API_KEYS.push('');
				}
			}
		}

		const res = await updateAnthropicConfig(localStorage.token, {
			ENABLE_ANTHROPIC_API: ENABLE_ANTHROPIC_API,
			ANTHROPIC_API_BASE_URLS: ANTHROPIC_API_BASE_URLS,
			ANTHROPIC_API_KEYS: ANTHROPIC_API_KEYS,
			ANTHROPIC_API_CONFIGS: ANTHROPIC_API_CONFIGS
		}).catch((error) => {
			toast.error(formatSettingsSaveError(error));
			return null;
		});

		// A newer request was fired while this one was in-flight; discard stale response
		if (thisVersion !== anthropicUpdateVersion) return false;
		if (!res) return false;

		// Server may normalize configs (e.g. prefix_id uniqueness, default names).
		ENABLE_ANTHROPIC_API = res?.ENABLE_ANTHROPIC_API ?? ENABLE_ANTHROPIC_API;
		ANTHROPIC_API_BASE_URLS = res?.ANTHROPIC_API_BASE_URLS ?? ANTHROPIC_API_BASE_URLS;
		ANTHROPIC_API_KEYS = res?.ANTHROPIC_API_KEYS ?? ANTHROPIC_API_KEYS;
		ANTHROPIC_API_CONFIGS = res?.ANTHROPIC_API_CONFIGS ?? ANTHROPIC_API_CONFIGS;

		for (const [idx, url] of ANTHROPIC_API_BASE_URLS.entries()) {
			if (!ANTHROPIC_API_CONFIGS[idx]) {
				ANTHROPIC_API_CONFIGS[idx] = ANTHROPIC_API_CONFIGS[url] || {};
			}
		}

		// Update cache with normalized config
		anthropicConfigCache.set({
			ENABLE_ANTHROPIC_API,
			ANTHROPIC_API_BASE_URLS,
			ANTHROPIC_API_KEYS,
			ANTHROPIC_API_CONFIGS
		});
		toast.success($i18n.t('Anthropic API settings updated'));
		if (refreshModels) {
			await refreshModelsStore(localStorage.token, { force: true, reason: 'admin-connections' });
		}

		return true;
	};

	const addAnthropicConnectionHandler = async (connection: any) => {
		expandedSections.anthropic = true;
		ANTHROPIC_API_BASE_URLS = [...ANTHROPIC_API_BASE_URLS, connection.url];
		ANTHROPIC_API_KEYS = [...ANTHROPIC_API_KEYS, connection.key];
		ANTHROPIC_API_CONFIGS[ANTHROPIC_API_BASE_URLS.length - 1] = connection.config;

		const ok = await updateAnthropicHandler(!!ENABLE_ANTHROPIC_API);
		if (!ok) {
			throw new Error($i18n.t('Failed to save connections'));
		}
	};

	onMount(async () => {
		if ($user?.role === 'admin') {
			let ollamaConfig: any = {};
			let openaiConfig: any = {};
			let geminiConfig: any = {};
			let grokConfig: any = {};
			let anthropicConfig: any = {};

			await Promise.all([
				(async () => {
					try {
						// Use cached config if available
						if ($ollamaConfigCache) {
							ollamaConfig = $ollamaConfigCache;
						} else {
							ollamaConfig = await getOllamaConfig(localStorage.token);
							ollamaConfigCache.set(ollamaConfig);
						}
					} catch (error) {
						console.error(error);
						ollamaConfig = {
							ENABLE_OLLAMA_API: false,
							OLLAMA_BASE_URLS: [],
							OLLAMA_API_CONFIGS: {}
						};
					}
				})(),
				(async () => {
					try {
						// Use cached config if available
						if ($openaiConfigCache) {
							openaiConfig = $openaiConfigCache;
						} else {
							openaiConfig = await getOpenAIConfig(localStorage.token);
							openaiConfigCache.set(openaiConfig);
						}
					} catch (error) {
						console.error(error);
						openaiConfig = {
							ENABLE_OPENAI_API: false,
							OPENAI_API_BASE_URLS: [],
							OPENAI_API_KEYS: [],
							OPENAI_API_CONFIGS: {}
						};
					}
				})(),
				(async () => {
					try {
						// Use cached config if available
						if ($geminiConfigCache) {
							geminiConfig = $geminiConfigCache;
						} else {
							geminiConfig = await getGeminiConfig(localStorage.token);
							geminiConfigCache.set(geminiConfig);
						}
					} catch (error) {
						console.error(error);
						geminiConfig = {
							ENABLE_GEMINI_API: false,
							GEMINI_API_BASE_URLS: [],
							GEMINI_API_KEYS: [],
							GEMINI_API_CONFIGS: {}
						};
					}
				})(),
				(async () => {
					try {
						if ($grokConfigCache) {
							grokConfig = $grokConfigCache;
						} else {
							grokConfig = await getGrokConfig(localStorage.token);
							grokConfigCache.set(grokConfig);
						}
					} catch (error) {
						console.error(error);
						grokConfig = {
							ENABLE_GROK_API: false,
							GROK_API_BASE_URLS: [],
							GROK_API_KEYS: [],
							GROK_API_CONFIGS: {}
						};
					}
				})(),
				(async () => {
					try {
						// Use cached config if available
						if ($anthropicConfigCache) {
							anthropicConfig = $anthropicConfigCache;
						} else {
							anthropicConfig = await getAnthropicConfig(localStorage.token);
							anthropicConfigCache.set(anthropicConfig);
						}
					} catch (error) {
						console.error(error);
						anthropicConfig = {
							ENABLE_ANTHROPIC_API: false,
							ANTHROPIC_API_BASE_URLS: [],
							ANTHROPIC_API_KEYS: [],
							ANTHROPIC_API_CONFIGS: {}
						};
					}
				})(),
				(async () => {
					try {
						// Use cached config if available
						if ($connectionsConfigCache) {
							connectionsConfig = JSON.parse(JSON.stringify($connectionsConfigCache));
						} else {
							connectionsConfig = await getConnectionsConfig(localStorage.token);
							connectionsConfigCache.set(JSON.parse(JSON.stringify(connectionsConfig)));
						}
					} catch (error) {
						console.error(error);
						connectionsConfig = {};
					}
				})()
			]);

			ENABLE_OPENAI_API = openaiConfig?.ENABLE_OPENAI_API ?? false;
			ENABLE_OLLAMA_API = ollamaConfig?.ENABLE_OLLAMA_API ?? false;

			OPENAI_API_BASE_URLS = openaiConfig?.OPENAI_API_BASE_URLS ?? [];
			OPENAI_API_KEYS = openaiConfig?.OPENAI_API_KEYS ?? [];
			OPENAI_API_CONFIGS = openaiConfig?.OPENAI_API_CONFIGS ?? {};

			OLLAMA_BASE_URLS = ollamaConfig?.OLLAMA_BASE_URLS ?? [];
			OLLAMA_API_CONFIGS = ollamaConfig?.OLLAMA_API_CONFIGS ?? {};

			if (ENABLE_OPENAI_API) {
				// get url and idx
				for (const [idx, url] of OPENAI_API_BASE_URLS.entries()) {
					if (!OPENAI_API_CONFIGS[idx]) {
						// Legacy support, url as key
						OPENAI_API_CONFIGS[idx] = OPENAI_API_CONFIGS[url] || {};
					}
				}
			}

			if (ENABLE_OLLAMA_API) {
				for (const [idx, url] of OLLAMA_BASE_URLS.entries()) {
					if (!OLLAMA_API_CONFIGS[idx]) {
						OLLAMA_API_CONFIGS[idx] = OLLAMA_API_CONFIGS[url] || {};
					}
				}
			}

			ENABLE_GEMINI_API = geminiConfig?.ENABLE_GEMINI_API ?? false;
			GEMINI_API_BASE_URLS = geminiConfig?.GEMINI_API_BASE_URLS ?? [];
			GEMINI_API_KEYS = geminiConfig?.GEMINI_API_KEYS ?? [];
			GEMINI_API_CONFIGS = geminiConfig?.GEMINI_API_CONFIGS ?? {};

			ENABLE_GROK_API = grokConfig?.ENABLE_GROK_API ?? false;
			GROK_API_BASE_URLS = grokConfig?.GROK_API_BASE_URLS ?? [];
			GROK_API_KEYS = grokConfig?.GROK_API_KEYS ?? [];
			GROK_API_CONFIGS = grokConfig?.GROK_API_CONFIGS ?? {};

			ENABLE_ANTHROPIC_API = anthropicConfig?.ENABLE_ANTHROPIC_API ?? false;
			ANTHROPIC_API_BASE_URLS = anthropicConfig?.ANTHROPIC_API_BASE_URLS ?? [];
			ANTHROPIC_API_KEYS = anthropicConfig?.ANTHROPIC_API_KEYS ?? [];
			ANTHROPIC_API_CONFIGS = anthropicConfig?.ANTHROPIC_API_CONFIGS ?? {};

			connectionsConfig = connectionsConfig ?? {};
			connectionsConfig.ENABLE_DIRECT_CONNECTIONS ??= false;
			connectionsConfig.ENABLE_BASE_MODELS_CACHE ??= true;

			for (const [idx] of GEMINI_API_BASE_URLS.entries()) {
				if (!GEMINI_API_CONFIGS[idx]) {
					GEMINI_API_CONFIGS[idx] = {};
				}
			}

			for (const [idx] of GROK_API_BASE_URLS.entries()) {
				if (!GROK_API_CONFIGS[idx]) {
					GROK_API_CONFIGS[idx] = {};
				}
			}

			for (const [idx] of ANTHROPIC_API_BASE_URLS.entries()) {
				if (!ANTHROPIC_API_CONFIGS[idx]) {
					ANTHROPIC_API_CONFIGS[idx] = {};
				}
			}

			initExpandedSections();
		}
	});

	const submitHandler = async () => {
		// Don't refresh models on form submit - only save configs
		// Wait for both saves to complete before dispatching success
		await Promise.all([
			updateOpenAIHandler(false),
			updateGeminiHandler(false),
			updateGrokHandler(false),
			updateAnthropicHandler(false),
			updateOllamaHandler(false)
		]);

		dispatch('save');

		await config.set(await getBackendConfig());
	};
</script>

<AddConnectionModal
	bind:show={showAddOpenAIConnectionModal}
	onSubmit={addOpenAIConnectionHandler}
/>

<AddConnectionModal
	gemini
	bind:show={showAddGeminiConnectionModal}
	onSubmit={addGeminiConnectionHandler}
/>

<AddConnectionModal
	grok
	bind:show={showAddGrokConnectionModal}
	onSubmit={addGrokConnectionHandler}
/>

<AddConnectionModal
	anthropic
	bind:show={showAddAnthropicConnectionModal}
	onSubmit={addAnthropicConnectionHandler}
/>

<AddConnectionModal
	ollama
	bind:show={showAddOllamaConnectionModal}
	onSubmit={addOllamaConnectionHandler}
/>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={submitHandler}
>
	<div class="overflow-y-auto scrollbar-hidden h-full pr-2">
		{#if ENABLE_OPENAI_API !== null && ENABLE_OLLAMA_API !== null && ENABLE_GEMINI_API !== null && ENABLE_GROK_API !== null && ENABLE_ANTHROPIC_API !== null && connectionsConfig !== null}
			<div class="max-w-6xl mx-auto space-y-3">
				<div bind:this={sectionEl_openai} class="scroll-mt-2">
					<div class="rounded-2xl border bg-gray-50 border-gray-100 dark:bg-gray-850 dark:border-gray-800">
						<div
							class="w-full flex items-center justify-between px-5 py-4 text-left cursor-pointer select-none"
							role="button"
							tabindex="0"
							aria-expanded={expandedSections.openai}
							on:click={async (e) => {
								const target = e.target;
								if (
									target instanceof Element &&
									target.closest('button, a, input, select, textarea, [data-no-toggle]')
								) {
									return;
								}
								expandedSections.openai = !expandedSections.openai;
								await revealIfExpanded('openai', sectionEl_openai);
							}}
							on:keydown={async (e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									expandedSections.openai = !expandedSections.openai;
									await revealIfExpanded('openai', sectionEl_openai);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div
									class="glass-icon-badge bg-emerald-100/80 dark:bg-emerald-900/30"
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] text-emerald-600 dark:text-emerald-400"
									>
										<path
											d="M21.55 10.004a5.416 5.416 0 00-.478-4.501c-1.217-2.09-3.662-3.166-6.05-2.66A5.59 5.59 0 0010.831 1C8.39.995 6.224 2.546 5.473 4.838A5.553 5.553 0 001.76 7.496a5.487 5.487 0 00.691 6.5 5.416 5.416 0 00.477 4.502c1.217 2.09 3.662 3.165 6.05 2.66A5.586 5.586 0 0013.168 23c2.443.006 4.61-1.546 5.361-3.84a5.553 5.553 0 003.715-2.66 5.488 5.488 0 00-.693-6.497v.001zm-8.381 11.558a4.199 4.199 0 01-2.675-.954c.034-.018.093-.05.132-.074l4.44-2.53a.71.71 0 00.364-.623v-6.176l1.877 1.069c.02.01.033.029.036.05v5.115c-.003 2.274-1.87 4.118-4.174 4.123zM4.192 17.78a4.059 4.059 0 01-.498-2.763c.032.02.09.055.131.078l4.44 2.53c.225.13.504.13.73 0l5.42-3.088v2.138a.068.068 0 01-.027.057L9.9 19.288c-1.999 1.136-4.552.46-5.707-1.51h-.001zM3.023 8.216A4.15 4.15 0 015.198 6.41l-.002.151v5.06a.711.711 0 00.364.624l5.42 3.087-1.876 1.07a.067.067 0 01-.063.005l-4.489-2.559c-1.995-1.14-2.679-3.658-1.53-5.63h.001zm15.417 3.54l-5.42-3.088L14.896 7.6a.067.067 0 01.063-.006l4.489 2.557c1.998 1.14 2.683 3.662 1.529 5.633a4.163 4.163 0 01-2.174 1.807V12.38a.71.71 0 00-.363-.623zm1.867-2.773a6.04 6.04 0 00-.132-.078l-4.44-2.53a.731.731 0 00-.729 0l-5.42 3.088V7.325a.068.068 0 01.027-.057L14.1 4.713c2-1.137 4.555-.46 5.707 1.513.487.833.664 1.809.499 2.757h.001zm-11.741 3.81l-1.877-1.068a.065.065 0 01-.036-.051V6.559c.001-2.277 1.873-4.122 4.181-4.12.976 0 1.92.338 2.671.954-.034.018-.092.05-.131.073l-4.44 2.53a.71.71 0 00-.365.623l-.003 6.173v.002zm1.02-2.168L12 9.25l2.414 1.375v2.75L12 14.75l-2.415-1.375v-2.75z"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100 tracking-tight">
									{$i18n.t('OpenAI API')}
								</div>
							</div>

							<div class="flex items-center gap-3">
								<div data-no-toggle>
									<Switch
										bind:state={ENABLE_OPENAI_API}
										on:change={async () => {
											if (ENABLE_OPENAI_API) {
												expandedSections.openai = true;
												updateOpenAIHandler(false);
												return;
											}

											showDisableOpenAIAPIConfirm = true;
										}}
									/>
								</div>

								<div
									class="transform transition-transform duration-200 {expandedSections.openai
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</div>

						{#if expandedSections.openai}
							<div transition:slide={{ duration: 200, easing: quintOut }} class="px-5 pb-5">
								<div class="grid grid-cols-1 md:grid-cols-2 gap-2">
										{#each OPENAI_API_BASE_URLS as url, idx (getConnectionRenderKey(url, OPENAI_API_KEYS[idx], OPENAI_API_CONFIGS[idx]))}
										<OpenAIConnection
											bind:url={OPENAI_API_BASE_URLS[idx]}
											bind:key={OPENAI_API_KEYS[idx]}
											bind:config={OPENAI_API_CONFIGS[idx]}
											onSubmit={async () => {
												const ok = await updateOpenAIHandler(!!ENABLE_OPENAI_API);
												if (!ok) {
													throw new Error($i18n.t('Failed to save connections'));
												}
											}}
											onDelete={() => {
												OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS.filter(
													(u, urlIdx) => !(urlIdx === idx && u === url)
												);
												OPENAI_API_KEYS = OPENAI_API_KEYS.filter((key, keyIdx) => idx !== keyIdx);

												let newConfig = {};
												OPENAI_API_BASE_URLS.forEach((u, newIdx) => {
													newConfig[newIdx] =
														OPENAI_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
												});
												OPENAI_API_CONFIGS = newConfig;
												updateOpenAIHandler(!!ENABLE_OPENAI_API);
											}}
										/>
									{/each}
									<button
										type="button"
										class="w-full min-h-[62px] bg-white dark:bg-gray-900 rounded-lg px-4 py-3 border border-dashed border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:focus:ring-gray-700"
										aria-label={$i18n.t('Add Connection')}
										on:click={() => {
											showAddOpenAIConnectionModal = true;
										}}
									>
										<div
											class="w-7 h-7 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-700 dark:text-gray-200"
										>
											<Plus className="size-3" />
										</div>
										<div
											class="text-xs font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
										>
											{$i18n.t('Click to add a new connection')}
										</div>
									</button>
								</div>
							</div>
						{/if}
					</div>
				</div>

				<!-- Gemini API Section -->
				<div bind:this={sectionEl_gemini} class="scroll-mt-2">
					<div class="rounded-2xl border bg-gray-50 border-gray-100 dark:bg-gray-850 dark:border-gray-800">
						<div
							class="w-full flex items-center justify-between px-5 py-4 text-left cursor-pointer select-none"
							role="button"
							tabindex="0"
							aria-expanded={expandedSections.gemini}
							on:click={async (e) => {
								const target = e.target;
								if (
									target instanceof Element &&
									target.closest('button, a, input, select, textarea, [data-no-toggle]')
								) {
									return;
								}
								expandedSections.gemini = !expandedSections.gemini;
								await revealIfExpanded('gemini', sectionEl_gemini);
							}}
							on:keydown={async (e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									expandedSections.gemini = !expandedSections.gemini;
									await revealIfExpanded('gemini', sectionEl_gemini);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div
									class="glass-icon-badge bg-blue-100/80 dark:bg-blue-900/30"
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] text-blue-600 dark:text-blue-400"
									>
										<path
											d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
										/>
										<path
											d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
										/>
										<path
											d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"
										/>
										<path
											d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100 tracking-tight">
									{$i18n.t('Gemini API')}
								</div>
							</div>

							<div class="flex items-center gap-3">
								<div data-no-toggle>
									<Switch
										bind:state={ENABLE_GEMINI_API}
										on:change={async () => {
											if (ENABLE_GEMINI_API) {
												expandedSections.gemini = true;
												updateGeminiHandler(false);
												return;
											}

											showDisableGeminiAPIConfirm = true;
										}}
									/>
								</div>

								<div
									class="transform transition-transform duration-200 {expandedSections.gemini
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</div>

						{#if expandedSections.gemini}
							<div transition:slide={{ duration: 200, easing: quintOut }} class="px-5 pb-5">
								<div class="grid grid-cols-1 md:grid-cols-2 gap-2">
										{#each GEMINI_API_BASE_URLS as url, idx (getConnectionRenderKey(url, GEMINI_API_KEYS[idx], GEMINI_API_CONFIGS[idx]))}
										<GeminiConnection
											bind:url={GEMINI_API_BASE_URLS[idx]}
											bind:key={GEMINI_API_KEYS[idx]}
											bind:config={GEMINI_API_CONFIGS[idx]}
											onSubmit={async () => {
												const ok = await updateGeminiHandler(!!ENABLE_GEMINI_API);
												if (!ok) {
													throw new Error($i18n.t('Failed to save connections'));
												}
											}}
											onDelete={() => {
												GEMINI_API_BASE_URLS = GEMINI_API_BASE_URLS.filter(
													(u, urlIdx) => !(urlIdx === idx && u === url)
												);
												GEMINI_API_KEYS = GEMINI_API_KEYS.filter((key, keyIdx) => idx !== keyIdx);

												let newConfig = {};
												GEMINI_API_BASE_URLS.forEach((u, newIdx) => {
													newConfig[newIdx] =
														GEMINI_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
												});
												GEMINI_API_CONFIGS = newConfig;
												updateGeminiHandler(!!ENABLE_GEMINI_API);
											}}
										/>
									{/each}
									<button
										type="button"
										class="w-full min-h-[62px] bg-white dark:bg-gray-900 rounded-lg px-4 py-3 border border-dashed border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:focus:ring-gray-700"
										aria-label={$i18n.t('Add Connection')}
										on:click={() => {
											showAddGeminiConnectionModal = true;
										}}
									>
										<div
											class="w-7 h-7 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-700 dark:text-gray-200"
										>
											<Plus className="size-3" />
										</div>
										<div
											class="text-xs font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
										>
											{$i18n.t('Click to add a new connection')}
										</div>
									</button>
								</div>
							</div>
						{/if}
					</div>
				</div>

				<!-- Grok API Section -->
				<div bind:this={sectionEl_grok} class="scroll-mt-2">
					<div class="rounded-2xl border bg-gray-50 border-gray-100 dark:bg-gray-850 dark:border-gray-800">
						<div
							class="w-full flex items-center justify-between px-5 py-4 text-left cursor-pointer select-none"
							role="button"
							tabindex="0"
							aria-expanded={expandedSections.grok}
							on:click={async (e) => {
								const target = e.target;
								if (
									target instanceof Element &&
									target.closest('button, a, input, select, textarea, [data-no-toggle]')
								) {
									return;
								}
								expandedSections.grok = !expandedSections.grok;
								await revealIfExpanded('grok', sectionEl_grok);
							}}
							on:keydown={async (e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									expandedSections.grok = !expandedSections.grok;
									await revealIfExpanded('grok', sectionEl_grok);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div class="glass-icon-badge bg-slate-100/80 dark:bg-slate-900/30">
									<ModelIcon
										src={GROK_PROVIDER_ICON}
										alt="Grok"
										bare
										className="size-[18px]"
									/>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100 tracking-tight">
									{$i18n.t('Grok API')}
								</div>
							</div>

							<div class="flex items-center gap-3">
								<div data-no-toggle>
									<Switch
										bind:state={ENABLE_GROK_API}
										on:change={async () => {
											if (ENABLE_GROK_API) {
												expandedSections.grok = true;
												updateGrokHandler(false);
												return;
											}

											showDisableGrokAPIConfirm = true;
										}}
									/>
								</div>

								<div
									class="transform transition-transform duration-200 {expandedSections.grok
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</div>

						{#if expandedSections.grok}
							<div transition:slide={{ duration: 200, easing: quintOut }} class="px-5 pb-5">
								<div class="grid grid-cols-1 md:grid-cols-2 gap-2">
									{#each GROK_API_BASE_URLS as url, idx (getConnectionRenderKey(url, GROK_API_KEYS[idx], GROK_API_CONFIGS[idx]))}
										<GrokConnection
											bind:url={GROK_API_BASE_URLS[idx]}
											bind:key={GROK_API_KEYS[idx]}
											bind:config={GROK_API_CONFIGS[idx]}
											onSubmit={async () => {
												const ok = await updateGrokHandler(!!ENABLE_GROK_API);
												if (!ok) {
													throw new Error($i18n.t('Failed to save connections'));
												}
											}}
											onDelete={() => {
												GROK_API_BASE_URLS = GROK_API_BASE_URLS.filter(
													(u, urlIdx) => !(urlIdx === idx && u === url)
												);
												GROK_API_KEYS = GROK_API_KEYS.filter((key, keyIdx) => idx !== keyIdx);

												let newConfig = {};
												GROK_API_BASE_URLS.forEach((u, newIdx) => {
													newConfig[newIdx] =
														GROK_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
												});
												GROK_API_CONFIGS = newConfig;
												updateGrokHandler(!!ENABLE_GROK_API);
											}}
										/>
									{/each}
									<button
										type="button"
										class="w-full min-h-[62px] bg-white dark:bg-gray-900 rounded-lg px-4 py-3 border border-dashed border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:focus:ring-gray-700"
										aria-label={$i18n.t('Add Connection')}
										on:click={() => {
											showAddGrokConnectionModal = true;
										}}
									>
										<div
											class="w-7 h-7 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-700 dark:text-gray-200"
										>
											<Plus className="size-3" />
										</div>
										<div
											class="text-xs font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
										>
											{$i18n.t('Click to add a new connection')}
										</div>
									</button>
								</div>
							</div>
						{/if}
					</div>
				</div>

				<!-- Anthropic API Section -->
				<div bind:this={sectionEl_anthropic} class="scroll-mt-2">
					<div class="rounded-2xl border bg-gray-50 border-gray-100 dark:bg-gray-850 dark:border-gray-800">
						<div
							class="w-full flex items-center justify-between px-5 py-4 text-left cursor-pointer select-none"
							role="button"
							tabindex="0"
							aria-expanded={expandedSections.anthropic}
							on:click={async (e) => {
								const target = e.target;
								if (
									target instanceof Element &&
									target.closest('button, a, input, select, textarea, [data-no-toggle]')
								) {
									return;
								}
								expandedSections.anthropic = !expandedSections.anthropic;
								await revealIfExpanded('anthropic', sectionEl_anthropic);
							}}
							on:keydown={async (e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									expandedSections.anthropic = !expandedSections.anthropic;
									await revealIfExpanded('anthropic', sectionEl_anthropic);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div
									class="glass-icon-badge bg-amber-100/80 dark:bg-amber-900/30"
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 16 16"
										fill="currentColor"
										class="size-[18px] text-amber-600 dark:text-amber-400"
									>
										<path
											d="m3.127 10.604 3.135-1.76.053-.153-.053-.085H6.11l-.525-.032-1.791-.048-1.554-.065-1.505-.08-.38-.081L0 7.832l.036-.234.32-.214.455.04 1.009.069 1.513.105 1.097.064 1.626.17h.259l.036-.105-.089-.065-.068-.064-1.566-1.062-1.695-1.121-.887-.646-.48-.327-.243-.306-.104-.67.435-.48.585.04.15.04.593.456 1.267.981 1.654 1.218.242.202.097-.068.012-.049-.109-.181-.9-1.626-.96-1.655-.428-.686-.113-.411a2 2 0 0 1-.068-.484l.496-.674L4.446 0l.662.089.279.242.411.94.666 1.48 1.033 2.014.302.597.162.553.06.17h.105v-.097l.085-1.134.157-1.392.154-1.792.052-.504.25-.605.497-.327.387.186.319.456-.045.294-.19 1.23-.37 1.93-.243 1.29h.142l.161-.16.654-.868 1.097-1.372.484-.545.565-.601.363-.287h.686l.505.751-.226.775-.707.895-.585.759-.839 1.13-.524.904.048.072.125-.012 1.897-.403 1.024-.186 1.223-.21.553.258.06.263-.218.536-1.307.323-1.533.307-2.284.54-.028.02.032.04 1.029.098.44.024h1.077l2.005.15.525.346.315.424-.053.323-.807.411-3.631-.863-.872-.218h-.12v.073l.726.71 1.331 1.202 1.667 1.55.084.383-.214.302-.226-.032-1.464-1.101-.565-.497-1.28-1.077h-.084v.113l.295.432 1.557 2.34.08.718-.112.234-.404.141-.444-.08-.911-1.28-.94-1.44-.759-1.291-.093.053-.448 4.821-.21.246-.484.186-.403-.307-.214-.496.214-.98.258-1.28.21-1.016.19-1.263.112-.42-.008-.028-.092.012-.953 1.307-1.448 1.957-1.146 1.227-.274.109-.477-.247.045-.44.266-.39 1.586-2.018.956-1.25.617-.723-.004-.105h-.036l-4.212 2.736-.75.096-.324-.302.04-.496.154-.162 1.267-.871z"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100 tracking-tight">
									{$i18n.t('Anthropic API')}
								</div>
							</div>

							<div class="flex items-center gap-3">
								<div data-no-toggle>
									<Switch
										bind:state={ENABLE_ANTHROPIC_API}
										on:change={async () => {
											if (ENABLE_ANTHROPIC_API) {
												expandedSections.anthropic = true;
												updateAnthropicHandler(false);
												return;
											}

											showDisableAnthropicAPIConfirm = true;
										}}
									/>
								</div>

								<div
									class="transform transition-transform duration-200 {expandedSections.anthropic
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</div>

						{#if expandedSections.anthropic}
							<div transition:slide={{ duration: 200, easing: quintOut }} class="px-5 pb-5">
								<div class="grid grid-cols-1 md:grid-cols-2 gap-2">
										{#each ANTHROPIC_API_BASE_URLS as url, idx (getConnectionRenderKey(url, ANTHROPIC_API_KEYS[idx], ANTHROPIC_API_CONFIGS[idx]))}
										<AnthropicConnection
											bind:url={ANTHROPIC_API_BASE_URLS[idx]}
											bind:key={ANTHROPIC_API_KEYS[idx]}
											bind:config={ANTHROPIC_API_CONFIGS[idx]}
											onSubmit={async () => {
												const ok = await updateAnthropicHandler(!!ENABLE_ANTHROPIC_API);
												if (!ok) {
													throw new Error($i18n.t('Failed to save connections'));
												}
											}}
											onDelete={() => {
												ANTHROPIC_API_BASE_URLS = ANTHROPIC_API_BASE_URLS.filter(
													(u, urlIdx) => !(urlIdx === idx && u === url)
												);
												ANTHROPIC_API_KEYS = ANTHROPIC_API_KEYS.filter(
													(key, keyIdx) => idx !== keyIdx
												);

												let newConfig = {};
												ANTHROPIC_API_BASE_URLS.forEach((u, newIdx) => {
													newConfig[newIdx] =
														ANTHROPIC_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
												});
												ANTHROPIC_API_CONFIGS = newConfig;
												updateAnthropicHandler(!!ENABLE_ANTHROPIC_API);
											}}
										/>
									{/each}
									<button
										type="button"
										class="w-full min-h-[62px] bg-white dark:bg-gray-900 rounded-lg px-4 py-3 border border-dashed border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:focus:ring-gray-700"
										aria-label={$i18n.t('Add Connection')}
										on:click={() => {
											showAddAnthropicConnectionModal = true;
										}}
									>
										<div
											class="w-7 h-7 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-700 dark:text-gray-200"
										>
											<Plus className="size-3" />
										</div>
										<div
											class="text-xs font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
										>
											{$i18n.t('Click to add a new connection')}
										</div>
									</button>
								</div>
							</div>
						{/if}
					</div>
				</div>

				<!-- Ollama API Section -->
				<div bind:this={sectionEl_ollama} class="scroll-mt-2">
					<div class="rounded-2xl border bg-gray-50 border-gray-100 dark:bg-gray-850 dark:border-gray-800">
						<div
							class="w-full flex items-center justify-between px-5 py-4 text-left cursor-pointer select-none"
							role="button"
							tabindex="0"
							aria-expanded={expandedSections.ollama}
							on:click={async (e) => {
								const target = e.target;
								if (
									target instanceof Element &&
									target.closest('button, a, input, select, textarea, [data-no-toggle]')
								) {
									return;
								}
								expandedSections.ollama = !expandedSections.ollama;
								await revealIfExpanded('ollama', sectionEl_ollama);
							}}
							on:keydown={async (e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									expandedSections.ollama = !expandedSections.ollama;
									await revealIfExpanded('ollama', sectionEl_ollama);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div
									class="glass-icon-badge bg-gray-100/80 dark:bg-gray-900/30"
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] text-gray-600 dark:text-gray-400"
									>
										<path
											d="M7.905 1.09c.216.085.411.225.588.41.295.306.544.744.734 1.263.191.522.315 1.1.362 1.68a5.054 5.054 0 012.049-.636l.051-.004c.87-.07 1.73.087 2.48.474.101.053.2.11.297.17.05-.569.172-1.134.36-1.644.19-.52.439-.957.733-1.264a1.67 1.67 0 01.589-.41c.257-.1.53-.118.796-.042.401.114.745.368 1.016.737.248.337.434.769.561 1.287.23.934.27 2.163.115 3.645l.053.04.026.019c.757.576 1.284 1.397 1.563 2.35.435 1.487.216 3.155-.534 4.088l-.018.021.002.003c.417.762.67 1.567.724 2.4l.002.03c.064 1.065-.2 2.137-.814 3.19l-.007.01.01.024c.472 1.157.62 2.322.438 3.486l-.006.039a.651.651 0 01-.747.536.648.648 0 01-.54-.742c.167-1.033.01-2.069-.48-3.123a.643.643 0 01.04-.617l.004-.006c.604-.924.854-1.83.8-2.72-.046-.779-.325-1.544-.8-2.273a.644.644 0 01.18-.886l.009-.006c.243-.159.467-.565.58-1.12a4.229 4.229 0 00-.095-1.974c-.205-.7-.58-1.284-1.105-1.683-.595-.454-1.383-.673-2.38-.61a.653.653 0 01-.632-.371c-.314-.665-.772-1.141-1.343-1.436a3.288 3.288 0 00-1.772-.332c-1.245.099-2.343.801-2.67 1.686a.652.652 0 01-.61.425c-1.067.002-1.893.252-2.497.703-.522.39-.878.935-1.066 1.588a4.07 4.07 0 00-.068 1.886c.112.558.331 1.02.582 1.269l.008.007c.212.207.257.53.109.785-.36.622-.629 1.549-.673 2.44-.05 1.018.186 1.902.719 2.536l.016.019a.643.643 0 01.095.69c-.576 1.236-.753 2.252-.562 3.052a.652.652 0 01-1.269.298c-.243-1.018-.078-2.184.473-3.498l.014-.035-.008-.012a4.339 4.339 0 01-.598-1.309l-.005-.019a5.764 5.764 0 01-.177-1.785c.044-.91.278-1.842.622-2.59l.012-.026-.002-.002c-.293-.418-.51-.953-.63-1.545l-.005-.024a5.352 5.352 0 01.093-2.49c.262-.915.777-1.701 1.536-2.269.06-.045.123-.09.186-.132-.159-1.493-.119-2.73.112-3.67.127-.518.314-.95.562-1.287.27-.368.614-.622 1.015-.737.266-.076.54-.059.797.042zm4.116 9.09c.936 0 1.8.313 2.446.855.63.527 1.005 1.235 1.005 1.94 0 .888-.406 1.58-1.133 2.022-.62.375-1.451.557-2.403.557-1.009 0-1.871-.259-2.493-.734-.617-.47-.963-1.13-.963-1.845 0-.707.398-1.417 1.056-1.946.668-.537 1.55-.849 2.485-.849zm0 .896a3.07 3.07 0 00-1.916.65c-.461.37-.722.835-.722 1.25 0 .428.21.829.61 1.134.455.347 1.124.548 1.943.548.799 0 1.473-.147 1.932-.426.463-.28.7-.686.7-1.257 0-.423-.246-.89-.683-1.256-.484-.405-1.14-.643-1.864-.643zm.662 1.21l.004.004c.12.151.095.37-.056.49l-.292.23v.446a.375.375 0 01-.376.373.375.375 0 01-.376-.373v-.46l-.271-.218a.347.347 0 01-.052-.49.353.353 0 01.494-.051l.215.172.22-.174a.353.353 0 01.49.051zm-5.04-1.919c.478 0 .867.39.867.871a.87.87 0 01-.868.871.87.87 0 01-.867-.87.87.87 0 01.867-.872zm8.706 0c.48 0 .868.39.868.871a.87.87 0 01-.868.871.87.87 0 01-.867-.87.87.87 0 01.867-.872zM7.44 2.3l-.003.002a.659.659 0 00-.285.238l-.005.006c-.138.189-.258.467-.348.832-.17.692-.216 1.631-.124 2.782.43-.128.899-.208 1.404-.237l.01-.001.019-.034c.046-.082.095-.161.148-.239.123-.771.022-1.692-.253-2.444-.134-.364-.297-.65-.453-.813a.628.628 0 00-.107-.09L7.44 2.3zm9.174.04l-.002.001a.628.628 0 00-.107.09c-.156.163-.32.45-.453.814-.29.794-.387 1.776-.23 2.572l.058.097.008.014h.03a5.184 5.184 0 011.466.212c.086-1.124.038-2.043-.128-2.722-.09-.365-.21-.643-.349-.832l-.004-.006a.659.659 0 00-.285-.239h-.004z"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100 tracking-tight">
									{$i18n.t('Ollama API')}
								</div>
							</div>

							<div class="flex items-center gap-3">
								<div data-no-toggle>
									<Switch
										bind:state={ENABLE_OLLAMA_API}
										on:change={async () => {
											if (ENABLE_OLLAMA_API) {
												expandedSections.ollama = true;
												updateOllamaHandler(false);
												return;
											}

											showDisableOllamaAPIConfirm = true;
										}}
									/>
								</div>

								<div
									class="transform transition-transform duration-200 {expandedSections.ollama
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</div>

						{#if expandedSections.ollama}
							<div
								transition:slide={{ duration: 200, easing: quintOut }}
								class="px-5 pb-5 space-y-2"
							>
								<div class="grid grid-cols-1 md:grid-cols-2 gap-2">
										{#each OLLAMA_BASE_URLS as url, idx (getOllamaRenderKey(url, OLLAMA_API_CONFIGS[idx]))}
										<OllamaConnection
											bind:url={OLLAMA_BASE_URLS[idx]}
											bind:config={OLLAMA_API_CONFIGS[idx]}
											{idx}
											onSubmit={async () => {
												const ok = await updateOllamaHandler(!!ENABLE_OLLAMA_API);
												if (!ok) {
													throw new Error($i18n.t('Failed to save connections'));
												}
											}}
											onDelete={() => {
												OLLAMA_BASE_URLS = OLLAMA_BASE_URLS.filter(
													(u, urlIdx) => !(urlIdx === idx && u === url)
												);

												let newConfig = {};
												OLLAMA_BASE_URLS.forEach((u, newIdx) => {
													newConfig[newIdx] =
														OLLAMA_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
												});
												OLLAMA_API_CONFIGS = newConfig;
												updateOllamaHandler(!!ENABLE_OLLAMA_API);
											}}
										/>
									{/each}
									<button
										type="button"
										class="w-full min-h-[62px] bg-white dark:bg-gray-900 rounded-lg px-4 py-3 border border-dashed border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition flex items-center justify-center gap-2 text-gray-500 dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:focus:ring-gray-700"
										aria-label={$i18n.t('Add Connection')}
										on:click={() => {
											showAddOllamaConnectionModal = true;
										}}
									>
										<div
											class="w-7 h-7 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-700 dark:text-gray-200"
										>
											<Plus className="size-3" />
										</div>
										<div
											class="text-xs font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
										>
											{$i18n.t('Click to add a new connection')}
										</div>
									</button>
								</div>

								<div class="text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('Trouble accessing Ollama?')}
									<a
										class=" text-gray-300 font-medium underline"
										href="https://github.com/ztx888/HaloWebUI#troubleshooting"
										target="_blank"
									>
										{$i18n.t('Click here for help.')}
									</a>
								</div>
							</div>
						{/if}
					</div>
				</div>

				<!-- Advanced Settings -->
				<div bind:this={sectionEl_advanced} class="scroll-mt-2">
					<div class="rounded-2xl border bg-gray-50 border-gray-100 dark:bg-gray-850 dark:border-gray-800">
						<button
							type="button"
							class="w-full flex items-center justify-between px-5 py-4 text-left"
							aria-expanded={expandedSections.advanced}
							aria-label={$i18n.t(expandedSections.advanced ? 'Collapse' : 'Expand')}
							on:click={async () => {
								expandedSections.advanced = !expandedSections.advanced;
								await revealIfExpanded('advanced', sectionEl_advanced);
							}}
						>
							<div class="flex items-center gap-3">
								<div
									class="glass-icon-badge bg-slate-100/80 dark:bg-slate-900/30"
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] text-slate-600 dark:text-slate-400"
									>
										<path
											fill-rule="evenodd"
											d="M11.078 2.25c-.917 0-1.699.663-1.85 1.567L9.05 4.889c-.02.12-.115.26-.297.348a7.493 7.493 0 00-.986.57c-.166.115-.334.126-.45.083L6.3 5.508a1.875 1.875 0 00-2.282.819l-.922 1.597a1.875 1.875 0 00.432 2.385l.84.692c.095.078.17.229.154.43a7.598 7.598 0 000 1.139c.015.2-.059.352-.153.43l-.841.692a1.875 1.875 0 00-.432 2.385l.922 1.597a1.875 1.875 0 002.282.818l1.019-.382c.115-.043.283-.031.45.082.312.214.641.405.985.57.182.088.277.228.297.35l.178 1.071c.151.904.933 1.567 1.85 1.567h1.844c.916 0 1.699-.663 1.85-1.567l.178-1.072c.02-.12.114-.26.297-.349.344-.165.673-.356.985-.57.167-.114.335-.125.45-.082l1.02.382a1.875 1.875 0 002.28-.819l.923-1.597a1.875 1.875 0 00-.432-2.385l-.84-.692c-.095-.078-.17-.229-.154-.43a7.614 7.614 0 000-1.139c-.016-.2.059-.352.153-.43l.84-.692c.708-.582.891-1.59.433-2.385l-.922-1.597a1.875 1.875 0 00-2.282-.818l-1.02.382c-.114.043-.282.031-.449-.083a7.49 7.49 0 00-.985-.57c-.183-.087-.277-.227-.297-.348l-.179-1.072a1.875 1.875 0 00-1.85-1.567h-1.843zM12 15.75a3.75 3.75 0 100-7.5 3.75 3.75 0 000 7.5z"
											clip-rule="evenodd"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100 tracking-tight">
									{$i18n.t('Advanced Settings')}
								</div>
							</div>

							<div
								class="transform transition-transform duration-200 {expandedSections.advanced
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-5 text-gray-400" />
							</div>
						</button>

						{#if expandedSections.advanced}
							<div
								transition:slide={{ duration: 200, easing: quintOut }}
								class="px-5 pb-5 space-y-4"
							>
								<div>
									<div class="flex justify-between items-center text-sm">
										<div class="font-medium">{$i18n.t('Cache Base Model List')}</div>
										<Switch
											bind:state={connectionsConfig.ENABLE_BASE_MODELS_CACHE}
											on:change={async (e) => {
												await baseModelsCacheChangeHandler(e.detail);
											}}
										/>
									</div>
									<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
										{$i18n.t(
											'Base Model List Cache speeds up access by fetching base models only at startup or on settings save—faster, but may not show recent base model changes.'
										)}
									</div>
								</div>
							</div>
						{/if}
					</div>
				</div>
			</div>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

</form>

<ConfirmDialog
	bind:show={showDisableBaseModelsCacheConfirm}
	title={$i18n.t('Disable Base Model List Cache?')}
	message={$i18n.t(
		'Disabling base model list caching may significantly slow down model loading. Not recommended on low-performance machines.'
	)}
	confirmLabel={$i18n.t('Disable')}
	onConfirm={async () => {
		suppressBaseModelsCacheSave = true;
		connectionsConfig.ENABLE_BASE_MODELS_CACHE = false;
		await updateConnectionsHandler();
		toast.warning(
			$i18n.t(
				'Base model list caching is disabled. Model loading may be slower until you re-enable it.'
			)
		);
	}}
/>

<ConfirmDialog
	bind:show={showDisableOpenAIAPIConfirm}
	title={$i18n.t('Disable OpenAI API?')}
	message={$i18n.t(
		'Turning off OpenAI API will disable all connections under it. Your connection settings will be kept.'
	)}
	confirmLabel={$i18n.t('Disable')}
	on:cancel={() => {
		ENABLE_OPENAI_API = true;
	}}
	onConfirm={async () => {
		await updateOpenAIHandler(false);
	}}
/>

<ConfirmDialog
	bind:show={showDisableGeminiAPIConfirm}
	title={$i18n.t('Disable Gemini API?')}
	message={$i18n.t(
		'Turning off Gemini API will disable all connections under it. Your connection settings will be kept.'
	)}
	confirmLabel={$i18n.t('Disable')}
	on:cancel={() => {
		ENABLE_GEMINI_API = true;
	}}
	onConfirm={async () => {
		await updateGeminiHandler(false);
	}}
/>

<ConfirmDialog
	bind:show={showDisableGrokAPIConfirm}
	title={$i18n.t('Disable Grok API?')}
	message={$i18n.t(
		'Turning off Grok API will disable all connections under it. Your connection settings will be kept.'
	)}
	confirmLabel={$i18n.t('Disable')}
	on:cancel={() => {
		ENABLE_GROK_API = true;
	}}
	onConfirm={async () => {
		await updateGrokHandler(false);
	}}
/>

<ConfirmDialog
	bind:show={showDisableAnthropicAPIConfirm}
	title={$i18n.t('Disable Anthropic API?')}
	message={$i18n.t(
		'Turning off Anthropic API will disable all connections under it. Your connection settings will be kept.'
	)}
	confirmLabel={$i18n.t('Disable')}
	on:cancel={() => {
		ENABLE_ANTHROPIC_API = true;
	}}
	onConfirm={async () => {
		await updateAnthropicHandler(false);
	}}
/>

<ConfirmDialog
	bind:show={showDisableOllamaAPIConfirm}
	title={$i18n.t('Disable Ollama API?')}
	message={$i18n.t(
		'Turning off Ollama API will disable all connections under it. Your connection settings will be kept.'
	)}
	confirmLabel={$i18n.t('Disable')}
	on:cancel={() => {
		ENABLE_OLLAMA_API = true;
	}}
	onConfirm={async () => {
		await updateOllamaHandler(false);
	}}
/>
