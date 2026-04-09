import { ANTHROPIC_API_BASE_URL } from '$lib/constants';
import { parseJsonResponse } from '../response';

export const getAnthropicConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${ANTHROPIC_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			console.error(err);
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

type AnthropicConfig = {
	ENABLE_ANTHROPIC_API: boolean;
	ANTHROPIC_API_BASE_URLS: string[];
	ANTHROPIC_API_KEYS: string[];
	ANTHROPIC_API_CONFIGS: object;
};

export const updateAnthropicConfig = async (token: string = '', config: AnthropicConfig) => {
	let error = null;

	const res = await fetch(`${ANTHROPIC_API_BASE_URL}/config/update`, {
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
		.then(parseJsonResponse)
		.catch((err) => {
			console.error(err);
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

export const verifyAnthropicConnection = async (
	token: string = '',
	connection: { url: string; key: string; config?: object }
) => {
	const { url, key, config } = connection;
	if (!url) {
		throw 'Anthropic: URL is required';
	}

	let error = null;

	const res = await fetch(`${ANTHROPIC_API_BASE_URL}/verify`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			url,
			key,
			config
		})
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = `Anthropic: ${err?.detail ?? err?.error?.message ?? err?.message ?? 'Network Problem'}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const healthCheckAnthropicConnection = async (
	token: string = '',
	connection: { url: string; key: string; config?: object; model?: string }
) => {
	const { url, key, config, model } = connection;
	if (!url) {
		throw 'Anthropic: URL is required';
	}

	let error = null;

	const res = await fetch(`${ANTHROPIC_API_BASE_URL}/health_check`, {
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
			model
		})
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = `Anthropic: ${err?.detail ?? err?.error?.message ?? err?.message ?? 'Network Problem'}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
