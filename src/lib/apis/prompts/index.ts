import { WEBUI_API_BASE_URL } from '$lib/constants';
import { parseJsonResponse } from '../response';

type PromptItem = {
	command: string;
	name: string;
	content: string;
	data?: object | null;
	meta?: object | null;
	tags?: string[] | null;
	is_active?: boolean | null;
	access_control?: null | object;
	commit_message?: string | null;
};

type PromptListResponse = {
	items: any[];
	total: number;
};

const normalizePromptCommand = (command: string) =>
	(command || '').startsWith('/') ? command.slice(1) : command;

const normalizePromptItem = (item) =>
	item
		? {
				...item,
				command: normalizePromptCommand(item.command)
			}
		: item;

const normalizePromptListResponse = (response: PromptListResponse): PromptListResponse => ({
	...response,
	items: (response?.items ?? []).map((item) => normalizePromptItem(item))
});

// ────────────────────────────────
// Create
// ────────────────────────────────

export const createNewPrompt = async (token: string, prompt: PromptItem) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/create`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...prompt,
			command: normalizePromptCommand(prompt.command)
		})
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Read (unpaginated, for stores)
// ────────────────────────────────

export const getPrompts = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return (res ?? []).map((item) => normalizePromptItem(item));
};

// ────────────────────────────────
// Read (paginated, for workspace list)
// ────────────────────────────────

export const getPromptList = async (
	token: string = '',
	page: number = 1,
	limit: number = 30,
	orderBy: string = 'updated_at'
): Promise<PromptListResponse> => {
	let error = null;

	const params = new URLSearchParams({
		page: String(page),
		limit: String(limit),
		order_by: orderBy
	});

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/list?${params}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return { items: [], total: 0 };
		});

	if (error) {
		throw error;
	}

	return normalizePromptListResponse(res);
};

// ────────────────────────────────
// Read by ID
// ────────────────────────────────

export const getPromptById = async (token: string, promptId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/id/${promptId}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Read by command (legacy compat)
// ────────────────────────────────

export const getPromptByCommand = async (token: string, command: string) => {
	let error = null;

	const normalizedCommand = normalizePromptCommand(command);

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/command/${normalizedCommand}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Update by ID
// ────────────────────────────────

export const updatePromptById = async (token: string, promptId: string, prompt: PromptItem) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/id/${promptId}/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...prompt,
			command: normalizePromptCommand(prompt.command)
		})
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Update by command (legacy compat)
// ────────────────────────────────

export const updatePromptByCommand = async (token: string, prompt: PromptItem) => {
	let error = null;

	const command = normalizePromptCommand(prompt.command);

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/command/${command}/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...prompt,
			command
		})
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Update meta/tags only
// ────────────────────────────────

export const updatePromptMeta = async (
	token: string,
	promptId: string,
	meta: object | null,
	tags: string[] | null
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/id/${promptId}/meta`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ meta, tags })
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Toggle by ID
// ────────────────────────────────

export const togglePromptById = async (token: string, promptId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/id/${promptId}/toggle`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Toggle by command (legacy compat)
// ────────────────────────────────

export const togglePromptByCommand = async (token: string, command: string) => {
	let error = null;

	command = normalizePromptCommand(command);

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/command/${command}/toggle`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return normalizePromptItem(res);
};

// ────────────────────────────────
// Delete by ID
// ────────────────────────────────

export const deletePromptById = async (token: string, promptId: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/id/${promptId}/delete`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// ────────────────────────────────
// Delete by command (legacy compat)
// ────────────────────────────────

export const deletePromptByCommand = async (token: string, command: string) => {
	let error = null;

	command = command.charAt(0) === '/' ? command.slice(1) : command;

	const res = await fetch(`${WEBUI_API_BASE_URL}/prompts/command/${command}/delete`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
