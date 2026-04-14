// @ts-nocheck
import Mention from '@tiptap/extension-mention';
import { mergeAttributes } from '@tiptap/core';
import { PluginKey } from '@tiptap/pm/state';
import Suggestion from '@tiptap/suggestion';

const getSuggestionChar = (attrs, fallback = '@') => attrs?.suggestionChar || fallback;
const getSuggestionLabel = (attrs) => attrs?.label ?? attrs?.id ?? '';

const MultiTriggerMention = Mention.extend({
	addOptions() {
		const parentOptions = this.parent?.() ?? {};

		return {
			...parentOptions,
			suggestions: [],
			renderText: ({ options, node }) =>
				`${getSuggestionChar(node.attrs, options.suggestion?.char)}${getSuggestionLabel(node.attrs)}`,
			renderHTML: ({ options, node }) => [
				'span',
				mergeAttributes(this.HTMLAttributes, options.HTMLAttributes),
				`${getSuggestionChar(node.attrs, options.suggestion?.char)}${getSuggestionLabel(node.attrs)}`
			]
		};
	},

	addAttributes() {
		return {
			...(this.parent?.() ?? {}),
			suggestionChar: {
				default: null,
				parseHTML: (element) => element.getAttribute('data-mention-suggestion-char'),
				renderHTML: (attributes) => {
					if (!attributes.suggestionChar) {
						return {};
					}

					return {
						'data-mention-suggestion-char': attributes.suggestionChar
					};
				}
			}
		};
	},

	addKeyboardShortcuts() {
		return {
			Backspace: () =>
				this.editor.commands.command(({ tr, state }) => {
					let isMention = false;
					const { selection } = state;
					const { empty, anchor } = selection;

					if (!empty) {
						return false;
					}

					state.doc.nodesBetween(anchor - 1, anchor, (node, pos) => {
						if (node.type.name === this.name) {
							isMention = true;
							tr.insertText(
								this.options.deleteTriggerWithBackspace
									? ''
									: getSuggestionChar(node.attrs, this.options.suggestion?.char || ''),
								pos,
								pos + node.nodeSize
							);

							return false;
						}
					});

					return isMention;
				})
		};
	},

	addProseMirrorPlugins() {
		const baseSuggestion = this.options.suggestion ?? {};
		const suggestions =
			Array.isArray(this.options.suggestions) && this.options.suggestions.length > 0
				? this.options.suggestions
				: [baseSuggestion];

		return suggestions.map((suggestion, index) => {
			const mergedSuggestion = {
				...baseSuggestion,
				...suggestion,
				pluginKey:
					suggestion?.pluginKey ??
					new PluginKey(`mention-${suggestion?.char ?? baseSuggestion?.char ?? index}`)
			};

			return Suggestion({
				editor: this.editor,
				...mergedSuggestion,
				command: ({ editor, range, props }) => {
					const nodeAfter = editor.view.state.selection.$to.nodeAfter;
					const overrideSpace = nodeAfter?.text?.startsWith(' ');

					if (overrideSpace) {
						range.to += 1;
					}

					editor
						.chain()
						.focus()
						.insertContentAt(range, [
							{
								type: this.name,
								attrs: {
									...props,
									suggestionChar: mergedSuggestion.char ?? baseSuggestion.char ?? '@'
								}
							},
							{
								type: 'text',
								text: ' '
							}
						])
						.run();

					editor.view.dom.ownerDocument.defaultView?.getSelection()?.collapseToEnd();
				}
			});
		});
	}
});

export default MultiTriggerMention;
