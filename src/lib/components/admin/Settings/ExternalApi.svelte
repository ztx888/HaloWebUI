<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getModels } from '$lib/apis';
	import { getUsers } from '$lib/apis/users';
	import {
		createExternalApiClient,
		deleteExternalApiClient,
		getExternalApiClientLogs,
		getExternalApiClients,
		getExternalApiGatewayConfig,
		updateExternalApiClient,
		updateExternalApiGatewayConfig
	} from '$lib/apis/external-api';
	import ClientModal from './ExternalApi/ClientModal.svelte';
	import ClientLogsModal from './ExternalApi/ClientLogsModal.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { WEBUI_BASE_URL } from '$lib/constants';

	const i18n: Writable<any> = getContext('i18n');
	export let saveHandler: Function;

	let ready = false;
	let saving = false;
	let clientsSaving = false;
	let gatewayConfig = {
		enabled: false,
		protocols: {
			openai: true,
			anthropic: true
		},
		default_rpm_limit: 60
	};
	let initialGatewayConfig = null;
	let clients = [];
	let initialClients = [];
	let users = [];
	let models = [];
	let dirtyConfig = false;
	let dirtyClients = false;

	let showClientModal = false;
	let editingClient = null;
	let latestCreatedKey = '';
	let latestCreatedClient = '';
	let selectedClient = null;
	let showLogsModal = false;
	let clientLogs = [];

	const gatewayBasePath = `${WEBUI_BASE_URL}/api/v1/external_api/gateway`;

	const clone = (value) => JSON.parse(JSON.stringify(value));

	$: dirtyConfig =
		ready && JSON.stringify(gatewayConfig) !== JSON.stringify(initialGatewayConfig);
	$: dirtyClients = ready && JSON.stringify(clients) !== JSON.stringify(initialClients);

	const loadData = async () => {
		const [configRes, clientsRes, usersRes, modelsRes] = await Promise.all([
			getExternalApiGatewayConfig(localStorage.token),
			getExternalApiClients(localStorage.token),
			getUsers(localStorage.token),
			getModels(localStorage.token)
		]);
		gatewayConfig = clone(configRes ?? gatewayConfig);
		initialGatewayConfig = clone(gatewayConfig);
		clients = clone(clientsRes ?? []);
		initialClients = clone(clients);
		users = usersRes ?? [];
		models = modelsRes ?? [];
		ready = true;
	};

	const saveConfig = async () => {
		saving = true;
		try {
			const res = await updateExternalApiGatewayConfig(localStorage.token, {
				enabled: gatewayConfig.enabled,
				openai: gatewayConfig.protocols?.openai,
				anthropic: gatewayConfig.protocols?.anthropic,
				default_rpm_limit: Number(gatewayConfig.default_rpm_limit) || 0
			});
			gatewayConfig = clone(res ?? gatewayConfig);
			initialGatewayConfig = clone(gatewayConfig);
			await saveHandler?.();
		} catch (error: any) {
			console.error(error);
			toast.error(error?.toString?.() || error?.detail || '保存失败');
		} finally {
			saving = false;
		}
	};

	const resetConfig = () => {
		gatewayConfig = clone(initialGatewayConfig);
	};

	const resetClients = () => {
		clients = clone(initialClients);
	};

	const saveClients = async () => {
		clientsSaving = true;
		try {
			const initialMap = new Map(initialClients.map((client) => [client.id, client]));
			const currentMap = new Map(clients.map((client) => [client.id, client]));

			for (const client of initialClients) {
				if (!currentMap.has(client.id)) {
					await deleteExternalApiClient(localStorage.token, client.id);
				}
			}

			for (const client of clients) {
				if (!client.id) continue;
				if (initialMap.has(client.id)) {
					await updateExternalApiClient(localStorage.token, client.id, {
						name: client.name,
						owner_user_id: client.owner_user_id,
						allowed_protocols: client.allowed_protocols,
						allowed_model_ids: client.allowed_model_ids,
						allow_tools: client.allow_tools,
						rpm_limit: client.rpm_limit,
						note: client.note,
						enabled: client.enabled
					});
				}
			}

			const latest = await getExternalApiClients(localStorage.token);
			clients = clone(latest ?? []);
			initialClients = clone(clients);
			await saveHandler?.();
		} catch (error: any) {
			console.error(error);
			toast.error(error?.toString?.() || error?.detail || '保存失败');
		} finally {
			clientsSaving = false;
		}
	};

	const handleClientSubmit = async (event) => {
		const { id, data } = event.detail;
		if (!id) {
			try {
				const created = await createExternalApiClient(localStorage.token, data);
				if (created?.client) {
					clients = [clone(created.client), ...clients];
					latestCreatedKey = created.api_key ?? '';
					latestCreatedClient = created.client.name ?? '';
					toast.success('已创建新的外部客户端密钥');
				}
			} catch (error: any) {
				console.error(error);
				toast.error(error?.toString?.() || error?.detail || '创建失败');
			}
			return;
		}

		clients = clients.map((client) => (client.id === id ? { ...client, ...clone(data), id } : client));
	};

	const openEditClient = (client) => {
		editingClient = clone(client);
		showClientModal = true;
	};

	const openCreateClient = () => {
		editingClient = null;
		showClientModal = true;
	};

	const openLogs = async (client) => {
		selectedClient = client;
		clientLogs = await getExternalApiClientLogs(localStorage.token, client.id, 100);
		showLogsModal = true;
	};

	const removeClient = async (clientId) => {
		clients = clients.filter((client) => client.id !== clientId);
	};

	const copyLatestKey = async () => {
		if (!latestCreatedKey) return;
		try {
			await navigator.clipboard.writeText(latestCreatedKey);
			toast.success('已复制到剪贴板');
		} catch {
			toast.error('复制失败');
		}
	};

	onMount(loadData);
</script>

<ClientModal
	bind:show={showClientModal}
	client={editingClient}
	{users}
	{models}
	on:submit={handleClientSubmit}
/>

<ClientLogsModal bind:show={showLogsModal} client={selectedClient} logs={clientLogs} />

<div class="flex h-full min-h-0 flex-col text-sm">
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		<div class="max-w-6xl mx-auto space-y-6">
			<section class="glass-section p-5 space-y-5">
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<div class="text-base font-semibold text-gray-800 dark:text-gray-100">外部 API 网关</div>
					<InlineDirtyActions
						dirty={dirtyConfig}
						saving={saving}
						saveAsSubmit={false}
						on:reset={resetConfig}
						on:save={saveConfig}
					/>
				</div>

				<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
					<label class="flex items-center justify-between glass-item px-4 py-3">
						<div>
							<div class="text-sm font-medium">启用外部客户端网关</div>
							<div class="text-xs text-gray-500 mt-1">开启后，外部程序和客户端可通过正式网关入口调用</div>
						</div>
						<Switch bind:state={gatewayConfig.enabled} />
					</label>

					<label class="flex items-center justify-between glass-item px-4 py-3">
						<div>
							<div class="text-sm font-medium">默认 RPM</div>
							<div class="text-xs text-gray-500 mt-1">未单独指定时，每个服务账号每分钟请求上限</div>
						</div>
						<input class="w-24 bg-transparent text-right" type="number" min="0" bind:value={gatewayConfig.default_rpm_limit} />
					</label>

					<label class="flex items-center justify-between glass-item px-4 py-3">
						<div>
							<div class="text-sm font-medium">OpenAI 网关</div>
							<div class="text-xs text-gray-500 mt-1">提供 `{gatewayBasePath}/openai/v1/*` 入口</div>
						</div>
						<Switch bind:state={gatewayConfig.protocols.openai} />
					</label>

					<label class="flex items-center justify-between glass-item px-4 py-3">
						<div>
							<div class="text-sm font-medium">Anthropic 网关</div>
							<div class="text-xs text-gray-500 mt-1">提供 `{gatewayBasePath}/anthropic/v1/*` 入口</div>
						</div>
						<Switch bind:state={gatewayConfig.protocols.anthropic} />
					</label>
				</div>

				<div class="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 text-xs text-gray-500 space-y-1">
					<div>OpenAI: `{gatewayBasePath}/openai/v1/models` `{gatewayBasePath}/openai/v1/chat/completions` `{gatewayBasePath}/openai/v1/responses`</div>
					<div>Anthropic: `{gatewayBasePath}/anthropic/v1/models` `{gatewayBasePath}/anthropic/v1/messages`</div>
				</div>
			</section>

			<section class="glass-section p-5 space-y-5">
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<div class="text-base font-semibold text-gray-800 dark:text-gray-100">服务账号密钥</div>
					<div class="flex items-center gap-2">
						<button
							type="button"
							class="px-3 py-1 text-xs font-medium bg-blue-500 hover:bg-blue-600 text-white rounded-full transition"
							on:click={openCreateClient}
						>
							+ 新增
						</button>
						<InlineDirtyActions
							dirty={dirtyClients}
							saving={clientsSaving}
							saveAsSubmit={false}
							on:reset={resetClients}
							on:save={saveClients}
						/>
					</div>
				</div>

				{#if latestCreatedKey}
					<div class="rounded-2xl border border-green-200 bg-green-50 dark:bg-green-950/20 dark:border-green-900/40 px-4 py-3">
						<div class="text-sm font-medium text-green-700 dark:text-green-300">
							已创建新的密钥：{latestCreatedClient}
						</div>
						<div class="mt-1 break-all text-xs text-green-600 dark:text-green-400">{latestCreatedKey}</div>
						<div class="mt-2">
							<button type="button" class="px-3 py-1 rounded-full bg-green-600 text-white text-xs" on:click={copyLatestKey}>
								复制
							</button>
						</div>
					</div>
				{/if}

				<div class="space-y-3">
					{#if clients.length === 0}
						<div class="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 px-4 py-8 text-center text-gray-400">
							暂无外部客户端密钥
						</div>
					{:else}
						{#each clients as client}
							<div class="rounded-2xl border border-gray-200 dark:border-gray-700 px-4 py-4 space-y-3">
								<div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
									<div class="min-w-0">
										<div class="flex items-center gap-2 flex-wrap">
											<div class="text-sm font-semibold text-gray-800 dark:text-gray-100">{client.name}</div>
											<span class="text-[10px] rounded-full px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500">{client.key_prefix}</span>
											{#if client.enabled}
												<span class="text-[10px] rounded-full px-2 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-600">启用</span>
											{:else}
												<span class="text-[10px] rounded-full px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500">停用</span>
											{/if}
											{#if client.allow_tools}
												<span class="text-[10px] rounded-full px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600">工具</span>
											{/if}
										</div>
										<div class="mt-1 text-xs text-gray-500 break-all">
											用户：{users.find((user) => user.id === client.owner_user_id)?.email || client.owner_user_id}
										</div>
										<div class="mt-1 text-xs text-gray-500">
											协议：{(client.allowed_protocols ?? []).join(' / ') || '-'} · 模型数：{client.allowed_model_ids?.length ?? 0} · RPM：{client.rpm_limit ?? gatewayConfig.default_rpm_limit}
										</div>
										{#if client.note}
											<div class="mt-2 text-xs text-gray-600 dark:text-gray-300">{client.note}</div>
										{/if}
									</div>
									<div class="flex flex-wrap items-center gap-2">
										<button type="button" class="px-3 py-1 rounded-full bg-gray-100 dark:bg-gray-800 text-xs" on:click={() => openLogs(client)}>
											日志
										</button>
										<button type="button" class="px-3 py-1 rounded-full bg-gray-100 dark:bg-gray-800 text-xs" on:click={() => openEditClient(client)}>
											编辑
										</button>
										<button type="button" class="px-3 py-1 rounded-full bg-red-50 dark:bg-red-950/30 text-red-500 text-xs" on:click={() => removeClient(client.id)}>
											删除
										</button>
									</div>
								</div>
							</div>
						{/each}
					{/if}
				</div>
			</section>
		</div>
	</div>
</div>
