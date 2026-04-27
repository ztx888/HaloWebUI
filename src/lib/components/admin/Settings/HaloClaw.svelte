<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { onMount, getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import { toast } from 'svelte-sonner';
	import { getModels } from '$lib/apis';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import {
		getModelLegacyIds,
		getModelSelectionId,
		resolveModelSelectionId
	} from '$lib/utils/model-identity';
	import {
		getHaloClawConfig,
		updateHaloClawConfig,
		getGateways,
		createGateway,
		updateGateway,
		toggleGateway,
		deleteGateway
	} from '$lib/apis/haloclaw';
	import { revealExpandedSection } from '$lib/utils/expanded-section-scroll';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import GatewayModal from './HaloClaw/GatewayModal.svelte';
	import ExternalUsersModal from './HaloClaw/ExternalUsersModal.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';

	const i18n: Writable<any> = getContext('i18n');
	export let saveHandler: Function;

	const DRAFT_GATEWAY_PREFIX = 'draft:';

	let gateways: any[] = [];
	let models: any[] = [];
	let haloclawEnabled = false;
	let defaultModel = '';
	let maxHistory = 20;
	let rateLimit = 10;
	let ready = false;

	let showGatewayModal = false;
	let editingGateway: any = null;
	let showUsersModal = false;
	let usersGateway: any = null;
	let expandedSections = { main: true, gateways: true };
	let sectionEl_main: HTMLElement;
	let sectionEl_gateways: HTMLElement;
	let initialMainSnapshot: any = null;
	let initialGatewaysState: any[] = [];
	let initialGatewaysSnapshot: any[] = [];
	let mainDirty = false;
	let gatewaysDirty = false;
	let mainSaving = false;
	let gatewaysSaving = false;

	const createDraftGatewayId = () =>
		`${DRAFT_GATEWAY_PREFIX}${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

	const isDraftGateway = (id: string | null | undefined) =>
		String(id ?? '').startsWith(DRAFT_GATEWAY_PREFIX);

	const normalizeGatewayAccessPolicy = (accessPolicy: Record<string, any> | null | undefined) => ({
		dm_policy: accessPolicy?.dm_policy ?? 'open',
		allowlist: Array.isArray(accessPolicy?.allowlist)
			? accessPolicy.allowlist.filter(Boolean)
			: [],
		group_policy: accessPolicy?.group_policy ?? 'mention'
	});

	const sanitizeGatewayMeta = (meta: Record<string, any> | null | undefined) => {
		const next = cloneSettingsSnapshot(meta ?? {});
		delete next.running;
		return next;
	};

	const buildGatewayPayload = (gateway: any) => ({
		platform: gateway?.platform ?? 'telegram',
		name: gateway?.name ?? '',
		config: cloneSettingsSnapshot(gateway?.config ?? {}),
		default_model_id: resolveModelId(gateway?.default_model_id ?? '') || null,
		system_prompt: gateway?.system_prompt || null,
		access_policy: normalizeGatewayAccessPolicy(gateway?.access_policy),
		enabled: Boolean(gateway?.enabled),
		meta: sanitizeGatewayMeta(gateway?.meta)
	});

	const buildGatewaySnapshot = (gateway: any) => ({
		id: gateway?.id ?? '',
		...buildGatewayPayload(gateway)
	});

	const buildGatewaysSnapshot = (currentGateways: any[]) =>
		currentGateways.map((gateway) => buildGatewaySnapshot(gateway));

	const normalizeMainNumber = (value: unknown, fallback: number) => {
		const normalized = Number(value);
		return Number.isFinite(normalized) ? normalized : fallback;
	};

	const buildMainSnapshot = () => ({
		enabled: Boolean(haloclawEnabled),
		default_model: resolveModelId(defaultModel ?? ''),
		max_history: normalizeMainNumber(maxHistory, 20),
		rate_limit: normalizeMainNumber(rateLimit, 10)
	});

	const syncMainBaseline = () => {
		initialMainSnapshot = cloneSettingsSnapshot(buildMainSnapshot());
		mainDirty = false;
	};

	const syncGatewaysBaseline = () => {
		initialGatewaysState = cloneSettingsSnapshot(gateways);
		initialGatewaysSnapshot = buildGatewaysSnapshot(initialGatewaysState);
		gatewaysDirty = false;
	};

	const syncMainDirty = () => {
		mainDirty = !!(
			ready &&
			initialMainSnapshot &&
			!isSettingsSnapshotEqual(buildMainSnapshot(), initialMainSnapshot)
		);
	};

	const syncGatewaysDirty = () => {
		gatewaysDirty = !!(
			ready &&
			initialGatewaysSnapshot &&
			!isSettingsSnapshotEqual(buildGatewaysSnapshot(gateways), initialGatewaysSnapshot)
		);
	};

	const resolveModelId = (id: string) =>
		resolveModelSelectionId(models ?? [], id, { preserveAmbiguous: true }) || id;

	$: modelLabelById = new Map(
		(models ?? []).flatMap((m) => {
			const label = getModelChatDisplayName(m) || m.name || m.id;
			const selectionId = getModelSelectionId(m);
			return Array.from(new Set([m.id, selectionId, ...getModelLegacyIds(m)].filter(Boolean))).map(
				(id): [string, string] => [id, label]
			);
		})
	);
	const formatModelLabel = (id: string) => modelLabelById.get(id) || id;

	const platformIcons: Record<string, string> = {
		telegram: '\u2708\uFE0F',
		wechat_work: '\uD83D\uDCBC',
		feishu: '\uD83D\uDD37'
	};
	const platformLabels: Record<string, string> = {
		telegram: 'Telegram',
		wechat_work: 'WeChat Work',
		feishu: 'Feishu / Lark'
	};

	async function loadData() {
		const [loadedConfig, loadedGateways, allModels] = await Promise.all([
			getHaloClawConfig(localStorage.token),
			getGateways(localStorage.token),
			getModels(localStorage.token)
		]);

		haloclawEnabled = Boolean(loadedConfig?.enabled);
		models = allModels;
		defaultModel = resolveModelId(loadedConfig?.default_model ?? '');
		maxHistory = Number(loadedConfig?.max_history ?? 20);
		rateLimit = Number(loadedConfig?.rate_limit ?? 10);
		gateways = cloneSettingsSnapshot(loadedGateways ?? []);
		ready = true;
		syncMainBaseline();
		syncGatewaysBaseline();
	}

	async function saveMainChanges() {
		mainSaving = true;
		try {
			const res = await updateHaloClawConfig(localStorage.token, {
				enabled: haloclawEnabled,
				default_model: resolveModelId(defaultModel),
				max_history: Number(maxHistory),
				rate_limit: Number(rateLimit)
			});

			haloclawEnabled = Boolean(res?.enabled);
			defaultModel = resolveModelId(res?.default_model ?? '');
			maxHistory = Number(res?.max_history ?? maxHistory);
			rateLimit = Number(res?.rate_limit ?? rateLimit);
			syncMainBaseline();
			await saveHandler?.();
		} catch (err: any) {
			console.error(err);
			toast.error(err?.toString?.() || err?.detail || 'Error');
		} finally {
			mainSaving = false;
		}
	}

	const resetMainChanges = () => {
		if (!initialMainSnapshot) return;
		const next = cloneSettingsSnapshot(initialMainSnapshot);
		haloclawEnabled = Boolean(next.enabled);
		defaultModel = resolveModelId(next.default_model ?? '');
		maxHistory = Number(next.max_history ?? 20);
		rateLimit = Number(next.rate_limit ?? 10);
		syncMainDirty();
	};

	const resetGatewayChanges = () => {
		gateways = cloneSettingsSnapshot(initialGatewaysState);
		syncGatewaysDirty();
	};

	async function handleGatewaySubmit(event: CustomEvent) {
		const { id, data } = event.detail;
		const existingGateway = id ? gateways.find((gateway) => gateway.id === id) : null;
		const nextGateway = {
			...(existingGateway ? cloneSettingsSnapshot(existingGateway) : {}),
			id: id ?? createDraftGatewayId(),
			...cloneSettingsSnapshot(data),
			meta: {
				...sanitizeGatewayMeta(data?.meta),
				running: existingGateway?.meta?.running ?? false
			}
		};

		if (existingGateway) {
			gateways = gateways.map((gateway) => (gateway.id === id ? nextGateway : gateway));
			syncGatewaysDirty();
			return;
		}

		gateways = [nextGateway, ...gateways];
		syncGatewaysDirty();
	}

	async function handleToggle(gw: any) {
		gateways = gateways.map((gateway) =>
			gateway.id === gw.id ? { ...gateway, enabled: !gateway.enabled } : gateway
		);
		syncGatewaysDirty();
	}

	async function handleDelete(gw: any) {
		if (!confirm($i18n.t('Delete gateway "{{name}}"?', { name: gw.name }))) return;
		gateways = gateways.filter((gateway) => gateway.id !== gw.id);
		syncGatewaysDirty();
	}

	async function saveGatewayChanges() {
		gatewaysSaving = true;
		try {
			const initialGatewaysById = new Map(
				initialGatewaysState.map((gateway) => [gateway.id, gateway])
			);
			const currentGatewaysById = new Map(gateways.map((gateway) => [gateway.id, gateway]));

			const deletedGateways = initialGatewaysState.filter(
				(gateway) => !currentGatewaysById.has(gateway.id)
			);
			const createdGateways = gateways.filter((gateway) => isDraftGateway(gateway.id));
			const existingGateways = gateways.filter((gateway) => !isDraftGateway(gateway.id));

			for (const gateway of deletedGateways) {
				await deleteGateway(localStorage.token, gateway.id);
			}

			for (const gateway of createdGateways) {
				await createGateway(localStorage.token, buildGatewayPayload(gateway));
			}

			for (const gateway of existingGateways) {
				const initialGateway = initialGatewaysById.get(gateway.id);
				if (!initialGateway) continue;

				const initialPayload = buildGatewayPayload(initialGateway);
				const currentPayload = buildGatewayPayload(gateway);
				const enabledChanged = initialPayload.enabled !== currentPayload.enabled;
				const initialConfigSnapshot = { ...initialPayload, enabled: false };
				const currentConfigSnapshot = { ...currentPayload, enabled: false };
				const contentChanged = !isSettingsSnapshotEqual(
					currentConfigSnapshot,
					initialConfigSnapshot
				);

				if (contentChanged) {
					await updateGateway(localStorage.token, gateway.id, currentPayload);
				}

				if (enabledChanged) {
					await toggleGateway(localStorage.token, gateway.id, currentPayload.enabled);
				}
			}

			const latestGateways = await getGateways(localStorage.token);
			gateways = cloneSettingsSnapshot(latestGateways ?? []);
			syncGatewaysBaseline();
			await saveHandler?.();
		} catch (err: any) {
			console.error(err);
			toast.error(err?.toString?.() || err?.detail || 'Error');
		} finally {
			gatewaysSaving = false;
		}
	}

	onMount(loadData);
</script>

<GatewayModal
	bind:show={showGatewayModal}
	gateway={editingGateway}
	{models}
	on:submit={handleGatewaySubmit}
/>

<ExternalUsersModal
	bind:show={showUsersModal}
	gateway={usersGateway}
	globalDefaultModel={initialMainSnapshot?.default_model ?? defaultModel}
	{models}
/>

<div class="flex h-full min-h-0 flex-col text-sm">
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		<div class="max-w-6xl mx-auto space-y-6">
				<!-- Global Settings -->
				<section
					bind:this={sectionEl_main}
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {mainDirty
					? 'glass-section glass-section-dirty'
					: 'glass-section'}"
				>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<button
							type="button"
							class="flex min-w-0 flex-1 items-center justify-between gap-4 text-left"
							on:click={async () => {
								expandedSections.main = !expandedSections.main;
								if (expandedSections.main) {
									await revealExpandedSection(sectionEl_main);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div class="glass-icon-badge bg-amber-50 dark:bg-amber-950/30">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] text-amber-500 dark:text-amber-400"
									>
										<path
											fill-rule="evenodd"
											d="M14.615 1.595a.75.75 0 0 1 .359.852L12.982 9.75h7.268a.75.75 0 0 1 .548 1.262l-10.5 11.25a.75.75 0 0 1-1.272-.71l1.992-7.302H3.75a.75.75 0 0 1-.548-1.262l10.5-11.25a.75.75 0 0 1 .913-.143Z"
											clip-rule="evenodd"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100">HaloClaw</div>
							</div>
							<div class="flex items-center gap-3">
								<span
									role="button"
									tabindex="0"
									on:click|stopPropagation
									on:keydown|stopPropagation
								>
									<Switch bind:state={haloclawEnabled} on:change={syncMainDirty} />
								</span>
								<div
									class="transform transition-transform duration-200 {expandedSections.main
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</button>

						<InlineDirtyActions
							dirty={mainDirty}
							saving={mainSaving}
							saveAsSubmit={false}
							on:reset={resetMainChanges}
							on:save={saveMainChanges}
						/>
					</div>

					{#if expandedSections.main}
						<div transition:slide={{ duration: 200, easing: quintOut }} class="space-y-3">
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<!-- Default Model -->
								<div
									class="glass-item px-4 py-3"
								>
									<div class="flex items-center justify-between">
										<div class="text-sm font-medium">{$i18n.t('Default Model')}</div>
										<div class="w-[12rem]">
											<HaloSelect
												bind:value={defaultModel}
												on:change={syncMainDirty}
												searchEnabled={true}
												placeholder={$i18n.t('Select a model')}
												searchPlaceholder={$i18n.t('Search a model')}
												noResultsText={$i18n.t('No results found')}
												options={[
													{ value: '', label: $i18n.t('Not set') },
													...(models ?? []).map((m) => ({
														value: getModelSelectionId(m),
														label: getModelChatDisplayName(m) || m.name || m.id
													}))
												]}
											/>
										</div>
									</div>
								</div>

								<!-- Max History -->
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">{$i18n.t('History Messages')}</div>
									<input
										class="w-20 text-sm bg-transparent outline-hidden text-right"
										type="number"
										min="1"
										max="100"
										bind:value={maxHistory}
										on:input={syncMainDirty}
									/>
								</div>

								<!-- Rate Limit -->
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<Tooltip content={$i18n.t('Messages per minute per user')}>
										<div class="text-sm font-medium">{$i18n.t('Rate Limit')}</div>
									</Tooltip>
									<input
										class="w-20 text-sm bg-transparent outline-hidden text-right"
										type="number"
										min="1"
										max="100"
										bind:value={rateLimit}
										on:input={syncMainDirty}
									/>
								</div>
							</div>
						</div>
					{/if}
				</section>

				<!-- Gateways -->
				<section
					bind:this={sectionEl_gateways}
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {gatewaysDirty
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<button
							type="button"
							class="flex min-w-0 flex-1 items-center justify-between gap-4 text-left"
							on:click={async () => {
								expandedSections.gateways = !expandedSections.gateways;
								if (expandedSections.gateways) {
									await revealExpandedSection(sectionEl_gateways);
								}
							}}
						>
							<div class="flex items-center gap-3">
								<div class="glass-icon-badge bg-indigo-50 dark:bg-indigo-950/30">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] text-indigo-500 dark:text-indigo-400"
									>
										<path
											d="M3.478 2.404a.75.75 0 0 0-.926.941l2.432 7.905H13.5a.75.75 0 0 1 0 1.5H4.984l-2.432 7.905a.75.75 0 0 0 .926.94 60.519 60.519 0 0 0 18.445-8.986.75.75 0 0 0 0-1.218A60.517 60.517 0 0 0 3.478 2.404Z"
										/>
									</svg>
								</div>
								<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
									{$i18n.t('Gateways')}
									{#if gateways.length > 0}
										<span class="text-sm text-gray-400 ml-1">({gateways.length})</span>
									{/if}
								</div>
							</div>
							<div class="flex items-center gap-3">
								<button
									type="button"
									class="px-3 py-1 text-xs font-medium bg-blue-500 hover:bg-blue-600 text-white rounded-full transition"
									on:click|stopPropagation={() => {
										editingGateway = null;
										showGatewayModal = true;
									}}
								>
									+ {$i18n.t('Add')}
								</button>
								<div
									class="transform transition-transform duration-200 {expandedSections.gateways
										? 'rotate-180'
										: ''}"
								>
									<ChevronDown className="size-5 text-gray-400" />
								</div>
							</div>
						</button>

						<InlineDirtyActions
							dirty={gatewaysDirty}
							saving={gatewaysSaving}
							saveAsSubmit={false}
							on:reset={resetGatewayChanges}
							on:save={saveGatewayChanges}
						/>
					</div>

					{#if expandedSections.gateways}
						<div transition:slide={{ duration: 200, easing: quintOut }} class="space-y-3">
							{#if gateways.length === 0}
								<div class="text-center py-6 text-gray-400">
									{$i18n.t('No gateways configured. Click "Add" to create one.')}
								</div>
							{:else}
								{#each gateways as gw}
									<div
										class="flex items-center justify-between glass-item px-4 py-3"
									>
										<div class="flex items-center gap-3">
											<div
												class="w-2.5 h-2.5 rounded-full {gw.meta?.running
													? 'bg-green-500'
													: gw.enabled
														? 'bg-yellow-500'
														: 'bg-gray-400'}"
											/>
											<div>
												<div class="font-medium flex items-center gap-1.5">
													<span>{platformIcons[gw.platform] || ''}</span>
													{gw.name}
												</div>
												<div class="text-xs text-gray-400">
													{platformLabels[gw.platform] || gw.platform}
													{#if gw.default_model_id}
														&middot; {formatModelLabel(gw.default_model_id)}
													{/if}
												</div>
											</div>
										</div>

										<div class="flex items-center gap-2">
											<Switch state={gw.enabled} on:change={() => handleToggle(gw)} />
											{#if !isDraftGateway(gw.id)}
												<Tooltip content={$i18n.t('Users & Logs')}>
													<button
														type="button"
														class="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
														on:click={() => {
															usersGateway = gw;
															showUsersModal = true;
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 20 20"
															fill="currentColor"
															class="w-4 h-4"
														>
															<path
																d="M10 9a3 3 0 100-6 3 3 0 000 6zM6 8a2 2 0 11-4 0 2 2 0 014 0zM1.49 15.326a.78.78 0 01-.358-.442 3 3 0 014.308-3.516 6.484 6.484 0 00-1.905 3.959c-.023.222-.014.442.025.654a4.97 4.97 0 01-2.07-.655zM16.44 15.98a4.97 4.97 0 002.07-.654.78.78 0 00.357-.442 3 3 0 00-4.308-3.517 6.484 6.484 0 011.907 3.96 2.32 2.32 0 01-.026.654zM18 8a2 2 0 11-4 0 2 2 0 014 0zM5.304 16.19a.844.844 0 01-.277-.71 5 5 0 019.947 0 .843.843 0 01-.277.71A6.975 6.975 0 0110 18a6.974 6.974 0 01-4.696-1.81z"
															/>
														</svg>
													</button>
												</Tooltip>
											{/if}
											<button
												type="button"
												class="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
												on:click={() => {
													editingGateway = gw;
													showGatewayModal = true;
												}}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 20 20"
													fill="currentColor"
													class="w-4 h-4"
												>
													<path
														d="M2.695 14.763l-1.262 3.154a.5.5 0 00.65.65l3.155-1.262a4 4 0 001.343-.885L17.5 5.5a2.121 2.121 0 00-3-3L3.58 13.42a4 4 0 00-.885 1.343z"
													/>
												</svg>
											</button>
											<button
												type="button"
												class="p-1.5 text-gray-400 hover:text-red-500 transition"
												on:click={() => handleDelete(gw)}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 20 20"
													fill="currentColor"
													class="w-4 h-4"
												>
													<path
														fill-rule="evenodd"
														d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5z"
														clip-rule="evenodd"
													/>
												</svg>
											</button>
										</div>
									</div>
								{/each}
							{/if}
						</div>
					{/if}
				</section>
		</div>
	</div>

</div>
