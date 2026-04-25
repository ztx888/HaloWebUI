<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getImageGenerationModels,
		getImageUsageConfig,
		imageGenerations
	} from '$lib/apis/images';
	import type { ImageGenerationModel, ImageUsageConfig } from '$lib/apis/images';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import ImagePreview from '$lib/components/common/ImagePreview.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import ArrowsPointingOut from '$lib/components/icons/ArrowsPointingOut.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import PhotoSolid from '$lib/components/icons/PhotoSolid.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import { WEBUI_NAME, user } from '$lib/stores';
	import { copyToClipboard } from '$lib/utils';
	import { localizeCommonError } from '$lib/utils/common-errors';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import {
		GROK_IMAGE_ASPECT_RATIO_OPTIONS,
		GROK_IMAGE_RESOLUTION_OPTIONS
	} from '$lib/utils/image-generation';
	import {
		CUSTOM_SIZE_OPTION_VALUE,
		WORKSPACE_IMAGE_SIZE_PRESETS,
		extractImageConstraintFromError,
		formatPixelCount,
		getRecommendedImageSizes,
		parseImageSize,
		type LearnedImageConstraint
	} from '$lib/utils/workspace-image-generation';

	type GeneratedImage = {
		url: string;
	};

	type ViewState = 'loading' | 'ready' | 'disabled' | 'denied' | 'error';

	type SizeOption = {
		value: string;
		ratio: string;
		label: string;
	};

	type WorkspaceImagePrefs = {
		selectionKey?: string;
		model?: string;
		credential_source?: string;
		connection_index?: number | null;
		presetSize?: string;
		customSize?: string;
		useCustomSize?: boolean;
		aspect_ratio?: string;
		resolution?: string;
		steps?: number;
		learnedConstraints?: Record<string, LearnedImageConstraint>;
	};

	const WORKSPACE_IMAGE_PREFS_KEY = 'workspace:image-studio:prefs:v1';
	const i18n = getContext('i18n');

	const promptIdeas = [
		'Cinematic portrait',
		'Clean product shot',
		'Editorial poster',
		'Cozy illustration',
		'Neon cityscape',
		'Minimal interior'
	];

	const curatedSizeOptions: SizeOption[] = [
		{ value: '1024x1024', ratio: '1:1', label: '1024x1024' },
		{ value: '1024x1536', ratio: '2:3', label: '1024x1536' },
		{ value: '1536x1024', ratio: '3:2', label: '1536x1024' },
		{ value: '1536x1536', ratio: '1:1', label: '1536x1536' },
		{ value: '2048x2048', ratio: '1:1', label: '2048x2048' },
		{ value: '2048x3072', ratio: '2:3', label: '2048x3072' },
		{ value: '3072x2048', ratio: '3:2', label: '3072x2048' }
	];

	let loaded = false;
	let loading = false;
	let preferencesReady = false;
	let lastPersistedPrefsSnapshot = '';
	let viewState: ViewState = 'loading';
	let loadError: string | null = null;
	let imageModels: ImageGenerationModel[] = [];
	let prompt = '';
	let selectedModel = '';
	let selectedModelRawId = '';
	let selectedPresetSize = WORKSPACE_IMAGE_SIZE_PRESETS[0];
	let usingCustomSize = false;
	let customSizeInput = '';
	let selectedAspectRatioOption = '1:1';
	let selectedResolution = '1k';
	let steps = 0;
	let canSubmit = false;
	let blockedReason: string | null = null;
	let workspaceNoModels = false;
	let learnedConstraints: Record<string, LearnedImageConstraint> = {};

	let generatedImages: GeneratedImage[] = [];
	let lastPrompt = '';
	let resultsSectionElement: HTMLElement | null = null;

	let previewOpen = false;
	let previewSrc = '';
	let previewAlt = '';
	let hadPersistedSelectionKey = false;

	const isAdmin = () => $user?.role === 'admin';
	const formatError = (error: unknown) =>
		localizeCommonError(error, (key, options) => $i18n.t(key, options));
	const getModelOptionValue = (model: ImageGenerationModel | null | undefined) =>
		`${model?.selection_key ?? model?.legacy_id ?? model?.id ?? ''}`.trim();
	const getModelSourceBadge = (model: ImageGenerationModel | null | undefined) => {
		const source = `${model?.source ?? ''}`.trim().toLowerCase();
		if (source === 'shared') {
			return $i18n.t('Shared');
		}
		if (source === 'personal') {
			return $i18n.t('Personal');
		}
		return '';
	};
	const getModelLabel = (model: ImageGenerationModel | null | undefined) =>
		getModelChatDisplayName(model as { id?: string; name?: string; connection_name?: string } | null) ||
		`${model?.name ?? model?.id ?? ''}`.trim();

	const loadWorkspacePrefs = () => {
		try {
			const raw = localStorage.getItem(WORKSPACE_IMAGE_PREFS_KEY);
			if (!raw) return;

			const prefs = JSON.parse(raw) as WorkspaceImagePrefs;
			selectedModel = `${prefs?.selectionKey ?? prefs?.model ?? ''}`.trim();
			selectedModelRawId = `${prefs?.model ?? ''}`.trim();
			hadPersistedSelectionKey = Boolean(`${prefs?.selectionKey ?? ''}`.trim());
			selectedPresetSize = curatedSizeOptions.some(
				(option) => option.value === `${prefs?.presetSize ?? ''}`.trim()
			)
				? (`${prefs?.presetSize}`.trim() as (typeof WORKSPACE_IMAGE_SIZE_PRESETS)[number])
				: WORKSPACE_IMAGE_SIZE_PRESETS[0];
			customSizeInput = `${prefs?.customSize ?? ''}`.trim();
			usingCustomSize = Boolean(prefs?.useCustomSize && customSizeInput);
			selectedAspectRatioOption = `${prefs?.aspect_ratio ?? '1:1'}`.trim() || '1:1';
			selectedResolution = `${prefs?.resolution ?? '1k'}`.trim().toLowerCase() || '1k';

			const nextSteps = Number(prefs?.steps ?? 0);
			steps = Number.isFinite(nextSteps) && nextSteps >= 0 ? nextSteps : 0;
			learnedConstraints =
				prefs?.learnedConstraints && typeof prefs.learnedConstraints === 'object'
					? prefs.learnedConstraints
					: {};
			lastPersistedPrefsSnapshot = raw;
		} catch (error) {
			console.warn('Failed to load workspace image prefs', error);
		}
	};

	$: modelOptions = imageModels.map((model) => ({
		value: getModelOptionValue(model),
		label: getModelLabel(model),
		description: model.id,
		badge: getModelSourceBadge(model)
	}));
	$: nativeAspectRatioOptions = GROK_IMAGE_ASPECT_RATIO_OPTIONS.map((option) => ({
		value: option.value,
		label: option.label
	}));
	$: nativeResolutionOptions = GROK_IMAGE_RESOLUTION_OPTIONS.map((option) => ({
		value: option.value,
		label: option.label
	}));

	$: sizeOptions = [
		...curatedSizeOptions,
		{
			value: CUSTOM_SIZE_OPTION_VALUE,
			ratio: $i18n.t('Custom'),
			label: $i18n.t('Custom size')
		}
	];

	$: selectedModelLabel =
		modelOptions.find((option) => option.value === selectedModel)?.label ?? selectedModel;
	$: selectedModelMeta =
		imageModels.find((model) => getModelOptionValue(model) === selectedModel) ?? null;
	$: usesNativeAspectRatioControls = Boolean(
		selectedModelMeta &&
			(selectedModelMeta?.size_mode === 'aspect_ratio' || selectedModelMeta?.supports_resolution)
	);
	$: showsResolutionControl = Boolean(selectedModelMeta?.supports_resolution);
	$: showsStepsControl = !showsResolutionControl;
	$: activeSize = usingCustomSize ? `${customSizeInput ?? ''}`.trim() : selectedPresetSize;
	$: activeSizeLabel =
		usingCustomSize && activeSize ? activeSize : usingCustomSize ? $i18n.t('Custom size') : selectedPresetSize;
	$: activeSizeParsed = parseImageSize(activeSize);
	$: selectedAspectRatio = activeSizeParsed?.aspectRatio ?? null;
	$: currentConstraint = selectedModel ? learnedConstraints[selectedModel] ?? null : null;
	$: sizeSelectValue = usingCustomSize ? CUSTOM_SIZE_OPTION_VALUE : selectedPresetSize;
	$: recommendedSizes = getRecommendedImageSizes(activeSize, {
		minPixels: currentConstraint?.minPixels,
		limit: 3
	});
	$: if (selectedModelMeta?.id) {
		selectedModelRawId = selectedModelMeta.id;
	}

	$: sizeValidation = (() => {
		if (usesNativeAspectRatioControls) {
			return null;
		}

		if (usingCustomSize && !activeSize) {
			return {
				kind: 'empty',
				blocking: true,
				title: $i18n.t('Custom size is required'),
				description: $i18n.t('Enter a custom size like {{example}}.', { example: '1344x768' })
			};
		}

		if (usingCustomSize && !activeSizeParsed) {
			return {
				kind: 'format',
				blocking: true,
				title: $i18n.t('Custom size format is invalid'),
				description: $i18n.t('Use a value like {{example}}.', { example: '1344x768' })
			};
		}

		if (currentConstraint?.minPixels && activeSizeParsed) {
			if (activeSizeParsed.pixels < currentConstraint.minPixels) {
				return {
					kind: 'minPixels',
					blocking: true,
					title: $i18n.t("Current size does not meet this model's requirement."),
					description: $i18n.t(
						'{{size}} has {{pixels}} pixels. This model currently requires at least {{minPixels}} pixels.',
						{
							size: activeSizeParsed.value,
							pixels: formatPixelCount(activeSizeParsed.pixels),
							minPixels: formatPixelCount(currentConstraint.minPixels)
						}
					)
				};
			}
		}

		return null;
	})();

	$: blockedReason =
		viewState !== 'ready'
			? viewState === 'denied'
				? $i18n.t('Image generation access required')
				: viewState === 'disabled'
					? $i18n.t('Image generation is disabled by the administrator.')
					: loadError || $i18n.t('Failed to load image generation settings.')
			: workspaceNoModels
				? $i18n.t('Image models are unavailable right now. Check your image settings.')
				: sizeValidation?.description ?? null;

	$: canSubmit =
		!loading &&
		viewState === 'ready' &&
		imageModels.length > 0 &&
		Boolean(prompt.trim()) &&
		!sizeValidation?.blocking;

	$: currentPrefsSnapshot = preferencesReady
		? JSON.stringify({
				selectionKey: selectedModel,
				model: selectedModelMeta?.id ?? selectedModelRawId ?? '',
				credential_source: selectedModelMeta?.source ?? '',
				connection_index: selectedModelMeta?.connection_index ?? null,
				model_ref: selectedModelMeta?.model_ref ?? null,
				presetSize: selectedPresetSize,
				customSize: customSizeInput,
				useCustomSize: usingCustomSize,
				aspect_ratio: selectedAspectRatioOption,
				resolution: selectedResolution,
				steps,
				learnedConstraints
			})
		: '';

	$: if (
		preferencesReady &&
		currentPrefsSnapshot &&
		currentPrefsSnapshot !== lastPersistedPrefsSnapshot
	) {
		try {
			localStorage.setItem(WORKSPACE_IMAGE_PREFS_KEY, currentPrefsSnapshot);
			lastPersistedPrefsSnapshot = currentPrefsSnapshot;
		} catch (error) {
			console.warn('Failed to persist workspace image prefs', error);
		}
	}

	const applyPromptIdea = (idea: string) => {
		const translatedIdea = $i18n.t(idea);
		prompt = prompt.trim() ? `${prompt.trim()}, ${translatedIdea}` : translatedIdea;
	};

	const handleComposerKeydown = (event: KeyboardEvent) => {
		if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !loading) {
			event.preventDefault();
			void submitHandler();
		}
	};

	const syncSelectedModelWithAvailableModels = (models: ImageGenerationModel[]) => {
		const normalizedModels = models ?? [];
		const availableValues = new Set(
			normalizedModels.map((model) => getModelOptionValue(model)).filter(Boolean)
		);

		if (selectedModel && availableValues.has(selectedModel)) {
			return;
		}

		const preferredModelId = `${selectedModelRawId ?? ''}`.trim();
		const nextModel =
			(preferredModelId
				? normalizedModels.find(
						(model) => model.legacy_id === preferredModelId
					) ??
					normalizedModels.find(
						(model) => model.id === preferredModelId && `${model.source ?? ''}`.trim() === 'shared'
					) ??
					normalizedModels.find((model) => model.id === preferredModelId)
				: null) ?? normalizedModels[0] ?? null;

		if (!nextModel) {
			selectedModel = '';
			return;
		}

		const nextValue = getModelOptionValue(nextModel);
		const sourceChanged =
			hadPersistedSelectionKey &&
			Boolean(selectedModel) &&
			selectedModel !== nextValue &&
			Boolean(preferredModelId) &&
			nextModel.id === preferredModelId;

		selectedModel = nextValue;
		selectedModelRawId = nextModel.id;

		if (sourceChanged) {
			toast.info($i18n.t('Your previous image model source is unavailable. Switched to another available source for the same model.'));
			hadPersistedSelectionKey = false;
		}
	};

	const loadWorkspaceModels = async () => {
		loadError = null;
		const nextModels = await getImageGenerationModels(localStorage.token, {
			context: 'workspace',
			credentialSource: 'auto'
		}).catch((error) => {
			loadError = `${error ?? ''}`;
			return null;
		});

		imageModels = Array.isArray(nextModels) ? nextModels : [];
		syncSelectedModelWithAvailableModels(imageModels);
		workspaceNoModels = !loadError && imageModels.length === 0;
	};

	const copyPromptHandler = async () => {
		const text = lastPrompt || prompt.trim();
		if (!text) return;

		const copied = await copyToClipboard(text);
		if (copied) {
			toast.success($i18n.t('Prompt copied'));
		}
	};

	const openPreview = (image: GeneratedImage, index: number) => {
		previewSrc = image.url;
		previewAlt = `${$i18n.t('Generated image')} ${index + 1}`;
		previewOpen = true;
	};

	const downloadImage = (url: string, index: number) => {
		const link = document.createElement('a');
		link.href = url;
		link.download = `generated-image-${index + 1}.png`;
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);
	};

	const setLearnedConstraint = (constraint: LearnedImageConstraint | null) => {
		if (!constraint?.minPixels || !selectedModel) return;

		const previous = learnedConstraints[selectedModel];
		const nextMinPixels = Math.max(previous?.minPixels ?? 0, constraint.minPixels);

		learnedConstraints = {
			...learnedConstraints,
			[selectedModel]: {
				...previous,
				...constraint,
				minPixels: nextMinPixels
			}
		};
	};

	const buildToastDescription = (constraint: LearnedImageConstraint | null) => {
		const parts: string[] = [];

		if (constraint?.minPixels && activeSizeParsed) {
			parts.push(
				$i18n.t(
					'{{size}} has {{pixels}} pixels. This model currently requires at least {{minPixels}} pixels.',
					{
						size: activeSizeParsed.value,
						pixels: formatPixelCount(activeSizeParsed.pixels),
						minPixels: formatPixelCount(constraint.minPixels)
					}
				)
			);
		}

		if (constraint?.requestId) {
			parts.push(`${$i18n.t('Request ID')}: ${constraint.requestId}`);
		}

		return parts.join(' ');
	};

	const showSizeValidationToast = () => {
		if (!sizeValidation) return;

		toast.error(sizeValidation.title, {
			description: sizeValidation.description,
			duration: 6000
		});
	};

	const handleSizeSelect = (nextValue: string) => {
		if (nextValue === CUSTOM_SIZE_OPTION_VALUE) {
			usingCustomSize = true;
			customSizeInput = customSizeInput.trim() || selectedPresetSize;
			return;
		}

		usingCustomSize = false;
		selectedPresetSize = nextValue;
	};

	const restorePresetSize = () => {
		usingCustomSize = false;
		customSizeInput = '';
	};

	const applyRecommendedSize = (size: string) => {
		selectedPresetSize = size;
		usingCustomSize = false;
		customSizeInput = '';
	};

	const submitHandler = async () => {
		const trimmedPrompt = prompt.trim();
		if (!trimmedPrompt) {
			toast.error($i18n.t('Please enter a prompt'));
			return;
		}

		if (sizeValidation?.blocking) {
			showSizeValidationToast();
			return;
		}

		if (!canSubmit) {
			if (blockedReason) {
				toast.error(blockedReason);
			}
			return;
		}

		loading = true;
		generatedImages = [];
		lastPrompt = trimmedPrompt;

		try {
			const response = await imageGenerations(localStorage.token, {
				prompt: trimmedPrompt,
				model: selectedModelMeta?.id || selectedModelRawId || undefined,
				model_ref: selectedModelMeta?.model_ref ?? undefined,
				size: usesNativeAspectRatioControls ? undefined : activeSize || undefined,
				aspect_ratio: usesNativeAspectRatioControls ? selectedAspectRatioOption : undefined,
				resolution: showsResolutionControl ? selectedResolution : undefined,
				steps: showsStepsControl && steps > 0 ? steps : undefined,
				credential_source:
					selectedModelMeta?.source === 'personal' || selectedModelMeta?.source === 'shared'
						? selectedModelMeta.source
						: undefined,
				connection_index: selectedModelMeta?.connection_index ?? undefined
			});

			if (response?.length) {
				generatedImages = response;
				await tick();
				resultsSectionElement?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
			} else {
				toast.error(
					$i18n.t('Model returned an empty response. Try resending or switching models.')
				);
			}
		} catch (error) {
			const learnedConstraint = extractImageConstraintFromError(error);
			setLearnedConstraint(learnedConstraint);

			if (learnedConstraint) {
				toast.error($i18n.t("Current size does not meet this model's requirement."), {
					description:
						buildToastDescription(learnedConstraint) || formatError(error),
					duration: 6000
				});
			} else {
				toast.error(formatError(error));
			}
		} finally {
			loading = false;
		}
	};

	onMount(async () => {
		loadWorkspacePrefs();
		preferencesReady = true;

		const allowed =
			$user?.role === 'admin' || Boolean($user?.permissions?.features?.image_generation);
		if (!allowed) {
			viewState = 'denied';
			loaded = true;
			return;
		}

		viewState = 'loading';
		loaded = true;

		const usageResult = await getImageUsageConfig(localStorage.token).catch((error) => error);

		if (
			usageResult instanceof Error ||
			!usageResult ||
			typeof usageResult !== 'object' ||
			!('enabled' in usageResult)
		) {
			loadError = `${usageResult ?? ''}`;
			viewState = 'error';
			return;
		}

		const usageConfig = usageResult as ImageUsageConfig;
		if (!usageConfig.enabled) {
			viewState = 'disabled';
			return;
		}


		await loadWorkspaceModels();

		if (loadError) {
			viewState = 'error';
			return;
		}

		viewState = 'ready';
	});
</script>

<svelte:head>
	<title>{$i18n.t('Images')} | {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	{#if viewState !== 'ready' || workspaceNoModels}
		<div class="space-y-4">
			<section class="workspace-section space-y-4">
				<div class="flex flex-col gap-3 lg:flex-row lg:items-center">
					<div class="workspace-toolbar-summary">
						<div class="workspace-count-pill">
							<PhotoSolid className="size-3.5" />
							{$i18n.t('Image Studio')}
						</div>
						<div class="text-xs text-gray-500 dark:text-gray-400">
							{blockedReason ?? $i18n.t('Loading image generation settings...')}
						</div>
					</div>
				</div>
			</section>

			<section class="workspace-section">
				<div class="workspace-empty-state">
					<div class="flex size-14 mx-auto items-center justify-center rounded-2xl bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500">
						<PhotoSolid className="size-7" />
					</div>
					<h2 class="mt-4 text-base font-semibold text-gray-900 dark:text-white">
						{viewState === 'denied'
							? $i18n.t('Image generation access required')
							: viewState === 'disabled'
								? $i18n.t('Image generation is disabled')
								: viewState === 'error'
									? $i18n.t('Unable to load image generation')
									: workspaceNoModels
										? $i18n.t('No models available')
										: $i18n.t('Loading...')}
					</h2>
					<p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
						{blockedReason ??
							(viewState === 'loading'
								? $i18n.t('Loading image generation settings...')
								: $i18n.t('Please try again later.'))}
					</p>
					<div class="mt-5 flex flex-wrap justify-center gap-2">
						<button type="button" class="workspace-secondary-button text-xs" on:click={() => location.reload()}>
							{$i18n.t('Refresh')}
						</button>
					</div>
				</div>
			</section>
		</div>
	{:else}
		<form class="space-y-4" on:submit|preventDefault={submitHandler}>
			<section class="workspace-section space-y-4">
				<div class="flex flex-col gap-3 lg:flex-row lg:items-center">
					<div class="workspace-toolbar-summary">
						<div class="workspace-count-pill">
							<PhotoSolid className="size-3.5" />
							{$i18n.t('Image Studio')}
						</div>
						<div class="space-y-1 text-xs text-gray-500 dark:text-gray-400">
							<div>
								{$i18n.t('Create polished visuals from a single prompt.')}
								<span class="hidden sm:inline ml-1 opacity-70">
									{$i18n.t('Press Ctrl/Command + Enter to generate.')}
								</span>
							</div>
							<div class="opacity-80">
								{$i18n.t(
									'This image workbench remembers your last model and generation settings only in this browser.'
								)}
							</div>
							<div class="opacity-80">
								{$i18n.t(
									'Shows all currently available image models. The suffix in the name indicates the source channel.'
								)}
							</div>
						</div>
					</div>

					<div class="workspace-toolbar">
						<HaloSelect
							bind:value={selectedModel}
							options={modelOptions}
							placeholder={$i18n.t('Select a model')}
							searchEnabled={true}
							searchPlaceholder={$i18n.t('Search a model')}
							noResultsText={$i18n.t('No results found')}
							className="w-full lg:w-72 text-xs"
						/>

						<div class="workspace-toolbar-actions">
							<button
								type="submit"
								class="workspace-primary-button"
								disabled={!canSubmit}
								title={blockedReason ?? ''}
							>
								{#if loading}
									<svg class="size-4 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
										<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
										<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
									</svg>
								{:else}
									<Sparkles className="size-4" strokeWidth="2" />
								{/if}
								<span>{loading ? $i18n.t('Generating...') : $i18n.t('Generate')}</span>
							</button>
						</div>
					</div>
				</div>
			</section>

			<section class="workspace-section space-y-4">
				<div class="glass-item p-4 space-y-3">
					<div class="flex items-center justify-between">
						<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
							{$i18n.t('Main Prompt')}
						</div>
						<Sparkles className="size-4 text-gray-400" />
					</div>

					<textarea
						rows="5"
						bind:value={prompt}
						on:keydown={handleComposerKeydown}
						placeholder={$i18n.t('Describe the image you want to generate...')}
						class="min-h-[8rem] w-full resize-none rounded-xl border border-gray-200/60 bg-white/85 p-3 text-sm leading-6 text-gray-900 outline-none placeholder:text-gray-400 dark:border-gray-700/50 dark:bg-gray-900/70 dark:text-gray-100 dark:placeholder:text-gray-500"
					/>

					<div class="flex flex-wrap gap-1.5">
						{#each promptIdeas as idea}
							<button
								type="button"
								class="rounded-full border border-gray-200/60 bg-white/85 px-2.5 py-1 text-xs text-gray-600 transition hover:bg-gray-50 dark:border-gray-700/50 dark:bg-gray-900/70 dark:text-gray-400 dark:hover:bg-gray-800"
								on:click={() => applyPromptIdea(idea)}
							>
								{$i18n.t(idea)}
							</button>
						{/each}
					</div>
				</div>

				<div class="glass-item p-4 space-y-4">
					<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
						{$i18n.t('Generation Settings')}
					</div>

					<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
						<div class="space-y-1.5">
							<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
								{$i18n.t('Model')}
							</div>
							<HaloSelect
								bind:value={selectedModel}
								options={modelOptions}
								placeholder={$i18n.t('Select a model')}
								searchEnabled={true}
								searchPlaceholder={$i18n.t('Search a model')}
								noResultsText={$i18n.t('No results found')}
								className="w-full text-xs"
							/>
						</div>

						{#if usesNativeAspectRatioControls}
							<div class="space-y-1.5">
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
									{$i18n.t('Aspect Ratio')}
								</div>
								<HaloSelect
									bind:value={selectedAspectRatioOption}
									options={nativeAspectRatioOptions}
									className="w-full text-xs"
								/>
								<div class="text-xs text-gray-500 dark:text-gray-400">
									{$i18n.t('This model uses aspect ratio instead of exact pixel size.')}
								</div>
							</div>
						{:else}
							<div class="space-y-1.5">
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
									{$i18n.t('Size')}
								</div>
								<HaloSelect
									value={sizeSelectValue}
									options={sizeOptions.map((option) => ({
										value: option.value,
										label:
											option.value === CUSTOM_SIZE_OPTION_VALUE
												? option.label
												: `${option.ratio} · ${option.label}`
									}))}
									className="w-full text-xs"
									on:change={(event) => handleSizeSelect(event.detail.value)}
								/>

								{#if usingCustomSize}
									<div class="space-y-2 rounded-xl border border-dashed border-gray-200/80 bg-gray-50/70 p-3 dark:border-gray-700/60 dark:bg-gray-900/40">
										<div class="flex items-center justify-between gap-2">
											<div class="text-xs font-medium text-gray-700 dark:text-gray-200">
												{$i18n.t('Custom size')}
											</div>
											<button
												type="button"
												class="text-xs font-medium text-gray-500 transition hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
												on:click={restorePresetSize}
											>
												{$i18n.t('Restore preset')}
											</button>
										</div>
										<input
											bind:value={customSizeInput}
											placeholder="1344x768"
											class="w-full rounded-xl border border-gray-200/80 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition focus:border-gray-300 dark:border-gray-700/60 dark:bg-gray-950/70 dark:text-gray-100 dark:focus:border-gray-600"
										/>
										<div class="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
											<div>{$i18n.t('Enter a custom size like {{example}}.', { example: '1344x768' })}</div>
											{#if activeSizeParsed}
												<div>
													{$i18n.t('Total pixels')}: {formatPixelCount(activeSizeParsed.pixels)}
												</div>
											{/if}
										</div>
									</div>
								{/if}

								{#if selectedModelMeta?.size_mode === 'aspect_ratio' && selectedAspectRatio}
									<div class="text-xs text-amber-600 dark:text-amber-400">
										{$i18n.t(
											'This model only accepts aspect ratio requests. {{size}} will be sent as {{ratio}}, not as an exact pixel size.',
											{ size: activeSizeLabel, ratio: selectedAspectRatio }
										)}
									</div>
								{/if}

								{#if sizeValidation}
									<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
										<div class="font-semibold">{sizeValidation.title}</div>
										<div class="mt-1 leading-5">{sizeValidation.description}</div>
										{#if currentConstraint?.requestId}
											<div class="mt-2 opacity-80">
												{$i18n.t('Request ID')}: {currentConstraint.requestId}
											</div>
										{/if}
										{#if recommendedSizes.length > 0}
											<div class="mt-3">
												<div class="mb-2 font-medium">{$i18n.t('Recommended sizes')}</div>
												<div class="flex flex-wrap gap-2">
													{#each recommendedSizes as size}
														<button
															type="button"
															class="rounded-full border border-amber-300 bg-white px-3 py-1 font-medium text-amber-700 transition hover:bg-amber-100 dark:border-amber-500/30 dark:bg-transparent dark:text-amber-200 dark:hover:bg-amber-500/20"
															on:click={() => applyRecommendedSize(size)}
														>
															{size}
														</button>
													{/each}
												</div>
											</div>
										{/if}
									</div>
								{/if}
							</div>
						{/if}

						<div class="space-y-1.5">
							{#if showsResolutionControl}
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
									{$i18n.t('Resolution')}
								</div>
								<HaloSelect
									bind:value={selectedResolution}
									options={nativeResolutionOptions}
									className="w-full text-xs"
								/>
							{:else if showsStepsControl}
								<div class="flex items-center justify-between">
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
										{$i18n.t('Set Steps')}
									</div>
									<div class="text-xs font-semibold text-gray-900 dark:text-gray-100">
										{steps === 0 ? $i18n.t('Auto') : steps}
									</div>
								</div>
								<input
									type="range"
									min="0"
									max="80"
									step="5"
									bind:value={steps}
									class="image-range mt-1 h-2 w-full cursor-pointer appearance-none rounded-full bg-gray-200 dark:bg-gray-800"
								/>
							{/if}
						</div>
					</div>
				</div>
			</section>

			<section bind:this={resultsSectionElement} class="workspace-section space-y-3">
				<div class="flex items-center justify-between">
					<div class="min-w-0 flex-1">
						<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
							{$i18n.t('Recent Result')}
						</div>
						{#if lastPrompt}
							<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400 truncate">
								{lastPrompt}
							</div>
						{/if}
					</div>
					{#if lastPrompt}
						<button type="button" class="workspace-icon-button" on:click={copyPromptHandler}>
							<Clipboard className="size-3.5" />
							<span class="text-xs">{$i18n.t('Copy prompt')}</span>
						</button>
					{/if}
				</div>

				{#if loading}
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
						<div class="shimmer h-56 rounded-xl glass-item" />
					</div>
				{:else if generatedImages.length > 0}
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
						{#each generatedImages as image, index}
							<div
								role="button"
								tabindex="0"
								class="result-card glass-item group overflow-hidden cursor-pointer p-1.5"
								style={`animation-delay: ${index * 60}ms;`}
								on:click={() => openPreview(image, index)}
								on:keydown={(event) => {
									if (event.key === 'Enter' || event.key === ' ') {
										event.preventDefault();
										openPreview(image, index);
									}
								}}
							>
								<div class="relative overflow-hidden rounded-lg bg-gray-100 dark:bg-gray-950">
									<img
										src={image.url}
										alt={`${$i18n.t('Generated image')} ${index + 1}`}
										class="max-h-[22rem] min-h-[12rem] w-full object-cover transition duration-500 group-hover:scale-[1.02]"
										loading="lazy"
									/>
									<div
										class="absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-black/70 via-black/20 to-transparent p-3 text-white opacity-0 transition duration-200 group-hover:opacity-100"
									>
										<div class="text-xs font-medium">{activeSizeLabel}</div>
										<div class="flex items-center gap-2">
											<button
												type="button"
												class="rounded-xl bg-white/15 p-2 backdrop-blur transition hover:bg-white/25"
												on:click|stopPropagation={() => openPreview(image, index)}
												aria-label={$i18n.t('Open preview')}
											>
												<ArrowsPointingOut className="size-4" />
											</button>
											<button
												type="button"
												class="rounded-xl bg-white/15 p-2 backdrop-blur transition hover:bg-white/25"
												on:click|stopPropagation={() => downloadImage(image.url, index)}
												aria-label={$i18n.t('Save image')}
											>
												<ArrowDownTray className="size-4" />
											</button>
										</div>
									</div>
								</div>
							</div>
						{/each}
					</div>
				{:else}
					<div class="workspace-empty-state">
						<div class="flex size-14 mx-auto items-center justify-center rounded-2xl bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500">
							<PhotoSolid className="size-7" />
						</div>
						<div class="mt-4 text-base font-semibold text-gray-900 dark:text-gray-100">
							{$i18n.t('Your images will appear here after generation.')}
						</div>
						<div class="mt-2 max-w-sm mx-auto text-sm leading-6 text-gray-500 dark:text-gray-400">
							{$i18n.t('Start with a strong subject, then add lighting, composition, materials, and mood for better results.')}
						</div>
					</div>
				{/if}
			</section>
		</form>
	{/if}
{/if}

<ImagePreview
	bind:show={previewOpen}
	src={previewSrc}
	alt={previewAlt}
/>
