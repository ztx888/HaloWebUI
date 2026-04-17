import { describe, expect, it } from 'vitest';

import { resolveAzureProviderType } from './connection-provider-state';

describe('connection provider state', () => {
	it('keeps the saved OpenAI provider when editing an Azure-looking URL', () => {
		expect(
			resolveAzureProviderType({
				currentAzure: false,
				url: 'https://example-resource.openai.azure.com/openai/v1',
				edit: true,
				savedAzure: false
			})
		).toBe(false);
	});

	it('auto-detects Azure for new Azure-looking connections', () => {
		expect(
			resolveAzureProviderType({
				currentAzure: false,
				url: 'https://example-resource.openai.azure.com/openai/v1'
			})
		).toBe(true);
	});

	it('respects a manual provider toggle for the current dialog session', () => {
		expect(
			resolveAzureProviderType({
				currentAzure: false,
				url: 'https://example-resource.openai.azure.com/openai/v1',
				providerTypeTouched: true
			})
		).toBe(false);
	});
});
