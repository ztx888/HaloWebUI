<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import TurndownService from 'turndown';
	import { onDestroy, onMount, tick } from 'svelte';
	import { createEventDispatcher } from 'svelte';
	import { DOMParser } from 'prosemirror-model';
	import { TextSelection } from 'prosemirror-state';
	import { Editor } from '@tiptap/core';
	import Code from '@tiptap/extension-code';
	import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
	import Highlight from '@tiptap/extension-highlight';
	import Placeholder from '@tiptap/extension-placeholder';
	import StarterKit from '@tiptap/starter-kit';
	import Typography from '@tiptap/extension-typography';
	import { all, createLowlight } from 'lowlight';

	import { PASTED_TEXT_CHARACTER_LIMIT } from '$lib/constants';
	import { AIAutocompletion } from './RichTextInput/AutoCompletion.js';
	import Mention from './RichTextInput/MultiTriggerMention';
	import {
		hydrateMentionTagsInHtml,
		serializeMentionTag
	} from './RichTextInput/mentions';

	const turndownService = new TurndownService({
		codeBlockStyle: 'fenced',
		headingStyle: 'atx'
	});
	turndownService.escape = (string) => string;
	turndownService.addRule('mentions', {
		filter: (node) => node.nodeName === 'SPAN' && node.getAttribute('data-type') === 'mention',
		replacement: (_content, node: HTMLElement) => {
			const id = node.getAttribute('data-id') || '';
			const ch = node.getAttribute('data-mention-suggestion-char') || '@';
			const label = node.getAttribute('data-label') || '';
			return serializeMentionTag(ch, id, label);
		}
	});

	const eventDispatch = createEventDispatcher();
	const lowlight = createLowlight(all);

	export let oncompositionstart = (_e) => {};
	export let oncompositionend = (_e) => {};
	export let onChange = (_content) => {};

	export let className = 'input-prose';
	export let placeholder = 'Type here...';
	export let value: any = '';
	export let html = '';
	export let id = '';

	export let raw = false;
	export let json = false;
	export let editable = true;

	export let preserveBreaks = false;
	export let generateAutoCompletion: Function = async () => null;
	export let autocomplete = false;
	export let messageInput = false;
	export let shiftEnter = false;
	export let largeTextAsFile = false;
	export let showFormattingToolbar = true;
	export let insertPromptAsRichText = false;
	export let suggestions = null;

	let element;
	let editor: Editor | null = null;

	const tryParseMarkdown = async (input: string) => {
		try {
			return marked.parse(input.replaceAll(`\n<br/>`, `<br/>`), {
				breaks: false
			});
		} catch {
			return input;
		}
	};

	const escapeHtml = (text: string) =>
		text
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;');

	const toMentionHtml = (text: string) =>
		text
			.split('\n')
			.map((line) => (line ? `<p>${hydrateMentionTagsInHtml(escapeHtml(line))}</p>` : '<p></p>'))
			.join('');

	const htmlToMarkdown = (htmlValue: string) => {
		let mdValue = turndownService
			.turndown(
				(preserveBreaks ? htmlValue.replace(/<p><\/p>/g, '<br/>') : htmlValue).replace(
					/ {2,}/g,
					(match) => match.replace(/ /g, '\u00a0')
				)
			)
			.replace(/\u00a0/g, ' ');

		if (!preserveBreaks) {
			mdValue = mdValue.replace(/<br\/>/g, '');
		}

		return mdValue;
	};

	const buildContent = async (inputValue: any) => {
		if (json) {
			return inputValue || html || null;
		}

		if (raw) {
			return inputValue || html || '';
		}

		return hydrateMentionTagsInHtml(await tryParseMarkdown(String(inputValue ?? '')));
	};

	const findNextTemplate = (doc, from = 0) => {
		let result = null;

		doc.nodesBetween(from, doc.content.size, (node, pos) => {
			if (result || !node.isText) {
				return result ? false : undefined;
			}

			const text = node.text;
			let index = Math.max(0, from - pos);

			while (index < text.length) {
				if (text.startsWith('{{', index)) {
					const endIndex = text.indexOf('}}', index + 2);
					if (endIndex !== -1) {
						result = {
							from: pos + index,
							to: pos + endIndex + 2
						};
						return false;
					}
				}
				index++;
			}
		});

		return result;
	};

	const selectNextTemplate = (state, dispatch) => {
		const { doc, selection } = state;
		let template = findNextTemplate(doc, selection.to);

		if (!template) {
			template = findNextTemplate(doc, 0);
		}

		if (template && dispatch) {
			dispatch(state.tr.setSelection(TextSelection.create(doc, template.from, template.to)));
			return true;
		}

		return false;
	};

	export const focus = () => {
		if (!editor || editor.isDestroyed) {
			return;
		}

		try {
			editor.view.focus();
			editor.view.dispatch(editor.view.state.tr.scrollIntoView());
		} catch (error) {
			console.warn('Error focusing editor', error);
		}
	};

	export const setContent = (content) => {
		editor?.commands.setContent(content);
	};

	export const getWordAtDocPos = () => {
		if (!editor) {
			return '';
		}

		const { state } = editor.view;
		const pos = state.selection.from;
		const resolvedPos = state.doc.resolve(pos);
		const text = resolvedPos.parent.textContent;
		const offset = resolvedPos.parentOffset;

		let wordStart = offset;
		let wordEnd = offset;

		while (wordStart > 0 && !/\s/.test(text[wordStart - 1])) {
			wordStart--;
		}

		while (wordEnd < text.length && !/\s/.test(text[wordEnd])) {
			wordEnd++;
		}

		return text.slice(wordStart, wordEnd);
	};

	const getWordBoundsAtPos = (doc, pos) => {
		const resolvedPos = doc.resolve(pos);
		const text = resolvedPos.parent.textContent;
		const paraStart = resolvedPos.start();
		const offset = resolvedPos.parentOffset;

		let wordStart = offset;
		let wordEnd = offset;

		while (wordStart > 0 && !/\s/.test(text[wordStart - 1])) {
			wordStart--;
		}

		while (wordEnd < text.length && !/\s/.test(text[wordEnd])) {
			wordEnd++;
		}

		return {
			start: paraStart + wordStart,
			end: paraStart + wordEnd
		};
	};

	const textToNodes = (state, text) => {
		if (!text.includes('\n')) {
			return text ? state.schema.text(text) : [];
		}

		const nodes = [];
		text.split('\n').forEach((line, index) => {
			if (index > 0) {
				nodes.push(state.schema.nodes.hardBreak.create());
			}
			if (line) {
				nodes.push(state.schema.text(line));
			}
		});

		return nodes;
	};

	export const replaceCommandWithText = async (text) => {
		if (!editor) {
			return;
		}

		const { state, dispatch } = editor.view;
		const { start, end } = getWordBoundsAtPos(state.doc, state.selection.from);
		let tr = state.tr;

		if (insertPromptAsRichText) {
			const htmlContent = DOMPurify.sanitize(
				hydrateMentionTagsInHtml(
					String(
						marked.parse(text, {
							breaks: true,
							gfm: true
						})
					).trim()
				)
			);
			const tempDiv = document.createElement('div');
			tempDiv.innerHTML = htmlContent;
			const fragment = DOMParser.fromSchema(state.schema).parse(tempDiv).content;

			const nodesToInsert = [];
			fragment.forEach((node) => {
				if (node.type.name === 'paragraph') {
					nodesToInsert.push(...node.content.content);
				} else {
					nodesToInsert.push(node);
				}
			});

			tr = tr.replaceWith(start, end, nodesToInsert);
			const nextPos = start + nodesToInsert.reduce((sum, node) => sum + node.nodeSize, 0);
			tr = tr.setSelection(TextSelection.near(tr.doc.resolve(nextPos)));
		} else {
			tr = tr.replaceWith(start, end, textToNodes(state, text));
			tr = tr.setSelection(TextSelection.near(tr.doc.resolve(Math.max(start + text.length, 1))));
		}

		dispatch(tr);
		await tick();
	};

	export const setText = (text: string) => {
		if (!editor) {
			return;
		}

		if (text === '') {
			editor.commands.clearContent();
		} else {
			editor.commands.setContent(toMentionHtml(text.replaceAll('\n\n', '\n')));
		}

		selectTemplate();
		focus();
	};

	export const insertContent = (content) => {
		if (!editor) {
			return;
		}

		if (typeof content === 'string') {
			editor.commands.insertContent(hydrateMentionTagsInHtml(String(marked.parse(content))));
		} else {
			editor.commands.insertContent(content);
		}

		focus();
	};

	export const replaceVariables = (variables) => {
		if (!editor) {
			return;
		}

		const { state, view } = editor;
		const replacements = [];
		let tr = state.tr;

		state.doc.descendants((node, pos) => {
			if (!node.isText || !node.text) {
				return;
			}

			const replacedText = node.text.replace(/{{\s*([^|}]+)(?:\|[^}]*)?\s*}}/g, (match, varName) => {
				const trimmedVarName = varName.trim();
				return Object.prototype.hasOwnProperty.call(variables, trimmedVarName)
					? String(variables[trimmedVarName])
					: match;
			});

			if (replacedText !== node.text) {
				replacements.push({
					from: pos,
					to: pos + node.text.length,
					text: replacedText
				});
			}
		});

		replacements.reverse().forEach(({ from, to, text }) => {
			tr = tr.replaceWith(from, to, textToNodes(state, text));
		});

		if (replacements.length > 0) {
			view.dispatch(tr);
		}
	};

	const selectTemplate = () => {
		if (!editor || value === '') {
			return;
		}

		setTimeout(() => {
			const templateFound = selectNextTemplate(editor.view.state, editor.view.dispatch);
			if (!templateFound) {
				const endPos = editor.view.state.doc.content.size;
				editor.view.dispatch(
					editor.view.state.tr.setSelection(TextSelection.create(editor.view.state.doc, endPos))
				);
			}
		}, 0);
	};

	const onValueChange = async () => {
		if (!editor) {
			return;
		}

		if (value === '') {
			editor.commands.clearContent();
			selectTemplate();
			return;
		}

		const currentJson = editor.getJSON();
		const currentHtml = editor.getHTML();
		const currentMd = htmlToMarkdown(currentHtml);

		if (json) {
			if (JSON.stringify(value) !== JSON.stringify(currentJson)) {
				editor.commands.setContent(value);
				selectTemplate();
			}
			return;
		}

		if (raw) {
			if (value !== currentHtml) {
				editor.commands.setContent(value);
				selectTemplate();
			}
			return;
		}

		if (value !== currentMd) {
			editor.commands.setContent(await buildContent(value));
			selectTemplate();
		}
	};

	onMount(async () => {
		if (preserveBreaks) {
			turndownService.addRule('preserveBreaks', {
				filter: 'br',
				replacement: () => '<br/>'
			});
		}

		editor = new Editor({
			element,
			extensions: [
				StarterKit.configure({
					code: false
				}),
				Code.configure({
					inputRules: false
				}),
				CodeBlockLowlight.configure({
					lowlight
				}),
				Highlight,
				Typography,
				Placeholder.configure({ placeholder }),
				...(suggestions
					? [
							Mention.configure({
								HTMLAttributes: { class: 'mention' },
								suggestions
							})
						]
					: []),
				...(autocomplete
					? [
							AIAutocompletion.configure({
								generateCompletion: async (text) => {
									if (text.trim().length === 0) {
										return null;
									}

									const suggestion = await generateAutoCompletion(text).catch(() => null);
									return suggestion && suggestion.trim().length > 0 ? suggestion : null;
								}
							})
						]
					: [])
			],
			content: await buildContent(value),
			autofocus: messageInput,
			onTransaction: () => {
				if (!editor) {
					return;
				}

				const htmlValue = editor.getHTML();
				const jsonValue = editor.getJSON();
				const mdValue = htmlToMarkdown(htmlValue);

				onChange({
					html: htmlValue,
					json: jsonValue,
					md: mdValue
				});

				if (json) {
					value = jsonValue;
					return;
				}

				value = raw ? htmlValue : mdValue;

				if (editor.isActive('paragraph') && value === '') {
					editor.commands.clearContent();
				}
			},
			editorProps: {
				attributes: { id },
				handleDOMEvents: {
					compositionstart: (_view, event) => {
						oncompositionstart(event);
						return false;
					},
					compositionend: (_view, event) => {
						oncompositionend(event);
						return false;
					},
					focus: (_view, event) => {
						eventDispatch('focus', { event });
						return false;
					},
					keyup: (_view, event) => {
						eventDispatch('keyup', { event });
						return false;
					},
					keydown: (view, event) => {
						if (messageInput) {
							const { state } = view;
							const { $head } = state.selection;

							const isInside = (nodeTypes: string[]) => {
								let currentNode = $head;
								while (currentNode) {
									if (nodeTypes.includes(currentNode.parent.type.name)) {
										return true;
									}
									if (!currentNode.depth) {
										break;
									}
									currentNode = state.doc.resolve(currentNode.before());
								}
								return false;
							};

							if (event.key === 'Tab' && !isInside(['codeBlock'])) {
								const handled = selectNextTemplate(view.state, view.dispatch);
								if (handled) {
									event.preventDefault();
									return true;
								}
							}

							if (event.key === 'Enter') {
								const isInCodeBlock = isInside(['codeBlock']);
								const isInList = isInside(['listItem', 'bulletList', 'orderedList', 'taskList']);
								const isInHeading = isInside(['heading']);

								if (isInCodeBlock || isInList || isInHeading) {
									return false;
								}
							}

							if (shiftEnter && event.key === 'Enter' && event.shiftKey && !event.ctrlKey && !event.metaKey) {
								editor?.commands.setHardBreak();
								view.dispatch(view.state.tr.scrollIntoView());
								event.preventDefault();
								return true;
							}
						}

						eventDispatch('keydown', { event });
						return false;
					},
					paste: (view, event) => {
						if (event.clipboardData) {
							const plainText = event.clipboardData.getData('text/plain');
							if (plainText) {
								if (largeTextAsFile && plainText.length > PASTED_TEXT_CHARACTER_LIMIT) {
									eventDispatch('paste', { event });
									event.preventDefault();
									return true;
								}

								return false;
							}

							const hasFile = Array.from(event.clipboardData.files).length > 0;
							const hasImageItem = Array.from(event.clipboardData.items).some((item) =>
								item.type.startsWith('image/')
							);

							if (hasFile || hasImageItem) {
								eventDispatch('paste', { event });
								event.preventDefault();
								return true;
							}
						}

						view.dispatch(view.state.tr.scrollIntoView());
						return false;
					}
				}
			}
		});

		editor.setEditable(editable);

		if (messageInput) {
			selectTemplate();
		}
	});

	onDestroy(() => {
		editor?.destroy();
	});

	$: if (editor) {
		editor.setEditable(editable);
	}

	$: if (editor && (json || raw || typeof value === 'string')) {
		onValueChange();
	}
</script>

{#if showFormattingToolbar && editor}
	<div class="flex items-center gap-1 px-1 pb-1 text-xs">
		<button
			type="button"
			class="px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
			on:click={() => editor?.chain().focus().toggleBold().run()}
		>
			B
		</button>
		<button
			type="button"
			class="px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition italic"
			on:click={() => editor?.chain().focus().toggleItalic().run()}
		>
			I
		</button>
		<button
			type="button"
			class="px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
			on:click={() => editor?.chain().focus().toggleCode().run()}
		>
			{'</>'}
		</button>
		<button
			type="button"
			class="px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
			on:click={() => editor?.chain().focus().toggleBulletList().run()}
		>
			•
		</button>
	</div>
{/if}

<div bind:this={element} class="relative w-full min-w-full h-full min-h-fit {className}" />
