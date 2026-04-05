export type MCPHeaderItem = {
	key: string;
	value: string;
};

export type MCPHeaderValidationCode =
	| 'missing_key'
	| 'invalid_name'
	| 'duplicate_key'
	| 'reserved_key'
	| 'contains_newline';

export type MCPHeaderValidationIssue = {
	index: number;
	field: 'key' | 'value';
	code: MCPHeaderValidationCode;
	key?: string;
};

const HTTP_HEADER_NAME_RE = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;

export const MCP_RESERVED_HTTP_HEADER_NAMES = new Set([
	'accept',
	'connection',
	'content-length',
	'content-type',
	'host',
	'mcp-protocol-version',
	'mcp-session-id',
	'transfer-encoding'
]);

export const createEmptyMCPHeaderItem = (): MCPHeaderItem => ({ key: '', value: '' });

export const getMCPHeaderItemsFromRecord = (
	headers?: Record<string, string> | null
): MCPHeaderItem[] =>
	Object.entries(headers ?? {}).map(([key, value]) => ({
		key,
		value: String(value ?? '')
	}));

export const prepareMCPHeaderItems = (items: MCPHeaderItem[]) => {
	const normalizedHeaders: Record<string, string> = {};
	const issues: MCPHeaderValidationIssue[] = [];
	const seenKeys = new Map<string, string>();

	for (const [index, item] of items.entries()) {
		const rawKey = String(item?.key ?? '');
		const key = rawKey.trim();
		const value = String(item?.value ?? '');

		if (!key && !value) {
			continue;
		}

		if (!key) {
			issues.push({ index, field: 'key', code: 'missing_key' });
			continue;
		}

		if (rawKey.includes('\r') || rawKey.includes('\n')) {
			issues.push({ index, field: 'key', code: 'contains_newline', key });
			continue;
		}

		if (value.includes('\r') || value.includes('\n')) {
			issues.push({ index, field: 'value', code: 'contains_newline', key });
			continue;
		}

		if (!HTTP_HEADER_NAME_RE.test(key)) {
			issues.push({ index, field: 'key', code: 'invalid_name', key });
			continue;
		}

		const lowerKey = key.toLowerCase();
		if (MCP_RESERVED_HTTP_HEADER_NAMES.has(lowerKey)) {
			issues.push({ index, field: 'key', code: 'reserved_key', key });
			continue;
		}

		if (seenKeys.has(lowerKey)) {
			issues.push({ index, field: 'key', code: 'duplicate_key', key });
			continue;
		}

		seenKeys.set(lowerKey, key);
		normalizedHeaders[key] = value;
	}

	const signature = items
		.map((item) => [String(item?.key ?? '').trim().toLowerCase(), String(item?.value ?? '')] as const)
		.filter(([key, value]) => Boolean(key) || Boolean(value))
		.sort(([leftKey, leftValue], [rightKey, rightValue]) =>
			leftKey === rightKey ? leftValue.localeCompare(rightValue) : leftKey.localeCompare(rightKey)
		);

	return {
		normalizedHeaders,
		issues,
		signature
	};
};
