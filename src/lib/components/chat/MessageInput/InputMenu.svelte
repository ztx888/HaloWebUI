<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { getContext } from 'svelte';

	import { config, user, tools as _tools } from '$lib/stores';

	import { getTools } from '$lib/apis/tools';
	import { getWebSearchModeLabel, type WebSearchMode } from '$lib/utils/web-search-mode';
	import type { WebSearchModeOption } from '$lib/utils/native-web-search';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import { Wrench, Globe, Image, Terminal, Camera, FileUp } from 'lucide-svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';

	const i18n = getContext('i18n');

	export let screenCaptureHandler: Function;
	export let uploadFilesHandler: Function;
	export let inputFilesHandler: Function;

	export let uploadGoogleDriveHandler: Function;
	export let uploadOneDriveHandler: Function;

	export let selectedToolIds: string[] = [];

	export let webSearchMode: WebSearchMode = 'off';
	export let webSearchModeOptions: WebSearchModeOption[] = [
		{ value: 'off', label: $i18n.t('Off') },
		{ value: 'halo', label: 'HaloWebUI' }
	];
	export let onWebSearchModeChange: ((mode: WebSearchMode) => void) | null = null;
	export let imageGenerationEnabled: boolean = false;
	export let codeInterpreterEnabled: boolean = false;

	export let onClose: Function;

	let tools = {};
	let show = false;
	let loadingTools = false;

	function toggleToolEnabled(toolId: string, enabled?: boolean) {
		const nextEnabled = enabled ?? !tools?.[toolId]?.enabled;
		tools = {
			...tools,
			[toolId]: {
				...tools[toolId],
				enabled: nextEnabled
			}
		};

		if (nextEnabled) {
			if (!selectedToolIds.includes(toolId)) {
				selectedToolIds = [...selectedToolIds, toolId];
			}
		} else {
			selectedToolIds = selectedToolIds.filter((id) => id !== toolId);
		}
	}

	$: if (show) {
		init();
	}

	let fileUploadEnabled = true;
	$: fileUploadEnabled = $user?.role === 'admin' || $user?.permissions?.chat?.file_upload;
	$: webSearchFeatureEnabled =
		Boolean($config?.features?.enable_halo_web_search ?? $config?.features?.enable_web_search) ||
		Boolean($config?.features?.enable_native_web_search);

	const init = async () => {
		if (!loadingTools) {
			loadingTools = true;
			try {
				const latestTools = await getTools(localStorage.token).catch(() => null);
				if (latestTools) {
					_tools.set(latestTools);
				}
			} finally {
				loadingTools = false;
			}
		}

		tools = ($_tools ?? []).reduce((a, tool) => {
			a[tool.id] = {
				name: tool.name,
				description: tool.meta.description,
				source: tool.meta?.source,
				ownerName: tool.meta?.owner_name,
				enabled: selectedToolIds.includes(tool.id)
			};
			return a;
		}, {});
	};

	const detectMobile = () => {
		const userAgent = navigator.userAgent || navigator.vendor || window.opera;
		return /android|iphone|ipad|ipod|windows phone/i.test(userAgent);
	};

	function handleFileChange(event) {
		const inputFiles = Array.from(event.target?.files);
		if (inputFiles && inputFiles.length > 0) {
			console.log(inputFiles);
			// Small delay for Android camera capture: some devices return
			// a black/empty image if the blob is read immediately.
			setTimeout(() => {
				inputFilesHandler(inputFiles);
			}, 300);
		}
		// Reset input so re-capturing triggers change event
		event.target.value = '';
	}

	$: currentWebSearchModeOption =
		webSearchModeOptions.find((option) => option.value === webSearchMode) ?? null;
	$: currentWebSearchModeLabel =
		currentWebSearchModeOption?.shortLabel ??
		currentWebSearchModeOption?.label ??
		getWebSearchModeLabel(webSearchMode, $i18n.t.bind($i18n));

	const getOptionDescriptionClasses = (tone?: WebSearchModeOption['descriptionTone']) => {
		switch (tone) {
			case 'warning':
				return 'mt-0.5 text-xs leading-4 text-amber-600/90 dark:text-amber-400/80';
			case 'info':
				return 'mt-0.5 text-xs leading-4 text-sky-600/80 dark:text-sky-400/80';
			default:
				return 'mt-0.5 text-xs leading-4 text-gray-500 dark:text-gray-400';
		}
	};
</script>

<!-- Hidden file input used to open the camera on mobile -->
<input
	id="camera-input"
	type="file"
	accept="image/*"
	capture="environment"
	on:change={handleFileChange}
	style="display: none;"
/>

<Dropdown
	bind:show
	on:change={(e) => {
		if (e.detail === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
		<DropdownMenu.Content
			class="w-full max-w-[220px] rounded-xl px-1 py-1 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-sm"
			sideOffset={10}
			alignOffset={-8}
			side="top"
			align="start"
			transition={flyAndScale}
		>
			{#if Object.keys(tools).length > 0}
				<div class="  max-h-28 overflow-y-auto scrollbar-hidden">
					{#each Object.keys(tools) as toolId}
						<button
							type="button"
							class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl"
							on:click={() => {
								toggleToolEnabled(toolId);
							}}
						>
							<div class="flex-1 truncate">
								<Tooltip
									content={tools[toolId]?.description ?? ''}
									placement="top-start"
									className="flex flex-1 gap-2 items-center"
								>
									<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
										<Wrench class="size-4" strokeWidth={2} />
									</span>

									<div class=" truncate">{tools[toolId].name}</div>
								</Tooltip>
								{#if tools[toolId]?.source === 'shared'}
									<span class="shrink-0 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
										共享
									</span>
								{/if}
							</div>
							{#if tools[toolId]?.source === 'shared' && tools[toolId]?.ownerName}
								<div class="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">
									管理员：{tools[toolId].ownerName}
								</div>
							{/if}

							<div class=" shrink-0" on:click|stopPropagation>
								<Switch
									state={tools[toolId].enabled}
									on:change={async (e) => {
										toggleToolEnabled(toolId, e.detail);
									}}
								/>
							</div>
						</button>
					{/each}
				</div>

				<hr class="border-black/5 dark:border-white/5 my-1" />
			{/if}

				{#if webSearchFeatureEnabled || $config?.features?.enable_image_generation || $config?.features?.enable_code_interpreter}
					{#if webSearchFeatureEnabled && webSearchModeOptions.some((option) => option.value !== 'off') && ($user?.role === 'admin' || $user?.permissions?.features?.web_search)}
					<DropdownMenu.Sub>
						<DropdownMenu.SubTrigger
							class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800"
						>
							<div class="flex gap-2 items-center min-w-0">
								<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
									<Globe class="size-4" strokeWidth={2} />
								</span>
									<div class="truncate">{$i18n.t('Web Search')}</div>
							</div>
							<div class="shrink-0 text-xs text-gray-500 dark:text-gray-400">
								{currentWebSearchModeLabel}
							</div>
						</DropdownMenu.SubTrigger>
							<DropdownMenu.SubContent
								class="w-full min-w-[260px] rounded-xl px-1 py-1 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-sm"
								sideOffset={8}
								transition={flyAndScale}
							>
								{#each webSearchModeOptions as option}
									<DropdownMenu.Item
										disabled={option.disabled}
										class="flex w-full justify-between gap-3 items-start px-3 py-2 text-sm font-medium cursor-pointer rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800 data-[disabled]:opacity-45 data-[disabled]:cursor-not-allowed"
											on:click={() => {
												if (option.disabled) {
													return;
												}
												webSearchMode = option.value;
												onWebSearchModeChange?.(option.value);
												show = false;
											}}
										>
										<div class="min-w-0 flex-1">
											<div class="flex items-center gap-2">
												<div class="truncate">{option.label}</div>
												{#if option.badge}
													<span
														class="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500 dark:bg-gray-800 dark:text-gray-400"
													>
														{option.badge}
													</span>
												{/if}
											</div>
											{#if option.description}
												<div class={getOptionDescriptionClasses(option.descriptionTone)}>
													{option.description}
												</div>
											{/if}
										</div>
										{#if webSearchMode === option.value}
											<div class="shrink-0 pt-0.5 text-xs text-blue-500 dark:text-blue-400">✓</div>
										{/if}
									</DropdownMenu.Item>
								{/each}
								</DropdownMenu.SubContent>
							</DropdownMenu.Sub>
						{/if}

				{#if $config?.features?.enable_image_generation && ($user?.role === 'admin' || $user?.permissions?.features?.image_generation)}
					<button
						type="button"
						class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl"
						on:click={() => {
							imageGenerationEnabled = !imageGenerationEnabled;
						}}
					>
						<div class="flex gap-2 items-center">
							<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
								<Image class="size-4" strokeWidth={2} />
							</span>
							<div class="truncate">{$i18n.t('Image')}</div>
						</div>
						<div class="shrink-0" on:click|stopPropagation>
							<Switch bind:state={imageGenerationEnabled} />
						</div>
					</button>
				{/if}

				{#if $config?.features?.enable_code_interpreter && ($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)}
					<button
						type="button"
						class="flex w-full justify-between gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer rounded-xl"
						on:click={() => {
							codeInterpreterEnabled = !codeInterpreterEnabled;
						}}
					>
						<div class="flex gap-2 items-center">
							<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
								<Terminal class="size-4" strokeWidth={2} />
							</span>
							<div class="truncate">{$i18n.t('Code Interpreter')}</div>
						</div>
						<div class="shrink-0" on:click|stopPropagation>
							<Switch bind:state={codeInterpreterEnabled} />
						</div>
					</button>
				{/if}

				<hr class="border-black/5 dark:border-white/5 my-1" />
			{/if}

			<Tooltip
				content={!fileUploadEnabled ? $i18n.t('You do not have permission to upload files') : ''}
				className="w-full"
			>
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm  font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800  rounded-xl {!fileUploadEnabled
						? 'opacity-50'
						: ''}"
					on:click={() => {
						if (fileUploadEnabled) {
							if (!detectMobile()) {
								screenCaptureHandler();
							} else {
								const cameraInputElement = document.getElementById('camera-input');

								if (cameraInputElement) {
									cameraInputElement.click();
								}
							}
						}
					}}
				>
					<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
						<Camera class="size-4" strokeWidth={2} />
					</span>
					<div class=" line-clamp-1">{$i18n.t('Capture')}</div>
				</DropdownMenu.Item>
			</Tooltip>

			<Tooltip
				content={!fileUploadEnabled ? $i18n.t('You do not have permission to upload files') : ''}
				className="w-full"
			>
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl {!fileUploadEnabled
						? 'opacity-50'
						: ''}"
					on:click={() => {
						if (fileUploadEnabled) {
							uploadFilesHandler();
						}
					}}
				>
					<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
						<FileUp class="size-4" strokeWidth={2} />
					</span>
					<div class="line-clamp-1">{$i18n.t('Upload Files')}</div>
				</DropdownMenu.Item>
			</Tooltip>

			{#if $config?.features?.enable_google_drive_integration}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
					on:click={() => {
						uploadGoogleDriveHandler();
					}}
				>
					<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
						<GoogleDrive className="size-4" />
					</span>
					<div class="line-clamp-1">{$i18n.t('Google Drive')}</div>
				</DropdownMenu.Item>
			{/if}

			{#if $config?.features?.enable_onedrive_integration}
				<DropdownMenu.Item
					class="flex gap-2 items-center px-3 py-2 text-sm font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
					on:click={() => {
						uploadOneDriveHandler();
					}}
				>
					<span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-gray-600 dark:bg-gray-700/60 dark:text-gray-300">
						<OneDrive className="size-4" />
					</span>
					<div class="line-clamp-1">{$i18n.t('OneDrive')}</div>
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Content>
	</div>
</Dropdown>
