type AnyModel = {
	id?: string;
	selection_id?: string;
	selectionId?: string;
	selection_key?: string;
	selectionKey?: string;
	model_id?: string;
	original_id?: string;
	originalId?: string;
	legacy_id?: string | null;
	legacyId?: string | null;
	legacy_ids?: string[];
	legacyIds?: string[];
	model_ref?: Record<string, unknown>;
	[key: string]: any;
};

const normalize = (value: unknown) =>
	typeof value === 'string' || typeof value === 'number' ? `${value}`.trim() : '';

export const getModelSelectionId = (model?: AnyModel | null): string =>
	normalize(
		model?.selection_id ??
			model?.selectionId ??
			model?.selection_key ??
			model?.selectionKey ??
			model?.id
	);

export const getModelCleanId = (model?: AnyModel | null): string =>
	normalize(model?.model_id ?? model?.original_id ?? model?.originalId ?? model?.id);

export const getModelRef = (model?: AnyModel | null): Record<string, unknown> | null => {
	const ref = model?.model_ref;
	return ref && typeof ref === 'object' ? ref : null;
};

export const parseModelSelectionId = (
	value?: string | null
): { provider: string; source: string; modelId: string; modelRef: Record<string, unknown> } | null => {
	const raw = normalize(value);
	if (!raw) return null;

	const parts = raw.split('::');
	if (parts.length !== 5 || parts[0] !== 'modelref') return null;

	try {
		const provider = decodeURIComponent(parts[1]).toLowerCase();
		const source = decodeURIComponent(parts[2]) || 'personal';
		const connectionToken = parts[3];
		const modelId = decodeURIComponent(parts[4]);
		if (!provider || !modelId) return null;

		const modelRef: Record<string, unknown> = { provider, source };
		if (connectionToken.startsWith('id:')) {
			modelRef.connection_id = decodeURIComponent(connectionToken.slice(3));
		} else if (connectionToken.startsWith('idx:')) {
			const rawIndex = decodeURIComponent(connectionToken.slice(4));
			const numericIndex = Number(rawIndex);
			modelRef.connection_index = Number.isInteger(numericIndex) ? numericIndex : rawIndex;
		} else if (connectionToken !== 'none') {
			return null;
		}

		return { provider, source, modelId, modelRef };
	} catch {
		return null;
	}
};

const cleanRefValue = (ref: Record<string, unknown> | null | undefined, key: string): string =>
	normalize(ref?.[key]);

export const modelRefMatches = (
	model?: AnyModel | null,
	modelRef?: Record<string, unknown> | null
): boolean => {
	if (!model || !modelRef) return false;

	const candidateRef = getModelRef(model);
	if (!candidateRef) return false;

	const provider = cleanRefValue(modelRef, 'provider').toLowerCase();
	const candidateProvider = cleanRefValue(candidateRef, 'provider').toLowerCase();
	if (provider && candidateProvider && provider !== candidateProvider) return false;

	const source = cleanRefValue(modelRef, 'source');
	const candidateSource = cleanRefValue(candidateRef, 'source');
	if (source && candidateSource && source !== candidateSource) return false;

	const connectionId =
		cleanRefValue(modelRef, 'connection_id') || cleanRefValue(modelRef, 'prefix_id');
	if (connectionId) {
		const candidateConnectionId =
			cleanRefValue(candidateRef, 'connection_id') || cleanRefValue(candidateRef, 'prefix_id');
		return candidateConnectionId === connectionId;
	}

	const connectionIndex = modelRef.connection_index;
	if (connectionIndex !== undefined && normalize(connectionIndex) !== '') {
		return normalize(candidateRef.connection_index) === normalize(connectionIndex);
	}

	return false;
};

export const findModelByRef = <T extends AnyModel>(
	models: T[] = [],
	modelRef?: Record<string, unknown> | null,
	modelIdHint?: string | null
): T | undefined => {
	if (!modelRef) return undefined;

	const parsedHint = parseModelSelectionId(modelIdHint);
	const cleanHint = normalize(parsedHint?.modelId ?? modelIdHint);
	const connectionId =
		cleanRefValue(modelRef, 'connection_id') || cleanRefValue(modelRef, 'prefix_id');
	const hasIndexOnlyRef =
		!connectionId &&
		modelRef.connection_index !== undefined &&
		normalize(modelRef.connection_index) !== '';

	if (hasIndexOnlyRef) {
		if (!cleanHint) return undefined;

		const provider = cleanRefValue(modelRef, 'provider').toLowerCase();
		const source = cleanRefValue(modelRef, 'source');
		const scopedCleanMatches = models.filter((model) => {
			const candidateRef = getModelRef(model);
			if (!candidateRef) return false;

			const candidateProvider = cleanRefValue(candidateRef, 'provider').toLowerCase();
			if (provider && candidateProvider && provider !== candidateProvider) return false;

			const candidateSource = cleanRefValue(candidateRef, 'source');
			if (source && candidateSource && source !== candidateSource) return false;

			return getModelCleanId(model) === cleanHint || getModelIdentityAliases(model).includes(cleanHint);
		});
		const uniqueScopedMatches = new Map(
			scopedCleanMatches.map((model) => [getModelSelectionId(model), model])
		);
		uniqueScopedMatches.delete('');
		if (uniqueScopedMatches.size !== 1) return undefined;
		return Array.from(uniqueScopedMatches.values())[0];
	}

	const matches = models.filter((model) => {
		if (!modelRefMatches(model, modelRef)) return false;
		if (!cleanHint) return true;
		return getModelCleanId(model) === cleanHint || getModelIdentityAliases(model).includes(cleanHint);
	});

	return matches.length === 1 ? matches[0] : undefined;
};

export const getModelLegacyIds = (model?: AnyModel | null): string[] => {
	const values = [
		model?.id,
		model?.selection_key,
		model?.selectionKey,
		model?.model_id,
		model?.original_id,
		model?.originalId,
		model?.legacy_id,
		model?.legacyId,
		...(Array.isArray(model?.legacy_ids) ? model?.legacy_ids : []),
		...(Array.isArray(model?.legacyIds) ? model?.legacyIds : [])
	];
	return Array.from(new Set(values.map(normalize).filter(Boolean)));
};

export const getModelIdentityAliases = (model?: AnyModel | null): string[] => {
	const selectionId = getModelSelectionId(model);
	return Array.from(new Set([selectionId, ...getModelLegacyIds(model)].filter(Boolean)));
};

export const buildModelIdentityLookup = <T extends AnyModel>(
	models: T[] = []
): { byId: Map<string, T>; ambiguous: Set<string> } => {
	const byId = new Map<string, T>();
	const ambiguous = new Set<string>();
	const identityOf = (model: T) => getModelSelectionId(model) || normalize(model?.id);

	for (const model of models ?? []) {
		const identity = identityOf(model);
		for (const alias of getModelIdentityAliases(model)) {
			if (ambiguous.has(alias)) continue;

			const existing = byId.get(alias);
			if (!existing) {
				byId.set(alias, model);
				continue;
			}

			if (identityOf(existing) === identity) continue;

			byId.delete(alias);
			ambiguous.add(alias);
		}
	}

	return { byId, ambiguous };
};

export const findModelByIdentity = <T extends AnyModel>(
	models: T[] = [],
	value?: string | null
): T | undefined => {
	const id = normalize(value);
	if (!id) return undefined;
	const { byId } = buildModelIdentityLookup(models);
	return byId.get(id);
};

export const resolveModelSelectionId = <T extends AnyModel>(
	models: T[] = [],
	value?: string | null,
	options: { preserveAmbiguous?: boolean; preserveMissing?: boolean } = {}
): string => {
	const id = normalize(value);
	if (!id) return '';
	const { byId, ambiguous } = buildModelIdentityLookup(models);
	const model = byId.get(id);
	if (options.preserveAmbiguous && ambiguous.has(id)) return id;
	if (model) return getModelSelectionId(model);
	return options.preserveMissing ? id : '';
};
