<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { getRAGConfig, updateRAGConfig } from '$lib/apis/retrieval';
	import { getTaskConfig, updateTaskConfig } from '$lib/apis';
	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import { revealExpandedSection } from '$lib/utils/expanded-section-scroll';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';
	import { normalizeWebSearchMode, type WebSearchMode } from '$lib/utils/web-search-mode';

	const i18n: Writable<any> = getContext('i18n');

	export let saveHandler: Function;

	let loading = true;
	let saving = false;

	// 折叠状态
	let expandedSections = {
		webSearch: true,
		loader: true
	};

	let sectionEl_webSearch: HTMLElement;
	let sectionEl_loader: HTMLElement;

	let selectedTab: 'webSearch' | 'loader' = 'webSearch';

	const tabMeta: Record<string, { label: string; description: string; badgeColor: string; iconColor: string }> = {
		webSearch: { label: 'Web Search', description: 'Configure web search engines and routing strategies', badgeColor: 'bg-cyan-50 dark:bg-cyan-950/30', iconColor: 'text-cyan-500 dark:text-cyan-400' },
		loader: { label: 'Web Loader', description: 'Configure web page loaders and content extraction', badgeColor: 'bg-teal-50 dark:bg-teal-950/30', iconColor: 'text-teal-500 dark:text-teal-400' }
	};

	$: activeTabMeta = tabMeta[selectedTab];

	let webSearchEngines = [
		'searxng',
		'google_pse',
		'brave',
		'kagi',
		'mojeek',
		'bocha',
		'serpstack',
		'serper',
		'serply',
		'searchapi',
		'serpapi',
		'duckduckgo',
		'tavily',
		'jina',
		'bing',
		'exa',
		'perplexity',
		'grok',
		'sougou'
	];
	let webLoaderEngines = ['playwright', 'firecrawl', 'tavily'];
	let defaultWebSearchModes: Array<{ value: WebSearchMode; label: string; disabled?: boolean }> = [];

	const YOUTUBE_LANGUAGES = [
		{ value: 'en', label: 'English' },
		{ value: 'zh-CN', label: '中文（简体）' },
		{ value: 'zh-TW', label: '中文（繁體）' },
		{ value: 'ja', label: '日本語' },
		{ value: 'ko', label: '한국어' },
		{ value: 'es', label: 'Español' },
		{ value: 'fr', label: 'Français' },
		{ value: 'de', label: 'Deutsch' },
		{ value: 'ru', label: 'Русский' },
		{ value: 'ar', label: 'العربية' },
		{ value: 'pt', label: 'Português' },
		{ value: 'hi', label: 'हिन्दी' }
	];
	let initialSnapshot = null;

	let webConfig: any = null;
	let youtubeLanguage = '';
	let youtubeTranslation = '';
	let runtimeCapabilities = {
		playwright_available: true,
		firecrawl_available: true,
		messages: {
			playwright: '',
			firecrawl: ''
		}
	};

	// Task config for query generation toggles
	let enableSearchQueryGeneration = true;
	let enableRetrievalQueryGeneration = true;

	const NUMERIC_FIELD_DEFAULTS = {
		WEB_SEARCH_RESULT_COUNT: 3,
		WEB_SEARCH_CONCURRENT_REQUESTS: 10,
		PLAYWRIGHT_TIMEOUT: 10000,
		FIRECRAWL_TIMEOUT: 30
	} as const;

	type NumericFieldName = keyof typeof NUMERIC_FIELD_DEFAULTS;
	type ValidationDetailItem = {
		loc?: unknown;
		msg?: unknown;
	};

	const NUMERIC_FIELD_LABELS: Record<NumericFieldName, string> = {
		WEB_SEARCH_RESULT_COUNT: '搜索结果数量',
		WEB_SEARCH_CONCURRENT_REQUESTS: '并发请求数',
		PLAYWRIGHT_TIMEOUT: 'Playwright 超时',
		FIRECRAWL_TIMEOUT: 'Firecrawl 超时'
	};

	const parseNumericValue = (value: unknown) => {
		if (typeof value === 'number' && Number.isInteger(value)) {
			return value;
		}

		if (typeof value === 'string') {
			const trimmed = value.trim();
			if (!trimmed) return null;

			const parsed = Number(trimmed);
			if (Number.isInteger(parsed)) {
				return parsed;
			}
		}

		return null;
	};

	const getNumericFieldSection = (field: NumericFieldName) =>
		field === 'PLAYWRIGHT_TIMEOUT' || field === 'FIRECRAWL_TIMEOUT' ? 'loader' : 'webSearch';

	const getSavedNumericValue = (field: NumericFieldName) =>
		initialSnapshot?.[getNumericFieldSection(field)]?.[field];

	const normalizeNumericField = (
		field: NumericFieldName,
		value: unknown,
		fallbackValue: unknown = undefined
	) => {
		const parsedValue = parseNumericValue(value);
		if (parsedValue !== null) {
			return parsedValue;
		}

		const parsedFallback = parseNumericValue(fallbackValue);
		if (parsedFallback !== null) {
			return parsedFallback;
		}

		return NUMERIC_FIELD_DEFAULTS[field];
	};

	const normalizeNumericWebConfig = (config: Record<string, any>, useSavedFallbacks = false) => {
		for (const field of Object.keys(NUMERIC_FIELD_DEFAULTS) as NumericFieldName[]) {
			config[field] = normalizeNumericField(
				field,
				config[field],
				useSavedFallbacks ? getSavedNumericValue(field) : undefined
			);
		}

		return config;
	};

	const syncLocalWebConfigFromPayload = (payloadWeb: Record<string, any>) => {
		for (const field of Object.keys(NUMERIC_FIELD_DEFAULTS) as NumericFieldName[]) {
			webConfig[field] = payloadWeb[field];
		}

		webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST = listToCsv(payloadWeb.WEB_SEARCH_DOMAIN_FILTER_LIST);
		webConfig.YOUTUBE_LOADER_LANGUAGE = listToCsv(payloadWeb.YOUTUBE_LOADER_LANGUAGE);
		webConfig.YOUTUBE_LOADER_TRANSLATION = payloadWeb.YOUTUBE_LOADER_TRANSLATION || '';
		youtubeLanguage = Array.isArray(payloadWeb.YOUTUBE_LOADER_LANGUAGE)
			? payloadWeb.YOUTUBE_LOADER_LANGUAGE[0] ?? ''
			: '';
		youtubeTranslation = payloadWeb.YOUTUBE_LOADER_TRANSLATION || '';
		webConfig = webConfig;
	};

	const formatValidationError = (detail: unknown) => {
		if (typeof detail === 'string' && detail.trim()) {
			return detail;
		}

		if (!Array.isArray(detail)) {
			return null;
		}

		const firstItem =
			detail.find((item) => {
				const candidate = item as ValidationDetailItem;
				return Array.isArray(candidate?.loc);
			}) ?? detail[0];
		const candidate = firstItem as ValidationDetailItem;
		const loc = Array.isArray(candidate?.loc) ? candidate.loc : [];
		const field =
			loc[0] === 'body' && loc[1] === 'web' && typeof loc[2] === 'string' ? loc[2] : null;
		const message = typeof candidate?.msg === 'string' ? candidate.msg : null;

		if (field && field in NUMERIC_FIELD_LABELS && message) {
			return `${NUMERIC_FIELD_LABELS[field as NumericFieldName]}: ${message}`;
		}

		if (field && message) {
			return `${field}: ${message}`;
		}

		return message;
	};

	const getDefaultWebSearchModeFallback = (): WebSearchMode => {
		if (webConfig?.ENABLE_WEB_SEARCH && webConfig?.ENABLE_NATIVE_WEB_SEARCH) return 'halo';
		if (webConfig?.ENABLE_WEB_SEARCH) return 'halo';
		if (webConfig?.ENABLE_NATIVE_WEB_SEARCH) return 'native';
		return 'off';
	};

	const getEnabledDefaultWebSearchModes = (): WebSearchMode[] => {
		const modes: WebSearchMode[] = ['off'];

		if (webConfig?.ENABLE_WEB_SEARCH) {
			modes.push('halo');
		}

		if (webConfig?.ENABLE_NATIVE_WEB_SEARCH) {
			modes.push('native');
		}

		if (webConfig?.ENABLE_WEB_SEARCH && webConfig?.ENABLE_NATIVE_WEB_SEARCH) {
			modes.push('auto');
		}

		return modes;
	};

	const coerceDefaultWebSearchMode = () => {
		if (!webConfig) return;

		const fallback = getDefaultWebSearchModeFallback();
		const normalized = normalizeWebSearchMode(webConfig.DEFAULT_WEB_SEARCH_MODE, fallback);

		webConfig.DEFAULT_WEB_SEARCH_MODE = getEnabledDefaultWebSearchModes().includes(normalized)
			? normalized
			: fallback;
	};

	const buildSnapshot = () => {
		if (!webConfig) return null;

		return {
			webSearch: {
				ENABLE_WEB_SEARCH: webConfig.ENABLE_WEB_SEARCH,
				ENABLE_NATIVE_WEB_SEARCH: webConfig.ENABLE_NATIVE_WEB_SEARCH,
				DEFAULT_WEB_SEARCH_MODE: webConfig.DEFAULT_WEB_SEARCH_MODE,
				WEB_SEARCH_ENGINE: webConfig.WEB_SEARCH_ENGINE,
				SEARXNG_QUERY_URL: webConfig.SEARXNG_QUERY_URL,
				GOOGLE_PSE_API_KEY: webConfig.GOOGLE_PSE_API_KEY,
				GOOGLE_PSE_ENGINE_ID: webConfig.GOOGLE_PSE_ENGINE_ID,
				BRAVE_SEARCH_API_KEY: webConfig.BRAVE_SEARCH_API_KEY,
				KAGI_SEARCH_API_KEY: webConfig.KAGI_SEARCH_API_KEY,
				MOJEEK_SEARCH_API_KEY: webConfig.MOJEEK_SEARCH_API_KEY,
				BOCHA_SEARCH_API_KEY: webConfig.BOCHA_SEARCH_API_KEY,
				SERPSTACK_API_KEY: webConfig.SERPSTACK_API_KEY,
				SERPSTACK_HTTPS: webConfig.SERPSTACK_HTTPS,
				SERPER_API_KEY: webConfig.SERPER_API_KEY,
				SERPLY_API_KEY: webConfig.SERPLY_API_KEY,
				DDGS_BACKEND: webConfig.DDGS_BACKEND,
				TAVILY_API_KEY: webConfig.TAVILY_API_KEY,
				SEARCHAPI_API_KEY: webConfig.SEARCHAPI_API_KEY,
				SEARCHAPI_ENGINE: webConfig.SEARCHAPI_ENGINE,
				SERPAPI_API_KEY: webConfig.SERPAPI_API_KEY,
				SERPAPI_ENGINE: webConfig.SERPAPI_ENGINE,
				JINA_API_KEY: webConfig.JINA_API_KEY,
				JINA_API_BASE_URL: webConfig.JINA_API_BASE_URL,
				BING_SEARCH_V7_ENDPOINT: webConfig.BING_SEARCH_V7_ENDPOINT,
				BING_SEARCH_V7_SUBSCRIPTION_KEY: webConfig.BING_SEARCH_V7_SUBSCRIPTION_KEY,
				EXA_API_KEY: webConfig.EXA_API_KEY,
				PERPLEXITY_API_KEY: webConfig.PERPLEXITY_API_KEY,
				GROK_API_KEY: webConfig.GROK_API_KEY,
				GROK_API_BASE_URL: webConfig.GROK_API_BASE_URL,
				GROK_API_MODEL: webConfig.GROK_API_MODEL,
				GROK_API_MODE: webConfig.GROK_API_MODE,
				SOUGOU_API_SID: webConfig.SOUGOU_API_SID,
				SOUGOU_API_SK: webConfig.SOUGOU_API_SK,
				WEB_SEARCH_RESULT_COUNT: webConfig.WEB_SEARCH_RESULT_COUNT,
				WEB_SEARCH_CONCURRENT_REQUESTS: webConfig.WEB_SEARCH_CONCURRENT_REQUESTS,
				WEB_SEARCH_DOMAIN_FILTER_LIST: webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST,
				BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL:
					webConfig.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL,
				WEB_SEARCH_TRUST_ENV: webConfig.WEB_SEARCH_TRUST_ENV
			},
			loader: {
				WEB_LOADER_ENGINE: webConfig.WEB_LOADER_ENGINE,
				ENABLE_WEB_LOADER_SSL_VERIFICATION: webConfig.ENABLE_WEB_LOADER_SSL_VERIFICATION,
				PLAYWRIGHT_WS_URL: webConfig.PLAYWRIGHT_WS_URL,
				PLAYWRIGHT_TIMEOUT: webConfig.PLAYWRIGHT_TIMEOUT,
				FIRECRAWL_API_BASE_URL: webConfig.FIRECRAWL_API_BASE_URL,
				FIRECRAWL_API_KEY: webConfig.FIRECRAWL_API_KEY,
				FIRECRAWL_TIMEOUT: webConfig.FIRECRAWL_TIMEOUT,
				TAVILY_EXTRACT_DEPTH: webConfig.TAVILY_EXTRACT_DEPTH,
				TAVILY_API_KEY: webConfig.TAVILY_API_KEY,
				YOUTUBE_LOADER_LANGUAGE: youtubeLanguage,
				YOUTUBE_LOADER_PROXY_URL: webConfig.YOUTUBE_LOADER_PROXY_URL,
				YOUTUBE_LOADER_TRANSLATION: youtubeTranslation
			}
		};
	};

	$: loaderEngineOptions = [
		{ value: '', label: $i18n.t('Default') },
		...webLoaderEngines.map((engine) => ({
			value: engine,
			label: engine,
			disabled:
				(engine === 'playwright' && !runtimeCapabilities.playwright_available) ||
				(engine === 'firecrawl' && !runtimeCapabilities.firecrawl_available)
		}))
	];
	$: selectedLoaderCapabilityMessage =
		webConfig?.WEB_LOADER_ENGINE === 'playwright'
			? runtimeCapabilities.messages.playwright
			: webConfig?.WEB_LOADER_ENGINE === 'firecrawl'
				? runtimeCapabilities.messages.firecrawl
				: '';
	$: selectedLoaderUnavailable =
		(webConfig?.WEB_LOADER_ENGINE === 'playwright' && !runtimeCapabilities.playwright_available) ||
		(webConfig?.WEB_LOADER_ENGINE === 'firecrawl' && !runtimeCapabilities.firecrawl_available);

	let snapshot: ReturnType<typeof buildSnapshot> = null;
	$: {
		webConfig;
		youtubeLanguage;
		youtubeTranslation;
		snapshot = buildSnapshot();
	}
	$: dirtySections = initialSnapshot && snapshot
		? {
				webSearch: !isSettingsSnapshotEqual(snapshot.webSearch, initialSnapshot.webSearch),
				loader: !isSettingsSnapshotEqual(snapshot.loader, initialSnapshot.loader)
			}
		: { webSearch: false, loader: false };

	const csvToList = (value: unknown) => {
		if (Array.isArray(value)) {
			return value
				.map((v) => String(v).trim())
				.filter((v) => v.length > 0);
		}

		if (typeof value !== 'string') return [];

		return value
			.split(',')
			.map((v) => v.trim())
			.filter((v) => v.length > 0);
	};

	const listToCsv = (value: unknown) => {
		if (Array.isArray(value)) {
			return value
				.map((v) => String(v).trim())
				.filter((v) => v.length > 0)
				.join(',');
		}

		return typeof value === 'string' ? value : '';
	};

	const loadConfig = async () => {
		loading = true;
		try {
			const [res, taskRes] = await Promise.all([
				getRAGConfig(localStorage.token),
				getTaskConfig(localStorage.token)
			]);

			if (res?.web) {
				webConfig = res.web;
				runtimeCapabilities = res?.capabilities ?? runtimeCapabilities;
				webConfig.ENABLE_WEB_SEARCH = webConfig.ENABLE_WEB_SEARCH ?? false;
				webConfig.ENABLE_NATIVE_WEB_SEARCH = webConfig.ENABLE_NATIVE_WEB_SEARCH ?? false;
				webConfig.DEFAULT_WEB_SEARCH_MODE = normalizeWebSearchMode(
					webConfig.DEFAULT_WEB_SEARCH_MODE,
					'halo'
				);
				normalizeNumericWebConfig(webConfig);
				webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST = listToCsv(webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST);
				webConfig.YOUTUBE_LOADER_LANGUAGE = listToCsv(webConfig.YOUTUBE_LOADER_LANGUAGE);
				const langArray = csvToList(webConfig.YOUTUBE_LOADER_LANGUAGE);
				youtubeLanguage = langArray.length > 0 ? langArray[0] : '';
				youtubeTranslation = webConfig.YOUTUBE_LOADER_TRANSLATION || '';
				coerceDefaultWebSearchMode();
				initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
			} else {
				webConfig = null;
			}

			if (taskRes) {
				enableSearchQueryGeneration = taskRes.ENABLE_SEARCH_QUERY_GENERATION ?? true;
				enableRetrievalQueryGeneration = taskRes.ENABLE_RETRIEVAL_QUERY_GENERATION ?? true;
			}
		} catch (error) {
			console.error('Failed to load web config', error);
			webConfig = null;
			toast.error($i18n.t('Failed to update settings'));
		} finally {
			loading = false;
		}
	};

	const submitHandler = async () => {
		if (!webConfig) return false;
		if (selectedLoaderUnavailable) {
			toast.error(selectedLoaderCapabilityMessage || $i18n.t('Current web loader is unavailable.'));
			return false;
		}

		coerceDefaultWebSearchMode();

		// Sync UI values back to webConfig
		webConfig.YOUTUBE_LOADER_LANGUAGE = youtubeLanguage;
		webConfig.YOUTUBE_LOADER_TRANSLATION = youtubeTranslation;

		// Use a copy so the UI stays as CSV strings even if the request fails.
		const payloadWeb = normalizeNumericWebConfig({ ...webConfig }, true);
		payloadWeb.WEB_SEARCH_DOMAIN_FILTER_LIST = csvToList(payloadWeb.WEB_SEARCH_DOMAIN_FILTER_LIST);
		payloadWeb.YOUTUBE_LOADER_LANGUAGE = youtubeLanguage ? [youtubeLanguage] : [];

		try {
			await updateRAGConfig(localStorage.token, {
				web: payloadWeb
			});

			syncLocalWebConfigFromPayload(payloadWeb);
			initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
		} catch (error) {
			console.error('Failed to update web search config', error);
			toast.error(formatValidationError(error) ?? $i18n.t('Failed to update settings'));
			return false;
		}

		try {
			await updateTaskConfig(localStorage.token, {
				ENABLE_SEARCH_QUERY_GENERATION: enableSearchQueryGeneration,
				ENABLE_RETRIEVAL_QUERY_GENERATION: enableRetrievalQueryGeneration
			});
			return true;
		} catch (error) {
			console.error('Failed to update task config', error);
			await loadConfig();
			toast.warning($i18n.t('联网搜索配置已保存，但查询生成开关未更新'));
			return false;
		}
	};

	onMount(loadConfig);

	const resetSectionChanges = (section: 'webSearch' | 'loader') => {
		if (!initialSnapshot || !webConfig) return;
		Object.assign(webConfig, cloneSettingsSnapshot(initialSnapshot[section]));
		if (section === 'loader') {
			youtubeLanguage = initialSnapshot.loader.YOUTUBE_LOADER_LANGUAGE;
			youtubeTranslation = initialSnapshot.loader.YOUTUBE_LOADER_TRANSLATION;
		}
		webConfig = webConfig;
	};

	$: defaultWebSearchModes = [
		{ value: 'off', label: $i18n.t('Off') },
		{ value: 'halo', label: 'HaloWebUI', disabled: !webConfig?.ENABLE_WEB_SEARCH },
		{
			value: 'native',
			label: $i18n.t('Model Native'),
			disabled: !webConfig?.ENABLE_NATIVE_WEB_SEARCH
		},
		{
			value: 'auto',
			label: $i18n.t('Auto'),
			disabled: !webConfig?.ENABLE_WEB_SEARCH || !webConfig?.ENABLE_NATIVE_WEB_SEARCH
		}
	];

	$: if (webConfig) {
		coerceDefaultWebSearchMode();
	}
</script>

{#if !loading && !webConfig}
	<div class="h-full w-full flex justify-center items-center">
		<div class="max-w-md mx-auto text-center space-y-4">
			<div class="text-sm font-medium text-gray-800 dark:text-gray-100">
				{$i18n.t('Failed to update settings')}
			</div>
			<p class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('Please try again later.')}
			</p>
			<button
				type="button"
				class="px-4 py-2 text-sm font-medium bg-gray-900 text-white dark:bg-white dark:text-gray-900 rounded-xl transition hover:opacity-90 active:scale-[0.98]"
				on:click={loadConfig}
			>
				{$i18n.t('Retry')}
			</button>
		</div>
	</div>
{:else}
	<form
		class="flex h-full min-h-0 flex-col text-sm"
		on:submit|preventDefault={async () => {
			if (saving) return;
			saving = true;
			const ok = await submitHandler();
			saving = false;

			if (ok) {
				await saveHandler?.();
			}
		}}
	>
		<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
			<div class="max-w-6xl mx-auto space-y-6">
					<!-- ====== 标头卡片 Hero ====== -->
				<section class="glass-section p-5 space-y-5">
					<div class="@container flex flex-col gap-5">
						<div class="flex flex-col gap-4 @[64rem]:flex-row @[64rem]:items-start @[64rem]:justify-between">
							<div class="min-w-0 @[64rem]:flex-1">
								<!-- Breadcrumb -->
								<div class="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-full border border-gray-200/80 bg-white/80 px-3.5 text-xs font-medium leading-none text-gray-600 dark:border-gray-700/80 dark:bg-gray-900/70 dark:text-gray-300">
									<span class="leading-none text-gray-400 dark:text-gray-500">{$i18n.t('Settings')}</span>
									<span class="leading-none text-gray-300 dark:text-gray-600">/</span>
									<span class="leading-none text-gray-900 dark:text-white">{$i18n.t('联网搜索')}</span>
								</div>

								<!-- Icon + Title + Description -->
								<div class="mt-3 flex items-start gap-3">
									<div class="glass-icon-badge {activeTabMeta.badgeColor}">
										{#if selectedTab === 'webSearch'}
										<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
											<path fill-rule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25ZM6.262 6.072a8.25 8.25 0 1010.562-.766 4.5 4.5 0 01-1.318 1.357L14.25 7.5l.165.33a.809.809 0 01-1.086 1.085l-.604-.302a1.125 1.125 0 00-1.298.21l-.132.131c-.439.44-.439 1.152 0 1.591l.296.296c.256.257.622.374.98.314l1.17-.195c.323-.054.654.036.905.245l1.33 1.108c.32.267.46.694.358 1.1a8.7 8.7 0 01-2.288 4.04l-.723.724a1.125 1.125 0 01-1.298.21l-.153-.076a1.125 1.125 0 01-.622-1.006v-1.089c0-.298-.119-.585-.33-.796l-1.347-1.347a1.125 1.125 0 01-.21-1.298L9.75 12l-1.64-1.64a6 6 0 01-1.676-3.257l-.172-1.03Z" clip-rule="evenodd" />
										</svg>
										{:else}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
											<path stroke-linecap="round" stroke-linejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 0 1-2.25 2.25M16.5 7.5V18a2.25 2.25 0 0 0 2.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 0 0 2.25 2.25h13.5M6 7.5h3v3H6v-3Z" />
										</svg>
										{/if}
									</div>
									<div class="min-w-0">
										<div class="flex items-center gap-3">
											<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
												{$i18n.t(activeTabMeta.label)}
											</div>
											<InlineDirtyActions
												dirty={selectedTab === 'webSearch' ? dirtySections.webSearch : dirtySections.loader}
												{saving}
												on:reset={() => resetSectionChanges(selectedTab === 'webSearch' ? 'webSearch' : 'loader')}
											/>
										</div>
										<p class="mt-1 text-xs text-gray-400 dark:text-gray-500">
											{$i18n.t(activeTabMeta.description)}
										</p>
									</div>
								</div>
							</div>

							<!-- Tab buttons -->
							<div class="inline-flex max-w-full flex-wrap items-center gap-2 self-start rounded-2xl bg-gray-100 p-1 dark:bg-gray-850 @[64rem]:ml-auto @[64rem]:mt-11 @[64rem]:flex-nowrap @[64rem]:justify-end @[64rem]:shrink-0">
								<button type="button" class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === 'webSearch' ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`} on:click={() => { selectedTab = 'webSearch'; }}>
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-4">
										<path fill-rule="evenodd" d="M12 2.25c-5.385 0-9.75 4.365-9.75 9.75s4.365 9.75 9.75 9.75 9.75-4.365 9.75-9.75S17.385 2.25 12 2.25ZM6.262 6.072a8.25 8.25 0 1010.562-.766 4.5 4.5 0 01-1.318 1.357L14.25 7.5l.165.33a.809.809 0 01-1.086 1.085l-.604-.302a1.125 1.125 0 00-1.298.21l-.132.131c-.439.44-.439 1.152 0 1.591l.296.296c.256.257.622.374.98.314l1.17-.195c.323-.054.654.036.905.245l1.33 1.108c.32.267.46.694.358 1.1a8.7 8.7 0 01-2.288 4.04l-.723.724a1.125 1.125 0 01-1.298.21l-.153-.076a1.125 1.125 0 01-.622-1.006v-1.089c0-.298-.119-.585-.33-.796l-1.347-1.347a1.125 1.125 0 01-.21-1.298L9.75 12l-1.64-1.64a6 6 0 01-1.676-3.257l-.172-1.03Z" clip-rule="evenodd" />
									</svg>
									<span class="min-w-0 truncate">{$i18n.t('联网搜索')}</span>
								</button>
								<button type="button" class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === 'loader' ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`} on:click={() => { selectedTab = 'loader'; }}>
									<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
										<path stroke-linecap="round" stroke-linejoin="round" d="M12 7.5h1.5m-1.5 3h1.5m-7.5 3h7.5m-7.5 3h7.5m3-9h3.375c.621 0 1.125.504 1.125 1.125V18a2.25 2.25 0 0 1-2.25 2.25M16.5 7.5V18a2.25 2.25 0 0 0 2.25 2.25M16.5 7.5V4.875c0-.621-.504-1.125-1.125-1.125H4.125C3.504 3.75 3 4.254 3 4.875V18a2.25 2.25 0 0 0 2.25 2.25h13.5M6 7.5h3v3H6v-3Z" />
									</svg>
									<span class="min-w-0 truncate">{$i18n.t('网页加载器')}</span>
								</button>
							</div>
						</div>
					</div>
				</section>

				{#if loading}
				<div class="flex justify-center py-16">
					<Spinner className="size-5" />
				</div>
				{:else if selectedTab === 'webSearch'}
				<!-- ====== 联网搜索内容 Web Search Content ====== -->
				<section
					bind:this={sectionEl_webSearch}
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {dirtySections.webSearch
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="space-y-3">
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div class="flex items-center justify-between glass-item px-4 py-3">
									<div>
										<div class="text-sm font-medium">{$i18n.t('Enable Native Web Search')}</div>
										<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
											{$i18n.t('Allow supported provider connections to use model-built-in web search tools.')}
										</div>
									</div>
									<Switch bind:state={webConfig.ENABLE_NATIVE_WEB_SEARCH} />
								</div>

								<div class="glass-item px-4 py-3">
									<div class="flex items-center justify-between gap-4">
										<div>
											<div class="text-sm font-medium">{$i18n.t('Default Web Search Mode')}</div>
											<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
												{$i18n.t('Used as the default chat preference for new sessions.')}
											</div>
										</div>
										<HaloSelect
											bind:value={webConfig.DEFAULT_WEB_SEARCH_MODE}
											options={defaultWebSearchModes}
											className="w-44"
										/>
									</div>
								</div>
							</div>

							<div class="pl-1 text-xs text-gray-500 dark:text-gray-400">
								{$i18n.t('The following engine, loader, result count, domain filter, and query generation settings only apply to HaloWebUI search mode.')}
							</div>

							<!-- Enable + Engine Selection -->
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">{$i18n.t('Enable HaloWebUI Search')}</div>
									<Switch bind:state={webConfig.ENABLE_WEB_SEARCH} />
								</div>

								<div
									class="glass-item px-4 py-3"
								>
									<div class="flex items-center justify-between">
										<div>
											<div class="text-sm font-medium">{$i18n.t('Search Engine')}</div>
											<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
												{$i18n.t('HaloWebUI mode only')}
											</div>
										</div>
										<HaloSelect
											bind:value={webConfig.WEB_SEARCH_ENGINE}
											options={webSearchEngines.map((e) => ({ value: e, label: e }))}
											placeholder={$i18n.t('Select a engine')}
											className="w-fit capitalize"
										/>
									</div>
								</div>
							</div>

							<!-- Engine Credentials -->
							{#if webConfig.WEB_SEARCH_ENGINE}
								<div class="space-y-3">
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
										{$i18n.t('Engine Credentials')}
									</div>

									{#if webConfig.WEB_SEARCH_ENGINE === 'searxng'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('SearXNG Query URL')}</div>
											<input
												class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
												type="text"
												placeholder={$i18n.t('Enter SearXNG Query URL')}
												bind:value={webConfig.SEARXNG_QUERY_URL}
												autocomplete="off"
											/>
											<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
												{$i18n.t('Example: http://searxng:8080/search?q=<query>')}
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'google_pse'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Google PSE API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Google PSE API Key')}
													bind:value={webConfig.GOOGLE_PSE_API_KEY}
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Google PSE Engine ID')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Google PSE Engine ID')}
													bind:value={webConfig.GOOGLE_PSE_ENGINE_ID}
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'brave'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Brave Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Brave Search API Key')}
												bind:value={webConfig.BRAVE_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'kagi'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Kagi Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Kagi Search API Key')}
												bind:value={webConfig.KAGI_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'mojeek'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Mojeek Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Mojeek Search API Key')}
												bind:value={webConfig.MOJEEK_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'bocha'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Bocha Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Bocha Search API Key')}
												bind:value={webConfig.BOCHA_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serpstack'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Serpstack API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Serpstack API Key')}
													bind:value={webConfig.SERPSTACK_API_KEY}
												/>
											</div>
											<div
												class="flex items-center justify-between glass-item px-4 py-3"
											>
												<div class="text-sm font-medium">{$i18n.t('Use HTTPS')}</div>
												<Switch bind:state={webConfig.SERPSTACK_HTTPS} />
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serper'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Serper API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Serper API Key')}
												bind:value={webConfig.SERPER_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serply'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Serply API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Serply API Key')}
												bind:value={webConfig.SERPLY_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'duckduckgo'}
										<div
											class="glass-item px-4 py-3"
										>
											<div class="flex items-center justify-between">
												<div class="text-sm font-medium">{$i18n.t('DuckDuckGo Backend')}</div>
												<HaloSelect
													bind:value={webConfig.DDGS_BACKEND}
													options={[
														{ value: 'lite', label: $i18n.t('Lite') },
														{ value: 'api', label: 'API' },
														{ value: 'html', label: 'HTML' }
													]}
													className="w-fit"
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'tavily'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Tavily API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Tavily API Key')}
												bind:value={webConfig.TAVILY_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'searchapi'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('SearchApi API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter SearchApi API Key')}
													bind:value={webConfig.SEARCHAPI_API_KEY}
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('SearchApi Engine')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder={$i18n.t('Enter SearchApi Engine')}
													bind:value={webConfig.SEARCHAPI_ENGINE}
													autocomplete="off"
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serpapi'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('SerpApi API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter SerpApi API Key')}
													bind:value={webConfig.SERPAPI_API_KEY}
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('SerpApi Engine')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder={$i18n.t('Enter SerpApi Engine')}
													bind:value={webConfig.SERPAPI_ENGINE}
													autocomplete="off"
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'jina'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Jina API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Jina API Key')}
													bind:value={webConfig.JINA_API_KEY}
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Jina API Base URL')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder="https://s.jina.ai/"
													bind:value={webConfig.JINA_API_BASE_URL}
													autocomplete="off"
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'bing'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Bing Search V7 Endpoint')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder={$i18n.t('Enter Bing Search V7 Endpoint')}
													bind:value={webConfig.BING_SEARCH_V7_ENDPOINT}
													autocomplete="off"
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Bing Search V7 Subscription Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Bing Search V7 Subscription Key')}
													bind:value={webConfig.BING_SEARCH_V7_SUBSCRIPTION_KEY}
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'exa'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Exa API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Exa API Key')}
												bind:value={webConfig.EXA_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'perplexity'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Perplexity API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Perplexity API Key')}
												bind:value={webConfig.PERPLEXITY_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'grok'}
										<div class="grid grid-cols-1 md:grid-cols-3 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Grok API Base URL')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder="https://api.x.ai"
													bind:value={webConfig.GROK_API_BASE_URL}
													autocomplete="off"
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Grok Model')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder="grok-4-1-fast"
													bind:value={webConfig.GROK_API_MODEL}
													autocomplete="off"
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('API Mode')}</div>
												<select
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													bind:value={webConfig.GROK_API_MODE}
												>
													<option value="chat_completions">Chat Completions</option>
													<option value="responses">Responses API</option>
												</select>
											</div>
										</div>
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Grok API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter xAI Grok API Key')}
												bind:value={webConfig.GROK_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'sougou'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Sougou Search API sID')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Sougou Search API sID')}
													bind:value={webConfig.SOUGOU_API_SID}
												/>
											</div>
											<div
												class="glass-item p-4"
											>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Sougou Search API SK')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Sougou Search API SK')}
													bind:value={webConfig.SOUGOU_API_SK}
												/>
											</div>
										</div>
									{/if}
								</div>
							{/if}

							<!-- Search Settings -->
							{#if webConfig.ENABLE_WEB_SEARCH}
								<div class="space-y-3">
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
										{$i18n.t('Search Settings')}
									</div>
									<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Search Result Count')}</div>
											<input
												class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
												type="number"
												placeholder={$i18n.t('Search Result Count')}
												bind:value={webConfig.WEB_SEARCH_RESULT_COUNT}
												required
											/>
										</div>
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Concurrent Requests')}</div>
											<input
												class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
												type="number"
												placeholder={$i18n.t('Concurrent Requests')}
												bind:value={webConfig.WEB_SEARCH_CONCURRENT_REQUESTS}
												required
											/>
										</div>
									</div>
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Domain Filter List')}</div>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											placeholder={$i18n.t(
												'Enter domains separated by commas (e.g., example.com,site.org)'
											)}
											bind:value={webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST}
										/>
										<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
											{$i18n.t('Comma-separated list of domains to filter search results')}
										</div>
									</div>
								</div>
							{/if}

							<!-- Advanced Options -->
							<div class="space-y-3">
								<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
									{$i18n.t('Advanced Options')}
								</div>
								<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
									<div
										class="flex items-center justify-between glass-item px-4 py-3"
									>
										<div class="text-sm font-medium">{$i18n.t('Retrieval Query Generation')}</div>
										<Switch bind:state={enableRetrievalQueryGeneration} />
									</div>

									<div
										class="flex items-center justify-between glass-item px-4 py-3"
									>
										<div class="text-sm font-medium">{$i18n.t('Web Search Query Generation')}</div>
										<Switch bind:state={enableSearchQueryGeneration} />
									</div>

									<div
										class="flex items-center justify-between glass-item px-4 py-3"
									>
										<div class="text-sm font-medium">
											<Tooltip content={$i18n.t('Full Context Mode')} placement="top-start">
												{$i18n.t('Bypass Embedding and Retrieval')}
											</Tooltip>
										</div>
										<Tooltip
											content={webConfig.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
												? $i18n.t(
														'Inject the entire content as context for comprehensive processing, this is recommended for complex queries.'
													)
												: $i18n.t(
														'Default to segmented retrieval for focused and relevant content extraction, this is recommended for most cases.'
													)}
										>
											<Switch bind:state={webConfig.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL} />
										</Tooltip>
									</div>

									<div
										class="flex items-center justify-between glass-item px-4 py-3"
									>
										<div class="text-sm font-medium">{$i18n.t('Trust Proxy Environment')}</div>
										<Tooltip
											content={webConfig.WEB_SEARCH_TRUST_ENV
												? $i18n.t(
														'Use proxy designated by http_proxy and https_proxy environment variables to fetch page contents.'
													)
												: $i18n.t('Use no proxy to fetch page contents.')}
										>
											<Switch bind:state={webConfig.WEB_SEARCH_TRUST_ENV} />
										</Tooltip>
									</div>
								</div>
							</div>
						</div>
				</section>

				{:else if selectedTab === 'loader'}
				<!-- ====== 网页加载器 Web Loader ====== -->
				<section
					bind:this={sectionEl_loader}
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {dirtySections.loader
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="space-y-3">
							<!-- Loader Engine -->
							<div
								class="glass-item px-4 py-3"
							>
								<div class="flex items-center justify-between">
									<div class="text-sm font-medium">{$i18n.t('Web Loader Engine')}</div>
									<HaloSelect
										bind:value={webConfig.WEB_LOADER_ENGINE}
										options={loaderEngineOptions}
										placeholder={$i18n.t('Select a engine')}
										className="w-fit capitalize"
									/>
								</div>
							</div>

							{#if selectedLoaderUnavailable}
								<div class="rounded-xl border border-amber-200/70 bg-amber-50 px-4 py-3 text-xs leading-relaxed text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
									{selectedLoaderCapabilityMessage}
								</div>
							{/if}

							<!-- Engine-specific config -->
							{#if webConfig.WEB_LOADER_ENGINE === '' || webConfig.WEB_LOADER_ENGINE === 'safe_web'}
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">{$i18n.t('Verify SSL Certificate')}</div>
									<Switch bind:state={webConfig.ENABLE_WEB_LOADER_SSL_VERIFICATION} />
								</div>
							{:else if webConfig.WEB_LOADER_ENGINE === 'playwright'}
								<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Playwright WebSocket URL')}</div>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											type="text"
											placeholder={$i18n.t('Enter Playwright WebSocket URL')}
											bind:value={webConfig.PLAYWRIGHT_WS_URL}
											autocomplete="off"
										/>
									</div>
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Playwright Timeout (ms)')}</div>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											type="number"
											placeholder={$i18n.t('Enter Playwright Timeout')}
											bind:value={webConfig.PLAYWRIGHT_TIMEOUT}
											autocomplete="off"
										/>
									</div>
								</div>
							{:else if webConfig.WEB_LOADER_ENGINE === 'firecrawl'}
								<div class="space-y-3">
									<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Firecrawl API Base URL')}</div>
											<input
												class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
												type="text"
												placeholder={$i18n.t('Enter Firecrawl API Base URL')}
												bind:value={webConfig.FIRECRAWL_API_BASE_URL}
												autocomplete="off"
											/>
										</div>
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Firecrawl API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Firecrawl API Key')}
												bind:value={webConfig.FIRECRAWL_API_KEY}
											/>
										</div>
									</div>
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Firecrawl Timeout (seconds)')}</div>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											type="number"
											placeholder="30"
											bind:value={webConfig.FIRECRAWL_TIMEOUT}
											autocomplete="off"
										/>
									</div>
								</div>
							{:else if webConfig.WEB_LOADER_ENGINE === 'tavily'}
								<div class="space-y-3">
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Tavily Extract Depth')}</div>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											type="text"
											placeholder={$i18n.t('Enter Tavily Extract Depth')}
											bind:value={webConfig.TAVILY_EXTRACT_DEPTH}
											autocomplete="off"
										/>
									</div>
									{#if webConfig.WEB_SEARCH_ENGINE !== 'tavily'}
										<div
											class="glass-item p-4"
										>
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Tavily API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Tavily API Key')}
												bind:value={webConfig.TAVILY_API_KEY}
											/>
										</div>
									{/if}
								</div>
							{/if}

							<!-- Loader Settings -->
							<div class="space-y-3">
								<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
									{$i18n.t('Loader Settings')}
								</div>
								<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Youtube Language')}</div>
										<HaloSelect
											bind:value={youtubeLanguage}
											options={YOUTUBE_LANGUAGES}
											placeholder={$i18n.t('Select a language')}
											className="w-full"
										/>
									</div>
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Youtube Translation Language')}</div>
										<HaloSelect
											bind:value={youtubeTranslation}
											options={YOUTUBE_LANGUAGES}
											placeholder={$i18n.t('Select a language')}
											className="w-full"
										/>
									</div>
								</div>
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Youtube Proxy URL')}</div>
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										type="text"
										placeholder={$i18n.t('Enter proxy URL (e.g. https://user:password@host:port)')}
										bind:value={webConfig.YOUTUBE_LOADER_PROXY_URL}
										autocomplete="off"
									/>
								</div>
							</div>
					</div>
				</section>
				{/if}
			</div>
		</div>
	</form>
{/if}
