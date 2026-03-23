<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n') as Writable<i18nType>;

	import { verifyOpenAIConnection } from '$lib/apis/openai';
	import { verifyOllamaConnection } from '$lib/apis/ollama';
	import { verifyGeminiConnection } from '$lib/apis/gemini';
	import { verifyAnthropicConnection } from '$lib/apis/anthropic';
	import {
		inferModelCapabilities,
		getModelGroup,
		type ModelCapabilities
	} from '$lib/utils/model-capabilities';
	import { getModelChatDisplayName } from '$lib/utils/model-display';

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Eye from '$lib/components/icons/Eye.svelte';
	import LightBulb from '$lib/components/icons/LightBulb.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Gift from '$lib/components/icons/Gift.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import { formatConnectionErrorToast } from '$lib/utils/connection-errors';

	export let show = false;
	export let modelIds: string[] = [];
	export let url = '';
	export let force_mode = false;
	export let key = '';
	export let ollama = false;
	export let gemini = false;
	export let anthropic = false;
	export let auth_type: string | undefined = undefined;
	export let headers: Record<string, string> | undefined = undefined;
	export let anthropic_version: string | undefined = undefined;
	export let anthropic_beta: string[] | undefined = undefined;

	let searchQuery = '';
	let newModelId = '';
	let loading = false;
	let availableModels: Array<{ id: string; name?: string }> = [];
	let serverModelListRequiresManualEntry = false;

	const describeConnectionError = (error: unknown) => {
		const { title, description } = formatConnectionErrorToast(error, (key, options) =>
			$i18n.t(key, options)
		);

		return description ? `${title} ${description}` : title;
	};

	// 本地选中状态
	let selectedIds: Set<string> = new Set();

	// 分组展开状态
	let expandedGroups: Set<string> = new Set();

	// 标签筛选
	type FilterTag =
		| 'all'
		| 'reasoning'
		| 'vision'
		| 'webSearch'
		| 'tools'
		| 'free'
		| 'imageGen'
		| 'embedding'
		| 'rerank'
		| 'selected';
	let activeTag: FilterTag = 'all';
	const filterTags: { key: FilterTag; label: string }[] = [
		{ key: 'all', label: '全部' },
		{ key: 'selected', label: '已选' },
		{ key: 'reasoning', label: '推理' },
		{ key: 'vision', label: '视觉' },
		{ key: 'webSearch', label: '联网' },
		{ key: 'tools', label: '工具' },
		{ key: 'free', label: '免费' },
		{ key: 'imageGen', label: '生图' },
		{ key: 'embedding', label: '嵌入' },
		{ key: 'rerank', label: '重排' }
	];

	// 追踪模态框打开状态，避免 selectedIds 被意外重置
	let prevShow = false;
	$: {
		if (show && !prevShow) {
			// 模态框刚打开时初始化
			selectedIds = new Set(modelIds);
			if (url) fetchModels();
		}
		prevShow = show;
	}

	const fetchModels = async () => {
		if (!url) {
			toast.error($i18n.t('Please enter URL first'));
			return;
		}

		loading = true;
		serverModelListRequiresManualEntry = false;
		try {
			let data: any;

			if (ollama) {
				// Use backend proxy to avoid CORS issues
				data = await verifyOllamaConnection(localStorage.token, { url, key });
				availableModels = (data?.models || []).map((m: any) => ({
					id: m.name || m.model,
					name: m.name || m.model
				}));
			} else if (gemini) {
				// Use backend proxy to avoid CORS issues
				data = await verifyGeminiConnection(localStorage.token, {
					url,
					key,
					config: {
						...(auth_type ? { auth_type } : {}),
						...(headers ? { headers } : {})
					}
				});

				// Gemini native: { models: [{ name: "models/...", displayName: "..." }, ...] }
				if (!Array.isArray(data?.models)) {
					throw new Error('Gemini: Invalid response (expected models.list format)');
				}

				availableModels = (data.models || []).map((m: any) => ({
					id: m.name?.replace('models/', '') || m.name,
					name: m.displayName || m.name
				}));
			} else if (anthropic) {
				data = await verifyAnthropicConnection(localStorage.token, {
					url,
					key,
					config: {
						...(auth_type ? { auth_type } : {}),
						...(anthropic_version ? { anthropic_version } : {}),
						...(anthropic_beta && anthropic_beta.length ? { anthropic_beta } : {}),
						...(headers ? { headers } : {})
					}
				});

				if (!Array.isArray(data?.data)) {
					throw new Error('Anthropic: Invalid response (expected models.list format)');
				}

				availableModels = (data.data || []).map((m: any) => ({
					id: m.id,
					name: m.display_name || m.id
				}));
			} else {
				// Use backend proxy to avoid CORS issues
				data = await verifyOpenAIConnection(localStorage.token, {
					url,
					key,
					purpose: 'models',
					config: {
						force_mode,
						...(auth_type ? { auth_type } : {}),
						...(headers ? { headers } : {})
					}
				});
				availableModels = (data?.data || []).map((m: any) => ({
					id: m.id,
					name: m.name || m.id
				}));

				serverModelListRequiresManualEntry =
					data?._openwebui?.manual_model_ids_required === true;
			}

			if (serverModelListRequiresManualEntry) {
				toast.info(
					$i18n.t('This provider does not expose a model list. Add model IDs manually below.'),
					{
						duration: 6000
					}
				);
			} else {
				toast.success($i18n.t('Found {{count}} models', { count: availableModels.length }));
			}
		} catch (error) {
			toast.error($i18n.t('Failed to fetch models'), {
				description: describeConnectionError(error),
				duration: 6000
			});
			availableModels = [];
			serverModelListRequiresManualEntry = false;
		} finally {
			loading = false;
		}
	};

	const toggleModel = (id: string) => {
		const newSet = new Set(selectedIds);
		if (newSet.has(id)) {
			newSet.delete(id);
		} else {
			newSet.add(id);
		}
		selectedIds = newSet;
	};

	const addCustomModel = () => {
		if (newModelId.trim()) {
			const newSet = new Set(selectedIds);
			newSet.add(newModelId.trim());
			selectedIds = newSet;
			newModelId = '';
		}
	};

	const selectAll = () => {
		const newSet = new Set(selectedIds);
		filteredModels.forEach((m) => newSet.add(m.id));
		selectedIds = newSet;
	};

	const deselectAll = () => {
		const newSet = new Set(selectedIds);
		filteredModels.forEach((m) => newSet.delete(m.id));
		selectedIds = newSet;
	};

	const confirm = () => {
		modelIds = Array.from(selectedIds);
		show = false;
	};

	$: filteredModels = availableModels.filter((m) => {
		// 搜索过滤
		const matchesSearch =
			!searchQuery ||
			m.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
			m.name?.toLowerCase().includes(searchQuery.toLowerCase());
		if (!matchesSearch) return false;

		// 标签过滤
		if (activeTag === 'all') return true;
		if (activeTag === 'selected') return selectedIds.has(m.id);
		const caps = inferModelCapabilities(m.id);
		return caps[activeTag as keyof ModelCapabilities];
	});

	// 手动添加的模型（在selectedIds中但不在availableModels中）
	$: customModels = Array.from(selectedIds).filter(
		(id) => !availableModels.some((m) => m.id === id)
	);

	// 按分组组织模型
	$: groupedModels = (() => {
		const groups = new Map<string, Array<{ id: string; name?: string }>>();
		for (const model of filteredModels) {
			const group = getModelGroup(model.id);
			if (!groups.has(group)) {
				groups.set(group, []);
			}
			groups.get(group)!.push(model);
		}
		// 按分组内模型数量排序（多的在前）
		return Array.from(groups.entries()).sort((a, b) => b[1].length - a[1].length);
	})();

	const toggleGroup = (group: string) => {
		const newSet = new Set(expandedGroups);
		if (newSet.has(group)) {
			newSet.delete(group);
		} else {
			newSet.add(group);
		}
		expandedGroups = newSet;
	};

	const selectGroup = (models: Array<{ id: string }>) => {
		const newSet = new Set(selectedIds);
		models.forEach((m) => newSet.add(m.id));
		selectedIds = newSet;
	};
</script>

<Modal size="md" bind:show>
	<div class="px-5 pt-4 pb-5 w-full dark:text-gray-200">
		<div class="flex justify-between items-center mb-4">
			<h2 class="text-lg font-medium">{$i18n.t('Model Management')}</h2>
			<button on:click={() => (show = false)} aria-label={$i18n.t('Close')}>
				<XMark className="size-5" />
			</button>
		</div>

		<!-- 搜索框 -->
		<div class="relative mb-3">
			<input
				type="text"
				class="w-full px-4 py-2.5 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
				placeholder={$i18n.t('Search models...')}
				bind:value={searchQuery}
			/>
		</div>

		<!-- 标签筛选 -->
		<div class="flex flex-wrap gap-2 mb-4">
			{#each filterTags as tag}
				<button
					type="button"
					class="px-3 py-1 text-xs rounded-full transition {activeTag === tag.key
						? 'bg-black dark:bg-white text-white dark:text-black'
						: 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700'}"
					on:click={() => (activeTag = tag.key)}
				>
					{tag.label}
				</button>
			{/each}
		</div>

		<!-- 从服务器获取 -->
		<div class="border border-gray-200 dark:border-gray-800 rounded-xl mb-4">
			<div
				class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800"
			>
				<span class="text-sm font-medium">{$i18n.t('Available Models')}</span>
				<div class="flex items-center gap-2">
					{#if availableModels.length > 0}
						<button
							type="button"
							class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
							on:click={selectAll}
						>
							{$i18n.t('Select All')}
						</button>
						<span class="text-gray-300 dark:text-gray-600">|</span>
						<button
							type="button"
							class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
							on:click={deselectAll}
						>
							{$i18n.t('Deselect All')}
						</button>
					{/if}
					<Tooltip content={$i18n.t('Fetch models from server')}>
						<button
							type="button"
							class="p-1.5 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
							on:click={fetchModels}
							disabled={loading}
						>
							{#if loading}
								<Spinner className="size-4" />
							{:else}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-4"
								>
									<path
										fill-rule="evenodd"
										d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
										clip-rule="evenodd"
									/>
								</svg>
							{/if}
						</button>
					</Tooltip>
				</div>
			</div>

			<div class="max-h-64 overflow-y-auto">
				{#if groupedModels.length > 0}
					{#each groupedModels as [group, models]}
						{@const selectedCount = models.filter((m) => selectedIds.has(m.id)).length}
						<!-- 分组头部 -->
						<div class="flex items-center border-b border-gray-100 dark:border-gray-800">
							<button
								type="button"
								class="flex items-center gap-2 flex-1 px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-850 text-left"
								on:click={() => toggleGroup(group)}
							>
								<svg
									class="size-4 text-gray-400 transition-transform {expandedGroups.has(group)
										? 'rotate-90'
										: ''}"
									fill="none"
									viewBox="0 0 24 24"
									stroke="currentColor"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										stroke-width="2"
										d="M9 5l7 7-7 7"
									/>
								</svg>
								<span class="text-sm font-medium">{group}</span>
								{#if selectedCount > 0}
									<span class="text-xs text-blue-600 dark:text-blue-400"
										>{selectedCount}/{models.length}</span
									>
								{:else}
									<span class="text-xs text-gray-400">{models.length}</span>
								{/if}
							</button>
							<Tooltip content={$i18n.t('Add entire group')}>
								<button
									type="button"
									class="p-2 mr-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
									on:click={() => selectGroup(models)}
								>
									<Plus className="size-4" />
								</button>
							</Tooltip>
						</div>
						<!-- 分组内的模型 -->
						{#if expandedGroups.has(group)}
							{#each models as model}
								{@const caps = inferModelCapabilities(model.id)}
								<button
									type="button"
									class="flex items-center gap-3 pl-10 pr-4 py-2 hover:bg-gray-50 dark:hover:bg-gray-850 cursor-pointer w-full text-left"
									on:click={() => toggleModel(model.id)}
								>
									<input
										type="checkbox"
										class="w-4 h-4 rounded border-gray-300 dark:border-gray-600 text-black dark:text-white focus:ring-0 pointer-events-none"
										checked={selectedIds.has(model.id)}
										tabindex="-1"
									/>
									<span class="text-sm flex-1 truncate">{getModelChatDisplayName(model)}</span>
									<div class="flex items-center gap-1 shrink-0">
										{#if caps.free}
											<Tooltip content={$i18n.t('Free')}>
												<Gift className="size-3.5 text-green-500" />
											</Tooltip>
										{/if}
										{#if caps.imageGen}
											<Tooltip content={$i18n.t('Image Generation')}>
												<Photo className="size-3.5 text-purple-500" />
											</Tooltip>
										{/if}
										{#if caps.vision}
											<Tooltip content={$i18n.t('Vision')}>
												<Eye className="size-3.5 text-cyan-500" />
											</Tooltip>
										{/if}
										{#if caps.reasoning}
											<Tooltip content={$i18n.t('Reasoning')}>
												<LightBulb className="size-3.5 text-yellow-500" />
											</Tooltip>
										{/if}
										{#if caps.tools}
											<Tooltip content={$i18n.t('Tools')}>
												<Wrench className="size-3.5 text-orange-500" />
											</Tooltip>
										{/if}
										{#if caps.webSearch}
											<Tooltip content={$i18n.t('Web Search')}>
												<GlobeAlt className="size-3.5 text-blue-500" />
											</Tooltip>
										{/if}
									</div>
									{#if model.name && model.name !== model.id}
										<Tooltip content={model.id}>
											<span class="text-xs text-gray-400 truncate max-w-32">{model.id}</span>
										</Tooltip>
									{/if}
								</button>
							{/each}
						{/if}
					{/each}
				{:else if availableModels.length === 0}
					<div class="px-4 py-8 text-center text-sm text-gray-500">
						{#if serverModelListRequiresManualEntry}
							{$i18n.t('This provider does not expose a model list. Add model IDs manually below.')}
						{:else}
							{$i18n.t('Click refresh button to fetch models from server')}
						{/if}
					</div>
				{:else}
					<div class="px-4 py-8 text-center text-sm text-gray-500">
						{$i18n.t('No models match your search')}
					</div>
				{/if}
			</div>
		</div>

		<!-- 手动添加的模型 -->
		{#if customModels.length > 0}
			<div class="border border-gray-200 dark:border-gray-800 rounded-xl mb-4">
				<div class="px-4 py-3 border-b border-gray-200 dark:border-gray-800">
					<span class="text-sm font-medium">{$i18n.t('Custom Models')}</span>
				</div>
				<div class="max-h-32 overflow-y-auto">
					{#each customModels as modelId}
						{@const caps = inferModelCapabilities(modelId)}
						<button
							type="button"
							class="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-850 cursor-pointer w-full text-left"
							on:click={() => toggleModel(modelId)}
						>
							<input
								type="checkbox"
								class="w-4 h-4 rounded border-gray-300 dark:border-gray-600 text-black dark:text-white focus:ring-0 pointer-events-none"
								checked={selectedIds.has(modelId)}
								tabindex="-1"
							/>
							<span class="text-sm flex-1 truncate">{modelId}</span>
							<div class="flex items-center gap-1 shrink-0">
								{#if caps.free}
									<Tooltip content={$i18n.t('Free')}>
										<Gift className="size-3.5 text-green-500" />
									</Tooltip>
								{/if}
								{#if caps.imageGen}
									<Tooltip content={$i18n.t('Image Generation')}>
										<Photo className="size-3.5 text-purple-500" />
									</Tooltip>
								{/if}
								{#if caps.vision}
									<Tooltip content={$i18n.t('Vision')}>
										<Eye className="size-3.5 text-cyan-500" />
									</Tooltip>
								{/if}
								{#if caps.reasoning}
									<Tooltip content={$i18n.t('Reasoning')}>
										<LightBulb className="size-3.5 text-yellow-500" />
									</Tooltip>
								{/if}
								{#if caps.tools}
									<Tooltip content={$i18n.t('Tools')}>
										<Wrench className="size-3.5 text-orange-500" />
									</Tooltip>
								{/if}
								{#if caps.webSearch}
									<Tooltip content={$i18n.t('Web Search')}>
										<GlobeAlt className="size-3.5 text-blue-500" />
									</Tooltip>
								{/if}
							</div>
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<!-- 手动添加 -->
		<div class="border border-gray-200 dark:border-gray-800 rounded-xl mb-4">
			<div class="px-4 py-3 border-b border-gray-200 dark:border-gray-800">
				<span class="text-sm font-medium">{$i18n.t('Add Custom Model')}</span>
			</div>
			<div class="flex items-center gap-2 px-4 py-3">
				<input
					type="text"
					class="flex-1 px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg focus:outline-none"
					placeholder={$i18n.t('Enter model ID')}
					bind:value={newModelId}
					on:keydown={(e) => e.key === 'Enter' && addCustomModel()}
				/>
				<button
					type="button"
					class="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition"
					on:click={addCustomModel}
				>
					<Plus className="size-5" />
				</button>
			</div>
		</div>

		<!-- 底部 -->
		<div class="flex items-center justify-between pt-2">
			<span class="text-sm text-gray-500">
				{$i18n.t('Selected: {{count}} models', { count: selectedIds.size })}
			</span>
			<div class="flex gap-2">
				<button
					type="button"
					class="px-4 py-2 text-sm font-medium bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-full transition"
					on:click={() => (show = false)}
				>
					{$i18n.t('Cancel')}
				</button>
				<button
					type="button"
					class="px-4 py-2 text-sm font-medium bg-black dark:bg-white text-white dark:text-black hover:bg-gray-800 dark:hover:bg-gray-100 rounded-full transition"
					on:click={confirm}
				>
					{$i18n.t('Confirm')}
				</button>
			</div>
		</div>
	</div>
</Modal>
