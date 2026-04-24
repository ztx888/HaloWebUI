import { createMessagesList } from '$lib/utils';

type PdfExportImageFile = {
	type: 'image';
	id?: string;
	name?: string;
	url: string;
	size?: number;
	content_type?: string;
};

type PdfExportCodeExecution = {
	id?: string;
	uuid?: string;
	name?: string;
	code?: string;
	language?: string;
	result?: {
		error?: string;
		output?: string;
		files?: Array<{
			type?: string;
			name?: string;
			url?: string;
			content_type?: string;
		}>;
	};
};

export type PdfExportMessage = {
	id?: string;
	role: string;
	content: string;
	model?: string;
	modelName?: string;
	timestamp?: number;
	completedAt?: number;
	instruction?: string;
	usage?: Record<string, unknown>;
	info?: Record<string, unknown>;
	files?: PdfExportImageFile[];
	code_executions?: PdfExportCodeExecution[];
};

const REASONING_DETAILS_REGEX = /<details\b[^>]*type="reasoning"[^>]*>[\s\S]*?<\/details>/gi;
const DETAILS_OPEN_TAG_REGEX = /^<details\b[^>]*>/i;
const DETAILS_SUMMARY_REGEX = /<summary\b[^>]*>[\s\S]*?<\/summary>/i;

const getVisibleMessageReasoningStates = (messageId?: string): boolean[] | null => {
	if (typeof document === 'undefined' || !messageId) {
		return null;
	}

	const messageElement = document.getElementById(`message-${messageId}`);
	if (!messageElement) {
		return null;
	}

	const reasoningBlocks = Array.from(
		messageElement.querySelectorAll<HTMLElement>(
			'[data-pdf-collapsible="true"][data-pdf-type="reasoning"]'
		)
	);

	if (reasoningBlocks.length === 0) {
		return null;
	}

	return reasoningBlocks.map((element) => element.dataset.pdfOpen === 'true');
};

const applyReasoningVisibilityToContent = (
	content: string,
	reasoningStates: boolean[] | null
): string => {
	if (!content || !reasoningStates || reasoningStates.length === 0) {
		return content;
	}

	let reasoningIndex = 0;

	return content.replace(REASONING_DETAILS_REGEX, (match) => {
		const isOpen = reasoningStates[reasoningIndex];
		reasoningIndex += 1;

		if (isOpen !== false) {
			return match;
		}

		const openTag = match.match(DETAILS_OPEN_TAG_REGEX)?.[0] ?? '<details type="reasoning">';
		const summary = match.match(DETAILS_SUMMARY_REGEX)?.[0];

		return [openTag, summary, '</details>'].filter(Boolean).join('\n');
	});
};

const normalizeImageFile = (file: any): PdfExportImageFile | null => {
	if (!file || file.type !== 'image' || typeof file?.url !== 'string' || !file.url) {
		return null;
	}

	return {
		type: 'image',
		...(typeof file?.id === 'string' && file.id ? { id: file.id } : {}),
		...(typeof file?.name === 'string' && file.name ? { name: file.name } : {}),
		url: file.url,
		...(typeof file?.size === 'number' ? { size: file.size } : {}),
		...(typeof file?.content_type === 'string' && file.content_type
			? { content_type: file.content_type }
			: {})
	};
};

const normalizeCodeExecution = (execution: any): PdfExportCodeExecution | null => {
	if (!execution || typeof execution !== 'object') {
		return null;
	}

	const normalizedFiles = Array.isArray(execution?.result?.files)
		? execution.result.files
				.filter((file: any) => file && typeof file === 'object' && typeof file?.url === 'string')
				.map((file: any) => ({
					...(typeof file?.type === 'string' ? { type: file.type } : {}),
					...(typeof file?.name === 'string' ? { name: file.name } : {}),
					url: file.url,
					...(typeof file?.content_type === 'string'
						? { content_type: file.content_type }
						: {})
				}))
		: [];

	return {
		...(typeof execution?.id === 'string' ? { id: execution.id } : {}),
		...(typeof execution?.uuid === 'string' ? { uuid: execution.uuid } : {}),
		...(typeof execution?.name === 'string' ? { name: execution.name } : {}),
		...(typeof execution?.code === 'string' ? { code: execution.code } : {}),
		...(typeof execution?.language === 'string' ? { language: execution.language } : {}),
		...(execution?.result && typeof execution.result === 'object'
			? {
					result: {
						...(typeof execution.result?.error === 'string'
							? { error: execution.result.error }
							: {}),
						...(typeof execution.result?.output === 'string'
							? { output: execution.result.output }
							: {}),
						...(normalizedFiles.length > 0 ? { files: normalizedFiles } : {})
					}
				}
			: {})
	};
};

export const buildPdfExportMessages = (chat: any): PdfExportMessage[] => {
	const history = chat?.chat?.history;
	if (!history?.currentId) {
		return [];
	}

	return createMessagesList(history, history.currentId).map((message: any) => {
		const reasoningStates = getVisibleMessageReasoningStates(message?.id);
		const content =
			typeof message?.content === 'string'
				? applyReasoningVisibilityToContent(message.content, reasoningStates)
				: '';

		return {
			...(typeof message?.id === 'string' ? { id: message.id } : {}),
			role: typeof message?.role === 'string' ? message.role : 'assistant',
			content,
			...(typeof message?.model === 'string' && message.model ? { model: message.model } : {}),
			...(typeof message?.modelName === 'string' && message.modelName ? { modelName: message.modelName } : {}),
			...(typeof message?.timestamp === 'number' ? { timestamp: message.timestamp } : {}),
			...(typeof message?.completedAt === 'number' ? { completedAt: message.completedAt } : {}),
			...(typeof message?.instruction === 'string' && message.instruction
				? { instruction: message.instruction }
				: {}),
			...(message?.usage && typeof message.usage === 'object' ? { usage: message.usage } : {}),
			...(message?.info && typeof message.info === 'object' ? { info: message.info } : {}),
			...(Array.isArray(message?.files)
				? {
						files: message.files
							.map((file: any) => normalizeImageFile(file))
							.filter(Boolean) as PdfExportImageFile[]
					}
				: {}),
			...(Array.isArray(message?.code_executions)
				? {
						code_executions: message.code_executions
							.map((execution: any) => normalizeCodeExecution(execution))
							.filter(Boolean) as PdfExportCodeExecution[]
					}
				: {})
		};
	});
};

export const buildPdfFileName = (title?: string | null) => {
	const baseName = (title ?? '').trim() || 'chat';
	return `chat-${baseName.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')}.pdf`;
};
