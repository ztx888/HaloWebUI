<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';

	import { onMount, onDestroy, getContext, createEventDispatcher, tick } from 'svelte';

	const dispatch = createEventDispatcher();

	import {
		resetVectorDB,
		getEmbeddingConfig,
		updateEmbeddingConfig,
		getRerankingConfig,
		updateRerankingConfig,
		getRAGConfig,
		updateRAGConfig
	} from '$lib/apis/retrieval';

	import { reindexKnowledgeFiles } from '$lib/apis/knowledge';
	import { deleteAllFiles } from '$lib/apis/files';
	import ResetUploadDirConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ResetVectorDBConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ReindexKnowledgeFilesConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';

	const i18n: Writable<any> = getContext('i18n');

	type DocumentTab = 'general' | 'embedding' | 'retrieval' | 'danger';

	let selectedTab: DocumentTab = 'general';

	const tabMeta: Record<
		DocumentTab,
		{ label: string; description: string; badgeColor: string; iconColor: string }
	> = {
		general: {
			label: '文档处理',
			description: '内容提取引擎、文本分割、文件限制与云存储集成。',
			badgeColor: 'bg-gray-50 dark:bg-gray-950/30',
			iconColor: 'text-gray-500 dark:text-gray-400'
		},
		embedding: {
			label: '嵌入模型',
			description: '配置嵌入引擎、模型和批处理大小。',
			badgeColor: 'bg-violet-50 dark:bg-violet-950/30',
			iconColor: 'text-violet-500 dark:text-violet-400'
		},
		retrieval: {
			label: '检索设置',
			description: '全文模式、混合搜索、重排序和 RAG 模板。',
			badgeColor: 'bg-cyan-50 dark:bg-cyan-950/30',
			iconColor: 'text-cyan-500 dark:text-cyan-400'
		},
		danger: {
			label: '危险区域',
			description: '重置上传目录、向量存储和重建知识库索引。',
			badgeColor: 'bg-red-50 dark:bg-red-950/30',
			iconColor: 'text-red-500 dark:text-red-400'
		}
	};

	$: activeTabMeta = tabMeta[selectedTab];
	$: visibleTabs = ['general', 'embedding', 'retrieval', 'danger'] as const;
	$: currentTabDirty = selectedTab === 'danger' ? false : (dirtySections[selectedTab] ?? false);

	const resetCurrentTab = () => {
		if (selectedTab === 'general' || selectedTab === 'embedding' || selectedTab === 'retrieval') {
			resetSectionChanges(selectedTab);
		}
	};

	let updateEmbeddingModelLoading = false;
	let updateRerankingModelLoading = false;
	let saving = false;

	let showResetConfirm = false;
	let showResetUploadDirConfirm = false;
	let showReindexConfirm = false;

	let embeddingEngine = '';
	let embeddingModel = '';
	let embeddingBatchSize = 1;
	let rerankingModel = '';
	let rerankingEngine = 'local';
	let rerankingApiUrl = '';
	let rerankingApiKey = '';
	let initialSnapshot = null;
	let autoSyncBaseline = false;
	let baselineSyncTimeout: ReturnType<typeof setTimeout> | null = null;
	const BASELINE_SYNC_WINDOW_MS = 400;
	let runtimeCapabilities = {
		local_embedding_available: true,
		local_reranking_available: true,
		colbert_reranking_available: true,
		playwright_available: true,
		firecrawl_available: true,
		messages: {
			local_embedding: '',
			local_reranking: '',
			colbert_reranking: '',
			playwright: '',
			firecrawl: ''
		}
	};

	let OpenAIUrl = '';
	let OpenAIKey = '';

	let OllamaUrl = '';
	let OllamaKey = '';
	let AzureOpenAIUrl = '';
	let AzureOpenAIKey = '';
	let AzureOpenAIVersion = '';
	let enableAsyncEmbedding = true;
	let embeddingConcurrentRequests = 5;
	let selectedExtractionEngine = '';
	let isBm25WeightCustom = false;

	const normalizeRerankingEngine = (engine?: string | null) => {
		const normalized = (engine ?? '').trim();
		return normalized === '' || normalized === 'local' ? 'local' : normalized;
	};

	const getRerankingModelPlaceholder = () => {
		if (rerankingEngine === 'jina') {
			return 'jina-reranker-m0';
		}

		if (rerankingEngine === 'external') {
			return 'reranker';
		}

		return 'BAAI/bge-reranker-v2-m3';
	};

	const getRerankingApiUrlPlaceholder = () =>
		rerankingEngine === 'jina' ? 'https://api.jina.ai/v1' : 'https://example.com/v1/rerank';

	const applyRerankingEnginePreset = (engine: string) => {
		rerankingEngine = normalizeRerankingEngine(engine);
		if (rerankingEngine === 'jina') {
			if (!rerankingModel) {
				rerankingModel = 'jina-reranker-m0';
			}
			if (!rerankingApiUrl) {
				rerankingApiUrl = 'https://api.jina.ai/v1';
			}
		} else if (rerankingEngine === 'local' && !rerankingModel) {
			rerankingModel = 'BAAI/bge-reranker-v2-m3';
		}
	};

	const handleRerankingEngineChange = () => {
		applyRerankingEnginePreset(rerankingEngine);
	};

	const defaultDocumentProviderConfigs = {
		local_default: {},
		mineru: {
			api_base_url: 'https://mineru.net',
			api_key: '',
			token: '',
			model_version: 'vlm',
			language: '',
			page_range: '',
			enable_formula: true,
			enable_table: true,
			is_ocr: false
		},
		open_mineru: {
			api_base_url: 'https://mineru.net',
			language: '',
			page_range: '',
			enable_formula: true,
			enable_table: true,
			is_ocr: false
		},
		doc2x: {
			api_base_url: 'https://v2.doc2x.noedgeai.com',
			api_key: ''
		},
		paddleocr: {
			server_url: '',
			api_key: ''
		},
		mistral: {
			api_key: ''
		},
		azure_document_intelligence: {
			endpoint: '',
			key: ''
		}
	};

	type ContentEngineId =
		| ''
		| 'tika'
		| 'docling'
		| 'datalab_marker'
		| 'document_intelligence'
		| 'mistral_ocr'
		| 'mineru'
		| 'open_mineru'
		| 'doc2x'
		| 'paddleocr'
		| 'external';

	const contentEngineMeta: Record<
		ContentEngineId,
		{
			label: string;
			description: string;
			requirement: string;
			limits: string;
			badge?: string;
			officialUrl?: string;
			officialLabel?: string;
		}
	> = {
		'': {
			label: '默认（本地解析）',
			description: '使用 HaloWebUI 本地解析链路处理常见文本、PDF 和 Office 文档，无需额外服务。',
			requirement: '无需 API 密钥，开箱即用。',
			limits: '复杂扫描件、版面还原或高质量 OCR 场景建议切换到远程解析引擎。',
			badge: '推荐'
		},
		tika: {
			label: 'Tika',
			description: '通过 Apache Tika 服务补充更多文档格式解析能力。',
			requirement: '需要可访问的 Tika 服务地址。',
			limits: '适合通用格式兼容，不强调高质量 OCR。',
			officialUrl: 'https://tika.apache.org/',
			officialLabel: '查看 Apache Tika'
		},
		docling: {
			label: 'Docling',
			description: '通过 Docling 服务增强复杂文档解析和结构还原能力。',
			requirement: '需要 Docling 服务地址，可选 API 密钥。',
			limits: '额外参数较多，建议在需要更强文档结构化时启用。',
			officialUrl: 'https://docling-project.github.io/docling/',
			officialLabel: '查看 Docling 文档'
		},
		datalab_marker: {
			label: 'Datalab Marker API',
			description: '高级 PDF/Office 解析服务，支持更细粒度 OCR、分页和 LLM 增强。',
			requirement: '需要 Marker API 密钥，可选自定义基础 URL。',
			limits: '配置较多，适合高质量版面还原和扫描件场景。',
			badge: '高级',
			officialUrl: 'https://www.datalab.to/',
			officialLabel: '查看 Datalab 文档'
		},
		document_intelligence: {
			label: 'Azure 文档智能',
			description: '接入 Azure Document Intelligence，进行企业级文档解析。',
			requirement: '需要服务端点和密钥。',
			limits: '更适合企业文档流和 Azure 生态。',
			officialUrl: 'https://learn.microsoft.com/azure/ai-services/document-intelligence/',
			officialLabel: '查看 Azure 文档'
		},
		mistral_ocr: {
			label: 'Mistral OCR',
			description: '使用 Mistral OCR API 处理 PDF 和图片内容。',
			requirement: '需要 Mistral API 密钥，可自定义兼容基础 URL。',
			limits: '更偏 OCR 场景，不建议理解成通用文档服务。',
			officialUrl: 'https://docs.mistral.ai/capabilities/document/',
			officialLabel: '查看 Mistral OCR'
		},
		mineru: {
			label: 'MinerU',
			description: '使用官方 MinerU 引擎，支持本地部署或云端模式。',
			requirement: '本地模式需要自建服务；云端模式需要 API 密钥。',
			limits: '主要面向 PDF 解析，高级参数建议按官方文档配置。',
			officialUrl: 'https://mineru.net/doc/docs/',
			officialLabel: '查看 MinerU 文档'
		},
		open_mineru: {
			label: 'Open MinerU（免费）',
			description: '免 Token 的轻量文档处理入口，适合快速试用和小文件场景。',
			requirement: '无需 API 密钥。',
			limits: 'IP 限频，能力和文件规模明显低于正式 MinerU。',
			officialUrl: 'https://mineru.net/doc/docs/',
			officialLabel: '查看 Open MinerU 说明'
		},
		doc2x: {
			label: 'Doc2x',
			description: '第三方文档解析服务，适合对接 Doc2x 官方或兼容端点。',
			requirement: '通常需要 API 密钥。',
			limits: '能力与配额取决于你接入的 Doc2x 服务端。',
			officialUrl: 'https://doc2x.noedgeai.com/',
			officialLabel: '查看 Doc2x 官网'
		},
		paddleocr: {
			label: 'PaddleOCR',
			description: '对接第三方或自建 PaddleOCR OCR 接口。',
			requirement: '需要完整 OCR 接口地址；第三方服务通常还需要访问令牌。',
			limits: '不同服务商的模型能力、配额和响应字段可能不同。',
			officialUrl: 'https://aistudio.baidu.com/paddleocr/',
			officialLabel: '查看 PaddleOCR 官网'
		},
		external: {
			label: '外部文档加载器',
			description: '把文件转发到自定义文档解析 API，再回收结构化内容。',
			requirement: '需要兼容文档解析协议的完整接口 URL 和 API 密钥。',
			limits: '该接口需要接收文件二进制并返回 Document JSON，不适用于聊天补全接口。',
			badge: '扩展'
		}
	};

	const contentEngineOptions = (Object.keys(contentEngineMeta) as ContentEngineId[]).map((engine) => ({
		value: engine,
		label: contentEngineMeta[engine].label,
		description: contentEngineMeta[engine].description,
		badge: contentEngineMeta[engine].badge
	}));

	const getContentEngineMeta = (engine: string) =>
		contentEngineMeta[(engine as ContentEngineId) || ''] ?? contentEngineMeta[''];
	$: selectedContentEngineMeta = getContentEngineMeta(selectedExtractionEngine);

	const mergeProviderConfigs = (value: any = {}) => {
		const merged = structuredClone(defaultDocumentProviderConfigs);
		for (const [provider, config] of Object.entries(value ?? {})) {
			merged[provider] = { ...(merged[provider] ?? {}), ...(config as Record<string, any>) };
		}
		return merged;
	};

	const formatJsonSetting = (value: unknown) =>
		typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : (value ?? '');

	const buildLegacyExternalProcessUrl = (url: string) => {
		const normalized = url.trim();
		if (!normalized) return '';

		const match = normalized.match(/^([^?#]*)([?#].*)?$/);
		const base = match?.[1] ?? normalized;
		const suffix = match?.[2] ?? '';
		const sanitizedBase = base.replace(/\/+$/, '');

		if (sanitizedBase.endsWith('/process')) {
			return `${sanitizedBase}${suffix}`;
		}

		return `${sanitizedBase}/process${suffix}`;
	};

	const normalizeExternalLoaderUrl = (url: unknown, isFullPath: boolean) => {
		const normalized = String(url ?? '').trim();
		if (!normalized) return '';
		if (isFullPath) return normalized;
		return buildLegacyExternalProcessUrl(normalized);
	};

	const deriveExtractionEngine = (value: any): ContentEngineId => {
		const provider = String(value?.DOCUMENT_PROVIDER ?? '').trim();
		const engine = String(value?.CONTENT_EXTRACTION_ENGINE ?? '').trim();

		if (provider === 'open_mineru' || provider === 'doc2x' || provider === 'paddleocr') {
			return provider as ContentEngineId;
		}
		if (provider === 'mineru') {
			return 'mineru';
		}
		if (provider === 'mistral' || provider === 'azure_document_intelligence') {
			return provider === 'mistral' ? 'mistral_ocr' : 'document_intelligence';
		}
		if (
			[
				'tika',
				'docling',
				'datalab_marker',
				'document_intelligence',
				'mistral_ocr',
				'mineru',
				'external'
			].includes(engine)
		) {
			return engine as ContentEngineId;
		}

		return '';
	};

	const applyExtractionEngine = (engine: ContentEngineId) => {
		selectedExtractionEngine = engine;
		if (['open_mineru', 'doc2x', 'paddleocr'].includes(engine)) {
			RAGConfig.DOCUMENT_PROVIDER = engine;
			RAGConfig.CONTENT_EXTRACTION_ENGINE = '';
			return;
		}

		RAGConfig.DOCUMENT_PROVIDER = 'local_default';
		RAGConfig.CONTENT_EXTRACTION_ENGINE = engine;
	};

	const normalizeDocumentsSettings = (value: any) => {
		const mergedConfigs = mergeProviderConfigs(value?.DOCUMENT_PROVIDER_CONFIGS);
		const legacyProvider = String(value?.DOCUMENT_PROVIDER ?? '').trim();
		const legacyMineruConfig = mergedConfigs?.mineru ?? {};
		const legacyMistralConfig = mergedConfigs?.mistral ?? {};
		const legacyAzureConfig = mergedConfigs?.azure_document_intelligence ?? {};
		const externalDocumentLoaderUrlIsFullPath =
			value?.EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH ?? false;
		const normalizedTextSplitter = value?.TEXT_SPLITTER === 'markdown' ? '' : (value?.TEXT_SPLITTER ?? '');
		const enableMarkdownHeaderTextSplitter =
			value?.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER ??
			(value?.TEXT_SPLITTER === 'markdown' ? true : false);

		return {
			...value,
			FILE_PROCESSING_DEFAULT_MODE:
				value?.FILE_PROCESSING_DEFAULT_MODE ??
				(value?.BYPASS_EMBEDDING_AND_RETRIEVAL ? 'full_context' : 'retrieval'),
			DOCUMENT_PROVIDER: value?.DOCUMENT_PROVIDER ?? 'local_default',
			DOCUMENT_PROVIDER_CONFIGS: mergedConfigs,
			PDF_EXTRACT_IMAGES: value?.PDF_EXTRACT_IMAGES ?? false,
			PDF_LOADING_MODE: value?.PDF_LOADING_MODE ?? value?.PDF_LOADER_MODE ?? '',
			TIKA_SERVER_URL: value?.TIKA_SERVER_URL ?? '',
			DOCLING_SERVER_URL: value?.DOCLING_SERVER_URL ?? '',
			TEXT_SPLITTER: normalizedTextSplitter,
			ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER: enableMarkdownHeaderTextSplitter,
			CHUNK_MIN_SIZE_TARGET: value?.CHUNK_MIN_SIZE_TARGET ?? 0,
			RAG_HYBRID_SEARCH_BM25_WEIGHT:
				value?.RAG_HYBRID_SEARCH_BM25_WEIGHT ?? value?.HYBRID_BM25_WEIGHT ?? 0.5,
			DATALAB_MARKER_API_KEY: value?.DATALAB_MARKER_API_KEY ?? '',
			DATALAB_MARKER_API_BASE_URL: value?.DATALAB_MARKER_API_BASE_URL ?? '',
			DATALAB_MARKER_ADDITIONAL_CONFIG: value?.DATALAB_MARKER_ADDITIONAL_CONFIG ?? '',
			DATALAB_MARKER_SKIP_CACHE: value?.DATALAB_MARKER_SKIP_CACHE ?? false,
			DATALAB_MARKER_FORCE_OCR: value?.DATALAB_MARKER_FORCE_OCR ?? false,
			DATALAB_MARKER_PAGINATE: value?.DATALAB_MARKER_PAGINATE ?? false,
			DATALAB_MARKER_STRIP_EXISTING_OCR: value?.DATALAB_MARKER_STRIP_EXISTING_OCR ?? false,
			DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION:
				value?.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION ?? false,
			DATALAB_MARKER_FORMAT_LINES: value?.DATALAB_MARKER_FORMAT_LINES ?? false,
			DATALAB_MARKER_USE_LLM: value?.DATALAB_MARKER_USE_LLM ?? false,
			DATALAB_MARKER_OUTPUT_FORMAT: value?.DATALAB_MARKER_OUTPUT_FORMAT ?? 'markdown',
			EXTERNAL_DOCUMENT_LOADER_URL: normalizeExternalLoaderUrl(
				value?.EXTERNAL_DOCUMENT_LOADER_URL,
				externalDocumentLoaderUrlIsFullPath
			),
			EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH: externalDocumentLoaderUrlIsFullPath,
			EXTERNAL_DOCUMENT_LOADER_API_KEY: value?.EXTERNAL_DOCUMENT_LOADER_API_KEY ?? '',
			DOCLING_API_KEY: value?.DOCLING_API_KEY ?? '',
			DOCLING_PARAMS: formatJsonSetting(value?.DOCLING_PARAMS),
			DOCUMENT_INTELLIGENCE_ENDPOINT:
				value?.DOCUMENT_INTELLIGENCE_ENDPOINT ??
				(legacyProvider === 'azure_document_intelligence'
					? (legacyAzureConfig.endpoint ?? '')
					: ''),
			DOCUMENT_INTELLIGENCE_KEY:
				value?.DOCUMENT_INTELLIGENCE_KEY ??
				(legacyProvider === 'azure_document_intelligence' ? (legacyAzureConfig.key ?? '') : ''),
			DOCUMENT_INTELLIGENCE_MODEL: value?.DOCUMENT_INTELLIGENCE_MODEL ?? 'prebuilt-layout',
			MISTRAL_OCR_API_BASE_URL: value?.MISTRAL_OCR_API_BASE_URL ?? 'https://api.mistral.ai/v1',
			MISTRAL_OCR_API_KEY:
				value?.MISTRAL_OCR_API_KEY ??
				(legacyProvider === 'mistral' ? (legacyMistralConfig.api_key ?? '') : ''),
			MINERU_API_MODE:
				value?.MINERU_API_MODE ??
				(legacyProvider === 'mineru' &&
				String(legacyMineruConfig.api_key ?? '').trim() !== ''
					? 'cloud'
					: 'local'),
			MINERU_API_URL:
				value?.MINERU_API_URL ??
				(legacyProvider === 'mineru' ? 'https://mineru.net/api/v4' : 'http://localhost:8000'),
			MINERU_API_KEY:
				value?.MINERU_API_KEY ??
				(legacyProvider === 'mineru' ? (legacyMineruConfig.api_key ?? '') : ''),
			MINERU_API_TIMEOUT: value?.MINERU_API_TIMEOUT ?? '300',
			MINERU_PARAMS:
				formatJsonSetting(value?.MINERU_PARAMS) ||
				(legacyProvider === 'mineru'
					? formatJsonSetting({
							enable_ocr: legacyMineruConfig.is_ocr ?? false,
							enable_formula: legacyMineruConfig.enable_formula ?? true,
							enable_table: legacyMineruConfig.enable_table ?? true,
							language: legacyMineruConfig.language ?? '',
							model_version: legacyMineruConfig.model_version ?? 'pipeline',
							page_ranges: legacyMineruConfig.page_range ?? ''
						})
					: ''),
			ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS:
				value?.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS ?? false,
			FILE_IMAGE_COMPRESSION_WIDTH: value?.FILE_IMAGE_COMPRESSION_WIDTH ?? '',
			FILE_IMAGE_COMPRESSION_HEIGHT: value?.FILE_IMAGE_COMPRESSION_HEIGHT ?? '',
			ALLOWED_FILE_EXTENSIONS: Array.isArray(value?.ALLOWED_FILE_EXTENSIONS)
				? value.ALLOWED_FILE_EXTENSIONS.join(', ')
				: (value?.ALLOWED_FILE_EXTENSIONS ?? '')
		};
	};

	let RAGConfig: any = {
		FILE_PROCESSING_DEFAULT_MODE: 'retrieval',
		DOCUMENT_PROVIDER: 'local_default',
		DOCUMENT_PROVIDER_CONFIGS: mergeProviderConfigs(),
		CONTENT_EXTRACTION_ENGINE: '',
		DATALAB_MARKER_API_KEY: '',
		DATALAB_MARKER_API_BASE_URL: '',
		DATALAB_MARKER_ADDITIONAL_CONFIG: '',
		DATALAB_MARKER_SKIP_CACHE: false,
		DATALAB_MARKER_FORCE_OCR: false,
		DATALAB_MARKER_PAGINATE: false,
		DATALAB_MARKER_STRIP_EXISTING_OCR: false,
		DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION: false,
		DATALAB_MARKER_FORMAT_LINES: false,
		DATALAB_MARKER_USE_LLM: false,
		DATALAB_MARKER_OUTPUT_FORMAT: 'markdown',
		EXTERNAL_DOCUMENT_LOADER_URL: '',
		EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH: false,
		EXTERNAL_DOCUMENT_LOADER_API_KEY: '',
		PDF_EXTRACT_IMAGES: false,
		PDF_LOADING_MODE: '',
		TIKA_SERVER_URL: '',
		DOCLING_SERVER_URL: '',
		DOCLING_API_KEY: '',
		DOCLING_PARAMS: '',
		DOCUMENT_INTELLIGENCE_ENDPOINT: '',
		DOCUMENT_INTELLIGENCE_KEY: '',
		DOCUMENT_INTELLIGENCE_MODEL: 'prebuilt-layout',
		MISTRAL_OCR_API_BASE_URL: 'https://api.mistral.ai/v1',
		MISTRAL_OCR_API_KEY: '',
		MINERU_API_MODE: 'local',
		MINERU_API_URL: 'http://localhost:8000',
		MINERU_API_KEY: '',
		MINERU_API_TIMEOUT: '300',
		MINERU_PARAMS: '',
		BYPASS_EMBEDDING_AND_RETRIEVAL: false,
		TEXT_SPLITTER: '',
		ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER: false,
		CHUNK_SIZE: 0,
		CHUNK_OVERLAP: 0,
		CHUNK_MIN_SIZE_TARGET: 0,
		CHUNK_MIN_SIZE: 0,
		RAG_FULL_CONTEXT: false,
		ENABLE_RAG_HYBRID_SEARCH: false,
		ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS: false,
		TOP_K: 4,
		TOP_K_RERANKER: 4,
		RAG_HYBRID_SEARCH_BM25_WEIGHT: 0.5,
		RELEVANCE_THRESHOLD: 0,
		RAG_SYSTEM_CONTEXT: '',
		RAG_TEMPLATE: '',
		FILE_MAX_SIZE: 0,
		FILE_MAX_COUNT: 0,
		FILE_IMAGE_COMPRESSION_WIDTH: '',
		FILE_IMAGE_COMPRESSION_HEIGHT: '',
		ALLOWED_FILE_EXTENSIONS: '',
		ENABLE_GOOGLE_DRIVE_INTEGRATION: false,
		ENABLE_ONEDRIVE_INTEGRATION: false
	};

	const buildSnapshot = () => ({
			general: {
				FILE_PROCESSING_DEFAULT_MODE: RAGConfig?.FILE_PROCESSING_DEFAULT_MODE,
				selectedExtractionEngine,
				DOCUMENT_PROVIDER: RAGConfig?.DOCUMENT_PROVIDER,
				DOCUMENT_PROVIDER_CONFIGS: RAGConfig?.DOCUMENT_PROVIDER_CONFIGS,
				CONTENT_EXTRACTION_ENGINE: RAGConfig?.CONTENT_EXTRACTION_ENGINE,
				DATALAB_MARKER_API_KEY: RAGConfig?.DATALAB_MARKER_API_KEY,
				DATALAB_MARKER_API_BASE_URL: RAGConfig?.DATALAB_MARKER_API_BASE_URL,
				DATALAB_MARKER_ADDITIONAL_CONFIG: RAGConfig?.DATALAB_MARKER_ADDITIONAL_CONFIG,
				DATALAB_MARKER_SKIP_CACHE: RAGConfig?.DATALAB_MARKER_SKIP_CACHE,
				DATALAB_MARKER_FORCE_OCR: RAGConfig?.DATALAB_MARKER_FORCE_OCR,
				DATALAB_MARKER_PAGINATE: RAGConfig?.DATALAB_MARKER_PAGINATE,
				DATALAB_MARKER_STRIP_EXISTING_OCR: RAGConfig?.DATALAB_MARKER_STRIP_EXISTING_OCR,
				DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION: RAGConfig?.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION,
				DATALAB_MARKER_FORMAT_LINES: RAGConfig?.DATALAB_MARKER_FORMAT_LINES,
				DATALAB_MARKER_USE_LLM: RAGConfig?.DATALAB_MARKER_USE_LLM,
				DATALAB_MARKER_OUTPUT_FORMAT: RAGConfig?.DATALAB_MARKER_OUTPUT_FORMAT,
				EXTERNAL_DOCUMENT_LOADER_URL: RAGConfig?.EXTERNAL_DOCUMENT_LOADER_URL,
				EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH:
					RAGConfig?.EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH,
				EXTERNAL_DOCUMENT_LOADER_API_KEY: RAGConfig?.EXTERNAL_DOCUMENT_LOADER_API_KEY,
				PDF_EXTRACT_IMAGES: RAGConfig?.PDF_EXTRACT_IMAGES,
				PDF_LOADING_MODE: RAGConfig?.PDF_LOADING_MODE,
				TIKA_SERVER_URL: RAGConfig?.TIKA_SERVER_URL,
				DOCLING_SERVER_URL: RAGConfig?.DOCLING_SERVER_URL,
				DOCLING_API_KEY: RAGConfig?.DOCLING_API_KEY,
				DOCLING_PARAMS: RAGConfig?.DOCLING_PARAMS,
				DOCUMENT_INTELLIGENCE_ENDPOINT: RAGConfig?.DOCUMENT_INTELLIGENCE_ENDPOINT,
				DOCUMENT_INTELLIGENCE_KEY: RAGConfig?.DOCUMENT_INTELLIGENCE_KEY,
				DOCUMENT_INTELLIGENCE_MODEL: RAGConfig?.DOCUMENT_INTELLIGENCE_MODEL,
				MISTRAL_OCR_API_BASE_URL: RAGConfig?.MISTRAL_OCR_API_BASE_URL,
				MISTRAL_OCR_API_KEY: RAGConfig?.MISTRAL_OCR_API_KEY,
				MINERU_API_MODE: RAGConfig?.MINERU_API_MODE,
				MINERU_API_URL: RAGConfig?.MINERU_API_URL,
				MINERU_API_KEY: RAGConfig?.MINERU_API_KEY,
				MINERU_API_TIMEOUT: RAGConfig?.MINERU_API_TIMEOUT,
				MINERU_PARAMS: RAGConfig?.MINERU_PARAMS,
				TEXT_SPLITTER: RAGConfig?.TEXT_SPLITTER,
				ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER: RAGConfig?.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER,
				CHUNK_SIZE: RAGConfig?.CHUNK_SIZE,
				CHUNK_OVERLAP: RAGConfig?.CHUNK_OVERLAP,
				CHUNK_MIN_SIZE_TARGET: RAGConfig?.CHUNK_MIN_SIZE_TARGET,
				CHUNK_MIN_SIZE: RAGConfig?.CHUNK_MIN_SIZE,
				FILE_MAX_SIZE: RAGConfig?.FILE_MAX_SIZE,
				FILE_MAX_COUNT: RAGConfig?.FILE_MAX_COUNT,
				FILE_IMAGE_COMPRESSION_WIDTH: RAGConfig?.FILE_IMAGE_COMPRESSION_WIDTH,
				FILE_IMAGE_COMPRESSION_HEIGHT: RAGConfig?.FILE_IMAGE_COMPRESSION_HEIGHT,
				ALLOWED_FILE_EXTENSIONS: RAGConfig?.ALLOWED_FILE_EXTENSIONS,
				ENABLE_GOOGLE_DRIVE_INTEGRATION: RAGConfig?.ENABLE_GOOGLE_DRIVE_INTEGRATION,
				ENABLE_ONEDRIVE_INTEGRATION: RAGConfig?.ENABLE_ONEDRIVE_INTEGRATION
			},
			embedding: {
				embeddingEngine,
				embeddingModel,
				embeddingBatchSize,
				OpenAIUrl,
				OpenAIKey,
				AzureOpenAIUrl,
				AzureOpenAIKey,
				AzureOpenAIVersion,
				OllamaUrl,
				OllamaKey,
				enableAsyncEmbedding,
				embeddingConcurrentRequests
			},
			retrieval: {
				RAG_FULL_CONTEXT: RAGConfig?.RAG_FULL_CONTEXT,
				ENABLE_RAG_HYBRID_SEARCH: RAGConfig?.ENABLE_RAG_HYBRID_SEARCH,
				ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS: RAGConfig?.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS,
				TOP_K: RAGConfig?.TOP_K,
				TOP_K_RERANKER: RAGConfig?.TOP_K_RERANKER,
				RAG_HYBRID_SEARCH_BM25_WEIGHT: RAGConfig?.RAG_HYBRID_SEARCH_BM25_WEIGHT,
			RELEVANCE_THRESHOLD: RAGConfig?.RELEVANCE_THRESHOLD,
			RAG_SYSTEM_CONTEXT: RAGConfig?.RAG_SYSTEM_CONTEXT,
			RAG_TEMPLATE: RAGConfig?.RAG_TEMPLATE,
			rerankingModel,
			rerankingEngine,
			rerankingApiUrl,
			rerankingApiKey
		}
	});

	$: snapshot = (
		RAGConfig,
		selectedExtractionEngine,
		embeddingEngine,
		embeddingModel,
		embeddingBatchSize,
		OpenAIUrl,
		OpenAIKey,
		AzureOpenAIUrl,
		AzureOpenAIKey,
		AzureOpenAIVersion,
		OllamaUrl,
		OllamaKey,
		enableAsyncEmbedding,
		embeddingConcurrentRequests,
		rerankingModel,
		rerankingEngine,
		rerankingApiUrl,
		rerankingApiKey,
		buildSnapshot()
	);
	$: dirtySections = initialSnapshot
		? {
				general: !isSettingsSnapshotEqual(snapshot.general, initialSnapshot.general),
				embedding: !isSettingsSnapshotEqual(snapshot.embedding, initialSnapshot.embedding),
				retrieval: !isSettingsSnapshotEqual(snapshot.retrieval, initialSnapshot.retrieval)
			}
		: {
				general: false,
				embedding: false,
				retrieval: false
			};
	$: if (
		autoSyncBaseline &&
		(initialSnapshot === null || !isSettingsSnapshotEqual(snapshot, initialSnapshot))
	) {
		initialSnapshot = cloneSettingsSnapshot(snapshot);
	}

	const startBaselineSync = () => {
		autoSyncBaseline = true;
		if (baselineSyncTimeout) {
			clearTimeout(baselineSyncTimeout);
		}
		baselineSyncTimeout = setTimeout(() => {
			autoSyncBaseline = false;
			baselineSyncTimeout = null;
		}, BASELINE_SYNC_WINDOW_MS);
	};

	const resetSectionChanges = (section: string) => {
		if (!initialSnapshot) return;
		const snap = cloneSettingsSnapshot(initialSnapshot);

		if (section === 'general') {
			for (const f of Object.keys(snap.general)) {
				if (f === 'selectedExtractionEngine') continue;
				RAGConfig[f] = snap.general[f];
			}
			selectedExtractionEngine = snap.general.selectedExtractionEngine;
			RAGConfig = RAGConfig;
		} else if (section === 'embedding') {
			embeddingEngine = snap.embedding.embeddingEngine;
			embeddingModel = snap.embedding.embeddingModel;
			embeddingBatchSize = snap.embedding.embeddingBatchSize;
			OpenAIUrl = snap.embedding.OpenAIUrl;
			OpenAIKey = snap.embedding.OpenAIKey;
			AzureOpenAIUrl = snap.embedding.AzureOpenAIUrl;
			AzureOpenAIKey = snap.embedding.AzureOpenAIKey;
			AzureOpenAIVersion = snap.embedding.AzureOpenAIVersion;
			OllamaUrl = snap.embedding.OllamaUrl;
			OllamaKey = snap.embedding.OllamaKey;
			enableAsyncEmbedding = snap.embedding.enableAsyncEmbedding;
			embeddingConcurrentRequests = snap.embedding.embeddingConcurrentRequests;
		} else if (section === 'retrieval') {
			for (const f of Object.keys(snap.retrieval)) {
				if (f === 'rerankingModel') {
					rerankingModel = snap.retrieval.rerankingModel;
				} else if (f === 'rerankingEngine') {
					rerankingEngine = snap.retrieval.rerankingEngine;
				} else if (f === 'rerankingApiUrl') {
					rerankingApiUrl = snap.retrieval.rerankingApiUrl;
				} else if (f === 'rerankingApiKey') {
					rerankingApiKey = snap.retrieval.rerankingApiKey;
				} else {
					RAGConfig[f] = snap.retrieval[f];
				}
			}
			RAGConfig = RAGConfig;
			isBm25WeightCustom = (RAGConfig?.RAG_HYBRID_SEARCH_BM25_WEIGHT ?? 0.5) !== 0.5;
		}
	};

	const isColbertRerankingModel = (model: string) =>
		(model || '').includes('jinaai/jina-colbert-v2');

	const getLocalRerankingMessage = () =>
		isColbertRerankingModel(rerankingModel)
			? runtimeCapabilities.messages.colbert_reranking
			: runtimeCapabilities.messages.local_reranking;

	$: localEmbeddingUnavailable =
		embeddingEngine === '' && !runtimeCapabilities.local_embedding_available;
	$: localRerankingUnavailable =
		rerankingEngine === 'local' &&
		rerankingModel !== '' &&
		(isColbertRerankingModel(rerankingModel)
			? !runtimeCapabilities.colbert_reranking_available
			: !runtimeCapabilities.local_reranking_available);

	const embeddingModelUpdateHandler = async () => {
		if (localEmbeddingUnavailable) {
			toast.error(runtimeCapabilities.messages.local_embedding);
			return false;
		}
		if (embeddingEngine === '' && embeddingModel.split('/').length - 1 > 1) {
			toast.error('检测到模型文件系统路径。更新时必须填写模型短名称，无法继续。');
			return false;
		}
		if (embeddingEngine === 'ollama' && embeddingModel === '') {
			toast.error('请填写 Ollama 嵌入模型名称。');
			return false;
		}

		if (embeddingEngine === 'openai' && embeddingModel === '') {
			toast.error('请填写 OpenAI 嵌入模型名称。');
			return false;
		}
		if (embeddingEngine === 'azure_openai' && embeddingModel === '') {
			toast.error('请填写 Azure OpenAI 嵌入模型名称。');
			return false;
		}

		if (embeddingEngine === 'openai' && (OpenAIKey === '' || OpenAIUrl === '')) {
			toast.error('请填写 OpenAI API 基础 URL 和 API 密钥。');
			return false;
		}
		if (
			embeddingEngine === 'azure_openai' &&
			(AzureOpenAIKey === '' || AzureOpenAIUrl === '' || AzureOpenAIVersion === '')
		) {
			toast.error('请填写 Azure OpenAI API 基础 URL、API 密钥和 API 版本。');
			return false;
		}

		updateEmbeddingModelLoading = true;
		const res = await updateEmbeddingConfig(localStorage.token, {
			embedding_engine: embeddingEngine,
			embedding_model: embeddingModel,
			embedding_batch_size: embeddingBatchSize,
			enable_async_embedding: enableAsyncEmbedding,
			embedding_concurrent_requests: embeddingConcurrentRequests,
			ollama_config: {
				key: OllamaKey,
				url: OllamaUrl
			},
			openai_config: {
				key: OpenAIKey,
				url: OpenAIUrl
			},
			azure_openai_config: {
				key: AzureOpenAIKey,
				url: AzureOpenAIUrl,
				version: AzureOpenAIVersion
			}
		}).catch(async (error) => {
			toast.error(`${error}`);
			await setEmbeddingConfig();
			return null;
		});
		updateEmbeddingModelLoading = false;

		if (res?.status === true) {
			toast.success(`嵌入模型已更新为「${res.embedding_model}」`, {
				duration: 1000 * 10
			});
			return true;
		}

		return false;
	};

	const rerankingModelUpdateHandler = async () => {
		if (localRerankingUnavailable) {
			toast.error(getLocalRerankingMessage());
			return false;
		}
		if (['jina', 'external'].includes(rerankingEngine) && rerankingModel !== '' && rerankingApiUrl === '') {
			toast.error('请填写重排序服务 API 基础 URL。');
			return false;
		}
		updateRerankingModelLoading = true;
		const res = await updateRerankingConfig(localStorage.token, {
			reranking_engine: rerankingEngine,
			reranking_model: rerankingModel,
			api_config: {
				url: rerankingApiUrl,
				key: rerankingApiKey
			}
		}).catch(async (error) => {
			toast.error(`${error}`);
			await setRerankingConfig();
			return null;
		});
		updateRerankingModelLoading = false;

		if (res?.status === true) {
			if (rerankingModel === '') {
				toast.success('已关闭重排序模型。', {
					duration: 1000 * 10
				});
			} else {
				toast.success(`重排序模型已更新为「${res.reranking_model}」`, {
					duration: 1000 * 10
				});
			}
			return true;
		}

		return false;
	};

	const parseJsonConfig = (value: unknown, fallback = {}) => {
		if (typeof value === 'string') {
			const trimmed = value.trim();
			if (!trimmed) return fallback;
			return JSON.parse(trimmed);
		}
		if (value && typeof value === 'object') return value;
		return fallback;
	};

	const submitHandler = async () => {
		applyExtractionEngine(selectedExtractionEngine as ContentEngineId);
		const selectedProviderConfig =
			RAGConfig.DOCUMENT_PROVIDER_CONFIGS?.[RAGConfig.DOCUMENT_PROVIDER] ?? {};

		if (
			selectedExtractionEngine === 'doc2x' &&
			String(selectedProviderConfig.api_key ?? '').trim() === ''
		) {
			toast.error('请填写 Doc2x API 密钥。');
			return;
		}

		if (selectedExtractionEngine === 'paddleocr' && String(selectedProviderConfig.server_url ?? '').trim() === '') {
			toast.error('请填写 PaddleOCR 服务地址。');
			return;
		}

		if (
			selectedExtractionEngine === 'mineru' &&
			RAGConfig.MINERU_API_MODE === 'cloud' &&
			RAGConfig.MINERU_API_KEY === ''
		) {
			toast.error('云端 API 模式下必须填写 MinerU API 密钥。');
			return;
		}

		if (
			selectedExtractionEngine === 'external' &&
			String(RAGConfig.EXTERNAL_DOCUMENT_LOADER_URL ?? '').trim() === ''
		) {
			toast.error('请填写外部文档解析接口完整 URL。');
			return;
		}
		if (
			selectedExtractionEngine === 'external' &&
			String(RAGConfig.EXTERNAL_DOCUMENT_LOADER_API_KEY ?? '').trim() === ''
		) {
			toast.error('请填写外部文档解析接口 API 密钥。');
			return;
		}
		if (selectedExtractionEngine === 'tika' && RAGConfig.TIKA_SERVER_URL === '') {
			toast.error('请填写 Tika 服务地址。');
			return;
		}
		if (selectedExtractionEngine === 'docling' && RAGConfig.DOCLING_SERVER_URL === '') {
			toast.error('请填写 Docling 服务地址。');
			return;
		}
		if (selectedExtractionEngine === 'datalab_marker' && RAGConfig.DATALAB_MARKER_API_KEY === '') {
			toast.error('请填写 Datalab Marker API 密钥。');
			return;
		}
		if (
			selectedExtractionEngine === 'datalab_marker' &&
			RAGConfig.DATALAB_MARKER_ADDITIONAL_CONFIG &&
			RAGConfig.DATALAB_MARKER_ADDITIONAL_CONFIG.trim() !== ''
		) {
			try {
				JSON.parse(RAGConfig.DATALAB_MARKER_ADDITIONAL_CONFIG);
			} catch (e) {
				toast.error('Datalab Marker 附加配置 JSON 格式不正确。');
				return;
			}
		}

		if (
			selectedExtractionEngine === 'document_intelligence' &&
			(RAGConfig.DOCUMENT_INTELLIGENCE_ENDPOINT === '' ||
				RAGConfig.DOCUMENT_INTELLIGENCE_KEY === '')
		) {
			toast.error('请填写 Azure 文档智能的服务端点和 API 密钥。');
			return;
		}
		if (selectedExtractionEngine === 'mistral_ocr' && RAGConfig.MISTRAL_OCR_API_KEY === '') {
			toast.error('请填写 Mistral OCR API 密钥。');
			return;
		}
		if (
			selectedExtractionEngine === 'mineru' &&
			String(RAGConfig.MINERU_API_URL ?? '').trim() === ''
		) {
			toast.error('请填写 MinerU API 地址。');
			return;
		}

		if (selectedExtractionEngine === 'docling' && typeof RAGConfig.DOCLING_PARAMS === 'string') {
			try {
				parseJsonConfig(RAGConfig.DOCLING_PARAMS);
			} catch (e) {
				toast.error('Docling 高级参数 JSON 格式不正确。');
				return;
			}
		}
		if (selectedExtractionEngine === 'mineru' && typeof RAGConfig.MINERU_PARAMS === 'string') {
			try {
				parseJsonConfig(RAGConfig.MINERU_PARAMS);
			} catch (e) {
				toast.error('MinerU 高级参数 JSON 格式不正确。');
				return;
			}
		}

		if (RAGConfig.FILE_PROCESSING_DEFAULT_MODE === 'retrieval') {
			const embeddingUpdated = await embeddingModelUpdateHandler();
			if (!embeddingUpdated) {
				return;
			}

			if (RAGConfig.ENABLE_RAG_HYBRID_SEARCH) {
				const rerankingUpdated = await rerankingModelUpdateHandler();
				if (!rerankingUpdated) {
					return;
				}
			}
		}

		if (!isBm25WeightCustom) {
			RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT = 0.5;
		}

		const normalizedAllowedExtensions = String(RAGConfig.ALLOWED_FILE_EXTENSIONS ?? '')
			.split(',')
			.map((ext) => ext.trim().replace(/^\./, '').toLowerCase())
			.filter((ext) => ext !== '');
		const normalizedExternalLoaderUrl = String(RAGConfig.EXTERNAL_DOCUMENT_LOADER_URL ?? '').trim();
		RAGConfig.EXTERNAL_DOCUMENT_LOADER_URL = normalizedExternalLoaderUrl;
		RAGConfig.EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH = normalizedExternalLoaderUrl !== '';

		try {
			await updateRAGConfig(localStorage.token, {
				...RAGConfig,
				ALLOWED_FILE_EXTENSIONS: normalizedAllowedExtensions,
				DOCLING_PARAMS: parseJsonConfig(RAGConfig.DOCLING_PARAMS),
				MINERU_PARAMS: parseJsonConfig(RAGConfig.MINERU_PARAMS)
			});
		} catch (error) {
			toast.error(`${error}`);
			return;
		}
		await tick();
		await tick();
		startBaselineSync();
		initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
		toast.success('文档设置已保存。');
		dispatch('save');
	};

	const setEmbeddingConfig = async () => {
		const embeddingConfig = await getEmbeddingConfig(localStorage.token);

		if (embeddingConfig) {
			embeddingEngine = embeddingConfig.embedding_engine;
			embeddingModel = embeddingConfig.embedding_model;
			embeddingBatchSize = embeddingConfig.embedding_batch_size ?? 1;
			enableAsyncEmbedding = embeddingConfig.enable_async_embedding ?? true;
			embeddingConcurrentRequests = embeddingConfig.embedding_concurrent_requests ?? 5;

			OpenAIKey = embeddingConfig.openai_config?.key ?? '';
			OpenAIUrl = embeddingConfig.openai_config?.url ?? '';

			AzureOpenAIKey = embeddingConfig.azure_openai_config?.key ?? '';
			AzureOpenAIUrl = embeddingConfig.azure_openai_config?.url ?? '';
			AzureOpenAIVersion = embeddingConfig.azure_openai_config?.version ?? '';

			OllamaKey = embeddingConfig.ollama_config?.key ?? '';
			OllamaUrl = embeddingConfig.ollama_config?.url ?? '';
		}
	};

	const setRerankingConfig = async () => {
		const rerankingConfig = await getRerankingConfig(localStorage.token);

		if (rerankingConfig) {
			rerankingModel = rerankingConfig.reranking_model;
			rerankingEngine = normalizeRerankingEngine(rerankingConfig.reranking_engine);
			rerankingApiUrl = rerankingConfig.api_config?.url ?? '';
			rerankingApiKey = rerankingConfig.api_config?.key ?? '';
		}
	};

	onMount(async () => {
		const [, , ragRes] = await Promise.all([
			setEmbeddingConfig(),
			setRerankingConfig(),
			getRAGConfig(localStorage.token)
		]);

		RAGConfig = normalizeDocumentsSettings(ragRes ?? {});
		selectedExtractionEngine = deriveExtractionEngine(RAGConfig);
		isBm25WeightCustom = (RAGConfig?.RAG_HYBRID_SEARCH_BM25_WEIGHT ?? 0.5) !== 0.5;
		runtimeCapabilities = ragRes?.capabilities ?? runtimeCapabilities;
		await tick();
		await tick();
		startBaselineSync();
		initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
	});

	onDestroy(() => {
		if (baselineSyncTimeout) {
			clearTimeout(baselineSyncTimeout);
		}
	});
</script>

<ResetUploadDirConfirmDialog
	bind:show={showResetUploadDirConfirm}
	on:confirm={async () => {
		const res = await deleteAllFiles(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Success'));
		}
	}}
/>

<ResetVectorDBConfirmDialog
	bind:show={showResetConfirm}
	on:confirm={() => {
		const res = resetVectorDB(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Success'));
		}
	}}
/>

<ReindexKnowledgeFilesConfirmDialog
	bind:show={showReindexConfirm}
	on:confirm={async () => {
		const res = await reindexKnowledgeFiles(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Success'));
		}
	}}
/>

<form
	class="flex h-full min-h-0 flex-col text-sm"
	on:submit|preventDefault={async () => {
		if (saving) return;
		saving = true;
		try {
			await submitHandler();
		} finally {
			saving = false;
		}
	}}
>
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		<div class="mx-auto max-w-6xl space-y-6">
			<section class="glass-section p-5 space-y-5">
				<div class="@container flex flex-col gap-5">
					<div class="flex flex-col gap-4 @[64rem]:flex-row @[64rem]:items-start @[64rem]:justify-between">
						<div class="min-w-0 @[64rem]:flex-1">
							<div class="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-full border border-gray-200/80 bg-white/80 px-3.5 text-xs font-medium leading-none text-gray-600 dark:border-gray-700/80 dark:bg-gray-900/70 dark:text-gray-300">
								<span class="leading-none text-gray-400 dark:text-gray-500">{$i18n.t('Settings')}</span>
								<span class="leading-none text-gray-300 dark:text-gray-600">/</span>
								<span class="leading-none text-gray-900 dark:text-white">{$i18n.t('文档处理')}</span>
							</div>

							<div class="mt-3 flex items-start gap-3">
								<div class="glass-icon-badge {activeTabMeta.badgeColor}">
									{#if selectedTab === 'general'}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
											<path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
											<path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
										</svg>
									{:else if selectedTab === 'embedding'}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
											<path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z" />
										</svg>
									{:else if selectedTab === 'retrieval'}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
											<path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
										</svg>
									{:else}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
											<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
										</svg>
									{/if}
								</div>
								<div class="min-w-0">
									<div class="flex items-center gap-3">
										<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
											{$i18n.t(activeTabMeta.label)}
										</div>
										{#if selectedTab !== 'danger'}
											<InlineDirtyActions
												dirty={currentTabDirty}
												{saving}
												on:reset={resetCurrentTab}
											/>
										{/if}
									</div>
									<p class="mt-1 text-xs text-gray-400 dark:text-gray-500">
										{$i18n.t(activeTabMeta.description)}
									</p>
								</div>
							</div>
						</div>

						<div class="inline-flex max-w-full flex-wrap items-center gap-2 self-start rounded-2xl bg-gray-100 p-1 dark:bg-gray-850 @[64rem]:ml-auto @[64rem]:mt-11 @[64rem]:flex-nowrap @[64rem]:justify-end @[64rem]:shrink-0">
							{#each visibleTabs as tab}
								<button
									type="button"
									class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === tab ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`}
									on:click={() => {
										selectedTab = tab;
									}}
								>
									{#if tab === 'general'}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
											<path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
											<path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
										</svg>
									{:else if tab === 'embedding'}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
											<path stroke-linecap="round" stroke-linejoin="round" d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z" />
										</svg>
									{:else if tab === 'retrieval'}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
											<path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
										</svg>
									{:else}
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
											<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
										</svg>
									{/if}
									<span>{$i18n.t(tabMeta[tab].label)}</span>
								</button>
							{/each}
						</div>
					</div>
				</div>
			</section>

			{#if selectedTab === 'general'}
				<section
					class="p-5 space-y-3 transition-all duration-300 {dirtySections.general
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="space-y-3">
						<div class="glass-item p-5">
							<div class="mb-3 flex items-center justify-between gap-4">
								<div class="text-sm font-medium">默认文件处理模式</div>
								<HaloSelect
									bind:value={RAGConfig.FILE_PROCESSING_DEFAULT_MODE}
									options={[
										{ value: 'retrieval', label: '检索模式' },
										{ value: 'full_context', label: '完整上下文模式' },
										{ value: 'native_file', label: '原生文件模式' }
									]}
									className="w-fit"
								/>
							</div>
							<div class="text-xs text-gray-500 dark:text-gray-400">
								{#if RAGConfig.FILE_PROCESSING_DEFAULT_MODE === 'retrieval'}
									上传后立即解析、切分并建立向量索引，适合知识库与长文档问答。
								{:else if RAGConfig.FILE_PROCESSING_DEFAULT_MODE === 'full_context'}
									上传后只提取全文，不建立索引；发送消息时整份注入模型上下文。
								{:else}
									上传后只保存原文件，不做本地解析；优先直接交给支持原生文件输入的模型。
								{/if}
							</div>
						</div>

						<div class="glass-item p-5">
							<div class="mb-3 flex items-center justify-between gap-4">
								<div class="text-sm font-medium">内容提取引擎</div>
								<HaloSelect
									bind:value={selectedExtractionEngine}
									options={contentEngineOptions}
									className="w-fit"
									contentClassName="w-[22rem]"
									on:change={(e) => {
										applyExtractionEngine(e.detail.value);
									}}
								/>
							</div>
							<div class="text-xs text-gray-500 dark:text-gray-400">
								{selectedContentEngineMeta.description}
							</div>
							<div class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
								<div class="rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
									<div class="text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
										接入要求
									</div>
									<div class="mt-1 text-sm text-gray-700 dark:text-gray-200">
										{selectedContentEngineMeta.requirement}
									</div>
								</div>
								<div class="rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
									<div class="text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
										能力与限制
									</div>
									<div class="mt-1 text-sm text-gray-700 dark:text-gray-200">
										{selectedContentEngineMeta.limits}
									</div>
								</div>
							</div>
							{#if selectedContentEngineMeta.officialUrl}
								<div class="mt-3 flex flex-wrap gap-4 text-xs">
									<a
										class="text-sky-600 hover:underline dark:text-sky-400"
										href={selectedContentEngineMeta.officialUrl}
										target="_blank"
										rel="noreferrer"
									>
										{selectedContentEngineMeta.officialLabel ?? '查看官方文档'}
									</a>
								</div>
							{/if}

							<div class="mt-4 space-y-3">
								{#if selectedExtractionEngine === ''}
									<div class="flex items-center justify-between gap-4">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">PDF 图像提取（OCR）</div>
										<Switch bind:state={RAGConfig.PDF_EXTRACT_IMAGES} />
									</div>
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">PDF 加载模式</div>
										<HaloSelect
											bind:value={RAGConfig.PDF_LOADING_MODE}
											options={[
												{ value: '', label: '按页（默认）' },
												{ value: 'single', label: '合并为单文档' }
											]}
											className="w-full"
										/>
									</div>
								{:else if selectedExtractionEngine === 'external'}
									<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">外部文档加载器 URL</div>
											<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" bind:value={RAGConfig.EXTERNAL_DOCUMENT_LOADER_URL} placeholder="填写外部文档解析接口完整 URL" />
											<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
												这里填写完整请求 URL。该接口需兼容外部文档解析协议：接收 `PUT` 文件二进制并返回 `Document JSON`，不适用于 `/v1/chat/completions` 这类聊天补全接口。
											</div>
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
											<SensitiveInput placeholder="填写外部文档加载器 API 密钥" bind:value={RAGConfig.EXTERNAL_DOCUMENT_LOADER_API_KEY} />
										</div>
									</div>
								{:else if selectedExtractionEngine === 'tika'}
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">Tika 服务地址</div>
										<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" placeholder="填写 Tika 服务地址，例如 http://localhost:9998" bind:value={RAGConfig.TIKA_SERVER_URL} />
									</div>
								{:else if selectedExtractionEngine === 'docling'}
									<div class="space-y-3">
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">Docling 服务地址</div>
												<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" placeholder="填写 Docling 服务地址，例如 http://localhost:5001" bind:value={RAGConfig.DOCLING_SERVER_URL} />
											</div>
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">Docling API 密钥</div>
												<SensitiveInput placeholder="填写 Docling API 密钥" bind:value={RAGConfig.DOCLING_API_KEY} />
											</div>
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">Docling 高级参数（JSON）</div>
											<Textarea bind:value={RAGConfig.DOCLING_PARAMS} placeholder={'{\n  "image_export_mode": "placeholder"\n}'} />
										</div>
									</div>
								{:else if selectedExtractionEngine === 'datalab_marker'}
									<div class="space-y-3">
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
												<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" bind:value={RAGConfig.DATALAB_MARKER_API_BASE_URL} placeholder="https://www.datalab.to/api/v1/marker" />
											</div>
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
												<SensitiveInput placeholder="填写 Datalab Marker API 密钥" bind:value={RAGConfig.DATALAB_MARKER_API_KEY} />
											</div>
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">附加配置（JSON）</div>
											<Textarea bind:value={RAGConfig.DATALAB_MARKER_ADDITIONAL_CONFIG} placeholder={'{"disable_links": true}'} />
										</div>
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
												<div class="text-sm font-medium">使用 LLM 增强</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_USE_LLM} />
											</div>
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
												<div class="text-sm font-medium">跳过缓存</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_SKIP_CACHE} />
											</div>
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
												<div class="text-sm font-medium">强制 OCR</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_FORCE_OCR} />
											</div>
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
												<div class="text-sm font-medium">按页输出</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_PAGINATE} />
											</div>
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
												<div class="text-sm font-medium">移除已有 OCR</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_STRIP_EXISTING_OCR} />
											</div>
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
												<div class="text-sm font-medium">禁用图片提取</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION} />
											</div>
											<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40 md:col-span-2">
												<div class="text-sm font-medium">保留行格式</div>
												<Switch bind:state={RAGConfig.DATALAB_MARKER_FORMAT_LINES} />
											</div>
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">输出格式</div>
											<HaloSelect
												bind:value={RAGConfig.DATALAB_MARKER_OUTPUT_FORMAT}
												options={[
													{ value: 'markdown', label: 'Markdown（推荐）' },
													{ value: 'json', label: 'JSON（结构化）' },
													{ value: 'html', label: 'HTML（网页）' }
												]}
												className="w-full"
											/>
										</div>
									</div>
								{:else if selectedExtractionEngine === 'document_intelligence'}
									<div class="space-y-3">
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">服务端点</div>
												<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" placeholder="填写 Azure Document Intelligence 服务端点" bind:value={RAGConfig.DOCUMENT_INTELLIGENCE_ENDPOINT} />
											</div>
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
												<SensitiveInput placeholder="填写 Azure Document Intelligence API 密钥" bind:value={RAGConfig.DOCUMENT_INTELLIGENCE_KEY} />
											</div>
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">模型</div>
											<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" placeholder="prebuilt-layout" bind:value={RAGConfig.DOCUMENT_INTELLIGENCE_MODEL} />
										</div>
									</div>
								{:else if selectedExtractionEngine === 'mistral_ocr'}
									<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
											<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" placeholder="https://api.mistral.ai/v1" bind:value={RAGConfig.MISTRAL_OCR_API_BASE_URL} />
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
											<SensitiveInput placeholder="填写 Mistral API 密钥" bind:value={RAGConfig.MISTRAL_OCR_API_KEY} />
										</div>
									</div>
								{:else if selectedExtractionEngine === 'mineru'}
									<div class="space-y-3">
										<div class="grid grid-cols-1 gap-3 md:grid-cols-3">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 模式</div>
												<HaloSelect
													bind:value={RAGConfig.MINERU_API_MODE}
													options={[
														{ value: 'local', label: '本地部署' },
														{ value: 'cloud', label: '云端 API' }
													]}
													className="w-full"
													on:change={() => {
														const cloudUrl = 'https://mineru.net/api/v4';
														const localUrl = 'http://localhost:8000';
														if (RAGConfig.MINERU_API_MODE === 'cloud' && (!RAGConfig.MINERU_API_URL || RAGConfig.MINERU_API_URL === localUrl)) {
															RAGConfig.MINERU_API_URL = cloudUrl;
														}
														if (RAGConfig.MINERU_API_MODE === 'local' && (!RAGConfig.MINERU_API_URL || RAGConfig.MINERU_API_URL === cloudUrl)) {
															RAGConfig.MINERU_API_URL = localUrl;
														}
													}}
												/>
											</div>
											<div class="md:col-span-2">
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 地址</div>
												<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" bind:value={RAGConfig.MINERU_API_URL} placeholder={RAGConfig.MINERU_API_MODE === 'cloud' ? 'https://mineru.net/api/v4' : 'http://localhost:8000'} />
											</div>
										</div>
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
												<SensitiveInput placeholder="填写 MinerU API 密钥" bind:value={RAGConfig.MINERU_API_KEY} />
											</div>
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">请求超时（秒）</div>
												<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" type="number" min="1" bind:value={RAGConfig.MINERU_API_TIMEOUT} placeholder="300" />
											</div>
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">高级参数（JSON）</div>
											<Textarea bind:value={RAGConfig.MINERU_PARAMS} placeholder={'{\n  "enable_ocr": false,\n  "enable_formula": true,\n  "enable_table": true,\n  "language": "en",\n  "model_version": "pipeline",\n  "page_ranges": ""\n}'} />
										</div>
									</div>
								{:else if selectedExtractionEngine === 'open_mineru'}
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
										<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" bind:value={RAGConfig.DOCUMENT_PROVIDER_CONFIGS.open_mineru.api_base_url} />
									</div>
								{:else if selectedExtractionEngine === 'doc2x'}
									<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
											<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" bind:value={RAGConfig.DOCUMENT_PROVIDER_CONFIGS.doc2x.api_base_url} />
										</div>
										<div>
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
											<SensitiveInput placeholder="填写 Doc2x API 密钥" bind:value={RAGConfig.DOCUMENT_PROVIDER_CONFIGS.doc2x.api_key} />
										</div>
									</div>
								{:else if selectedExtractionEngine === 'paddleocr'}
									<div class="space-y-3">
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">OCR 接口地址</div>
												<input class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300" placeholder="https://your-service.aistudio-hub.baidu.com/ocr" bind:value={RAGConfig.DOCUMENT_PROVIDER_CONFIGS.paddleocr.server_url} />
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													优先填写第三方 PaddleOCR OCR 完整接口地址。按官方文档，常见云端地址形态为 `https://你的服务名.aistudio-hub.baidu.com/ocr`；自建服务常见为 `http://127.0.0.1:8080/ocr`。
												</div>
											</div>
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">访问令牌 / API 密钥</div>
												<SensitiveInput placeholder="第三方服务通常需要；留空表示不使用鉴权" bind:value={RAGConfig.DOCUMENT_PROVIDER_CONFIGS.paddleocr.api_key} />
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													若填写裸令牌，系统默认按官方常见格式发送 `Authorization: token &lt;TOKEN&gt;`；如服务商要求其他前缀，可直接填写完整值，例如 `Bearer xxx`。
												</div>
											</div>
										</div>
									</div>
								{/if}
							</div>
						</div>

						<div class="glass-item p-5">
							<div class="mb-3 flex items-center justify-between gap-4">
								<div class="text-sm font-medium">文本切分器</div>
								<HaloSelect
									bind:value={RAGConfig.TEXT_SPLITTER}
									options={[
										{
											value: '',
											label: '默认（按字符）'
										},
										{
											value: 'token',
											label: '按 Token（Tiktoken）'
										}
									]}
									className="w-fit"
								/>
							</div>

							<div class="mb-3 flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
								<div>
									<div class="text-sm font-medium">Markdown 标题分割器</div>
									<div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
										先按 Markdown 标题切分，再进入字符或 Token 切分。
									</div>
								</div>
								<Switch bind:state={RAGConfig.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER} />
							</div>

							<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">分块大小</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										type="number"
										placeholder="填写分块大小"
										bind:value={RAGConfig.CHUNK_SIZE}
										autocomplete="off"
										min="0"
									/>
								</div>
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">分块重叠</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										type="number"
										placeholder="填写分块重叠"
										bind:value={RAGConfig.CHUNK_OVERLAP}
										autocomplete="off"
										min="0"
									/>
								</div>
								{#if RAGConfig.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER}
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">最小块合并阈值</div>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											type="number"
											placeholder="0 表示关闭"
											bind:value={RAGConfig.CHUNK_MIN_SIZE_TARGET}
											autocomplete="off"
											min="0"
										/>
									</div>
								{/if}
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">最小分块大小</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										type="number"
										placeholder="0 表示关闭"
										bind:value={RAGConfig.CHUNK_MIN_SIZE}
										autocomplete="off"
										min="0"
									/>
								</div>
							</div>
						</div>

						<div class="glass-item p-5">
							<div class="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">文件限制</div>
							<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
								<div class="md:col-span-2">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">允许的文件扩展名</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										type="text"
										placeholder="例如：pdf, docx, txt；留空表示允许全部"
										bind:value={RAGConfig.ALLOWED_FILE_EXTENSIONS}
										autocomplete="off"
									/>
								</div>
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Max Upload Size')}</div>
									<Tooltip
										content={$i18n.t(
											'The maximum file size in MB. If the file size exceeds this limit, the file will not be uploaded.'
										)}
										placement="top-start"
									>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											type="number"
											placeholder="留空表示不限制"
											bind:value={RAGConfig.FILE_MAX_SIZE}
											autocomplete="off"
											min="0"
										/>
									</Tooltip>
								</div>

								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Max Upload Count')}</div>
									<Tooltip
										content={$i18n.t(
											'The maximum number of files that can be used at once in chat. If the number of files exceeds this limit, the files will not be uploaded.'
										)}
										placement="top-start"
									>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											type="number"
											placeholder="留空表示不限制"
											bind:value={RAGConfig.FILE_MAX_COUNT}
											autocomplete="off"
											min="0"
										/>
									</Tooltip>
								</div>
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">图片压缩宽度</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										type="number"
										placeholder="留空表示不限制"
										bind:value={RAGConfig.FILE_IMAGE_COMPRESSION_WIDTH}
										autocomplete="off"
										min="0"
									/>
								</div>
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">图片压缩高度</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										type="number"
										placeholder="留空表示不限制"
										bind:value={RAGConfig.FILE_IMAGE_COMPRESSION_HEIGHT}
										autocomplete="off"
										min="0"
									/>
								</div>
							</div>
						</div>

						<div class="glass-item p-5">
							<div class="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">云存储</div>
							<div class="space-y-3">
								<div class="flex items-center justify-between gap-4">
									<div class="text-sm font-medium">{$i18n.t('Google Drive')}</div>
									<Switch bind:state={RAGConfig.ENABLE_GOOGLE_DRIVE_INTEGRATION} />
								</div>
								<div class="flex items-center justify-between gap-4">
									<div class="text-sm font-medium">{$i18n.t('OneDrive')}</div>
									<Switch bind:state={RAGConfig.ENABLE_ONEDRIVE_INTEGRATION} />
								</div>
							</div>
						</div>
					</div>
				</section>
			{:else if selectedTab === 'embedding'}
				<section
					class="p-5 space-y-3 transition-all duration-300 {dirtySections.embedding
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="space-y-3">
						{#if RAGConfig.FILE_PROCESSING_DEFAULT_MODE !== 'retrieval'}
							<div class="glass-item border border-amber-200/70 bg-amber-50/80 p-4 text-xs leading-6 text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
								当前默认文件处理模式不是“检索模式”。这里的嵌入设置仍会影响手动按检索模式处理的文件，以及后续重建索引时的行为，但不会作用于默认按“完整上下文”或“原生文件”保存的上传。
							</div>
						{/if}
						<div class="glass-item p-5">
							<div class="flex items-center justify-between gap-4">
								<div class="text-sm font-medium">嵌入引擎</div>
								<HaloSelect
									bind:value={embeddingEngine}
									placeholder="选择嵌入引擎"
										options={[
											{
												value: '',
												label: '默认（SentenceTransformers）',
												disabled: !runtimeCapabilities.local_embedding_available
											},
											{ value: 'ollama', label: $i18n.t('Ollama') },
											{ value: 'openai', label: $i18n.t('OpenAI') },
											{ value: 'azure_openai', label: 'Azure OpenAI' }
										]}
										className="w-fit"
										on:change={(e) => {
											if (e.detail.value === 'ollama') {
												embeddingModel = '';
											} else if (e.detail.value === 'openai') {
												embeddingModel = 'text-embedding-3-small';
											} else if (e.detail.value === 'azure_openai') {
												embeddingModel = 'text-embedding-3-small';
											} else if (e.detail.value === '') {
												embeddingModel = 'sentence-transformers/all-MiniLM-L6-v2';
											}
										}}
									/>
							</div>

							{#if localEmbeddingUnavailable}
								<div class="mt-3 rounded-xl border border-amber-200/70 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
									{runtimeCapabilities.messages.local_embedding}
								</div>
							{/if}

							{#if embeddingEngine === 'openai'}
									<div class="mt-3 space-y-3 border-t border-gray-100/60 pt-3 dark:border-gray-800/40">
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
											<div class="w-full sm:w-1/2">
												<input
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													placeholder="填写 OpenAI 兼容 API 基础 URL"
													bind:value={OpenAIUrl}
													required
												/>
											</div>
										</div>
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
											<div class="w-full sm:w-1/2">
												<SensitiveInput
													inputClassName="w-full text-sm"
													placeholder="填写 OpenAI API 密钥"
													bind:value={OpenAIKey}
												/>
											</div>
										</div>
								</div>
								{:else if embeddingEngine === 'ollama'}
									<div class="mt-3 space-y-3 border-t border-gray-100/60 pt-3 dark:border-gray-800/40">
									<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
										<div class="w-full sm:w-1/2">
											<input
												class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
												placeholder="填写 Ollama 服务地址"
												bind:value={OllamaUrl}
												required
											/>
										</div>
									</div>
									<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
										<div class="w-full sm:w-1/2">
											<SensitiveInput
												inputClassName="w-full text-sm"
												placeholder="如有鉴权请填写 API 密钥"
												bind:value={OllamaKey}
												required={false}
											/>
										</div>
										</div>
									</div>
								{:else if embeddingEngine === 'azure_openai'}
									<div class="mt-3 space-y-3 border-t border-gray-100/60 pt-3 dark:border-gray-800/40">
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 基础 URL</div>
											<div class="w-full sm:w-1/2">
												<input
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													placeholder="https://YOUR-RESOURCE.openai.azure.com"
													bind:value={AzureOpenAIUrl}
													required
												/>
											</div>
										</div>
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 密钥</div>
											<div class="w-full sm:w-1/2">
												<SensitiveInput
													inputClassName="w-full text-sm"
													placeholder="填写 Azure OpenAI API 密钥"
													bind:value={AzureOpenAIKey}
												/>
											</div>
										</div>
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">API 版本</div>
											<div class="w-full sm:w-1/2">
												<input
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													placeholder="2024-02-01"
													bind:value={AzureOpenAIVersion}
													required
												/>
											</div>
										</div>
									</div>
								{/if}
						</div>

						<div class="glass-item p-5">
							<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">嵌入模型</div>
							{#if embeddingEngine === 'ollama'}
								<input
									class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
									bind:value={embeddingModel}
									placeholder={$i18n.t('Set embedding model')}
									required
								/>
							{:else}
								<div class="flex w-full gap-2">
									<input
										class="glass-input flex-1 px-3 py-2 text-sm dark:text-gray-300"
										placeholder={$i18n.t('Set embedding model (e.g. {{model}})', {
											model: embeddingModel.slice(-40)
										})}
										bind:value={embeddingModel}
									/>

									{#if embeddingEngine === ''}
										<button
											class="rounded-lg bg-gray-100 px-3 py-2 transition hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700"
											type="button"
											on:click={() => {
												embeddingModelUpdateHandler();
											}}
											disabled={updateEmbeddingModelLoading || localEmbeddingUnavailable}
										>
											{#if updateEmbeddingModelLoading}
												<Spinner className="size-4" />
											{:else}
												<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4">
													<path d="M8.75 2.75a.75.75 0 0 0-1.5 0v5.69L5.03 6.22a.75.75 0 0 0-1.06 1.06l3.5 3.5a.75.75 0 0 0 1.06 0l3.5-3.5a.75.75 0 0 0-1.06-1.06L8.75 8.44V2.75Z" />
													<path d="M3.5 9.75a.75.75 0 0 0-1.5 0v1.5A2.75 2.75 0 0 0 4.75 14h6.5A2.75 2.75 0 0 0 14 11.25v-1.5a.75.75 0 0 0-1.5 0v1.5c0 .69-.56 1.25-1.25 1.25h-6.5c-.69 0-1.25-.56-1.25-1.25v-1.5Z" />
												</svg>
											{/if}
										</button>
									{/if}
								</div>
							{/if}

							<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
								更换嵌入模型后，通常需要重新导入或重建全部文档索引。
							</div>

								{#if embeddingEngine === 'ollama' || embeddingEngine === 'openai' || embeddingEngine === 'azure_openai'}
									<div class="mt-3 space-y-3 border-t border-gray-100/60 pt-3 dark:border-gray-800/40">
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">嵌入批大小</div>
											<input
												bind:value={embeddingBatchSize}
												type="number"
												class="glass-input w-24 px-3 py-2 text-right text-sm dark:text-gray-300"
												min="-2"
												max="16000"
												step="1"
											/>
										</div>
										<div class="flex items-center justify-between gap-4">
											<div>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">异步嵌入处理</div>
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													并发执行嵌入批处理以加速文档处理，如遇速率限制可关闭。
												</div>
											</div>
											<Switch bind:state={enableAsyncEmbedding} />
										</div>
										<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
											<div>
												<div class="text-xs font-medium text-gray-500 dark:text-gray-400">嵌入并发请求数</div>
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													限制并发嵌入请求数，`0` 表示不限制。
												</div>
											</div>
											<input
												bind:value={embeddingConcurrentRequests}
												type="number"
												class="glass-input w-24 px-3 py-2 text-right text-sm dark:text-gray-300"
												min="0"
												step="1"
											/>
										</div>
									</div>
								{/if}
						</div>
					</div>
				</section>
			{:else if selectedTab === 'retrieval'}
				<section
					class="p-5 space-y-3 transition-all duration-300 {dirtySections.retrieval
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="space-y-3">
						{#if RAGConfig.FILE_PROCESSING_DEFAULT_MODE !== 'retrieval'}
							<div class="glass-item border border-sky-200/70 bg-sky-50/80 p-4 text-xs leading-6 text-sky-700 dark:border-sky-900/60 dark:bg-sky-950/20 dark:text-sky-300">
								当前默认文件处理模式不是“检索模式”。这里的召回、混合搜索和重排设置主要影响已建立索引的知识库内容，以及后续改为检索模式处理的文件。
							</div>
						{/if}
						<div class="glass-item p-5">
							<div class="flex items-center justify-between gap-4">
								<div class="text-sm font-medium">全文模式</div>
								<Tooltip
									content={RAGConfig.RAG_FULL_CONTEXT
										? $i18n.t(
												'Inject the entire content as context for comprehensive processing, this is recommended for complex queries.'
											)
										: $i18n.t(
												'Default to segmented retrieval for focused and relevant content extraction, this is recommended for most cases.'
											)}
								>
									<Switch bind:state={RAGConfig.RAG_FULL_CONTEXT} />
								</Tooltip>
							</div>
						</div>

						{#if !RAGConfig.RAG_FULL_CONTEXT}
							<div class="glass-item p-5">
								<div class="space-y-4">
									<div class="flex items-center justify-between gap-4">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">混合搜索</div>
										<Switch bind:state={RAGConfig.ENABLE_RAG_HYBRID_SEARCH} />
									</div>

										{#if RAGConfig.ENABLE_RAG_HYBRID_SEARCH === true}
											<div class="space-y-4 border-t border-gray-100/60 pt-4 dark:border-gray-800/40">
												<div class="flex items-center justify-between gap-4 rounded-2xl border border-gray-200/70 bg-white/60 px-4 py-3 dark:border-gray-700/70 dark:bg-gray-900/40">
													<div>
														<div class="text-sm font-medium">BM25 富化文本</div>
														<div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
															把文件名、标题、标题层级和摘要拼入 BM25 文本，提升词法召回率。
														</div>
													</div>
													<Switch bind:state={RAGConfig.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS} />
												</div>
												<div>
													<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
														重排序引擎
													</div>
												<select
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													bind:value={rerankingEngine}
													on:change={handleRerankingEngineChange}
												>
													<option value="local">本地模型</option>
													<option value="jina">Jina</option>
													<option value="external">外部 API</option>
												</select>
											</div>

											{#if ['jina', 'external'].includes(rerankingEngine)}
												<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
													<div>
														<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
															API 基础 URL
														</div>
														<input
															class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
															placeholder={getRerankingApiUrlPlaceholder()}
															bind:value={rerankingApiUrl}
														/>
													</div>
													<div>
														<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
															API 密钥
														</div>
														<SensitiveInput
															placeholder="填写重排序服务 API 密钥"
															required={false}
															bind:value={rerankingApiKey}
														/>
													</div>
												</div>
											{/if}

											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
													重排序模型
												</div>
												<div class="flex w-full gap-2">
													<input
														class="glass-input flex-1 px-3 py-2 text-sm dark:text-gray-300"
														placeholder={$i18n.t('Set reranking model (e.g. {{model}})', {
															model: getRerankingModelPlaceholder()
														})}
														bind:value={rerankingModel}
													/>
													<button
														class="rounded-lg bg-gray-100 px-3 py-2 transition hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700"
														type="button"
														on:click={() => {
															rerankingModelUpdateHandler();
														}}
														disabled={updateRerankingModelLoading || localRerankingUnavailable}
													>
														{#if updateRerankingModelLoading}
															<Spinner className="size-4" />
														{:else}
															<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="h-4 w-4">
																<path d="M8.75 2.75a.75.75 0 0 0-1.5 0v5.69L5.03 6.22a.75.75 0 0 0-1.06 1.06l3.5 3.5a.75.75 0 0 0 1.06 0l3.5-3.5a.75.75 0 0 0-1.06-1.06L8.75 8.44V2.75Z" />
																<path d="M3.5 9.75a.75.75 0 0 0-1.5 0v1.5A2.75 2.75 0 0 0 4.75 14h6.5A2.75 2.75 0 0 0 14 11.25v-1.5a.75.75 0 0 0-1.5 0v1.5c0 .69-.56 1.25-1.25 1.25h-6.5c-.69 0-1.25-.56-1.25-1.25v-1.5Z" />
															</svg>
														{/if}
													</button>
												</div>
												{#if localRerankingUnavailable}
													<div class="mt-2 rounded-xl border border-amber-200/70 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
														{getLocalRerankingMessage()}
													</div>
												{/if}
											</div>
										</div>
									{/if}

									<div class="border-t border-gray-100/60 pt-4 dark:border-gray-800/40">
										<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Top K')}</div>
												<input
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													type="number"
													placeholder={$i18n.t('Enter Top K')}
													bind:value={RAGConfig.TOP_K}
													autocomplete="off"
													min="0"
												/>
											</div>
											{#if RAGConfig.ENABLE_RAG_HYBRID_SEARCH === true}
												<div>
													<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
														重排序候选数
													</div>
													<input
														class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
														type="number"
														placeholder="填写重排序候选数"
														bind:value={RAGConfig.TOP_K_RERANKER}
														autocomplete="off"
														min="0"
													/>
												</div>
											{/if}
										</div>
									</div>

										{#if RAGConfig.ENABLE_RAG_HYBRID_SEARCH === true}
											<div class="space-y-4 border-t border-gray-100/60 pt-4 dark:border-gray-800/40">
												<div>
													<div class="mb-1.5 flex items-center justify-between">
														<div class="text-xs font-medium text-gray-500 dark:text-gray-400">BM25 权重</div>
														<button
															class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition hover:border-gray-300 hover:text-gray-900 dark:border-gray-700 dark:text-gray-300 dark:hover:border-gray-600 dark:hover:text-white"
															type="button"
															on:click={() => {
																isBm25WeightCustom = !isBm25WeightCustom;
																if (!isBm25WeightCustom) {
																	RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT = 0.5;
																} else if (RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT === null || RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT === undefined) {
																	RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT = 0.5;
																}
															}}
														>
															{isBm25WeightCustom ? '自定义' : '默认'}
														</button>
													</div>
													{#if isBm25WeightCustom}
														<div class="space-y-2">
															<div class="flex items-center justify-between text-xs text-gray-400">
																<span>语义</span>
																<span>{RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT ?? 0.5}</span>
																<span>词法</span>
															</div>
															<input
																class="w-full"
																type="range"
																step="0.05"
																min="0"
																max="1"
																bind:value={RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT}
															/>
															<input
																class="glass-input w-28 px-3 py-2 text-right text-sm dark:text-gray-300"
																type="number"
																step="0.01"
																min="0"
																max="1"
																bind:value={RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT}
															/>
														</div>
													{/if}
													<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
														{$i18n.t(
															'Balance between keyword (BM25) and semantic (vector) search. 0 = pure vector, 1 = pure keyword.'
													)}
												</div>
											</div>

											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
													相关性阈值
												</div>
												<input
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													type="number"
													step="0.01"
													placeholder="填写阈值分数"
													bind:value={RAGConfig.RELEVANCE_THRESHOLD}
													autocomplete="off"
													min="0.0"
													title="分数范围为 0.0 到 1.0。"
												/>
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													设置后，仅返回分数大于或等于该阈值的结果。
												</div>
											</div>
										</div>
									{/if}
								</div>
							</div>
						{/if}

						<div class="glass-item p-5">
							<div class="space-y-4">
								<div>
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
										RAG 系统上下文
									</div>
									<Tooltip
										content="固定追加在 RAG 上下文前的系统指令，可提升 KV Cache 复用稳定性。"
										placement="top-start"
										className="w-full"
									>
										<Textarea
											bind:value={RAGConfig.RAG_SYSTEM_CONTEXT}
											placeholder="留空表示不使用固定系统前缀；也可以填写面向 RAG 查询的系统指令"
										/>
									</Tooltip>
								</div>

								<div class="border-t border-gray-100/60 pt-4 dark:border-gray-800/40">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">RAG 提示模板</div>
									<Tooltip
										content="留空时使用默认提示词，也可以在这里填写自定义模板。"
										placement="top-start"
										className="w-full"
									>
										<Textarea
											bind:value={RAGConfig.RAG_TEMPLATE}
											placeholder="留空表示使用默认提示词；也可以填写自定义模板"
										/>
									</Tooltip>
								</div>
							</div>
						</div>
					</div>
				</section>
			{:else if selectedTab === 'danger'}
				<section class="p-5 space-y-3 transition-all duration-300 glass-section border-red-200/60 dark:border-red-800/40">
					<div class="glass-item border border-red-100/70 bg-red-50/70 p-5 dark:border-red-900/40 dark:bg-red-950/20">
						<div class="flex items-start gap-3">
							<div class="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-red-100 text-red-600 dark:bg-red-950/50 dark:text-red-400">
								<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor" class="size-4">
									<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
								</svg>
							</div>
							<div class="min-w-0">
								<div class="text-sm font-medium text-red-700 dark:text-red-300">{$i18n.t('Danger Zone')}</div>
								<p class="mt-1 text-xs leading-5 text-red-600/85 dark:text-red-300/80">
									{$i18n.t('These actions affect uploaded files, vector storage, and knowledge indexing. Please confirm carefully before proceeding.')}
								</p>
							</div>
						</div>
					</div>

					<div class="glass-item p-5">
						<div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
							<div class="min-w-0">
								<div class="text-sm font-medium">{$i18n.t('Reset Upload Directory')}</div>
								<p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('Delete all uploaded document files from the server storage directory.')}
								</p>
							</div>
							<button
								class="rounded-lg bg-red-50 px-3.5 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-100 dark:bg-red-950/30 dark:text-red-400 dark:hover:bg-red-900/40"
								type="button"
								on:click={() => {
									showResetUploadDirConfirm = true;
								}}
							>
								{$i18n.t('Reset')}
							</button>
						</div>
					</div>

					<div class="glass-item p-5">
						<div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
							<div class="min-w-0">
								<div class="text-sm font-medium">{$i18n.t('Reset Vector Storage/Knowledge')}</div>
								<p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('Clear vector storage and remove indexed knowledge data for all documents.')}
								</p>
							</div>
							<button
								class="rounded-lg bg-red-50 px-3.5 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-100 dark:bg-red-950/30 dark:text-red-400 dark:hover:bg-red-900/40"
								type="button"
								on:click={() => {
									showResetConfirm = true;
								}}
							>
								{$i18n.t('Reset')}
							</button>
						</div>
					</div>

					<div class="glass-item p-5">
						<div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
							<div class="min-w-0">
								<div class="text-sm font-medium">{$i18n.t('Reindex Knowledge Base Vectors')}</div>
								<p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('Rebuild the vector index for existing knowledge files after model or retrieval changes.')}
								</p>
							</div>
							<button
								class="rounded-lg bg-red-50 px-3.5 py-1.5 text-xs font-medium text-red-600 transition hover:bg-red-100 dark:bg-red-950/30 dark:text-red-400 dark:hover:bg-red-900/40"
								type="button"
								on:click={() => {
									showReindexConfirm = true;
								}}
							>
								{$i18n.t('Reindex')}
							</button>
						</div>
					</div>
				</section>
			{/if}
		</div>
	</div>
</form>
