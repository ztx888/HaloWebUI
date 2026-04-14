<script lang="ts">
	import Fuse from 'fuse.js';
	import { getContext } from 'svelte';
	import { models } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ModelIcon from '$lib/components/common/ModelIcon.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';

	const i18n = getContext('i18n');

	export let query = '';
	export let onSelect = (_event) => {};
	export let filteredItems = [];

	let selectedIdx = 0;
	let fuse = new Fuse(
		$models
				.filter((model) => !model?.info?.meta?.hidden)
				.map((model) => ({
					...model,
					modelName: getModelChatDisplayName(model),
					tags: model?.info?.meta?.tags?.map((tag) => tag.name).join(' '),
					desc: model?.info?.meta?.description
				})),
		{
			keys: ['value', 'tags', 'modelName'],
			threshold: 0.5
		}
	);

	$: filteredItems = query
		? fuse.search(query).map((entry) => entry.item)
		: $models.filter((model) => !model?.info?.meta?.hidden);

	$: if (query) {
		selectedIdx = 0;
	}

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
	};

	export const select = () => {
		const model = filteredItems[selectedIdx];
		if (model) {
			onSelect({ type: 'model', data: model });
		}
	};
</script>

<div class="px-2 text-xs text-gray-500 py-1">{$i18n.t('Models')}</div>

{#if filteredItems.length > 0}
	{#each filteredItems as model, modelIdx}
		<Tooltip content={model.id} placement="top-start">
			<button
				class="px-2.5 py-1.5 rounded-xl w-full text-left {modelIdx === selectedIdx
					? 'bg-gray-50 dark:bg-gray-800 selected-command-option-button'
					: ''}"
				type="button"
				data-selected={modelIdx === selectedIdx}
				on:click={() => {
					onSelect({ type: 'model', data: model });
				}}
				on:mousemove={() => {
					selectedIdx = modelIdx;
				}}
				>
					<div class="flex text-black dark:text-gray-100 line-clamp-1">
						<ModelIcon
							src={model?.info?.meta?.profile_image_url ??
								model?.meta?.profile_image_url ??
								'/static/favicon.png'}
							alt={model?.name ?? model.id}
							className="rounded-lg size-6 mr-2 shrink-0"
						/>
						<div class="truncate">{getModelChatDisplayName(model)}</div>
					</div>
				</button>
			</Tooltip>
	{/each}
{/if}
