<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { getContext, createEventDispatcher } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import { getModelSelectionId, resolveModelSelectionId } from '$lib/utils/model-identity';

	const i18n: Writable<any> = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let gateway: any = null;
	export let models: any[] = [];

	let platform = gateway?.platform ?? 'telegram';
	let name = gateway?.name ?? '';
	let defaultModelId = gateway?.default_model_id ?? '';
	let systemPrompt = gateway?.system_prompt ?? '';
	let dmPolicy = gateway?.access_policy?.dm_policy ?? 'open';
	let allowlist = gateway?.access_policy?.allowlist?.join(', ') ?? '';
	let groupPolicy = gateway?.access_policy?.group_policy ?? 'mention';

	// --- Telegram config ---
	let botToken = gateway?.config?.bot_token ?? '';

	// --- WeChat Work config ---
	let corpId = gateway?.config?.corp_id ?? '';
	let agentId = gateway?.config?.agent_id ?? '';
	let wxSecret = gateway?.config?.secret ?? '';
	let wxToken = gateway?.config?.token ?? '';
	let aesKey = gateway?.config?.aes_key ?? '';

	// --- Feishu config ---
	let appId = gateway?.config?.app_id ?? '';
	let appSecret = gateway?.config?.app_secret ?? '';
	let verificationToken = gateway?.config?.verification_token ?? '';
	let encryptKey = gateway?.config?.encrypt_key ?? '';

	// --- Tool config (stored in meta) ---
	const BUILTIN_TOOLS = [
		{ id: 'builtin:web', label: '联网搜索' },
		{ id: 'builtin:time', label: '时间工具' },
		{ id: 'builtin:memory', label: '记忆' },
		{ id: 'builtin:knowledge', label: '知识库' },
		{ id: 'builtin:images', label: '图片生成' }
	];
	let toolIds: string[] = gateway?.meta?.tool_ids ?? [];
	let maxToolRounds: number = gateway?.meta?.max_tool_rounds ?? 5;

	// Webhook URL for display
	let webhookCopied = false;
	$: webhookUrl =
		gateway?.id && platform !== 'telegram'
			? `${window.location.origin}/api/v1/haloclaw/webhook/${platform}/${gateway.id}`
			: '';

	const resolveModelId = (id: string) =>
		resolveModelSelectionId(models ?? [], id, { preserveAmbiguous: true }) || id;

	const resetForm = (gw: any) => {
		platform = gw?.platform ?? 'telegram';
		name = gw?.name ?? '';
		defaultModelId = resolveModelId(gw?.default_model_id ?? '');
		systemPrompt = gw?.system_prompt ?? '';
		dmPolicy = gw?.access_policy?.dm_policy ?? 'open';
		allowlist = gw?.access_policy?.allowlist?.join(', ') ?? '';
		groupPolicy = gw?.access_policy?.group_policy ?? 'mention';

		botToken = gw?.config?.bot_token ?? '';

		corpId = gw?.config?.corp_id ?? '';
		agentId = gw?.config?.agent_id ?? '';
		wxSecret = gw?.config?.secret ?? '';
		wxToken = gw?.config?.token ?? '';
		aesKey = gw?.config?.aes_key ?? '';

		appId = gw?.config?.app_id ?? '';
		appSecret = gw?.config?.app_secret ?? '';
		verificationToken = gw?.config?.verification_token ?? '';
		encryptKey = gw?.config?.encrypt_key ?? '';

		toolIds = gw?.meta?.tool_ids ?? [];
		maxToolRounds = gw?.meta?.max_tool_rounds ?? 5;

		webhookCopied = false;
	};

	// When the modal opens (or the gateway changes while open), hydrate the form from the gateway.
	// Without this, opening "Edit" after previously opening "Add" shows a blank "new" form.
	$: if (show) {
		resetForm(gateway);
	}

	const platformLabels: Record<string, string> = {
		telegram: 'Telegram',
		wechat_work: 'WeChat Work',
		feishu: 'Feishu / Lark'
	};

	function buildConfig(): Record<string, any> {
		if (platform === 'telegram') {
			return { bot_token: botToken };
		} else if (platform === 'wechat_work') {
			return {
				corp_id: corpId,
				agent_id: agentId,
				secret: wxSecret,
				token: wxToken,
				aes_key: aesKey
			};
		} else if (platform === 'feishu') {
			return {
				app_id: appId,
				app_secret: appSecret,
				verification_token: verificationToken,
				encrypt_key: encryptKey
			};
		}
		return {};
	}

	function submitHandler() {
		const data: any = {
			platform,
			name,
			config: buildConfig(),
			default_model_id: resolveModelId(defaultModelId) || null,
			system_prompt: systemPrompt || null,
			access_policy: {
				dm_policy: dmPolicy,
				allowlist: allowlist
					? allowlist
							.split(',')
							.map((s: string) => s.trim())
							.filter(Boolean)
					: [],
				group_policy: groupPolicy
			},
			meta: {
				...(gateway?.meta ?? {}),
				tool_ids: toolIds,
				max_tool_rounds: Math.max(1, Math.min(10, maxToolRounds))
			},
			enabled: gateway?.enabled ?? false
		};

		dispatch('submit', { id: gateway?.id, data });
		show = false;
	}

	function copyWebhookUrl() {
		navigator.clipboard.writeText(webhookUrl);
		webhookCopied = true;
		setTimeout(() => (webhookCopied = false), 2000);
	}
</script>

<Modal size="md" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-100 px-5 pt-4 pb-2">
			<div class="text-lg font-medium self-center font-primary">
				{gateway ? $i18n.t('Edit Gateway') : $i18n.t('Add Gateway')}
			</div>
			<button class="self-center" on:click={() => (show = false)} type="button">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="w-5 h-5"
				>
					<path
						d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
					/>
				</svg>
			</button>
		</div>

		<div class="flex flex-col w-full px-5 pb-4 dark:text-gray-200">
			<form class="flex flex-col w-full" on:submit|preventDefault={submitHandler}>
				<div class="space-y-3">
					<!-- Platform -->
					<div>
						<label class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
							{$i18n.t('Platform')}
						</label>
						<HaloSelect
							bind:value={platform}
							options={Object.entries(platformLabels).map(([value, label]) => ({ value, label }))}
							disabled={!!gateway}
							className="w-full"
						/>
					</div>

					<!-- Name -->
					<div>
						<label class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
							{$i18n.t('Name')}
						</label>
						<input
							class="w-full"
							type="text"
							placeholder={platform === 'telegram'
								? $i18n.t('My Telegram Bot')
								: platform === 'wechat_work'
									? $i18n.t('WeChat Work Bot')
									: $i18n.t('Feishu Bot')}
							bind:value={name}
							required
						/>
					</div>

					<!-- Webhook URL (shown for existing wechat_work / feishu gateways) -->
					{#if webhookUrl}
						<div>
							<label class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
								{$i18n.t('Webhook URL')}
							</label>
							<div class="flex items-center gap-2">
								<input
									class="w-full rounded-lg text-xs bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 px-3 py-2 outline-hidden font-mono"
									type="text"
									value={webhookUrl}
									readonly
								/>
								<button
									class="px-2 py-2 text-xs rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition shrink-0"
									type="button"
									on:click={copyWebhookUrl}
								>
									{webhookCopied ? $i18n.t('Copied!') : $i18n.t('Copy')}
								</button>
							</div>
							<p class="text-xs text-gray-500 mt-1">
								{$i18n.t("Configure this URL in your platform's app console.")}
							</p>
						</div>
					{/if}

					<!-- Platform-specific config fields -->
					{#if platform === 'telegram'}
						<!-- Telegram: Bot Token -->
						<div>
							<label class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
								{$i18n.t('Bot Token')}
							</label>
							<input
								class="w-full"
								type="text"
								placeholder="123456:ABC-DEF..."
								bind:value={botToken}
								required
							/>
							<p class="text-xs text-gray-500 mt-1">
								{$i18n.t('Get from')}
								<a
									href="https://t.me/BotFather"
									target="_blank"
									rel="noopener"
									class="text-blue-500 hover:underline">@BotFather</a
								>
							</p>
						</div>
					{:else if platform === 'wechat_work'}
						<!-- WeChat Work: Corp ID, Agent ID, Secret, Token, AES Key -->
						<div class="grid grid-cols-2 gap-3">
							<div>
								<label class="text-xs text-gray-500 mb-1 block">Corp ID</label>
								<input
									class="w-full"
									type="text"
									placeholder="ww1234567890"
									bind:value={corpId}
									required
								/>
							</div>
							<div>
								<label class="text-xs text-gray-500 mb-1 block">Agent ID</label>
								<input
									class="w-full"
									type="text"
									placeholder="1000002"
									bind:value={agentId}
									required
								/>
							</div>
						</div>
						<div>
							<label class="text-xs text-gray-500 mb-1 block">Secret</label>
							<input
								class="w-full"
								type="text"
								placeholder={$i18n.t('App Secret')}
								bind:value={wxSecret}
								required
							/>
						</div>
						<div class="grid grid-cols-2 gap-3">
							<div>
								<label class="text-xs text-gray-500 mb-1 block">{$i18n.t('Callback Token')}</label>
								<input
									class="w-full"
									type="text"
									placeholder={$i18n.t('Token for signature verification')}
									bind:value={wxToken}
									required
								/>
							</div>
							<div>
								<label class="text-xs text-gray-500 mb-1 block">EncodingAESKey</label>
								<input
									class="w-full"
									type="text"
									placeholder={$i18n.t('43-character key')}
									bind:value={aesKey}
									required
								/>
							</div>
						</div>
					{:else if platform === 'feishu'}
						<!-- Feishu: App ID, App Secret, Verification Token, Encrypt Key -->
						<div class="grid grid-cols-2 gap-3">
							<div>
								<label class="text-xs text-gray-500 mb-1 block">App ID</label>
								<input
									class="w-full"
									type="text"
									placeholder="cli_a1b2c3..."
									bind:value={appId}
									required
								/>
							</div>
							<div>
								<label class="text-xs text-gray-500 mb-1 block">App Secret</label>
								<input
									class="w-full"
									type="text"
									placeholder={$i18n.t('App Secret')}
									bind:value={appSecret}
									required
								/>
							</div>
						</div>
						<div class="grid grid-cols-2 gap-3">
							<div>
								<label class="text-xs text-gray-500 mb-1 block"
									>{$i18n.t('Verification Token')}</label
								>
								<input
									class="w-full"
									type="text"
									placeholder={$i18n.t('Event subscription token')}
									bind:value={verificationToken}
									required
								/>
							</div>
							<div>
								<label class="text-xs text-gray-500 mb-1 block"
									>Encrypt Key <span class="text-gray-400">({$i18n.t('optional')})</span></label
								>
								<input
									class="w-full"
									type="text"
									placeholder={$i18n.t('Leave empty to disable encryption')}
									bind:value={encryptKey}
									required
								/>
							</div>
						</div>
					{/if}

					<!-- Default Model -->
					<div>
						<label class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
							{$i18n.t('Default Model')}
						</label>
						<HaloSelect
							bind:value={defaultModelId}
							searchEnabled={true}
							placeholder={$i18n.t('Select a model')}
							searchPlaceholder={$i18n.t('Search a model')}
							noResultsText={$i18n.t('No results found')}
							options={[
								{ value: '', label: $i18n.t('Use global default') },
								...(models ?? []).map((m) => ({
									value: getModelSelectionId(m),
									label: getModelChatDisplayName(m) || m.name || m.id
								}))
							]}
						/>
					</div>

					<!-- System Prompt -->
					<div>
						<label class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
							{$i18n.t('System Prompt')} <span class="text-gray-400">({$i18n.t('optional')})</span>
						</label>
						<textarea
							class="w-full rounded-lg text-sm bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 px-3 py-2 outline-hidden resize-y"
							rows="3"
							placeholder={$i18n.t('You are a helpful assistant...')}
							bind:value={systemPrompt}
						/>
					</div>

					<!-- Access Control -->
					<div class="border-t border-gray-200 dark:border-gray-700 pt-3">
						<p class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
							{$i18n.t('Access Control')}
						</p>

						<div class="grid grid-cols-2 gap-3">
							<div>
								<label class="text-xs text-gray-500 mb-1 block">{$i18n.t('DM Policy')}</label>
								<HaloSelect
									bind:value={dmPolicy}
									options={[
										{ value: 'open', label: $i18n.t('Open (anyone)') },
										{ value: 'allowlist', label: $i18n.t('Allowlist only') }
									]}
									className="w-full"
								/>
							</div>
							<div>
								<label class="text-xs text-gray-500 mb-1 block">{$i18n.t('Group Policy')}</label>
								<HaloSelect
									bind:value={groupPolicy}
									options={[
										{ value: 'mention', label: $i18n.t('Mention only') },
										{ value: 'always', label: $i18n.t('Always respond') },
										{ value: 'disabled', label: $i18n.t('Disabled') }
									]}
									className="w-full"
								/>
							</div>
						</div>

						{#if dmPolicy === 'allowlist'}
							<div class="mt-2">
								<label class="text-xs text-gray-500 mb-1 block">
									{$i18n.t('Allowed User IDs')}
									<span class="text-gray-400">({$i18n.t('comma separated')})</span>
								</label>
								<input
									class="w-full"
									type="text"
									placeholder="123456789, 987654321"
									bind:value={allowlist}
								/>
							</div>
						{/if}
					</div>

					<!-- Tools Configuration -->
					<div class="border-t border-gray-200 dark:border-gray-700 pt-3">
						<p class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
							{$i18n.t('Tools')}
						</p>

						<div class="space-y-2">
							{#each BUILTIN_TOOLS as tool}
								<label class="flex items-center gap-2 cursor-pointer">
									<input
										type="checkbox"
										class="rounded border-gray-300 dark:border-gray-600 text-blue-500"
										checked={toolIds.includes(tool.id)}
										on:change={(e) => {
											if (e.currentTarget.checked) {
												toolIds = [...toolIds, tool.id];
											} else {
												toolIds = toolIds.filter((t) => t !== tool.id);
											}
										}}
									/>
									<span class="text-sm text-gray-700 dark:text-gray-300">{tool.label}</span>
								</label>
							{/each}
						</div>

						<div class="mt-3">
							<label class="text-xs text-gray-500 mb-1 block">
								{$i18n.t('Max Tool Rounds')}
								<span class="text-gray-400">(1-10)</span>
							</label>
							<input
								class="w-20 rounded-lg text-sm bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 px-3 py-2 outline-hidden"
								type="number"
								min="1"
								max="10"
								bind:value={maxToolRounds}
							/>
						</div>
					</div>
				</div>

				<div class="flex justify-end pt-4 text-sm font-medium">
					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
						type="submit"
					>
						{$i18n.t('Save')}
					</button>
				</div>
			</form>
		</div>
	</div>
</Modal>
