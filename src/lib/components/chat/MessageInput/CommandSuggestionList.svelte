<script lang="ts">
	import Prompts from './Commands/Prompts.svelte';
	import Knowledge from './Commands/Knowledge.svelte';
	import Models from './Commands/Models.svelte';
	import Skills from './Commands/Skills.svelte';

	export let char = '';
	export let query = '';
	export let command: (payload: { id: string; label: string }) => void;
	export let onSelect = (_event) => {};
	export let onUpload = (_event) => {};
	export let insertTextHandler = (_text) => {};

	let suggestionElement = null;
	let filteredItems = [];

	const onKeyDown = (event: KeyboardEvent) => {
		if (!['ArrowUp', 'ArrowDown', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
			return false;
		}

		if (event.key === 'ArrowUp') {
			suggestionElement?.selectUp();
			document.querySelector(`[data-selected="true"]`)?.scrollIntoView({
				block: 'center',
				inline: 'nearest',
				behavior: 'instant'
			});
			return true;
		}

		if (event.key === 'ArrowDown') {
			suggestionElement?.selectDown();
			document.querySelector(`[data-selected="true"]`)?.scrollIntoView({
				block: 'center',
				inline: 'nearest',
				behavior: 'instant'
			});
			return true;
		}

		if (event.key === 'Enter' || event.key === 'Tab') {
			suggestionElement?.select();
			if (event.key === 'Enter') {
				event.preventDefault();
			}
			return true;
		}

		return event.key === 'Escape';
	};

	// @ts-ignore
	export function _onKeyDown(event: KeyboardEvent) {
		return onKeyDown(event);
	}
</script>

<div
	class="{(filteredItems ?? []).length > 0 || ['/', '$'].includes(char)
		? ''
		: 'hidden'} rounded-2xl shadow-lg border border-gray-200 dark:border-gray-800 flex flex-col bg-white dark:bg-gray-850 w-72 p-1"
	id="suggestions-container"
>
	<div class="overflow-y-auto scrollbar-thin max-h-60">
		{#if char === '/'}
			<Prompts
				bind:this={suggestionElement}
				{query}
				bind:filteredItems
				onSelect={(event) => {
					const { type, data } = event;
					if (type === 'prompt') {
						insertTextHandler(data.content);
					}
				}}
			/>
		{:else if char === '#'}
			<Knowledge
				bind:this={suggestionElement}
				{query}
				bind:filteredItems
				onSelect={(event) => {
					const { type, data } = event;
					if (type === 'knowledge') {
						insertTextHandler('');
						onUpload({ type: 'file', data });
					} else if (type === 'web') {
						insertTextHandler('');
						onUpload({ type: 'web', data });
					}
				}}
			/>
		{:else if char === '@'}
			<Models
				bind:this={suggestionElement}
				{query}
				bind:filteredItems
				onSelect={(event) => {
					const { type, data } = event;
					if (type === 'model') {
						insertTextHandler('');
						onSelect({ type: 'model', data });
					}
				}}
			/>
		{:else if char === '$'}
			<Skills
				bind:this={suggestionElement}
				{query}
				bind:filteredItems
				onSelect={(event) => {
					const { type, data } = event;
					if (type === 'skill') {
						command({
							id: `${data.id}|${data.name}`,
							label: data.name
						});
						onSelect({ type: 'skill', data });
					}
				}}
			/>
		{/if}
	</div>
</div>
