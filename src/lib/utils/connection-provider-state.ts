type ProviderStateInput = {
	currentAzure: boolean;
	url?: string;
	direct?: boolean;
	gemini?: boolean;
	grok?: boolean;
	anthropic?: boolean;
	ollama?: boolean;
	edit?: boolean;
	savedAzure?: boolean;
	providerTypeTouched?: boolean;
};

export const looksLikeAzureOpenAIUrl = (url: string = '') => {
	const trimmed = url.trim().replace(/#$/, '');
	if (!trimmed) return false;

	const candidates = trimmed.match(/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//)
		? [trimmed]
		: [`https://${trimmed}`];

	for (const candidate of candidates) {
		try {
			const host = new URL(candidate).hostname.toLowerCase();
			return (
				host.endsWith('.openai.azure.com') ||
				host.endsWith('.cognitiveservices.azure.com') ||
				host.endsWith('.cognitive.microsoft.com')
			);
		} catch {
			continue;
		}
	}

	return false;
};

export const resolveAzureProviderType = ({
	currentAzure,
	url = '',
	direct = false,
	gemini = false,
	grok = false,
	anthropic = false,
	ollama = false,
	edit = false,
	savedAzure = false,
	providerTypeTouched = false
}: ProviderStateInput) => {
	if (direct || gemini || grok || anthropic || ollama) {
		return false;
	}

	if (providerTypeTouched) {
		return currentAzure;
	}

	if (edit) {
		return savedAzure;
	}

	return looksLikeAzureOpenAIUrl(url);
};
