import { OPENAI_API_BASE_URL, WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';

const OPENAI_CHAT_COMPLETIONS_SUFFIX = '/chat/completions';

type OpenAIConnectionConfig = Record<string, any> & {
	force_mode?: boolean;
};

type OpenAIVerifyPurpose = 'connection' | 'models';

const isForceModeConnection = (
	url: string,
	config?: OpenAIConnectionConfig
) => {
	const normalizedUrl = (url || '').trim().replace(/\/+$/, '');
	return Boolean(config?.force_mode) || normalizedUrl.endsWith(OPENAI_CHAT_COMPLETIONS_SUFFIX);
};

const isDashScopeCompatibleConnection = (url: string) => {
	try {
		const parsed = new URL(url.trim());
		const host = parsed.hostname.toLowerCase();
		const path = parsed.pathname.replace(/\/+$/, '');

		if (host === 'coding.dashscope.aliyuncs.com') {
			return path === '/v1';
		}

		return (
			(host === 'dashscope.aliyuncs.com' ||
				(host.startsWith('dashscope-') && host.endsWith('.aliyuncs.com'))) &&
			path === '/compatible-mode/v1'
		);
	} catch {
		return false;
	}
};

const looksLikeModelsEndpointUnsupported = (status: number, body: unknown) => {
	if (status !== 404 && status !== 405) return false;

	const text =
		typeof body === 'string'
			? body.trim().toLowerCase()
			: JSON.stringify(body ?? '').toLowerCase().trim();

	if (!text) return true;
	if (text.startsWith('<!doctype html') || text.startsWith('<html')) return true;

	return (
		text.includes('endpoint not found') ||
		text.includes('route not found') ||
		text.includes('resource not found') ||
		text.includes('404 page not found') ||
		(text.includes('models') &&
			['not found', 'unsupported', 'not support', 'unknown', 'no route'].some((term) =>
				text.includes(term)
			))
	);
};

const buildDashScopeVerifyFallback = (purpose: OpenAIVerifyPurpose) =>
	purpose === 'models'
		? {
				object: 'list',
				data: [],
				_openwebui: {
					manual_model_ids_required: true,
					reason: 'models_endpoint_unsupported',
					provider: 'dashscope'
				}
			}
		: {
				ok: true,
				_openwebui: {
					verification_succeeded: true,
					models_endpoint_supported: false,
					provider: 'dashscope'
				}
			};

const getOpenAIModelsEndpoint = (
	url: string,
	config?: OpenAIConnectionConfig
) => {
	const normalizedUrl = (url || '').trim().replace(/\/+$/, '');
	if (!normalizedUrl) return '';

	if (isForceModeConnection(normalizedUrl, config)) {
		if (normalizedUrl.endsWith(OPENAI_CHAT_COMPLETIONS_SUFFIX)) {
			return `${normalizedUrl.slice(0, -OPENAI_CHAT_COMPLETIONS_SUFFIX.length)}/models`;
		}
		return normalizedUrl;
	}

	return `${normalizedUrl}/models`;
};

export const getOpenAIConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type OpenAIConfig = {
	ENABLE_OPENAI_API: boolean;
	OPENAI_API_BASE_URLS: string[];
	OPENAI_API_KEYS: string[];
	OPENAI_API_CONFIGS: object;
};

export const updateOpenAIConfig = async (token: string = '', config: OpenAIConfig) => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOpenAIUrls = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/urls`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res.OPENAI_API_BASE_URLS;
};

export const updateOpenAIUrls = async (token: string = '', urls: string[]) => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/urls/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			urls: urls
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res.OPENAI_API_BASE_URLS;
};

export const getOpenAIKeys = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/keys`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res.OPENAI_API_KEYS;
};

export const updateOpenAIKeys = async (token: string = '', keys: string[]) => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/keys/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			keys: keys
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res.OPENAI_API_KEYS;
};

export const getOpenAIModelsDirect = async (
	url: string,
	key: string,
	config?: OpenAIConnectionConfig
) => {
	let error = null;

	const res = await fetch(getOpenAIModelsEndpoint(url, config), {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(key && { authorization: `Bearer ${key}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `OpenAI: ${err?.detail ?? err?.error?.message ?? err?.message ?? 'Network Problem'}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOpenAIModels = async (token: string, urlIdx?: number) => {
	let error = null;

	const res = await fetch(
		`${OPENAI_API_BASE_URL}/models${typeof urlIdx === 'number' ? `/${urlIdx}` : ''}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				...(token && { authorization: `Bearer ${token}` })
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const verifyOpenAIConnection = async (
	token: string = '',
	urlOrConnection:
		| string
		| {
				url: string;
				key: string;
				config?: OpenAIConnectionConfig;
				purpose?: OpenAIVerifyPurpose;
		  } = 'https://api.openai.com/v1',
	keyOrDirect: string | boolean = '',
	direct: boolean = false
) => {
	const url = typeof urlOrConnection === 'string' ? urlOrConnection : urlOrConnection.url;
	const key =
		typeof urlOrConnection === 'string'
			? typeof keyOrDirect === 'string'
				? keyOrDirect
				: ''
			: urlOrConnection.key;
	const config = typeof urlOrConnection === 'string' ? undefined : urlOrConnection.config;
	const purpose =
		typeof urlOrConnection === 'string' ? 'connection' : (urlOrConnection.purpose ?? 'connection');
	const isDirect = typeof keyOrDirect === 'boolean' ? keyOrDirect : direct;

	if (!url) {
		throw 'OpenAI: URL is required';
	}

	let error = null;
	let res = null;

	if (isDirect) {
		res = await fetch(getOpenAIModelsEndpoint(url, config), {
			method: 'GET',
			headers: {
				Accept: 'application/json',
				Authorization: `Bearer ${key}`,
				'Content-Type': 'application/json'
			}
		})
			.then(async (res) => {
				if (!res.ok) {
					let upstreamError: any = null;
					try {
						upstreamError = await res.json();
					} catch {
						try {
							upstreamError = await res.text();
						} catch {
							upstreamError = null;
						}
					}

					if (
						isDashScopeCompatibleConnection(url) &&
						looksLikeModelsEndpointUnsupported(res.status, upstreamError)
					) {
						return buildDashScopeVerifyFallback(purpose);
					}

					throw upstreamError;
				}
				return res.json();
			})
			.catch((err) => {
				error = `OpenAI: ${err?.detail ?? err?.error?.message ?? err?.message ?? 'Network Problem'}`;
				return [];
			});

		if (error) {
			throw error;
		}
	} else {
		res = await fetch(`${OPENAI_API_BASE_URL}/verify`, {
			method: 'POST',
			headers: {
				Accept: 'application/json',
				Authorization: `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				url,
				key,
				config,
				purpose
			})
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				error = `OpenAI: ${err?.detail ?? err?.error?.message ?? err?.message ?? 'Network Problem'}`;
				return [];
			});

		if (error) {
			throw error;
		}
	}

	return res;
};

export const chatCompletion = async (
	token: string = '',
	body: object,
	url: string = `${WEBUI_BASE_URL}/api`
): Promise<[Response | null, AbortController]> => {
	const controller = new AbortController();
	let error = null;

	const res = await fetch(`${url}/chat/completions`, {
		signal: controller.signal,
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
	}).catch((err) => {
		console.log(err);
		error = err;
		return null;
	});

	if (error) {
		throw error;
	}

	return [res, controller];
};

export const generateOpenAIChatCompletion = async (
	token: string = '',
	body: object,
	url: string = `${WEBUI_BASE_URL}/api`
) => {
	let error = null;

	const res = await fetch(`${url}/chat/completions`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `${err?.detail ?? err}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const synthesizeOpenAISpeech = async (
	token: string = '',
	speaker: string = 'alloy',
	text: string = '',
	model: string = 'tts-1'
) => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/audio/speech`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			model: model,
			input: text,
			voice: speaker
		})
	}).catch((err) => {
		console.log(err);
		error = err;
		return null;
	});

	if (error) {
		throw error;
	}

	return res;
};
