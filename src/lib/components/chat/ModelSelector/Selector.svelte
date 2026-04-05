<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import Fuse from 'fuse.js';

	import { flyAndScale } from '$lib/utils/transitions';
	import { createEventDispatcher, getContext, tick } from 'svelte';
	import { Pin, Share2, SlidersHorizontal, Star } from 'lucide-svelte';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Search from '$lib/components/icons/Search.svelte';

	import { deleteModel, getOllamaVersion, pullModel } from '$lib/apis/ollama';

	import {
		user,
		MODEL_DOWNLOAD_POOL,
		models,
		mobile,
		temporaryChatEnabled,
		settings,
		config
	} from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import { capitalizeFirstLetter, sanitizeResponseContent, splitStream } from '$lib/utils';
	import { localizeCommonError } from '$lib/utils/common-errors';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import {
		getTemporaryChatAccess,
		getTemporaryChatNavigationPath,
		persistTemporaryChatOverride
	} from '$lib/utils/temporary-chat';
	import { ensureModels, refreshModels } from '$lib/services/models';
	import { updateUserSettings } from '$lib/apis/users';

	import ModelIcon from '$lib/components/common/ModelIcon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
	import { goto } from '$app/navigation';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	const formatError = (error: unknown) =>
		localizeCommonError(error, (key, options) => $i18n.t(key, options));

	const showError = (error: unknown) => toast.error(formatError(error));

	export let id = '';
	export let value = '';
	export let placeholder = 'Select a model';
	export let searchEnabled = true;
	export let searchPlaceholder = $i18n.t('Search a model');

	export let showTemporaryChatControl = false;
	export let showSetDefaultAction = false;

	export let items: {
		label: string;
		value: string;
		model: Model;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		[key: string]: any;
	}[] = [];

	export let className = 'w-[32rem]';
	export let triggerClassName = 'text-base';

	let tagsContainerElement;

	let show = false;
	let tags = [];

	// Progressive rendering: render a small batch first so the dropdown appears instantly,
	// then expand to the full list after the frame paints.
	const INITIAL_RENDER_LIMIT = 8;
	let renderLimit = INITIAL_RENDER_LIMIT;

	let selectedModel = '';
	$: selectedModel = items.find((item) => item.value === value) ?? '';

	let searchValue = '';

	let selectedTag = '';
	let selectedConnectionType = '';

	let pinnedModels: string[] = [];
	let pinnedModelSet: Set<string> = new Set();
	let userDefaultModelId = '';
	let defaultModelId = '';
	let defaultModelItem: (typeof items)[number] | null = null;

	// Filter out stale pins: models that were deleted or hidden should not stay pinned
	$: {
		const availableIds = new Set(
			items.filter((item) => !(item.model?.info?.meta?.hidden ?? false)).map((item) => item.value)
		);
		pinnedModels = (($settings?.pinnedModels ?? []) as string[]).filter((id) =>
			availableIds.has(id)
		);
		pinnedModelSet = new Set(pinnedModels);
	}

	const togglePinModel = async (modelId: string) => {
		const current = $settings?.pinnedModels ?? [];
		const updated = current.includes(modelId)
			? current.filter((id: string) => id !== modelId)
			: [...current, modelId];
		settings.set({ ...$settings, pinnedModels: updated });
		await updateUserSettings(localStorage.token, { ui: { ...$settings, pinnedModels: updated } });
	};

	$: userDefaultModelId =
		($settings?.models ?? []).find((id) => typeof id === 'string' && id.trim() !== '') ?? '';

	$: defaultModelId = userDefaultModelId;

	$: defaultModelItem = items.find((item) => item.value === defaultModelId) ?? null;

	const setDefaultModel = async (modelId: string) => {
		value = modelId;
		const nextSettings = { ...$settings, models: [modelId] };
		settings.set(nextSettings);
		await updateUserSettings(localStorage.token, { ui: nextSettings });
		toast.success($i18n.t('Default model updated'));
		show = false;
	};

	const clearDefaultModel = async () => {
		const currentSettings = $settings ?? {};
		const { models: _models, ...nextSettings } = currentSettings;
		settings.set(nextSettings);
		await updateUserSettings(localStorage.token, { ui: nextSettings });
		toast.success($i18n.t('Default model cleared'));
		show = false;
	};

	const applyTemporaryChatMode = async (enabled: boolean) => {
		const defaultEnabled = $settings?.temporaryChatByDefault ?? false;
		const { allowed, enforced } = getTemporaryChatAccess($user);
		const nextEnabled = allowed ? (enforced ? true : enabled) : false;

		persistTemporaryChatOverride(nextEnabled, { defaultEnabled, enforced, allowed });
		temporaryChatEnabled.set(nextEnabled);

		const targetPath = getTemporaryChatNavigationPath({
			currentUrl: new URL(window.location.href),
			enabled: nextEnabled,
			defaultEnabled,
			enforced,
			allowed,
			pathname: '/'
		});

		await goto(targetPath);
		await tick();
		(
			document.getElementById('new-chat-button') ??
			document.getElementById('sidebar-new-chat-button')
		)?.click();
		show = false;
	};

	let ollamaVersion = null;
	let selectedModelIdx = 0;

	// Lazy Fuse: only build index when search is actually used
	let fuse: Fuse<any> | null = null;
	let fuseItemsRef: typeof items | null = null;

	function getFuse() {
		if (fuse && fuseItemsRef === items) return fuse;
		const indexed = items.map((item) => ({
			...item,
			modelName: getModelChatDisplayName(item.model),
			tags: (item.model?.tags ?? []).map((tag) => tag.name).join(' '),
			desc: item.model?.info?.meta?.description
		}));
		fuse = new Fuse(indexed, {
			keys: ['value', 'tags', 'modelName'],
			threshold: 0.4
		});
		fuseItemsRef = items;
		return fuse;
	}

	// Lazy description cache: only parse on hover, dynamic import marked
	let descriptionCache = new Map<string, string>();

	function getDescriptionHtml(raw: string): string {
		if (descriptionCache.has(raw)) return descriptionCache.get(raw)!;
		// Synchronous fallback: return sanitized plain text immediately
		const plain = sanitizeResponseContent(raw).replaceAll('\n', ' ');
		// Kick off async parse for next hover
		import('marked').then(({ marked }) => {
			descriptionCache.set(
				raw,
				marked.parse(sanitizeResponseContent(raw).replaceAll('\n', '<br>')) as string
			);
		});
		return plain;
	}

	$: filteredItems = (
		searchValue
			? getFuse()
					.search(searchValue)
					.map((e) => e.item)
			: items
	)
		.filter((item) => {
			const matchesTag =
				selectedTag === '' || (item.model?.tags ?? []).some((tag) => tag.name === selectedTag);
			const matchesConnection =
				selectedConnectionType === '' || item.model?.owned_by === selectedConnectionType;

			return matchesTag && matchesConnection;
		})
		.filter((item) => !(item.model?.info?.meta?.hidden ?? false))
		.sort((a, b) => {
			if (!searchValue) {
				const aPin = pinnedModelSet.has(a.value) ? 0 : 1;
				const bPin = pinnedModelSet.has(b.value) ? 0 : 1;
				return aPin - bPin;
			}
			return 0;
		});

	// Items actually rendered in DOM — capped by renderLimit for progressive rendering
	// When searching, always show all results (search results are small)
	$: visibleItems = searchValue || renderLimit >= filteredItems.length
		? filteredItems
		: filteredItems.slice(0, renderLimit);

	$: if (selectedTag !== '' || selectedConnectionType !== '') {
		resetView();
	}

	const resetView = async () => {
		await tick();

		const selectedInFiltered = filteredItems.findIndex((item) => item.value === value);

		if (selectedInFiltered >= 0) {
			// The selected model is visible in the current filter
			selectedModelIdx = selectedInFiltered;
		} else {
			// The selected model is not visible, default to first item in filtered list
			selectedModelIdx = 0;
		}

		await tick();
		const item = document.querySelector(`[data-arrow-selected="true"]`);
		item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
	};

	const pullModelHandler = async () => {
		const sanitizedModelTag = searchValue.trim().replace(/^ollama\s+(run|pull)\s+/, '');

		console.log($MODEL_DOWNLOAD_POOL);
		if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag]) {
			toast.error(
				$i18n.t(`Model '{{modelTag}}' is already in queue for downloading.`, {
					modelTag: sanitizedModelTag
				})
			);
			return;
		}
		if (Object.keys($MODEL_DOWNLOAD_POOL).length === 3) {
			toast.error(
				$i18n.t('Maximum of 3 models can be downloaded simultaneously. Please try again later.')
			);
			return;
		}

		const pullResult = await pullModel(localStorage.token, sanitizedModelTag, '0').catch(
			(error) => {
				showError(error);
				return null;
			}
		);
		if (!pullResult) {
			return;
		}
		const [res, controller] = pullResult;

		if (res) {
			const reader = res.body
				.pipeThrough(new TextDecoderStream())
				.pipeThrough(splitStream('\n'))
				.getReader();

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL,
				[sanitizedModelTag]: {
					...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
					abortController: controller,
					reader,
					done: false
				}
			});

			while (true) {
				try {
					const { value, done } = await reader.read();
					if (done) break;

					let lines = value.split('\n');

					for (const line of lines) {
						if (line !== '') {
							let data = JSON.parse(line);
							console.log(data);
							if (data.error) {
								throw data.error;
							}
							if (data.detail) {
								throw data.detail;
							}

							if (data.status) {
								if (data.digest) {
									let downloadProgress = 0;
									if (data.completed) {
										downloadProgress = Math.round((data.completed / data.total) * 1000) / 10;
									} else {
										downloadProgress = 100;
									}

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											pullProgress: downloadProgress,
											digest: data.digest
										}
									});
								} else {
									toast.success(data.status);

									MODEL_DOWNLOAD_POOL.set({
										...$MODEL_DOWNLOAD_POOL,
										[sanitizedModelTag]: {
											...$MODEL_DOWNLOAD_POOL[sanitizedModelTag],
											done: data.status === 'success'
										}
									});
								}
							}
						}
					}
				} catch (error) {
					console.log(error);
					if (typeof error !== 'string') {
						error = error.message;
					}

					showError(error);
					// opts.callback({ success: false, error, modelName: opts.modelName });
					break;
				}
			}

			if ($MODEL_DOWNLOAD_POOL[sanitizedModelTag].done) {
				toast.success(
					$i18n.t(`Model '{{modelName}}' has been successfully downloaded.`, {
						modelName: sanitizedModelTag
					})
				);

				await refreshModels(localStorage.token, { force: true, reason: 'ollama-download' });
			} else {
				toast.error($i18n.t('Download canceled'));
			}

			delete $MODEL_DOWNLOAD_POOL[sanitizedModelTag];

			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
		}
	};

	// Lazy: only fetch ollamaVersion when user actually searches (for "pull from ollama" feature)
	let ollamaVersionFetched = false;
	const ensureOllamaVersion = () => {
		if (ollamaVersionFetched) return;
		ollamaVersionFetched = true;
		getOllamaVersion(localStorage.token)
			.then((v) => (ollamaVersion = v))
			.catch(() => (ollamaVersion = false));
	};

	// Combined reactive: compute tags, hasVisibleItems, hasMixedSources in one pass
	let hasVisibleItems = false;
	let hasMixedSources = false;
	$: {
		let _hasVisible = false;
		let _hasOllama = false;
		let _hasOpenai = false;
		const tagSet = new Set<string>();
		for (const item of items) {
			const hidden = item.model?.info?.meta?.hidden ?? false;
			if (!hidden) {
				_hasVisible = true;
				for (const tag of item.model?.tags ?? []) tagSet.add(tag.name);
			}
			if (item.model?.owned_by === 'ollama') _hasOllama = true;
			if (item.model?.owned_by === 'openai') _hasOpenai = true;
		}
		hasVisibleItems = _hasVisible;
		hasMixedSources = _hasOllama && _hasOpenai;
		tags = Array.from(tagSet).sort((a, b) => a.localeCompare(b));
	}

	const cancelModelPullHandler = async (model: string) => {
		const { reader, abortController } = $MODEL_DOWNLOAD_POOL[model];
		if (abortController) {
			abortController.abort();
		}
		if (reader) {
			await reader.cancel();
			delete $MODEL_DOWNLOAD_POOL[model];
			MODEL_DOWNLOAD_POOL.set({
				...$MODEL_DOWNLOAD_POOL
			});
			await deleteModel(localStorage.token, model);
			toast.success(`${model} download has been canceled`);
		}
	};

	const getAdminEditHref = (item) => {
		const modelId = encodeURIComponent(item.value);
		const baseModelId = item.model?.info?.base_model_id;

		return baseModelId != null
			? `/workspace/models/edit?id=${modelId}`
			: `/settings/models?id=${modelId}`;
	};
</script>

<DropdownMenu.Root
	bind:open={show}
	onOpenChange={async () => {
		searchValue = '';
		renderLimit = INITIAL_RENDER_LIMIT;
		window.setTimeout(() => document.getElementById('model-search-input')?.focus(), 0);

		// After the dropdown paints with the initial batch, render the rest then scroll
		requestAnimationFrame(() => {
			renderLimit = Infinity;
			resetView();
		});

		if (show && $models.length === 0) {
			void ensureModels(localStorage.token, { reason: 'model-selector' }).catch((error) => {
				const msg = error instanceof Error ? error.message : `${error}`;
				toast.error(msg);
			});
		}
	}}
	closeFocus={false}
>
	<DropdownMenu.Trigger
		class="relative font-primary inline-flex items-center gap-1.5 max-w-full
			px-3 py-1.5 rounded-xl
			bg-white dark:bg-gray-900
			border border-gray-200/50 dark:border-gray-700/30
			hover:border-gray-300/70 dark:hover:border-gray-600/50
			hover:bg-gray-50 dark:hover:bg-gray-800
			active:bg-gray-100 dark:active:bg-gray-750
			transition-colors duration-50
			shadow-none outline-hidden ring-0 focus-visible:ring-0 focus-visible:outline-hidden {triggerClassName}"
		aria-label={placeholder}
		id="model-selector-{id}-button"
	>
		<span class="truncate font-medium text-gray-700 dark:text-gray-200">
			{#if selectedModel}
				{selectedModel.label}
			{:else}
				<span class="text-gray-400 dark:text-gray-500">{placeholder}</span>
			{/if}
		</span>
		<span class="shrink-0 ml-0.5 text-gray-400 dark:text-gray-500 transition-colors duration-200 hover:text-gray-600 dark:hover:text-gray-300">
			<ChevronDown className="size-3.5" strokeWidth="2.5" />
		</span>
	</DropdownMenu.Trigger>

	<DropdownMenu.Content
		class=" z-40 {$mobile
			? `w-full`
			: `${className}`} max-w-[calc(100vw-1rem)] justify-start rounded-xl  bg-white dark:bg-gray-850 dark:text-white shadow-lg  outline-hidden"
		transition={(node) => flyAndScale(node, { duration: 80 })}
		side={$mobile ? 'bottom' : 'bottom-start'}
		sideOffset={3}
	>
		<slot>
			{#if searchEnabled}
				<div class="flex items-center gap-2.5 px-5 mt-3.5 mb-1.5">
					<Search className="size-4" strokeWidth="2.5" />

					<input
						id="model-search-input"
						bind:value={searchValue}
						class="w-full text-sm bg-transparent outline-hidden"
						placeholder={searchPlaceholder}
						autocomplete="off"
						on:input={() => ensureOllamaVersion()}
						on:keydown={(e) => {
							if (e.code === 'Enter' && visibleItems.length > 0 && selectedModelIdx >= 0) {
								value = visibleItems[selectedModelIdx].value;
								show = false;
								return; // dont need to scroll on selection
							} else if (e.code === 'ArrowDown') {
								selectedModelIdx = Math.min(selectedModelIdx + 1, visibleItems.length - 1);
							} else if (e.code === 'ArrowUp') {
								selectedModelIdx = Math.max(selectedModelIdx - 1, 0);
							} else {
								// if the user types something, reset to the top selection.
								selectedModelIdx = 0;
							}

							const item = document.querySelector(`[data-arrow-selected="true"]`);
							item?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
						}}
					/>
				</div>
			{/if}

			{#if showSetDefaultAction}
				<div class="px-5 pb-1.5">
					<div
						class="inline-flex max-w-full items-center gap-1.5 rounded-md border border-gray-100/80 bg-gray-50/70 px-2 py-1 text-[11px] text-gray-500 dark:border-gray-800/80 dark:bg-gray-900/40 dark:text-gray-400"
					>
						<Star
							class="size-3.5 shrink-0"
							strokeWidth={2.05}
							color={defaultModelId ? '#f59e0b' : 'currentColor'}
							fill={defaultModelId ? '#f59e0b' : 'none'}
						/>
						<span class="shrink-0">{$i18n.t('Default Model')}:</span>
						<span class="truncate text-gray-700 dark:text-gray-300">
							{(defaultModelItem?.label ?? defaultModelId) || $i18n.t('None')}
						</span>
					</div>
				</div>
			{/if}

			<div class="px-3 max-h-64 overflow-y-auto scrollbar-hidden relative">
				{#if tags && hasVisibleItems}
					<div
						class=" flex w-full sticky top-0 z-10 bg-white dark:bg-gray-850 overflow-x-auto scrollbar-none"
						on:wheel={(e) => {
							if (e.deltaY !== 0) {
								e.preventDefault();
								e.currentTarget.scrollLeft += e.deltaY;
							}
						}}
					>
						<div
							class="flex gap-1 w-fit text-center text-sm font-medium rounded-full bg-transparent px-1.5 pb-0.5"
							bind:this={tagsContainerElement}
						>
							{#if hasMixedSources || tags.length > 0}
								<button
									type="button"
									class="min-w-fit outline-none p-1.5 {selectedTag === '' &&
									selectedConnectionType === ''
										? ''
										: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} capitalize"
									on:click={() => {
										selectedConnectionType = '';
										selectedTag = '';
									}}
								>
									{$i18n.t('All')}
								</button>
							{/if}

							{#if hasMixedSources}
								<button
									type="button"
									class="min-w-fit outline-none p-1.5 {selectedConnectionType === 'ollama'
										? ''
										: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} capitalize"
									on:click={() => {
										selectedTag = '';
										selectedConnectionType = 'ollama';
									}}
								>
									{$i18n.t('Local')}
								</button>
								<button
									type="button"
									class="min-w-fit outline-none p-1.5 {selectedConnectionType === 'openai'
										? ''
										: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} capitalize"
									on:click={() => {
										selectedTag = '';
										selectedConnectionType = 'openai';
									}}
								>
									{$i18n.t('External')}
								</button>
							{/if}

							{#each tags as tag}
								<button
									type="button"
									class="min-w-fit outline-none p-1.5 {selectedTag === tag
										? ''
										: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} capitalize"
									on:click={() => {
										selectedConnectionType = '';
										selectedTag = tag;
									}}
								>
									{tag}
								</button>
							{/each}
						</div>
					</div>
				{/if}

				{#each visibleItems as item, index}
					{@const isSharedModel = Boolean(
						item.model?.info?.user_id && item.model.info.user_id !== $user?.id
					)}
					{#if !searchValue && index > 0 && pinnedModelSet.has(visibleItems[index - 1]?.value) && !pinnedModelSet.has(item.value)}
						<div class="border-b border-gray-100 dark:border-gray-800 my-1"></div>
					{/if}
					<button
						aria-label="model-item"
						class="group/item flex w-full text-left font-medium overflow-hidden select-none items-center rounded-button py-2 pl-3 pr-1.5 text-sm text-gray-700 dark:text-gray-100 outline-hidden rounded-lg cursor-pointer data-highlighted:bg-muted {index ===
						selectedModelIdx
							? 'bg-gray-100 dark:bg-gray-800'
							: ''}"
					style="content-visibility:auto;contain-intrinsic-size:auto 52px"
						data-arrow-selected={index === selectedModelIdx}
						data-value={item.value}
						on:pointerenter={() => {
							selectedModelIdx = index;
						}}
						on:click={() => {
							value = item.value;
							selectedModelIdx = index;

							show = false;
						}}
					>
						<div class="flex flex-col flex-1 min-w-0">
							<div
								class="flex min-w-0 items-start gap-2"
								title={$user?.role === 'admin' ? (item?.value ?? '') : item.label}
							>
								<ModelIcon
									src={item.model?.info?.meta?.profile_image_url ??
										item.model?.meta?.profile_image_url ??
										'/static/favicon.png'}
									alt="Model"
									className="rounded-lg size-5 mt-0.5 shrink-0"
									loading={index < 8 ? 'eager' : 'lazy'}
								/>

								<div class="min-w-0 flex-1">
									<div class="flex min-w-0 items-center gap-1.5">
										<div class="min-w-0 flex flex-1 items-center gap-1">
											<div class="min-w-0 truncate">
												{item.label}
											</div>

											{#if item.model.owned_by === 'ollama' && (item.model.ollama?.details?.parameter_size ?? '') !== ''}
												<div class="ml-1 shrink-0 items-center translate-y-[0.5px]">
													<span
														class="text-xs font-medium text-gray-600 dark:text-gray-400"
														title={`${
															item.model.ollama?.details?.quantization_level
																? item.model.ollama?.details?.quantization_level + ' '
																: ''
														}${
															item.model.ollama?.size
																? `(${(item.model.ollama?.size / 1024 ** 3).toFixed(1)}GB)`
																: ''
														}`}
													>
														{item.model.ollama?.details?.parameter_size ?? ''}
													</span>
												</div>
											{/if}
										</div>

										{#if item.model?.info?.meta?.description}
											<Tooltip
												content={getDescriptionHtml(item.model?.info?.meta?.description)}
											>
												<div class="shrink-0 translate-y-[1px]">
													<svg
														xmlns="http://www.w3.org/2000/svg"
														fill="none"
														viewBox="0 0 24 24"
														stroke-width="1.5"
														stroke="currentColor"
														class="size-3.5 text-gray-400 dark:text-gray-500"
													>
														<path
															stroke-linecap="round"
															stroke-linejoin="round"
															d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"
														/>
													</svg>
												</div>
											</Tooltip>
										{/if}
									</div>

									{#if isSharedModel}
										<div class="mt-1 flex flex-wrap items-center gap-1.5">
											<span
												class="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:bg-sky-900/25 dark:text-sky-300 whitespace-nowrap"
												title={$i18n.t('Shared model')}
											>
												<Share2 class="size-3 shrink-0" strokeWidth={2.1} />
												<span>{$i18n.t('Shared')}</span>
											</span>

											{#if $user?.role !== 'admin'}
												<span
													class="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-gray-700/50 dark:text-gray-300 whitespace-nowrap"
												>
													{$i18n.t('Read Only')}
												</span>
											{/if}
										</div>
									{/if}
								</div>
							</div>
						</div>

						<div class="flex items-center gap-0.5 shrink-0 self-center pl-3">
							{#if showSetDefaultAction}
								<!-- svelte-ignore a11y-click-events-have-key-events -->
								<div
									role="button"
									tabindex="-1"
									title={userDefaultModelId === item.value
										? $i18n.t('Clear Default Model')
										: $i18n.t('Set as default')}
									class="p-1.5 rounded-lg {defaultModelId === item.value
										? 'text-amber-500 dark:text-amber-400 bg-amber-50/90 dark:bg-amber-900/20'
										: 'opacity-0 group-hover/item:opacity-100 text-gray-400 hover:text-amber-500 dark:hover:text-amber-300 hover:bg-amber-50/80 dark:hover:bg-amber-900/15'} cursor-pointer transition-colors duration-150"
									on:click|stopPropagation={async () => {
										selectedModelIdx = index;
										if (userDefaultModelId === item.value) {
											await clearDefaultModel();
											return;
										}

										await setDefaultModel(item.value);
									}}
								>
									<Star
										class="size-3.5"
										strokeWidth={2.05}
										fill={defaultModelId === item.value ? 'currentColor' : 'none'}
									/>
								</div>
							{/if}

							<!-- svelte-ignore a11y-click-events-have-key-events -->
							<div
								role="button"
								tabindex="-1"
								title={pinnedModelSet.has(item.value)
									? $i18n.t('取消置顶')
									: $i18n.t('置顶模型')}
								class="p-1.5 rounded-lg {pinnedModelSet.has(item.value)
									? 'text-blue-500 dark:text-blue-400 bg-blue-50/90 dark:bg-blue-900/20'
									: 'opacity-0 group-hover/item:opacity-100 text-gray-400 hover:text-blue-500 dark:hover:text-blue-300 hover:bg-blue-50/70 dark:hover:bg-blue-900/15'} cursor-pointer transition-colors duration-150"
								on:click|stopPropagation={() => {
									togglePinModel(item.value);
								}}
							>
								<Pin
									class="size-3.5 transition-transform duration-150"
									strokeWidth={2.1}
									style={`transform: rotate(${pinnedModelSet.has(item.value) ? 40 : 0}deg); transform-origin: center;`}
									fill={pinnedModelSet.has(item.value) ? 'currentColor' : 'none'}
								/>
							</div>

							{#if $user?.role === 'admin' && item.model?.info}
								<a
									href={getAdminEditHref(item)}
									title={$i18n.t('编辑模型')}
									class="p-1.5 rounded-lg opacity-0 group-hover/item:opacity-100 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
									on:click|stopPropagation={() => {
										show = false;
									}}
								>
									<SlidersHorizontal class="size-3.5" strokeWidth={2.1} />
								</a>
							{/if}
						</div>
					</button>
				{:else}
					<div class="">
						<div class="block px-3 py-2 text-sm text-gray-700 dark:text-gray-100">
							{$i18n.t('No results found')}
						</div>
					</div>
				{/each}
				{#if !(searchValue.trim() in $MODEL_DOWNLOAD_POOL) && searchValue && ollamaVersion && $user?.role === 'admin'}
					<Tooltip
						content={$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, {
							searchValue: searchValue
						})}
						placement="top-start"
					>
						<button
							class="flex w-full font-medium line-clamp-1 select-none items-center rounded-button py-2 pl-3 pr-1.5 text-sm text-gray-700 dark:text-gray-100 outline-hidden hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg cursor-pointer data-highlighted:bg-muted"
							on:click={() => {
								pullModelHandler();
							}}
						>
							<div class=" truncate">
								{$i18n.t(`Pull "{{searchValue}}" from Ollama.com`, { searchValue: searchValue })}
							</div>
						</button>
					</Tooltip>
				{/if}

				{#each Object.keys($MODEL_DOWNLOAD_POOL) as model}
					<div
						class="flex w-full justify-between font-medium select-none rounded-button py-2 pl-3 pr-1.5 text-sm text-gray-700 dark:text-gray-100 outline-hidden rounded-lg cursor-pointer data-highlighted:bg-muted"
					>
						<div class="flex">
							<div class="-ml-2 mr-2.5 translate-y-0.5">
								<svg
									class="size-4"
									viewBox="0 0 24 24"
									fill="currentColor"
									xmlns="http://www.w3.org/2000/svg"
									><style>
										.spinner_ajPY {
											transform-origin: center;
											animation: spinner_AtaB 0.75s infinite linear;
										}
										@keyframes spinner_AtaB {
											100% {
												transform: rotate(360deg);
											}
										}
									</style><path
										d="M12,1A11,11,0,1,0,23,12,11,11,0,0,0,12,1Zm0,19a8,8,0,1,1,8-8A8,8,0,0,1,12,20Z"
										opacity=".25"
									/><path
										d="M10.14,1.16a11,11,0,0,0-9,8.92A1.59,1.59,0,0,0,2.46,12,1.52,1.52,0,0,0,4.11,10.7a8,8,0,0,1,6.66-6.61A1.42,1.42,0,0,0,12,2.69h0A1.57,1.57,0,0,0,10.14,1.16Z"
										class="spinner_ajPY"
									/></svg
								>
							</div>

							<div class="flex flex-col self-start">
								<div class="flex gap-1">
									<div class="line-clamp-1">
										Downloading "{model}"
									</div>

									<div class="shrink-0">
										{'pullProgress' in $MODEL_DOWNLOAD_POOL[model]
											? `(${$MODEL_DOWNLOAD_POOL[model].pullProgress}%)`
											: ''}
									</div>
								</div>

								{#if 'digest' in $MODEL_DOWNLOAD_POOL[model] && $MODEL_DOWNLOAD_POOL[model].digest}
									<div class="-mt-1 h-fit text-[0.7rem] dark:text-gray-500 line-clamp-1">
										{$MODEL_DOWNLOAD_POOL[model].digest}
									</div>
								{/if}
							</div>
						</div>

						<div class="mr-2 ml-1 translate-y-0.5">
							<Tooltip content={$i18n.t('Cancel')}>
								<button
									class="text-gray-800 dark:text-gray-100"
									on:click={() => {
										cancelModelPullHandler(model);
									}}
								>
									<svg
										class="w-4 h-4 text-gray-800 dark:text-white"
										aria-hidden="true"
										xmlns="http://www.w3.org/2000/svg"
										width="24"
										height="24"
										fill="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke="currentColor"
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M6 18 17.94 6M18 18 6.06 6"
										/>
									</svg>
								</button>
							</Tooltip>
						</div>
					</div>
				{/each}
			</div>

			{#if showTemporaryChatControl}
				<div
					class="flex items-center mx-2 mb-2"
					on:pointerenter={() => {
						selectedModelIdx = -1;
					}}
				>
					<div
						class="flex justify-between w-full font-medium line-clamp-1 select-none items-center rounded-button py-2 px-3 text-sm text-gray-700 dark:text-gray-100 outline-hidden hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg cursor-pointer data-highlighted:bg-muted"
						role="button"
						tabindex="0"
						on:click={async () => {
							await applyTemporaryChatMode(!$temporaryChatEnabled);
						}}
						on:keydown={async (event) => {
							if (event.key === 'Enter' || event.key === ' ') {
								event.preventDefault();
								await applyTemporaryChatMode(!$temporaryChatEnabled);
							}
						}}
					>
						<div class="flex gap-2.5 items-center">
							<ChatBubbleOval className="size-4" strokeWidth="2.5" />

							{$i18n.t(`Temporary Chat`)}
						</div>

						<div on:click|stopPropagation={() => {}}>
							<Switch
								state={$temporaryChatEnabled}
								on:change={async (event) => {
									await applyTemporaryChatMode(event.detail);
								}}
							/>
						</div>
					</div>
				</div>
			{:else if filteredItems.length === 0}
				<div class="mb-3"></div>
			{/if}

			<div class="hidden w-[42rem]" />
			<div class="hidden w-[32rem]" />
		</slot>
	</DropdownMenu.Content>
</DropdownMenu.Root>
