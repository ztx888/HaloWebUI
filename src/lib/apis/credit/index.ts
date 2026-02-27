import { WEBUI_API_BASE_URL } from '$lib/constants';

export const getCreditConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const createTradeTicket = async (token: string, payType: string, amount: number) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/tickets`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			pay_type: payType,
			amount: amount
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const listCreditLog = async (token: string, page: number) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/logs?page=${page}`, {
		method: 'GET',
		headers: {
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const listAllCreditLog = async (
	token: string,
	page: number,
	limit: number,
	query: string
) => {
	let error = null;

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/credit/all_logs?page=${page}&limit=${limit}&query=${query}`,
		{
			method: 'GET',
			headers: {
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getCreditStats = async (token: string, data: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/statistics`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(data)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteCreditLogs = async (token: string, timestamp: number) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/logs`, {
		method: 'DELETE',
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ timestamp })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// redemption codes api functions

export const getRedemptionCodes = async (
	token: string,
	page: number = 1,
	limit: number = 30,
	keyword: string = ''
) => {
	let error = null;

	const params = new URLSearchParams({
		page: page.toString(),
		limit: limit.toString()
	});

	if (keyword) {
		params.append('keyword', keyword);
	}

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/redemption_codes?${params}`, {
		method: 'GET',
		headers: {
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const createRedemptionCodes = async (
	token: string,
	purpose: string,
	count: number,
	amount: number,
	expiredAt?: number
) => {
	let error = null;

	const body: any = {
		purpose,
		count,
		amount
	};

	if (expiredAt) {
		body.expired_at = expiredAt;
	}

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/redemption_codes`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateRedemptionCode = async (
	token: string,
	code: string,
	purpose?: string,
	amount?: number,
	expiredAt?: number
) => {
	let error = null;

	const body: any = {};

	if (purpose !== undefined) {
		body.purpose = purpose;
	}
	if (amount !== undefined) {
		body.amount = amount;
	}
	if (expiredAt !== undefined) {
		body.expired_at = expiredAt;
	}

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/redemption_codes/${code}`, {
		method: 'PUT',
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: 'application/json',
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteRedemptionCode = async (token: string, code: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/redemption_codes/${code}`, {
		method: 'DELETE',
		headers: {
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const exportRedemptionCodes = async (token: string, keyword: string) => {
	let error = null;

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/credit/redemption_codes/export?keyword=${encodeURIComponent(keyword)}`,
		{
			method: 'GET',
			headers: {
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res;
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const receiveRedemptionCode = async (token: string, code: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/credit/redemption_codes/${code}/receive`, {
		method: 'GET',
		headers: {
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
