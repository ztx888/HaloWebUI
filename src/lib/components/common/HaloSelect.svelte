<script lang="ts">
	import { Select } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { createEventDispatcher, onDestroy, onMount, tick } from 'svelte';
	import OverflowTooltip from '$lib/components/common/OverflowTooltip.svelte';

	type Option = {
		value: string;
		label: string;
		disabled?: boolean;
		description?: string;
		badge?: string;
	};

	const dispatch = createEventDispatcher<{ change: { value: string } }>();

	export let value: string = '';
	export let options: Option[] = [];
	export let disabled: boolean = false;
	export let placeholder: string = '';
	export let className: string = '';
	export let searchEnabled: boolean = false;
	export let searchPlaceholder: string = 'Search';
	export let noResultsText: string = 'No results found';

	const DEFAULT_MIN_TRIGGER_WIDTH = '8.5rem';
	const DEFAULT_MAX_TRIGGER_WIDTH = '14rem';
	const CONTENT_WIDTH_CLASS_RE = /(?:^|\s)w-(?:fit|auto|min|max)(?=\s|$)/g;
	const WIDTH_UTILITY_RE =
		/(?:^|\s)(?:w-|min-w-|max-w-|basis-|flex-1\b|grow\b|shrink\b|self-stretch\b)/;

	let triggerEl: HTMLButtonElement | null = null;
	let triggerResizeObserver: ResizeObserver | null = null;
	let triggerWidth = 0;
	let open = false;
	let searchValue = '';
	let searchInputEl: HTMLInputElement | null = null;

	const normalizeClassName = (rawClassName: string) =>
		rawClassName.replace(CONTENT_WIDTH_CLASS_RE, ' ').replace(/\s+/g, ' ').trim();

	const syncTriggerWidth = () => {
		triggerWidth = triggerEl ? Math.round(triggerEl.getBoundingClientRect().width) : 0;
	};

	const observeTriggerEl = () => {
		if (!triggerResizeObserver || !triggerEl) return;
		triggerResizeObserver.disconnect();
		triggerResizeObserver.observe(triggerEl);
	};

	$: matchedOption = options.find((o) => String(o.value) === String(value));
	$: filteredOptions = searchValue
		? options.filter((option) => {
				const q = searchValue.toLowerCase();
				return (
					option.label.toLowerCase().includes(q) ||
					String(option.value).toLowerCase().includes(q) ||
					option.description?.toLowerCase().includes(q)
				);
			})
		: options;
	$: selectedItem = matchedOption
		? {
				value: String(matchedOption.value),
				label: matchedOption.label
			}
		: value !== undefined && value !== null && value !== ''
			? {
					value: String(value),
					label: String(value)
				}
			: undefined;
	$: normalizedClassName = normalizeClassName(className);
	$: hasExplicitWidthClass = WIDTH_UTILITY_RE.test(normalizedClassName);
	$: triggerStyle = hasExplicitWidthClass
		? undefined
		: `min-width: min(100%, ${DEFAULT_MIN_TRIGGER_WIDTH}); max-width: min(100%, ${DEFAULT_MAX_TRIGGER_WIDTH});`;
	$: triggerTextContainerClass = hasExplicitWidthClass ? 'min-w-0 flex-1' : 'min-w-0';
	$: contentMinWidth = triggerWidth > 0 ? `${triggerWidth}px` : undefined;

	onMount(async () => {
		if (typeof ResizeObserver !== 'undefined') {
			triggerResizeObserver = new ResizeObserver(() => {
				syncTriggerWidth();
			});
			observeTriggerEl();
		}

		await tick();
		syncTriggerWidth();
	});

	onDestroy(() => {
		triggerResizeObserver?.disconnect();
	});

	$: if (triggerEl) {
		normalizedClassName;
		selectedItem;
		tick().then(() => {
			observeTriggerEl();
			syncTriggerWidth();
		});
	}

	function handleSelectedChange(next: { value: string; label?: string } | undefined) {
		if (next && String(next.value) !== String(value)) {
			value = next.value;
			dispatch('change', { value: next.value });
		}
	}
</script>

<Select.Root
	bind:open={open}
	items={options.map((o) => ({ value: o.value, label: o.label }))}
	selected={selectedItem}
	onOpenChange={async (next) => {
		searchValue = '';
		if (next && searchEnabled) {
			await tick();
			searchInputEl?.focus();
		}
	}}
	onSelectedChange={handleSelectedChange}
	{disabled}
>
	<Select.Trigger
		bind:this={triggerEl}
		class="inline-flex items-center justify-between gap-2 rounded-lg
					border border-gray-200 dark:border-gray-700
					bg-gray-50 dark:bg-gray-850
					text-sm text-gray-800 dark:text-gray-200
					outline-none cursor-pointer transition-colors
					hover:border-gray-300 dark:hover:border-gray-600
					disabled:opacity-60 disabled:cursor-not-allowed
					px-3 py-2 {normalizedClassName}"
		style={triggerStyle}
		aria-label={selectedItem?.label ?? placeholder}
	>
		{#if selectedItem}
			<OverflowTooltip
				content={selectedItem.label}
				className={triggerTextContainerClass}
				textClassName="block truncate"
			>
				{selectedItem.label}
			</OverflowTooltip>
		{:else}
			<span class="{triggerTextContainerClass} truncate text-gray-400 dark:text-gray-500">
				{placeholder}
			</span>
		{/if}
		<svg
			class="shrink-0 size-3.5 opacity-50"
			xmlns="http://www.w3.org/2000/svg"
			fill="none"
			viewBox="0 0 24 24"
			stroke-width="2"
			stroke="currentColor"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
		</svg>
	</Select.Trigger>

	<Select.Content
		class="z-[10000] overflow-hidden rounded-xl
				border border-gray-200/80 dark:border-gray-700/60
				bg-white dark:bg-gray-900 dark:text-white
				shadow-lg outline-none p-1 !max-w-[20rem]"
		style={contentMinWidth ? `min-width: ${contentMinWidth}` : undefined}
		transition={flyAndScale}
		transitionConfig={{ y: -4, start: 0.97, duration: 150 }}
		sideOffset={4}
		sameWidth={false}
		fitViewport={true}
	>
		{#if searchEnabled}
			<div class="px-2 pt-2 pb-1">
				<input
					bind:this={searchInputEl}
					bind:value={searchValue}
					class="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-850 px-3 py-2 text-sm outline-none"
					placeholder={searchPlaceholder}
					autocomplete="off"
				/>
			</div>
		{/if}
		<div class="max-h-60 overflow-y-auto scrollbar-hidden">
			{#if filteredOptions.length > 0}
					{#each filteredOptions as option (option.value)}
						<Select.Item
							value={option.value}
							label={option.label}
							disabled={option.disabled}
							class="flex w-full items-start
								px-2.5 py-1.5 text-sm rounded-lg
								outline-none select-none cursor-pointer
									transition-colors duration-75
									data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed
								{String(option.value) === String(value)
						? 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20'
						: 'text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 data-[highlighted]:bg-gray-100 dark:data-[highlighted]:bg-gray-800'}"
					>
							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2">
									<OverflowTooltip
										content={option.label}
										className="min-w-0 flex-1"
										textClassName="block truncate whitespace-nowrap"
									>
										{option.label}
									</OverflowTooltip>
									{#if option.badge}
										<span
											class="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500 dark:bg-gray-800 dark:text-gray-400"
										>
											{option.badge}
										</span>
									{/if}
								</div>
								{#if option.description}
									<div class="mt-0.5 text-xs leading-4 text-gray-500 dark:text-gray-400">
										{option.description}
									</div>
								{/if}
							</div>
						</Select.Item>
					{/each}
			{:else}
				<div class="px-3 py-2 text-sm text-gray-500 dark:text-gray-400">{noResultsText}</div>
			{/if}
		</div>
	</Select.Content>
</Select.Root>
