type ModelLike = {
	id?: string;
	name?: string;
	owned_by?: string;
	anthropic?: Record<string, unknown>;
};

export type AnthropicThinkingProfile = {
	isAnthropic: boolean;
	supportsThinking: boolean;
	supportsDisplay: boolean;
	supportsEffort: boolean;
	prefersAdaptive: boolean;
	maxOutputCap: number | null;
};

const FAMILY_FIRST_RE =
	/(?:^|[^a-z0-9])(?:claude[-_/ ]+)?(?<family>opus|sonnet|haiku)(?:[-_/ ]+(?<major>\d))(?:[-_. /]*(?<minor>\d))?/i;
const VERSION_FIRST_RE =
	/(?:^|[^a-z0-9])claude(?:[-_/ ]+(?<major>\d))(?:[-_. /]*(?<minor>\d))?(?:[-_/ ]+(?<family>opus|sonnet|haiku))/i;

const normalizeModelText = (value: unknown) =>
	String(value ?? '')
		.trim()
		.toLowerCase()
		.replace(/_/g, '-')
		.replace(/(?<=\d)\.(?=\d)/g, '-')
		.replace(/\s+/g, '-');

const toPositiveInt = (value: unknown): number | null => {
	const parsed = Number(value);
	return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : null;
};

const parseSignature = (text: string) => {
	const normalized = normalizeModelText(text);
	const isMithos = normalized.includes('mythos');
	const match = FAMILY_FIRST_RE.exec(normalized) ?? VERSION_FIRST_RE.exec(normalized);

	if (!match?.groups) {
		return {
			family: null as string | null,
			major: null as number | null,
			minor: null as number | null,
			isMithos
		};
	}

	return {
		family: match.groups.family?.toLowerCase() ?? null,
		major: toPositiveInt(match.groups.major),
		minor: toPositiveInt(match.groups.minor),
		isMithos
	};
};

const extractMetaCap = (model: ModelLike | null | undefined): number | null => {
	if (!model || typeof model !== 'object') {
		return null;
	}

	const anthropic = model.anthropic;
	if (!anthropic || typeof anthropic !== 'object') {
		return null;
	}
	const capabilities = anthropic['capabilities'] as Record<string, unknown> | undefined;

	const candidates = [
		anthropic.max_tokens,
		anthropic.max_output_tokens,
		anthropic.output_token_limit,
		capabilities?.max_tokens,
		capabilities?.max_output_tokens,
		capabilities?.output_token_limit
	];

	for (const candidate of candidates) {
		const parsed = toPositiveInt(candidate);
		if (parsed) {
			return parsed;
		}
	}

	return null;
};

export const getAnthropicThinkingProfile = (
	model: ModelLike | null | undefined
): AnthropicThinkingProfile => {
	const id = model?.id ?? model?.name ?? '';
	const owner = String(model?.owned_by ?? '').toLowerCase();
	const normalizedId = normalizeModelText(id);
	const looksAnthropic =
		owner === 'anthropic' ||
		owner === 'claude' ||
		normalizedId.includes('claude') ||
		normalizedId.includes('mythos');

	if (!looksAnthropic) {
		return {
			isAnthropic: false,
			supportsThinking: false,
			supportsDisplay: false,
			supportsEffort: false,
			prefersAdaptive: false,
			maxOutputCap: null
		};
	}

	const { family, major, minor, isMithos } = parseSignature(id);
	let supportsDisplay = false;
	let supportsEffort = false;
	let prefersAdaptive = false;
	let supportsThinking = false;

	if (isMithos) {
		supportsThinking = true;
		supportsDisplay = true;
		supportsEffort = true;
		prefersAdaptive = true;
	} else if (family && major) {
		supportsThinking = true;
		if ((family === 'sonnet' || family === 'opus') && major === 4 && minor === 6) {
			supportsDisplay = true;
			supportsEffort = true;
			prefersAdaptive = true;
		} else if (major === 4) {
			supportsDisplay = true;
		}
	}

	let maxOutputCap = extractMetaCap(model);
	if (!maxOutputCap) {
		if (family === 'opus' && major === 4 && minor === 6) {
			maxOutputCap = 128000;
		} else if (supportsThinking || isMithos) {
			maxOutputCap = 64000;
		}
	}

	return {
		isAnthropic: true,
		supportsThinking,
		supportsDisplay,
		supportsEffort,
		prefersAdaptive,
		maxOutputCap
	};
};

export const getAnthropicEffortSteps = (model: ModelLike | null | undefined) => {
	const profile = getAnthropicThinkingProfile(model);
	if (!profile.isAnthropic) {
		return null;
	}

	return [
		{ value: 'none', label: '关闭' },
		{ value: null, label: '默认' },
		{ value: 'low', label: 'Low' },
		{ value: 'medium', label: 'Medium' },
		{ value: 'high', label: 'High' },
		{ value: 'max', label: 'Max' }
	];
};

const formatTokenLabel = (value: number) => {
	if (value === 64000) {
		return '64K';
	}
	if (value === 128000) {
		return '128K';
	}

	const rounded = Math.max(1, Math.round(value / 1024));
	return `${rounded}K`;
};

export const getAnthropicBudgetSteps = (model: ModelLike | null | undefined) => {
	const profile = getAnthropicThinkingProfile(model);
	if (!profile.isAnthropic) {
		return null;
	}

	const cap = profile.maxOutputCap ?? 64000;
	const presets = [2048, 8192, 16384, 32768];
	const finalPreset = cap >= 65536 ? 65536 : cap > 32768 ? cap : null;
	const numericSteps = [...presets, finalPreset]
		.filter((value): value is number => typeof value === 'number' && value > 0 && value <= cap)
		.filter((value, index, array) => array.indexOf(value) === index);

	return [
		{ value: 0, label: '关闭' },
		{ value: null, label: '默认' },
		...numericSteps.map((value) => ({
			value,
			label: formatTokenLabel(value)
		}))
	];
};
