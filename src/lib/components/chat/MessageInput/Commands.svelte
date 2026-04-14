<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Prompts from './Commands/Prompts.svelte';
	import Knowledge from './Commands/Knowledge.svelte';
	import Models from './Commands/Models.svelte';
	import SkillsPanel from './Commands/Skills.svelte';

	const dispatch = createEventDispatcher();

	export let prompt = '';
	export let files = [];
	export let insertTextHandler = async (_text: string) => {};

	let commandElement = null;
	let query = '';
	let triggerChar = '';
	let show = false;

	const replaceLastWord = (input: string, replacement: string) => {
		const lines = input.split('\n');
		const lastLine = lines.pop() ?? '';
		const words = lastLine.split(' ');
		words.pop();
		if (replacement) {
			words.push(replacement);
		}
		lines.push(words.join(' '));
		return lines.join('\n');
	};

	const isKnowledgeCommand = (token: string) =>
		(token?.charAt(0) === '#' && token.startsWith('#') && !token.includes('# ')) ||
		('\\#' === token.slice(0, 2) && token.startsWith('#') && !token.includes('# '));

	$: {
		const token = prompt?.split('\n').pop()?.split(' ')?.pop() ?? '';
		if (isKnowledgeCommand(token)) {
			triggerChar = '#';
			query = token.includes('\\#') ? token.slice(2) : token.slice(1);
		} else {
			triggerChar = token?.charAt(0) ?? '';
			query = token.startsWith('/') || token.startsWith('@') || token.startsWith('$')
				? token.slice(1)
				: token;
		}
	}

	$: show = ['/', '#', '@', '$'].includes(triggerChar);

	export const selectUp = () => {
		commandElement?.selectUp?.();
	};

	export const selectDown = () => {
		commandElement?.selectDown?.();
	};
</script>

{#if show}
	<div id="commands-container" class="px-2 mb-2 text-left w-full absolute bottom-0 left-0 right-0 z-10">
		<div class="flex w-full rounded-xl border border-gray-100 dark:border-gray-850">
			<div class="max-h-60 flex flex-col w-full rounded-xl bg-white dark:bg-gray-900 dark:text-gray-100">
				<div class="m-1 overflow-y-auto p-1 rounded-r-xl space-y-0.5 scrollbar-hidden">
					{#if triggerChar === '/'}
						<Prompts
							bind:this={commandElement}
							query={query}
							onSelect={async (event) => {
								const { type, data } = event;
								if (type === 'prompt') {
									await insertTextHandler(data.content);
								}
							}}
						/>
					{:else if triggerChar === '#'}
						<Knowledge
							bind:this={commandElement}
							query={query}
							onSelect={(event) => {
								const { type, data } = event;
								if (type === 'knowledge') {
									if (!files.find((file) => file.id === data.id)) {
										files = [...files, { ...data, status: 'processed' }];
									}
									prompt = replaceLastWord(prompt, '');
									dispatch('select');
								} else if (type === 'web') {
									prompt = replaceLastWord(prompt, '');
									dispatch('upload', { type: 'web', data });
								}
							}}
						/>
					{:else if triggerChar === '@'}
						<Models
							bind:this={commandElement}
							query={query}
							onSelect={(event) => {
								const { type, data } = event;
								if (type === 'model') {
									prompt = replaceLastWord(prompt, '');
									dispatch('select', { type: 'model', data });
								}
							}}
						/>
					{:else if triggerChar === '$'}
						<SkillsPanel
							bind:this={commandElement}
							query={query}
							onSelect={(event) => {
								const { type, data } = event;
								if (type === 'skill') {
									prompt = replaceLastWord(prompt, `<$${data.id}|${data.name}> `);
								}
							}}
						/>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}
