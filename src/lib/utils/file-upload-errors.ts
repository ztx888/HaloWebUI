import { localizeCommonError } from './common-errors';

type Translate = (key: string, options?: Record<string, unknown>) => string;

type LocalizeOptions = {
	isAdmin?: boolean;
};

export type FileUploadDiagnostic = {
	code: string;
	title?: string;
	message?: string;
	hint?: string;
	blocking?: boolean;
};

const ENCODING_DETECTION_RE = /^Could not detect encoding for\s+(.+)$/is;

const getErrorText = (error: unknown): string => {
	if (typeof error === 'string') {
		return error;
	}

	if (error instanceof Error) {
		return error.message;
	}

	if (error && typeof error === 'object') {
		if ('detail' in error && typeof error.detail === 'string') {
			return error.detail;
		}

		if ('message' in error && typeof error.message === 'string') {
			return error.message;
		}

		if ('diagnostic' in error && error.diagnostic && typeof error.diagnostic === 'object') {
			const diagnostic = error.diagnostic as FileUploadDiagnostic;
			return diagnostic.message ?? diagnostic.title ?? '';
		}
	}

	return `${error ?? ''}`;
};

const getDisplayFileName = (value: string): string => {
	const normalized = value.trim().replace(/^['"]|['"]$/g, '');
	const parts = normalized.split(/[\\/]/).filter(Boolean);
	return parts[parts.length - 1] ?? normalized;
};

const coerceDiagnostic = (value: unknown): FileUploadDiagnostic | null => {
	if (!value || typeof value !== 'object') {
		return null;
	}

	if ('diagnostic' in value && value.diagnostic && typeof value.diagnostic === 'object') {
		return coerceDiagnostic(value.diagnostic);
	}

	if ('detail' in value && value.detail && typeof value.detail === 'object') {
		return coerceDiagnostic(value.detail);
	}

	if ('code' in value && typeof value.code === 'string') {
		return value as FileUploadDiagnostic;
	}

	return null;
};

const getDiagnosticKeys = (code: string): Record<string, string | null> | null => {
	switch (code) {
		case 'unsupported_archive':
			return {
				title: 'Archive not supported',
				message: 'This model does not support archive files. Please extract and upload files individually.',
				hint: null
			};
		case 'unsupported_binary_file':
			return {
				title: 'Unsupported file type',
				message: 'This file format is not supported for parsing.',
				hint: 'Upload a supported text or document file instead.'
			};
		case 'missing_text_encoding_dependency':
			return {
				title: 'Server dependency missing',
				message:
					'The server is missing the text encoding detection dependency needed to read this file.',
				hint: 'Ask an administrator to update the document parsing dependencies, then try again.'
			};
		case 'unsupported_text_encoding':
			return {
				title: 'Encoding could not be detected',
				message:
					'The file could not be read as a supported text document. It may be binary data, an archive, or use an unsupported encoding.',
				hint: 'Upload a plain text or supported document file instead.'
			};
		case 'embedding_unavailable':
			return {
				title: 'Document retrieval is not configured',
				message: 'No embedding model is available for document retrieval.',
				hint: null
			};
		case 'embedding_provider_unauthorized':
			return {
				title: 'Embedding service authentication failed',
				message: 'The embedding model service rejected the request while indexing this file.',
				hint: null
			};
		case 'embedding_provider_unreachable':
			return {
				title: 'Embedding service unreachable',
				message: 'The embedding model service could not be reached while indexing this file.',
				hint: null
			};
		case 'embedding_generation_failed':
			return {
				title: 'File indexing failed',
				message: 'The system could not build retrieval indexes for this file.',
				hint: null
			};
		case 'embedding_chunk_too_large':
			return {
				title: 'Chunk exceeds embedding model limit',
				message:
					"A single text chunk was rejected by the embedding service even after splitting the batch to one item. This usually means the chunk exceeds the embedding model's input token limit.",
				hint: null
			};
		case 'file_processing_failed':
			return {
				title: 'File processing failed',
				message: 'The file could not be parsed or indexed successfully.',
				hint: 'Try again with a supported file, or contact an administrator.'
			};
		default:
			return null;
	}
};

const getEmbeddingHintKey = (code: string, isAdmin: boolean): string | null => {
	if (!['embedding_unavailable', 'embedding_generation_failed'].includes(code)) {
		return null;
	}

	return isAdmin
		? 'Go to /settings/documents to configure an embedding model, or switch the default file processing mode to "Full Context" or "Native File".'
		: 'Ask an administrator to configure document retrieval, or switch the default file processing mode to "Full Context" or "Native File" if you have admin access.';
};

const getEmbeddingServiceHintKey = (code: string, isAdmin: boolean): string | null => {
	if (code === 'embedding_provider_unauthorized') {
		return isAdmin
			? 'Check the embedding model credentials and endpoint in /settings/documents, then try again.'
			: 'Ask an administrator to check the document retrieval credentials.';
	}

	if (code === 'embedding_provider_unreachable') {
		return isAdmin
			? 'Check the embedding model endpoint or network connection in /settings/documents, then try again.'
			: 'Ask an administrator to check the document retrieval service endpoint.';
	}

	if (code === 'embedding_chunk_too_large') {
		return isAdmin
			? 'Go to Admin → Settings → Documents, reduce Chunk Size (and optionally Chunk Overlap), then re-upload this file.'
			: 'Ask an administrator to reduce the document chunk size.';
	}

	return null;
};

export const getFileUploadDiagnostic = (error: unknown): FileUploadDiagnostic | null =>
	coerceDiagnostic(error);

export const getLocalizedFileUploadDiagnostic = (
	error: unknown,
	t: Translate,
	options: LocalizeOptions = {}
) => {
	const diagnostic = coerceDiagnostic(error);

	if (diagnostic) {
		const keys = getDiagnosticKeys(diagnostic.code);
		const title = keys?.title ? t(keys.title) : diagnostic.title ?? t('Upload failed');
		const message = keys?.message ? t(keys.message) : diagnostic.message ?? t('Upload failed');
		const hintKey =
			getEmbeddingHintKey(diagnostic.code, options.isAdmin ?? false) ??
			getEmbeddingServiceHintKey(diagnostic.code, options.isAdmin ?? false) ??
			keys?.hint;
		const hint = hintKey ? t(hintKey) : diagnostic.hint ?? '';

		return {
			code: diagnostic.code,
			title,
			message,
			hint,
			blocking: diagnostic.blocking ?? true
		};
	}

	const message = getErrorText(error).trim();
	if (!message) {
		return {
			code: 'unknown',
			title: t('Upload failed'),
			message: '',
			hint: '',
			blocking: true
		};
	}

	const encodingMatch = message.match(ENCODING_DETECTION_RE);
	if (encodingMatch) {
		return {
			code: 'unsupported_text_encoding',
			title: t('Encoding could not be detected'),
			message: t(
				'Could not detect the file encoding for "{{name}}". This file may be an archive, a binary file, or use an unsupported encoding. The file was uploaded, but its content may not be available for retrieval.',
				{
					name: getDisplayFileName(encodingMatch[1])
				}
			),
			hint: t('Upload a plain text or supported document file instead.'),
			blocking: true
		};
	}

	return {
		code: 'fallback',
		title: t('Upload failed'),
		message: localizeCommonError(message, t),
		hint: '',
		blocking: true
	};
};

export const localizeFileUploadError = (
	error: unknown,
	t: Translate,
	options: LocalizeOptions = {}
): string => {
	const localized = getLocalizedFileUploadDiagnostic(error, t, options);
	return localized.hint ? `${localized.message}\n${localized.hint}` : localized.message;
};

export const isFailedUploadFile = (file: any): boolean =>
	Boolean(file && file.type !== 'image' && file.status === 'failed');

export const buildIgnoredFailedFilesMessage = (
	files: Array<{ name?: string }>,
	t: Translate
): string => {
	const names = files
		.map((file) => file?.name?.trim())
		.filter(Boolean)
		.slice(0, 3)
		.join(', ');

	return t('Ignored failed file(s) for this message: {{names}}', {
		names: names || t('Unknown file')
	});
};
