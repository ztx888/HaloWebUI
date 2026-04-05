import { describe, expect, it } from 'vitest';

import {
	getMCPHeaderItemsFromRecord,
	prepareMCPHeaderItems
} from './mcp-headers';

describe('mcp headers', () => {
	it('normalizes valid header rows into a request header object', () => {
		const prepared = prepareMCPHeaderItems([
			{ key: ' X-API-Key ', value: 'abc123' },
			{ key: 'X-Trace', value: '7' },
			{ key: '', value: '' }
		]);

		expect(prepared.issues).toEqual([]);
		expect(prepared.normalizedHeaders).toEqual({
			'X-API-Key': 'abc123',
			'X-Trace': '7'
		});
		expect(prepared.signature).toEqual([
			['x-api-key', 'abc123'],
			['x-trace', '7']
		]);
	});

	it('reports duplicate and reserved header names locally', () => {
		const prepared = prepareMCPHeaderItems([
			{ key: 'Authorization', value: 'Bearer token' },
			{ key: 'authorization', value: 'Bearer token-2' },
			{ key: 'Content-Type', value: 'application/json' }
		]);

		expect(prepared.normalizedHeaders).toEqual({
			Authorization: 'Bearer token'
		});
		expect(prepared.issues).toEqual([
			{ index: 1, field: 'key', code: 'duplicate_key', key: 'authorization' },
			{ index: 2, field: 'key', code: 'reserved_key', key: 'Content-Type' }
		]);
	});

	it('flags multiline values and missing header names', () => {
		const prepared = prepareMCPHeaderItems([
			{ key: '', value: 'abc123' },
			{ key: 'X-API-Key', value: 'line1\nline2' }
		]);

		expect(prepared.normalizedHeaders).toEqual({});
		expect(prepared.issues).toEqual([
			{ index: 0, field: 'key', code: 'missing_key' },
			{ index: 1, field: 'value', code: 'contains_newline', key: 'X-API-Key' }
		]);
	});

	it('restores stored headers for presets like composio and changes signature when values change', () => {
		const presetItems = getMCPHeaderItemsFromRecord({
			'x-consumer-api-key': ''
		});
		const before = prepareMCPHeaderItems(presetItems);
		const after = prepareMCPHeaderItems([
			{ key: 'x-consumer-api-key', value: 'ck_abc' }
		]);

		expect(presetItems).toEqual([{ key: 'x-consumer-api-key', value: '' }]);
		expect(before.signature).toEqual([['x-consumer-api-key', '']]);
		expect(after.signature).toEqual([['x-consumer-api-key', 'ck_abc']]);
		expect(after.signature).not.toEqual(before.signature);
	});
});
