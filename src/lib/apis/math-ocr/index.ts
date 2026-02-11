import { WEBUI_API_BASE_URL } from '$lib/constants';

type MathOCRConvertBody = {
	image_base64: string;
	model?: string;
	prompt?: string;
};

export const convertMathOCR = async (token: string, body: MathOCRConvertBody) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/math-ocr/convert`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify(body)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = err;
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
