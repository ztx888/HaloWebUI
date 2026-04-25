<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, getContext, onMount, tick } from 'svelte';
	import { config as backendConfig, user } from '$lib/stores';
	import { getBackendConfig } from '$lib/apis';
	import { getConfig, updateConfig, getImageGenerationConfig, updateImageGenerationConfig } from '$lib/apis/images';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	let loading = false;
	let config = null;
	let imageGenerationConfig = { IMAGE_MODEL_FILTER_REGEX: '' };
	let initialSnapshot = null;

	const getErrorText = (error) => {
		if (typeof error === 'string') return error;
		if (error instanceof Error) return error.message;
		if (error && typeof error === 'object') {
			if ('detail' in error && typeof error.detail === 'string') return error.detail;
			if ('message' in error && typeof error.message === 'string') return error.message;
		}
		return `${error ?? ''}`;
	};

	const formatImageSettingsError = (error) => {
		const message = getErrorText(error).trim();
		return message ? $i18n.t(message) : $i18n.t('Connection failed');
	};

	const normalizeImageSettingsSnapshot = (
		sourceConfig = config,
		sourceImageConfig = imageGenerationConfig
	) => ({
		enabled: sourceConfig?.enabled === true,
		shared_key_enabled: sourceConfig?.shared_key_enabled === true,
		IMAGE_MODEL_FILTER_REGEX: `${sourceImageConfig?.IMAGE_MODEL_FILTER_REGEX ?? ''}`
	});

	$: snapshot = normalizeImageSettingsSnapshot(config, imageGenerationConfig);
	$: isDirty = !!(initialSnapshot && config && !isSettingsSnapshotEqual(snapshot, initialSnapshot));

	const syncBaseline = (sourceConfig = config, sourceImageConfig = imageGenerationConfig) => {
		initialSnapshot = cloneSettingsSnapshot(
			normalizeImageSettingsSnapshot(sourceConfig, sourceImageConfig)
		);
	};

	const resetChanges = () => {
		if (!initialSnapshot) return;
		config = {
			...config,
			enabled: initialSnapshot.enabled,
			shared_key_enabled: initialSnapshot.shared_key_enabled
		};
		imageGenerationConfig = {
			...imageGenerationConfig,
			IMAGE_MODEL_FILTER_REGEX: initialSnapshot.IMAGE_MODEL_FILTER_REGEX
		};
	};

	const serializeConfigForSave = (draftConfig) => ({
		...normalizeImageSettingsSnapshot(draftConfig, imageGenerationConfig),
		engine: '',
		prompt_generation: false
	});

	const loadImageSettings = async () => {
		const [loadedConfig, loadedImageConfig] = await Promise.all([
			getConfig(localStorage.token).catch((error) => {
				toast.error(formatImageSettingsError(error));
				return null;
			}),
			getImageGenerationConfig(localStorage.token).catch((error) => {
				toast.error(formatImageSettingsError(error));
				return null;
			})
		]);

		if (loadedConfig) config = normalizeImageSettingsSnapshot(loadedConfig, imageGenerationConfig);
		if (loadedImageConfig) {
			imageGenerationConfig = {
				...imageGenerationConfig,
				IMAGE_MODEL_FILTER_REGEX: `${loadedImageConfig?.IMAGE_MODEL_FILTER_REGEX ?? ''}`
			};
		}
	};

	const saveHandler = async () => {
		loading = true;

		const updatedConfig = await updateConfig(localStorage.token, serializeConfigForSave(config)).catch((error) => {
			toast.error(formatImageSettingsError(error));
			return null;
		});

		const updatedImageGenerationConfig = await updateImageGenerationConfig(
			localStorage.token,
			{
				...imageGenerationConfig,
				IMAGE_MODEL_FILTER_REGEX: `${imageGenerationConfig?.IMAGE_MODEL_FILTER_REGEX ?? ''}`
			}
		).catch((error) => {
			toast.error(formatImageSettingsError(error));
			return null;
		});

		if (!updatedConfig || !updatedImageGenerationConfig) {
			loading = false;
			return;
		}

		config = normalizeImageSettingsSnapshot(updatedConfig, imageGenerationConfig);
		imageGenerationConfig = {
			...imageGenerationConfig,
			IMAGE_MODEL_FILTER_REGEX: `${updatedImageGenerationConfig?.IMAGE_MODEL_FILTER_REGEX ?? ''}`
		};
		backendConfig.set(await getBackendConfig());
		await tick();
		syncBaseline(config, imageGenerationConfig);
		dispatch('save');
		loading = false;
	};

	onMount(async () => {
		if ($user?.role !== 'admin') return;

		await loadImageSettings();
		await tick();
		syncBaseline();
	});
</script>

<form class="flex h-full min-h-0 flex-col text-sm" on:submit|preventDefault={saveHandler}>
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		{#if config}
			<div class="max-w-6xl mx-auto space-y-6">
				<section class="glass-section p-5 space-y-5 {isDirty ? 'glass-section-dirty' : ''}">
					<div class="flex items-center justify-between gap-3">
						<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
							{$i18n.t('Image Settings')}
						</div>
						<InlineDirtyActions dirty={isDirty} saving={loading} on:reset={resetChanges} />
					</div>

					<div class="space-y-3">
						<div class="flex items-center justify-between glass-item px-4 py-3">
							<div>
								<div class="text-sm font-medium">{$i18n.t('Image Generation')}</div>
								<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('Users can generate images by selecting an image model in chat or in the image workspace.')}
								</div>
							</div>
							<Switch bind:state={config.enabled} />
						</div>

						<div class="flex items-center justify-between glass-item px-4 py-3">
							<div>
								<div class="text-sm font-medium">{$i18n.t('Allow users to use the workspace shared key')}</div>
								<div class="mt-1 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('When enabled, users without personal connections can fall back to the workspace shared key.')}
								</div>
							</div>
							<Switch bind:state={config.shared_key_enabled} />
						</div>
					</div>
				</section>

				<section class="glass-section p-5 space-y-5 {isDirty ? 'glass-section-dirty' : ''}">
					<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
						{$i18n.t('Model Filter Regex')}
					</div>
					<div class="glass-item p-4">
						<Tooltip content={$i18n.t('Regex pattern to filter image models (leave empty to show all)')} placement="top-start">
							<input
								class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
								placeholder={$i18n.t('e.g. dall-e|gpt-image')}
								bind:value={imageGenerationConfig.IMAGE_MODEL_FILTER_REGEX}
							/>
						</Tooltip>
					</div>
				</section>
			</div>
		{/if}
	</div>
</form>
