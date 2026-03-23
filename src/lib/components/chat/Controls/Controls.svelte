<script lang="ts">
	import { createEventDispatcher, getContext, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	const dispatch = createEventDispatcher();
	const i18n: Writable<any> = getContext('i18n');

	import XMark from '$lib/components/icons/XMark.svelte';
	import ArrowPath from '$lib/components/icons/ArrowPath.svelte';
	import AdvancedParams from '../Settings/Advanced/AdvancedParams.svelte';
	import Valves from '$lib/components/chat/Controls/Valves.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import SteppedSlider from '$lib/components/common/SteppedSlider.svelte';

	import { config, settings, user } from '$lib/stores';
	import { getBackendConfig } from '$lib/apis';
	import { getNativeToolsConfig, setNativeToolsConfig } from '$lib/apis/configs';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';
	export let models = [];
	export let chatFiles = [];
	export let params: Record<string, any> = {};

	type ToolCallingMode = 'default' | 'native';
	type SectionKey = 'system' | 'thinking' | 'advanced';

	const FOLLOW_GLOBAL_TOOL_CALLING_MODE = true;
	const THINKING_KEYS = ['reasoning_effort', 'max_thinking_tokens'] as const;
	const ADVANCED_PARAM_KEYS = [
		'stream_response',
		'function_calling',
		'seed',
		'stop',
		'temperature',
		'logit_bias',
		'frequency_penalty',
		'presence_penalty',
		'repeat_penalty',
		'repeat_last_n',
		'mirostat',
		'mirostat_eta',
		'mirostat_tau',
		'top_k',
		'top_p',
		'min_p',
		'tfs_z',
		'num_ctx',
		'num_batch',
		'num_keep',
		'max_tokens',
		'use_mmap',
		'use_mlock',
		'num_thread',
		'num_gpu',
		'template'
	] as const;

	type AdvancedSnapshot = Record<string, any> & {
		toolCallingMode: ToolCallingMode;
	};

	type SectionSnapshot = {
		system: string | null;
		thinking: Record<string, any>;
		advanced: AdvancedSnapshot;
	};

	let systemAck = false;
	let thinkingAck = false;
	let advancedAck = false;
	let baselineLocked = false;
	let initialSectionSnapshot: SectionSnapshot | null = null;
	let sectionSnapshot: SectionSnapshot;
	let previousAdvancedSnapshot: AdvancedSnapshot | null = null;
	let lastFlashAt: Record<SectionKey, number> = {
		system: 0,
		thinking: 0,
		advanced: 0
	};

	const normalizeToolCallingMode = (value: unknown): ToolCallingMode | null => {
		return value === 'default' || value === 'native' ? value : null;
	};

	const normalizeSystemValue = (value: unknown): string | null => {
		if (typeof value !== 'string') {
			return null;
		}

		return value.trim() ? value : null;
	};

	const normalizeAdvancedValue = (key: (typeof ADVANCED_PARAM_KEYS)[number], value: unknown) => {
		if (key === 'stop' || key === 'logit_bias' || key === 'template') {
			if (typeof value !== 'string') {
				return value ?? null;
			}

			return value.trim() ? value : null;
		}

		return value ?? null;
	};

	const getDisplayedToolCallingMode = (): ToolCallingMode =>
		normalizeToolCallingMode(params?.function_calling) ??
		normalizeToolCallingMode($config?.tools?.calling_mode ?? null) ??
		'default';

	const buildAdvancedSnapshot = (): AdvancedSnapshot => ({
		toolCallingMode: getDisplayedToolCallingMode(),
		...Object.fromEntries(
			ADVANCED_PARAM_KEYS.map((key) => [key, normalizeAdvancedValue(key, params?.[key])])
		)
	});

	const buildAdvancedSnapshotFromParams = (
		nextParams: Record<string, any> = params
	): AdvancedSnapshot => ({
		toolCallingMode: getDisplayedToolCallingMode(),
		...Object.fromEntries(
			ADVANCED_PARAM_KEYS.map((key) => [key, normalizeAdvancedValue(key, nextParams?.[key])])
		)
	});

	const buildSectionSnapshot = (): SectionSnapshot => ({
		system: normalizeSystemValue(params?.system),
		thinking: Object.fromEntries(THINKING_KEYS.map((key) => [key, params?.[key] ?? null])),
		advanced: buildAdvancedSnapshot()
	});

	const lockBaseline = () => {
		if (baselineLocked) {
			return;
		}

		initialSectionSnapshot = cloneSettingsSnapshot(buildSectionSnapshot());
		baselineLocked = true;
	};

	const flashSection = async (section: SectionKey) => {
		if (section === 'system') {
			systemAck = false;
			await tick();
			systemAck = true;
			return;
		}

		if (section === 'thinking') {
			thinkingAck = false;
			await tick();
			thinkingAck = true;
			return;
		}

		advancedAck = false;
		await tick();
		advancedAck = true;
	};

	const markInteraction = (section: SectionKey) => {
		lockBaseline();
		const now = Date.now();
		if (now - lastFlashAt[section] < 320) {
			return;
		}
		lastFlashAt[section] = now;
		void flashSection(section);
	};

	const restoreThinkingUIState = () => {
		customEffortMode = false;
		customEffortValue = '';
		customTokenMode = false;
		customTokenValue = '';
		activeMode = 'effort';
	};

	const updateGlobalToolCallingMode = async (newMode: ToolCallingMode) => {
		config.update((c) => ({ ...c, tools: { ...c?.tools, calling_mode: newMode } }));
		try {
			const current = await getNativeToolsConfig(localStorage.token);
			if (!current) return;
			current.TOOL_CALLING_MODE = newMode;
			await setNativeToolsConfig(localStorage.token, current);
		} catch (err) {
			console.error('Failed to update global tool calling mode', err);
			const backendConfig = await getBackendConfig().catch(() => null);
			if (backendConfig) config.set(backendConfig);
		}
	};

	const handleAdvancedParamsChange = (e: CustomEvent<Record<string, any>>) => {
		const nextSnapshot = cloneSettingsSnapshot(buildAdvancedSnapshotFromParams(e.detail));

		if (!baselineLocked || previousAdvancedSnapshot === null) {
			previousAdvancedSnapshot = nextSnapshot;
			return;
		}

		if (!isSettingsSnapshotEqual(nextSnapshot, previousAdvancedSnapshot)) {
			previousAdvancedSnapshot = nextSnapshot;
			markInteraction('advanced');
			return;
		}

		previousAdvancedSnapshot = nextSnapshot;
	};

	const resetSystem = () => {
		if (!initialSectionSnapshot) return;
		markInteraction('system');
		params = { ...params, system: initialSectionSnapshot.system };
	};

	const restoreSystemInheritance = () => {
		markInteraction('system');
		params = { ...params, system: null };
	};

	const resetThinking = () => {
		if (!initialSectionSnapshot) return;
		markInteraction('thinking');
		const restored = { ...params };
		for (const key of THINKING_KEYS) {
			restored[key] = initialSectionSnapshot.thinking[key];
		}
		params = restored;
		restoreThinkingUIState();
	};

	const resetAdvanced = async () => {
		if (!initialSectionSnapshot) return;
		markInteraction('advanced');
		const restored = { ...params };
		for (const key of ADVANCED_PARAM_KEYS) {
			restored[key] = initialSectionSnapshot.advanced[key];
		}
		params = restored;
		if (
			FOLLOW_GLOBAL_TOOL_CALLING_MODE &&
			getDisplayedToolCallingMode() !== initialSectionSnapshot.advanced.toolCallingMode
		) {
			await updateGlobalToolCallingMode(initialSectionSnapshot.advanced.toolCallingMode);
		}
	};

	const resetAll = async () => {
		if (!initialSectionSnapshot) return;
		markInteraction('system');
		markInteraction('thinking');
		markInteraction('advanced');
		const restored = { ...params, system: initialSectionSnapshot.system };
		for (const key of THINKING_KEYS) {
			restored[key] = initialSectionSnapshot.thinking[key];
		}
		for (const key of ADVANCED_PARAM_KEYS) {
			restored[key] = initialSectionSnapshot.advanced[key];
		}
		params = restored;
		restoreThinkingUIState();
		if (
			FOLLOW_GLOBAL_TOOL_CALLING_MODE &&
			getDisplayedToolCallingMode() !== initialSectionSnapshot.advanced.toolCallingMode
		) {
			await updateGlobalToolCallingMode(initialSectionSnapshot.advanced.toolCallingMode);
		}
	};

	$: sectionSnapshot = buildSectionSnapshot();
	$: if (!baselineLocked || initialSectionSnapshot === null) {
		initialSectionSnapshot = cloneSettingsSnapshot(sectionSnapshot);
	}
	$: systemModified = initialSectionSnapshot
		? !isSettingsSnapshotEqual(sectionSnapshot.system, initialSectionSnapshot.system)
		: false;
	$: thinkingModified = initialSectionSnapshot
		? !isSettingsSnapshotEqual(sectionSnapshot.thinking, initialSectionSnapshot.thinking)
		: false;
	$: advancedModified = initialSectionSnapshot
		? !isSettingsSnapshotEqual(sectionSnapshot.advanced, initialSectionSnapshot.advanced)
		: false;
	$: anyModified = systemModified || thinkingModified || advancedModified;
	$: if (!baselineLocked || previousAdvancedSnapshot === null) {
		previousAdvancedSnapshot = cloneSettingsSnapshot(sectionSnapshot.advanced);
	}

	// 区块默认/修改边框样式
	const defaultBorder = 'border-gray-100 dark:border-gray-800/60';
	const modifiedBorder = 'border-teal-300/60 dark:border-teal-600/40';

	let showValves = false;

	const effortSteps = [
		{ value: 'none', label: '关闭' },
		{ value: null, label: '默认' },
		{ value: 'low', label: 'Low' },
		{ value: 'medium', label: 'Medium' },
		{ value: 'high', label: 'High' },
		{ value: 'xhigh', label: 'XHigh' },
		{ value: 'max', label: 'Max' }
	];

	const tokenSteps = [
		{ value: 0, label: '关闭' },
		{ value: null, label: '默认' },
		{ value: 2048, label: '2K' },
		{ value: 8192, label: '8K' },
		{ value: 16384, label: '16K' },
		{ value: 32768, label: '32K' },
		{ value: 65536, label: '64K' }
	];

	// 滑动条每个 step 的颜色
	const effortSliderColors = [
		'bg-gray-500 dark:bg-gray-400', // 关闭
		'bg-slate-500 dark:bg-slate-400', // 默认
		'bg-sky-500 dark:bg-sky-400', // Low
		'bg-blue-500 dark:bg-blue-400', // Medium
		'bg-amber-500 dark:bg-amber-400', // High
		'bg-orange-500 dark:bg-orange-400', // XHigh
		'bg-red-500 dark:bg-red-400' // Max
	];
	const budgetSliderColors = [
		'bg-gray-500 dark:bg-gray-400', // 关闭
		'bg-slate-500 dark:bg-slate-400', // 默认
		'bg-sky-500 dark:bg-sky-400', // 2K
		'bg-blue-500 dark:bg-blue-400', // 8K
		'bg-amber-500 dark:bg-amber-400', // 16K
		'bg-orange-500 dark:bg-orange-400', // 32K
		'bg-red-500 dark:bg-red-400' // 64K
	];

	let customEffortMode = false;
	let customEffortValue = '';
	let customTokenMode = false;
	let customTokenValue = '';

	// 强度/预算 互斥模式
	let activeMode: 'effort' | 'budget' = 'effort';

	function switchMode(mode: 'effort' | 'budget') {
		if (activeMode === mode) return;
		markInteraction('thinking');
		activeMode = mode;
		if (mode === 'effort') {
			customTokenMode = false;
			customTokenValue = '';
			params = { ...params, max_thinking_tokens: null };
		} else {
			customEffortMode = false;
			customEffortValue = '';
			params = { ...params, reasoning_effort: null };
		}
	}

	$: currentChatSystemPrompt = typeof params?.system === 'string' ? params.system : '';
	$: globalSystemPrompt = typeof $settings?.system === 'string' ? $settings.system : '';
	$: hasCurrentChatSystemPromptOverride = normalizeSystemValue(params?.system) !== null;
	$: hasGlobalSystemPrompt = normalizeSystemValue($settings?.system) !== null;
	// 系统提示词有内容时自动展开
	$: systemPromptHasContent = !!currentChatSystemPrompt.trim();

	const handleUpdateGlobalToolCallingMode = async (e: CustomEvent<ToolCallingMode>) => {
		await updateGlobalToolCallingMode(e.detail);
		markInteraction('advanced');
	};

	$: {
		const effortVal = params?.reasoning_effort ?? null;
		if (effortVal && effortVal !== 'none' && !effortSteps.some((s) => s.value === effortVal)) {
			customEffortMode = true;
			customEffortValue = effortVal;
		}
		// 外部设置了 effort → 自动切到强度模式
		if (effortVal && effortVal !== 'none') activeMode = 'effort';
	}

	$: {
		const currentVal = params?.max_thinking_tokens ?? null;
		if (currentVal !== null && !tokenSteps.some((p) => p.value === currentVal)) {
			customTokenMode = true;
			customTokenValue = String(currentVal);
		}
		// 外部设置了 budget（包括关闭）→ 自动切到预算模式
		if (currentVal !== null) activeMode = 'budget';
	}
</script>

<div class=" dark:text-white">
	<div class=" flex items-center justify-between dark:text-gray-100 mb-3">
		<div class=" text-lg font-medium self-center font-primary">{$i18n.t('Chat Controls')}</div>
		<div class="flex items-center gap-1">
			{#if anyModified}
				<button
					class="self-center p-1.5 rounded-lg text-gray-400 hover:text-teal-500 dark:hover:text-teal-400 hover:bg-black/5 dark:hover:bg-white/5 transition-colors duration-200"
					title={$i18n.t('Reset All')}
					on:click={resetAll}
				>
					<ArrowPath className="size-3.5" />
				</button>
			{/if}
			<button
				class="self-center p-1.5 rounded-lg hover:bg-black/5 dark:hover:bg-white/5 transition-colors duration-200"
				on:click={() => {
					dispatch('close');
				}}
			>
				<XMark className="size-3.5" />
			</button>
		</div>
	</div>

	<div class=" dark:text-gray-200 text-sm font-primary space-y-2">
		{#if chatFiles.length > 0}
			<div
				class="rounded-xl border border-gray-100 dark:border-gray-800/60 bg-gray-50/40 dark:bg-white/[0.02]"
			>
				<Collapsible
					title={$i18n.t('Files')}
					open={true}
					buttonClassName="w-full px-3 py-2.5 rounded-xl hover:bg-gray-100/60 dark:hover:bg-white/[0.04] transition-colors duration-200"
				>
					<div class="flex flex-col gap-1 px-3 pb-3" slot="content">
						{#each chatFiles as file, fileIdx}
							<FileItem
								className="w-full"
								item={file}
								edit={true}
								url={file?.url ? file.url : null}
								name={file.name}
								type={file.type}
								size={file?.size}
								dismissible={true}
								on:dismiss={() => {
									chatFiles.splice(fileIdx, 1);
									chatFiles = chatFiles;
								}}
								on:click={() => {
									console.log(file);
								}}
							/>
						{/each}
					</div>
				</Collapsible>
			</div>
		{/if}

		<div
			class="rounded-xl border border-gray-100 dark:border-gray-800/60 bg-gray-50/40 dark:bg-white/[0.02]"
		>
			<Collapsible
				bind:open={showValves}
				title={$i18n.t('Valves')}
				buttonClassName="w-full px-3 py-2.5 rounded-xl hover:bg-gray-100/60 dark:hover:bg-white/[0.04] transition-colors duration-200"
			>
				<div class="text-sm px-3 pb-3" slot="content">
					<Valves show={showValves} />
				</div>
			</Collapsible>
		</div>

		{#if $user?.role === 'admin' || $user?.permissions.chat?.controls}
			<!-- svelte-ignore a11y-no-static-element-interactions -->
			<div
				class="rounded-xl border bg-gray-50/40 dark:bg-white/[0.02] transition-all duration-300 relative
					{systemModified ? modifiedBorder : defaultBorder}
					{systemAck ? 'controls-ack' : ''}"
				on:pointerdown|capture={lockBaseline}
				on:keydown|capture={lockBaseline}
				on:focusin|capture={lockBaseline}
				on:animationend={() => {
					systemAck = false;
				}}
			>
				<Collapsible
					chevron={true}
					open={systemPromptHasContent}
					buttonClassName="w-full px-3 py-2.5 rounded-xl hover:bg-gray-100/60 dark:hover:bg-white/[0.04] transition-colors duration-200"
				>
					<div class="flex w-full items-start justify-between gap-3">
						<div class="min-w-0 flex flex-wrap items-center gap-2">
							<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
								{$i18n.t('Current Chat System Prompt')}
							</div>
							<span
								class="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium {hasCurrentChatSystemPromptOverride
									? 'border-teal-200/80 bg-teal-50 text-teal-700 dark:border-teal-800/70 dark:bg-teal-950/40 dark:text-teal-300'
									: 'border-sky-200/80 bg-sky-50 text-sky-700 dark:border-sky-800/70 dark:bg-sky-950/40 dark:text-sky-300'}"
							>
								{hasCurrentChatSystemPromptOverride
									? $i18n.t('Override Global System Prompt')
									: $i18n.t('Inheriting Global System Prompt')}
							</span>
							{#if systemModified || systemAck}
								<span class="inline-flex items-center rounded-full border border-teal-200/80 bg-teal-50 px-2 py-0.5 text-[11px] font-medium text-teal-700 dark:border-teal-800/70 dark:bg-teal-950/40 dark:text-teal-300">
									{systemModified ? '已调整' : '已应用'}
								</span>
							{/if}
						</div>

						<div class="flex items-center gap-1">
							{#if systemModified}
								<button
									class="p-1.5 rounded-lg text-teal-500 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-200 hover:bg-teal-50/80 dark:hover:bg-teal-950/40 transition-colors duration-150"
									title={$i18n.t('Reset')}
									on:pointerup|stopPropagation
									on:click|stopPropagation={resetSystem}
								>
									<ArrowPath className="size-3" />
								</button>
							{/if}
							{#if hasCurrentChatSystemPromptOverride}
								<button
									class="inline-flex items-center rounded-lg border border-sky-200/80 bg-white/90 px-2.5 py-1 text-[11px] font-medium text-sky-700 hover:bg-sky-50 dark:border-sky-800/70 dark:bg-gray-900/70 dark:text-sky-300 dark:hover:bg-sky-950/30 transition-colors duration-150"
									on:pointerup|stopPropagation
									on:click|stopPropagation={restoreSystemInheritance}
								>
									{$i18n.t('Restore Inheritance')}
								</button>
							{/if}
						</div>
					</div>

					<div class="px-3 pb-3" slot="content">
						<div class="space-y-3">
							{#if !hasCurrentChatSystemPromptOverride}
								<div class="rounded-xl border border-sky-200/80 bg-sky-50/70 p-3 dark:border-sky-800/60 dark:bg-sky-950/20">
									<div class="flex items-center gap-2 text-[11px] font-medium text-sky-700 dark:text-sky-300">
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											class="size-3.5 shrink-0"
										>
											<path
												fill-rule="evenodd"
												d="M18 10A8 8 0 1 1 2 10a8 8 0 0 1 16 0Zm-7.25-3a.75.75 0 0 0-1.5 0v3.25c0 .414.336.75.75.75h2a.75.75 0 0 0 0-1.5h-1.25V7Z"
												clip-rule="evenodd"
											/>
										</svg>
										{$i18n.t('Current effective content comes from the global default system prompt.')}
									</div>
									{#if hasGlobalSystemPrompt}
										<div class="mt-2 max-h-44 overflow-y-auto rounded-lg border border-sky-200/80 bg-white/90 px-3 py-2 text-xs leading-5 whitespace-pre-wrap text-gray-700 dark:border-sky-800/60 dark:bg-gray-900/70 dark:text-gray-200">
											{globalSystemPrompt}
										</div>
									{:else}
										<div class="mt-2 rounded-lg border border-dashed border-sky-200/80 bg-white/70 px-3 py-2 text-xs leading-5 text-sky-700/90 dark:border-sky-800/60 dark:bg-gray-900/50 dark:text-sky-300/90">
											{$i18n.t('No global default system prompt is set yet.')}
										</div>
									{/if}
								</div>
							{/if}

							<div class="space-y-2">
								<div class="flex items-center justify-between gap-2">
									<div class="text-[11px] font-medium uppercase tracking-[0.08em] text-gray-500 dark:text-gray-400">
										{$i18n.t('Override for This Chat')}
									</div>
									<div class="text-[11px] text-gray-400 dark:text-gray-500">
										{$i18n.t('Only affects the current chat')}
									</div>
								</div>
								<textarea
									bind:value={params.system}
									on:input={lockBaseline}
									class="w-full text-xs py-2.5 px-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200/60 dark:border-gray-700/40 rounded-xl outline-hidden resize-none focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
									rows="4"
									placeholder={$i18n.t(
										'Leave empty to inherit the global default system prompt. Enter text here to override it only for the current chat.'
									)}
								/>
							</div>
						</div>
					</div>
				</Collapsible>
			</div>

			<!-- svelte-ignore a11y-no-static-element-interactions -->
			<div
				class="rounded-xl border bg-gray-50/40 dark:bg-white/[0.02] transition-all duration-300 relative
					{thinkingModified ? modifiedBorder : defaultBorder}
					{thinkingAck ? 'controls-ack' : ''}"
				on:pointerdown|capture={lockBaseline}
				on:keydown|capture={lockBaseline}
				on:focusin|capture={lockBaseline}
				on:animationend={() => {
					thinkingAck = false;
				}}
			>
				{#if thinkingModified || thinkingAck}
					<div
						class="absolute top-2 right-10 z-10 flex items-center gap-1 px-2 py-1 rounded-full bg-teal-50/95 dark:bg-teal-900/70 border border-teal-200/80 dark:border-teal-700/60 shadow-sm pointer-events-none"
					>
						<span class="text-[10px] font-medium text-teal-700 dark:text-teal-300">
							{thinkingModified ? '已调整' : '已应用'}
						</span>
						{#if thinkingModified}
							<button
								class="pointer-events-auto p-0.5 rounded-full text-teal-500 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-200 transition-colors duration-150 cursor-pointer"
								title={$i18n.t('Reset')}
								on:click|stopPropagation={resetThinking}
							>
								<ArrowPath className="size-2.5" />
							</button>
						{/if}
					</div>
				{/if}
				<Collapsible
					title={$i18n.t('思考强度')}
					open={true}
					buttonClassName="w-full px-3 py-2.5 rounded-xl hover:bg-gray-100/60 dark:hover:bg-white/[0.04] transition-colors duration-200"
				>
					<div class="text-sm px-3 pb-3 space-y-3" slot="content">
						<!-- 模式切换: segmented toggle -->
						<div class="flex items-center gap-0.5 p-0.5 rounded-lg bg-gray-100 dark:bg-gray-800">
							<button
								type="button"
								class="flex-1 text-xs py-1.5 rounded-md transition-all duration-200 cursor-pointer
									{activeMode === 'effort'
									? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400 font-medium'
									: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
								on:click={() => switchMode('effort')}
							>
								强度模式
							</button>
							<button
								type="button"
								class="flex-1 text-xs py-1.5 rounded-md transition-all duration-200 cursor-pointer
									{activeMode === 'budget'
									? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400 font-medium'
									: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}"
								on:click={() => switchMode('budget')}
							>
								预算模式
							</button>
						</div>

						{#if activeMode === 'effort'}
							<!-- 强度模式 -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<div class="text-xs text-gray-500 dark:text-gray-400">思考强度</div>
									<button
										type="button"
										class="text-[10px] transition-colors duration-150 cursor-pointer {customEffortMode
											? 'text-blue-500 dark:text-blue-400'
											: 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => {
											markInteraction('thinking');
											if (customEffortMode) {
												customEffortMode = false;
												customEffortValue = '';
												params = { ...params, reasoning_effort: null };
											} else {
												customEffortMode = true;
												customEffortValue = params?.reasoning_effort ?? '';
											}
										}}
									>
										{customEffortMode ? '返回预设' : '自定义'}
									</button>
								</div>
								{#if customEffortMode}
									<input
										type="text"
										class="w-full text-xs py-2 px-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200/60 dark:border-gray-700/40 rounded-lg outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
										placeholder="输入自定义值，如 high、medium"
										bind:value={customEffortValue}
										on:input={() => {
											lockBaseline();
											params = { ...params, reasoning_effort: customEffortValue || null };
										}}
									/>
								{:else}
									<SteppedSlider
										steps={effortSteps}
										value={params?.reasoning_effort ?? null}
										stepColors={effortSliderColors}
										on:change={(e) => {
											markInteraction('thinking');
											params = { ...params, reasoning_effort: e.detail.value };
										}}
									/>
								{/if}
							</div>
						{:else}
							<!-- 预算模式 -->
							<div>
								<div class="flex items-center justify-between mb-1">
									<div class="text-xs text-gray-500 dark:text-gray-400">思考预算</div>
									<button
										type="button"
										class="text-[10px] transition-colors duration-150 cursor-pointer {customTokenMode
											? 'text-blue-500 dark:text-blue-400'
											: 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'}"
										on:click={() => {
											markInteraction('thinking');
											if (customTokenMode) {
												customTokenMode = false;
												customTokenValue = '';
												params = { ...params, max_thinking_tokens: null };
											} else {
												customTokenMode = true;
												customTokenValue =
													params?.max_thinking_tokens != null
														? String(params.max_thinking_tokens)
														: '';
											}
										}}
									>
										{customTokenMode ? '返回预设' : '自定义'}
									</button>
								</div>
								{#if customTokenMode}
									<input
										type="number"
										class="w-full text-xs py-2 px-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200/60 dark:border-gray-700/40 rounded-lg outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
										placeholder="输入 Token 数量（最小 1024）"
										bind:value={customTokenValue}
										min="1024"
										on:input={() => {
											lockBaseline();
											const val = parseInt(customTokenValue);
											params = { ...params, max_thinking_tokens: isNaN(val) ? null : val };
										}}
									/>
								{:else}
									<SteppedSlider
										steps={tokenSteps}
										value={params?.max_thinking_tokens ?? null}
										stepColors={budgetSliderColors}
										on:change={(e) => {
											markInteraction('thinking');
											params = { ...params, max_thinking_tokens: e.detail.value };
										}}
									/>
								{/if}
							</div>
						{/if}
					</div>
				</Collapsible>
			</div>

			<!-- svelte-ignore a11y-no-static-element-interactions -->
			<div
				class="rounded-xl border bg-gray-50/40 dark:bg-white/[0.02] transition-all duration-300 relative
					{advancedModified ? modifiedBorder : defaultBorder}
					{advancedAck ? 'controls-ack' : ''}"
				on:pointerdown|capture={lockBaseline}
				on:keydown|capture={lockBaseline}
				on:focusin|capture={lockBaseline}
				on:animationend={() => {
					advancedAck = false;
				}}
			>
				{#if advancedModified || advancedAck}
					<div
						class="absolute top-2 right-10 z-10 flex items-center gap-1 px-2 py-1 rounded-full bg-teal-50/95 dark:bg-teal-900/70 border border-teal-200/80 dark:border-teal-700/60 shadow-sm pointer-events-none"
					>
						<span class="text-[10px] font-medium text-teal-700 dark:text-teal-300">
							{advancedModified ? '已调整' : '已应用'}
						</span>
						{#if advancedModified}
							<button
								class="pointer-events-auto p-0.5 rounded-full text-teal-500 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-200 transition-colors duration-150 cursor-pointer"
								title={$i18n.t('Reset')}
								on:click|stopPropagation={resetAdvanced}
							>
								<ArrowPath className="size-2.5" />
							</button>
						{/if}
					</div>
				{/if}
				<Collapsible
					title={$i18n.t('Advanced Params')}
					open={true}
					buttonClassName="w-full px-3 py-2.5 rounded-xl hover:bg-gray-100/60 dark:hover:bg-white/[0.04] transition-colors duration-200"
				>
					<div class="text-sm px-3 pb-3" slot="content">
						<AdvancedParams
							admin={$user?.role === 'admin'}
							globalToolCallingMode={$config?.tools?.calling_mode ?? null}
							followGlobalToolCallingMode={FOLLOW_GLOBAL_TOOL_CALLING_MODE}
							bind:params
							on:change={handleAdvancedParamsChange}
							on:updateGlobalToolCallingMode={handleUpdateGlobalToolCallingMode}
						/>
					</div>
				</Collapsible>
			</div>
		{/if}
	</div>
</div>
