<script lang="ts">
	import type { Writable } from 'svelte/store';
	import {
		getRAGConfig,
		updateRAGConfig,
		verifyTavilyWebConfig,
		type TavilyConfigVerifyItem,
		type TavilyConfigVerifyResponse
	} from '$lib/apis/retrieval';
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
	import { translateWithDefault } from '$lib/i18n';

	const i18n: Writable<any> = getContext('i18n');
	const tr = (key: string, defaultValue: string) =>
		translateWithDefault($i18n, key, defaultValue);

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
	const WEB_SEARCH_ENGINE_LABELS: Record<string, string> = {
		searxng: 'SearXNG',
		google_pse: 'Google PSE',
		brave: 'Brave',
		kagi: 'Kagi',
		mojeek: 'Mojeek',
		bocha: 'Bocha',
		serpstack: 'Serpstack',
		serper: 'Serper',
		serply: 'Serply',
		searchapi: 'SearchApi',
		serpapi: 'SerpApi',
		duckduckgo: 'DuckDuckGo',
		tavily: 'Tavily',
		jina: 'Jina',
		bing: 'Bing',
		exa: 'Exa',
		perplexity: 'Perplexity',
		grok: 'Grok',
		sougou: 'Sougou'
	};

	const getWebSearchEngineLabel = (engine: string) => WEB_SEARCH_ENGINE_LABELS[engine] ?? engine;

	$: webSearchEngineOptions = webSearchEngines.map((engine) => ({
		value: engine,
		label: getWebSearchEngineLabel(engine)
	}));
	$: currentWebSearchEngineOption =
		webSearchEngineOptions.find((option) => option.value === webConfig?.WEB_SEARCH_ENGINE) ?? null;
	$: ddgsBackendOptions = [
		{ value: 'lite', label: $i18n.t('Lite') },
		{ value: 'api', label: 'API' },
		{ value: 'html', label: 'HTML' }
	];
	$: grokApiModeOptions = [
		{ value: 'chat_completions', label: 'Chat Completions' },
		{ value: 'responses', label: 'Responses API' }
	];

	let YOUTUBE_LANGUAGES = [];
	$: YOUTUBE_LANGUAGES = [
		{ value: 'en', label: 'English' },
		{ value: 'zh-CN', label: tr('中文（简体）', 'Simplified Chinese') },
		{ value: 'zh-TW', label: tr('中文（繁體）', 'Traditional Chinese') },
		{ value: 'ja', label: tr('日本語', 'Japanese') },
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
	let tavilySearchBaseUrlInput = '';
	let tavilyExtractBaseUrlInput = '';
	let runtimeCapabilities = {
		playwright_available: true,
		firecrawl_available: true,
		messages: {
			playwright: '',
			firecrawl: ''
		}
	};
	let tavilyVerifyLoading = false;
	let tavilyVerifyResult: TavilyConfigVerifyResponse | null = null;
	let lastTavilyVerifyFingerprint = '';

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
	type TavilyEndpoint = 'search' | 'extract';
	type TavilyUrlState = {
		baseUrl: string;
		forceMode: boolean;
		previewUrl: string;
		error: string | null;
	};
	type TavilyVerifyFingerprint = {
		WEB_SEARCH_ENGINE: string;
		WEB_LOADER_ENGINE: string;
		TAVILY_API_KEY: string;
		TAVILY_SEARCH_API_BASE_URL: string;
		TAVILY_SEARCH_API_FORCE_MODE: boolean;
		TAVILY_EXTRACT_API_BASE_URL: string;
		TAVILY_EXTRACT_API_FORCE_MODE: boolean;
		TAVILY_EXTRACT_DEPTH: string;
	};

	const NUMERIC_FIELD_LABEL_KEYS: Record<NumericFieldName, string> = {
		WEB_SEARCH_RESULT_COUNT: '搜索结果数量',
		WEB_SEARCH_CONCURRENT_REQUESTS: '并发请求数',
		PLAYWRIGHT_TIMEOUT: 'Playwright 超时',
		FIRECRAWL_TIMEOUT: 'Firecrawl 超时'
	};
	const DEFAULT_TAVILY_API_BASE_URL = 'https://api.tavily.com';
	const TAVILY_URL_TOOLTIP =
		'Tavily URL accepts a base URL or the matching endpoint. Add # at the end to use the exact URL without auto-appending /search or /extract.';
	const TAVILY_FORCE_MODE_DESCRIPTION =
		'Force mode uses the exact URL and will not auto-append /search or /extract.';

	const getTavilyEndpointLabel = (endpoint: TavilyEndpoint) =>
		endpoint === 'search' ? 'search' : 'extract';

	const getUrlOriginWithAuth = (parsed: URL) => {
		const auth = parsed.username
			? `${parsed.username}${parsed.password ? `:${parsed.password}` : ''}@`
			: '';
		return `${parsed.protocol}//${auth}${parsed.host}`;
	};

	const buildTavilyPreviewUrl = (baseUrl: string, endpoint: TavilyEndpoint, forceMode = false) => {
		const normalizedBaseUrl = (baseUrl || DEFAULT_TAVILY_API_BASE_URL).trim().replace(/\/+$/, '');
		if (!normalizedBaseUrl) return '';
		if (forceMode) return normalizedBaseUrl;

		try {
			const parsed = new URL(normalizedBaseUrl);
			const path = parsed.pathname.replace(/\/+$/, '');
			const nextPath = path ? `${path}/${endpoint}` : `/${endpoint}`;
			return `${getUrlOriginWithAuth(parsed)}${nextPath}${parsed.search}`;
		} catch {
			return `${normalizedBaseUrl}/${endpoint}`;
		}
	};

	const restoreTavilyUrlInput = (baseUrl: unknown, forceMode: unknown) => {
		const normalizedBaseUrl =
			typeof baseUrl === 'string' && baseUrl.trim()
				? baseUrl.trim().replace(/\/+$/, '')
				: DEFAULT_TAVILY_API_BASE_URL;

		return forceMode ? `${normalizedBaseUrl}#` : normalizedBaseUrl;
	};

	const parseTavilyUrlInput = (input: string, endpoint: TavilyEndpoint): TavilyUrlState => {
		const rawInput = String(input ?? '').trim();
		const explicitForceMode = rawInput.endsWith('#');
		const rawUrl = explicitForceMode ? rawInput.slice(0, -1).trim() : rawInput;
		const normalizedInput = rawUrl.replace(/\/+$/, '');

		if (!normalizedInput) {
			return {
				baseUrl: DEFAULT_TAVILY_API_BASE_URL,
				forceMode: false,
				previewUrl: buildTavilyPreviewUrl(DEFAULT_TAVILY_API_BASE_URL, endpoint),
				error: null
			};
		}

		if (!/^https?:\/\//i.test(normalizedInput)) {
			return {
				baseUrl: normalizedInput,
				forceMode: explicitForceMode,
				previewUrl: normalizedInput,
				error: $i18n.t('Tavily {{endpoint}} URL must start with http:// or https://.', {
					endpoint: getTavilyEndpointLabel(endpoint)
				})
			};
		}

		try {
			const parsed = new URL(normalizedInput);
			if (explicitForceMode) {
				return {
					baseUrl: normalizedInput,
					forceMode: true,
					previewUrl: normalizedInput,
					error: null
				};
			}

			let path = parsed.pathname.replace(/\/+$/, '');
			const endpointSuffix = `/${endpoint}`;
			const wrongEndpoint: TavilyEndpoint = endpoint === 'search' ? 'extract' : 'search';
			const wrongSuffix = `/${wrongEndpoint}`;

			if (path.toLowerCase().endsWith(wrongSuffix)) {
				return {
					baseUrl: normalizedInput,
					forceMode: false,
					previewUrl: normalizedInput,
					error: $i18n.t(
						'Tavily {{endpoint}} URL cannot end with {{wrongSuffix}}. Use a base URL or an endpoint ending with {{endpointSuffix}}.',
						{
							endpoint: getTavilyEndpointLabel(endpoint),
							wrongSuffix,
							endpointSuffix
						}
					)
				};
			}

			if (path.toLowerCase().endsWith(endpointSuffix)) {
				path = path.slice(0, -endpointSuffix.length).replace(/\/+$/, '');
			}

			const baseUrl = `${getUrlOriginWithAuth(parsed)}${path}${parsed.search}`;
			return {
				baseUrl,
				forceMode: false,
				previewUrl: buildTavilyPreviewUrl(baseUrl, endpoint),
				error: null
			};
		} catch {
			return {
				baseUrl: normalizedInput,
				forceMode: explicitForceMode,
				previewUrl: normalizedInput,
				error: $i18n.t('Tavily {{endpoint}} URL is invalid.', {
					endpoint: getTavilyEndpointLabel(endpoint)
				})
			};
		}
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

		webConfig.TAVILY_API_KEY = payloadWeb.TAVILY_API_KEY || '';
		webConfig.TAVILY_SEARCH_API_BASE_URL =
			payloadWeb.TAVILY_SEARCH_API_BASE_URL || DEFAULT_TAVILY_API_BASE_URL;
		webConfig.TAVILY_SEARCH_API_FORCE_MODE = payloadWeb.TAVILY_SEARCH_API_FORCE_MODE ?? false;
		webConfig.TAVILY_EXTRACT_API_BASE_URL =
			payloadWeb.TAVILY_EXTRACT_API_BASE_URL || DEFAULT_TAVILY_API_BASE_URL;
		webConfig.TAVILY_EXTRACT_API_FORCE_MODE = payloadWeb.TAVILY_EXTRACT_API_FORCE_MODE ?? false;
		webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST = listToCsv(payloadWeb.WEB_SEARCH_DOMAIN_FILTER_LIST);
		webConfig.YOUTUBE_LOADER_LANGUAGE = listToCsv(payloadWeb.YOUTUBE_LOADER_LANGUAGE);
		webConfig.YOUTUBE_LOADER_TRANSLATION = payloadWeb.YOUTUBE_LOADER_TRANSLATION || '';
		youtubeLanguage = Array.isArray(payloadWeb.YOUTUBE_LOADER_LANGUAGE)
			? payloadWeb.YOUTUBE_LOADER_LANGUAGE[0] ?? ''
			: '';
		youtubeTranslation = payloadWeb.YOUTUBE_LOADER_TRANSLATION || '';
		tavilySearchBaseUrlInput = restoreTavilyUrlInput(
			webConfig.TAVILY_SEARCH_API_BASE_URL,
			webConfig.TAVILY_SEARCH_API_FORCE_MODE
		);
		tavilyExtractBaseUrlInput = restoreTavilyUrlInput(
			webConfig.TAVILY_EXTRACT_API_BASE_URL,
			webConfig.TAVILY_EXTRACT_API_FORCE_MODE
		);
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

		if (field && field in NUMERIC_FIELD_LABEL_KEYS && message) {
			return `${$i18n.t(NUMERIC_FIELD_LABEL_KEYS[field as NumericFieldName], {
				defaultValue:
					field === 'WEB_SEARCH_RESULT_COUNT'
						? 'Search Result Count'
						: field === 'WEB_SEARCH_CONCURRENT_REQUESTS'
							? 'Concurrent Requests'
							: field === 'PLAYWRIGHT_TIMEOUT'
								? 'Playwright Timeout'
								: 'Firecrawl Timeout'
			})}: ${message}`;
		}

		if (field && message) {
			return `${field}: ${message}`;
		}

		return message;
	};

	const buildSnapshot = () => {
		if (!webConfig) return null;

		return {
			webSearch: {
				ENABLE_WEB_SEARCH: webConfig.ENABLE_WEB_SEARCH,
				ENABLE_NATIVE_WEB_SEARCH: webConfig.ENABLE_NATIVE_WEB_SEARCH,
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
				TAVILY_SEARCH_API_BASE_URL: webConfig.TAVILY_SEARCH_API_BASE_URL,
				TAVILY_SEARCH_API_FORCE_MODE: webConfig.TAVILY_SEARCH_API_FORCE_MODE,
				TAVILY_SEARCH_API_BASE_URL_INPUT: tavilySearchBaseUrlInput,
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
				TAVILY_EXTRACT_API_BASE_URL: webConfig.TAVILY_EXTRACT_API_BASE_URL,
				TAVILY_EXTRACT_API_FORCE_MODE: webConfig.TAVILY_EXTRACT_API_FORCE_MODE,
				TAVILY_EXTRACT_API_BASE_URL_INPUT: tavilyExtractBaseUrlInput,
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
	$: tavilySearchUrlState = parseTavilyUrlInput(tavilySearchBaseUrlInput, 'search');
	$: tavilyExtractUrlState = parseTavilyUrlInput(tavilyExtractBaseUrlInput, 'extract');
	$: shouldShowTavilyVerify =
		Boolean(webConfig) &&
		(webConfig?.WEB_SEARCH_ENGINE === 'tavily' || webConfig?.WEB_LOADER_ENGINE === 'tavily');
	$: tavilyVerifyFingerprint = webConfig
		? JSON.stringify({
				WEB_SEARCH_ENGINE: String(webConfig.WEB_SEARCH_ENGINE || ''),
				WEB_LOADER_ENGINE: String(webConfig.WEB_LOADER_ENGINE || ''),
				TAVILY_API_KEY: String(webConfig.TAVILY_API_KEY || '').trim(),
				TAVILY_SEARCH_API_BASE_URL: tavilySearchUrlState.baseUrl,
				TAVILY_SEARCH_API_FORCE_MODE: tavilySearchUrlState.forceMode,
				TAVILY_EXTRACT_API_BASE_URL: tavilyExtractUrlState.baseUrl,
				TAVILY_EXTRACT_API_FORCE_MODE: tavilyExtractUrlState.forceMode,
				TAVILY_EXTRACT_DEPTH: String(webConfig.TAVILY_EXTRACT_DEPTH || 'basic')
			} as TavilyVerifyFingerprint)
		: '';
	$: if (!shouldShowTavilyVerify) {
		tavilyVerifyResult = null;
		lastTavilyVerifyFingerprint = '';
	} else if (
		tavilyVerifyResult &&
		lastTavilyVerifyFingerprint &&
		tavilyVerifyFingerprint !== lastTavilyVerifyFingerprint &&
		!tavilyVerifyLoading
	) {
		tavilyVerifyResult = null;
	}

	let snapshot: ReturnType<typeof buildSnapshot> = null;
	$: {
		webConfig;
		youtubeLanguage;
		youtubeTranslation;
		tavilySearchBaseUrlInput;
		tavilyExtractBaseUrlInput;
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
				webConfig.TAVILY_API_KEY = webConfig.TAVILY_API_KEY || '';
				webConfig.TAVILY_SEARCH_API_BASE_URL =
					webConfig.TAVILY_SEARCH_API_BASE_URL || DEFAULT_TAVILY_API_BASE_URL;
				webConfig.TAVILY_SEARCH_API_FORCE_MODE =
					webConfig.TAVILY_SEARCH_API_FORCE_MODE ?? false;
				webConfig.TAVILY_EXTRACT_API_BASE_URL =
					webConfig.TAVILY_EXTRACT_API_BASE_URL || DEFAULT_TAVILY_API_BASE_URL;
				webConfig.TAVILY_EXTRACT_API_FORCE_MODE =
					webConfig.TAVILY_EXTRACT_API_FORCE_MODE ?? false;
				normalizeNumericWebConfig(webConfig);
				webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST = listToCsv(webConfig.WEB_SEARCH_DOMAIN_FILTER_LIST);
				webConfig.YOUTUBE_LOADER_LANGUAGE = listToCsv(webConfig.YOUTUBE_LOADER_LANGUAGE);
				const langArray = csvToList(webConfig.YOUTUBE_LOADER_LANGUAGE);
				youtubeLanguage = langArray.length > 0 ? langArray[0] : '';
				youtubeTranslation = webConfig.YOUTUBE_LOADER_TRANSLATION || '';
				tavilySearchBaseUrlInput = restoreTavilyUrlInput(
					webConfig.TAVILY_SEARCH_API_BASE_URL,
					webConfig.TAVILY_SEARCH_API_FORCE_MODE
				);
				tavilyExtractBaseUrlInput = restoreTavilyUrlInput(
					webConfig.TAVILY_EXTRACT_API_BASE_URL,
					webConfig.TAVILY_EXTRACT_API_FORCE_MODE
				);
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

		// Sync UI values back to webConfig
		webConfig.YOUTUBE_LOADER_LANGUAGE = youtubeLanguage;
		webConfig.YOUTUBE_LOADER_TRANSLATION = youtubeTranslation;

		if (tavilySearchUrlState.error) {
			toast.error(tavilySearchUrlState.error);
			return false;
		}
		if (tavilyExtractUrlState.error) {
			toast.error(tavilyExtractUrlState.error);
			return false;
		}
		if (
			(webConfig.WEB_SEARCH_ENGINE === 'tavily' || webConfig.WEB_LOADER_ENGINE === 'tavily') &&
			!String(webConfig.TAVILY_API_KEY || '').trim()
		) {
			toast.error($i18n.t('Tavily API Key is required when Tavily search or loader is enabled.'));
			return false;
		}

		// Use a copy so the UI stays as CSV strings even if the request fails.
		const payloadWeb = normalizeNumericWebConfig({ ...webConfig }, true);
		payloadWeb.TAVILY_API_KEY = String(payloadWeb.TAVILY_API_KEY || '').trim();
		payloadWeb.TAVILY_SEARCH_API_BASE_URL = tavilySearchUrlState.baseUrl;
		payloadWeb.TAVILY_SEARCH_API_FORCE_MODE = tavilySearchUrlState.forceMode;
		payloadWeb.TAVILY_EXTRACT_API_BASE_URL = tavilyExtractUrlState.baseUrl;
		payloadWeb.TAVILY_EXTRACT_API_FORCE_MODE = tavilyExtractUrlState.forceMode;
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

	const getTavilyVerifyBadgeClasses = (item: TavilyConfigVerifyItem) => {
		if (item.ok === true) {
			return 'border-emerald-200/80 bg-emerald-50/80 text-emerald-700 dark:border-emerald-900/70 dark:bg-emerald-950/30 dark:text-emerald-300';
		}

		if (item.ok === false) {
			return 'border-rose-200/80 bg-rose-50/80 text-rose-700 dark:border-rose-900/70 dark:bg-rose-950/30 dark:text-rose-300';
		}

		return 'border-gray-200/80 bg-white/70 text-gray-500 dark:border-gray-700/70 dark:bg-gray-900/30 dark:text-gray-400';
	};

	const getTavilyVerifyStatusText = (item: TavilyConfigVerifyItem) => {
		if (item.ok === true) return tr('可用', 'Available');
		if (item.ok === false) return tr('不可用', 'Unavailable');
		return tr('未启用', 'Disabled');
	};

	const verifyTavilyConfig = async () => {
		if (!webConfig || tavilyVerifyLoading) return;
		if (tavilySearchUrlState.error) {
			toast.error(tavilySearchUrlState.error);
			return;
		}
		if (tavilyExtractUrlState.error) {
			toast.error(tavilyExtractUrlState.error);
			return;
		}
		if (
			(webConfig.WEB_SEARCH_ENGINE === 'tavily' || webConfig.WEB_LOADER_ENGINE === 'tavily') &&
			!String(webConfig.TAVILY_API_KEY || '').trim()
		) {
			toast.error($i18n.t('Tavily API Key is required when Tavily search or loader is enabled.'));
			return;
		}

		tavilyVerifyLoading = true;
		try {
			const result = await verifyTavilyWebConfig(localStorage.token, {
				WEB_SEARCH_ENGINE: webConfig.WEB_SEARCH_ENGINE,
				WEB_LOADER_ENGINE: webConfig.WEB_LOADER_ENGINE,
				TAVILY_API_KEY: String(webConfig.TAVILY_API_KEY || '').trim(),
				TAVILY_SEARCH_API_BASE_URL: tavilySearchUrlState.baseUrl,
				TAVILY_SEARCH_API_FORCE_MODE: tavilySearchUrlState.forceMode,
				TAVILY_EXTRACT_API_BASE_URL: tavilyExtractUrlState.baseUrl,
				TAVILY_EXTRACT_API_FORCE_MODE: tavilyExtractUrlState.forceMode,
				TAVILY_EXTRACT_DEPTH: String(webConfig.TAVILY_EXTRACT_DEPTH || 'basic').trim() || 'basic'
			});

			tavilyVerifyResult = result;
			lastTavilyVerifyFingerprint = tavilyVerifyFingerprint;

			const enabledItems = [result.search, result.loader].filter((item) => item.enabled);
			const allPassed = enabledItems.length > 0 && enabledItems.every((item) => item.ok === true);

			if (allPassed) {
				toast.success(tr('Tavily 配置验证通过', 'Tavily configuration verified successfully.'));
			} else {
				toast.warning(tr('Tavily 配置验证完成，请检查失败项', 'Tavily verification completed. Please review the failed items.'));
			}
		} catch (error) {
			console.error('Failed to verify Tavily config', error);
			toast.error(formatValidationError(error) ?? tr('Tavily 配置验证失败', 'Failed to verify Tavily configuration.'));
		} finally {
			tavilyVerifyLoading = false;
		}
	};

	onMount(loadConfig);

	const resetSectionChanges = (section: 'webSearch' | 'loader') => {
		if (!initialSnapshot || !webConfig) return;
		const sectionSnapshot: Record<string, any> = cloneSettingsSnapshot(initialSnapshot[section]);
		delete sectionSnapshot.TAVILY_SEARCH_API_BASE_URL_INPUT;
		delete sectionSnapshot.TAVILY_EXTRACT_API_BASE_URL_INPUT;
		Object.assign(webConfig, sectionSnapshot);
		if (section === 'webSearch') {
			tavilySearchBaseUrlInput = initialSnapshot.webSearch.TAVILY_SEARCH_API_BASE_URL_INPUT;
		}
		if (section === 'loader') {
			youtubeLanguage = initialSnapshot.loader.YOUTUBE_LOADER_LANGUAGE;
			youtubeTranslation = initialSnapshot.loader.YOUTUBE_LOADER_TRANSLATION;
			tavilyExtractBaseUrlInput = initialSnapshot.loader.TAVILY_EXTRACT_API_BASE_URL_INPUT;
		}
		webConfig = webConfig;
	};
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

				{#if shouldShowTavilyVerify}
					<section class="glass-section p-5 space-y-4">
						<div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
							<div class="space-y-1">
								<div class="text-sm font-medium text-gray-800 dark:text-gray-100">
									{tr('测试 Tavily 配置', 'Test Tavily Configuration')}
								</div>
								<div class="text-xs text-gray-500 dark:text-gray-400">
									{tr(
										'直接测试当前输入的 Tavily 搜索和网页提取配置，不会保存也不会改动已生效设置。',
										'Test the current Tavily search and extract inputs without saving or changing the active configuration.'
									)}
								</div>
							</div>
							<button
								type="button"
								class="inline-flex items-center justify-center gap-2 rounded-xl border border-gray-200/80 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700/80 dark:bg-gray-900/70 dark:text-gray-200 dark:hover:bg-gray-800/80 disabled:cursor-not-allowed disabled:opacity-60"
								on:click={verifyTavilyConfig}
								disabled={tavilyVerifyLoading}
							>
								{#if tavilyVerifyLoading}
									<Spinner className="size-4" />
								{/if}
								<span>
									{tavilyVerifyLoading
										? tr('正在测试…', 'Testing...')
										: tr('测试 Tavily 配置', 'Test Tavily Configuration')}
								</span>
							</button>
						</div>

						{#if tavilyVerifyResult}
							<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
								{#if tavilyVerifyResult.search.enabled}
									<div class={`rounded-2xl border px-4 py-3 ${getTavilyVerifyBadgeClasses(tavilyVerifyResult.search)}`}>
										<div class="flex items-center justify-between gap-3">
											<div class="text-sm font-medium">
												{tr('Tavily 搜索接口', 'Tavily Search API')}
											</div>
											<div class="text-xs font-medium">
												{getTavilyVerifyStatusText(tavilyVerifyResult.search)}
											</div>
										</div>
										<div class="mt-2 text-xs leading-5">
											{tavilyVerifyResult.search.message}
										</div>
										{#if tavilyVerifyResult.search.http_status}
											<div class="mt-2 text-[11px] opacity-80">
												HTTP {tavilyVerifyResult.search.http_status}
											</div>
										{/if}
									</div>
								{/if}

								{#if tavilyVerifyResult.loader.enabled}
									<div class={`rounded-2xl border px-4 py-3 ${getTavilyVerifyBadgeClasses(tavilyVerifyResult.loader)}`}>
										<div class="flex items-center justify-between gap-3">
											<div class="text-sm font-medium">
												{tr('Tavily 网页提取接口', 'Tavily Extract API')}
											</div>
											<div class="text-xs font-medium">
												{getTavilyVerifyStatusText(tavilyVerifyResult.loader)}
											</div>
										</div>
										<div class="mt-2 text-xs leading-5">
											{tavilyVerifyResult.loader.message}
										</div>
										{#if tavilyVerifyResult.loader.http_status}
											<div class="mt-2 text-[11px] opacity-80">
												HTTP {tavilyVerifyResult.loader.http_status}
											</div>
										{/if}
									</div>
								{/if}
							</div>
						{/if}
					</section>
				{/if}

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
					<div class="space-y-4">
						<div class="grid grid-cols-1 md:grid-cols-2 gap-3 items-stretch">
							<div class="glass-item h-full px-4 py-3 flex items-start justify-between gap-4">
								<div class="min-w-0">
									<div class="text-sm font-medium">{$i18n.t('Enable Native Web Search')}</div>
									<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
										{$i18n.t('Allow supported provider connections to use model-built-in web search tools.')}
									</div>
								</div>
								<div class="shrink-0 pt-0.5">
									<Switch bind:state={webConfig.ENABLE_NATIVE_WEB_SEARCH} />
								</div>
							</div>

							<div class="glass-item h-full px-4 py-3 flex items-start justify-between gap-4">
								<div class="min-w-0">
									<div class="text-sm font-medium">{$i18n.t('Enable HaloWebUI Search')}</div>
									<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
										{$i18n.t('HaloWebUI mode only')}
									</div>
								</div>
								<div class="shrink-0 pt-0.5">
									<Switch bind:state={webConfig.ENABLE_WEB_SEARCH} />
								</div>
							</div>
						</div>

						<div class="space-y-4 rounded-2xl border border-gray-200/50 dark:border-gray-700/30 bg-white/35 dark:bg-gray-900/10 p-3">
							<div class="px-1 space-y-1">
								<div class="text-sm font-medium text-gray-800 dark:text-gray-100">
									{$i18n.t('HaloWebUI Search Configuration')}
								</div>
								<div class="text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('The following engine, loader, result count, domain filter, and query generation settings only apply to HaloWebUI search mode.')}
								</div>
							</div>

							<div class="grid grid-cols-1 xl:grid-cols-[minmax(16rem,0.9fr)_minmax(0,1.1fr)] gap-4 items-stretch">
								<div class="glass-item h-full p-4 space-y-3">
									<div class="space-y-1">
										<div class="text-sm font-medium">{$i18n.t('Search Engine')}</div>
										<div class="text-xs text-gray-500 dark:text-gray-400">
											{$i18n.t('Choose the search engine used by HaloWebUI web search.')}
										</div>
									</div>
									<div class="space-y-1.5">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
											{$i18n.t('Current Search Engine')}
										</div>
										<HaloSelect
											bind:value={webConfig.WEB_SEARCH_ENGINE}
											options={webSearchEngineOptions}
											placeholder={$i18n.t('Select a engine')}
											className="w-full"
										/>
									</div>
								</div>

								<div class="glass-item h-full p-4 space-y-4">
									<div class="space-y-1">
										<div class="flex flex-wrap items-center gap-2">
											<div class="text-sm font-medium">
												{$i18n.t('Current Engine Configuration')}
											</div>
											{#if currentWebSearchEngineOption}
												<span class="inline-flex items-center rounded-full bg-gray-100/90 dark:bg-gray-800/80 px-2 py-0.5 text-[11px] font-medium text-gray-500 dark:text-gray-300">
													{currentWebSearchEngineOption.label}
												</span>
											{/if}
										</div>
										<div class="text-xs text-gray-500 dark:text-gray-400">
											{$i18n.t('Configure the parameters required by the selected search engine.')}
										</div>
									</div>

									{#if webConfig.WEB_SEARCH_ENGINE === 'searxng'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('SearXNG Query URL')}</div>
											<input
												class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
												type="text"
												placeholder={$i18n.t('Enter SearXNG Query URL')}
												bind:value={webConfig.SEARXNG_QUERY_URL}
												autocomplete="off"
											/>
											<div class="text-xs text-gray-400 dark:text-gray-500">
												{$i18n.t('Example: http://searxng:8080/search?q=<query>')}
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'google_pse'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Google PSE API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Google PSE API Key')}
													bind:value={webConfig.GOOGLE_PSE_API_KEY}
												/>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Google PSE Engine ID')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Google PSE Engine ID')}
													bind:value={webConfig.GOOGLE_PSE_ENGINE_ID}
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'brave'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Brave Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Brave Search API Key')}
												bind:value={webConfig.BRAVE_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'kagi'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Kagi Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Kagi Search API Key')}
												bind:value={webConfig.KAGI_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'mojeek'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Mojeek Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Mojeek Search API Key')}
												bind:value={webConfig.MOJEEK_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'bocha'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Bocha Search API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Bocha Search API Key')}
												bind:value={webConfig.BOCHA_SEARCH_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serpstack'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Serpstack API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Serpstack API Key')}
													bind:value={webConfig.SERPSTACK_API_KEY}
												/>
											</div>
											<div class="flex items-center justify-between rounded-lg border border-gray-200/60 dark:border-gray-700/40 bg-gray-100/70 dark:bg-gray-800/60 px-3 py-2.5">
												<div class="text-sm font-medium text-gray-700 dark:text-gray-200">{$i18n.t('Use HTTPS')}</div>
												<Switch bind:state={webConfig.SERPSTACK_HTTPS} />
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serper'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Serper API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Serper API Key')}
												bind:value={webConfig.SERPER_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'serply'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Serply API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Serply API Key')}
												bind:value={webConfig.SERPLY_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'duckduckgo'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('DuckDuckGo Backend')}</div>
											<HaloSelect
												bind:value={webConfig.DDGS_BACKEND}
												options={ddgsBackendOptions}
												className="w-full"
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'tavily'}
										<div class="space-y-3">
											<div class="space-y-1.5">
												<div class="flex items-center gap-1 text-xs font-medium text-gray-500 dark:text-gray-400">
													<span>{$i18n.t('Tavily Search Base URL')}</span>
													<Tooltip content={$i18n.t(TAVILY_URL_TOOLTIP)}>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 20 20"
															fill="currentColor"
															class="size-3.5 cursor-help text-gray-400 hover:text-gray-500"
														>
															<path
																fill-rule="evenodd"
																d="M18 10a8 8 0 11-16 0 8 8 0 0116 0ZM8.94 6.94a.75.75 0 11-1.061-1.061 3 3 0 112.871 5.026v.345a.75.75 0 01-1.5 0v-.5c0-.72.57-1.172 1.081-1.287A1.5 1.5 0 108.94 6.94ZM10 15a1 1 0 100-2 1 1 0 000 2Z"
																clip-rule="evenodd"
															/>
														</svg>
													</Tooltip>
													{#if tavilySearchUrlState.forceMode}
														<span class="text-amber-600 dark:text-amber-400">
															({$i18n.t('Force mode')})
														</span>
													{/if}
												</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder={$i18n.t('Enter Tavily Search Base URL')}
													bind:value={tavilySearchBaseUrlInput}
													autocomplete="off"
												/>
												<div class="text-xs text-gray-400 dark:text-gray-500">
													<span class="text-gray-500 dark:text-gray-400">{$i18n.t('Preview')}:</span>
													<span class="ml-1 break-all text-gray-600 dark:text-gray-300">
														{tavilySearchUrlState.previewUrl}
													</span>
												</div>
												{#if tavilySearchUrlState.error}
													<div class="text-xs text-red-500 dark:text-red-400">
														{tavilySearchUrlState.error}
													</div>
												{:else if tavilySearchUrlState.forceMode}
													<div class="text-xs text-amber-600 dark:text-amber-400">
														{$i18n.t(TAVILY_FORCE_MODE_DESCRIPTION)}
													</div>
												{/if}
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Tavily API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Tavily API Key')}
													bind:value={webConfig.TAVILY_API_KEY}
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'searchapi'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('SearchApi API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter SearchApi API Key')}
													bind:value={webConfig.SEARCHAPI_API_KEY}
												/>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('SearchApi Engine')}</div>
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
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('SerpApi API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter SerpApi API Key')}
													bind:value={webConfig.SERPAPI_API_KEY}
												/>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('SerpApi Engine')}</div>
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
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Jina API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Jina API Key')}
													bind:value={webConfig.JINA_API_KEY}
												/>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Jina API Base URL')}</div>
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
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Bing Search V7 Endpoint')}</div>
												<input
													class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
													type="text"
													placeholder={$i18n.t('Enter Bing Search V7 Endpoint')}
													bind:value={webConfig.BING_SEARCH_V7_ENDPOINT}
													autocomplete="off"
												/>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Bing Search V7 Subscription Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Bing Search V7 Subscription Key')}
													bind:value={webConfig.BING_SEARCH_V7_SUBSCRIPTION_KEY}
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'exa'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Exa API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Exa API Key')}
												bind:value={webConfig.EXA_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'perplexity'}
										<div class="space-y-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Perplexity API Key')}</div>
											<SensitiveInput
												placeholder={$i18n.t('Enter Perplexity API Key')}
												bind:value={webConfig.PERPLEXITY_API_KEY}
											/>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'grok'}
										<div class="space-y-3">
											<div class="grid grid-cols-1 md:grid-cols-3 gap-3">
												<div class="space-y-1.5">
													<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Grok API Base URL')}</div>
													<input
														class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
														type="text"
														placeholder="https://api.x.ai"
														bind:value={webConfig.GROK_API_BASE_URL}
														autocomplete="off"
													/>
												</div>
												<div class="space-y-1.5">
													<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Grok Model')}</div>
													<input
														class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
														type="text"
														placeholder="grok-4-1-fast"
														bind:value={webConfig.GROK_API_MODEL}
														autocomplete="off"
													/>
												</div>
												<div class="space-y-1.5">
													<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Mode')}</div>
													<HaloSelect
														bind:value={webConfig.GROK_API_MODE}
														options={grokApiModeOptions}
														className="w-full"
													/>
												</div>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Grok API Key')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter xAI Grok API Key')}
													bind:value={webConfig.GROK_API_KEY}
												/>
											</div>
										</div>
									{:else if webConfig.WEB_SEARCH_ENGINE === 'sougou'}
										<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Sougou Search API sID')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Sougou Search API sID')}
													bind:value={webConfig.SOUGOU_API_SID}
												/>
											</div>
											<div class="space-y-1.5">
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Sougou Search API SK')}</div>
												<SensitiveInput
													placeholder={$i18n.t('Enter Sougou Search API SK')}
													bind:value={webConfig.SOUGOU_API_SK}
												/>
											</div>
										</div>
									{:else}
										<div class="text-sm text-gray-500 dark:text-gray-400">
											{$i18n.t('Select a engine')}
										</div>
									{/if}
								</div>
							</div>
						</div>

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
									<div class="glass-item p-4 space-y-1.5">
										<div class="flex items-center gap-1 text-xs font-medium text-gray-500 dark:text-gray-400">
											<span>{$i18n.t('Tavily Extract Base URL')}</span>
											<Tooltip content={$i18n.t(TAVILY_URL_TOOLTIP)}>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 20 20"
													fill="currentColor"
													class="size-3.5 cursor-help text-gray-400 hover:text-gray-500"
												>
													<path
														fill-rule="evenodd"
														d="M18 10a8 8 0 11-16 0 8 8 0 0116 0ZM8.94 6.94a.75.75 0 11-1.061-1.061 3 3 0 112.871 5.026v.345a.75.75 0 01-1.5 0v-.5c0-.72.57-1.172 1.081-1.287A1.5 1.5 0 108.94 6.94ZM10 15a1 1 0 100-2 1 1 0 000 2Z"
														clip-rule="evenodd"
													/>
												</svg>
											</Tooltip>
											{#if tavilyExtractUrlState.forceMode}
												<span class="text-amber-600 dark:text-amber-400">
													({$i18n.t('Force mode')})
												</span>
											{/if}
										</div>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											type="text"
											placeholder={$i18n.t('Enter Tavily Extract Base URL')}
											bind:value={tavilyExtractBaseUrlInput}
											autocomplete="off"
										/>
										<div class="text-xs text-gray-400 dark:text-gray-500">
											<span class="text-gray-500 dark:text-gray-400">{$i18n.t('Preview')}:</span>
											<span class="ml-1 break-all text-gray-600 dark:text-gray-300">
												{tavilyExtractUrlState.previewUrl}
											</span>
										</div>
										{#if tavilyExtractUrlState.error}
											<div class="text-xs text-red-500 dark:text-red-400">
												{tavilyExtractUrlState.error}
											</div>
										{:else if tavilyExtractUrlState.forceMode}
											<div class="text-xs text-amber-600 dark:text-amber-400">
												{$i18n.t(TAVILY_FORCE_MODE_DESCRIPTION)}
											</div>
										{/if}
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
