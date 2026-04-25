import { IMAGES_API_BASE_URL } from '$lib/constants';
import { parseJsonResponse } from '../response';

export type ImageGenerationRequest = {
	prompt: string;
	model?: string;
	model_ref?: Record<string, unknown>;
	size?: string;
	image_size?: '512' | '1K' | '2K' | '4K' | string;
	aspect_ratio?: string;
	resolution?: string;
	n?: number;
	negative_prompt?: string;
	credential_source?: 'auto' | 'personal' | 'shared';
	connection_index?: number;
	steps?: number;
	background?: string;
};

export type ImageUsageConfig = {
	enabled: boolean;
	shared_key: {
		enabled: boolean;
		available: boolean;
		providers?: Record<string, boolean>;
	};
	personal_key: {
		supported: boolean;
		providers?: string[];
	};
};

export type ImageGenerationConfig = {
	MODEL?: string;
	IMAGE_SIZE?: string;
	IMAGE_ASPECT_RATIO?: string;
	IMAGE_RESOLUTION?: string;
	IMAGE_STEPS?: number;
	IMAGE_MODEL_FILTER_REGEX?: string | null;
};

export type ImageGenerationModel = {
	id: string;
	name?: string;
	selection_key?: string;
	legacy_id?: string | null;
	model_ref?: Record<string, unknown> | null;
	provider?: 'openai' | 'gemini' | 'grok' | string | null;
	generation_mode?: string;
	detection_method?: string;
	supports_background?: boolean;
	supports_batch?: boolean;
	size_mode?: 'exact' | 'aspect_ratio' | 'unsupported' | string;
	supports_image_size?: boolean;
	supports_resolution?: boolean;
	text_output_supported?: boolean;
	source?: 'settings' | 'personal' | 'shared' | string | null;
	connection_index?: number | null;
	connection_name?: string | null;
	connection_icon?: string | null;
};

export const getConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(parseJsonResponse)
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

export const getImageUsageConfig = async (token: string = ''): Promise<ImageUsageConfig> => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/usage/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(parseJsonResponse)
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

	return res as ImageUsageConfig;
};

export const updateConfig = async (token: string = '', config: object) => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/config/update`, {
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

export const verifyConfigUrl = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/config/url/verify`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(parseJsonResponse)
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

export const getImageGenerationConfig = async (
	token: string = ''
): Promise<ImageGenerationConfig> => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/image/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(parseJsonResponse)
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

	return res as ImageGenerationConfig;
};

export const updateImageGenerationConfig = async (
	token: string = '',
	config: ImageGenerationConfig
): Promise<ImageGenerationConfig> => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/image/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({ ...config })
	})
		.then(parseJsonResponse)
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

	return res as ImageGenerationConfig;
};

export const getImageGenerationModels = async (
	token: string = '',
	params: {
		context?: 'settings' | 'runtime' | string;
		credentialSource?: 'auto' | 'personal' | 'shared' | string;
		connectionIndex?: number | null;
	} = {}
): Promise<ImageGenerationModel[]> => {
	let error = null;
	const query = new URLSearchParams();

	if (params.context) {
		query.set('context', `${params.context}`);
	}
	if (params.credentialSource) {
		query.set('credential_source', `${params.credentialSource}`);
	}
	if (Number.isInteger(params.connectionIndex)) {
		query.set('connection_index', `${params.connectionIndex}`);
	}

	const suffix = query.toString() ? `?${query.toString()}` : '';

	const res = await fetch(`${IMAGES_API_BASE_URL}/models${suffix}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(parseJsonResponse)
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

	return (res ?? []) as ImageGenerationModel[];
};

export const imageGenerations = async (
	token: string = '',
	request: string | ImageGenerationRequest
) => {
	let error = null;

	const payload =
		typeof request === 'string'
			? {
					prompt: request
				}
			: request;

	const res = await fetch(`${IMAGES_API_BASE_URL}/generations`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify(payload)
	})
		.then(parseJsonResponse)
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
