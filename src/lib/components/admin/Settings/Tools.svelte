<script lang="ts">
	import { toast } from 'svelte-sonner';
	import fileSaver from 'file-saver';
	import { onMount, getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { config, tools as toolsStore, user } from '$lib/stores';
	import { getBackendConfig } from '$lib/apis';

	const { saveAs } = fileSaver;

	const dispatch = createEventDispatcher();
	const i18n: Writable<any> = getContext('i18n');

	export let roleAware = false;

	// 组件导入
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Connection from '$lib/components/chat/Settings/Tools/Connection.svelte';
	import AddServerModal from '$lib/components/AddServerModal.svelte';
	import MCPServerModal from '$lib/components/admin/Settings/Tools/MCPServerModal.svelte';
	import ValvesModal from '$lib/components/workspace/common/ValvesModal.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';

	// API导入
	import {
		getToolServerConnections,
		setToolServerConnections,
		getNativeToolsConfig,
		setNativeToolsConfig,
		getMCPServerConnections,
		setMCPServerConnections
	} from '$lib/apis/configs';

	import {
		createNewTool,
		deleteToolById,
		exportTools,
		getToolList,
		getTools,
		updateToolById
	} from '$lib/apis/tools';

	// ==================== 标签页状态 ====================
	$: canManageGlobalToolPolicies = !roleAware || $user?.role === 'admin';

	let selectedTab: 'native' | 'mcp' | 'workspace' | 'openapi' = 'native';

	const tabMeta: Record<string, { label: string; description: string; badgeColor: string; iconColor: string }> = {
		native:    { label: '内置功能',       description: '管理工具调用模式、内置搜索、知识库、图像生成等原生工具开关。', badgeColor: 'bg-emerald-50 dark:bg-emerald-950/30', iconColor: 'text-emerald-500 dark:text-emerald-400' },
		mcp:       { label: 'MCP 服务器',     description: '通过 MCP 协议连接外部工具服务器，支持 HTTP 与 stdio 传输。', badgeColor: 'bg-violet-50 dark:bg-violet-950/30',  iconColor: 'text-violet-500 dark:text-violet-400' },
		workspace: { label: '工作空间工具',   description: '管理自定义 Python 工具，支持导入、导出和阀门配置。',           badgeColor: 'bg-blue-50 dark:bg-blue-950/30',    iconColor: 'text-blue-500 dark:text-blue-400' },
		openapi:   { label: 'OpenAPI 服务器', description: '连接兼容 OpenAPI 规范的工具服务器，适用于企业级集成。',       badgeColor: 'bg-orange-50 dark:bg-orange-950/30', iconColor: 'text-orange-500 dark:text-orange-400' }
	};

	$: activeTabMeta = tabMeta[selectedTab];
	// 非管理员时自动跳到 mcp
	$: if (!canManageGlobalToolPolicies && selectedTab === 'native') selectedTab = 'mcp';

	// ==================== 加载状态 ====================
	let saving = false;
	let initialSnapshot: any = null;
	const MIN_TOOL_CALL_ROUNDS = 1;
	const MAX_TOOL_CALL_ROUNDS = 30;
	const DEFAULT_MAX_TOOL_CALL_ROUNDS = 15;

	// ==================== Native Features 配置 ====================
	const defaultNativeToolsConfig = {
		TOOL_CALLING_MODE: 'default', // 'default' | 'native'
		ENABLE_INTERLEAVED_THINKING: false,
		MAX_TOOL_CALL_ROUNDS: DEFAULT_MAX_TOOL_CALL_ROUNDS,

		// Built-in system tools (Native Mode)
		ENABLE_WEB_SEARCH_TOOL: true,
		ENABLE_URL_FETCH: true,
		ENABLE_URL_FETCH_RENDERED: false,

		ENABLE_LIST_KNOWLEDGE_BASES: true,
		ENABLE_SEARCH_KNOWLEDGE_BASES: true,
		ENABLE_QUERY_KNOWLEDGE_FILES: true,
		ENABLE_VIEW_KNOWLEDGE_FILE: true,

		ENABLE_IMAGE_GENERATION_TOOL: true,
		ENABLE_IMAGE_EDIT: false,

		ENABLE_MEMORY_TOOLS: true,
		ENABLE_NOTES: false,
		ENABLE_CHAT_HISTORY_TOOLS: true,
		ENABLE_TIME_TOOLS: true,
		ENABLE_CHANNEL_TOOLS: true,
		ENABLE_TERMINAL_TOOL: false
	};
	let nativeToolsConfig = cloneSettingsSnapshot(defaultNativeToolsConfig);

	const normalizeNativeToolsConfig = (value: Record<string, any> | null | undefined) => ({
		...cloneSettingsSnapshot(defaultNativeToolsConfig),
		...(value ?? {}),
		TOOL_CALLING_MODE:
			value?.TOOL_CALLING_MODE === 'native' || value?.TOOL_CALLING_MODE === 'default'
				? value.TOOL_CALLING_MODE
				: defaultNativeToolsConfig.TOOL_CALLING_MODE,
		ENABLE_INTERLEAVED_THINKING: Boolean(value?.ENABLE_INTERLEAVED_THINKING),
		MAX_TOOL_CALL_ROUNDS: clampMaxToolCallRounds(
			Number(value?.MAX_TOOL_CALL_ROUNDS ?? defaultNativeToolsConfig.MAX_TOOL_CALL_ROUNDS)
		),
		ENABLE_WEB_SEARCH_TOOL:
			value?.ENABLE_WEB_SEARCH_TOOL ?? defaultNativeToolsConfig.ENABLE_WEB_SEARCH_TOOL,
		ENABLE_URL_FETCH: value?.ENABLE_URL_FETCH ?? defaultNativeToolsConfig.ENABLE_URL_FETCH,
		ENABLE_URL_FETCH_RENDERED:
			value?.ENABLE_URL_FETCH_RENDERED ?? defaultNativeToolsConfig.ENABLE_URL_FETCH_RENDERED,
		ENABLE_LIST_KNOWLEDGE_BASES:
			value?.ENABLE_LIST_KNOWLEDGE_BASES ??
			defaultNativeToolsConfig.ENABLE_LIST_KNOWLEDGE_BASES,
		ENABLE_SEARCH_KNOWLEDGE_BASES:
			value?.ENABLE_SEARCH_KNOWLEDGE_BASES ??
			defaultNativeToolsConfig.ENABLE_SEARCH_KNOWLEDGE_BASES,
		ENABLE_QUERY_KNOWLEDGE_FILES:
			value?.ENABLE_QUERY_KNOWLEDGE_FILES ??
			defaultNativeToolsConfig.ENABLE_QUERY_KNOWLEDGE_FILES,
		ENABLE_VIEW_KNOWLEDGE_FILE:
			value?.ENABLE_VIEW_KNOWLEDGE_FILE ?? defaultNativeToolsConfig.ENABLE_VIEW_KNOWLEDGE_FILE,
		ENABLE_IMAGE_GENERATION_TOOL:
			value?.ENABLE_IMAGE_GENERATION_TOOL ??
			defaultNativeToolsConfig.ENABLE_IMAGE_GENERATION_TOOL,
		ENABLE_IMAGE_EDIT: value?.ENABLE_IMAGE_EDIT ?? defaultNativeToolsConfig.ENABLE_IMAGE_EDIT,
		ENABLE_MEMORY_TOOLS:
			value?.ENABLE_MEMORY_TOOLS ?? defaultNativeToolsConfig.ENABLE_MEMORY_TOOLS,
		ENABLE_NOTES: value?.ENABLE_NOTES ?? defaultNativeToolsConfig.ENABLE_NOTES,
		ENABLE_CHAT_HISTORY_TOOLS:
			value?.ENABLE_CHAT_HISTORY_TOOLS ?? defaultNativeToolsConfig.ENABLE_CHAT_HISTORY_TOOLS,
		ENABLE_TIME_TOOLS: value?.ENABLE_TIME_TOOLS ?? defaultNativeToolsConfig.ENABLE_TIME_TOOLS,
		ENABLE_CHANNEL_TOOLS:
			value?.ENABLE_CHANNEL_TOOLS ?? defaultNativeToolsConfig.ENABLE_CHANNEL_TOOLS,
		ENABLE_TERMINAL_TOOL:
			value?.ENABLE_TERMINAL_TOOL ?? defaultNativeToolsConfig.ENABLE_TERMINAL_TOOL
	});

	// Build snapshot for dirty detection
	const buildSnapshot = () => ({
		native: {
			TOOL_CALLING_MODE: nativeToolsConfig.TOOL_CALLING_MODE,
			ENABLE_INTERLEAVED_THINKING: nativeToolsConfig.ENABLE_INTERLEAVED_THINKING,
			MAX_TOOL_CALL_ROUNDS: nativeToolsConfig.MAX_TOOL_CALL_ROUNDS,
			ENABLE_WEB_SEARCH_TOOL: nativeToolsConfig.ENABLE_WEB_SEARCH_TOOL,
			ENABLE_URL_FETCH: nativeToolsConfig.ENABLE_URL_FETCH,
			ENABLE_URL_FETCH_RENDERED: nativeToolsConfig.ENABLE_URL_FETCH_RENDERED,
			ENABLE_LIST_KNOWLEDGE_BASES: nativeToolsConfig.ENABLE_LIST_KNOWLEDGE_BASES,
			ENABLE_SEARCH_KNOWLEDGE_BASES: nativeToolsConfig.ENABLE_SEARCH_KNOWLEDGE_BASES,
			ENABLE_QUERY_KNOWLEDGE_FILES: nativeToolsConfig.ENABLE_QUERY_KNOWLEDGE_FILES,
			ENABLE_VIEW_KNOWLEDGE_FILE: nativeToolsConfig.ENABLE_VIEW_KNOWLEDGE_FILE,
			ENABLE_IMAGE_GENERATION_TOOL: nativeToolsConfig.ENABLE_IMAGE_GENERATION_TOOL,
			ENABLE_IMAGE_EDIT: nativeToolsConfig.ENABLE_IMAGE_EDIT,
			ENABLE_MEMORY_TOOLS: nativeToolsConfig.ENABLE_MEMORY_TOOLS,
			ENABLE_NOTES: nativeToolsConfig.ENABLE_NOTES,
			ENABLE_CHAT_HISTORY_TOOLS: nativeToolsConfig.ENABLE_CHAT_HISTORY_TOOLS,
			ENABLE_TIME_TOOLS: nativeToolsConfig.ENABLE_TIME_TOOLS,
			ENABLE_CHANNEL_TOOLS: nativeToolsConfig.ENABLE_CHANNEL_TOOLS,
			ENABLE_TERMINAL_TOOL: nativeToolsConfig.ENABLE_TERMINAL_TOOL
		},
		mcp: mcpServers,
		openapi: openAPIServers
	});

	let snapshot: {
		native: Record<string, any>;
		mcp: Array<any>;
		openapi: Array<any>;
	};
	$: {
		nativeToolsConfig;
		mcpServers;
		openAPIServers;
		snapshot = buildSnapshot();
	}
	$: dirtySections = initialSnapshot && snapshot
		? {
				native: !isSettingsSnapshotEqual(snapshot.native, initialSnapshot.native),
				mcp: !isSettingsSnapshotEqual(snapshot.mcp, initialSnapshot.mcp),
				openapi: !isSettingsSnapshotEqual(snapshot.openapi, initialSnapshot.openapi),
				workspace: false
			}
		: {
				native: false,
				mcp: false,
				openapi: false,
				workspace: false
			};

		// ==================== Workspace Tools 配置 ====================
		let workspaceTools: Array<any> = [];
		let toolsImportInputElement: HTMLInputElement;

		let showValvesModal = false;
		let selectedValvesToolId: string | null = null;

		// ==================== MCP 配置 ====================
		type MCPRuntimeCommandCapability = {
			available: boolean;
			message?: string | null;
		};

		type MCPRuntimeCapabilities = {
			commands: Record<string, MCPRuntimeCommandCapability>;
		};

		type MCPRuntimeProfile = 'main' | 'slim' | 'custom';

		const buildDefaultMCPRuntimeCapabilities = (): MCPRuntimeCapabilities => ({
			commands: {
				npx: { available: true, message: null },
				uvx: { available: true, message: null },
				git: { available: true, message: null }
			}
		});

		const normalizeMCPRuntimeCapabilities = (value: any): MCPRuntimeCapabilities => {
			const defaults = buildDefaultMCPRuntimeCapabilities();
			const commands = { ...defaults.commands };

			for (const [command, capability] of Object.entries(value?.commands ?? {})) {
				commands[command] = {
					available: (capability as any)?.available !== false,
					message: typeof (capability as any)?.message === 'string' ? (capability as any).message : null
				};
			}

			return { commands };
		};

		const normalizeMCPRuntimeProfile = (value: any): MCPRuntimeProfile =>
			value === 'main' || value === 'slim' ? value : 'custom';

		let mcpServers: Array<any> = [];
		let mcpRuntimeCapabilities: MCPRuntimeCapabilities = buildDefaultMCPRuntimeCapabilities();
		let mcpRuntimeProfile: MCPRuntimeProfile = 'custom';
		let showMCPModal = false;
		let editingMCPServerIndex: number | null = null;

	const normalizeMCPServer = (server: any) => ({
		transport_type: server?.transport_type ?? 'http',
		url: server?.url ?? '',
		command: server?.command ?? '',
		args: Array.isArray(server?.args) ? [...server.args] : [],
		env: server?.env ?? {},
		headers: { ...(server?.headers ?? {}) },
		name: server?.name,
		description: server?.description,
		auth_type: server?.auth_type ?? 'none',
		key: server?.key,
		config: {
			...(server?.config ?? {}),
			enable: server?.config?.enable ?? server?.enabled ?? true
		},
		server_info: server?.server_info ?? undefined,
		tool_count: server?.tool_count ?? undefined,
		verified_at: server?.verified_at ?? undefined
	});

	const getServerDisplayName = (server: any): string => {
		if (server.name) return server.name;
		if (server.server_info?.name) return server.server_info.name;
		if ((server.transport_type ?? 'http') === 'stdio') {
			return server.command || 'stdio MCP Server';
		}
		try {
			return new URL(server.url).hostname;
		} catch {
			return server.url;
		}
	};

	const getMCPTransportLabel = (server: any): string =>
		(server.transport_type ?? 'http') === 'stdio' ? 'stdio' : 'HTTP';

	const getMCPPrimaryValue = (server: any): string =>
		(server.transport_type ?? 'http') === 'stdio' ? server.command || '' : server.url || '';

	const getMCPHeaderCount = (server: any): number =>
		Object.keys(server?.headers ?? {}).length;

	const formatVerifiedAt = (value?: string): string => {
		if (!value) return '';
		const parsed = new Date(value);
		if (Number.isNaN(parsed.getTime())) return value;
		return parsed.toLocaleString();
	};

	// ==================== OpenAPI Servers 配置 ====================
	let openAPIServers: Array<{
		url: string;
		path: string;
		auth_type?: string;
		key?: string;
		config?: any;
	}> = [];
	let showOpenAPIModal = false;

	// ==================== 处理函数 ====================

	// Reset section to initial state
	const resetSection = (section: 'native' | 'mcp' | 'openapi') => {
		if (section === 'native' && !canManageGlobalToolPolicies) return;
		if (!initialSnapshot) return;

		if (section === 'native') {
			nativeToolsConfig = normalizeNativeToolsConfig(initialSnapshot.native);
		} else if (section === 'mcp') {
			mcpServers = cloneSettingsSnapshot(initialSnapshot.mcp);
		} else if (section === 'openapi') {
			openAPIServers = cloneSettingsSnapshot(initialSnapshot.openapi);
		}

		toast.success($i18n.t('Settings reset'));
	};

	const resetCurrentTab = () => {
		if (selectedTab === 'native' || selectedTab === 'mcp' || selectedTab === 'openapi') {
			resetSection(selectedTab);
		}
	};
	const saveNativeToolsConfig = async ({ silent = false }: { silent?: boolean } = {}) => {
		const res = await setNativeToolsConfig(localStorage.token, nativeToolsConfig).catch(() => {
			if (!silent) toast.error($i18n.t('保存内置工具配置失败'));
			return null;
		});

		if (res) {
			nativeToolsConfig = normalizeNativeToolsConfig(res);

			// Keep the global config store in sync so chat UI reflects changes immediately.
			try {
				const backendConfig = await getBackendConfig();
				if (backendConfig) config.set(backendConfig);
			} catch (e) {
				// Non-fatal; the setting is saved server-side already.
				console.warn('Failed to refresh backend config', e);
			}

			if (!silent) toast.success($i18n.t('内置工具配置已保存'));
			return true;
		}

		return false;
	};

	// Workspace Tools 处理
	const clampMaxToolCallRounds = (value: number) => {
		if (!Number.isFinite(value)) return DEFAULT_MAX_TOOL_CALL_ROUNDS;
		return Math.min(MAX_TOOL_CALL_ROUNDS, Math.max(MIN_TOOL_CALL_ROUNDS, value));
	};

	const handleMaxToolCallRoundsInput = (event: Event) => {
		const target = event.currentTarget as HTMLInputElement | null;
		const value = Number(target?.value ?? DEFAULT_MAX_TOOL_CALL_ROUNDS);
		nativeToolsConfig.MAX_TOOL_CALL_ROUNDS = Number.isFinite(value)
			? value
			: DEFAULT_MAX_TOOL_CALL_ROUNDS;
		nativeToolsConfig = nativeToolsConfig;
	};

	const handleMaxToolCallRoundsBlur = () => {
		nativeToolsConfig.MAX_TOOL_CALL_ROUNDS = clampMaxToolCallRounds(
			Number(nativeToolsConfig.MAX_TOOL_CALL_ROUNDS)
		);
		nativeToolsConfig = nativeToolsConfig;
	};

	const loadWorkspaceTools = async () => {
		const res = await getToolList(localStorage.token).catch(() => {
			toast.error($i18n.t('加载工作空间工具失败'));
			return null;
		});

		workspaceTools = res || [];
	};

	const deleteWorkspaceTool = async (toolId: string) => {
		if (!confirm($i18n.t('确定要删除该工具吗？'))) return;

		const res = await deleteToolById(localStorage.token, toolId).catch(() => {
			toast.error($i18n.t('删除工作空间工具失败'));
			return null;
		});

		if (res) {
			toast.success($i18n.t('工作空间工具已删除'));
			await loadWorkspaceTools();
			toolsStore.set(await getTools(localStorage.token));
		}
	};

	const exportWorkspaceTools = async () => {
		const res = await exportTools(localStorage.token).catch(() => {
			toast.error($i18n.t('导出工作空间工具失败'));
			return null;
		});

		if (res) {
			const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' });
			saveAs(blob, `workspace-tools-export-${Date.now()}.json`);
			toast.success($i18n.t('工作空间工具已导出'));
		}
	};

	const importWorkspaceTools = async () => {
		toolsImportInputElement?.click();
	};

	const handleWorkspaceToolsImport = async () => {
		const file = toolsImportInputElement?.files?.[0];
		if (!file) return;

		try {
			const text = await file.text();
			let items = JSON.parse(text);
			if (!Array.isArray(items)) items = [items];

			for (const item of items) {
				if (!item?.id || !item?.name || !item?.content) continue;

				const payload = {
					id: item.id,
					name: item.name,
					meta: item.meta ?? { description: '' },
					content: item.content,
					access_control: item.access_control ?? null
				};

				const created = await createNewTool(localStorage.token, payload).catch(async () => {
					return await updateToolById(localStorage.token, payload.id, payload).catch(() => null);
				});

				if (!created) {
					toast.error($i18n.t(`导入失败: {{id}}`, { id: payload.id }));
				}
			}

			toast.success($i18n.t('工作空间工具已导入'));
			await loadWorkspaceTools();
			toolsStore.set(await getTools(localStorage.token));
		} catch (e) {
			toast.error($i18n.t('导入文件格式不正确'));
		} finally {
			toolsImportInputElement.value = '';
		}
	};

	// MCP Servers 处理
	const loadMCPServers = async () => {
		const res = await getMCPServerConnections(localStorage.token).catch(() => {
			toast.error($i18n.t('加载 MCP 服务器失败'));
			return null;
		});

		mcpServers = (res?.MCP_SERVER_CONNECTIONS || []).map(normalizeMCPServer);
		mcpRuntimeCapabilities = normalizeMCPRuntimeCapabilities(res?.MCP_RUNTIME_CAPABILITIES);
		mcpRuntimeProfile = normalizeMCPRuntimeProfile(res?.MCP_RUNTIME_PROFILE);
	};

	const addMCPServer = async (server: any) => {
		const previous = cloneSettingsSnapshot(mcpServers);
		mcpServers = [...mcpServers, normalizeMCPServer(server)];
		const ok = await saveMCPServers();
		if (!ok) {
			mcpServers = previous;
			throw new Error('保存 MCP 服务器失败');
		}
	};

	const updateMCPServer = async (index: number, server: any) => {
		const previous = cloneSettingsSnapshot(mcpServers);
		mcpServers[index] = normalizeMCPServer(server);
		mcpServers = mcpServers;
		const ok = await saveMCPServers();
		if (!ok) {
			mcpServers = previous;
			throw new Error('保存 MCP 服务器失败');
		}
	};

	const deleteMCPServer = async (index: number) => {
		const previous = cloneSettingsSnapshot(mcpServers);
		mcpServers = mcpServers.filter((_: any, i: number) => i !== index);
		const ok = await saveMCPServers();
		if (!ok) {
			mcpServers = previous;
		}
	};

	const saveMCPServers = async ({ silent = false }: { silent?: boolean } = {}) => {
		const res = await setMCPServerConnections(localStorage.token, {
			MCP_SERVER_CONNECTIONS: mcpServers
		}).catch(() => {
			if (!silent) toast.error($i18n.t('保存 MCP 服务器失败'));
			return null;
		});

		if (res) {
			mcpServers = (res?.MCP_SERVER_CONNECTIONS || []).map(normalizeMCPServer);
			mcpRuntimeCapabilities = normalizeMCPRuntimeCapabilities(res?.MCP_RUNTIME_CAPABILITIES);
			mcpRuntimeProfile = normalizeMCPRuntimeProfile(res?.MCP_RUNTIME_PROFILE);
			if (!silent) toast.success($i18n.t('MCP 服务器已保存'));
			toolsStore.set(await getTools(localStorage.token));
			return true;
		}

		return false;
	};

	// OpenAPI Servers 处理
	const addOpenAPIServer = async (server: {
		url: string;
		path: string;
		auth_type?: string;
		key?: string;
		config?: any;
	}) => {
		openAPIServers = [...openAPIServers, server];
		await saveOpenAPIServers();
	};

	const updateOpenAPIServer = async () => {
		await saveOpenAPIServers();
	};

	const deleteOpenAPIServer = async (index: number) => {
		openAPIServers = openAPIServers.filter((_: any, i: number) => i !== index);
		await saveOpenAPIServers();
	};

	const saveOpenAPIServers = async ({ silent = false }: { silent?: boolean } = {}) => {
		const res = await setToolServerConnections(localStorage.token, {
			TOOL_SERVER_CONNECTIONS: openAPIServers
		}).catch((err) => {
			if (!silent) toast.error($i18n.t('保存 OpenAPI 服务器失败'));
			return null;
		});

		if (res) {
			if (!silent) toast.success($i18n.t('OpenAPI 服务器已保存'));
			toolsStore.set(await getTools(localStorage.token));
			return true;
		}

		return false;
	};

	// ==================== 提交处理 ====================
	const submitHandler = async () => {
		if (saving) return;

		saving = true;
		try {
			const okNative = canManageGlobalToolPolicies
				? await saveNativeToolsConfig({ silent: true })
				: true;
			const okMCP = await saveMCPServers({ silent: true });
			const okOpenAPI = await saveOpenAPIServers({ silent: true });

			if (okNative && okMCP && okOpenAPI) {
				initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
				dispatch('save');
			} else {
				toast.error($i18n.t('保存失败，请检查配置后重试'));
			}
		} finally {
			saving = false;
		}
	};

	// ==================== 初始化 ====================
	onMount(async () => {
		try {
			// 并行加载所有配置（4个请求无依赖关系）
			const [nativeRes, toolServerRes] = await Promise.all([
				getNativeToolsConfig(localStorage.token).catch(() => null),
				getToolServerConnections(localStorage.token).catch(() => ({ TOOL_SERVER_CONNECTIONS: [] })),
				loadWorkspaceTools(),
				loadMCPServers()
			]);

			nativeToolsConfig = normalizeNativeToolsConfig(nativeRes);
			openAPIServers = toolServerRes.TOOL_SERVER_CONNECTIONS || [];

			// 创建初始快照
			initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
		} catch (error) {
			toast.error($i18n.t('加载工具配置失败'));
		}
	});
</script>

<input
	bind:this={toolsImportInputElement}
	type="file"
	accept="application/json"
	class="scroll-mt-2 hidden"
	on:change={() => {
		handleWorkspaceToolsImport();
	}}
/>

<ValvesModal
	bind:show={showValvesModal}
	type="tool"
	id={selectedValvesToolId}
	on:save={() => {
		showValvesModal = false;
	}}
/>

	<MCPServerModal
		bind:show={showMCPModal}
		isAdmin={$user?.role === 'admin'}
		runtimeCapabilities={mcpRuntimeCapabilities}
		runtimeProfile={mcpRuntimeProfile}
		connection={editingMCPServerIndex !== null ? mcpServers[editingMCPServerIndex] : null}
		onSubmit={async (connection) => {
		if (editingMCPServerIndex !== null) {
			await updateMCPServer(editingMCPServerIndex, connection);
		} else {
			await addMCPServer(connection);
		}
		editingMCPServerIndex = null;
	}}
/>

<!-- OpenAPI Server Modal -->
<AddServerModal bind:show={showOpenAPIModal} onSubmit={addOpenAPIServer} />

<form
	class="flex h-full min-h-0 flex-col text-sm"
	on:submit|preventDefault={async () => {
		await submitHandler();
	}}
>
		<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
			<div class="max-w-6xl mx-auto space-y-6">
				<!-- ==================== Hero Section ==================== -->
				<section class="glass-section p-5 space-y-5">
					<div class="@container flex flex-col gap-5">
						<div class="flex flex-col gap-4 @[64rem]:flex-row @[64rem]:items-start @[64rem]:justify-between">
							<div class="min-w-0 @[64rem]:flex-1">
								<!-- Breadcrumb -->
								<div class="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-full border border-gray-200/80 bg-white/80 px-3.5 text-xs font-medium leading-none text-gray-600 dark:border-gray-700/80 dark:bg-gray-900/70 dark:text-gray-300">
									<span class="leading-none text-gray-400 dark:text-gray-500">{$i18n.t('Settings')}</span>
									<span class="leading-none text-gray-300 dark:text-gray-600">/</span>
									<span class="leading-none text-gray-900 dark:text-white">{$i18n.t('工具集成')}</span>
								</div>

								<!-- Icon badge + title + description -->
								<div class="mt-3 flex items-start gap-3">
									<div class="glass-icon-badge {activeTabMeta.badgeColor}">
										{#if selectedTab === 'native'}
											<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
												<path stroke-linecap="round" stroke-linejoin="round" d="M21.75 6.75a4.5 4.5 0 0 1-4.884 4.484c-1.076-.091-2.264.071-2.95.904l-7.152 8.684a2.548 2.548 0 1 1-3.586-3.586l8.684-7.152c.833-.686.995-1.874.904-2.95a4.5 4.5 0 0 1 6.336-4.486l-3.276 3.276a3.004 3.004 0 0 0 2.25 2.25l3.276-3.276c.256.565.398 1.192.398 1.852Z" />
												<path stroke-linecap="round" stroke-linejoin="round" d="M4.867 19.125h.008v.008h-.008v-.008Z" />
											</svg>
										{:else if selectedTab === 'mcp'}
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180" fill="none" stroke="currentColor" stroke-width="12" stroke-linecap="round" class="size-[18px] {activeTabMeta.iconColor}">
												<path d="M18 84.8528L85.8822 16.9706C95.2548 7.59798 110.451 7.59798 119.823 16.9706C129.196 26.3431 129.196 41.5391 119.823 50.9117L68.5581 102.177" />
												<path d="M69.2652 101.47L119.823 50.9117C129.196 41.5391 144.392 41.5391 153.765 50.9117L154.118 51.2652C163.491 60.6378 163.491 75.8338 154.118 85.2063L92.7248 146.6C89.6006 149.724 89.6006 154.789 92.7248 157.913L105.331 170.52" />
												<path d="M102.853 33.9411L52.6482 84.1457C43.2756 93.5183 43.2756 108.714 52.6482 118.087C62.0208 127.459 77.2167 127.459 86.5893 118.087L136.794 67.8822" />
											</svg>
										{:else if selectedTab === 'workspace'}
											<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
												<path stroke-linecap="round" stroke-linejoin="round" d="M20.25 14.15v4.25c0 1.094-.787 2.036-1.872 2.18-2.087.277-4.216.42-6.378.42s-4.291-.143-6.378-.42c-1.085-.144-1.872-1.086-1.872-2.18v-4.25m16.5 0a2.18 2.18 0 0 0 .75-1.661V8.706c0-1.081-.768-2.015-1.837-2.175a48.114 48.114 0 0 0-3.413-.387m4.5 8.006c-.194.165-.42.295-.673.38A23.978 23.978 0 0 1 12 15.75c-2.648 0-5.195-.429-7.577-1.22a2.016 2.016 0 0 1-.673-.38m0 0A2.18 2.18 0 0 1 3 12.489V8.706c0-1.081.768-2.015 1.837-2.175a48.111 48.111 0 0 1 3.413-.387m7.5 0V5.25A2.25 2.25 0 0 0 13.5 3h-3a2.25 2.25 0 0 0-2.25 2.25v.894m7.5 0a48.667 48.667 0 0 0-7.5 0" />
											</svg>
										{:else}
											<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-[18px] {activeTabMeta.iconColor}">
												<path stroke-linecap="round" stroke-linejoin="round" d="M21.75 17.25v-.228a4.5 4.5 0 0 0-.12-1.03l-2.268-9.64a3.375 3.375 0 0 0-3.285-2.602H7.923a3.375 3.375 0 0 0-3.285 2.602l-2.268 9.64a4.5 4.5 0 0 0-.12 1.03v.228m19.5 0a3 3 0 0 1-3 3H5.25a3 3 0 0 1-3-3m19.5 0a3 3 0 0 0-3-3H5.25a3 3 0 0 0-3 3m16.5 0h.008v.008h-.008v-.008Zm-3 0h.008v.008h-.008v-.008Z" />
											</svg>
										{/if}
									</div>
									<div class="min-w-0">
										<div class="flex items-center gap-3">
											<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
												{$i18n.t(activeTabMeta.label)}
											</div>
											{#if selectedTab !== 'workspace'}
												<InlineDirtyActions
													dirty={dirtySections[selectedTab]}
													{saving}
													on:reset={resetCurrentTab}
												/>
											{/if}
										</div>
										<p class="mt-1 text-xs text-gray-400 dark:text-gray-500">
											{$i18n.t(activeTabMeta.description)}
										</p>
									</div>
								</div>
							</div>

							<!-- Tab buttons -->
							<div class="inline-flex max-w-full flex-wrap items-center gap-2 self-start rounded-2xl bg-gray-100 p-1 dark:bg-gray-850 @[64rem]:ml-auto @[64rem]:mt-11 @[64rem]:flex-nowrap @[64rem]:justify-end @[64rem]:shrink-0">
								{#if canManageGlobalToolPolicies}
									<button type="button" class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === 'native' ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`} on:click={() => { selectedTab = 'native'; }}>
										<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
											<path stroke-linecap="round" stroke-linejoin="round" d="M21.75 6.75a4.5 4.5 0 0 1-4.884 4.484c-1.076-.091-2.264.071-2.95.904l-7.152 8.684a2.548 2.548 0 1 1-3.586-3.586l8.684-7.152c.833-.686.995-1.874.904-2.95a4.5 4.5 0 0 1 6.336-4.486l-3.276 3.276a3.004 3.004 0 0 0 2.25 2.25l3.276-3.276c.256.565.398 1.192.398 1.852Z" />
										</svg>
										<span class="min-w-0 truncate">{$i18n.t('内置功能')}</span>
									</button>
								{/if}
								<button type="button" class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === 'mcp' ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`} on:click={() => { selectedTab = 'mcp'; }}>
									<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180" fill="none" stroke="currentColor" stroke-width="12" stroke-linecap="round" class="size-4">
										<path d="M18 84.8528L85.8822 16.9706C95.2548 7.59798 110.451 7.59798 119.823 16.9706C129.196 26.3431 129.196 41.5391 119.823 50.9117L68.5581 102.177" />
										<path d="M69.2652 101.47L119.823 50.9117C129.196 41.5391 144.392 41.5391 153.765 50.9117L154.118 51.2652C163.491 60.6378 163.491 75.8338 154.118 85.2063L92.7248 146.6C89.6006 149.724 89.6006 154.789 92.7248 157.913L105.331 170.52" />
										<path d="M102.853 33.9411L52.6482 84.1457C43.2756 93.5183 43.2756 108.714 52.6482 118.087C62.0208 127.459 77.2167 127.459 86.5893 118.087L136.794 67.8822" />
									</svg>
									<span class="min-w-0 truncate">{$i18n.t('MCP')}</span>
								</button>
								<button type="button" class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === 'workspace' ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`} on:click={() => { selectedTab = 'workspace'; }}>
									<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
										<path stroke-linecap="round" stroke-linejoin="round" d="M20.25 14.15v4.25c0 1.094-.787 2.036-1.872 2.18-2.087.277-4.216.42-6.378.42s-4.291-.143-6.378-.42c-1.085-.144-1.872-1.086-1.872-2.18v-4.25m16.5 0a2.18 2.18 0 0 0 .75-1.661V8.706c0-1.081-.768-2.015-1.837-2.175a48.114 48.114 0 0 0-3.413-.387m4.5 8.006c-.194.165-.42.295-.673.38A23.978 23.978 0 0 1 12 15.75c-2.648 0-5.195-.429-7.577-1.22a2.016 2.016 0 0 1-.673-.38m0 0A2.18 2.18 0 0 1 3 12.489V8.706c0-1.081.768-2.015 1.837-2.175a48.111 48.111 0 0 1 3.413-.387m7.5 0V5.25A2.25 2.25 0 0 0 13.5 3h-3a2.25 2.25 0 0 0-2.25 2.25v.894m7.5 0a48.667 48.667 0 0 0-7.5 0" />
									</svg>
									<span class="min-w-0 truncate">{$i18n.t('工作空间')}</span>
								</button>
								<button type="button" class={`flex min-w-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all ${selectedTab === 'openapi' ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}`} on:click={() => { selectedTab = 'openapi'; }}>
									<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="size-4">
										<path stroke-linecap="round" stroke-linejoin="round" d="M21.75 17.25v-.228a4.5 4.5 0 0 0-.12-1.03l-2.268-9.64a3.375 3.375 0 0 0-3.285-2.602H7.923a3.375 3.375 0 0 0-3.285 2.602l-2.268 9.64a4.5 4.5 0 0 0-.12 1.03v.228m19.5 0a3 3 0 0 1-3 3H5.25a3 3 0 0 1-3-3m19.5 0a3 3 0 0 0-3-3H5.25a3 3 0 0 0-3 3m16.5 0h.008v.008h-.008v-.008Zm-3 0h.008v.008h-.008v-.008Z" />
									</svg>
									<span class="min-w-0 truncate">{$i18n.t('OpenAPI')}</span>
								</button>
							</div>
						</div>
					</div>
				</section>

				<!-- ==================== Tab Content ==================== -->
				{#if selectedTab === 'native' && canManageGlobalToolPolicies}
			<section
				class="p-5 space-y-3 transition-all duration-300 {dirtySections.native
					? 'glass-section glass-section-dirty'
					: 'glass-section'}"
			>
						<!-- Tool Calling Mode -->
						<div
							class="glass-item p-4"
						>
							<div class="flex items-center justify-between mb-2.5">
								<div class="text-sm font-medium">{$i18n.t('工具调用模式')}</div>
								<HaloSelect
									bind:value={nativeToolsConfig.TOOL_CALLING_MODE}
									options={[
										{ value: 'default', label: $i18n.t('默认') },
										{ value: 'native', label: $i18n.t('原生') }
									]}
									className="w-fit text-right"
								/>
							</div>
							<div class="text-xs text-gray-500 dark:text-gray-400">
								{#if nativeToolsConfig.TOOL_CALLING_MODE === 'default'}
									{$i18n.t('使用基于提示词的逻辑，几乎兼容所有模型，但可能较慢。')}
								{:else}
									{$i18n.t('使用内置工具调用实现更快、更可靠的多步骤操作。需要高质量模型。')}
								{/if}
							</div>
						</div>

						<!-- Interleaved Thinking -->
						{#if nativeToolsConfig.TOOL_CALLING_MODE === 'native'}
							<div
								class="glass-item p-4"
							>
								<div class="flex items-center justify-between">
									<div>
										<div class="text-sm font-medium">{$i18n.t('交错思考')}</div>
										<div class="text-xs text-gray-500 mt-1">
											{$i18n.t('启用思考-行动-思考循环以实现深度研究能力')}
										</div>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_INTERLEAVED_THINKING} />
								</div>
							</div>
						{/if}

						<!-- Max Tool Call Rounds -->
						<div
							class="glass-item p-4"
						>
							<div class="flex items-center justify-between">
								<div>
									<div class="text-sm font-medium">{$i18n.t('最大工具调用轮数')}</div>
									<div class="text-xs text-gray-500 mt-1">
										{$i18n.t(
											'限制单条消息内工具 follow-up 的最大轮数（1-30），用于兼容多步检索并防止死循环'
										)}
									</div>
								</div>
								<input
									type="number"
									min={MIN_TOOL_CALL_ROUNDS}
									max={MAX_TOOL_CALL_ROUNDS}
									step="1"
									class="w-24 py-1.5 px-2 text-sm text-right glass-input"
									value={nativeToolsConfig.MAX_TOOL_CALL_ROUNDS}
									on:input={handleMaxToolCallRoundsInput}
									on:blur={handleMaxToolCallRoundsBlur}
								/>
							</div>
						</div>

						<!-- Web Tools -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('网络工具')}</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('网络搜索')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono">search_web</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_WEB_SEARCH_TOOL} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('网址抓取')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono">fetch_url</code>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_URL_FETCH} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('网址抓取（渲染）')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>fetch_url_rendered</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_URL_FETCH_RENDERED} />
								</div>
							</div>
						</div>

						<!-- Knowledge Tools -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('知识库工具')}</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('列出知识库')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>list_knowledge_bases</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_LIST_KNOWLEDGE_BASES} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('搜索知识库')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_knowledge_bases</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_SEARCH_KNOWLEDGE_BASES} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('检索知识内容')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>query_knowledge_bases</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_knowledge_files</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_QUERY_KNOWLEDGE_FILES} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('查看知识文件')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>view_knowledge_file</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_VIEW_KNOWLEDGE_FILE} />
								</div>
							</div>
						</div>

						<!-- Image Tools -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('图像工具')}</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('生成图像')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>generate_image</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_IMAGE_GENERATION_TOOL} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('编辑图像')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono">edit_image</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_IMAGE_EDIT} />
								</div>
								<div class="text-xs text-gray-400 dark:text-gray-500 pt-1">
									{$i18n.t('提示：图像工具仅在对话中启用「图像生成」功能且用户具备权限时注入。')}
								</div>
							</div>
						</div>

						<!-- Memory Tools -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('记忆工具')}</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('启用记忆工具')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono">add_memory</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_memories</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>forget_memory</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_MEMORY_TOOLS} />
								</div>
							</div>
						</div>

						<!-- Notes Tools -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('笔记工具')}</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('启用笔记工具（实验）')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono">add_note</code>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_notes</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_NOTES} />
								</div>
							</div>
						</div>

						<!-- Chat & Time Tools -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('其他工具')}</div>
							<div class="space-y-2">
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('对话历史')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_chats</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_CHAT_HISTORY_TOOLS} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('时间与日期')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>get_current_time</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>get_current_date</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>get_current_timestamp</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_TIME_TOOLS} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('频道工具')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_channels</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>search_channel_messages</code
										>
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>view_channel_message</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_CHANNEL_TOOLS} />
								</div>
								<div class="flex items-center justify-between">
									<div class="text-xs text-gray-500">
										{$i18n.t('终端命令')}
										<code class="text-[11px] bg-gray-100/80 dark:bg-gray-800/60 px-1 py-0.5 rounded font-mono"
											>execute_command</code
										>
									</div>
									<Switch bind:state={nativeToolsConfig.ENABLE_TERMINAL_TOOL} />
								</div>
							</div>
						</div>
			</section>

			{:else if selectedTab === 'mcp'}
			<!-- ==================== MCP Servers ==================== -->
			<section
				class="p-5 space-y-3 transition-all duration-300 {dirtySections.mcp
					? 'glass-section glass-section-dirty'
					: 'glass-section'}"
			>
						<!-- MCP Info -->
						<div
							class="p-3 rounded-xl bg-blue-50/80 dark:bg-blue-950/20 border border-blue-200/50 dark:border-blue-800/30 backdrop-blur-sm"
						>
							<div class="flex items-start gap-2.5">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="1.5"
									stroke="currentColor"
									class="size-4 text-blue-500 dark:text-blue-400 shrink-0 mt-0.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"
									/>
								</svg>
								<div>
									<div class="text-xs font-medium text-blue-700 dark:text-blue-300">
										{$i18n.t('关于 MCP')}
									</div>
									<div class="text-xs leading-relaxed text-blue-600 dark:text-blue-400 mt-0.5">
										{$i18n.t(
											'MCP（模型上下文协议）是一个用于 LLM 与外部工具通信的开放标准。当前支持 HTTP 与 stdio 两种传输方式。'
										)}
									</div>
								</div>
							</div>
						</div>

						<!-- MCP Servers Management -->
						<div
							class="glass-item p-4"
						>
							<div class="flex items-center justify-between mb-2.5">
								<div class="text-sm font-medium">{$i18n.t('管理 MCP 服务器')}</div>
								<Tooltip content={$i18n.t('添加 MCP 服务器')}>
									<button
										class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
										type="button"
										on:click={() => {
											editingMCPServerIndex = null;
											showMCPModal = true;
										}}
									>
										<Plus />
									</button>
								</Tooltip>
							</div>

							<!-- MCP Servers List -->
							{#if mcpServers.length > 0}
								<div class="space-y-2">
									{#each mcpServers as server, idx}
										<div
											class="flex items-center justify-between p-3 glass-item"
										>
											<div class="flex-1 min-w-0">
												<div class="flex items-center gap-2">
													<span
														class="w-2 h-2 rounded-full shrink-0 {server.config?.enable
															? server.verified_at
																? 'bg-green-500'
																: 'bg-gray-400'
															: 'bg-gray-300 dark:bg-gray-600'}"
													></span>
													<div class="text-sm font-medium truncate">
														{getServerDisplayName(server)}
													</div>
													{#if server.tool_count != null}
														<span
															class="px-1.5 py-0.5 text-[10px] rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 shrink-0"
														>
															{server.tool_count}
															{$i18n.t('个工具')}
														</span>
													{/if}
													<span
														class="px-1.5 py-0.5 text-xs rounded bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 shrink-0"
													>
														{getMCPTransportLabel(server)}
													</span>
													{#if server.transport_type !== 'stdio'}
														<span
															class="px-1.5 py-0.5 text-xs rounded bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 shrink-0"
														>
															{server.auth_type || 'none'}
														</span>
														{#if getMCPHeaderCount(server) > 0}
															<span
																class="px-1.5 py-0.5 text-xs rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 shrink-0"
															>
																{$i18n.t('自定义头')} {getMCPHeaderCount(server)}
															</span>
														{/if}
													{/if}
													<span
														class="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-800/70 text-gray-600 dark:text-gray-300 shrink-0"
													>
														{server.verified_at
															? $i18n.t('已验证')
															: $i18n.t('未验证')}
													</span>
												</div>
												<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5 truncate ml-4">
													{getMCPPrimaryValue(server)}
												</div>
												{#if server.verified_at}
													<div class="text-xs text-gray-400 dark:text-gray-500 mt-0.5 ml-4">
														{$i18n.t('上次验证于')} {formatVerifiedAt(server.verified_at)}
													</div>
												{:else}
													<div class="text-xs text-amber-600 dark:text-amber-300 mt-0.5 ml-4">
														{$i18n.t(
															'Saved but not verified yet. Open edit and click "Verify Connection".'
														)}
													</div>
												{/if}
											</div>
											<div class="flex items-center gap-2 ml-3">
												<Switch
													bind:state={server.config.enable}
													on:change={() => {
														updateMCPServer(idx, server);
													}}
												/>
												<Tooltip content={$i18n.t('编辑')}>
													<button
														class="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition"
														type="button"
														on:click={() => {
															editingMCPServerIndex = idx;
															showMCPModal = true;
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															fill="none"
															viewBox="0 0 24 24"
															stroke-width="1.5"
															stroke="currentColor"
															class="size-4"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
															/>
														</svg>
													</button>
												</Tooltip>
												<Tooltip content={$i18n.t('删除')}>
													<button
														class="p-1.5 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-red-600 dark:text-red-400 transition"
														type="button"
														on:click={() => {
															deleteMCPServer(idx);
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															fill="none"
															viewBox="0 0 24 24"
															stroke-width="1.5"
															stroke="currentColor"
															class="size-4"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
															/>
														</svg>
													</button>
												</Tooltip>
											</div>
										</div>
									{/each}
								</div>
							{:else}
								<div class="text-center py-8 text-gray-500">
									<!-- MCP 官方图标 -->
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 180 180"
										fill="none"
										stroke="currentColor"
										stroke-width="12"
										stroke-linecap="round"
										class="size-12 mx-auto mb-3 opacity-50"
									>
										<path
											d="M18 84.8528L85.8822 16.9706C95.2548 7.59798 110.451 7.59798 119.823 16.9706C129.196 26.3431 129.196 41.5391 119.823 50.9117L68.5581 102.177"
										/>
										<path
											d="M69.2652 101.47L119.823 50.9117C129.196 41.5391 144.392 41.5391 153.765 50.9117L154.118 51.2652C163.491 60.6378 163.491 75.8338 154.118 85.2063L92.7248 146.6C89.6006 149.724 89.6006 154.789 92.7248 157.913L105.331 170.52"
										/>
										<path
											d="M102.853 33.9411L52.6482 84.1457C43.2756 93.5183 43.2756 108.714 52.6482 118.087C62.0208 127.459 77.2167 127.459 86.5893 118.087L136.794 67.8822"
										/>
									</svg>
									<div class="text-sm">{$i18n.t('未配置 MCP 服务器')}</div>
									<div class="text-xs mt-1">{$i18n.t('连接到兼容 MCP 的工具服务器')}</div>
								</div>
							{/if}
						</div>

						<!-- MCP Authentication Options Info -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('认证选项')}</div>
							<div class="space-y-2 text-xs text-gray-500">
								<div class="flex items-start gap-2">
									<span class="px-1.5 py-0.5 rounded bg-gray-100/80 dark:bg-gray-800/60 font-mono"
										>None</span
									>
									<span>{$i18n.t('用于不需要令牌的本地或内部 MCP 服务器')}</span>
								</div>
								<div class="flex items-start gap-2">
									<span class="px-1.5 py-0.5 rounded bg-gray-100/80 dark:bg-gray-800/60 font-mono"
										>Bearer</span
									>
									<span>{$i18n.t('用于需要 API 令牌的服务器')}</span>
								</div>
								<div class="flex items-start gap-2">
									<span class="px-1.5 py-0.5 rounded bg-gray-100/80 dark:bg-gray-800/60 font-mono"
										>OAuth 2.1</span
									>
									<span>{$i18n.t('用于带有身份提供商流程的企业级部署')}</span>
								</div>
								<div class="flex items-start gap-2">
									<span class="px-1.5 py-0.5 rounded bg-gray-100/80 dark:bg-gray-800/60 font-mono"
										>Headers</span
									>
									<span>{$i18n.t('适用于 x-consumer-api-key、x-api-key 或供应商要求的专用请求头')}</span>
								</div>
							</div>
						</div>
			</section>

			{:else if selectedTab === 'workspace'}
			<!-- ==================== Workspace Tools 工作区工具 ==================== -->
			<section
				class="p-5 space-y-3 transition-all duration-300 glass-section"
			>
						<!-- Security Warning -->
						<div
							class="p-3 glass-warning"
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
								<div>
									<div class="text-xs font-medium text-amber-700 dark:text-amber-300">
										{$i18n.t('安全警告')}
									</div>
									<div class="text-xs leading-relaxed text-amber-700 dark:text-amber-300 mt-0.5">
										{$i18n.t(
											'工作空间工具会在服务器上执行任意 Python 代码。请仅向受信任的用户授予访问权限。'
										)}
									</div>
								</div>
							</div>
						</div>

						<!-- Tools Management -->
						<div
							class="glass-item p-4"
						>
							<div class="flex items-center justify-between mb-2.5">
								<div class="text-sm font-medium">{$i18n.t('管理工作空间工具')}</div>
								<div class="flex items-center gap-2">
									<Tooltip content={$i18n.t('导入工具')}>
										<button
											class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											type="button"
											on:click={() => {
												importWorkspaceTools();
											}}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="size-4"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
												/>
											</svg>
										</button>
									</Tooltip>
									<Tooltip content={$i18n.t('导出所有工具')}>
										<button
											class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											type="button"
											on:click={() => {
												exportWorkspaceTools();
											}}
										>
											<svg
												xmlns="http://www.w3.org/2000/svg"
												fill="none"
												viewBox="0 0 24 24"
												stroke-width="1.5"
												stroke="currentColor"
												class="size-4"
											>
												<path
													stroke-linecap="round"
													stroke-linejoin="round"
													d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
												/>
											</svg>
										</button>
									</Tooltip>
									<Tooltip content={$i18n.t('创建新工具')}>
										<button
											class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											type="button"
											on:click={() => {
												goto('/workspace/tools/create');
											}}
										>
											<Plus />
										</button>
									</Tooltip>
								</div>
							</div>

							<!-- Tools List -->
							{#if workspaceTools.length > 0}
								<div class="space-y-2">
									{#each workspaceTools as tool, idx}
										<div
											class="flex items-center justify-between p-3 glass-item"
										>
											<div class="flex-1 min-w-0">
												<div class="text-sm font-medium truncate">{tool.name}</div>
												<div class="text-xs text-gray-500 truncate">
													{tool.meta?.description || $i18n.t('无描述')}
												</div>
											</div>
											<div class="flex items-center gap-2 ml-3">
												<Tooltip content={$i18n.t('配置阀门')}>
													<button
														class="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition"
														type="button"
														on:click={() => {
															selectedValvesToolId = tool.id;
															showValvesModal = true;
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															fill="none"
															viewBox="0 0 24 24"
															stroke-width="1.5"
															stroke="currentColor"
															class="size-4"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
															/>
														</svg>
													</button>
												</Tooltip>
												<Tooltip content={$i18n.t('编辑')}>
													<button
														class="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition"
														type="button"
														on:click={() => {
															goto(`/workspace/tools/edit?id=${tool.id}`);
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															fill="none"
															viewBox="0 0 24 24"
															stroke-width="1.5"
															stroke="currentColor"
															class="size-4"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
															/>
														</svg>
													</button>
												</Tooltip>
												<Tooltip content={$i18n.t('删除')}>
													<button
														class="p-1.5 rounded-lg hover:bg-red-100 dark:hover:bg-red-900/30 text-red-600 dark:text-red-400 transition"
														type="button"
														on:click={() => {
															deleteWorkspaceTool(tool.id);
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															fill="none"
															viewBox="0 0 24 24"
															stroke-width="1.5"
															stroke="currentColor"
															class="size-4"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
															/>
														</svg>
													</button>
												</Tooltip>
											</div>
										</div>
									{/each}
								</div>
							{:else}
								<div class="text-center py-8 text-gray-500">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="1.5"
										stroke="currentColor"
										class="size-12 mx-auto mb-3 opacity-50"
									>
										<path stroke-linecap="round" stroke-linejoin="round" d="M20.25 14.15v4.25c0 1.094-.787 2.036-1.872 2.18-2.087.277-4.216.42-6.378.42s-4.291-.143-6.378-.42c-1.085-.144-1.872-1.086-1.872-2.18v-4.25m16.5 0a2.18 2.18 0 0 0 .75-1.661V8.706c0-1.081-.768-2.015-1.837-2.175a48.114 48.114 0 0 0-3.413-.387m4.5 8.006c-.194.165-.42.295-.673.38A23.978 23.978 0 0 1 12 15.75c-2.648 0-5.195-.429-7.577-1.22a2.016 2.016 0 0 1-.673-.38m0 0A2.18 2.18 0 0 1 3 12.489V8.706c0-1.081.768-2.015 1.837-2.175a48.111 48.111 0 0 1 3.413-.387m7.5 0V5.25A2.25 2.25 0 0 0 13.5 3h-3a2.25 2.25 0 0 0-2.25 2.25v.894m7.5 0a48.667 48.667 0 0 0-7.5 0" />
									</svg>
									<div class="text-sm">{$i18n.t('未配置工作空间工具')}</div>
									<div class="text-xs mt-1">{$i18n.t('创建自定义 Python 工具以扩展 LLM 能力')}</div>
								</div>
							{/if}
						</div>
			</section>

			{:else if selectedTab === 'openapi'}
			<!-- ==================== OpenAPI Servers ==================== -->
			<section
				class="p-5 space-y-3 transition-all duration-300 {dirtySections.openapi
					? 'glass-section glass-section-dirty'
					: 'glass-section'}"
			>
						<!-- OpenAPI Info -->
						<div
							class="p-3 rounded-xl bg-green-50/80 dark:bg-green-950/20 border border-green-200/50 dark:border-green-800/30 backdrop-blur-sm"
						>
							<div class="flex items-start gap-2.5">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="1.5"
									stroke="currentColor"
									class="size-4 text-green-500 dark:text-green-400 shrink-0 mt-0.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
									/>
								</svg>
								<div>
									<div class="text-xs font-medium text-green-700 dark:text-green-300">
										{$i18n.t('推荐集成方式')}
									</div>
									<div class="text-xs leading-relaxed text-green-600 dark:text-green-400 mt-0.5">
										{$i18n.t('OpenAPI 是企业级部署的首选集成方式，提供 SSO、API 网关和审计追踪。')}
									</div>
								</div>
							</div>
						</div>

						<!-- OpenAPI Servers Management -->
						<div
							class="glass-item p-4"
						>
							<div class="flex items-center justify-between mb-2.5">
								<div class="text-sm font-medium">{$i18n.t('管理 OpenAPI 服务器')}</div>
								<Tooltip content={$i18n.t('添加 OpenAPI 服务器')}>
									<button
										class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
										type="button"
										on:click={() => {
											showOpenAPIModal = true;
										}}
									>
										<Plus />
									</button>
								</Tooltip>
							</div>

							<!-- OpenAPI Servers List -->
							{#if openAPIServers.length > 0}
								<div class="flex flex-col gap-2">
									{#each openAPIServers as server, idx}
										<Connection
											bind:connection={server}
											onSubmit={() => {
												updateOpenAPIServer();
											}}
											onDelete={() => {
												deleteOpenAPIServer(idx);
											}}
										/>
									{/each}
								</div>
							{:else}
								<div class="text-center py-8 text-gray-500">
									<!-- OpenAPI 服务器图标 -->
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="1.5"
										stroke="currentColor"
										class="size-12 mx-auto mb-3 opacity-50"
									>
										<path stroke-linecap="round" stroke-linejoin="round" d="M21.75 17.25v-.228a4.5 4.5 0 0 0-.12-1.03l-2.268-9.64a3.375 3.375 0 0 0-3.285-2.602H7.923a3.375 3.375 0 0 0-3.285 2.602l-2.268 9.64a4.5 4.5 0 0 0-.12 1.03v.228m19.5 0a3 3 0 0 1-3 3H5.25a3 3 0 0 1-3-3m19.5 0a3 3 0 0 0-3-3H5.25a3 3 0 0 0-3 3m16.5 0h.008v.008h-.008v-.008Zm-3 0h.008v.008h-.008v-.008Z" />
									</svg>
									<div class="text-sm">{$i18n.t('未配置 OpenAPI 服务器')}</div>
									<div class="text-xs mt-1">{$i18n.t('连接到兼容 OpenAPI 的工具服务器')}</div>
								</div>
							{/if}
						</div>

						<!-- OpenAPI Authentication Info -->
						<div
							class="glass-item p-4"
						>
							<div class="text-sm font-medium text-gray-600 dark:text-gray-300 mb-2.5">{$i18n.t('认证选项')}</div>
							<div class="space-y-2 text-xs text-gray-500">
								<div class="flex items-start gap-2">
									<span class="px-1.5 py-0.5 rounded bg-gray-100/80 dark:bg-gray-800/60 font-mono"
										>Bearer</span
									>
									<span>{$i18n.t('使用专用 API 密钥进行认证')}</span>
								</div>
								<div class="flex items-start gap-2">
									<span class="px-1.5 py-0.5 rounded bg-gray-100/80 dark:bg-gray-800/60 font-mono"
										>Session</span
									>
									<span>{$i18n.t('使用当前用户会话凭据')}</span>
								</div>
							</div>
						</div>
			</section>
			{/if}
			</div>
	</div>
</form>
