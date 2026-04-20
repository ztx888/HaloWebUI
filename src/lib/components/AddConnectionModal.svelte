<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext('i18n') as Writable<i18nType>;

	import { healthCheckOpenAIConnection, verifyOpenAIConnection } from '$lib/apis/openai';
	import { healthCheckOllamaConnection, verifyOllamaConnection } from '$lib/apis/ollama';
	import { healthCheckGeminiConnection, verifyGeminiConnection } from '$lib/apis/gemini';
	import { healthCheckGrokConnection, verifyGrokConnection } from '$lib/apis/grok';
	import { healthCheckAnthropicConnection, verifyAnthropicConnection } from '$lib/apis/anthropic';

	import Modal from '$lib/components/common/Modal.svelte';
	import ConnectionAvatarPicker from '$lib/components/common/ConnectionAvatarPicker.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tags from './common/Tags.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import Bolt from '$lib/components/icons/Bolt.svelte';
	import Check from '$lib/components/icons/Check.svelte';
	import Textarea from './common/Textarea.svelte';
	import CollapsibleSection from '$lib/components/common/CollapsibleSection.svelte';
	import ModelSelectorModal from '$lib/components/common/ModelSelectorModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import { formatConnectionErrorToast } from '$lib/utils/connection-errors';
	import { resolveAzureProviderType } from '$lib/utils/connection-provider-state';

	interface ConnectionConfig {
		enable?: boolean;
		tags?: Array<{ name: string }>;
		prefix_id?: string;
		force_mode?: boolean;
		remark?: string;
		icon?: string;
		model_ids?: string[];
		connection_type?: string;
		auth_type?: string;
		headers?: Record<string, string>;
		azure?: boolean;
		api_version?: string;
		use_responses_api?: boolean;
		responses_api_exclude_patterns?: string[];
		native_file_inputs_enabled?: boolean;
		// Anthropic-specific
		anthropic_version?: string;
		anthropic_beta?: string[];
		use_files_api?: boolean;
		files_auto_attach?: boolean;
		files_cache_ttl?: string;
		files_citations?: boolean;
		anthropic_extra_body?: Record<string, any>;
	}

	interface Connection {
		url: string;
		key: string;
		config: ConnectionConfig;
	}

	export let onSubmit: Function = () => {};
	export let onDelete: Function = () => {};

	export let show = false;
	export let edit = false;

	export let ollama = false;
	export let gemini = false;
	export let grok = false;
	export let anthropic = false;
	export let direct = false;

	export let connection: Connection | null = null;

	let url = '';
	let key = '';
	let auth_type = 'bearer';

	let connectionType = 'external';
	let azure = false;
	let savedAzureProviderType = false;
	let providerTypeTouched = false;
	$: {
		const nextAzure = resolveAzureProviderType({
			currentAzure: azure,
			url,
			direct,
			gemini,
			grok,
			anthropic,
			ollama,
			edit,
			savedAzure: savedAzureProviderType,
			providerTypeTouched
		});
		if (azure !== nextAzure) {
			azure = nextAzure;
		}
	}

	let prefixId = '';
	let preserveEmptyPrefixId = false;
	let remark = '';
	let icon = '';
	let enable = true;
	let apiVersion = '';
	const AZURE_API_VERSION_AUTO = 'auto';
	const AZURE_API_VERSION_CUSTOM = '__custom__';
	const AZURE_API_VERSION_PRESETS = [
		'2025-04-01-preview',
		'2025-03-01-preview',
		'2025-02-01-preview',
		'2025-01-01-preview'
	];
	let azureApiVersionMode = AZURE_API_VERSION_AUTO;
	let azureCustomApiVersion = '';

	let headers = '';

	$: parsedHeaders = (() => {
		if (!headers) return undefined;
		try {
			const obj = JSON.parse(headers);
			if (typeof obj !== 'object' || Array.isArray(obj) || obj === null) return undefined;
			return obj as Record<string, string>;
		} catch {
			return undefined;
		}
	})();

	let tags: Array<{ name: string }> = [];

	let modelIds: string[] = [];

	let useResponsesApi = false;
	let responsesApiExcludePatterns: Array<{ name: string }> = [{ name: 'gemini' }];
	let nativeFileInputsEnabled = false;
	let nativeFileInputsTouched = false;

	// Anthropic settings
	let anthropicVersion = '2023-06-01';
	let anthropicVersionMode: '2023-06-01' | 'custom' = '2023-06-01';
	let anthropicCustomVersion = '2023-06-01';
	let anthropicBetas: Array<{ name: string }> = [{ name: 'files-api-2025-04-14' }];
	let showCustomBetaInput = false;
	let customBetaValue = '';
	let useFilesApi = true;
	let filesAutoAttach = true;
	let filesCacheTtl = '';
	let filesCitations = false;
	let anthropicExtraBody = '';

	$: parsedAnthropicExtraBody = (() => {
		if (!anthropicExtraBody) return undefined;
		try {
			const obj = JSON.parse(anthropicExtraBody);
			if (typeof obj !== 'object' || Array.isArray(obj) || obj === null) return undefined;
			return obj as Record<string, any>;
		} catch {
			return undefined;
		}
	})();

	// Collapsible section UX:
	// - Keep the modal within viewport by scrolling inside the form content.
	// - When user opens "Advanced Settings", collapse other sections + scroll it into view (focus mode).
	let basicSectionOpen = true;
	let modelManagementOpen = true;
	let advancedSectionOpen = false;
	let scrollContainerEl: HTMLDivElement | null = null;
	let advancedSectionAnchorEl: HTMLDivElement | null = null;
	let advancedOpenHandled = false;
	let prevBasicSectionOpen: boolean | null = null;
	let prevModelManagementOpen: boolean | null = null;

	const focusAdvancedSection = async () => {
		await tick();
		advancedSectionAnchorEl?.scrollIntoView({ block: 'start', behavior: 'smooth' });
	};

	$: if (advancedSectionOpen && !advancedOpenHandled) {
		advancedOpenHandled = true;
		prevBasicSectionOpen = basicSectionOpen;
		prevModelManagementOpen = modelManagementOpen;
		basicSectionOpen = false;
		modelManagementOpen = false;
		void focusAdvancedSection();
	}

	$: if (!advancedSectionOpen && advancedOpenHandled) {
		advancedOpenHandled = false;
		// Restore the user's previous context when leaving Advanced.
		if (prevBasicSectionOpen !== null) basicSectionOpen = prevBasicSectionOpen;
		if (prevModelManagementOpen !== null) modelManagementOpen = prevModelManagementOpen;
		prevBasicSectionOpen = null;
		prevModelManagementOpen = null;
	}

	type ModelHealthState = {
		status: 'idle' | 'testing' | 'success' | 'error';
		responseTimeMs?: number;
		detail?: string;
	};

	type ProviderHealthCheckRequest =
		| {
				provider: 'openai';
				connection: { url: string; key: string; config?: object };
		  }
		| {
				provider: 'gemini';
				connection: { url: string; key: string; config?: object };
		  }
		| {
				provider: 'grok';
				connection: { url: string; key: string; config?: object };
		  }
		| {
				provider: 'anthropic';
				connection: { url: string; key: string; config?: object };
		  }
		| {
				provider: 'ollama';
				connection: { url: string; key?: string; config?: object };
		  };

	const BATCH_HEALTH_CHECK_DELAY_MS = 300;

	let loading = false;
	let batchHealthChecking = false;
	let batchHealthProgress = { current: 0, total: 0 };
	let modelHealthStates: Record<string, ModelHealthState> = {};
	let modelHealthContextKey = '';
	let showModelSelector = false;
	let showNoModelsConfirm = false;

	const OPENAI_CHAT_COMPLETIONS_SUFFIX = '/chat/completions';

	// 检测是否为强制模式（URL 以 # 结尾）
	$: isForceMode = url.trim().endsWith('#');
	$: if (isForceMode && useResponsesApi) {
		useResponsesApi = false;
	}

	// 智能规范化 URL，确保以正确的版本路径结尾
	const normalizeUrl = (inputUrl: string, versionPath: string): string => {
		if (!inputUrl) return '';

		let normalized = inputUrl.trim();

		// 如果以 # 结尾，移除 # 并跳过自动规范化
		if (normalized.endsWith('#')) {
			return normalized.slice(0, -1).replace(/\/+$/, '');
		}

		// 移除末尾的斜杠
		normalized = normalized.replace(/\/+$/, '');

		// 移除可能存在的 chat/completions 等路径
		normalized = normalized.replace(/\/chat\/completions$/, '');
		normalized = normalized.replace(/\/models$/, '');
		normalized = normalized.replace(/\/completions$/, '');
		normalized = normalized.replace(/\/+$/, '');

		// 检查是否已经以版本路径结尾
		if (!normalized.endsWith(versionPath)) {
			// 如果以部分版本路径结尾（如 /v1/ 变成了 /v1），不需要再添加
			// 检查是否有重复的版本路径
			const versionRegex = new RegExp(`${versionPath.replace('/', '\\/')}$`);
			if (!versionRegex.test(normalized)) {
				// 移除可能存在的不完整版本路径
				normalized = normalized.replace(/\/v1beta$/, '');
				normalized = normalized.replace(/\/v1$/, '');
				normalized = normalized.replace(/\/+$/, '');
				// 添加版本路径
				normalized = normalized + versionPath;
			}
		}

		return normalized;
	};

	const normalizeAzureUrl = (inputUrl: string): string => {
		if (!inputUrl) return '';

		let normalized = inputUrl.trim();
		if (normalized.endsWith('#')) {
			return normalized.slice(0, -1).replace(/\/+$/, '');
		}

		normalized = normalized.replace(/\/+$/, '');

		try {
			const parsed = new URL(normalized);
			let path = parsed.pathname.replace(/\/+$/, '');

			path = path.replace(/\/responses$/, '');
			path = path.replace(/\/models$/, '');
			path = path.replace(/\/chat\/completions$/, '');
			path = path.replace(/\/completions$/, '');
			path = path.replace(/\/+$/, '');

			if (path.includes('/openai/deployments/')) {
				const [prefix, remainder] = path.split('/openai/deployments/', 2);
				const deployment = remainder.split('/', 1)[0]?.trim();
				path = deployment ? `${prefix}/openai/deployments/${deployment}` : `${prefix}/openai/v1`;
			} else if (path.endsWith('/openai/v1')) {
				// keep as-is
			} else if (path.endsWith('/openai')) {
				path = `${path}/v1`;
			} else if (path.endsWith('/v1')) {
				path = `${path.slice(0, -'/v1'.length)}/openai/v1`;
			} else {
				path = path ? `${path}/openai/v1` : '/openai/v1';
			}

			return `${parsed.protocol}//${parsed.host}${path}`;
		} catch {
			return normalized;
		}
	};

	const getAzurePreviewBaseUrl = (inputUrl: string): string => {
		const normalized = normalizeAzureUrl(inputUrl);
		if (!normalized) return '';

		if (normalized.includes('/openai/deployments/')) {
			const [prefix] = normalized.split('/openai/deployments/', 1);
			return `${prefix}/openai/v1`;
		}

		return normalized;
	};

	const applyAzureApiVersionState = (value: string) => {
		const normalized = (value ?? '').toString().trim();
		if (!normalized) {
			azureApiVersionMode = AZURE_API_VERSION_AUTO;
			azureCustomApiVersion = '';
			return;
		}

		if (AZURE_API_VERSION_PRESETS.includes(normalized)) {
			azureApiVersionMode = normalized;
			azureCustomApiVersion = '';
			return;
		}

		azureApiVersionMode = AZURE_API_VERSION_CUSTOM;
		azureCustomApiVersion = normalized;
	};

	const isLegacyForceModeUrl = (inputUrl: string) =>
		!gemini &&
		!grok &&
		!anthropic &&
		!ollama &&
		(inputUrl || '').trim().replace(/\/+$/, '').endsWith(OPENAI_CHAT_COMPLETIONS_SUFFIX);

	const describeConnectionErrorToast = (error: unknown) =>
		formatConnectionErrorToast(error, (key, options) =>
			$i18n.t(key, options)
		);

	const showConnectionErrorToast = (error: unknown) => {
		const { title, description } = describeConnectionErrorToast(error);

		toast.error(title, {
			...(description ? { description } : {}),
			duration: description ? 6000 : 4000
		});
	};

	const getHostname = (inputUrl: string): string => {
		const trimmed = (inputUrl || '').trim().replace(/#$/, '');
		if (!trimmed) return '';

		const candidates = trimmed.match(/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//) ? [trimmed] : [`https://${trimmed}`];
		for (const candidate of candidates) {
			try {
				return new URL(candidate).hostname.toLowerCase();
			} catch {
				continue;
			}
		}

		return '';
	};

	const isOfficialOpenAIHostname = (hostname: string) =>
		hostname === 'api.openai.com' || hostname.endsWith('.openai.com');

	const normalizeOpenAIUrl = (inputUrl: string): string => {
		const normalized = normalizeUrl(inputUrl, '/v1');
		if (!normalized) return '';

		if (!isOfficialOpenAIHostname(getHostname(normalized))) {
			return normalized;
		}

		return normalized
			.replace(/\/openai\/deployments\/[^/?#]+$/, '/v1')
			.replace(/\/openai\/v1$/, '/v1')
			.replace(/\/openai$/, '/v1');
	};

	const getDefaultNativeFileInputsEnabled = () => {
		if (ollama || direct || anthropic || gemini || grok || azure || isForceMode) {
			return false;
		}

		if (!useResponsesApi) {
			return false;
		}

		const hostname = getHostname(url || 'https://api.openai.com/v1');
		return isOfficialOpenAIHostname(hostname);
	};

	// 获取规范化后的 URL
	$: normalizedUrl = gemini
		? normalizeUrl(url, '/v1beta')
		: grok
			? normalizeUrl(url.replace(/#$/, ''), '/v1')
		: ollama
			? url.trim().endsWith('#')
				? url.trim().slice(0, -1).replace(/\/+$/, '')
				: url.replace(/\/+$/, '')
			: azure
				? normalizeAzureUrl(url)
				: normalizeOpenAIUrl(url);
	$: if (azure) {
		if (azureApiVersionMode === AZURE_API_VERSION_AUTO) {
			if (apiVersion !== '') {
				apiVersion = '';
			}
		} else if (azureApiVersionMode === AZURE_API_VERSION_CUSTOM) {
			const nextValue = azureCustomApiVersion.trim();
			if (apiVersion !== nextValue) {
				apiVersion = nextValue;
			}
		} else if (apiVersion !== azureApiVersionMode) {
			apiVersion = azureApiVersionMode;
		}
	}

	const ensureAnthropicBeta = (name: string) => {
		const trimmed = (name || '').trim();
		if (!trimmed) return;
		if (!anthropicBetas.some((b) => b.name === trimmed)) {
			anthropicBetas = [...anthropicBetas, { name: trimmed }];
		}
	};

	const removeAnthropicBeta = (name: string) => {
		const trimmed = (name || '').trim();
		if (!trimmed) return;
		anthropicBetas = anthropicBetas.filter((b) => b.name !== trimmed);
	};

	$: if (anthropic) {
		if (anthropicVersionMode === 'custom') {
			anthropicVersion = anthropicCustomVersion.trim() || '2023-06-01';
		} else {
			anthropicVersion = anthropicVersionMode;
		}
	}

	$: if (anthropic && useFilesApi) {
		// Files API is beta and requires this header in official docs.
		ensureAnthropicBeta('files-api-2025-04-14');
	}

	const onAnthropicCacheTtlChange = (e: CustomEvent<{ value: string }>) => {
		const v = e.detail?.value ?? '';
		if (v === '1h') {
			// Recommended when using extended cache TTL (kept optional; user can remove).
			ensureAnthropicBeta('extended-cache-ttl-2025-04-11');
		}
	};

	// 预览完整的 API 端点
	$: previewEndpoint = (() => {
		if (!url) return '';
		if (isForceMode && !grok) {
			// 强制模式下直接显示用户输入（去掉 #）
			return url.trim().slice(0, -1).replace(/\/+$/, '');
		}
		if (gemini) {
			return `${normalizedUrl}/models`;
		} else if (grok) {
			return normalizedUrl;
		} else if (anthropic) {
			return `${normalizedUrl}/messages`;
		} else if (ollama) {
			return `${normalizedUrl}/api/chat`;
		} else if (azure) {
			return `${getAzurePreviewBaseUrl(url)}/chat/completions`;
		} else {
			return `${normalizedUrl}/chat/completions`;
		}
	})();

	$: isOfficialOpenAIConnection = !gemini && !grok && !anthropic && !ollama && !direct && !azure
		? isOfficialOpenAIHostname(getHostname(url || 'https://api.openai.com/v1'))
		: false;
	$: showNativeFileInputsToggle =
		!ollama && !direct && !gemini && !grok && !anthropic && !azure && !isForceMode && useResponsesApi;
	$: if (show && !nativeFileInputsTouched) {
		nativeFileInputsEnabled = getDefaultNativeFileInputsEnabled();
	}

	const parseHeadersInput = (): Record<string, string> | null | undefined => {
		if (!headers) return undefined;

		try {
			const parsed = JSON.parse(headers);
			if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
				throw new Error('Headers must be a valid JSON object');
			}

			headers = JSON.stringify(parsed, null, 2);
			return parsed as Record<string, string>;
		} catch (error) {
			toast.error($i18n.t('Headers must be a valid JSON object'));
			return null;
		}
	};

	const getOpenAIConnectionConfig = (parsedHeaders?: Record<string, string>) => ({
		...(direct && preserveEmptyPrefixId
			? { prefix_id: '' }
			: prefixId.trim()
				? { prefix_id: prefixId.trim() }
				: {}),
		force_mode: isForceMode,
		auth_type,
		...(azure ? { azure: true } : {}),
		...(apiVersion ? { api_version: apiVersion } : {}),
		...(parsedHeaders ? { headers: parsedHeaders } : {}),
		...(!ollama && !gemini && !grok && !anthropic && !isForceMode && useResponsesApi
			? {
					use_responses_api: true,
					responses_api_exclude_patterns: responsesApiExcludePatterns
						.map((p) => p.name)
						.filter((p) => p.trim())
				}
			: {}),
		...(!ollama && !direct && !gemini && !grok && !anthropic && !azure && !isForceMode && useResponsesApi
			? {
					native_file_inputs_enabled: nativeFileInputsEnabled
				}
			: {})
	});

	const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

	const updateModelHealthState = (modelId: string, nextState: ModelHealthState) => {
		modelHealthStates = {
			...modelHealthStates,
			[modelId]: nextState
		};
	};

	const getModelHealthState = (modelId: string): ModelHealthState =>
		modelHealthStates[modelId] ?? { status: 'idle' };

	const getModelNameTooltip = (modelId: string) => modelId;

	const getModelHealthActionTooltip = (modelId: string) => {
		const state = getModelHealthState(modelId);
		if (state.status === 'testing') {
			return $i18n.t('Testing model...');
		}
		return $i18n.t('Test Model');
	};

	const buildOpenAIHealthCheckPayload = () => {
		const parsedOpenAIHeaders = parseHeadersInput();
		if (headers && parsedOpenAIHeaders === null) return null;

		return {
			url: normalizedUrl,
			key,
			config: getOpenAIConnectionConfig(parsedOpenAIHeaders ?? undefined)
		};
	};

	const buildGeminiHealthCheckPayload = () => {
		const parsedGeminiHeaders = parseHeadersInput();
		if (headers && parsedGeminiHeaders === null) return null;

		return {
			url: normalizedUrl,
			key,
			config: {
				auth_type,
				...(prefixId.trim() ? { prefix_id: prefixId.trim() } : {}),
				...(parsedGeminiHeaders ? { headers: parsedGeminiHeaders } : {})
			}
		};
	};

	const buildGrokHealthCheckPayload = () => {
		const parsedGrokHeaders = parseHeadersInput();
		if (headers && parsedGrokHeaders === null) return null;

		return {
			url: normalizedUrl,
			key,
			config: {
				auth_type,
				...(prefixId.trim() ? { prefix_id: prefixId.trim() } : {}),
				...(parsedGrokHeaders ? { headers: parsedGrokHeaders } : {})
			}
		};
	};

	const buildAnthropicHealthCheckPayload = () => {
		const parsedAnthropicHeaders = parseHeadersInput();
		if (headers && parsedAnthropicHeaders === null) return null;
		if (anthropicExtraBody && !parsedAnthropicExtraBody) {
			toast.error($i18n.t('Anthropic extra params must be a valid JSON object'));
			return null;
		}

		return {
			url: normalizedUrl,
			key,
			config: {
				auth_type,
				...(prefixId.trim() ? { prefix_id: prefixId.trim() } : {}),
				anthropic_version: anthropicVersion,
				anthropic_beta: anthropicBetas.map((b) => b.name).filter((b) => b.trim()),
				use_files_api: useFilesApi,
				files_auto_attach: filesAutoAttach,
				files_cache_ttl: filesCacheTtl,
				files_citations: filesCitations,
				...(parsedAnthropicExtraBody
					? { anthropic_extra_body: parsedAnthropicExtraBody }
					: {}),
				...(parsedAnthropicHeaders ? { headers: parsedAnthropicHeaders } : {})
			}
		};
	};

	const buildOllamaHealthCheckPayload = () => ({
		url: normalizedUrl,
		key,
		config: {
			...(prefixId.trim() ? { prefix_id: prefixId.trim() } : {})
		}
	});

	const buildHealthCheckRequest = (): ProviderHealthCheckRequest | null => {
		if (direct) return null;

		if (ollama) {
			return {
				provider: 'ollama',
				connection: buildOllamaHealthCheckPayload()
			};
		}

		if (gemini) {
			const connection = buildGeminiHealthCheckPayload();
			return connection ? { provider: 'gemini', connection } : null;
		}

		if (grok) {
			const connection = buildGrokHealthCheckPayload();
			return connection ? { provider: 'grok', connection } : null;
		}

		if (anthropic) {
			const connection = buildAnthropicHealthCheckPayload();
			return connection ? { provider: 'anthropic', connection } : null;
		}

		const connection = buildOpenAIHealthCheckPayload();
		return connection ? { provider: 'openai', connection } : null;
	};

	const verifyOllamaHandler = async () => {
		const verifyUrl = normalizedUrl;

		const res = await verifyOllamaConnection(localStorage.token, {
			url: verifyUrl,
			key
		}).catch((error) => {
			showConnectionErrorToast(error);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const verifyOpenAIHandler = async () => {
		const verifyUrl = normalizedUrl;
		const parsedOpenAIHeaders = parseHeadersInput();
		if (headers && parsedOpenAIHeaders === null) return;

		const res = await verifyOpenAIConnection(
			localStorage.token,
			{
				url: verifyUrl,
				key,
				purpose: 'connection',
				config: getOpenAIConnectionConfig(parsedOpenAIHeaders ?? undefined)
			},
			direct
		).catch((error) => {
			showConnectionErrorToast(error);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const runModelHealthCheck = async (
		modelId: string,
		options: {
			silentToast?: boolean;
			healthCheckRequest?: ProviderHealthCheckRequest;
		} = {}
	) => {
		if (!modelId || direct) return false;

		const request = options.healthCheckRequest ?? buildHealthCheckRequest();
		if (!request) return false;

		updateModelHealthState(modelId, { status: 'testing' });

		try {
			let result;
			if (request.provider === 'openai') {
				result = await healthCheckOpenAIConnection(localStorage.token, {
					...request.connection,
					model: modelId
				});
			} else if (request.provider === 'gemini') {
				result = await healthCheckGeminiConnection(localStorage.token, {
					...request.connection,
					model: modelId
				});
			} else if (request.provider === 'grok') {
				result = await healthCheckGrokConnection(localStorage.token, {
					...request.connection,
					model: modelId
				});
			} else if (request.provider === 'anthropic') {
				result = await healthCheckAnthropicConnection(localStorage.token, {
					...request.connection,
					model: modelId
				});
			} else {
				result = await healthCheckOllamaConnection(localStorage.token, {
					...request.connection,
					model: modelId
				});
			}

			updateModelHealthState(modelId, {
				status: 'success',
				responseTimeMs: result?.response_time_ms ?? 0
			});

			if (!options.silentToast) {
				toast.success(
					$i18n.t('Model test passed: {{model}} ({{time}}ms)', {
						model: result?.model ?? modelId,
						time: result?.response_time_ms ?? 0
					})
				);
			}

			return true;
		} catch (error) {
			const { title, description } = describeConnectionErrorToast(error);
			updateModelHealthState(modelId, {
				status: 'error',
				detail: description ? `${title} ${description}` : title
			});

			if (!options.silentToast) {
				toast.error(title, {
					...(description ? { description } : {}),
					duration: description ? 6000 : 4000
				});
			}

			return false;
		}
	};

	const runBatchHealthCheck = async () => {
		if (modelIds.length === 0 || batchHealthChecking || direct) {
			return;
		}

		const sharedRequest = buildHealthCheckRequest();
		if (!sharedRequest) return;

		batchHealthChecking = true;
		batchHealthProgress = { current: 0, total: modelIds.length };

		if (modelIds.length > 5) {
			toast.info($i18n.t('Selected model batch tests run sequentially to reduce rate-limit risk.'), {
				duration: 4000
			});
		}

		let passed = 0;
		const failed: string[] = [];

		try {
			for (const [index, modelId] of modelIds.entries()) {
				batchHealthProgress = { current: index + 1, total: modelIds.length };
				const ok = await runModelHealthCheck(modelId, {
					silentToast: true,
					healthCheckRequest: sharedRequest
				});

				if (ok) {
					passed += 1;
				} else {
					failed.push(modelId);
				}

				if (index < modelIds.length - 1) {
					await sleep(BATCH_HEALTH_CHECK_DELAY_MS);
				}
			}
		} finally {
			batchHealthChecking = false;
			batchHealthProgress = { current: 0, total: 0 };
		}

		if (failed.length === 0) {
			toast.success(
				$i18n.t('Batch model test passed: {{passed}}/{{total}}', {
					passed,
					total: modelIds.length
				})
			);
		} else if (passed > 0) {
			toast.warning(
				$i18n.t('Batch model test finished: {{passed}}/{{total}} passed', {
					passed,
					total: modelIds.length
				}),
				{
					description: failed.join(', '),
					duration: 6000
				}
			);
		} else {
			toast.error($i18n.t('Batch model test failed for all selected models'), {
				description: failed.join(', '),
				duration: 6000
			});
		}
	};

	$: {
		const allowedModels = new Set(modelIds);
		const filteredStates = Object.fromEntries(
			Object.entries(modelHealthStates).filter(([modelId]) => allowedModels.has(modelId))
		);
		if (Object.keys(filteredStates).length !== Object.keys(modelHealthStates).length) {
			modelHealthStates = filteredStates;
		}
	}

	$: {
		const nextContextKey = JSON.stringify({
			url: normalizedUrl,
			key,
			ollama,
			gemini,
			grok,
			anthropic,
			auth_type,
			prefixId,
			azure,
			apiVersion,
			headers,
			isForceMode,
			useResponsesApi,
			modelIds,
			anthropicVersion,
			anthropicBetas,
			useFilesApi,
			filesAutoAttach,
			filesCacheTtl,
			filesCitations,
			anthropicExtraBody
		});

		if (modelHealthContextKey && modelHealthContextKey !== nextContextKey) {
			modelHealthStates = {};
			batchHealthChecking = false;
			batchHealthProgress = { current: 0, total: 0 };
		}

		modelHealthContextKey = nextContextKey;
	}

	const verifyGeminiHandler = async () => {
		const verifyUrl = normalizedUrl;

		let _headers = null;

		if (headers) {
			try {
				_headers = JSON.parse(headers);
				if (typeof _headers !== 'object' || Array.isArray(_headers)) {
					_headers = null;
					throw new Error('Headers must be a valid JSON object');
				}
				headers = JSON.stringify(_headers, null, 2);
			} catch (error) {
				toast.error($i18n.t('Headers must be a valid JSON object'));
				return;
			}
		}

		const res = await verifyGeminiConnection(localStorage.token, {
			url: verifyUrl,
			key,
			config: {
				auth_type,
				...(_headers ? { headers: _headers } : {})
			}
		}).catch((error) => {
			showConnectionErrorToast(error);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const verifyGrokHandler = async () => {
		const verifyUrl = normalizedUrl;

		let _headers = null;

		if (headers) {
			try {
				_headers = JSON.parse(headers);
				if (typeof _headers !== 'object' || Array.isArray(_headers)) {
					_headers = null;
					throw new Error('Headers must be a valid JSON object');
				}
				headers = JSON.stringify(_headers, null, 2);
			} catch (error) {
				toast.error($i18n.t('Headers must be a valid JSON object'));
				return;
			}
		}

		const res = await verifyGrokConnection(localStorage.token, {
			url: verifyUrl,
			key,
			config: {
				auth_type,
				...(_headers ? { headers: _headers } : {})
			}
		}).catch((error) => {
			showConnectionErrorToast(error);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const verifyAnthropicHandler = async () => {
		const verifyUrl = normalizedUrl;

		let _headers = null;

		if (headers) {
			try {
				_headers = JSON.parse(headers);
				if (typeof _headers !== 'object' || Array.isArray(_headers)) {
					_headers = null;
					throw new Error('Headers must be a valid JSON object');
				}
				headers = JSON.stringify(_headers, null, 2);
			} catch (error) {
				toast.error($i18n.t('Headers must be a valid JSON object'));
				return;
			}
		}

		const res = await verifyAnthropicConnection(localStorage.token, {
			url: verifyUrl,
			key,
			config: {
				auth_type,
				anthropic_version: anthropicVersion,
				anthropic_beta: anthropicBetas.map((b) => b.name).filter((b) => b.trim()),
				use_files_api: useFilesApi,
				files_auto_attach: filesAutoAttach,
				files_cache_ttl: filesCacheTtl,
				files_citations: filesCitations,
				...(_headers ? { headers: _headers } : {})
			}
		}).catch((error) => {
			showConnectionErrorToast(error);
		});

		if (res) {
			toast.success($i18n.t('Server connection verified'));
		}
	};

	const verifyHandler = () => {
		if (ollama) {
			verifyOllamaHandler();
		} else if (gemini) {
			verifyGeminiHandler();
		} else if (grok) {
			verifyGrokHandler();
		} else if (anthropic) {
			verifyAnthropicHandler();
		} else {
			verifyOpenAIHandler();
		}
	};

	const submitHandler = async () => {
		loading = true;

		if (!remark) {
			loading = false;
			toast.error($i18n.t('Connection name is required'));
			return;
		}

		if (!ollama && !url) {
			loading = false;
			toast.error($i18n.t('URL is required'));
			return;
		}

		if (azure) {
			if (!key && !['azure_ad', 'microsoft_entra_id'].includes(auth_type)) {
				loading = false;
				toast.error($i18n.t('Key is required'));
				return;
			}

			if (modelIds.length === 0) {
				loading = false;
				toast.error($i18n.t('Deployment names are required for Azure OpenAI'));
				return;
			}
		}

		// Confirm if no models selected (except Azure which already requires models)
		if (!azure && modelIds.length === 0) {
			loading = false;
			showNoModelsConfirm = true;
			return;
		}

		await doSubmit();
	};

	const doSubmit = async () => {
		loading = true;
		showNoModelsConfirm = false;

		try {
			if (headers) {
				try {
					const _headers = JSON.parse(headers);
					if (typeof _headers !== 'object' || Array.isArray(_headers)) {
						throw new Error('Headers must be a valid JSON object');
					}
					headers = JSON.stringify(_headers, null, 2);
				} catch (error) {
					toast.error($i18n.t('Headers must be a valid JSON object'));
					return;
				}
			}

			// 使用规范化后的 URL
			const submitUrl = normalizedUrl;

			if (anthropic && anthropicExtraBody) {
				if (!parsedAnthropicExtraBody) {
					toast.error($i18n.t('Anthropic extra params must be a valid JSON object'));
					return;
				}
				// Prevent users from trying to override core fields generated by Open WebUI.
				const forbiddenKeys = ['model', 'messages', 'system', 'stream'];
				const found = forbiddenKeys.filter((k) =>
					Object.prototype.hasOwnProperty.call(parsedAnthropicExtraBody, k)
				);
				if (found.length) {
					toast.error(
						$i18n.t('Anthropic extra params cannot include: {{keys}}', { keys: found.join(', ') })
					);
					return;
				}
			}

			const connection = {
				url: submitUrl,
				key,
				config: {
					enable: enable,
					tags: tags,
					...(direct && preserveEmptyPrefixId
						? { prefix_id: '' }
						: prefixId.trim()
							? { prefix_id: prefixId.trim() }
							: {}),
					remark: remark,
					icon: icon || undefined,
					model_ids: modelIds,
					connection_type: connectionType,
					auth_type,
					headers: headers ? JSON.parse(headers) : undefined,
					...(!grok && isForceMode ? { force_mode: true } : {}),
					...(anthropic
						? {
								anthropic_version: anthropicVersion,
								anthropic_beta: anthropicBetas.map((b) => b.name).filter((b) => b.trim()),
								use_files_api: useFilesApi,
								files_auto_attach: filesAutoAttach,
								files_cache_ttl: filesCacheTtl,
								files_citations: filesCitations,
								...(parsedAnthropicExtraBody
									? { anthropic_extra_body: parsedAnthropicExtraBody }
									: {})
							}
						: {}),
					...(!ollama && azure ? { azure: true, ...(apiVersion ? { api_version: apiVersion } : {}) } : {}),
					...(!ollama && !gemini && !grok && !anthropic && !direct && !azure && !isForceMode && useResponsesApi
						? {
								native_file_inputs_enabled: nativeFileInputsEnabled
							}
						: {}),
					...(!ollama && !gemini && !grok && !anthropic && !isForceMode && useResponsesApi
						? {
								use_responses_api: true,
								responses_api_exclude_patterns: responsesApiExcludePatterns
									.map((p) => p.name)
									.filter((p) => p.trim())
							}
						: {})
				}
			};

			try {
				await onSubmit(connection);
			} catch (error) {
				const message =
					error instanceof Error
						? error.message
						: typeof error === 'string'
							? error
							: (() => {
									try {
										return JSON.stringify(error);
									} catch {
										return `${error}`;
									}
								})();
				toast.error(`${$i18n.t('Failed to save connections')}${message ? `: ${message}` : ''}`);
				return;
			}

			show = false;

			url = '';
			key = '';
			auth_type = gemini ? 'x-goog-api-key' : anthropic ? 'x-api-key' : 'bearer';
			prefixId = '';
			preserveEmptyPrefixId = false;
			remark = '';
			icon = '';
			apiVersion = '';
			azureApiVersionMode = AZURE_API_VERSION_AUTO;
			azureCustomApiVersion = '';
			savedAzureProviderType = false;
			providerTypeTouched = false;
			tags = [];
			modelIds = [];
			useResponsesApi = false;
			responsesApiExcludePatterns = [{ name: 'gemini' }];
			nativeFileInputsEnabled = false;
			nativeFileInputsTouched = false;
			anthropicVersion = '2023-06-01';
			anthropicVersionMode = '2023-06-01';
			anthropicCustomVersion = '2023-06-01';
			anthropicBetas = [{ name: 'files-api-2025-04-14' }];
			useFilesApi = true;
			filesAutoAttach = true;
			filesCacheTtl = '';
			filesCitations = false;
			anthropicExtraBody = '';
		} finally {
			loading = false;
		}
	};

	const normalizeGeminiAuthType = (t: any) => {
		const v = (t ?? '').toString().toLowerCase().trim();
		if (v === 'bearer') return 'x-goog-api-key';
		return t ?? 'x-goog-api-key';
	};

	const init = () => {
		batchHealthChecking = false;
		batchHealthProgress = { current: 0, total: 0 };
		modelHealthStates = {};
		modelHealthContextKey = '';
		providerTypeTouched = false;

		if (connection) {
			savedAzureProviderType = connection.config?.azure ?? false;
			const shouldRestoreForceMode =
				(!grok && connection.config?.force_mode === true) ||
				isLegacyForceModeUrl(connection.url);
			url =
				shouldRestoreForceMode && !connection.url.trim().endsWith('#')
					? `${connection.url}#`
					: connection.url;
			key = connection.key;

			auth_type = gemini
				? normalizeGeminiAuthType(connection.config.auth_type)
				: (connection.config.auth_type ?? 'bearer');
			headers = connection.config?.headers
				? JSON.stringify(connection.config.headers, null, 2)
				: '';

			enable = connection.config?.enable ?? true;
			tags = connection.config?.tags ?? [];
			prefixId = connection.config?.prefix_id ?? '';
			preserveEmptyPrefixId =
				!!direct &&
				!!connection?.config &&
				Object.prototype.hasOwnProperty.call(connection.config, 'prefix_id') &&
				!(connection.config?.prefix_id ?? '').toString().trim();
			remark = connection.config?.remark ?? '';
			icon = connection.config?.icon ?? '';
			modelIds = connection.config?.model_ids ?? [];

			if (ollama) {
				connectionType = connection.config?.connection_type ?? 'local';
			} else {
				connectionType = connection.config?.connection_type ?? 'external';
				if (anthropic) {
					savedAzureProviderType = false;
					apiVersion = '';
					azureApiVersionMode = AZURE_API_VERSION_AUTO;
					azureCustomApiVersion = '';
					useResponsesApi = false;
					responsesApiExcludePatterns = [{ name: 'gemini' }];
					nativeFileInputsEnabled = false;
					nativeFileInputsTouched = false;

					const v = (connection.config?.anthropic_version ?? '2023-06-01').toString();
					anthropicVersion = v;
					if (v === '2023-06-01') {
						anthropicVersionMode = v;
						anthropicCustomVersion = v;
					} else {
						anthropicVersionMode = 'custom';
						anthropicCustomVersion = v;
					}

					anthropicBetas = (connection.config?.anthropic_beta ?? []).map((b) => ({ name: b }));
					useFilesApi = connection.config?.use_files_api ?? true;
					filesAutoAttach = connection.config?.files_auto_attach ?? true;
					filesCacheTtl = connection.config?.files_cache_ttl ?? '';
					filesCitations = connection.config?.files_citations ?? false;
					anthropicExtraBody = connection.config?.anthropic_extra_body
						? JSON.stringify(connection.config.anthropic_extra_body, null, 2)
						: '';
				} else {
					apiVersion = connection.config?.api_version ?? '';
					applyAzureApiVersionState(connection.config?.api_version ?? '');
					useResponsesApi = connection.config?.use_responses_api ?? false;
					responsesApiExcludePatterns = (
						connection.config?.responses_api_exclude_patterns ?? ['gemini']
					).map((p) => ({ name: p }));
					if (
						Object.prototype.hasOwnProperty.call(
							connection.config ?? {},
							'native_file_inputs_enabled'
						)
					) {
						nativeFileInputsEnabled = connection.config?.native_file_inputs_enabled ?? false;
						nativeFileInputsTouched = true;
					} else {
						nativeFileInputsEnabled = getDefaultNativeFileInputsEnabled();
						nativeFileInputsTouched = false;
					}
				}
			}
			if (!connection.config?.azure) {
				apiVersion = '';
				azureApiVersionMode = AZURE_API_VERSION_AUTO;
				azureCustomApiVersion = '';
			}
		} else {
			savedAzureProviderType = false;
			if (gemini) {
				auth_type = 'x-goog-api-key';
			}
			if (anthropic) {
				auth_type = 'x-api-key';
				anthropicVersion = '2023-06-01';
				anthropicVersionMode = '2023-06-01';
				anthropicCustomVersion = '2023-06-01';
				anthropicBetas = [{ name: 'files-api-2025-04-14' }];
				useFilesApi = true;
				filesAutoAttach = true;
				filesCacheTtl = '';
				filesCitations = false;
				anthropicExtraBody = '';
			}
			apiVersion = '';
			azureApiVersionMode = AZURE_API_VERSION_AUTO;
			azureCustomApiVersion = '';
			preserveEmptyPrefixId = false;
			nativeFileInputsEnabled = false;
			nativeFileInputsTouched = false;
		}

		// Default UX: start with Basic + Model Management visible, Advanced collapsed.
		// (When Advanced expands and overflows, we auto-collapse other sections.)
		basicSectionOpen = true;
		modelManagementOpen = true;
		advancedSectionOpen = false;
		advancedOpenHandled = false;
		prevBasicSectionOpen = null;
		prevModelManagementOpen = null;
	};

	$: if (show) {
		init();
	}

	onMount(() => {
		init();
	});
</script>

<ModelSelectorModal
	bind:show={showModelSelector}
	bind:modelIds
	url={normalizedUrl}
	force_mode={isForceMode}
	{key}
	{azure}
	api_version={apiVersion}
	{auth_type}
	headers={parsedHeaders}
	anthropic_version={anthropicVersion}
	anthropic_beta={anthropicBetas.map((b) => b.name).filter((b) => b.trim())}
	{ollama}
	{gemini}
	{grok}
	{anthropic}
/>

<ConfirmDialog
	bind:show={showNoModelsConfirm}
	title={$i18n.t('No Models Added')}
	message={$i18n.t('No models added yet. Are you sure you want to save?')}
	confirmLabel={$i18n.t('Save Anyway')}
	on:confirm={doSubmit}
/>

<Modal size="sm" bind:show dismissible={false}>
	<div class="select-text flex flex-col max-h-[calc(100dvh-4rem)] overflow-hidden">
		<div class="flex items-center justify-between dark:text-gray-100 px-5 pt-4 pb-3">
			<div class="flex items-center gap-3">
				<h1 class="text-lg font-medium font-primary">
					{#if edit}
						{$i18n.t('Edit Connection')}
					{:else}
						{$i18n.t('Add Connection')}
					{/if}
				</h1>
				<!-- 启用开关移到标题旁边 -->
				<div
					class="flex items-center gap-2 px-2.5 py-1 rounded-full {enable
						? 'bg-emerald-50 dark:bg-emerald-900/30'
						: 'bg-gray-100 dark:bg-gray-800'}"
				>
					<span
						class="text-xs font-medium {enable
							? 'text-emerald-600 dark:text-emerald-400'
							: 'text-gray-500'}">{enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}</span
					>
					<Switch bind:state={enable} />
				</div>
				<!-- Responses API 状态指示器 -->
				{#if !ollama && !gemini && !grok && !anthropic && !direct && !azure && !isForceMode}
					<div
						class="flex items-center gap-1.5 px-2.5 py-1 rounded-full {useResponsesApi
							? 'bg-blue-50 dark:bg-blue-900/30'
							: 'bg-gray-100 dark:bg-gray-800'}"
					>
						<span
							class="text-xs font-medium {useResponsesApi
								? 'text-blue-600 dark:text-blue-400'
								: 'text-gray-500'}"
						>
							Responses API {useResponsesApi ? $i18n.t('Enabled') : $i18n.t('Disabled')}
						</span>
					</div>
				{/if}
			</div>
			<button
				class="self-center p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
				aria-label={$i18n.t('Close modal')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col w-full px-4 pb-4 dark:text-gray-200 flex-1 min-h-0 overflow-hidden">
			<form
				class="flex flex-col w-full flex-1 min-h-0"
				on:submit={(e) => {
					e.preventDefault();
					submitHandler();
				}}
			>
				<!-- 可滚动内容区：避免 modal 变得过长撑出屏幕 -->
				<div
					bind:this={scrollContainerEl}
					class="flex flex-col gap-3 flex-1 min-h-0 overflow-y-auto pr-1 pb-2 scrollbar-hidden"
				>
					<!-- 基础设置 -->
					<CollapsibleSection title={$i18n.t('Basic Settings')} bind:open={basicSectionOpen}>
						<div class="flex flex-col gap-3">
							<!-- 备注名称和分组标签（同一行） -->
							<div class="flex gap-5 items-end">
								<div class="flex items-center mb-px">
									<ConnectionAvatarPicker
										{icon}
										name={remark}
										on:change={(e) => {
											icon = e.detail;
										}}
									/>
								</div>
								<div class="flex flex-col flex-1">
									<label for="remark-input" class="text-xs text-gray-500 mb-1">
										{$i18n.t('Connection Name')}
									</label>
									<input
										id="remark-input"
										class="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
										type="text"
										bind:value={remark}
										placeholder={$i18n.t('e.g. Claude API')}
										autocomplete="off"
										required
									/>
								</div>
								<div class="flex flex-col flex-1">
									<span class="text-xs text-gray-500 mb-1">{$i18n.t('Group Tag')}</span>
									<Tags
										bind:tags
										placeholder={$i18n.t('Add tags for model classification')}
										on:add={(e) => {
											tags = [...tags, { name: e.detail }];
										}}
										on:delete={(e) => {
											tags = tags.filter((tag) => tag.name !== e.detail);
										}}
									/>
								</div>
							</div>

							<!-- API 地址 -->
							<div class="flex flex-col">
								<div class="flex items-center gap-1 mb-1">
									<label for="url-input" class="text-xs text-gray-500">
										{$i18n.t('API Address')}
									</label>
									<Tooltip
										content={$i18n.t(
											'URL will be auto-normalized (e.g. auto-append /v1). Add # at the end to disable auto-normalization and use exact URL.'
										)}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											class="size-3.5 text-gray-400 hover:text-gray-500 cursor-help"
										>
											<path
												fill-rule="evenodd"
												d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM8.94 6.94a.75.75 0 11-1.061-1.061 3 3 0 112.871 5.026v.345a.75.75 0 01-1.5 0v-.5c0-.72.57-1.172 1.081-1.287A1.5 1.5 0 108.94 6.94zM10 15a1 1 0 100-2 1 1 0 000 2z"
												clip-rule="evenodd"
											/>
										</svg>
									</Tooltip>
									{#if isForceMode}
										<span class="text-xs text-amber-600 dark:text-amber-400 ml-1"
											>({$i18n.t('Force mode')})</span
										>
									{/if}
								</div>
								<div class="flex gap-2">
									<input
										id="url-input"
										class="flex-1 px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
										type="text"
										bind:value={url}
										on:blur={() => {
											// Keep provider-specific URLs normalized unless user explicitly disables normalization via '#'.
											if (
												!ollama &&
												!direct &&
												!anthropic &&
												!isForceMode &&
												url &&
												url.trim() !== normalizedUrl
											) {
												url = normalizedUrl;
											}
										}}
										placeholder={$i18n.t('Enter API address')}
										autocomplete="off"
										required
									/>
									<Tooltip content={$i18n.t('Test Connection')}>
										<button
											type="button"
											class="p-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
											on:click={verifyHandler}
											aria-label={$i18n.t('Test Connection')}
										>
											<!-- 心跳/脉冲检测图标 -->
											<svg
												xmlns="http://www.w3.org/2000/svg"
												viewBox="0 0 24 24"
												fill="none"
												stroke="currentColor"
												stroke-width="2"
												stroke-linecap="round"
												stroke-linejoin="round"
												class="size-4"
											>
												<path d="M22 12h-4l-3 9L9 3l-3 9H2" />
											</svg>
										</button>
									</Tooltip>
								</div>
								<div class="text-xs text-gray-400 mt-1">
									<span class="text-gray-500">{$i18n.t('Preview')}:</span>
									{#if url && previewEndpoint}
										<span class="text-gray-600 dark:text-gray-300 break-all">{previewEndpoint}</span
										>
									{:else if gemini}
										<span class="break-all"
											>https://generativelanguage.googleapis.com/v1beta/models</span
										>
									{:else if grok}
										<span class="break-all">https://api.x.ai/v1</span>
									{:else if anthropic}
										<span class="break-all">https://api.anthropic.com/v1/messages</span>
									{:else if ollama}
										<span class="break-all">http://localhost:11434/api/chat</span>
									{:else}
										<span class="break-all">https://api.openai.com/v1/chat/completions</span>
									{/if}
								</div>
								{#if isForceMode && !gemini && !grok && !anthropic && !ollama}
									<div class="text-xs text-amber-600 dark:text-amber-400 mt-1">
										{$i18n.t(
											'Force mode uses the exact URL and will not auto-append /chat/completions or /v1.'
										)}
									</div>
								{/if}
							</div>

							<!-- API Key -->
							<div class="flex flex-col">
								<label for="api-key-input" class="text-xs text-gray-500 mb-1">
									{$i18n.t('API Key')}
								</label>
								<SensitiveInput
									bind:value={key}
									placeholder={$i18n.t('Enter API key, usually starts with sk')}
									required={false}
									outerClassName="flex flex-1 px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg"
									inputClassName="w-full text-sm bg-transparent outline-none"
								/>
							</div>

						</div>
					</CollapsibleSection>

					<!-- 模型管理 -->
					<CollapsibleSection title={$i18n.t('Model Management')} bind:open={modelManagementOpen}>
						<div class="flex flex-col gap-3">
							<div class="flex items-center justify-between">
								<div class="text-sm">
									{#if modelIds.length > 0}
										<span class="text-gray-700 dark:text-gray-300"
											>{$i18n.t('{{count}} models selected', { count: modelIds.length })}</span
										>
									{:else}
										<span class="text-gray-500"
											>{$i18n.t('Please add models in Model Management')}</span
										>
									{/if}
								</div>
								<div class="flex items-center gap-2">
									{#if !direct}
										<Tooltip
											content={modelIds.length === 0
												? $i18n.t('Please add models in Model Management')
												: $i18n.t('Sequentially test selected models')}
										>
											<button
												type="button"
												class="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
												on:click={runBatchHealthCheck}
												disabled={batchHealthChecking || modelIds.length === 0}
											>
												{#if batchHealthChecking}
													<Spinner className="size-3.5" />
													<span>
														{$i18n.t('Testing selected models: {{current}}/{{total}}', batchHealthProgress)}
													</span>
												{:else}
													<ArrowPath className="size-3.5" />
													<span>{$i18n.t('Test Selected')}</span>
												{/if}
											</button>
										</Tooltip>
									{/if}
									<button
										type="button"
										class="px-3 py-1.5 text-sm font-medium bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition"
										on:click={() => (showModelSelector = true)}
									>
										{$i18n.t('Manage Models')}
									</button>
								</div>
							</div>

							{#if modelIds.length > 0}
								<div class="flex flex-wrap gap-1.5">
									{#each modelIds as modelId}
										{@const state = getModelHealthState(modelId)}
										<div
											class="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs max-w-44 {state.status === 'success'
												? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/20 dark:text-emerald-300'
												: state.status === 'error'
													? 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800 dark:bg-rose-900/20 dark:text-rose-300'
													: 'border-gray-200 bg-gray-100 text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300'}"
										>
											<Tooltip content={getModelNameTooltip(modelId)}>
												<span class="truncate max-w-28">{modelId}</span>
											</Tooltip>
											{#if !direct}
												<Tooltip content={getModelHealthActionTooltip(modelId)}>
													<button
														type="button"
														class="inline-flex items-center justify-center rounded p-0.5 hover:bg-black/5 dark:hover:bg-white/10 transition disabled:opacity-50 disabled:cursor-not-allowed"
														on:click={() => runModelHealthCheck(modelId)}
														disabled={batchHealthChecking || state.status === 'testing'}
														aria-label={$i18n.t('Test this model')}
													>
														{#if state.status === 'testing'}
															<Spinner className="size-3" />
														{:else if state.status === 'success'}
															<Check className="size-3 text-emerald-600 dark:text-emerald-400" />
														{:else if state.status === 'error'}
															<XMark className="size-3 text-rose-600 dark:text-rose-400" />
														{:else}
															<Bolt className="size-3 text-gray-500 dark:text-gray-400" />
														{/if}
													</button>
												</Tooltip>
											{/if}
										</div>
									{/each}
								</div>
								{#if !direct}
									<div class="text-xs text-gray-400">
										{$i18n.t('Single model tests use the chip action. Batch tests run sequentially to reduce rate-limit risk.')}
									</div>
								{/if}
							{:else if azure}
								<div class="text-xs text-amber-600 dark:text-amber-400">
									{$i18n.t('Deployment names are required for Azure OpenAI')}
								</div>
							{/if}
						</div>
					</CollapsibleSection>

					<!-- 高级设置 -->
					<div bind:this={advancedSectionAnchorEl}>
						<CollapsibleSection
							title={$i18n.t('Advanced Settings')}
							bind:open={advancedSectionOpen}
						>
							<div class="flex flex-col gap-3">
								{#if !ollama && !direct && gemini}
									<!-- Gemini Auth Mode -->
									<div class="flex flex-col">
										<label for="gemini-auth-mode" class="text-xs text-gray-500 mb-1">
											{$i18n.t('Gemini Auth Mode')}
										</label>
										<HaloSelect
											bind:value={auth_type}
											options={[
												{ value: 'x-goog-api-key', label: $i18n.t('x-goog-api-key (Header)') },
												{ value: 'query', label: $i18n.t('key=... (Query)') },
												{ value: 'authorization', label: $i18n.t('Authorization: Bearer') },
												{ value: 'none', label: $i18n.t('Custom Headers Only') }
											]}
											className="w-full"
										/>
										<div class="text-xs text-gray-400 mt-1">
											{$i18n.t(
												'Custom headers override this mode. For most Gemini endpoints, x-goog-api-key is recommended.'
											)}
										</div>
									</div>
								{:else if !ollama && !direct && anthropic}
									<!-- Anthropic Settings -->
									<div class="flex flex-col gap-3">
										<div
											class="bg-gray-50 dark:bg-gray-850 rounded-xl p-3 space-y-3 border border-gray-200 dark:border-gray-700"
										>
											<div
												class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide"
											>
												{$i18n.t('Authentication')}
											</div>
											<!-- Anthropic Auth Mode -->
											<div class="flex flex-col">
												<label
													for="anthropic-auth-mode"
													class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
												>
													{$i18n.t('Auth Mode')}
												</label>
												<HaloSelect
													bind:value={auth_type}
													options={[
														{ value: 'x-api-key', label: $i18n.t('x-api-key (Header)') },
														{ value: 'bearer', label: $i18n.t('Authorization: Bearer') },
														{ value: 'none', label: $i18n.t('Custom Headers Only') }
													]}
													className="w-full"
												/>
												<div class="text-xs text-gray-400 mt-1">
													{$i18n.t(
														'For proxies that expect Bearer auth, select "Authorization: Bearer". Default x-api-key works for official Anthropic API.'
													)}
												</div>
											</div>
											<div class="flex flex-col">
												<label
													for="anthropic-version-mode"
													class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
												>
													{$i18n.t('Anthropic Version')}
												</label>
												<HaloSelect
													bind:value={anthropicVersionMode}
													options={[
														{ value: '2023-06-01', label: `2023-06-01（${$i18n.t('Latest')}）` },
														{ value: 'custom', label: $i18n.t('Custom') }
													]}
													className="w-full"
												/>

												{#if anthropicVersionMode === 'custom'}
													<input
														id="anthropic-version"
														class="w-full mt-2 px-3 py-2 text-sm bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
														type="text"
														bind:value={anthropicCustomVersion}
														placeholder="2023-06-01"
														autocomplete="off"
													/>
												{/if}
												<div class="text-xs text-gray-400 mt-1">
													{$i18n.t(
														'Sets the required anthropic-version request header. 2023-06-01 is the only active version; use Custom only if Anthropic releases a newer one.'
													)}
												</div>
											</div>
										</div>

										<div
											class="bg-gray-50 dark:bg-gray-850 rounded-xl p-3 space-y-3 border border-gray-200 dark:border-gray-700"
										>
											<div
												class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide"
											>
												{$i18n.t('Beta Headers')}
											</div>
											<div class="flex flex-col">
												<HaloSelect
													value=""
													options={[
														...['files-api-2025-04-14', 'extended-cache-ttl-2025-04-11', 'prompt-caching-2024-07-31', 'interleaved-thinking-2025-05-14', 'computer-use-2024-10-22', 'computer-use-2025-01-24', 'code-execution-2025-05-22', 'mcp-client-2025-04-04', 'mcp-client-2025-11-20', 'token-counting-2024-11-01', 'token-efficient-tools-2025-02-19', 'message-batches-2024-09-24', 'output-128k-2025-02-19', 'pdfs-2024-09-25', 'dev-full-thinking-2025-05-14', 'context-1m-2025-08-07', 'context-management-2025-06-27', 'model-context-window-exceeded-2025-08-26', 'skills-2025-10-02', 'fast-mode-2026-02-01'].map((preset) => ({
															value: preset,
															label: `${anthropicBetas.some((b) => b.name === preset) ? '✓ ' : ''}${preset}`
														})),
														{ value: '__custom__', label: `${$i18n.t('Custom')}...` }
													]}
													placeholder={`${$i18n.t('Select Beta Header to add')}...`}
													className="w-full"
													on:change={(e) => {
														const val = e.detail?.value;
														if (val === '__custom__') {
															showCustomBetaInput = true;
														} else if (val) {
															ensureAnthropicBeta(val);
														}
													}}
												/>
												{#if showCustomBetaInput}
													<div class="flex gap-2 mt-2">
														<input
															class="flex-1 px-3 py-2 text-sm bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
															type="text"
															bind:value={customBetaValue}
															placeholder={$i18n.t('e.g. my-beta-2025-01-01')}
															autocomplete="off"
															on:keydown={(e) => {
																if (e.key === 'Enter') {
																	e.preventDefault();
																	if (customBetaValue.trim()) {
																		ensureAnthropicBeta(customBetaValue.trim());
																		customBetaValue = '';
																	}
																	showCustomBetaInput = false;
																}
																if (e.key === 'Escape') {
																	showCustomBetaInput = false;
																	customBetaValue = '';
																}
															}}
														/>
														<button
															type="button"
															class="px-3 py-2 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition"
															on:click={() => {
																if (customBetaValue.trim()) {
																	ensureAnthropicBeta(customBetaValue.trim());
																	customBetaValue = '';
																}
																showCustomBetaInput = false;
															}}
														>
															{$i18n.t('Add')}
														</button>
													</div>
												{/if}
												{#if anthropicBetas.length > 0}
													<div class="flex flex-wrap items-center gap-1.5 mt-2">
														{#each anthropicBetas as beta}
															<span
																class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800 rounded-full"
															>
																<span class="truncate max-w-[200px]">{beta.name}</span>
																<button
																	type="button"
																	class="shrink-0 ml-0.5 hover:text-red-500 dark:hover:text-red-400 transition"
																	on:click={() => removeAnthropicBeta(beta.name)}
																>
																	<XMark className="size-3" strokeWidth="2.5" />
																</button>
															</span>
														{/each}
													</div>
												{/if}
												<div class="text-xs text-gray-400 mt-1">
													{$i18n.t('Required for some official features (e.g. Files API).')}
												</div>
											</div>
										</div>

										<div
											class="bg-gray-50 dark:bg-gray-850 rounded-xl p-3 space-y-3 border border-gray-200 dark:border-gray-700"
										>
											<div
												class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide"
											>
												{$i18n.t('File Handling')}
											</div>
											<div class="flex items-center justify-between">
												<div>
													<span class="text-sm">{$i18n.t('Use Files API')}</span>
													<div class="text-xs text-gray-400 mt-0.5">
														{$i18n.t('Upload & reuse file_id for user uploaded files')}
													</div>
												</div>
												<Switch bind:state={useFilesApi} />
											</div>

											<div class="flex items-center justify-between">
												<div>
													<span class="text-sm">{$i18n.t('Auto Attach Uploaded Files')}</span>
													<div class="text-xs text-gray-400 mt-0.5">
														{$i18n.t('Send uploaded files to Claude as document blocks')}
													</div>
												</div>
												<Switch bind:state={filesAutoAttach} />
											</div>

											<div class="flex flex-col">
												<label
													for="anthropic-cache-ttl"
													class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
												>
													{$i18n.t('Prompt Cache TTL')}
												</label>
												<HaloSelect
													bind:value={filesCacheTtl}
													options={[
														{ value: '', label: $i18n.t('Off') },
														{ value: '5m', label: $i18n.t('5 minutes') },
														{ value: '1h', label: $i18n.t('1 hour') }
													]}
													className="w-full"
													on:change={onAnthropicCacheTtlChange}
												/>
												<div class="text-xs text-gray-400 mt-1">
													{$i18n.t(
														'Applies cache_control to attached file blocks (model must support prompt caching).'
													)}
												</div>
											</div>

											<div class="flex items-center justify-between">
												<div>
													<span class="text-sm">{$i18n.t('Enable Citations')}</span>
													<div class="text-xs text-gray-400 mt-0.5">
														{$i18n.t('Enable Claude citations for attached documents')}
													</div>
												</div>
												<Switch bind:state={filesCitations} />
											</div>
										</div>

										<div
											class="bg-gray-50 dark:bg-gray-850 rounded-xl p-3 space-y-3 border border-gray-200 dark:border-gray-700"
										>
											<div
												class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide"
											>
												{$i18n.t('Advanced Parameters')}
											</div>
											<div class="flex flex-col">
												<label
													for="anthropic-extra-body"
													class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
												>
													{$i18n.t('Anthropic Extra Params (JSON)')}
												</label>
												<Textarea
													id="anthropic-extra-body"
													className="w-full text-sm bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
													bind:value={anthropicExtraBody}
													placeholder={'{"thinking": {"type": "enabled"}}'}
												/>
												<div class="text-xs text-gray-400 mt-1">
													{$i18n.t(
														'Merged into the Anthropic /messages request body. Existing keys generated by Open WebUI are not overridden.'
													)}
													{$i18n.t('Forbidden keys: model, messages, system, stream.')}
												</div>
											</div>
										</div>
									</div>
								{:else if !ollama && !direct && !anthropic && !grok}
									<!-- Provider Type -->
									<div class="flex items-center justify-between">
										<span class="text-sm">{$i18n.t('Provider Type')}</span>
										<button
											type="button"
											class="px-3 py-1 text-sm bg-gray-100 dark:bg-gray-800 rounded-lg"
											on:click={() => {
												providerTypeTouched = true;
												const nextAzure = !azure;
												azure = nextAzure;

												if (!isForceMode && url.trim()) {
													url = nextAzure ? normalizeAzureUrl(url) : normalizeOpenAIUrl(url);
												}
											}}
										>
											{azure ? $i18n.t('Azure OpenAI') : $i18n.t('OpenAI')}
										</button>
									</div>
								{/if}

								{#if azure}
									<!-- API Version -->
									<div class="flex flex-col gap-2">
										<label for="api-version" class="text-xs text-gray-500 mb-1">
											{$i18n.t('API Version')}
										</label>
										<HaloSelect
											bind:value={azureApiVersionMode}
											options={[
												{
													value: AZURE_API_VERSION_AUTO,
													label: `${$i18n.t('Auto')} (${ $i18n.t('Recommended') })`
												},
												...AZURE_API_VERSION_PRESETS.map((version, idx) => ({
													value: version,
													label:
														idx === 0
															? `${version} (${ $i18n.t('Latest') })`
															: version
												})),
												{
													value: AZURE_API_VERSION_CUSTOM,
													label: `${$i18n.t('Custom')}...`
												}
											]}
											className="w-full"
										/>
										{#if azureApiVersionMode === AZURE_API_VERSION_CUSTOM}
											<input
												id="api-version"
												class="w-full px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
												type="text"
												bind:value={azureCustomApiVersion}
												placeholder="2025-01-01-preview"
												autocomplete="off"
											/>
										{/if}
										<div class="text-xs text-gray-400 mt-1">
											{$i18n.t(
												'Usually not required. HaloWebUI only uses api-version when it needs to fall back to legacy Azure deployment paths.'
											)}
										</div>
									</div>
								{/if}

								{#if !ollama && !direct && !gemini && !grok && !anthropic && !azure && !isForceMode}
									<!-- Use Responses API -->
									<div class="flex flex-col gap-2">
										<div class="flex items-center justify-between">
											<div>
												<span class="text-sm">{$i18n.t('Use Responses API')}</span>
												{#if useResponsesApi}
													<div class="text-xs text-amber-600 dark:text-amber-400 mt-0.5">
														{$i18n.t(
															'Ensure current API endpoint supports Responses format requests'
														)}
													</div>
												{/if}
											</div>
											<Switch bind:state={useResponsesApi} />
										</div>

										{#if useResponsesApi}
											<!-- Responses API Exclude Patterns -->
											<div class="flex flex-col mt-1">
												<label for="responses-exclude" class="text-xs text-gray-500 mb-1">
													{$i18n.t('Exclude Models')}
												</label>
												<Tags
													bind:tags={responsesApiExcludePatterns}
													placeholder={$i18n.t('Add model keyword to exclude')}
													on:add={(e) => {
														responsesApiExcludePatterns = [
															...responsesApiExcludePatterns,
															{ name: e.detail }
														];
													}}
													on:delete={(e) => {
														responsesApiExcludePatterns = responsesApiExcludePatterns.filter(
															(p) => p.name !== e.detail
														);
													}}
												/>
												<div class="text-xs text-amber-600 dark:text-amber-400 mt-1">
													{$i18n.t(
														'Models containing these keywords will keep using Chat Completions API'
													)}
												</div>
											</div>

											{#if showNativeFileInputsToggle}
												<div class="flex items-center justify-between mt-2">
													<div>
														<span class="text-sm">{$i18n.t('Enable Native File Inputs')}</span>
														<div class="text-xs text-gray-400 mt-0.5">
															{#if isOfficialOpenAIConnection}
																{$i18n.t(
																	'Official OpenAI connections default to enabled. Compatible gateways usually need manual opt-in.'
																)}
															{:else}
																{$i18n.t(
																	'Uploads local document files through the OpenAI Files API when supported, and automatically falls back to local parsing when it fails.'
																)}
															{/if}
														</div>
													</div>
													<Switch
														state={nativeFileInputsEnabled}
														on:change={(e) => {
															nativeFileInputsEnabled = e.detail;
															nativeFileInputsTouched = true;
														}}
													/>
												</div>
											{/if}
										{/if}
									</div>
								{:else if isForceMode && !ollama && !direct && !gemini && !grok && !anthropic && !azure}
									<div class="text-xs text-amber-600 dark:text-amber-400">
										{$i18n.t('Force mode connections do not support Responses API auto-routing.')}
									</div>
								{/if}

								{#if !ollama && !direct}
									<!-- Headers -->
									<div
										class="bg-gray-50 dark:bg-gray-850 rounded-xl p-3 space-y-3 border border-gray-200 dark:border-gray-700"
									>
										<div class="flex flex-col">
											<label
												for="headers"
												class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
											>
												{$i18n.t('Custom Headers (JSON)')}
											</label>
											<Textarea
												className="w-full text-sm bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
												bind:value={headers}
												placeholder={$i18n.t('{"X-Custom-Header": "value"}')}
												required={false}
												minSize={60}
											/>
										</div>
									</div>
								{/if}
							</div>
						</CollapsibleSection>
					</div>
				</div>

				<!-- 按钮 -->
				<div class="flex justify-end pt-2 gap-2 shrink-0">
					{#if edit}
						<button
							type="button"
							class="px-4 py-2 text-sm font-medium text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 hover:bg-red-100 dark:hover:bg-red-900/40 border border-red-200 dark:border-red-800 rounded-xl transition"
							on:click={() => {
								onDelete();
								show = false;
							}}
						>
							{$i18n.t('Delete')}
						</button>
					{/if}

					<button
						type="submit"
						class="px-4 py-2 text-sm font-medium bg-black dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-gray-100 rounded-xl transition flex items-center gap-2"
						disabled={loading}
					>
						{$i18n.t('Save')}
						{#if loading}
							<Spinner className="size-4" />
						{/if}
					</button>
				</div>
			</form>
		</div>
	</div>
</Modal>
