<script lang="ts">
	import DOMPurify from 'dompurify';
	import { toast } from 'svelte-sonner';

	import type { Token } from 'marked';
	import { getContext } from 'svelte';
	import { readable } from 'svelte/store';

	const i18n = getContext('i18n');

	import { WEBUI_BASE_URL } from '$lib/constants';
	import { copyToClipboard, unescapeHtml } from '$lib/utils';

	import Image from '$lib/components/common/Image.svelte';
	import KatexRenderer from './KatexRenderer.svelte';
	import Source from './Source.svelte';
	import SourceToken from './SourceToken.svelte';

	export let id: string;
	export let tokens: Token[] = [];
	export let onSourceClick: Function = () => {};

	// Streaming context for word-level fade animation
	const streamingStore =
		getContext<import('svelte/store').Writable<boolean>>('streamingMessage') || readable(false);
	$: isStreaming = $streamingStore;

	function segmentText(text: string): string[] {
		try {
			const segmenter = new Intl.Segmenter(undefined, { granularity: 'word' });
			return [...segmenter.segment(text)].map((s) => s.segment);
		} catch {
			return text.match(/[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]|\S+|\s+/g) || [text];
		}
	}

	type InlineRenderableToken = Token & {
		raw?: string;
		text?: string;
	};

	const SVG_OPEN_TAG_RE = /^<svg(?:\s|>)/i;
	const SVG_CLOSE_TAG_RE = /<\/svg>/i;

	function getTokenContent(token: InlineRenderableToken): string {
		const value = token.text ?? token.raw ?? '';
		return typeof value === 'string' ? value : '';
	}

	function isSvgFragmentToken(token: Token): boolean {
		return token.type === 'html' || token.type === 'text' || token.type === 'escape';
	}

	// Marked may split inline SVG into <svg> + child tags + </svg> tokens inside a paragraph.
	// Rendering those pieces one by one breaks the DOM tree and leaves an empty-sized svg shell.
	function mergeInlineSvgTokens(tokens: Token[] = []): InlineRenderableToken[] {
		const merged: InlineRenderableToken[] = [];

		for (let i = 0; i < tokens.length; i += 1) {
			const token = tokens[i] as InlineRenderableToken;
			const content = getTokenContent(token);

			if (token.type === 'html' && SVG_OPEN_TAG_RE.test(content)) {
				const parts = [content];
				let foundClose = SVG_CLOSE_TAG_RE.test(content);
				let j = i + 1;

				while (j < tokens.length && !foundClose && isSvgFragmentToken(tokens[j])) {
					const next = tokens[j] as InlineRenderableToken;
					const nextContent = getTokenContent(next);
					parts.push(nextContent);
					foundClose = SVG_CLOSE_TAG_RE.test(nextContent);
					j += 1;
				}

				if (foundClose) {
					const svgContent = parts.join('');
					merged.push({
						...token,
						type: 'html',
						raw: svgContent,
						text: svgContent
					});
					i = j - 1;
					continue;
				}
			}

			merged.push(token);
		}

		return merged;
	}

	let renderTokens: InlineRenderableToken[] = [];
	$: renderTokens = mergeInlineSvgTokens(tokens);
</script>

{#each renderTokens as token}
	{#if token.type === 'escape'}
		{unescapeHtml(token.text)}
	{:else if token.type === 'html'}
		{@const html = DOMPurify.sanitize(token.text, { ADD_ATTR: ['style'] })}
		{#if html && html.includes('<video')}
			{@html html}
		{:else if token.text.includes(`<iframe src="${WEBUI_BASE_URL}/api/v1/files/`)}
			{@html `${token.text}`}
		{:else if token.text.includes(`<source_id`)}
			<Source {id} {token} onClick={onSourceClick} />
		{:else}
			{@html html}
		{/if}
	{:else if token.type === 'link'}
		{#if token.tokens}
			<a href={token.href} target="_blank" rel="nofollow" title={token.title}>
				<svelte:self id={`${id}-a`} tokens={token.tokens} {onSourceClick} />
			</a>
		{:else}
			<a href={token.href} target="_blank" rel="nofollow" title={token.title}>{token.text}</a>
		{/if}
	{:else if token.type === 'image'}
		<Image src={token.href} alt={token.text} />
	{:else if token.type === 'strong'}
		<strong><svelte:self id={`${id}-strong`} tokens={token.tokens} {onSourceClick} /></strong>
	{:else if token.type === 'em'}
		<em><svelte:self id={`${id}-em`} tokens={token.tokens} {onSourceClick} /></em>
	{:else if token.type === 'codespan'}
		<!-- svelte-ignore a11y-click-events-have-key-events -->
		<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
		<code
			class="codespan cursor-pointer"
			on:click={() => {
				copyToClipboard(unescapeHtml(token.text));
				toast.success($i18n.t('Copied to clipboard'));
			}}>{unescapeHtml(token.text)}</code
		>
	{:else if token.type === 'br'}
		<br />
	{:else if token.type === 'del'}
		<del><svelte:self id={`${id}-del`} tokens={token.tokens} {onSourceClick} /></del>
	{:else if token.type === 'inlineKatex'}
		{#if token.text}
			<KatexRenderer content={token.text} displayMode={false} />
		{/if}
	{:else if token.type === 'iframe'}
		<iframe
			src="{WEBUI_BASE_URL}/api/v1/files/{token.fileId}/content"
			title={token.fileId}
			width="100%"
			frameborder="0"
			sandbox="allow-scripts"
			onload="this.style.height=(this.contentWindow.document.body.scrollHeight+20)+'px';"
		></iframe>
	{:else if token.type === 'citation'}
		<SourceToken {id} {token} onClick={onSourceClick} />
	{:else if token.type === 'text'}
		{#if isStreaming}
			{#each segmentText(token.raw) as segment}
				{#if /^\s+$/.test(segment)}
					{segment}
				{:else}
					<span class="streaming-word">{segment}</span>
				{/if}
			{/each}
		{:else}
			{token.raw}
		{/if}
	{/if}
{/each}
