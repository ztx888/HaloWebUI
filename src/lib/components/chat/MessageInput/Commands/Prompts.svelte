<script lang="ts">
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { getPrompts } from '$lib/apis/prompts';
	import { config } from '$lib/stores';
	import { getContext, onDestroy } from 'svelte';

	const i18n = getContext('i18n');

	export let query = '';
	export let onSelect = (_event) => {};
	export let filteredItems = [];

	let selectedPromptIdx = 0;
	let items = [];
	let defaultSuggestionItems = [];
	let visibleItems = [];
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	const normalizeDefaultPromptSuggestion = (item, index) => {
		const title = Array.isArray(item?.title)
			? item.title
			: item?.title
				? [item.title, '']
				: ['', ''];

		return {
			id: item?.id ?? `default-prompt-suggestion-${index}`,
			command: '',
			name: title.filter(Boolean).join(' · ') || $i18n.t('Prompt suggestion'),
			title,
			content: item?.content ?? '',
			isDefaultSuggestion: true
		};
	};

	$: defaultSuggestionItems = ($config?.default_prompt_suggestions ?? []).map((item, index) =>
		normalizeDefaultPromptSuggestion(item, index)
	);

	$: visibleItems = items.length > 0 ? items : defaultSuggestionItems;

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getItems();
		}, 200);
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	$: filteredItems = visibleItems
		.filter((item) => {
			const lowerQuery = query.toLowerCase();
			if (item.isDefaultSuggestion) {
				return (
					(item?.content ?? '').toLowerCase().includes(lowerQuery) ||
					(item?.title ?? []).join(' ').toLowerCase().includes(lowerQuery) ||
					(item?.name ?? '').toLowerCase().includes(lowerQuery)
				);
			}

			return (
				(item?.command ?? '').toLowerCase().includes(lowerQuery) ||
				(item?.name ?? '').toLowerCase().includes(lowerQuery) ||
				(item?.content ?? '').toLowerCase().includes(lowerQuery)
			);
		})
		.sort((a, b) => (a.name || a.command || '').localeCompare(b.name || b.command || ''));

	$: if (query) {
		selectedPromptIdx = 0;
	}

	const getItems = async () => {
		const res = await getPrompts(localStorage.token).catch(() => null);
		if (res) {
			items = res.filter((item) => item.is_active !== false);
		}
	};

	export const selectUp = () => {
		selectedPromptIdx = Math.max(0, selectedPromptIdx - 1);
	};

	export const selectDown = () => {
		selectedPromptIdx = Math.min(selectedPromptIdx + 1, filteredItems.length - 1);
	};

	export const select = () => {
		const command = filteredItems[selectedPromptIdx];
		if (command) {
			onSelect({ type: 'prompt', data: command });
		}
	};
</script>

<div class="px-2 text-xs text-gray-500 py-1">
	{items.length > 0 ? $i18n.t('Prompts') : $i18n.t('Prompt suggestions')}
</div>

{#if filteredItems.length > 0}
	<div class="space-y-0.5 scrollbar-hidden">
		{#each filteredItems as promptItem, promptIdx}
			<Tooltip content={promptItem.name} placement="top-start">
				<button
					class="px-3 py-1 rounded-xl w-full text-left {promptIdx === selectedPromptIdx
						? 'bg-gray-50 dark:bg-gray-800 selected-command-option-button'
						: ''} truncate"
					type="button"
					on:click={() => {
						onSelect({ type: 'prompt', data: promptItem });
					}}
					on:mousemove={() => {
						selectedPromptIdx = promptIdx;
					}}
					data-selected={promptIdx === selectedPromptIdx}
				>
					{#if promptItem.isDefaultSuggestion}
						<div class="min-w-0">
							<div class="font-medium text-black dark:text-gray-100 line-clamp-1">
								{promptItem.title?.[0] || promptItem.name}
							</div>
							<div class="text-xs text-gray-600 dark:text-gray-100 line-clamp-1">
								{promptItem.title?.[1] || promptItem.content}
							</div>
						</div>
					{:else}
						<span class="font-medium text-black dark:text-gray-100">{promptItem.command}</span>
						<span class="text-xs text-gray-600 dark:text-gray-100"> {promptItem.name}</span>
					{/if}
				</button>
			</Tooltip>
		{/each}
	</div>
{:else}
	<div class="px-3 py-2 text-sm text-gray-500 dark:text-gray-400">
		{#if items.length === 0 && defaultSuggestionItems.length === 0}
			{$i18n.t('No prompt suggestions available yet.')}
		{:else if items.length === 0}
			{$i18n.t('No matching prompt suggestions.')}
		{:else}
			{$i18n.t('No matching prompts.')}
		{/if}
	</div>
{/if}
