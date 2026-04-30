import { EXTERNAL_API_ADMIN_BASE_URL } from '$lib/constants';
import { parseJsonResponse } from '../response';

export const getExternalApiGatewayConfig = async (token: string) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return null;
		});
	if (error) throw error;
	return res;
};

export const updateExternalApiGatewayConfig = async (token: string, body: object) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/config`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(body)
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return null;
		});
	if (error) throw error;
	return res;
};

export const getExternalApiClients = async (token: string) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/clients`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return [];
		});
	if (error) throw error;
	return res ?? [];
};

export const createExternalApiClient = async (token: string, body: object) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/clients`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(body)
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return null;
		});
	if (error) throw error;
	return res;
};

export const updateExternalApiClient = async (token: string, clientId: string, body: object) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/clients/${clientId}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(body)
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return null;
		});
	if (error) throw error;
	return res;
};

export const deleteExternalApiClient = async (token: string, clientId: string) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/clients/${clientId}`, {
		method: 'DELETE',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return null;
		});
	if (error) throw error;
	return res;
};

export const getExternalApiClientLogs = async (token: string, clientId: string, limit = 100) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/clients/${clientId}/logs?limit=${limit}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return [];
		});
	if (error) throw error;
	return res ?? [];
};

export const getExternalApiLogs = async (token: string, limit = 100) => {
	let error = null;
	const res = await fetch(`${EXTERNAL_API_ADMIN_BASE_URL}/logs?limit=${limit}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(parseJsonResponse)
		.catch((err) => {
			error = err.detail;
			return [];
		});
	if (error) throw error;
	return res ?? [];
};
