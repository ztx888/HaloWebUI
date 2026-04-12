<script lang="ts">
	import Plus from '$lib/components/icons/Plus.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';

	const dispatch = createEventDispatcher();

	const i18n: Writable<any> = getContext('i18n');
	type ToolCallingMode = 'default' | 'native' | 'off';
	const TOOL_CALLING_MODE_ORDER: ToolCallingMode[] = ['default', 'native', 'off'];

	export let admin = false;
	export let globalToolCallingMode: ToolCallingMode | null = null;
	// Chat-level advanced menu should follow the global Tools setting (no separate "auto/admin" label).
	export let followGlobalToolCallingMode = false;
	export let enableCustomParams = false;

	// NOTE: This component binds a large "bag of optional params" into multiple
	// settings UIs. Use a loose type to avoid TS inferring every field as `null`.
	export let params: any = {
		// Advanced
		stream_response: null, // Set stream responses for this model individually
		function_calling: null,
		seed: null,
		stop: null,
		temperature: null,
		reasoning_effort: null,
		logit_bias: null,
		frequency_penalty: null,
		repeat_last_n: null,
		mirostat: null,
		mirostat_eta: null,
		mirostat_tau: null,
		top_k: null,
		top_p: null,
		min_p: null,
		tfs_z: null,
		num_ctx: null,
		num_batch: null,
		num_keep: null,
		max_tokens: null,
		use_mmap: null,
		use_mlock: null,
		num_thread: null,
		num_gpu: null,
		custom_params: null,
		template: null
	};

	let customFieldName = '';
	let customFieldValue = '';
	let customParamEntries: [string, any][] = [];
	let customParamsExpanded = false;

	$: if (params) {
		dispatch('change', params);
	}

	const normalizeToolCallingMode = (v: any): ToolCallingMode | null => {
		const s = (v ?? null) as any;
		return s === 'default' || s === 'native' || s === 'off' ? s : null;
	};

	const getNextToolCallingMode = (mode: ToolCallingMode): ToolCallingMode => {
		const currentIndex = TOOL_CALLING_MODE_ORDER.indexOf(mode);
		return TOOL_CALLING_MODE_ORDER[(currentIndex + 1) % TOOL_CALLING_MODE_ORDER.length];
	};

	const getToolCallingLabel = (mode: ToolCallingMode): string => {
		switch (mode) {
			case 'native':
				return $i18n.t('Native');
			case 'off':
				return $i18n.t('Off');
			default:
				return $i18n.t('Compatibility');
		}
	};

	const getToolCallingValueClasses = (mode: ToolCallingMode): string =>
		mode === 'native'
			? 'text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300'
			: 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300';

	let effectiveToolCallingMode: ToolCallingMode = 'default';
	$: {
		const local = normalizeToolCallingMode(params?.function_calling);
		const global = normalizeToolCallingMode(globalToolCallingMode);
		effectiveToolCallingMode = (local ?? global ?? 'default') as ToolCallingMode;

		// When enabled, do not persist per-chat overrides; always follow the global setting.
		if (followGlobalToolCallingMode && params) {
			params.function_calling = null;
		}
	}

	const setToolCallingMode = (mode: ToolCallingMode) => {
		if (!params) return;
		const global = normalizeToolCallingMode(globalToolCallingMode);

		// If a global mode exists and the user selects it, keep the param unset so it follows globally.
		if (global && mode === global) {
			params.function_calling = null;
			return;
		}

		params.function_calling = mode;
	};

	const cycleToolCallingMode = () => {
		const next = getNextToolCallingMode(effectiveToolCallingMode);
		if (followGlobalToolCallingMode) {
			dispatch('updateGlobalToolCallingMode', next);
			return;
		}
		setToolCallingMode(next);
	};

	const isPlainObject = (value: unknown): value is Record<string, any> =>
		typeof value === 'object' && value !== null && !Array.isArray(value);

	const getCustomParams = (): Record<string, any> =>
		isPlainObject(params?.custom_params) ? params.custom_params : {};

	const setCustomParams = (nextCustomParams: Record<string, any>) => {
		if (!params) return;
		params = {
			...params,
			custom_params: Object.keys(nextCustomParams).length > 0 ? nextCustomParams : null
		};
	};

	const parseCustomParamValue = (value: string) => {
		const trimmed = value.trim();

		if (trimmed === '') {
			return '';
		}

		if (trimmed === 'true') return true;
		if (trimmed === 'false') return false;
		if (trimmed === 'null') return null;

		if (/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(trimmed)) {
			return Number(trimmed);
		}

		if (
			((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
				(trimmed.startsWith('[') && trimmed.endsWith(']')) ||
				(trimmed.startsWith('"') && trimmed.endsWith('"'))) &&
			trimmed.length >= 2
		) {
			try {
				return JSON.parse(trimmed);
			} catch {
				return value;
			}
		}

		return value;
	};

	const formatCustomParamValue = (value: unknown): string => {
		if (typeof value === 'string') return value;
		if (typeof value === 'number' || typeof value === 'boolean') return String(value);
		if (value === null) return 'null';

		try {
			return JSON.stringify(value);
		} catch {
			return String(value);
		}
	};

	const addCustomParam = () => {
		const key = customFieldName.trim();
		if (!key) return;

		setCustomParams({
			...getCustomParams(),
			[key]: parseCustomParamValue(customFieldValue)
		});

		customFieldName = '';
		customFieldValue = '';
	};

	const removeCustomParam = (key: string) => {
		const nextCustomParams = { ...getCustomParams() };
		delete nextCustomParams[key];
		setCustomParams(nextCustomParams);
	};

	const clearCustomParams = () => {
		if (!params) return;
		customFieldName = '';
		customFieldValue = '';
		params = {
			...params,
			custom_params: null
		};
	};

	const toggleCustomParamsPanel = () => {
		customParamsExpanded = !customParamsExpanded;
	};

	$: customParamEntries = Object.entries(getCustomParams());
	$: hasCustomParams = customParamEntries.length > 0;
</script>

<div class="space-y-1.5 text-xs pb-safe-bottom">
	<div>
		<Tooltip
			content={$i18n.t(
				'When enabled, the model will respond to each chat message in real-time, generating a response as soon as the user sends a message. This mode is useful for live chat applications, but may impact performance on slower hardware.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div
				class="py-1.5 px-1 flex w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
			>
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Stream Chat Response')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					on:click={() => {
						params.stream_response =
							(params?.stream_response ?? null) === null
								? true
								: params.stream_response
									? false
									: null;
					}}
					type="button"
				>
					{#if params.stream_response === true}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('On')}</span
						>
					{:else if params.stream_response === false}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Off')}</span
						>
					{:else}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>
	</div>

	<div>
		<Tooltip
			content={$i18n.t(
				'Compatibility mode works with a wider range of models by calling tools once before execution. Native mode leverages the model’s built-in tool-calling capabilities, but requires the model to inherently support this feature.'
			) +
				` ${$i18n.t('Off mode disables tool calling while keeping your selected tools configured.')}` +
				(followGlobalToolCallingMode
					? `\n\n${$i18n.t('Clicking here will update the global Tools setting.')}`
					: '')}
			placement="top-start"
			className="inline-tooltip"
		>
			<div
				class="py-1.5 px-1 flex w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
			>
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Tool Calling')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={cycleToolCallingMode}
				>
					<span class={getToolCallingValueClasses(effectiveToolCallingMode)}>
						{getToolCallingLabel(effectiveToolCallingMode)}
					</span>
				</button>
			</div>
		</Tooltip>
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Sets the random number seed to use for generation. Setting this to a specific number will make the model generate the same text for the same prompt.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Seed')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.seed = (params?.seed ?? null) === null ? 0 : null;
					}}
				>
					{#if (params?.seed ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
						>
							{$i18n.t('Default')}
						</span>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
						>
							{$i18n.t('Custom')}
						</span>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.seed ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 bg-gray-50 dark:bg-gray-800/50 border border-gray-200/60 dark:border-gray-700/40 outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
						type="number"
						placeholder={$i18n.t('Enter Seed')}
						bind:value={params.seed}
						autocomplete="off"
						min="0"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Sets the stop sequences to use. When this pattern is encountered, the LLM will stop generating text and return. Multiple stop patterns may be set by specifying multiple separate stop parameters in a modelfile.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Stop Sequence')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.stop = (params?.stop ?? null) === null ? '' : null;
					}}
				>
					{#if (params?.stop ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
						>
							{$i18n.t('Default')}
						</span>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
						>
							{$i18n.t('Custom')}
						</span>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.stop ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						class="w-full rounded-lg py-2 px-3 text-sm dark:text-gray-300 bg-gray-50 dark:bg-gray-800/50 border border-gray-200/60 dark:border-gray-700/40 outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
						type="text"
						placeholder={$i18n.t('Enter stop sequence')}
						bind:value={params.stop}
						autocomplete="off"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'The temperature of the model. Increasing the temperature will make the model answer more creatively.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Temperature')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.temperature = (params?.temperature ?? null) === null ? 0.8 : null;
					}}
				>
					{#if (params?.temperature ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
						>
							{$i18n.t('Default')}
						</span>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
						>
							{$i18n.t('Custom')}
						</span>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.temperature ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="2"
						step="0.05"
						bind:value={params.temperature}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.temperature}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="2"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Boosting or penalizing specific tokens for constrained responses. Bias values will be clamped between -100 and 100 (inclusive). (Default: none)'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Logit Bias')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.logit_bias = (params?.logit_bias ?? null) === null ? '' : null;
					}}
				>
					{#if (params?.logit_bias ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
						>
							{$i18n.t('Default')}
						</span>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
						>
							{$i18n.t('Custom')}
						</span>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.logit_bias ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						class="w-full rounded-lg pl-2 py-2 px-3 text-sm dark:text-gray-300 bg-gray-50 dark:bg-gray-800/50 border border-gray-200/60 dark:border-gray-700/40 outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
						type="text"
						placeholder={$i18n.t(
							'Enter comma-seperated "token:bias_value" pairs (example: 5432:100, 413:-100)'
						)}
						bind:value={params.logit_bias}
						autocomplete="off"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t('Enable Mirostat sampling for controlling perplexity.')}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Mirostat')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.mirostat = (params?.mirostat ?? null) === null ? 0 : null;
					}}
				>
					{#if (params?.mirostat ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.mirostat ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="2"
						step="1"
						bind:value={params.mirostat}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.mirostat}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="2"
						step="1"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Influences how quickly the algorithm responds to feedback from the generated text. A lower learning rate will result in slower adjustments, while a higher learning rate will make the algorithm more responsive.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Mirostat Eta')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.mirostat_eta = (params?.mirostat_eta ?? null) === null ? 0.1 : null;
					}}
				>
					{#if (params?.mirostat_eta ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.mirostat_eta ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="1"
						step="0.05"
						bind:value={params.mirostat_eta}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.mirostat_eta}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="1"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Controls the balance between coherence and diversity of the output. A lower value will result in more focused and coherent text.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Mirostat Tau')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.mirostat_tau = (params?.mirostat_tau ?? null) === null ? 5.0 : null;
					}}
				>
					{#if (params?.mirostat_tau ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.mirostat_tau ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="10"
						step="0.5"
						bind:value={params.mirostat_tau}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.mirostat_tau}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="10"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Reduces the probability of generating nonsense. A higher value (e.g. 100) will give more diverse answers, while a lower value (e.g. 10) will be more conservative.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Top K')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.top_k = (params?.top_k ?? null) === null ? 40 : null;
					}}
				>
					{#if (params?.top_k ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.top_k ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="1000"
						step="0.5"
						bind:value={params.top_k}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.top_k}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="100"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Works together with top-k. A higher value (e.g., 0.95) will lead to more diverse text, while a lower value (e.g., 0.5) will generate more focused and conservative text.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Top P')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.top_p = (params?.top_p ?? null) === null ? 0.9 : null;
					}}
				>
					{#if (params?.top_p ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.top_p ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="1"
						step="0.05"
						bind:value={params.top_p}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.top_p}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="1"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Alternative to the top_p, and aims to ensure a balance of quality and variety. The parameter p represents the minimum probability for a token to be considered, relative to the probability of the most likely token. For example, with p=0.05 and the most likely token having a probability of 0.9, logits with a value less than 0.045 are filtered out.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Min P')}
				</div>
				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.min_p = (params?.min_p ?? null) === null ? 0.0 : null;
					}}
				>
					{#if (params?.min_p ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.min_p ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="1"
						step="0.05"
						bind:value={params.min_p}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.min_p}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="1"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Sets a scaling bias against tokens to penalize repetitions, based on how many times they have appeared. A higher value (e.g., 1.5) will penalize repetitions more strongly, while a lower value (e.g., 0.9) will be more lenient. At 0, it is disabled.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Frequency Penalty')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.frequency_penalty = (params?.frequency_penalty ?? null) === null ? 1.1 : null;
					}}
				>
					{#if (params?.frequency_penalty ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.frequency_penalty ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-2"
						max="2"
						step="0.05"
						bind:value={params.frequency_penalty}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.frequency_penalty}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-2"
						max="2"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Sets a flat bias against tokens that have appeared at least once. A higher value (e.g., 1.5) will penalize repetitions more strongly, while a lower value (e.g., 0.9) will be more lenient. At 0, it is disabled.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Presence Penalty')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.presence_penalty = (params?.presence_penalty ?? null) === null ? 0.0 : null;
					}}
				>
					{#if (params?.presence_penalty ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.presence_penalty ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-2"
						max="2"
						step="0.05"
						bind:value={params.presence_penalty}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.presence_penalty}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-2"
						max="2"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t('Sets how far back for the model to look back to prevent repetition.')}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Repeat Last N')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.repeat_last_n = (params?.repeat_last_n ?? null) === null ? 64 : null;
					}}
				>
					{#if (params?.repeat_last_n ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.repeat_last_n ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-1"
						max="128"
						step="1"
						bind:value={params.repeat_last_n}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.repeat_last_n}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-1"
						max="128"
						step="1"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Tail free sampling is used to reduce the impact of less probable tokens from the output. A higher value (e.g., 2.0) will reduce the impact more, while a value of 1.0 disables this setting.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Tfs Z')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.tfs_z = (params?.tfs_z ?? null) === null ? 1 : null;
					}}
				>
					{#if (params?.tfs_z ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.tfs_z ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="0"
						max="2"
						step="0.05"
						bind:value={params.tfs_z}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.tfs_z}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="0"
						max="2"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'This option controls how many tokens are preserved when refreshing the context. For example, if set to 2, the last 2 tokens of the conversation context will be retained. Preserving context can help maintain the continuity of a conversation, but it may reduce the ability to respond to new topics.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Tokens To Keep On Context Refresh (num_keep)')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.num_keep = (params?.num_keep ?? null) === null ? 24 : null;
					}}
				>
					{#if (params?.num_keep ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.num_keep ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-1"
						max="10240000"
						step="1"
						bind:value={params.num_keep}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div class="">
					<input
						bind:value={params.num_keep}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-1"
						step="1"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'This option sets the maximum number of tokens the model can generate in its response. Increasing this limit allows the model to provide longer answers, but it may also increase the likelihood of unhelpful or irrelevant content being generated.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Max Tokens (num_predict)')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.max_tokens = (params?.max_tokens ?? null) === null ? 128 : null;
					}}
				>
					{#if (params?.max_tokens ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.max_tokens ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-2"
						max="131072"
						step="1"
						bind:value={params.max_tokens}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.max_tokens}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-2"
						step="1"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'Control the repetition of token sequences in the generated text. A higher value (e.g., 1.5) will penalize repetitions more strongly, while a lower value (e.g., 1.1) will be more lenient. At 1, it is disabled.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Repeat Penalty (Ollama)')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.repeat_penalty = (params?.repeat_penalty ?? null) === null ? 1.1 : null;
					}}
				>
					{#if (params?.repeat_penalty ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.repeat_penalty ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-2"
						max="2"
						step="0.05"
						bind:value={params.repeat_penalty}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.repeat_penalty}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-2"
						max="2"
						step="any"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t('Sets the size of the context window used to generate the next token.')}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Context Length')}
					{$i18n.t('(Ollama)')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.num_ctx = (params?.num_ctx ?? null) === null ? 2048 : null;
					}}
				>
					{#if (params?.num_ctx ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.num_ctx ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="-1"
						max="10240000"
						step="1"
						bind:value={params.num_ctx}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div class="">
					<input
						bind:value={params.num_ctx}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="-1"
						step="1"
					/>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
	>
		<Tooltip
			content={$i18n.t(
				'The batch size determines how many text requests are processed together at once. A higher batch size can increase the performance and speed of the model, but it also requires more memory.'
			)}
			placement="top-start"
			className="inline-tooltip"
		>
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">
					{$i18n.t('Batch Size (num_batch)')}
				</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.num_batch = (params?.num_batch ?? null) === null ? 512 : null;
					}}
				>
					{#if (params?.num_batch ?? null) === null}
						<span
							class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
							>{$i18n.t('Default')}</span
						>
					{:else}
						<span
							class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
							>{$i18n.t('Custom')}</span
						>
					{/if}
				</button>
			</div>
		</Tooltip>

		{#if (params?.num_batch ?? null) !== null}
			<div class="flex mt-0.5 space-x-2">
				<div class=" flex-1">
					<input
						id="steps-range"
						type="range"
						min="256"
						max="8192"
						step="256"
						bind:value={params.num_batch}
						class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
					/>
				</div>
				<div>
					<input
						bind:value={params.num_batch}
						type="number"
						class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
						min="256"
						step="256"
					/>
				</div>
			</div>
		{/if}
	</div>

	{#if enableCustomParams}
		<div
			class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
		>
			<Tooltip
				content="添加当前模型界面里没直接暴露的请求字段。参数值会自动识别为数字、布尔值、null、JSON，或原始文本。"
				placement="top-start"
				className="inline-tooltip"
			>
				<button
					class="flex w-full items-center justify-between gap-3 text-left text-xs cursor-pointer transition-colors duration-200"
					type="button"
					on:click={toggleCustomParamsPanel}
				>
					<div class="self-center text-xs font-medium">自定义请求参数</div>
					<div
						class="shrink-0 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
					>
						添加
					</div>
				</button>
			</Tooltip>

			{#if customParamsExpanded}
				<div class="mt-2 rounded-xl border border-dashed border-gray-200/80 dark:border-gray-700/60 bg-gray-50/60 dark:bg-gray-900/25 px-2.5 py-2.5 space-y-2">
					<div class="flex items-center justify-between gap-3">
						<div class="text-[11px] leading-5 text-gray-500 dark:text-gray-400">
							会作为补充字段附加到上游请求，不覆盖系统已有参数。
						</div>
						{#if hasCustomParams}
							<button
								class="shrink-0 text-[11px] text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 transition-colors duration-200"
								type="button"
								on:click={clearCustomParams}
							>
								清空
							</button>
						{/if}
					</div>

					<div class="grid grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_auto] gap-1.5">
						<input
							class="w-full rounded-lg py-1.5 px-2.5 text-xs dark:text-gray-300 bg-white/80 dark:bg-gray-800/70 border border-gray-200/70 dark:border-gray-700/50 outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
							type="text"
							placeholder="参数名，如 top_k"
							bind:value={customFieldName}
							autocomplete="off"
						/>
						<input
							class="w-full rounded-lg py-1.5 px-2.5 text-xs dark:text-gray-300 bg-white/80 dark:bg-gray-800/70 border border-gray-200/70 dark:border-gray-700/50 outline-hidden focus:border-blue-300/50 dark:focus:border-blue-500/30 transition-colors duration-200 placeholder:text-gray-400 dark:placeholder:text-gray-500"
							type="text"
							placeholder='参数值，支持 JSON / true / 123 / null'
							bind:value={customFieldValue}
							autocomplete="off"
							on:keydown={(event) => {
								if (event.key === 'Enter') {
									event.preventDefault();
									addCustomParam();
								}
							}}
						/>
						<button
							class="inline-flex size-8 items-center justify-center rounded-lg border border-gray-200/70 dark:border-gray-700/50 bg-white/90 dark:bg-gray-800/70 text-gray-500 hover:text-blue-600 hover:border-blue-300/60 dark:text-gray-400 dark:hover:text-blue-300 dark:hover:border-blue-500/40 transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50"
							type="button"
							on:click={addCustomParam}
							disabled={!customFieldName.trim()}
							title="添加参数"
						>
							<Plus className="size-3.5" strokeWidth="2.5" />
						</button>
					</div>

					{#if hasCustomParams}
						<div class="space-y-1.5">
							{#each customParamEntries as [key, value]}
								<div
									class="flex items-start justify-between gap-2 rounded-lg border border-gray-200/60 dark:border-gray-700/40 bg-white/80 dark:bg-gray-900/35 px-2.5 py-2"
								>
									<div class="min-w-0 flex-1">
										<div class="inline-flex max-w-full items-center rounded-md bg-gray-100/90 dark:bg-gray-800/80 px-1.5 py-0.5 text-[11px] font-medium text-gray-700 dark:text-gray-200 break-all">
											{key}
										</div>
										<div class="mt-1 text-[11px] leading-5 text-gray-500 dark:text-gray-400 break-all">
											{formatCustomParamValue(value)}
										</div>
									</div>
									<button
										class="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-gray-400 hover:bg-gray-100/80 hover:text-red-500 dark:text-gray-500 dark:hover:bg-gray-800/70 dark:hover:text-red-400 transition-colors duration-200"
										type="button"
										on:click={() => removeCustomParam(key)}
										title={$i18n.t('Remove')}
									>
										<XMark className="size-3" strokeWidth="2.25" />
									</button>
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/if}

	{#if admin}
		<div
			class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
		>
			<Tooltip
				content={$i18n.t(
					'Enable Memory Mapping (mmap) to load model data. This option allows the system to use disk storage as an extension of RAM by treating disk files as if they were in RAM. This can improve model performance by allowing for faster data access. However, it may not work correctly with all systems and can consume a significant amount of disk space.'
				)}
				placement="top-start"
				className="inline-tooltip"
			>
				<div class="flex w-full justify-between">
					<div class=" self-center text-xs font-medium">
						{$i18n.t('use_mmap (Ollama)')}
					</div>
					<button
						class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
						type="button"
						on:click={() => {
							params.use_mmap = (params?.use_mmap ?? null) === null ? true : null;
						}}
					>
						{#if (params?.use_mmap ?? null) === null}
							<span
								class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
								>{$i18n.t('Default')}</span
							>
						{:else}
							<span
								class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
								>{$i18n.t('Custom')}</span
							>
						{/if}
					</button>
				</div>
			</Tooltip>

			{#if (params?.use_mmap ?? null) !== null}
				<div class="flex justify-between items-center mt-1">
					<div class="text-xs text-gray-500">
						{params.use_mmap ? 'Enabled' : 'Disabled'}
					</div>
					<div class=" pr-2">
						<Switch bind:state={params.use_mmap} />
					</div>
				</div>
			{/if}
		</div>

		<div
			class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
		>
			<Tooltip
				content={$i18n.t(
					"Enable Memory Locking (mlock) to prevent model data from being swapped out of RAM. This option locks the model's working set of pages into RAM, ensuring that they will not be swapped out to disk. This can help maintain performance by avoiding page faults and ensuring fast data access."
				)}
				placement="top-start"
				className="inline-tooltip"
			>
				<div class="flex w-full justify-between">
					<div class=" self-center text-xs font-medium">
						{$i18n.t('use_mlock (Ollama)')}
					</div>

					<button
						class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
						type="button"
						on:click={() => {
							params.use_mlock = (params?.use_mlock ?? null) === null ? true : null;
						}}
					>
						{#if (params?.use_mlock ?? null) === null}
							<span
								class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
								>{$i18n.t('Default')}</span
							>
						{:else}
							<span
								class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
								>{$i18n.t('Custom')}</span
							>
						{/if}
					</button>
				</div>
			</Tooltip>

			{#if (params?.use_mlock ?? null) !== null}
				<div class="flex justify-between items-center mt-1">
					<div class="text-xs text-gray-500">
						{params.use_mlock ? 'Enabled' : 'Disabled'}
					</div>

					<div class=" pr-2">
						<Switch bind:state={params.use_mlock} />
					</div>
				</div>
			{/if}
		</div>

		<div
			class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
		>
			<Tooltip
				content={$i18n.t(
					'Set the number of worker threads used for computation. This option controls how many threads are used to process incoming requests concurrently. Increasing this value can improve performance under high concurrency workloads but may also consume more CPU resources.'
				)}
				placement="top-start"
				className="inline-tooltip"
			>
				<div class="flex w-full justify-between">
					<div class=" self-center text-xs font-medium">
						{$i18n.t('num_thread (Ollama)')}
					</div>

					<button
						class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
						type="button"
						on:click={() => {
							params.num_thread = (params?.num_thread ?? null) === null ? 2 : null;
						}}
					>
						{#if (params?.num_thread ?? null) === null}
							<span
								class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
								>{$i18n.t('Default')}</span
							>
						{:else}
							<span
								class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
								>{$i18n.t('Custom')}</span
							>
						{/if}
					</button>
				</div>
			</Tooltip>

			{#if (params?.num_thread ?? null) !== null}
				<div class="flex mt-0.5 space-x-2">
					<div class=" flex-1">
						<input
							id="steps-range"
							type="range"
							min="1"
							max="256"
							step="1"
							bind:value={params.num_thread}
							class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
						/>
					</div>
					<div class="">
						<input
							bind:value={params.num_thread}
							type="number"
							class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
							min="1"
							max="256"
							step="1"
						/>
					</div>
				</div>
			{/if}
		</div>

		<div
			class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150"
		>
			<Tooltip
				content={$i18n.t(
					'Set the number of layers, which will be off-loaded to GPU. Increasing this value can significantly improve performance for models that are optimized for GPU acceleration but may also consume more power and GPU resources.'
				)}
				placement="top-start"
				className="inline-tooltip"
			>
				<div class="flex w-full justify-between">
					<div class=" self-center text-xs font-medium">
						{$i18n.t('num_gpu (Ollama)')}
					</div>

					<button
						class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
						type="button"
						on:click={() => {
							params.num_gpu = (params?.num_gpu ?? null) === null ? 0 : null;
						}}
					>
						{#if (params?.num_gpu ?? null) === null}
							<span
								class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
								>{$i18n.t('Default')}</span
							>
						{:else}
							<span
								class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300"
								>{$i18n.t('Custom')}</span
							>
						{/if}
					</button>
				</div>
			</Tooltip>

			{#if (params?.num_gpu ?? null) !== null}
				<div class="flex mt-0.5 space-x-2">
					<div class=" flex-1">
						<input
							id="steps-range"
							type="range"
							min="0"
							max="256"
							step="1"
							bind:value={params.num_gpu}
							class="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-gray-200 dark:bg-gray-700"
						/>
					</div>
					<div class="">
						<input
							bind:value={params.num_gpu}
							type="number"
							class="bg-gray-50 dark:bg-gray-800/50 text-center w-14 rounded-md border border-gray-200/60 dark:border-gray-700/40 py-0.5 text-xs outline-none transition-colors duration-200"
							min="0"
							max="256"
							step="1"
						/>
					</div>
				</div>
			{/if}
		</div>

		<!-- <div class="py-1.5 px-1 w-full justify-between rounded-lg hover:bg-gray-50/80 dark:hover:bg-white/[0.02] transition-colors duration-150">
			<div class="flex w-full justify-between">
				<div class=" self-center text-xs font-medium">{$i18n.t('Template')}</div>

				<button
					class="text-xs cursor-pointer transition-colors duration-200 shrink-0"
					type="button"
					on:click={() => {
						params.template = (params?.template ?? null) === null ? '' : null;
					}}
				>
					{#if (params?.template ?? null) === null}
						<span class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300">{$i18n.t('Default')}</span>
					{:else}
						<span class="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300">{$i18n.t('Custom')}</span>
					{/if}
				</button>
			</div>

			{#if (params?.template ?? null) !== null}
				<div class="flex mt-0.5 space-x-2">
					<div class=" flex-1">
						<textarea
							class="px-3 py-1.5 text-sm w-full bg-transparent border dark:border-gray-600 outline-hidden rounded-lg -mb-1"
							placeholder={$i18n.t('Write your model template content here')}
							rows="4"
							bind:value={params.template}
						/>
					</div>
				</div>
			{/if}
		</div> -->
	{/if}
</div>
