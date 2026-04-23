<script lang="ts">
	import { decode } from 'html-entities';
	import { v4 as uuidv4 } from 'uuid';

	import { getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	const i18n: Writable<any> = getContext('i18n');

	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	async function loadLocale(locales: string[]) {
		for (const locale of locales) {
			try {
				dayjs.locale(locale);
				break; // Stop after successfully loading the first available locale
			} catch (error) {
				console.error(`Could not load locale '${locale}':`, error);
			}
		}
	}

	// Assuming $i18n.languages is an array of language codes
	$: {
		const locales = $i18n?.languages;
		if (Array.isArray(locales)) {
			loadLocale(locales);
		}
	}

	const dispatch = createEventDispatcher();
	$: dispatch('change', open);

	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '../icons/ChevronUp.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import Spinner from './Spinner.svelte';
	import CodeBlock from '../chat/Messages/CodeBlock.svelte';
	import Markdown from '../chat/Messages/Markdown.svelte';
	import Image from './Image.svelte';

	export let open = false;

	export let className = '';
	export let buttonClassName =
		'w-fit text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition';

	// Allow callers (e.g. markdown rendering) to set text direction explicitly.
	export let dir: string | undefined = undefined;

	export let id = '';
	export let title = null;
	export let attributes: any = null;

	export let chevron = false;
	export let grow = false;

	export let disabled = false;
	export let hide = false;

	const collapsibleId = uuidv4();

	function parseJSONString(str: string): any {
		try {
			return parseJSONString(JSON.parse(str));
		} catch (e) {
			return str;
		}
	}

	function formatJSONString(str: string): string {
		try {
			const parsed = parseJSONString(str);
			// If parsed is an object/array, then it's valid JSON
			if (typeof parsed === 'object') {
				return JSON.stringify(parsed, null, 2);
			} else {
				// It's a primitive value like a number, boolean, etc.
				return `${JSON.stringify(String(parsed))}`;
			}
		} catch (e) {
			// Not valid JSON, return as-is
			return str;
		}
	}

	function isImageGenerationTool(name: string): boolean {
		return ['generate_image', 'edit_image'].includes(name?.toLowerCase() ?? '');
	}
</script>

<div
	{id}
	{dir}
	class={className}
	data-pdf-collapsible="true"
	data-pdf-open={open ? 'true' : 'false'}
	data-pdf-type={attributes?.type ?? ''}
>
	{#if title !== null}
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<div
			class="{buttonClassName} cursor-pointer {attributes?.type === 'error'
				? 'w-full rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-red-800 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-200'
				: attributes?.type === 'warning'
					? 'w-full rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800 dark:border-amber-800/50 dark:bg-amber-900/20 dark:text-amber-200'
					: ''}"
			on:pointerup={() => {
				if (!disabled) {
					open = !open;
				}
			}}
		>
			<div
				class=" w-full font-medium flex items-center justify-between gap-2 {attributes?.done &&
				attributes?.done !== 'true'
					? 'shimmer'
					: ''}
			"
			>
				{#if attributes?.done && attributes?.done !== 'true'}
					<div>
						<Spinner className="size-4" />
					</div>
				{/if}

				<div class="">
					{#if attributes?.type === 'reasoning'}
						{#if attributes?.done === 'true' && attributes?.duration}
							{#if attributes.duration < 60}
								{$i18n.t('Thought for {{DURATION}} seconds', {
									DURATION: attributes.duration
								})}
							{:else}
								{$i18n.t('Thought for {{DURATION}}', {
									DURATION: dayjs.duration(attributes.duration, 'seconds').humanize()
								})}
							{/if}
						{:else}
							{$i18n.t('Thinking...')}
						{/if}
					{:else if attributes?.type === 'code_interpreter'}
						{#if attributes?.done === 'true'}
							{$i18n.t('Analyzed')}
						{:else}
							{$i18n.t('Analyzing...')}
						{/if}
					{:else if attributes?.type === 'tool_calls'}
						{#if attributes?.done === 'true'}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}`}
								content={$i18n.t('View Result from **{{NAME}}**', {
									NAME: attributes.name
								})}
							/>
						{:else}
							<span>{$i18n.t('Executing')} <strong>{attributes.name}</strong>...</span>
						{/if}
					{:else if attributes?.type === 'error'}
						{#if attributes?.status || attributes?.message}
							<Markdown
								id={`${collapsibleId}-error`}
								content={`**${$i18n.t('Error')}**${
									attributes?.status ? ` (HTTP ${attributes.status})` : ''
								}${attributes?.message ? `: ${attributes.message}` : ''}`}
							/>
						{:else}
							<span class="font-semibold">{$i18n.t('Error')}</span>
						{/if}
					{:else}
						{title}
					{/if}
				</div>

				<div class="flex self-center translate-y-[1px]">
					{#if open}
						<ChevronUp strokeWidth="3.5" className="size-3.5" />
					{:else}
						<ChevronDown strokeWidth="3.5" className="size-3.5" />
					{/if}
				</div>
			</div>
		</div>
	{:else}
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<div
			class="{buttonClassName} cursor-pointer"
			on:pointerup={() => {
				if (!disabled) {
					open = !open;
				}
			}}
		>
			<div>
				<div class="flex items-start justify-between">
					<slot />

					{#if chevron}
						<div class="flex self-start translate-y-1">
							{#if open}
								<ChevronUp strokeWidth="3.5" className="size-3.5" />
							{:else}
								<ChevronDown strokeWidth="3.5" className="size-3.5" />
							{/if}
						</div>
					{/if}
				</div>

				{#if grow}
					{#if open && !hide}
						<div
							transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
							on:pointerup={(e) => {
								e.stopPropagation();
							}}
						>
							<slot name="content" />
						</div>
					{/if}
				{/if}
			</div>
		</div>
	{/if}

	{#if attributes?.type === 'tool_calls'}
		{@const args = decode(attributes?.arguments)}
		{@const result = decode(attributes?.result ?? '')}
		{@const files = parseJSONString(decode(attributes?.files ?? ''))}

		{#if !grow}
			{#if open && !hide}
				<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
					{#if attributes?.type === 'tool_calls'}
						{#if attributes?.done === 'true'}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}-result`}
								content={`> \`\`\`json
> ${formatJSONString(args)}
> ${formatJSONString(result)}
> \`\`\``}
							/>
						{:else}
							<Markdown
								id={`${collapsibleId}-tool-calls-${attributes?.id}-result`}
								content={`> \`\`\`json
> ${formatJSONString(args)}
> \`\`\``}
							/>
						{/if}
					{:else}
						<slot name="content" />
					{/if}
				</div>
			{/if}

			{#if attributes?.done === 'true' && !isImageGenerationTool(attributes?.name)}
				{#if typeof files === 'object'}
					{#each files ?? [] as file, idx}
						{#if typeof file === 'string' && file.startsWith('data:image/')}
							<Image src={file} alt="Image" />
						{:else if file?.type === 'image' && file?.url}
							<Image src={file.url} alt="Image" />
						{/if}
					{/each}
				{/if}
			{/if}
		{/if}
	{:else if !grow}
		{#if open && !hide}
			<div
				transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}
				class={attributes?.type === 'error'
					? 'mt-1 rounded-lg border border-red-200 bg-red-50/60 px-3 py-2 text-red-900 dark:border-red-900/50 dark:bg-red-900/10 dark:text-red-100'
					: attributes?.type === 'warning'
						? 'mt-1 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-amber-900 dark:border-amber-800/50 dark:bg-amber-900/10 dark:text-amber-100'
						: ''}
			>
				<slot name="content" />
			</div>
		{/if}
	{/if}
</div>
