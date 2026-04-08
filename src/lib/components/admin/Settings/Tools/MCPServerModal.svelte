<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';
	import type { Writable } from 'svelte/store';

	const i18n: Writable<any> = getContext('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import CollapsibleSection from '$lib/components/common/CollapsibleSection.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import {
		createEmptyMCPHeaderItem,
		getMCPHeaderItemsFromRecord,
		prepareMCPHeaderItems
	} from '$lib/utils/mcp-headers';
	import type { MCPHeaderItem, MCPHeaderValidationIssue } from '$lib/utils/mcp-headers';

	import { verifyMCPServerConnection } from '$lib/apis/configs';
	import { getErrorDetail } from '$lib/apis/response';

	type TransportType = 'http' | 'stdio';

	interface EnvItem {
		key: string;
		value: string;
	}

	interface VerifyResult {
		server_info: any;
		tool_count: number;
		tools: any[];
		verified_at?: string;
	}

	interface MCPPreset {
		id: string;
		name: string;
		description: string;
		category: 'hosted' | 'stdio';
		transport_type: TransportType;
		icon: string;
		url?: string;
		command?: string;
		args?: string[];
		headers?: Record<string, string>;
		auth_type?: string;
		requires_key?: boolean;
		setup_hint: string;
		runtime_hint?: string;
		doc_url?: string;
	}

	type RuntimeCommandCapability = {
		available: boolean;
		message?: string | null;
	};

	type RuntimeCapabilities = {
		commands?: Record<string, RuntimeCommandCapability>;
	};

	type RuntimeProfile = 'main' | 'slim' | 'custom';

	export let show = false;
	export let connection: any = null;
	export let isAdmin = false;
	export let runtimeCapabilities: RuntimeCapabilities = { commands: {} };
	export let runtimeProfile: RuntimeProfile = 'custom';
	export let onSubmit: (connection: any) => Promise<void> = async () => {};

	let activeTab: 'manual' | 'presets' = 'presets';
	let hydrating = false;
	let lastTransportType: TransportType = 'http';
	let lastVerifiedSignature: string | null = null;

	let transport_type: TransportType = 'http';
	let name = '';
	let url = '';
	let command = '';
	let argsItems: string[] = [];
	let envItems: EnvItem[] = [];
	let headerItems: MCPHeaderItem[] = [];
	let description = '';
	let auth_type = 'none';
	let key = '';
	let enable = true;

	let loading = false;
	let verifyStatus: 'idle' | 'loading' | 'success' | 'error' = 'idle';
	let verifyResult: VerifyResult | null = null;
	let verifyError = '';
	let showAllTools = false;

	const MCP_PRESETS: MCPPreset[] = [
		{
			id: 'composio',
			name: 'Composio',
			description: '200+ 应用集成 (GitHub, Slack, Gmail, Jira 等)',
			category: 'hosted',
			transport_type: 'http',
			icon: '🔗',
			url: 'https://connect.composio.dev/mcp',
			headers: { 'x-consumer-api-key': '' },
			auth_type: 'none',
			requires_key: true,
			setup_hint: '把 composio 提供的 API Key 填到 x-consumer-api-key 的值里即可',
			doc_url: 'https://docs.composio.dev'
		},
		{
			id: 'smithery',
			name: 'Smithery',
			description: 'MCP 服务器托管平台，支持 fetch/memory/search 等',
			category: 'hosted',
			transport_type: 'http',
			icon: '🛠️',
			url: 'https://server.smithery.ai/{server-name}/mcp',
			auth_type: 'bearer',
			requires_key: true,
			setup_hint: '在 smithery.ai 注册，选择 MCP 服务器获取端点和 Key',
			doc_url: 'https://smithery.ai'
		},
		{
			id: 'zapier',
			name: 'Zapier MCP',
			description: '工作流自动化，连接 7000+ 应用',
			category: 'hosted',
			transport_type: 'http',
			icon: '⚡',
			url: 'https://actions.zapier.com/mcp/actions',
			auth_type: 'bearer',
			requires_key: true,
			setup_hint: '在 Zapier 设置中启用 MCP 并获取 Access Token',
			doc_url: 'https://actions.zapier.com'
		},
		{
			id: 'memory',
			name: 'Memory',
			description: '本地记忆型 MCP 服务器',
			category: 'stdio',
			transport_type: 'stdio',
			icon: '🧠',
			command: 'npx',
			args: ['-y', '@modelcontextprotocol/server-memory'],
			setup_hint: '无需额外配置，首次启动可能会下载 npm 包。',
			runtime_hint: '需要 Node.js 与 npx'
		},
		{
			id: 'sequential-thinking',
			name: 'Sequential Thinking',
			description: '适合多步拆解与推理过程',
			category: 'stdio',
			transport_type: 'stdio',
			icon: '🪜',
			command: 'npx',
			args: ['-y', '@modelcontextprotocol/server-sequential-thinking'],
			setup_hint: '首次启动可能会下载 npm 包。',
			runtime_hint: '需要 Node.js 与 npx'
		},
		{
			id: 'context7',
			name: 'Context7',
			description: '查询最新文档与框架 API',
			category: 'stdio',
			transport_type: 'stdio',
			icon: '📚',
			command: 'npx',
			args: ['-y', '@upstash/context7-mcp'],
			setup_hint: '首次启动可能会下载 npm 包。',
			runtime_hint: '需要 Node.js 与 npx'
		},
		{
			id: 'fetch',
			name: 'Fetch',
			description: '网页抓取 MCP 服务器',
			category: 'stdio',
			transport_type: 'stdio',
			icon: '🌐',
			command: 'uvx',
			args: ['mcp-server-fetch'],
			setup_hint: '请确保当前运行环境已安装 uv。',
			runtime_hint: '需要 Python 与 uv/uvx'
		},
		{
			id: 'time',
			name: 'Time',
			description: '时间与时区 MCP 服务器',
			category: 'stdio',
			transport_type: 'stdio',
			icon: '⏰',
			command: 'uvx',
			args: ['mcp-server-time'],
			setup_hint: '请确保当前运行环境已安装 uv。',
			runtime_hint: '需要 Python 与 uv/uvx'
		}
	];

	const emptyEnvItem = (): EnvItem => ({ key: '', value: '' });
	const getRuntimeCapabilityKey = (command?: string) =>
		command?.trim().split(/[\\/]/).pop()?.toLowerCase() ?? '';
	const getRuntimeCommandCapability = (command?: string) => {
		const capabilityKey = getRuntimeCapabilityKey(command);
		if (!capabilityKey) return null;
		return runtimeCapabilities?.commands?.[capabilityKey] ?? null;
	};
	const stdioCommandUsesGitSource = (commandValue: string, args: string[]) => {
		const capabilityKey = getRuntimeCapabilityKey(commandValue);
		if (capabilityKey !== 'uv' && capabilityKey !== 'uvx') {
			return false;
		}

		return args.some((arg, idx) => {
			const normalizedArg = arg.trim();
			if (!normalizedArg) return false;
			if (normalizedArg.startsWith('git+')) return true;
			if (normalizedArg.startsWith('--from=')) {
				return normalizedArg.slice('--from='.length).startsWith('git+');
			}
			return normalizedArg === '--from' && (args[idx + 1] ?? '').trim().startsWith('git+');
		});
	};
	const getVerifyActionLabel = () =>
		lastVerifiedSignature ? $i18n.t('Re-verify Connection') : $i18n.t('Verify Connection');

	const getPresetRuntimeCapability = (preset: MCPPreset) => {
		return getRuntimeCommandCapability(preset.command);
	};

	const isPresetRuntimeUnavailable = (preset: MCPPreset) =>
		getPresetRuntimeCapability(preset)?.available === false;

	const getPresetSetupHint = (preset: MCPPreset) => {
		if (!isPresetRuntimeUnavailable(preset)) {
			return preset.setup_hint;
		}

		if (runtimeProfile === 'slim') {
			return '当前为官方 slim 轻量版，未内置该运行时。想直接体验这个 MCP，推荐切换到官方 main 镜像。';
		}

		return getPresetRuntimeCapability(preset)?.message || preset.setup_hint;
	};

	const getPresetRuntimeHint = (preset: MCPPreset) => {
		if (!isPresetRuntimeUnavailable(preset)) {
			return preset.runtime_hint;
		}

		if (runtimeProfile === 'slim') {
			return '推荐切换到 main 镜像获得开箱体验';
		}

		return getPresetRuntimeCapability(preset)?.message || preset.runtime_hint;
	};

	const getManualStdioHint = () =>
		runtimeProfile === 'slim'
			? '当前运行的是官方 slim 轻量版，默认不内置 Node.js / uv。想直接使用常见 stdio MCP，推荐切换到官方 main 镜像；如果你愿意自行安装运行时，也可以继续手动配置。'
			: 'stdio 命令运行在 HaloWebUI 服务端。请确保服务端已安装对应 runtime；npx 需要 Node.js，uvx 需要 Python + uv。启动中的 stdio MCP 会额外占用内存，空闲后会自动回收。';

	$: hostedPresets = MCP_PRESETS.filter((preset) => preset.category === 'hosted');
	$: stdioPresets = MCP_PRESETS.filter((preset) => preset.category === 'stdio');

	const normalizeArgs = () => argsItems.map((item) => item.trim()).filter(Boolean);
	const normalizeEnv = () =>
		envItems.reduce((acc, item) => {
			const envKey = item.key.trim();
			if (!envKey) return acc;
			acc[envKey] = item.value;
			return acc;
		}, {} as Record<string, string>);
	$: normalizedArgs = normalizeArgs();
	$: normalizedEnvMap = normalizeEnv();
	$: preparedHeaders = prepareMCPHeaderItems(headerItems);
	$: normalizedHeaders = preparedHeaders.normalizedHeaders;
	$: headerValidationIssues = preparedHeaders.issues;
	$: hasHeaderValidationIssues = headerValidationIssues.length > 0;
	$: currentStdioUsesGitSource =
		transport_type === 'stdio' && stdioCommandUsesGitSource(command, normalizedArgs);
	$: gitRuntimeCapability = runtimeCapabilities?.commands?.git ?? null;
	$: missingGitForCurrentStdio =
		currentStdioUsesGitSource && gitRuntimeCapability?.available === false;
	const isFormInvalid = () =>
		transport_type === 'http'
			? url.trim() === '' || prepareMCPHeaderItems(headerItems).issues.length > 0
			: command.trim() === '';

	const formatVerifiedAt = (value?: string) => {
		if (!value) return '';
		const parsed = new Date(value);
		if (Number.isNaN(parsed.getTime())) return value;
		return parsed.toLocaleString();
	};

	const buildVerificationSignature = () =>
		JSON.stringify({
			transport_type,
			url: url.trim().replace(/\/$/, ''),
			command: command.trim(),
			args: normalizeArgs(),
			env: normalizeEnv(),
			auth_type: transport_type === 'http' ? auth_type : 'none',
			headers: transport_type === 'http' ? preparedHeaders.signature : [],
			key:
				transport_type === 'http' && (auth_type === 'bearer' || auth_type === 'oauth21')
					? key
					: ''
		});

	const clearVerifyCache = () => {
		verifyStatus = 'idle';
		verifyResult = null;
		verifyError = '';
		showAllTools = false;
		lastVerifiedSignature = null;
	};

	const resetForTransport = (nextTransport: TransportType) => {
		if (nextTransport === 'stdio') {
			url = '';
			auth_type = 'none';
			key = '';
			headerItems = [];
		} else {
			command = '';
			argsItems = [];
			envItems = [];
			headerItems = [];
		}
		clearVerifyCache();
	};

	const buildConnectionPayload = ({ persistVerify }: { persistVerify: boolean }) => {
		const currentArgs = normalizeArgs();
		const currentEnv = normalizeEnv();
		const base: any = {
			transport_type,
			name: name.trim() || undefined,
			description: description.trim() || undefined,
			config: { enable }
		};

		if (transport_type === 'http') {
			base.url = url.trim().replace(/\/$/, '');
			base.auth_type = auth_type;
			if (Object.keys(normalizedHeaders).length > 0) {
				base.headers = normalizedHeaders;
			}
			if ((auth_type === 'bearer' || auth_type === 'oauth21') && key) {
				base.key = key;
			}
		} else {
			base.command = command.trim();
			base.args = currentArgs;
			base.env = currentEnv;
		}

		if (persistVerify) {
			base.server_info = verifyResult?.server_info || undefined;
			base.tool_count = verifyResult?.tool_count ?? undefined;
			base.verified_at = verifyResult?.verified_at ?? undefined;
		}

		return base;
	};

	const init = () => {
		hydrating = true;
		verifyStatus = 'idle';
		verifyResult = null;
		verifyError = '';
		showAllTools = false;
		lastVerifiedSignature = null;

		if (!connection) {
			activeTab = 'presets';
			transport_type = 'http';
			name = '';
			url = '';
			command = '';
			argsItems = [];
			envItems = [];
			headerItems = [];
			description = '';
			auth_type = 'none';
			key = '';
			enable = true;
			lastTransportType = transport_type;
			hydrating = false;
			return;
		}

		activeTab = 'manual';
		transport_type = (connection.transport_type ?? 'http') as TransportType;
		name = connection.name ?? '';
		url = connection.url ?? '';
		command = connection.command ?? '';
		argsItems = Array.isArray(connection.args) ? [...connection.args] : [];
		envItems = Object.entries(connection.env ?? {}).map(([envKey, envValue]) => ({
			key: envKey,
			value: String(envValue ?? '')
		}));
		headerItems = getMCPHeaderItemsFromRecord(connection.headers);
		description = connection.description ?? '';
		auth_type = connection.auth_type ?? 'none';
		key = connection.key ?? '';
		enable = connection.config?.enable ?? connection.enabled ?? true;

		if (connection.server_info || connection.verified_at) {
			verifyStatus = 'success';
			verifyResult = {
				server_info: connection.server_info ?? {},
				tool_count: connection.tool_count ?? 0,
				tools: [],
				verified_at: connection.verified_at
			};
			lastVerifiedSignature = buildVerificationSignature();
		}

		lastTransportType = transport_type;
		hydrating = false;
	};

	$: if (show) {
		init();
	}

	onMount(() => {
		init();
	});

	$: if (!hydrating && transport_type !== lastTransportType) {
		resetForTransport(transport_type);
		lastTransportType = transport_type;
	}

	$: if (!hydrating && lastVerifiedSignature && buildVerificationSignature() !== lastVerifiedSignature) {
		clearVerifyCache();
	}

	const applyPreset = (preset: MCPPreset) => {
		transport_type = preset.transport_type;
		name = preset.name;
		description = preset.description;
		if (preset.transport_type === 'http') {
			url = preset.url ?? '';
			auth_type = preset.auth_type ?? 'none';
			headerItems = getMCPHeaderItemsFromRecord(preset.headers);
			key = '';
		} else {
			command = preset.command ?? '';
			argsItems = [...(preset.args ?? [])];
			envItems = [];
			headerItems = [];
			auth_type = 'none';
			key = '';
		}
		activeTab = 'manual';
		clearVerifyCache();
	};

	const verifyHandler = async () => {
		if (isFormInvalid()) {
			return;
		}

		loading = true;
		verifyStatus = 'loading';
		verifyError = '';

		const res = await verifyMCPServerConnection(
			localStorage.token,
			buildConnectionPayload({ persistVerify: false })
		).catch((err) => {
			verifyStatus = 'error';
			verifyError = getErrorDetail(err, $i18n.t('Connection failed'));
			return null;
		});

		loading = false;

		if (res) {
			verifyStatus = 'success';
			verifyResult = {
				server_info: res.server_info || {},
				tool_count: res.tool_count ?? 0,
				tools: res.tools || [],
				verified_at: res.verified_at
			};
			lastVerifiedSignature = buildVerificationSignature();
			if (!name.trim() && res.server_info?.name) {
				name = res.server_info.name;
			}
			toast.success(
				$i18n.t('Connection successful') +
					(res.tool_count !== undefined ? ` (${res.tool_count} ${$i18n.t('tools')})` : '')
			);
		}
	};

	const submitHandler = async () => {
		if (isFormInvalid()) {
			return;
		}

		loading = true;
		try {
			await onSubmit(buildConnectionPayload({ persistVerify: true }));
			show = false;
		} catch (error) {
			// Save failure toast is handled by the parent settings page.
		} finally {
			loading = false;
		}
	};

	const addArgRow = () => {
		argsItems = [...argsItems, ''];
	};

	const removeArgRow = (idx: number) => {
		argsItems = argsItems.filter((_, index) => index !== idx);
	};

	const updateArgRow = (idx: number, value: string) => {
		argsItems[idx] = value;
		argsItems = argsItems;
	};

	const addEnvRow = () => {
		envItems = [...envItems, emptyEnvItem()];
	};

	const removeEnvRow = (idx: number) => {
		envItems = envItems.filter((_, index) => index !== idx);
	};

	const updateEnvRow = (idx: number, keyName: 'key' | 'value', value: string) => {
		envItems[idx] = { ...envItems[idx], [keyName]: value };
		envItems = envItems;
	};

	const addHeaderRow = () => {
		headerItems = [...headerItems, createEmptyMCPHeaderItem()];
	};

	const removeHeaderRow = (idx: number) => {
		headerItems = headerItems.filter((_, index) => index !== idx);
	};

	const updateHeaderRow = (idx: number, keyName: 'key' | 'value', value: string) => {
		headerItems[idx] = { ...headerItems[idx], [keyName]: value };
		headerItems = headerItems;
	};

	const getHeaderIssuesForIndex = (idx: number): MCPHeaderValidationIssue[] =>
		headerValidationIssues.filter((issue) => issue.index === idx);

	const hasHeaderFieldIssue = (idx: number, field: 'key' | 'value') =>
		headerValidationIssues.some((issue) => issue.index === idx && issue.field === field);

	const getHeaderIssueMessage = (issue: MCPHeaderValidationIssue) => {
		if (issue.code === 'missing_key') {
			return $i18n.t('请输入请求头名称');
		}
		if (issue.code === 'invalid_name') {
			return $i18n.t('请求头名称格式无效：{{key}}', { key: issue.key || '' });
		}
		if (issue.code === 'duplicate_key') {
			return $i18n.t('请求头名称重复：{{key}}', { key: issue.key || '' });
		}
		if (issue.code === 'reserved_key') {
			return $i18n.t('该请求头由 HaloWebUI 管理，不能自定义：{{key}}', {
				key: issue.key || ''
			});
		}
		return $i18n.t('请求头名称和值不能包含换行');
	};
</script>

<Modal size="md" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-100 px-5 pt-4 pb-2">
			<div class="text-lg font-medium self-center font-primary">
				{connection ? $i18n.t('Edit Connection') : $i18n.t('Add Connection')}
			</div>
			<button
				class="self-center"
				on:click={() => {
					show = false;
				}}
				type="button"
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-5 h-5">
					<path
						d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
					/>
				</svg>
			</button>
		</div>

		{#if !connection}
			<div class="flex px-5 gap-1 border-b border-gray-100 dark:border-gray-800">
				<button
					type="button"
					class="px-4 py-2 text-sm font-medium transition-colors {activeTab === 'presets'
						? 'text-black dark:text-white border-b-2 border-black dark:border-white'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					on:click={() => (activeTab = 'presets')}
				>
					{$i18n.t('推荐服务器')}
				</button>
				<button
					type="button"
					class="px-4 py-2 text-sm font-medium transition-colors {activeTab === 'manual'
						? 'text-black dark:text-white border-b-2 border-black dark:border-white'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					on:click={() => (activeTab = 'manual')}
				>
					{$i18n.t('手动配置')}
				</button>
			</div>
		{/if}

		<div class="flex flex-col w-full px-5 pb-4 dark:text-gray-200">
			{#if activeTab === 'manual'}
				<form
					class="flex flex-col w-full"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div class="space-y-3 mt-3">
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('传输方式')}</div>
							<HaloSelect
								bind:value={transport_type}
								options={[
									{ value: 'http', label: 'HTTP' },
									...(isAdmin ? [{ value: 'stdio', label: 'stdio' }] : [])
								]}
								className="w-fit"
							/>
						</div>

						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('服务器名称（可选）')}</div>
							<input
								class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
								type="text"
								bind:value={name}
								placeholder={$i18n.t('例如: My MCP Server')}
								autocomplete="off"
							/>
						</div>

						{#if transport_type === 'http'}
							<div>
								<div class="flex items-center justify-between mb-1">
									<div class="text-xs text-gray-500">{$i18n.t('URL')}</div>
									<Tooltip content={enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}>
										<Switch bind:state={enable} />
									</Tooltip>
								</div>
								<div class="flex gap-2 items-center">
									<input
										class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
										type="text"
										bind:value={url}
										placeholder={$i18n.t('API Base URL')}
										autocomplete="off"
										required
									/>
									<Tooltip content={getVerifyActionLabel()} className="shrink-0">
										<button
											class="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-gray-200 bg-transparent px-2.5 py-1.5 text-xs font-medium transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:hover:bg-gray-850"
											on:click={() => {
												verifyHandler();
											}}
											type="button"
											disabled={loading || isFormInvalid()}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="h-4 w-4 {verifyStatus === 'loading' ? 'animate-spin' : ''}">
												<path
													fill-rule="evenodd"
													d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
													clip-rule="evenodd"
												/>
											</svg>
											<span>{getVerifyActionLabel()}</span>
										</button>
									</Tooltip>
								</div>
							</div>
						{:else}
							<div class="space-y-3 rounded-xl border border-gray-200 dark:border-gray-800 p-3">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">{$i18n.t('Command')}</div>
									<Tooltip content={enable ? $i18n.t('Enabled') : $i18n.t('Disabled')}>
										<Switch bind:state={enable} />
									</Tooltip>
								</div>
								<div class="flex gap-2 items-center">
									<input
										class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
										type="text"
										bind:value={command}
										placeholder={$i18n.t('例如: npx / uvx / python')}
										autocomplete="off"
										required
									/>
									<Tooltip content={getVerifyActionLabel()} className="shrink-0">
										<button
											class="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-gray-200 bg-transparent px-2.5 py-1.5 text-xs font-medium transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:hover:bg-gray-850"
											on:click={() => {
												verifyHandler();
											}}
											type="button"
											disabled={loading || isFormInvalid()}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="h-4 w-4 {verifyStatus === 'loading' ? 'animate-spin' : ''}">
												<path
													fill-rule="evenodd"
													d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
													clip-rule="evenodd"
												/>
											</svg>
											<span>{getVerifyActionLabel()}</span>
										</button>
									</Tooltip>
								</div>

								<div>
									<div class="mb-2 flex items-center justify-between">
										<div class="text-xs text-gray-500">{$i18n.t('Args')}</div>
										<button
											type="button"
											class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
											on:click={addArgRow}
										>
											{$i18n.t('添加参数')}
										</button>
									</div>
									<div class="space-y-2">
										{#if argsItems.length === 0}
											<div class="text-xs text-gray-400">{$i18n.t('暂无参数')}</div>
										{/if}
										{#each argsItems as arg, idx}
											<div class="flex gap-2 items-center">
												<input
													class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
													type="text"
													value={arg}
													on:input={(event) =>
														updateArgRow(idx, event.currentTarget.value)}
													placeholder={$i18n.t('参数')}
													autocomplete="off"
												/>
												<button
													type="button"
													class="text-xs text-red-500 hover:underline shrink-0"
													on:click={() => removeArgRow(idx)}
												>
													{$i18n.t('移除')}
												</button>
											</div>
										{/each}
									</div>
								</div>

								<div>
									<div class="mb-2 flex items-center justify-between">
										<div class="text-xs text-gray-500">{$i18n.t('Env')}</div>
										<button
											type="button"
											class="text-xs text-blue-600 dark:text-blue-400 hover:underline"
											on:click={addEnvRow}
										>
											{$i18n.t('添加环境变量')}
										</button>
									</div>
									<div class="space-y-2">
										{#if envItems.length === 0}
											<div class="text-xs text-gray-400">{$i18n.t('暂无环境变量')}</div>
										{/if}
										{#each envItems as item, idx}
											<div class="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
												<input
													class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
													type="text"
													value={item.key}
													on:input={(event) =>
														updateEnvRow(idx, 'key', event.currentTarget.value)}
													placeholder="KEY"
													autocomplete="off"
												/>
												<input
													class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
													type="text"
													value={item.value}
													on:input={(event) =>
														updateEnvRow(idx, 'value', event.currentTarget.value)}
													placeholder="VALUE"
													autocomplete="off"
												/>
												<button
													type="button"
													class="text-xs text-red-500 hover:underline shrink-0"
													on:click={() => removeEnvRow(idx)}
												>
													{$i18n.t('移除')}
												</button>
											</div>
										{/each}
									</div>
								</div>

								{#if missingGitForCurrentStdio}
									<div class="text-xs text-amber-700 dark:text-amber-300 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/40 p-2 leading-relaxed">
										{$i18n.t(
											'This stdio MCP uses a Git source. The current runtime has uv/uvx, but is missing git. Switch to the official main image with git included, or install git in the container and verify again.'
										)}
									</div>
								{/if}

								<div class="text-xs text-amber-700 dark:text-amber-300 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/40 p-2 leading-relaxed">
									{getManualStdioHint()}
								</div>
							</div>
						{/if}

						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('描述（可选）')}</div>
							<input
								class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b border-gray-200 dark:border-gray-700 pb-1"
								type="text"
								bind:value={description}
								placeholder={$i18n.t('简要描述此服务器的用途')}
								autocomplete="off"
							/>
						</div>

						{#if transport_type === 'http'}
							<CollapsibleSection
								title={$i18n.t('Advanced')}
								open={auth_type !== 'none' || headerItems.length > 0}
								className="mt-1"
							>
								<div class="space-y-3">
									<div class="rounded-xl border border-gray-200 dark:border-gray-800 bg-gray-50/80 dark:bg-gray-900/50 p-3 space-y-3">
										<div>
											<div class="text-sm font-medium text-gray-800 dark:text-gray-200">
												{$i18n.t('常用认证')}
											</div>
											<div class="text-xs text-gray-500 mt-1">
												{$i18n.t('自定义请求头会覆盖同名自动认证头。')}
											</div>
										</div>
										<div>
											<div class="text-xs text-gray-500">{$i18n.t('Auth')}</div>
											<HaloSelect
												bind:value={auth_type}
												options={[
													{ value: 'none', label: 'None' },
													{ value: 'bearer', label: 'Bearer' },
													{ value: 'session', label: 'Session' },
													{ value: 'oauth21', label: 'OAuth 2.1' }
												]}
												className="w-fit"
											/>
										</div>

										{#if auth_type === 'bearer' || auth_type === 'oauth21'}
											<div>
												<div class="text-xs text-gray-500">{$i18n.t('Key')}</div>
												<SensitiveInput bind:value={key} />
											</div>
										{/if}
									</div>

									<div class="rounded-xl border border-gray-200 dark:border-gray-800 bg-white/80 dark:bg-gray-950/40 p-3 space-y-3">
										<div class="flex items-start justify-between gap-3">
											<div class="min-w-0">
												<div class="text-sm font-medium text-gray-800 dark:text-gray-200">
													{$i18n.t('自定义请求头')}
												</div>
												<div class="text-xs text-gray-500 mt-1 leading-relaxed">
													{$i18n.t(
														'适用于 x-consumer-api-key、x-api-key、Authorization 等供应商专用请求头。'
													)}
												</div>
											</div>
											<button
												type="button"
												class="shrink-0 text-xs text-blue-600 dark:text-blue-400 hover:underline"
												on:click={addHeaderRow}
											>
												{$i18n.t('添加请求头')}
											</button>
										</div>

										<div class="space-y-2">
											{#if headerItems.length === 0}
												<div class="rounded-lg border border-dashed border-gray-200 dark:border-gray-800 px-3 py-4 text-xs text-gray-400 dark:text-gray-500">
													{$i18n.t('暂无自定义请求头')}
												</div>
											{/if}

											{#each headerItems as item, idx}
												<div class="space-y-2 rounded-xl border border-gray-200 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-900/50 p-2.5">
													<div class="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-2 items-start">
														<input
															class="w-full text-sm bg-transparent placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-hidden border-b pb-1 {hasHeaderFieldIssue(idx, 'key')
																? 'border-red-300 dark:border-red-700'
																: 'border-gray-200 dark:border-gray-700'}"
															type="text"
															value={item.key}
															on:input={(event) =>
																updateHeaderRow(idx, 'key', event.currentTarget.value)}
															placeholder={$i18n.t('Header Name')}
															autocomplete="off"
														/>
														<SensitiveInput
															bind:value={headerItems[idx].value}
															required={false}
															placeholder={$i18n.t('Header Value')}
															outerClassName={`w-full flex bg-transparent border ${
																hasHeaderFieldIssue(idx, 'value')
																	? 'border-red-300 dark:border-red-700'
																	: 'border-gray-200 dark:border-gray-700'
															} rounded-lg px-3 py-2`}
														/>
														<button
															type="button"
															class="text-xs text-red-500 hover:underline shrink-0 pt-2"
															on:click={() => removeHeaderRow(idx)}
														>
															{$i18n.t('移除')}
														</button>
													</div>

													{#if getHeaderIssuesForIndex(idx).length > 0}
														<div class="space-y-1">
															{#each getHeaderIssuesForIndex(idx) as issue}
																<div class="text-xs text-red-600 dark:text-red-400">
																	{getHeaderIssueMessage(issue)}
																</div>
															{/each}
														</div>
													{/if}
												</div>
											{/each}
										</div>
									</div>
								</div>
							</CollapsibleSection>
						{/if}

						{#if verifyStatus === 'loading'}
							<div class="flex items-center gap-2 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/50 rounded-lg">
								<svg class="animate-spin h-4 w-4 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
									<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
									<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
								</svg>
								<span class="text-sm text-blue-700 dark:text-blue-300">{$i18n.t('验证中...')}</span>
							</div>
						{:else if verifyStatus === 'success' && verifyResult}
							<div class="p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800/50 rounded-lg space-y-2">
								<div class="flex items-center gap-2">
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4 text-green-600 dark:text-green-400">
										<path
											fill-rule="evenodd"
											d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z"
											clip-rule="evenodd"
										/>
									</svg>
									<span class="text-sm font-medium text-green-800 dark:text-green-300">
										{verifyResult.server_info?.name || 'MCP Server'}
										{#if verifyResult.server_info?.version}
											<span class="text-xs font-normal opacity-70">v{verifyResult.server_info.version}</span>
										{/if}
									</span>
									<span class="ml-auto px-2 py-0.5 text-xs rounded-full bg-green-100 dark:bg-green-800/40 text-green-700 dark:text-green-300">
										{verifyResult.tool_count}
										{$i18n.t('个工具')}
									</span>
								</div>

								{#if verifyResult.verified_at}
									<div class="text-xs text-green-700 dark:text-green-300/80">
										{$i18n.t('上次验证于')} {formatVerifiedAt(verifyResult.verified_at)}
									</div>
								{/if}

								{#if verifyResult.tools && verifyResult.tools.length > 0}
									<div class="space-y-1 mt-2">
										{#each showAllTools ? verifyResult.tools : verifyResult.tools.slice(0, 5) as tool}
											<div class="flex items-start gap-2 text-xs">
												<span class="font-mono text-green-700 dark:text-green-400 shrink-0">{tool.name}</span>
												{#if tool.description}
													<span class="text-gray-500 truncate">{tool.description}</span>
												{/if}
											</div>
										{/each}
										{#if verifyResult.tools.length > 5 && !showAllTools}
											<button
												type="button"
												class="text-xs text-green-600 dark:text-green-400 hover:underline"
												on:click={() => (showAllTools = true)}
											>
												{$i18n.t('显示全部')} ({verifyResult.tools.length})
											</button>
										{/if}
									</div>
								{/if}
							</div>
							{:else if verifyStatus === 'error'}
								<div class="flex items-start gap-2 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg">
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4 text-red-500 shrink-0">
										<path
											fill-rule="evenodd"
											d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-5a.75.75 0 01.75.75v4.5a.75.75 0 01-1.5 0v-4.5A.75.75 0 0110 5zm0 10a1 1 0 100-2 1 1 0 000 2z"
											clip-rule="evenodd"
										/>
									</svg>
									<span class="min-w-0 whitespace-pre-wrap break-words text-sm text-red-700 dark:text-red-300">{verifyError || $i18n.t('Connection failed')}</span>
								</div>
							{/if}
					</div>

					<div class="flex justify-end pt-4 text-sm font-medium">
						<button
							class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 disabled:opacity-50 disabled:cursor-not-allowed text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
							type="submit"
							disabled={loading || isFormInvalid()}
						>
							{$i18n.t('Save')}
						</button>
					</div>
				</form>
				{:else if activeTab === 'presets'}
					<div class="space-y-4 mt-3">
						{#if isAdmin && runtimeProfile === 'slim'}
							<div class="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs leading-relaxed text-sky-700 dark:border-sky-800/50 dark:bg-sky-950/30 dark:text-sky-300">
								当前运行的是官方 `slim` 轻量版。它不会预装 stdio MCP 常用运行时；想直接体验 `Memory`、`Context7`、`Fetch`、`Time` 等预设，推荐切换到官方 `main` 镜像。
							</div>
						{/if}

						<div>
							<div class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">
								{$i18n.t('HTTP 托管服务')}
						</div>
						<div class="space-y-2">
							{#each hostedPresets as preset}
								<button
									type="button"
									class="w-full text-left p-3 rounded-xl border border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900/60 transition"
									on:click={() => applyPreset(preset)}
								>
									<div class="flex items-start gap-3">
										<div class="text-lg">{preset.icon}</div>
										<div class="min-w-0 flex-1">
											<div class="flex items-center gap-2">
												<div class="text-sm font-medium">{preset.name}</div>
												<span class="px-1.5 py-0.5 text-[10px] rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">HTTP</span>
											</div>
											<div class="text-xs text-gray-500 mt-0.5">{preset.description}</div>
											<div class="text-xs text-gray-400 mt-1">{preset.setup_hint}</div>
										</div>
									</div>
								</button>
							{/each}
						</div>
					</div>

						{#if isAdmin && stdioPresets.length > 0}
							<div>
								<div class="text-xs font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">
									{$i18n.t('stdio 本地服务')}
								</div>
								<div class="space-y-2">
									{#each stdioPresets as preset}
										<button
											type="button"
											class="w-full text-left p-3 rounded-xl border transition {isPresetRuntimeUnavailable(preset)
												? 'border-amber-200 bg-amber-50/80 hover:border-amber-300 dark:border-amber-800/40 dark:bg-amber-950/20 dark:hover:border-amber-700'
												: 'border-gray-200 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-800 dark:hover:border-gray-700 dark:hover:bg-gray-900/60'}"
											on:click={() => applyPreset(preset)}
										>
											<div class="flex items-start gap-3">
												<div class="text-lg">{preset.icon}</div>
												<div class="min-w-0 flex-1">
													<div class="flex items-center gap-2">
														<div class="text-sm font-medium">{preset.name}</div>
														<span class="px-1.5 py-0.5 text-[10px] rounded bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">stdio</span>
														{#if isPresetRuntimeUnavailable(preset) && runtimeProfile === 'slim'}
															<span class="px-1.5 py-0.5 text-[10px] rounded bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">推荐 main</span>
														{/if}
													</div>
													<div class="text-xs text-gray-500 mt-0.5">{preset.description}</div>
													{#if preset.command}
														<div class="text-xs font-mono text-gray-500 mt-1 break-all">
															{preset.command} {(preset.args ?? []).join(' ')}
														</div>
													{/if}
													<div class="text-xs mt-1 {isPresetRuntimeUnavailable(preset) ? 'text-amber-700 dark:text-amber-300' : 'text-gray-400'}">
														{getPresetSetupHint(preset)}
													</div>
													{#if getPresetRuntimeHint(preset)}
														<div class="text-xs text-amber-700 dark:text-amber-300 mt-1">
															{getPresetRuntimeHint(preset)}
														</div>
													{/if}
												</div>
										</div>
									</button>
								{/each}
							</div>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
</Modal>
