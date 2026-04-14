const escapeHtml = (value: string) =>
	value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');

const splitMentionId = (id: string) => {
	const [baseId, ...labelParts] = String(id ?? '').split('|');
	return {
		id: baseId,
		embeddedLabel: labelParts.join('|')
	};
};

export const getMentionDisplayLabel = (id: string, label = '') => {
	if (label) {
		return label;
	}

	const { embeddedLabel } = splitMentionId(id);
	return embeddedLabel || id;
};

export const serializeMentionTag = (char: string, id: string, label = '') => {
	const normalizedChar = char || '@';
	const normalizedId = String(id ?? '');
	const normalizedLabel = String(label ?? '');

	if (!normalizedId) {
		return '';
	}

	if (normalizedId.includes('|')) {
		return `<${normalizedChar}${normalizedId}>`;
	}

	if (normalizedLabel && normalizedLabel !== normalizedId) {
		return `<${normalizedChar}${normalizedId}|${normalizedLabel}>`;
	}

	return `<${normalizedChar}${normalizedId}>`;
};

export const buildMentionSpan = (char: string, id: string, label = '') => {
	const normalizedChar = char || '@';
	const { id: normalizedId } = splitMentionId(id);
	const displayLabel = getMentionDisplayLabel(id, label);

	if (!normalizedId) {
		return '';
	}

	return `<span class="mention" data-type="mention" data-id="${escapeHtml(
		normalizedId
	)}" data-label="${escapeHtml(displayLabel)}" data-mention-suggestion-char="${escapeHtml(
		normalizedChar
	)}">${escapeHtml(`${normalizedChar}${displayLabel}`)}</span>`;
};

export const hydrateMentionTagsInHtml = (htmlContent: string) =>
	String(htmlContent ?? '').replace(
		/(?:&lt;|<)([@#$])([\w.\-:/]+(?:\|[^&>|]+)?)(?:\|([^&>]*?))?(?:&gt;|>)/g,
		(_, char, id, label) => buildMentionSpan(char, id, label)
	);
