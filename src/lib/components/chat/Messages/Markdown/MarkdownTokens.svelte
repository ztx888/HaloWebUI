<script lang="ts">
	import DOMPurify from 'dompurify';
	import { createEventDispatcher, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { marked, type Token } from 'marked';
	import { unescapeHtml } from '$lib/utils';

	import { WEBUI_BASE_URL } from '$lib/constants';

	import CodeBlock from '$lib/components/chat/Messages/CodeBlock.svelte';
	import MarkdownInlineTokens from '$lib/components/chat/Messages/Markdown/MarkdownInlineTokens.svelte';
	import KatexRenderer from './KatexRenderer.svelte';
	import AlertRenderer, { alertComponent } from './AlertRenderer.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import ToolCallGroup from '$lib/components/common/ToolCallGroup.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';

	import Source from './Source.svelte';
	import { settings } from '$lib/stores';
	import { isSvgMarkup, promoteSvgMarkupTokens } from './svgMarkupTokens';
	import { getHeadingAnchorId } from '$lib/utils/headings';

	const dispatch = createEventDispatcher();

	export let id: string;
	export let messageId: string = id;
	export let tokens: Token[];
	export let top = true;
	export let attributes = {};

	export let save = false;

	export let onTaskClick: Function = () => {};
	export let onSourceClick: Function = () => {};
	export let charAnimation = false;
	export let pathPrefix: number[] = [];

	let detailsOpenState = new Map<string, boolean>();

	const getDetailsStateKey = (token: any, tokenIdx: number) =>
		[messageId, ...pathPrefix, tokenIdx, token?.attributes?.type ?? '', token?.summary ?? ''].join(
			':'
		);

	const getDefaultDetailsOpen = (token: any) =>
		token?.attributes?.type === 'error' || token?.attributes?.type === 'warning'
			? true
			: ($settings?.expandDetails ?? false);

	const getDetailsOpen = (token: any, tokenIdx: number) => {
		const key = getDetailsStateKey(token, tokenIdx);
		return detailsOpenState.has(key)
			? (detailsOpenState.get(key) ?? false)
			: getDefaultDetailsOpen(token);
	};

	const setDetailsOpen = (token: any, tokenIdx: number, open: boolean) => {
		detailsOpenState.set(getDetailsStateKey(token, tokenIdx), open);
	};

	const headerComponent = (depth: number) => {
		return 'h' + depth;
	};

	const exportTableToCSVHandler = (token, tokenIdx = 0) => {
		console.log('Exporting table to CSV');

		const header = token.header.map((headerCell) => `"${headerCell.text.replace(/"/g, '""')}"`);
		const rows = token.rows.map((row) =>
			row.map((cell) => {
				const cellContent = cell.tokens.map((token) => token.text).join('');
				return `"${cellContent.replace(/"/g, '""')}"`;
			})
		);

		const csvData = [header, ...rows];
		const csvContent = csvData.map((row) => row.join(',')).join('\n');
		const bom = '\uFEFF';
		const blob = new Blob([bom + csvContent], { type: 'text/csv;charset=UTF-8' });
		saveAs(blob, `table-${id}-${tokenIdx}.csv`);
	};

	type RenderItem =
		| { kind: 'token'; token: any; originalIdx: number }
		| { kind: 'tool_call_group'; tokens: any[]; startIdx: number };

	function groupConsecutiveToolCalls(tokens: Token[]): RenderItem[] {
		const items: RenderItem[] = [];
		let i = 0;
		while (i < tokens.length) {
			const token = tokens[i] as any;
			if (token.type === 'details' && token.attributes?.type === 'tool_calls') {
				const group: any[] = [token];
				const startIdx = i;
				let j = i + 1;
				while (j < tokens.length) {
					const next = tokens[j] as any;
					if (next.type === 'space') {
						if (
							j + 1 < tokens.length &&
							(tokens[j + 1] as any).type === 'details' &&
							(tokens[j + 1] as any).attributes?.type === 'tool_calls'
						) {
							j++;
							continue;
						} else {
							break;
						}
					}
					if (next.type === 'details' && next.attributes?.type === 'tool_calls') {
						group.push(next);
						j++;
					} else {
						break;
					}
				}
				if (group.length >= 2) {
					items.push({ kind: 'tool_call_group', tokens: group, startIdx });
				} else {
					items.push({ kind: 'token', token, originalIdx: i });
				}
				i = j;
			} else {
				items.push({ kind: 'token', token, originalIdx: i });
				i++;
			}
		}
		return items;
	}

	$: normalizedTokens = promoteSvgMarkupTokens(tokens);
	$: renderItems = groupConsecutiveToolCalls(normalizedTokens);
</script>

<!-- {JSON.stringify(tokens)} -->
{#each renderItems as item, idx (idx)}
	{#if item.kind === 'tool_call_group'}
		<ToolCallGroup id={`${id}-tcg-${item.startIdx}`} tokens={item.tokens} />
	{:else}
		{@const token = item.token}
		{@const tokenIdx = item.originalIdx}
		{#if token.type === 'hr'}
			<hr class=" border-gray-100 dark:border-gray-850" />
		{:else if token.type === 'heading'}
			<svelte:element
				this={headerComponent(token.depth)}
				dir="auto"
				id={getHeadingAnchorId(messageId, [...pathPrefix, tokenIdx])}
				class="message-outline-anchor"
			>
				<MarkdownInlineTokens
					id={`${id}-${tokenIdx}-h`}
					tokens={token.tokens}
					{charAnimation}
					{onSourceClick}
				/>
			</svelte:element>
		{:else if token.type === 'code'}
			<div
				id={getHeadingAnchorId(messageId, [...pathPrefix, tokenIdx])}
				class="message-outline-anchor"
			>
				{#if token.raw.includes('```')}
					<CodeBlock
						id={`${id}-${tokenIdx}`}
						{messageId}
						collapsed={$settings?.collapseCodeBlocks ?? false}
						{token}
						lang={token?.lang ?? ''}
						code={token?.text ?? ''}
						{attributes}
						{save}
						onCode={(value) => {
							dispatch('code', value);
						}}
						onSave={(value) => {
							dispatch('update', {
								raw: token.raw,
								oldContent: token.text,
								newContent: value
							});
						}}
					/>
				{:else}
					{token.text}
				{/if}
			</div>
		{:else if token.type === 'table'}
			<div class="relative w-full group">
				<div class="scrollbar-hidden relative overflow-x-auto max-w-full rounded-lg">
					<table
						class=" w-full text-sm text-left text-gray-500 dark:text-gray-200 max-w-full rounded-xl"
					>
						<thead
							class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-850 dark:text-gray-200 border-none"
						>
							<tr class="">
								{#each token.header as header, headerIdx}
									<th
										scope="col"
										class="px-3! py-1.5! cursor-pointer border border-gray-100 dark:border-gray-850"
										style={token.align[headerIdx] ? `text-align: ${token.align[headerIdx]}` : ''}
									>
										<div class="gap-1.5 text-left">
											<div class="shrink-0 break-normal">
												<MarkdownInlineTokens
													id={`${id}-${tokenIdx}-header-${headerIdx}`}
													tokens={header.tokens}
													{onSourceClick}
												/>
											</div>
										</div>
									</th>
								{/each}
							</tr>
						</thead>
						<tbody>
							{#each token.rows as row, rowIdx}
								<tr class="bg-white dark:bg-gray-900 dark:border-gray-850 text-xs">
									{#each row ?? [] as cell, cellIdx}
										<td
											class="px-3! py-1.5! text-gray-900 dark:text-white w-max border border-gray-100 dark:border-gray-850"
											style={token.align[cellIdx] ? `text-align: ${token.align[cellIdx]}` : ''}
										>
											<div class="break-normal">
												<MarkdownInlineTokens
													id={`${id}-${tokenIdx}-row-${rowIdx}-${cellIdx}`}
													tokens={cell.tokens}
													{onSourceClick}
												/>
											</div>
										</td>
									{/each}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>

				<div class=" absolute top-1 right-1.5 z-20 invisible group-hover:visible">
					<Tooltip content={$i18n.t('Export to CSV')}>
						<button
							class="p-1 rounded-lg bg-transparent transition"
							on:click={(e) => {
								e.stopPropagation();
								exportTableToCSVHandler(token, tokenIdx);
							}}
						>
							<ArrowDownTray className=" size-3.5" strokeWidth="1.5" />
						</button>
					</Tooltip>
				</div>
			</div>
		{:else if token.type === 'blockquote'}
			{@const alert = alertComponent(token)}
			{#if alert}
				<AlertRenderer {token} {alert} />
			{:else}
				<blockquote dir="auto">
					<svelte:self
						id={`${id}-${tokenIdx}`}
						{messageId}
						tokens={token.tokens}
						pathPrefix={[...pathPrefix, tokenIdx]}
						{charAnimation}
						{onTaskClick}
						{onSourceClick}
					/>
				</blockquote>
			{/if}
		{:else if token.type === 'list'}
			{#if token.ordered}
				<ol start={token.start || 1} dir="auto">
					{#each token.items ?? [] as item, itemIdx}
						<li class="text-start">
							{#if item?.task}
								<input
									class=" translate-y-[1px] -translate-x-1"
									type="checkbox"
									checked={item.checked}
									on:change={(e) => {
										onTaskClick({
											id: id,
											token: token,
											tokenIdx: tokenIdx,
											item: item,
											itemIdx: itemIdx,
											checked: e.target.checked
										});
									}}
								/>
							{/if}

							<svelte:self
								id={`${id}-${tokenIdx}-${itemIdx}`}
								{messageId}
								tokens={item.tokens}
								pathPrefix={[...pathPrefix, tokenIdx, itemIdx]}
								top={token.loose}
								{charAnimation}
								{onTaskClick}
								{onSourceClick}
							/>
						</li>
					{/each}
				</ol>
			{:else}
				<ul dir="auto">
					{#each token.items ?? [] as item, itemIdx}
						<li class="text-start">
							{#if item?.task}
								<input
									class=" translate-y-[1px] -translate-x-1"
									type="checkbox"
									checked={item.checked}
									on:change={(e) => {
										onTaskClick({
											id: id,
											token: token,
											tokenIdx: tokenIdx,
											item: item,
											itemIdx: itemIdx,
											checked: e.target.checked
										});
									}}
								/>
							{/if}

							<svelte:self
								id={`${id}-${tokenIdx}-${itemIdx}`}
								{messageId}
								tokens={item.tokens}
								pathPrefix={[...pathPrefix, tokenIdx, itemIdx]}
								top={token.loose}
								{charAnimation}
								{onTaskClick}
								{onSourceClick}
							/>
						</li>
					{/each}
				</ul>
			{/if}
		{:else if token.type === 'details'}
			<Collapsible
				title={token.summary}
				open={getDetailsOpen(token, tokenIdx)}
				attributes={token?.attributes}
				className="w-full space-y-1"
				dir="auto"
				on:change={(e) => {
					setDetailsOpen(token, tokenIdx, e.detail);
				}}
			>
				<div class=" mb-1.5" slot="content">
					<svelte:self
						id={`${id}-${tokenIdx}-d`}
						{messageId}
						tokens={marked.lexer(token.text)}
						pathPrefix={[...pathPrefix, tokenIdx]}
						attributes={token?.attributes}
						{charAnimation}
						{onTaskClick}
						{onSourceClick}
					/>
				</div>
			</Collapsible>
		{:else if token.type === 'html'}
			{@const isSvgMarkupToken = isSvgMarkup(token.text)}
			{@const html = DOMPurify.sanitize(token.text, { ADD_ATTR: ['style'] })}
			{#if isSvgMarkupToken}
				<CodeBlock
					id={`${id}-${tokenIdx}-html-svg`}
					{messageId}
					collapsed={$settings?.collapseCodeBlocks ?? false}
					token={{
						type: 'code',
						lang: 'svg',
						raw: `\`\`\`svg\n${token.text}\n\`\`\``,
						text: token.text
					}}
					lang="svg"
					code={token.text}
					{attributes}
					{save}
					onCode={(value) => {
						dispatch('code', value);
					}}
				/>
			{:else if html && html.includes('<video')}
				{@html html}
			{:else if token.text.includes(`<iframe src="${WEBUI_BASE_URL}/api/v1/files/`)}
				{@html `${token.text}`}
			{:else if token.text.includes(`<source_id`)}
				<Source {id} {token} onClick={onSourceClick} />
			{:else}
				{@html html}
			{/if}
		{:else if token.type === 'iframe'}
			<iframe
				src="{WEBUI_BASE_URL}/api/v1/files/{token.fileId}/content"
				title={token.fileId}
				width="100%"
				frameborder="0"
				onload="this.style.height=(this.contentWindow.document.body.scrollHeight+20)+'px';"
			></iframe>
		{:else if token.type === 'paragraph'}
			<p dir="auto">
				<MarkdownInlineTokens
					id={`${id}-${tokenIdx}-p`}
					tokens={token.tokens ?? []}
					{charAnimation}
					{onSourceClick}
				/>
			</p>
		{:else if token.type === 'text'}
			{#if top}
				<p>
					{#if token.tokens}
						<MarkdownInlineTokens
							id={`${id}-${tokenIdx}-t`}
							tokens={token.tokens}
							{charAnimation}
							{onSourceClick}
						/>
					{:else}
						{unescapeHtml(token.text)}
					{/if}
				</p>
			{:else if token.tokens}
				<MarkdownInlineTokens
					id={`${id}-${tokenIdx}-p`}
					tokens={token.tokens ?? []}
					{charAnimation}
					{onSourceClick}
				/>
			{:else}
				{unescapeHtml(token.text)}
			{/if}
		{:else if token.type === 'inlineKatex'}
			{#if token.text}
				<KatexRenderer content={token.text} displayMode={token?.displayMode ?? false} />
			{/if}
		{:else if token.type === 'blockKatex'}
			{#if token.text}
				<KatexRenderer content={token.text} displayMode={token?.displayMode ?? false} />
			{/if}
		{:else if token.type === 'space'}
			<div class="my-2" />
		{:else}
			<!-- Unsupported token -->
		{/if}
	{/if}
{/each}
