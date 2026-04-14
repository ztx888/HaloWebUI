import sha256 from 'js-sha256';

export type SelectionThreadAnchor = {
	start: number;
	end: number;
	exact: string;
	prefix: string;
	suffix: string;
};

export type SelectionThreadUserTurn = {
	id: string;
	role: 'user';
	displayContent: string;
	requestContent: string;
};

export type SelectionThreadAssistantTurn = {
	id: string;
	role: 'assistant';
	content: string;
	state: 'streaming' | 'done' | 'error' | 'interrupted';
	usage?: Record<string, unknown>;
};

export type SelectionThreadTurn = SelectionThreadUserTurn | SelectionThreadAssistantTurn;

export type SelectionThread = {
	id: string;
	sourceMessageId: string;
	sourceMessageHash: string;
	anchor: SelectionThreadAnchor;
	quote: string;
	pinned: boolean;
	draft: string;
	turns: SelectionThreadTurn[];
	addedToConversationAt?: number;
	createdAt: number;
	updatedAt: number;
	actionId?: string;
};

export type PersistedSelectionThreads = {
	version: 1;
	items: SelectionThread[];
};

export const SELECTION_THREADS_VERSION = 1 as const;
const ANCHOR_CONTEXT_CHARS = 24;

export const createEmptySelectionThreads = (): PersistedSelectionThreads => ({
	version: SELECTION_THREADS_VERSION,
	items: []
});

export const hashSelectionThreadSource = (value: string) => sha256(value ?? '');

export const sameSelectionThreadAnchor = (
	left: SelectionThreadAnchor,
	right: SelectionThreadAnchor
): boolean =>
	left.start === right.start &&
	left.end === right.end &&
	left.exact === right.exact &&
	left.prefix === right.prefix &&
	left.suffix === right.suffix;

export const sortSelectionThreads = (threads: SelectionThread[]) =>
	[...(threads ?? [])].sort((left, right) => left.createdAt - right.createdAt);

export const createSelectionThread = ({
	id,
	sourceMessageId,
	sourceMessageHash,
	anchor,
	quote,
	actionId,
	pinned = false,
	draft = '',
	turns = [],
	addedToConversationAt,
	createdAt = Date.now(),
	updatedAt = createdAt
}: {
	id: string;
	sourceMessageId: string;
	sourceMessageHash: string;
	anchor: SelectionThreadAnchor;
	quote: string;
	actionId?: string;
	pinned?: boolean;
	draft?: string;
	turns?: SelectionThreadTurn[];
	addedToConversationAt?: number;
	createdAt?: number;
	updatedAt?: number;
}): SelectionThread => ({
	id,
	sourceMessageId,
	sourceMessageHash,
	anchor,
	quote,
	pinned,
	draft,
	turns,
	...(typeof addedToConversationAt === 'number' ? { addedToConversationAt } : {}),
	createdAt,
	updatedAt,
	...(typeof actionId === 'string' && actionId ? { actionId } : {})
});

export const interruptSelectionThread = (thread: SelectionThread): SelectionThread => {
	let hasInterruptedTurn = false;
	const turns = thread.turns.map((turn) => {
		if (turn.role !== 'assistant' || turn.state !== 'streaming') {
			return turn;
		}

		hasInterruptedTurn = true;
		return {
			...turn,
			state: 'interrupted' as const
		};
	});

	if (!hasInterruptedTurn) {
		return thread;
	}

	return {
		...thread,
		turns,
		updatedAt: Date.now()
	};
};

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

const normalizeAnchor = (value: unknown): SelectionThreadAnchor | null => {
	if (!isPlainObject(value)) {
		return null;
	}

	const start = Number(value.start);
	const end = Number(value.end);
	const exact = typeof value.exact === 'string' ? value.exact : '';

	if (!Number.isFinite(start) || !Number.isFinite(end) || exact === '') {
		return null;
	}

	return {
		start: Math.max(0, Math.trunc(start)),
		end: Math.max(Math.trunc(start), Math.trunc(end)),
		exact,
		prefix: typeof value.prefix === 'string' ? value.prefix : '',
		suffix: typeof value.suffix === 'string' ? value.suffix : ''
	};
};

type NormalizeSelectionThreadsOptions = {
	coerceStreamingToInterrupted?: boolean;
};

const normalizeTurn = (
	value: unknown,
	options: NormalizeSelectionThreadsOptions = {}
): SelectionThreadTurn | null => {
	const { coerceStreamingToInterrupted = false } = options;

	if (!isPlainObject(value) || typeof value.id !== 'string') {
		return null;
	}

	if (value.role === 'user') {
		const displayContent =
			typeof value.displayContent === 'string'
				? value.displayContent
				: typeof value.requestContent === 'string'
					? value.requestContent
					: '';
		const requestContent =
			typeof value.requestContent === 'string' ? value.requestContent : displayContent;

		if (displayContent === '' && requestContent === '') {
			return null;
		}

		return {
			id: value.id,
			role: 'user',
			displayContent,
			requestContent
		};
	}

	if (value.role === 'assistant') {
		const normalizedState =
			value.state === 'streaming' ||
			value.state === 'done' ||
			value.state === 'error' ||
			value.state === 'interrupted'
				? value.state
				: 'done';
		const state =
			coerceStreamingToInterrupted && normalizedState === 'streaming'
				? 'interrupted'
				: normalizedState;

		return {
			id: value.id,
			role: 'assistant',
			content: typeof value.content === 'string' ? value.content : '',
			state,
			...(isPlainObject(value.usage) ? { usage: value.usage as Record<string, unknown> } : {})
		};
	}

	return null;
};

const normalizeThread = (
	value: unknown,
	options: NormalizeSelectionThreadsOptions = {}
): SelectionThread | null => {
	if (!isPlainObject(value)) {
		return null;
	}

	const anchor = normalizeAnchor(value.anchor);
	if (
		anchor === null ||
		typeof value.id !== 'string' ||
		typeof value.sourceMessageId !== 'string' ||
		typeof value.sourceMessageHash !== 'string' ||
		typeof value.quote !== 'string'
	) {
		return null;
	}

	const turns = Array.isArray(value.turns)
		? value.turns
				.map((turn) => normalizeTurn(turn, options))
				.filter((turn): turn is SelectionThreadTurn => turn !== null)
		: [];

	return createSelectionThread({
		id: value.id,
		sourceMessageId: value.sourceMessageId,
		sourceMessageHash: value.sourceMessageHash,
		anchor,
		quote: value.quote,
		pinned: Boolean(value.pinned),
		draft: typeof value.draft === 'string' ? value.draft : '',
		turns,
		addedToConversationAt:
			typeof value.addedToConversationAt === 'number' ? value.addedToConversationAt : undefined,
		createdAt:
			typeof value.createdAt === 'number' && Number.isFinite(value.createdAt)
				? value.createdAt
				: Date.now(),
		updatedAt:
			typeof value.updatedAt === 'number' && Number.isFinite(value.updatedAt)
				? value.updatedAt
				: Date.now(),
		actionId: typeof value.actionId === 'string' && value.actionId ? value.actionId : undefined
	});
};

export const normalizeSelectionThreads = (
	value: unknown,
	options: NormalizeSelectionThreadsOptions = {}
): PersistedSelectionThreads => {
	if (!isPlainObject(value) || !Array.isArray(value.items)) {
		return createEmptySelectionThreads();
	}

	return {
		version: SELECTION_THREADS_VERSION,
		items: sortSelectionThreads(
			value.items
				.map((item) => normalizeThread(item, options))
				.filter((item): item is SelectionThread => item !== null)
		)
	};
};

export const quoteSelectionText = (value: string) =>
	(value ?? '')
		.split('\n')
		.map((line) => `> ${line}`)
		.join('\n');

export const buildSelectionThreadPrompt = (
	actionPrompt: string,
	quote: string,
	inputContent = ''
) => {
	return (actionPrompt ?? '')
		.replaceAll('{{INPUT_CONTENT}}', inputContent)
		.replaceAll('{{CONTENT}}', quote)
		.replaceAll('{{SELECTED_CONTENT}}', quoteSelectionText(quote));
};

export const serializeSelectionRange = (
	root: HTMLElement,
	range: Range
): SelectionThreadAnchor | null => {
	if (!root?.contains(range.commonAncestorContainer)) {
		return null;
	}

	const text = root.textContent ?? '';
	const exact = range.toString();
	if (exact.trim() === '') {
		return null;
	}

	const preRange = document.createRange();
	preRange.selectNodeContents(root);
	preRange.setEnd(range.startContainer, range.startOffset);

	const start = preRange.toString().length;
	const end = start + exact.length;

	return {
		start,
		end,
		exact,
		prefix: text.slice(Math.max(0, start - ANCHOR_CONTEXT_CHARS), start),
		suffix: text.slice(end, end + ANCHOR_CONTEXT_CHARS)
	};
};

type TextNodeEntry = {
	node: Text;
	start: number;
	end: number;
};

const collectTextNodeEntries = (root: HTMLElement): { text: string; entries: TextNodeEntry[] } => {
	const entries: TextNodeEntry[] = [];
	let cursor = 0;
	let text = '';
	const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);

	let currentNode = walker.nextNode();
	while (currentNode) {
		const node = currentNode as Text;
		const value = node.textContent ?? '';
		if (value !== '') {
			const start = cursor;
			cursor += value.length;
			text += value;
			entries.push({
				node,
				start,
				end: cursor
			});
		}
		currentNode = walker.nextNode();
	}

	return { text, entries };
};

const createRangeFromOffsets = (
	root: HTMLElement,
	entries: TextNodeEntry[],
	start: number,
	end: number
): Range | null => {
	if (start < 0 || end <= start) {
		return null;
	}

	let startEntry: TextNodeEntry | null = null;
	let endEntry: TextNodeEntry | null = null;

	for (const entry of entries) {
		if (startEntry === null && start >= entry.start && start <= entry.end) {
			startEntry = entry;
		}

		if (endEntry === null && end >= entry.start && end <= entry.end) {
			endEntry = entry;
		}

		if (startEntry && endEntry) {
			break;
		}
	}

	if (startEntry === null || endEntry === null) {
		return null;
	}

	const range = document.createRange();
	range.setStart(startEntry.node, Math.max(0, start - startEntry.start));
	range.setEnd(endEntry.node, Math.max(0, end - endEntry.start));

	if (!root.contains(range.commonAncestorContainer)) {
		return null;
	}

	return range;
};

export const findSelectionAnchorOffsets = (
	text: string,
	anchor: SelectionThreadAnchor,
	options: {
		preferOffsets?: boolean;
	} = {}
): { start: number; end: number } | null => {
	const { preferOffsets = true } = options;

	if (!anchor.exact || !text) {
		return null;
	}

	if (
		preferOffsets &&
		anchor.start >= 0 &&
		anchor.end > anchor.start &&
		text.slice(anchor.start, anchor.end) === anchor.exact
	) {
		return {
			start: anchor.start,
			end: anchor.end
		};
	}

	const matches: number[] = [];
	let cursor = text.indexOf(anchor.exact);

	while (cursor !== -1) {
		matches.push(cursor);
		cursor = text.indexOf(anchor.exact, cursor + 1);
	}

	if (matches.length === 0) {
		return null;
	}

	let bestMatch = matches[0];
	let bestScore = -1;

	for (const start of matches) {
		const end = start + anchor.exact.length;
		let score = 0;

		if (anchor.prefix && text.slice(Math.max(0, start - anchor.prefix.length), start) === anchor.prefix) {
			score += 2;
		}

		if (anchor.suffix && text.slice(end, end + anchor.suffix.length) === anchor.suffix) {
			score += 2;
		}

		const distance = Math.abs(start - anchor.start);
		score += Math.max(0, 1 - Math.min(distance, 1000) / 1000);

		if (score > bestScore) {
			bestScore = score;
			bestMatch = start;
		}
	}

	return {
		start: bestMatch,
		end: bestMatch + anchor.exact.length
	};
};

export const resolveSelectionAnchorRange = (
	root: HTMLElement,
	anchor: SelectionThreadAnchor,
	options: {
		preferOffsets?: boolean;
	} = {}
): Range | null => {
	const { text, entries } = collectTextNodeEntries(root);
	const offsets = findSelectionAnchorOffsets(text, anchor, options);

	if (!offsets) {
		return null;
	}

	return createRangeFromOffsets(root, entries, offsets.start, offsets.end);
};

export const materializeSelectionThreadMessages = (thread: SelectionThread) =>
	thread.turns.flatMap((turn) => {
		if (turn.role === 'user') {
			return [
				{
					role: 'user' as const,
					content: turn.requestContent
				}
			];
		}

		return [
			{
				role: 'assistant' as const,
				content: turn.content,
				...(turn.usage ? { usage: turn.usage } : {})
			}
		];
	});

