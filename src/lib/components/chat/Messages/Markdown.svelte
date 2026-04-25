<script lang="ts">
	import { createEventDispatcher, onDestroy } from 'svelte';
	import { Marked, Tokenizer, type Token } from 'marked';

	import { replaceTokens, processResponseContent } from '$lib/utils';
	import { user } from '$lib/stores';
	import {
		DEFAULT_CHAT_TRANSITION_MODE,
		type ChatTransitionMode
	} from '$lib/utils/lobehub-chat-appearance';
	import { getModelChatDisplayName } from '$lib/utils/model-display';

	import markedExtension from '$lib/utils/marked/extension';
	import markedKatexExtension from '$lib/utils/marked/katex-extension';
	import citationExtension from '$lib/utils/marked/citation-extension';
	import { extractHeadings, type HeadingItem } from '$lib/utils/headings';

	import MarkdownTokens from './Markdown/MarkdownTokens.svelte';
	import { createSmoothStreamContentController } from './streaming/smoothStreamContent';

	const dispatch = createEventDispatcher();

	export let id = '';
	export let content = '';
	export let model = null;
	export let save = false;
	export let streaming = false;
	export let transitionMode: ChatTransitionMode = DEFAULT_CHAT_TRANSITION_MODE;

	export let sourceIds = [];
	export let headings: HeadingItem[] = [];

	export let onSourceClick: Function = () => {};
	export let onTaskClick: Function = () => {};

	let tokens: Token[] = [];
	let processedContent = '';
	let renderedContent = '';
	let delayedAnimated = false;
	let delayedAnimatedTimer: ReturnType<typeof setTimeout> | null = null;
	let lastLexedContent = '';
	let effectiveTransitionMode: ChatTransitionMode = transitionMode;

	let smoothStreamController = createSmoothStreamContentController({
		enabled: false,
		initialContent: '',
		onUpdate: (nextContent) => {
			renderedContent = nextContent;
		},
		preset: transitionMode === 'smooth' ? 'silky' : 'balanced'
	});

	const sourceIdsRef = { current: [] as unknown[] };
	$: sourceIdsRef.current = sourceIds;

	function noSingleTildeExtension() {
		return {
			name: 'noSingleTilde',
			level: 'inline',
			walkTokens(token: any) {
				if (token.type === 'del') {
					const raw = token.raw || '';
					if (/^~[^~]+~$/.test(raw) && !/^~~[^~]+~~$/.test(raw)) {
						token.type = 'text';
					}
				}
			}
		};
	}

	const options = {
		throwOnError: false
	};

	const markdownParser = new Marked(
		markedKatexExtension(options),
		markedExtension(options),
		citationExtension(sourceIdsRef),
		noSingleTildeExtension()
	);

	{
		const CJK = '\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\uf900-\ufaff';
		const addCJK = (re: RegExp) =>
			new RegExp(re.source.replaceAll('\\p{P}', '\\p{P}' + CJK), re.flags);

		markdownParser.use({
			tokenizer: {
				emStrong(this: any, src: string, maskedSrc: string, prevChar: string) {
					if (!this.rules.inline._cjkPatched) {
						this.rules.inline.punctuation = addCJK(this.rules.inline.punctuation);
						this.rules.inline.emStrong.rDelimAst = addCJK(this.rules.inline.emStrong.rDelimAst);
						this.rules.inline.emStrong.rDelimUnd = addCJK(this.rules.inline.emStrong.rDelimUnd);
						this.rules.inline._cjkPatched = true;
					}
					return Tokenizer.prototype.emStrong.call(this, src, maskedSrc, prevChar);
				}
			}
		});
	}

	const syncDelayedAnimated = (nextAnimated: boolean) => {
		if (delayedAnimatedTimer) {
			clearTimeout(delayedAnimatedTimer);
			delayedAnimatedTimer = null;
		}

		if (nextAnimated) {
			delayedAnimated = true;
			return;
		}

		if (!delayedAnimated) {
			return;
		}

		delayedAnimatedTimer = setTimeout(() => {
			delayedAnimated = false;
			delayedAnimatedTimer = null;
		}, 1000);
	};

	$: processedContent = content
		? replaceTokens(
				processResponseContent(content),
				getModelChatDisplayName(model),
				$user?.name
			)
		: '';

	const hasStreamingReasoningDetails = (value: string) =>
		/<details\b[^>]*type="reasoning"[^>]*done="false"/i.test(value);

	// Reasoning summaries stream as full <details> snapshots, not plain text appends.
	// Bypass transition effects for these structured blocks so the expanded thinking body
	// can refresh immediately as each backend delta arrives.
	$: effectiveTransitionMode =
		streaming && hasStreamingReasoningDetails(processedContent) ? 'none' : transitionMode;

	$: syncDelayedAnimated(streaming && effectiveTransitionMode !== 'none');
	$: renderTransitionMode =
		effectiveTransitionMode !== 'none' && delayedAnimated ? effectiveTransitionMode : 'none';

	// Rebuild controller when preset changes
	$: {
		const nextPreset = effectiveTransitionMode === 'smooth' ? 'silky' : 'balanced';
		const currentContent = smoothStreamController.getDisplayedContent();
		smoothStreamController.destroy();
		smoothStreamController = createSmoothStreamContentController({
			enabled: false,
			initialContent: currentContent,
			onUpdate: (nextContent) => {
				renderedContent = nextContent;
			},
			preset: nextPreset
		});
	}

	$: smoothStreamController.setEnabled(renderTransitionMode !== 'none');

	$: if (renderTransitionMode !== 'none') {
		smoothStreamController.setContent(processedContent);
	} else {
		renderedContent = processedContent;
	}

	$: {
		const nextLexedContent =
			renderTransitionMode !== 'none' ? renderedContent : processedContent;

		if (nextLexedContent !== lastLexedContent) {
			lastLexedContent = nextLexedContent;
			tokens = nextLexedContent ? markdownParser.lexer(nextLexedContent) : [];
		}
	}

	$: headings = tokens.length > 0 ? extractHeadings(tokens, id) : [];

	onDestroy(() => {
		if (delayedAnimatedTimer) {
			clearTimeout(delayedAnimatedTimer);
		}
		smoothStreamController.destroy();
	});
</script>

<MarkdownTokens
	{tokens}
	{id}
	messageId={id}
	{save}
	charAnimation={renderTransitionMode !== 'none'}
	{onTaskClick}
	{onSourceClick}
	on:update={(e) => {
		dispatch('update', e.detail);
	}}
	on:code={(e) => {
		dispatch('code', e.detail);
	}}
/>
