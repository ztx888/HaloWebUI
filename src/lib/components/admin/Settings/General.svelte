<script lang="ts">
	import { getWebhookUrl, updateWebhookUrl } from '$lib/apis';
	import { getAdminConfig, updateAdminConfig } from '$lib/apis/auths';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { WEBUI_BUILD_HASH, WEBUI_VERSION } from '$lib/constants';
	import { config, showChangelog } from '$lib/stores';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let updateAvailable = null;
	let currentVersion = WEBUI_VERSION;
	let version = {
		current: '',
		latest: ''
	};

	let adminConfig = null;
	let webhookUrl = '';

	// 存储初始状态用于对比
	let initialAdminConfig = null;
	let initialWebhookUrl = '';

	// 追踪保存状态
	let isSaving = false;

	type DirtySections = {
		security: boolean;
		features: boolean;
		system: boolean;
	};

	const EMPTY_DIRTY_SECTIONS: DirtySections = {
		security: false,
		features: false,
		system: false
	};

	let dirtySections: DirtySections = { ...EMPTY_DIRTY_SECTIONS };

	$: currentVersion = $config?.version ?? WEBUI_VERSION;

	// 定义区块字段映射
	const sectionFields = {
		security: ['DEFAULT_USER_ROLE', 'ENABLE_SIGNUP', 'SHOW_ADMIN_DETAILS', 'ENABLE_API_KEY', 'ENABLE_API_KEY_ENDPOINT_RESTRICTIONS', 'API_KEY_ALLOWED_ENDPOINTS', 'JWT_EXPIRES_IN'],
		features: ['ENABLE_CHANNELS', 'ENABLE_USER_WEBHOOKS'],
		system: ['WEBUI_URL']
	};

	const cloneConfig = (value: typeof adminConfig) => JSON.parse(JSON.stringify(value));

	// 计算各区块的 dirty 状态
	const getDirtySections = (
		currentAdminConfig: typeof adminConfig,
		currentInitialAdminConfig: typeof initialAdminConfig,
		currentWebhookUrl: string,
		currentInitialWebhookUrl: string
	): DirtySections => {
		if (!currentInitialAdminConfig || !currentAdminConfig) return { ...EMPTY_DIRTY_SECTIONS };

		const dirty: DirtySections = { ...EMPTY_DIRTY_SECTIONS };

		for (const [section, fields] of Object.entries(sectionFields)) {
			for (const field of fields) {
				if (
					JSON.stringify(currentAdminConfig[field]) !==
					JSON.stringify(currentInitialAdminConfig[field])
				) {
					dirty[section as keyof DirtySections] = true;
					break;
				}
			}
		}

		if (currentWebhookUrl !== currentInitialWebhookUrl) {
			dirty.system = true;
		}

		return dirty;
	};

	// 响应式计算
	$: dirtySections = getDirtySections(
		adminConfig,
		initialAdminConfig,
		webhookUrl,
		initialWebhookUrl
	);

	// 恢复到初始状态（按区块）
	const resetSectionChanges = (section: keyof DirtySections) => {
		if (!initialAdminConfig || !adminConfig) return;

		const fields = sectionFields[section];
		if (fields) {
			for (const field of fields) {
				adminConfig[field] = JSON.parse(JSON.stringify(initialAdminConfig[field]));
			}
			adminConfig = adminConfig;
		}

		if (section === 'system') {
			webhookUrl = initialWebhookUrl;
		}
	};

	// 恢复全部
	const resetChanges = () => {
		if (initialAdminConfig) {
			adminConfig = cloneConfig(initialAdminConfig);
		}
		webhookUrl = initialWebhookUrl;
	};

	const checkForVersionUpdates = async () => {
		updateAvailable = null;
		version = {
			current: currentVersion,
			latest: currentVersion
		};
		updateAvailable = false;
	};

	const updateHandler = async () => {
		isSaving = true;

		try {
			const newWebhookUrl = await updateWebhookUrl(localStorage.token, webhookUrl);
			webhookUrl = newWebhookUrl;
			initialWebhookUrl = newWebhookUrl;

			const res = await updateAdminConfig(localStorage.token, adminConfig);

			if (res) {
				initialAdminConfig = cloneConfig(adminConfig);
				await saveHandler?.();
			} else {
				toast.error($i18n.t('Failed to update settings'));
			}
		} finally {
			isSaving = false;
		}
	};

	onMount(async () => {
		checkForVersionUpdates();

		await Promise.all([
			(async () => {
				adminConfig = await getAdminConfig(localStorage.token);
				initialAdminConfig = cloneConfig(adminConfig);
			})(),

			(async () => {
				webhookUrl = await getWebhookUrl(localStorage.token);
				initialWebhookUrl = webhookUrl;
			})()
		]);
	});
</script>

<form
	class="flex h-full min-h-0 flex-col text-sm"
	on:submit|preventDefault={async () => {
		await updateHandler();
	}}
>
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		{#if adminConfig !== null}
			<div class="max-w-6xl mx-auto">
			<div class="space-y-6 pb-4">
			<section
				class="glass-section p-5 space-y-4"
			>
				<!-- Row 1: Identity + inline links -->
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<!-- Left: Logo + Name + Version + Badge -->
					<div class="flex items-center gap-3 min-w-0 flex-wrap">
						<div
							class="glass-icon-badge bg-gray-100 dark:bg-gray-800/50"
						>
							<svg
								width="18"
								height="18"
								viewBox="0 0 120 120"
								xmlns="http://www.w3.org/2000/svg"
							>
								<path
									d="M60 17 A43 43 0 1 1 17 60"
									fill="none"
									stroke="currentColor"
									stroke-width="14"
									stroke-linecap="round"
									class="text-gray-500 dark:text-gray-400"
								/>
								<circle cx="60" cy="60" r="13" fill="currentColor" class="text-gray-500 dark:text-gray-400" />
							</svg>
						</div>
						<div class="flex items-center gap-2 flex-wrap">
							<span
								class="text-base font-semibold text-gray-800 dark:text-gray-100 whitespace-nowrap"
							>
								Halo WebUI
							</span>
							<Tooltip content={WEBUI_BUILD_HASH}>
								<span
									class="text-sm font-medium text-gray-400 dark:text-gray-500 whitespace-nowrap"
									>V{currentVersion}</span
								>
							</Tooltip>
							<span
								class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-600 dark:bg-amber-900/25 dark:text-amber-400 leading-none"
							>
								测试版
							</span>
						</div>
					</div>

					<!-- Right: Inline quick links -->
					<div class="flex items-center gap-1 text-xs shrink-0">
						<a
							href="https://docs.openwebui.cn/"
							target="_blank"
							class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/15 transition-colors"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="w-3.5 h-3.5"
							>
								<path
									d="M10.75 16.82A7.462 7.462 0 0115 15.5c.71 0 1.396.098 2.046.282A.75.75 0 0018 15.06V4.94a.75.75 0 00-.546-.721A9.006 9.006 0 0015 3.75a8.98 8.98 0 00-4.25 1.065v12.005zM9.25 4.815A8.98 8.98 0 005 3.75c-.85 0-1.673.118-2.454.34A.75.75 0 002 4.94v10.12a.75.75 0 00.954.721A7.506 7.506 0 015 15.5c1.579 0 3.042.487 4.25 1.32V4.815z"
								/>
							</svg>
							{$i18n.t('Documentation')}
						</a>
						<span class="text-gray-300 dark:text-gray-600 select-none">·</span>
						<a
							href="https://github.com/ztx888/HaloWebUI"
							target="_blank"
							class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700/40 transition-colors"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 24 24"
								fill="currentColor"
								class="w-3.5 h-3.5"
							>
								<path
									fill-rule="evenodd"
									d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
									clip-rule="evenodd"
								/>
							</svg>
							GitHub
						</a>
						<span class="text-gray-300 dark:text-gray-600 select-none">·</span>
						<a
							href="https://github.com/ztx888/HaloWebUI/issues"
							target="_blank"
							class="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-gray-500 dark:text-gray-400 hover:text-orange-600 dark:hover:text-orange-400 hover:bg-orange-50 dark:hover:bg-orange-900/15 transition-colors"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="w-3.5 h-3.5"
							>
								<path
									fill-rule="evenodd"
									d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z"
									clip-rule="evenodd"
								/>
							</svg>
							{$i18n.t('Feedback')}
						</a>
					</div>
				</div>

				<!-- Row 2: Action buttons -->
				<div
					class="glass-item px-4 py-3 flex items-center gap-2"
				>
					<button
						class="h-8 px-3 text-xs font-medium rounded-lg transition-all active:scale-[0.98] flex items-center gap-1.5 whitespace-nowrap {updateAvailable
							? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900 shadow-sm hover:opacity-90'
							: 'text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'}"
						on:click={() => checkForVersionUpdates()}
						type="button"
					>
						{#if updateAvailable === null}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="w-3.5 h-3.5"
							>
								<path
									fill-rule="evenodd"
									d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
									clip-rule="evenodd"
								/>
							</svg>
							{$i18n.t('Check for updates')}
						{:else if updateAvailable}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="w-3.5 h-3.5"
							>
								<path
									d="M10.75 2.75a.75.75 0 00-1.5 0v8.614L6.295 8.235a.75.75 0 10-1.09 1.03l4.25 4.5a.75.75 0 001.09 0l4.25-4.5a.75.75 0 00-1.09-1.03l-2.955 3.129V2.75z"
								/>
								<path
									d="M3.5 12.75a.75.75 0 00-1.5 0v2.5A2.75 2.75 0 004.75 18h10.5A2.75 2.75 0 0018 15.25v-2.5a.75.75 0 00-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5z"
								/>
							</svg>
							{$i18n.t('Update to v{{version}}', { version: version.latest })}
						{:else}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="w-3.5 h-3.5"
							>
								<path
									fill-rule="evenodd"
									d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z"
									clip-rule="evenodd"
								/>
							</svg>
							{$i18n.t('Up to date')}
						{/if}
					</button>
					<button
						class="h-8 px-3 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-all active:scale-[0.98] flex items-center gap-1.5 whitespace-nowrap"
						on:click={() => showChangelog.set(true)}
						type="button"
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 20 20"
							fill="currentColor"
							class="w-3.5 h-3.5"
						>
							<path
								fill-rule="evenodd"
								d="M10 2c-1.716 0-3.408.106-5.07.31C3.806 2.45 3 3.414 3 4.517V17.25a.75.75 0 001.075.676L10 15.082l5.925 2.844A.75.75 0 0017 17.25V4.517c0-1.103-.806-2.068-1.93-2.207A41.403 41.403 0 0010 2z"
								clip-rule="evenodd"
							/>
						</svg>
						{$i18n.t("See what's new")}
					</button>
				</div>
			</section>
				<!-- ====== Identity & Security ====== -->
				<section
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {dirtySections.security
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="flex items-center gap-3">
							<div class="glass-icon-badge bg-violet-50 dark:bg-violet-950/30">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="currentColor"
									class="size-[18px] text-violet-500 dark:text-violet-400"
								>
									<path
										fill-rule="evenodd"
										d="M12 1.5a5.25 5.25 0 00-5.25 5.25v3a3 3 0 00-3 3v6.75a3 3 0 003 3h10.5a3 3 0 003-3v-6.75a3 3 0 00-3-3v-3c0-2.9-2.35-5.25-5.25-5.25zm3.75 8.25v-3a3.75 3.75 0 10-7.5 0v3h7.5z"
										clip-rule="evenodd"
									/>
								</svg>
							</div>
							<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
								{$i18n.t('Identity & Security')}
							</div>
						</div>

						<InlineDirtyActions
							dirty={dirtySections.security}
							saving={isSaving}
							on:reset={() => resetSectionChanges('security')}
						/>
					</div>

					<div class="space-y-5">
						<div class="space-y-3">
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('User Registration')}
							</div>
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="glass-item px-4 py-3"
								>
									<div class="flex items-center justify-between">
										<div class="text-sm font-medium">{$i18n.t('Default User Role')}</div>
										<HaloSelect
											bind:value={adminConfig.DEFAULT_USER_ROLE}
											placeholder={$i18n.t('Select a role')}
											options={[
												{ value: 'pending', label: $i18n.t('pending') },
												{ value: 'user', label: $i18n.t('user') },
												{ value: 'admin', label: $i18n.t('admin') }
											]}
											className="w-fit"
										/>
									</div>
								</div>
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">{$i18n.t('Enable New Sign Ups')}</div>
									<Switch bind:state={adminConfig.ENABLE_SIGNUP} />
								</div>
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">
										{$i18n.t('Show Admin Details in Account Pending Overlay')}
									</div>
									<Switch bind:state={adminConfig.SHOW_ADMIN_DETAILS} />
								</div>
							</div>
						</div>

						<div class="space-y-3">
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('API Keys')}
							</div>
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">{$i18n.t('Enable API Key')}</div>
									<Switch bind:state={adminConfig.ENABLE_API_KEY} />
								</div>
								{#if adminConfig?.ENABLE_API_KEY}
									<div
										class="flex items-center justify-between glass-item px-4 py-3"
									>
										<div class="text-sm font-medium">
											{$i18n.t('API Key Endpoint Restrictions')}
										</div>
										<Switch bind:state={adminConfig.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS} />
									</div>
								{/if}
							</div>

							{#if adminConfig?.ENABLE_API_KEY && adminConfig?.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS}
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('Allowed Endpoints')}
									</div>
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										type="text"
										placeholder={`e.g.) /api/v1/messages, /api/v1/channels`}
										bind:value={adminConfig.API_KEY_ALLOWED_ENDPOINTS}
									/>
									<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
										<a
											href="https://docs.openwebui.com/getting-started/api-endpoints"
											target="_blank"
											class="text-gray-500 dark:text-gray-400 font-medium underline hover:text-blue-500"
										>
											{$i18n.t('To learn more about available endpoints, visit our documentation.')}
										</a>
									</div>
								</div>
							{/if}
						</div>

						<div class="space-y-3">
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('Session Security')}
							</div>
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('JWT Expiration')}
								</div>
								<input
									class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
									type="text"
									placeholder={`e.g.) "30m","1h", "10d".`}
									bind:value={adminConfig.JWT_EXPIRES_IN}
								/>
								<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('Valid time units:')}
									<span class="text-gray-500 dark:text-gray-400 font-medium">
										{$i18n.t("'s', 'm', 'h', 'd', 'w' or '-1' for no expiration.")}
									</span>
								</div>

								{#if adminConfig.JWT_EXPIRES_IN === '-1'}
									<div
										class="mt-3 p-3 glass-warning"
									>
										<div class="flex items-start gap-2.5">
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="size-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
												/>
											</svg>
											<div class="text-xs leading-relaxed text-amber-700 dark:text-amber-300">
												<a
													href="https://docs.openwebui.com/getting-started/env-configuration#jwt_expires_in"
													target="_blank"
													class="underline hover:text-amber-800 dark:hover:text-amber-100"
												>
													{$i18n.t('No expiration can pose security risks.')}
												</a>
											</div>
										</div>
									</div>
								{/if}
							</div>
						</div>
					</div>
				</section>

				<!-- ====== Application Features ====== -->
				<section
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {dirtySections.features
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="flex items-center gap-3">
							<div class="glass-icon-badge bg-emerald-50 dark:bg-emerald-950/30">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="currentColor"
									class="size-[18px] text-emerald-500 dark:text-emerald-400"
								>
									<path
										fill-rule="evenodd"
										d="M3 6a3 3 0 013-3h2.25a3 3 0 013 3v2.25a3 3 0 01-3 3H6a3 3 0 01-3-3V6zm9.75 0a3 3 0 013-3H18a3 3 0 013 3v2.25a3 3 0 01-3 3h-2.25a3 3 0 01-3-3V6zM3 15.75a3 3 0 013-3h2.25a3 3 0 013 3V18a3 3 0 01-3 3H6a3 3 0 01-3-3v-2.25zm9.75 0a3 3 0 013-3H18a3 3 0 013 3V18a3 3 0 01-3 3h-2.25a3 3 0 01-3-3v-2.25z"
										clip-rule="evenodd"
									/>
								</svg>
							</div>
							<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
								{$i18n.t('App Features')}
							</div>
						</div>

						<InlineDirtyActions
							dirty={dirtySections.features}
							saving={isSaving}
							on:reset={() => resetSectionChanges('features')}
						/>
					</div>

					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
							{$i18n.t('Features')}
						</div>
						<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
							<div
								class="flex items-center justify-between glass-item px-4 py-3"
							>
								<div class="text-sm font-medium">
									{$i18n.t('Enable Channels')}
									<span
										class="ml-1 text-xs px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded"
									>
										{$i18n.t('Beta')}
									</span>
								</div>
								<Switch bind:state={adminConfig.ENABLE_CHANNELS} />
							</div>
							<div
								class="flex items-center justify-between glass-item px-4 py-3"
							>
								<div class="text-sm font-medium">{$i18n.t('Enable User Webhooks')}</div>
								<Switch bind:state={adminConfig.ENABLE_USER_WEBHOOKS} />
							</div>
						</div>
					</div>
				</section>

				<!-- ====== System Settings ====== -->
				<section
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {dirtySections.system
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="flex items-center gap-3">
							<div class="glass-icon-badge bg-blue-50 dark:bg-blue-950/30">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="currentColor"
									class="size-[18px] text-blue-500 dark:text-blue-400"
								>
									<path
										fill-rule="evenodd"
										d="M2.25 6a3 3 0 013-3h13.5a3 3 0 013 3v12a3 3 0 01-3 3H5.25a3 3 0 01-3-3V6zm3.97.97a.75.75 0 011.06 0l2.25 2.25a.75.75 0 010 1.06l-2.25 2.25a.75.75 0 01-1.06-1.06l1.72-1.72-1.72-1.72a.75.75 0 010-1.06zm4.28 4.28a.75.75 0 000 1.5h3a.75.75 0 000-1.5h-3z"
										clip-rule="evenodd"
									/>
								</svg>
							</div>
							<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
								{$i18n.t('System Connections')}
							</div>
						</div>

						<InlineDirtyActions
							dirty={dirtySections.system}
							saving={isSaving}
							on:reset={() => resetSectionChanges('system')}
						/>
					</div>

					<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
						<div
							class="glass-item p-4"
						>
							<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('WebUI URL')}</div>
							<input
								class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
								type="text"
								placeholder={`e.g.) "http://localhost:3000"`}
								bind:value={adminConfig.WEBUI_URL}
							/>
							<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t(
									'Enter the public URL of your WebUI. This URL will be used to generate links in the notifications.'
								)}
							</div>
						</div>

						<div
							class="glass-item p-4"
						>
							<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">{$i18n.t('Webhook URL')}</div>
							<input
								class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
								type="text"
								placeholder={`https://example.com/webhook`}
								bind:value={webhookUrl}
							/>
							<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t('Configure webhook endpoint for system notifications')}
							</div>
						</div>
					</div>
				</section>
				</div>
			</div>
		{/if}
	</div>

</form>
