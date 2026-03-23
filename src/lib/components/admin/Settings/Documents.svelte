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
	$: visibleTabs = RAGConfig.BYPASS_EMBEDDING_AND_RETRIEVAL
		? (['general', 'danger'] as const)
		: (['general', 'embedding', 'retrieval', 'danger'] as const);
	$: if (
		RAGConfig.BYPASS_EMBEDDING_AND_RETRIEVAL &&
		(selectedTab === 'embedding' || selectedTab === 'retrieval')
	) {
		selectedTab = 'general';
	}
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

	let RAGConfig: any = {
		CONTENT_EXTRACTION_ENGINE: '',
		PDF_EXTRACT_IMAGES: false,
		PDF_LOADING_MODE: '',
		TIKA_SERVER_URL: '',
		DOCLING_SERVER_URL: '',
		DOCUMENT_INTELLIGENCE_ENDPOINT: '',
		DOCUMENT_INTELLIGENCE_KEY: '',
		MISTRAL_OCR_API_KEY: '',
		BYPASS_EMBEDDING_AND_RETRIEVAL: false,
		TEXT_SPLITTER: '',
		CHUNK_SIZE: 0,
		CHUNK_OVERLAP: 0,
		CHUNK_MIN_SIZE: 0,
		RAG_FULL_CONTEXT: false,
		ENABLE_RAG_HYBRID_SEARCH: false,
		TOP_K: 4,
		TOP_K_RERANKER: 4,
		RAG_HYBRID_SEARCH_BM25_WEIGHT: 0.5,
		RELEVANCE_THRESHOLD: 0,
		RAG_SYSTEM_CONTEXT: '',
		RAG_TEMPLATE: '',
		FILE_MAX_SIZE: 0,
		FILE_MAX_COUNT: 0,
		ENABLE_GOOGLE_DRIVE_INTEGRATION: false,
		ENABLE_ONEDRIVE_INTEGRATION: false
	};

	const buildSnapshot = () => ({
		general: {
			CONTENT_EXTRACTION_ENGINE: RAGConfig?.CONTENT_EXTRACTION_ENGINE,
			PDF_EXTRACT_IMAGES: RAGConfig?.PDF_EXTRACT_IMAGES,
			PDF_LOADING_MODE: RAGConfig?.PDF_LOADING_MODE,
			TIKA_SERVER_URL: RAGConfig?.TIKA_SERVER_URL,
			DOCLING_SERVER_URL: RAGConfig?.DOCLING_SERVER_URL,
			DOCUMENT_INTELLIGENCE_ENDPOINT: RAGConfig?.DOCUMENT_INTELLIGENCE_ENDPOINT,
			DOCUMENT_INTELLIGENCE_KEY: RAGConfig?.DOCUMENT_INTELLIGENCE_KEY,
			MISTRAL_OCR_API_KEY: RAGConfig?.MISTRAL_OCR_API_KEY,
			BYPASS_EMBEDDING_AND_RETRIEVAL: RAGConfig?.BYPASS_EMBEDDING_AND_RETRIEVAL,
			TEXT_SPLITTER: RAGConfig?.TEXT_SPLITTER,
			CHUNK_SIZE: RAGConfig?.CHUNK_SIZE,
			CHUNK_OVERLAP: RAGConfig?.CHUNK_OVERLAP,
			CHUNK_MIN_SIZE: RAGConfig?.CHUNK_MIN_SIZE,
			FILE_MAX_SIZE: RAGConfig?.FILE_MAX_SIZE,
			FILE_MAX_COUNT: RAGConfig?.FILE_MAX_COUNT,
			ENABLE_GOOGLE_DRIVE_INTEGRATION: RAGConfig?.ENABLE_GOOGLE_DRIVE_INTEGRATION,
			ENABLE_ONEDRIVE_INTEGRATION: RAGConfig?.ENABLE_ONEDRIVE_INTEGRATION
		},
		embedding: {
			embeddingEngine,
			embeddingModel,
			embeddingBatchSize,
			OpenAIUrl,
			OpenAIKey,
			OllamaUrl,
			OllamaKey
		},
		retrieval: {
			RAG_FULL_CONTEXT: RAGConfig?.RAG_FULL_CONTEXT,
			ENABLE_RAG_HYBRID_SEARCH: RAGConfig?.ENABLE_RAG_HYBRID_SEARCH,
			TOP_K: RAGConfig?.TOP_K,
			TOP_K_RERANKER: RAGConfig?.TOP_K_RERANKER,
			RAG_HYBRID_SEARCH_BM25_WEIGHT: RAGConfig?.RAG_HYBRID_SEARCH_BM25_WEIGHT,
			RELEVANCE_THRESHOLD: RAGConfig?.RELEVANCE_THRESHOLD,
			RAG_SYSTEM_CONTEXT: RAGConfig?.RAG_SYSTEM_CONTEXT,
			RAG_TEMPLATE: RAGConfig?.RAG_TEMPLATE,
			rerankingModel
		}
	});

	$: snapshot = (
		RAGConfig,
		embeddingEngine,
		embeddingModel,
		embeddingBatchSize,
		OpenAIUrl,
		OpenAIKey,
		OllamaUrl,
		OllamaKey,
		rerankingModel,
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
				RAGConfig[f] = snap.general[f];
			}
			RAGConfig = RAGConfig;
		} else if (section === 'embedding') {
			embeddingEngine = snap.embedding.embeddingEngine;
			embeddingModel = snap.embedding.embeddingModel;
			embeddingBatchSize = snap.embedding.embeddingBatchSize;
			OpenAIUrl = snap.embedding.OpenAIUrl;
			OpenAIKey = snap.embedding.OpenAIKey;
			OllamaUrl = snap.embedding.OllamaUrl;
			OllamaKey = snap.embedding.OllamaKey;
		} else if (section === 'retrieval') {
			for (const f of Object.keys(snap.retrieval)) {
				if (f === 'rerankingModel') {
					rerankingModel = snap.retrieval.rerankingModel;
				} else {
					RAGConfig[f] = snap.retrieval[f];
				}
			}
			RAGConfig = RAGConfig;
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
		['', 'local'].includes(rerankingEngine) &&
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
			toast.error(
				$i18n.t(
					'Model filesystem path detected. Model shortname is required for update, cannot continue.'
				)
			);
			return false;
		}
		if (embeddingEngine === 'ollama' && embeddingModel === '') {
			toast.error(
				$i18n.t(
					'Model filesystem path detected. Model shortname is required for update, cannot continue.'
				)
			);
			return false;
		}

		if (embeddingEngine === 'openai' && embeddingModel === '') {
			toast.error(
				$i18n.t(
					'Model filesystem path detected. Model shortname is required for update, cannot continue.'
				)
			);
			return false;
		}

		if ((embeddingEngine === 'openai' && OpenAIKey === '') || OpenAIUrl === '') {
			toast.error($i18n.t('OpenAI URL/Key required.'));
			return false;
		}

		updateEmbeddingModelLoading = true;
		const res = await updateEmbeddingConfig(localStorage.token, {
			embedding_engine: embeddingEngine,
			embedding_model: embeddingModel,
			embedding_batch_size: embeddingBatchSize,
			ollama_config: {
				key: OllamaKey,
				url: OllamaUrl
			},
			openai_config: {
				key: OpenAIKey,
				url: OpenAIUrl
			}
		}).catch(async (error) => {
			toast.error(`${error}`);
			await setEmbeddingConfig();
			return null;
		});
		updateEmbeddingModelLoading = false;

		if (res?.status === true) {
			toast.success($i18n.t('Embedding model set to "{{embedding_model}}"', res), {
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
		updateRerankingModelLoading = true;
		const res = await updateRerankingConfig(localStorage.token, {
			reranking_model: rerankingModel
		}).catch(async (error) => {
			toast.error(`${error}`);
			await setRerankingConfig();
			return null;
		});
		updateRerankingModelLoading = false;

		if (res?.status === true) {
			if (rerankingModel === '') {
				toast.success($i18n.t('Reranking model disabled', res), {
					duration: 1000 * 10
				});
			} else {
				toast.success($i18n.t('Reranking model set to "{{reranking_model}}"', res), {
					duration: 1000 * 10
				});
			}
			return true;
		}

		return false;
	};

	const submitHandler = async () => {
		if (RAGConfig.CONTENT_EXTRACTION_ENGINE === 'tika' && RAGConfig.TIKA_SERVER_URL === '') {
			toast.error($i18n.t('Tika Server URL required.'));
			return;
		}
		if (
			RAGConfig.CONTENT_EXTRACTION_ENGINE === 'docling' &&
			RAGConfig.DOCLING_SERVER_URL === ''
		) {
			toast.error($i18n.t('Docling Server URL required.'));
			return;
		}

		if (
			RAGConfig.CONTENT_EXTRACTION_ENGINE === 'document_intelligence' &&
			(RAGConfig.DOCUMENT_INTELLIGENCE_ENDPOINT === '' ||
				RAGConfig.DOCUMENT_INTELLIGENCE_KEY === '')
		) {
			toast.error($i18n.t('Document Intelligence endpoint and key required.'));
			return;
		}
		if (
			RAGConfig.CONTENT_EXTRACTION_ENGINE === 'mistral_ocr' &&
			RAGConfig.MISTRAL_OCR_API_KEY === ''
		) {
			toast.error($i18n.t('Mistral OCR API Key required.'));
			return;
		}

		if (!RAGConfig.BYPASS_EMBEDDING_AND_RETRIEVAL) {
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

		await updateRAGConfig(localStorage.token, RAGConfig);
		await tick();
		await tick();
		startBaselineSync();
		initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
		dispatch('save');
	};

	const setEmbeddingConfig = async () => {
		const embeddingConfig = await getEmbeddingConfig(localStorage.token);

		if (embeddingConfig) {
			embeddingEngine = embeddingConfig.embedding_engine;
			embeddingModel = embeddingConfig.embedding_model;
			embeddingBatchSize = embeddingConfig.embedding_batch_size ?? 1;

			OpenAIKey = embeddingConfig.openai_config.key;
			OpenAIUrl = embeddingConfig.openai_config.url;

			OllamaKey = embeddingConfig.ollama_config.key;
			OllamaUrl = embeddingConfig.ollama_config.url;
		}
	};

	const setRerankingConfig = async () => {
		const rerankingConfig = await getRerankingConfig(localStorage.token);

		if (rerankingConfig) {
			rerankingModel = rerankingConfig.reranking_model;
			rerankingEngine = rerankingConfig.reranking_engine ?? 'local';
		}
	};

	onMount(async () => {
		const [, , ragRes] = await Promise.all([
			setEmbeddingConfig(),
			setRerankingConfig(),
			getRAGConfig(localStorage.token)
		]);

		RAGConfig = ragRes;
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
								<div class="text-sm font-medium">{$i18n.t('Content Extraction Engine')}</div>
								<HaloSelect
									bind:value={RAGConfig.CONTENT_EXTRACTION_ENGINE}
									options={[
										{ value: '', label: $i18n.t('Default') },
										{ value: 'tika', label: $i18n.t('Tika') },
										{ value: 'docling', label: $i18n.t('Docling') },
										{ value: 'document_intelligence', label: $i18n.t('Document Intelligence') },
										{ value: 'mistral_ocr', label: $i18n.t('Mistral OCR') }
									]}
									className="w-fit"
								/>
							</div>

							{#if RAGConfig.CONTENT_EXTRACTION_ENGINE === ''}
								<div class="flex items-center justify-between gap-4">
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('PDF Extract Images (OCR)')}</div>
									<Switch bind:state={RAGConfig.PDF_EXTRACT_IMAGES} />
								</div>
								<div class="mt-2">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('PDF Loading Mode')}</div>
									<HaloSelect
										bind:value={RAGConfig.PDF_LOADING_MODE}
										options={[
											{ value: '', label: $i18n.t('Page (Default)') },
											{ value: 'single', label: $i18n.t('Single Document') }
										]}
										className="w-full"
									/>
								</div>
							{:else if RAGConfig.CONTENT_EXTRACTION_ENGINE === 'tika'}
								<div class="mt-2">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Tika Server URL')}</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										placeholder={$i18n.t('Enter Tika Server URL')}
										bind:value={RAGConfig.TIKA_SERVER_URL}
									/>
								</div>
							{:else if RAGConfig.CONTENT_EXTRACTION_ENGINE === 'docling'}
								<div class="mt-2">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Docling Server URL')}</div>
									<input
										class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
										placeholder={$i18n.t('Enter Docling Server URL')}
										bind:value={RAGConfig.DOCLING_SERVER_URL}
									/>
								</div>
							{:else if RAGConfig.CONTENT_EXTRACTION_ENGINE === 'document_intelligence'}
								<div class="mt-2 space-y-3">
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
											{$i18n.t('Document Intelligence Endpoint')}
										</div>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											placeholder={$i18n.t('Enter Document Intelligence Endpoint')}
											bind:value={RAGConfig.DOCUMENT_INTELLIGENCE_ENDPOINT}
										/>
									</div>
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Key')}</div>
										<SensitiveInput
											placeholder={$i18n.t('Enter Document Intelligence Key')}
											bind:value={RAGConfig.DOCUMENT_INTELLIGENCE_KEY}
										/>
									</div>
								</div>
							{:else if RAGConfig.CONTENT_EXTRACTION_ENGINE === 'mistral_ocr'}
								<div class="mt-2">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Key')}</div>
									<SensitiveInput
										placeholder={$i18n.t('Enter Mistral API Key')}
										bind:value={RAGConfig.MISTRAL_OCR_API_KEY}
									/>
								</div>
							{/if}
						</div>

						<div class="glass-item p-5">
							<div class="flex items-center justify-between gap-4">
								<div class="text-sm font-medium">
									<Tooltip content={$i18n.t('Full Context Mode')} placement="top-start">
										{$i18n.t('Bypass Embedding and Retrieval')}
									</Tooltip>
								</div>
								<Tooltip
									content={RAGConfig.BYPASS_EMBEDDING_AND_RETRIEVAL
										? $i18n.t(
												'Inject the entire content as context for comprehensive processing, this is recommended for complex queries.'
											)
										: $i18n.t(
												'Default to segmented retrieval for focused and relevant content extraction, this is recommended for most cases.'
											)}
								>
									<Switch bind:state={RAGConfig.BYPASS_EMBEDDING_AND_RETRIEVAL} />
								</Tooltip>
							</div>
						</div>

						{#if !RAGConfig.BYPASS_EMBEDDING_AND_RETRIEVAL}
							<div class="glass-item p-5">
								<div class="mb-3 flex items-center justify-between gap-4">
									<div class="text-sm font-medium">{$i18n.t('Text Splitter')}</div>
									<HaloSelect
										bind:value={RAGConfig.TEXT_SPLITTER}
										options={[
											{
												value: '',
												label: `${$i18n.t('Default')} (${$i18n.t('Character')})`
											},
											{
												value: 'token',
												label: `${$i18n.t('Token')} (${$i18n.t('Tiktoken')})`
											},
											{ value: 'markdown', label: $i18n.t('Markdown Header') }
										]}
										className="w-fit"
									/>
								</div>

								<div class="grid grid-cols-1 gap-3 md:grid-cols-2">
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Chunk Size')}</div>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											type="number"
											placeholder={$i18n.t('Enter Chunk Size')}
											bind:value={RAGConfig.CHUNK_SIZE}
											autocomplete="off"
											min="0"
										/>
									</div>
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Chunk Overlap')}</div>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											type="number"
											placeholder={$i18n.t('Enter Chunk Overlap')}
											bind:value={RAGConfig.CHUNK_OVERLAP}
											autocomplete="off"
											min="0"
										/>
									</div>
									<div>
										<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Min Chunk Size')}</div>
										<input
											class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
											type="number"
											placeholder={$i18n.t('0 = disabled')}
											bind:value={RAGConfig.CHUNK_MIN_SIZE}
											autocomplete="off"
											min="0"
										/>
									</div>
								</div>
							</div>
						{/if}

						<div class="glass-item p-5">
							<div class="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">{$i18n.t('File Limits')}</div>
							<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
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
											placeholder={$i18n.t('Leave empty for unlimited')}
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
											placeholder={$i18n.t('Leave empty for unlimited')}
											bind:value={RAGConfig.FILE_MAX_COUNT}
											autocomplete="off"
											min="0"
										/>
									</Tooltip>
								</div>
							</div>
						</div>

						<div class="glass-item p-5">
							<div class="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">{$i18n.t('Cloud Storage')}</div>
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
						<div class="glass-item p-5">
							<div class="flex items-center justify-between gap-4">
								<div class="text-sm font-medium">{$i18n.t('Embedding Model Engine')}</div>
								<HaloSelect
									bind:value={embeddingEngine}
									placeholder={$i18n.t('Select an embedding model engine')}
									options={[
										{
											value: '',
											label: $i18n.t('Default (SentenceTransformers)'),
											disabled: !runtimeCapabilities.local_embedding_available
										},
										{ value: 'ollama', label: $i18n.t('Ollama') },
										{ value: 'openai', label: $i18n.t('OpenAI') }
									]}
									className="w-fit"
									on:change={(e) => {
										if (e.detail.value === 'ollama') {
											embeddingModel = '';
										} else if (e.detail.value === 'openai') {
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
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Base URL')}</div>
										<div class="w-full sm:w-1/2">
											<input
												class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
												placeholder={$i18n.t('API Base URL')}
												bind:value={OpenAIUrl}
												required
											/>
										</div>
									</div>
									<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Key')}</div>
										<div class="w-full sm:w-1/2">
											<SensitiveInput
												inputClassName="w-full text-sm"
												placeholder={$i18n.t('API Key')}
												bind:value={OpenAIKey}
											/>
										</div>
									</div>
								</div>
							{:else if embeddingEngine === 'ollama'}
								<div class="mt-3 space-y-3 border-t border-gray-100/60 pt-3 dark:border-gray-800/40">
									<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Base URL')}</div>
										<div class="w-full sm:w-1/2">
											<input
												class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
												placeholder={$i18n.t('API Base URL')}
												bind:value={OllamaUrl}
												required
											/>
										</div>
									</div>
									<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('API Key')}</div>
										<div class="w-full sm:w-1/2">
											<SensitiveInput
												inputClassName="w-full text-sm"
												placeholder={$i18n.t('API Key')}
												bind:value={OllamaKey}
												required={false}
											/>
										</div>
									</div>
								</div>
							{/if}
						</div>

						<div class="glass-item p-5">
							<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Embedding Model')}</div>
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
								{$i18n.t(
									'Warning: If you update or change your embedding model, you will need to re-import all documents.'
								)}
							</div>

							{#if embeddingEngine === 'ollama' || embeddingEngine === 'openai'}
								<div class="mt-3 flex flex-col gap-2 border-t border-gray-100/60 pt-3 dark:border-gray-800/40 sm:flex-row sm:items-center sm:justify-between">
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Embedding Batch Size')}</div>
									<input
										bind:value={embeddingBatchSize}
										type="number"
										class="glass-input w-24 px-3 py-2 text-right text-sm dark:text-gray-300"
										min="-2"
										max="16000"
										step="1"
									/>
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
						<div class="glass-item p-5">
							<div class="flex items-center justify-between gap-4">
								<div class="text-sm font-medium">{$i18n.t('Full Context Mode')}</div>
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
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('Hybrid Search')}</div>
										<Switch bind:state={RAGConfig.ENABLE_RAG_HYBRID_SEARCH} />
									</div>

									{#if RAGConfig.ENABLE_RAG_HYBRID_SEARCH === true}
										<div class="border-t border-gray-100/60 pt-4 dark:border-gray-800/40">
											<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
												{$i18n.t('Reranking Model')}
											</div>
											<div class="flex w-full gap-2">
												<input
													class="glass-input flex-1 px-3 py-2 text-sm dark:text-gray-300"
													placeholder={$i18n.t('Set reranking model (e.g. {{model}})', {
														model: 'BAAI/bge-reranker-v2-m3'
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
														{$i18n.t('Top K Reranker')}
													</div>
													<input
														class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
														type="number"
														placeholder={$i18n.t('Enter Top K Reranker')}
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
													<div class="text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('BM25 Weight')}</div>
													<div class="text-xs text-gray-400">
														{RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT ?? 0.5}
													</div>
												</div>
												<input
													class="w-full"
													type="range"
													step="0.05"
													min="0"
													max="1"
													bind:value={RAGConfig.RAG_HYBRID_SEARCH_BM25_WEIGHT}
												/>
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													{$i18n.t(
														'Balance between keyword (BM25) and semantic (vector) search. 0 = pure vector, 1 = pure keyword.'
													)}
												</div>
											</div>

											<div>
												<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">
													{$i18n.t('Relevance Threshold')}
												</div>
												<input
													class="glass-input w-full px-3 py-2 text-sm dark:text-gray-300"
													type="number"
													step="0.01"
													placeholder={$i18n.t('Enter Score')}
													bind:value={RAGConfig.RELEVANCE_THRESHOLD}
													autocomplete="off"
													min="0.0"
													title={$i18n.t(
														'The score should be a value between 0.0 (0%) and 1.0 (100%).'
													)}
												/>
												<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
													{$i18n.t(
														'Note: If you set a minimum score, the search will only return documents with a score greater than or equal to the minimum score.'
													)}
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
										{$i18n.t('RAG System Context')}
									</div>
									<Tooltip
										content={$i18n.t(
											'Static system instruction prepended to RAG context for KV cache reuse'
										)}
										placement="top-start"
										className="w-full"
									>
										<Textarea
											bind:value={RAGConfig.RAG_SYSTEM_CONTEXT}
											placeholder={$i18n.t(
												'Leave empty for no static prefix, or enter system instructions for RAG queries'
											)}
										/>
									</Tooltip>
								</div>

								<div class="border-t border-gray-100/60 pt-4 dark:border-gray-800/40">
									<div class="mb-1.5 text-xs font-medium text-gray-500 dark:text-gray-400">{$i18n.t('RAG Template')}</div>
									<Tooltip
										content={$i18n.t(
											'Leave empty to use the default prompt, or enter a custom prompt'
										)}
										placement="top-start"
										className="w-full"
									>
										<Textarea
											bind:value={RAGConfig.RAG_TEMPLATE}
											placeholder={$i18n.t(
												'Leave empty to use the default prompt, or enter a custom prompt'
											)}
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
