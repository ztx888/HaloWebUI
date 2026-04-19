import { describe, expect, it } from 'vitest';

import {
	buildIgnoredFailedFilesMessage,
	getLocalizedFileUploadDiagnostic,
	localizeFileUploadError
} from './file-upload-errors';

const t = (key: string, options?: Record<string, unknown>) =>
	key.replace(/\{\{(\w+)\}\}/g, (_, token) => String(options?.[token] ?? ''));

describe('file upload errors', () => {
	it('localizes archive diagnostics', () => {
		const localized = getLocalizedFileUploadDiagnostic(
			{
				diagnostic: {
					code: 'unsupported_archive',
					title: 'ignored',
					message: 'ignored',
					hint: 'ignored',
					blocking: true
				}
			},
			t
		);

		expect(localized.title).toBe('Archive not supported');
		expect(localized.message).toContain('Compressed archives cannot be uploaded directly');
		expect(localized.hint).toContain('Extract the archive');
	});

	it('uses role-specific hints for embedding configuration failures', () => {
		const admin = getLocalizedFileUploadDiagnostic(
			{
				diagnostic: {
					code: 'embedding_unavailable',
					title: '',
					message: '',
					hint: '',
					blocking: true
				}
			},
			t,
			{ isAdmin: true }
		);
		const member = getLocalizedFileUploadDiagnostic(
			{
				diagnostic: {
					code: 'embedding_unavailable',
					title: '',
					message: '',
					hint: '',
					blocking: true
				}
			},
			t,
			{ isAdmin: false }
		);

		expect(admin.hint).toContain('/settings/documents');
		expect(member.hint).toContain('administrator');
	});

	it('localizes missing chardet diagnostics', () => {
		const message = localizeFileUploadError(
			{
				diagnostic: {
					code: 'missing_text_encoding_dependency',
					title: '',
					message: '',
					hint: '',
					blocking: true
				}
			},
			t
		);

		expect(message).toContain('text encoding detection dependency');
		expect(message).toContain('administrator');
	});

	it('falls back to encoding detection localization for legacy string errors', () => {
		const message = localizeFileUploadError('Could not detect encoding for demo.rar', t);

		expect(message).toContain('demo.rar');
		expect(message).toContain('unsupported encoding');
	});

	it('builds a single warning message for ignored failed files', () => {
		const message = buildIgnoredFailedFilesMessage(
			[{ name: 'a.rar' }, { name: 'b.html' }],
			t
		);

		expect(message).toBe('Ignored failed file(s) for this message: a.rar, b.html');
	});

	describe('embedding_chunk_too_large diagnostic', () => {
		const buildChunkTooLargeError = () => ({
			diagnostic: {
				code: 'embedding_chunk_too_large',
				title: '',
				message: '',
				hint: '',
				blocking: true
			}
		});

		it('returns the specific title and message keys via getDiagnosticKeys', () => {
			const localized = getLocalizedFileUploadDiagnostic(buildChunkTooLargeError(), t);

			expect(localized.title).toBe('Chunk exceeds embedding model limit');
			expect(localized.message).toContain("input token limit");
		});

		it('returns an admin-specific hint pointing at Chunk Size', () => {
			const localized = getLocalizedFileUploadDiagnostic(buildChunkTooLargeError(), t, {
				isAdmin: true
			});

			expect(localized.hint).toContain('Chunk Size');
			expect(localized.hint).toContain('Documents');
		});

		it('returns a non-admin hint asking to contact an administrator', () => {
			const localized = getLocalizedFileUploadDiagnostic(buildChunkTooLargeError(), t, {
				isAdmin: false
			});

			expect(localized.hint.toLowerCase()).toContain('administrator');
			expect(localized.hint).not.toContain('Chunk Size');
		});

		it('is NOT captured by the getEmbeddingHintKey whitelist (regression)', () => {
			// If the new code were mistakenly added to getEmbeddingHintKey's whitelist,
			// the admin hint would become the generic "configure an embedding model"
			// message rather than the Chunk Size guidance.
			const localized = getLocalizedFileUploadDiagnostic(buildChunkTooLargeError(), t, {
				isAdmin: true
			});

			expect(localized.hint).not.toContain('configure an embedding model');
			expect(localized.hint).not.toContain('Full Context');
		});
	});
});
