import { getModelBaseName, getModelChatDisplayName } from '$lib/utils/model-display';
import {
	buildModelIdentityLookup,
	findModelByRef,
	getModelCleanId,
	getModelRef,
	getModelSelectionId,
	parseModelSelectionId
} from '$lib/utils/model-identity';

type AnyModel = {
	id?: string;
	name?: string;
	model_id?: string;
	original_id?: string;
	model_ref?: Record<string, unknown>;
	[key: string]: any;
};

export type ChatModelResolutionStatus = 'resolved' | 'stale' | 'ambiguous' | 'empty';

export type ChatModelSelectionHint = {
	selection_id?: string;
	display_name?: string;
	model_id?: string;
	model_ref?: Record<string, unknown> | null;
};

export type ChatModelResolution = {
	status: ChatModelResolutionStatus;
	value: string;
	searchValue: string;
	model?: AnyModel;
};

const normalize = (value: unknown) =>
	typeof value === 'string' || typeof value === 'number' ? `${value}`.trim() : '';

const uniqueStrings = (values: unknown[]) =>
	Array.from(new Set(values.map((value) => normalize(value)).filter(Boolean)));

const getRefConnectionId = (modelRef?: Record<string, unknown> | null) =>
	normalize(modelRef?.connection_id) || normalize(modelRef?.prefix_id);

type ModelRecoveryIndex = {
	byId: Map<string, AnyModel>;
	ambiguous: Set<string>;
	displayNames: Map<string, AnyModel[]>;
	bareNames: Map<string, AnyModel[]>;
};
type ParsedModelSelection = NonNullable<ReturnType<typeof parseModelSelectionId>>;

const recoveryIndexCache = new WeakMap<AnyModel[], ModelRecoveryIndex>();

const uniqueBySelectionId = (models: AnyModel[]) => {
	const seen = new Set<string>();
	const result: AnyModel[] = [];
	for (const model of models) {
		const id = getModelSelectionId(model);
		if (!id || seen.has(id)) continue;
		seen.add(id);
		result.push(model);
	}
	return result;
};

const addIndexValue = (index: Map<string, AnyModel[]>, value: unknown, model: AnyModel) => {
	const key = normalize(value);
	if (!key) return;
	index.set(key, [...(index.get(key) ?? []), model]);
};

const getRecoveryIndex = (models: AnyModel[]): ModelRecoveryIndex => {
	const cached = recoveryIndexCache.get(models);
	if (cached) return cached;

	const { byId, ambiguous } = buildModelIdentityLookup(models);
	const displayNames = new Map<string, AnyModel[]>();
	const bareNames = new Map<string, AnyModel[]>();

	for (const model of models ?? []) {
		addIndexValue(displayNames, getModelChatDisplayName(model), model);
		addIndexValue(bareNames, getModelCleanId(model), model);
		addIndexValue(bareNames, getModelBaseName(model), model);
		addIndexValue(bareNames, model?.name, model);
	}

	const index = { byId, ambiguous, displayNames, bareNames };
	recoveryIndexCache.set(models, index);
	return index;
};

const findUniqueDisplayName = (index: ModelRecoveryIndex, displayName: string) => {
	const target = normalize(displayName);
	if (!target) return undefined;

	const matches = uniqueBySelectionId(index.displayNames.get(target) ?? []);
	return matches.length === 1 ? matches[0] : undefined;
};

const findBareModelName = (index: ModelRecoveryIndex, modelName: string) => {
	const target = normalize(modelName);
	if (!target) return { model: undefined, ambiguous: false };

	const matches = uniqueBySelectionId(index.bareNames.get(target) ?? []);

	return {
		model: matches.length === 1 ? matches[0] : undefined,
		ambiguous: matches.length > 1
	};
};

const hasAmbiguousBareModelName = (
	index: ModelRecoveryIndex,
	modelIdHint?: string | null
) => {
	const target = normalize(modelIdHint);
	if (!target) return false;
	return uniqueBySelectionId(index.bareNames.get(target) ?? []).length > 1;
};

const findModelByStableRef = (
	models: AnyModel[],
	index: ModelRecoveryIndex,
	modelRef?: Record<string, unknown> | null,
	modelIdHint?: string | null
) => {
	if (!modelRef) return undefined;
	if (!getRefConnectionId(modelRef) && modelRef.connection_index !== undefined) {
		if (hasAmbiguousBareModelName(index, modelIdHint)) {
			return undefined;
		}
	}
	return findModelByRef(models, modelRef, modelIdHint);
};

export const buildModelSelectionHint = (
	model?: AnyModel | null
): ChatModelSelectionHint | null => {
	if (!model) return null;

	return {
		selection_id: getModelSelectionId(model),
		display_name: getModelChatDisplayName(model),
		model_id: getModelCleanId(model),
		model_ref: getModelRef(model)
	};
};

export const resolveChatModelSelection = (
	models: AnyModel[] = [],
	input:
		| string
		| {
				value?: unknown;
				model_ref?: Record<string, unknown> | null;
				modelRef?: Record<string, unknown> | null;
				display_name?: unknown;
				displayName?: unknown;
				selection_id?: unknown;
				selectionId?: unknown;
				model_id?: unknown;
				modelId?: unknown;
		  }
): ChatModelResolution => {
	const value = normalize(typeof input === 'string' ? input : input?.value);
	const selectionId = normalize(
		typeof input === 'string' ? '' : input?.selection_id ?? input?.selectionId
	);
	const displayName = normalize(
		typeof input === 'string' ? '' : input?.display_name ?? input?.displayName
	);
	const identityCandidates = uniqueStrings([value, selectionId]);
	const parsedSelections = identityCandidates
		.map((candidate) => parseModelSelectionId(candidate))
		.filter((parsed): parsed is ParsedModelSelection => Boolean(parsed));
	const parsedModelId = normalize(parsedSelections.find((parsed) => parsed?.modelId)?.modelId);
	const modelId =
		normalize(typeof input === 'string' ? '' : input?.model_id ?? input?.modelId) ||
		parsedModelId;
	const modelRef =
		typeof input === 'string' ? null : (input?.model_ref ?? input?.modelRef ?? null);
	const displayLookupValue = displayName || (value.includes('|') ? value : '');
	const searchValue = displayLookupValue || modelId || value || selectionId;

	if (!value && !selectionId && !displayName && !modelId && !modelRef) {
		return { status: 'empty', value: '', searchValue: '' };
	}

	const index = getRecoveryIndex(models);

	for (const candidate of identityCandidates) {
		const directModel = index.byId.get(candidate);
		if (directModel) {
			return {
				status: 'resolved',
				value: getModelSelectionId(directModel),
				searchValue,
				model: directModel
			};
		}
	}

	const hasAmbiguousIdentity = identityCandidates.some((candidate) =>
		index.ambiguous.has(candidate)
	);

	for (const parsed of parsedSelections) {
		if (!parsed?.modelRef || !getRefConnectionId(parsed.modelRef)) continue;
		const parsedModel = findModelByStableRef(models, index, parsed.modelRef, parsed.modelId);
		if (parsedModel) {
			return {
				status: 'resolved',
				value: getModelSelectionId(parsedModel),
				searchValue,
				model: parsedModel
			};
		}
	}

	const refModel = modelRef
		? findModelByStableRef(models, index, modelRef, modelId || value || selectionId)
		: undefined;
	if (refModel) {
		return {
			status: 'resolved',
			value: getModelSelectionId(refModel),
			searchValue,
			model: refModel
		};
	}

	const displayModel = findUniqueDisplayName(index, displayLookupValue);
	if (displayModel) {
		return {
			status: 'resolved',
			value: getModelSelectionId(displayModel),
			searchValue,
			model: displayModel
		};
	}

	const bare = findBareModelName(index, modelId || value || selectionId);
	if (bare.model) {
		return {
			status: 'resolved',
			value: getModelSelectionId(bare.model),
			searchValue,
			model: bare.model
		};
	}

	return {
		status: bare.ambiguous || hasAmbiguousIdentity ? 'ambiguous' : 'stale',
		value: value || selectionId || modelId || displayName,
		searchValue
	};
};

export const resolveChatModelSelections = (
	models: AnyModel[] = [],
	values: unknown[] = [],
	hints: Array<ChatModelSelectionHint | null | undefined> = []
) =>
	values.map((value, index) =>
		resolveChatModelSelection(models, {
			value,
			...(hints[index] ?? {})
		})
	);
