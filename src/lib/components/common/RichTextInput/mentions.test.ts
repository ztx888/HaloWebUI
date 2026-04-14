import { describe, expect, it } from 'vitest';

import {
	buildMentionSpan,
	getMentionDisplayLabel,
	hydrateMentionTagsInHtml,
	serializeMentionTag
} from './mentions';

describe('mention helpers', () => {
	it('serializes mention nodes without duplicating embedded labels', () => {
		expect(serializeMentionTag('@', 'U:user-1|Alice', 'Alice')).toBe('<@U:user-1|Alice>');
		expect(serializeMentionTag('$', 'skill-id|Summarize', 'Summarize')).toBe(
			'<$skill-id|Summarize>'
		);
	});

	it('serializes plain ids with an explicit label when needed', () => {
		expect(serializeMentionTag('#', 'kb-1', 'Knowledge Base')).toBe('<#kb-1|Knowledge Base>');
		expect(serializeMentionTag('@', 'model-1', '')).toBe('<@model-1>');
	});

	it('hydrates stored mention tags back into mention spans', () => {
		const hydrated = hydrateMentionTagsInHtml('Hello &lt;@U:user-1|Alice&gt; and <$skill-id|Summarize>');

		expect(hydrated).toContain('data-id="U:user-1"');
		expect(hydrated).toContain('data-label="Alice"');
		expect(hydrated).toContain('data-mention-suggestion-char="@"');
		expect(hydrated).toContain('data-id="skill-id"');
		expect(hydrated).toContain('data-label="Summarize"');
		expect(hydrated).toContain('data-mention-suggestion-char="$"');
	});

	it('derives the display label from either label or embedded id metadata', () => {
		expect(getMentionDisplayLabel('U:user-1|Alice')).toBe('Alice');
		expect(getMentionDisplayLabel('kb-1', 'Knowledge Base')).toBe('Knowledge Base');
		expect(buildMentionSpan('@', 'U:user-1|Alice')).toContain('>@Alice<');
	});
});
