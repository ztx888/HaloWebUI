import { describe, expect, it } from 'vitest';

import {
	getRenderableMessageError,
	hasVisibleMessageFiles,
	shouldHideMissingOutputError
} from './chat-message-errors';

describe('chat-message-errors', () => {
	it('treats attached files as visible output', () => {
		expect(hasVisibleMessageFiles([{ type: 'image', url: '/api/v1/files/demo/content' }])).toBe(true);
		expect(hasVisibleMessageFiles([{ type: 'image', id: 'file_123' }])).toBe(true);
	});

	it('does not treat empty file payloads as visible output', () => {
		expect(hasVisibleMessageFiles([])).toBe(false);
		expect(hasVisibleMessageFiles([{}])).toBe(false);
		expect(hasVisibleMessageFiles(null)).toBe(false);
		expect(hasVisibleMessageFiles([{ type: 'file', id: 'file_123' }])).toBe(false);
	});

	it('hides missing-output errors when visible files already exist', () => {
		const error = { type: 'empty_response', content: '模型返回了空响应（0 token）。' };
		const files = [{ type: 'image', url: '/api/v1/files/demo/content' }];

		expect(shouldHideMissingOutputError(error, files)).toBe(true);
		expect(getRenderableMessageError(error, files)).toBeNull();
	});

	it('keeps tool-no-output errors hidden only when files are visible', () => {
		const error = { type: 'tool_no_output', content: '工具调用已完成，但未生成可显示的最终回答。' };
		const files = [{ type: 'image', id: 'file_123' }];

		expect(shouldHideMissingOutputError(error, files)).toBe(true);
		expect(getRenderableMessageError(error, files)).toBeNull();
		expect(getRenderableMessageError(error, [])).toEqual(error);
	});

	it('does not hide real api errors even when files exist', () => {
		const error = { type: 'api_error', content: 'HTTP 500 upstream failed' };
		const files = [{ type: 'image', url: '/api/v1/files/demo/content' }];

		expect(shouldHideMissingOutputError(error, files)).toBe(false);
		expect(getRenderableMessageError(error, files)).toEqual(error);
	});
});
