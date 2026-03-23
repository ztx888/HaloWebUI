import type { Model, NativeWebSearchSupport } from '$lib/stores';
import type { WebSearchMode } from '$lib/utils/web-search-mode';

type Translator = (key: string, options?: Record<string, unknown>) => string;

type WebSearchConfigLike = {
	features?: {
		enable_web_search?: boolean;
		enable_halo_web_search?: boolean;
		enable_native_web_search?: boolean;
	};
};

type ModelLike = Partial<Model> & {
	id?: string;
	name?: string;
	owned_by?: string;
	native_web_search_supported?: boolean;
	native_web_search_support?: NativeWebSearchSupport;
};

export type WebSearchModeOption = {
	value: WebSearchMode;
	label: string;
	shortLabel?: string;
	description?: string;
	disabled?: boolean;
	badge?: string;
};

export type NativeWebSearchSummary = {
	total: number;
	supportedCount: number;
	unknownCount: number;
	unsupportedCount: number;
	anySupported: boolean;
	anyUnknown: boolean;
	anyUnsupported: boolean;
	allSupported: boolean;
	allUnsupported: boolean;
	hasSelection: boolean;
	sampleSupported?: NativeWebSearchSupport;
	sampleUnknown?: NativeWebSearchSupport;
	sampleUnsupported?: NativeWebSearchSupport;
};

const SUPPORTED_STATUSES = new Set<NativeWebSearchSupport['status']>([
	'supported',
	'unknown',
	'unsupported'
]);

export function getNativeWebSearchSupport(model?: ModelLike | null): NativeWebSearchSupport {
	const support = model?.native_web_search_support;
	if (support && typeof support === 'object' && SUPPORTED_STATUSES.has(support.status)) {
		return support;
	}

	if (model?.native_web_search_supported === true) {
		return {
			status: 'supported',
			reason: 'legacy_supported',
			source: 'legacy'
		};
	}

	if (model?.native_web_search_supported === false) {
		return {
			status: 'unsupported',
			reason: 'legacy_unsupported',
			source: 'legacy'
		};
	}

	const owner = (model?.owned_by ?? '').toString().toLowerCase();
	if (owner === 'openai' || owner === 'google' || owner === 'gemini') {
		return {
			status: 'unknown',
			reason: 'compat_connection_unverified',
			source: 'inferred'
		};
	}

	return {
		status: 'unsupported',
		reason: owner ? 'provider_not_supported' : 'unknown_model',
		source: 'inferred'
	};
}

export function summarizeNativeWebSearchSupport(models: Array<ModelLike | null | undefined>): NativeWebSearchSummary {
	const validModels = models.filter(Boolean) as ModelLike[];
	const summary: NativeWebSearchSummary = {
		total: validModels.length,
		supportedCount: 0,
		unknownCount: 0,
		unsupportedCount: 0,
		anySupported: false,
		anyUnknown: false,
		anyUnsupported: false,
		allSupported: false,
		allUnsupported: false,
		hasSelection: validModels.length > 0
	};

	for (const model of validModels) {
		const support = getNativeWebSearchSupport(model);
		if (support.status === 'supported') {
			summary.supportedCount += 1;
			summary.sampleSupported ??= support;
			continue;
		}
		if (support.status === 'unknown') {
			summary.unknownCount += 1;
			summary.sampleUnknown ??= support;
			continue;
		}
		summary.unsupportedCount += 1;
		summary.sampleUnsupported ??= support;
	}

	summary.anySupported = summary.supportedCount > 0;
	summary.anyUnknown = summary.unknownCount > 0;
	summary.anyUnsupported = summary.unsupportedCount > 0;
	summary.allSupported = summary.hasSelection && summary.supportedCount === summary.total;
	summary.allUnsupported = summary.hasSelection && summary.unsupportedCount === summary.total;

	return summary;
}

export function describeNativeWebSearchSupport(
	t: Translator,
	support?: NativeWebSearchSupport | null
): string {
	switch (support?.reason) {
		case 'official_connection':
			return t('Official provider endpoint detected. Native web search is available by default.');
		case 'connection_enabled':
			return t('Native web search has been enabled for this connection.');
		case 'connection_disabled':
			return t('Native web search is disabled for this connection.');
		case 'compat_connection_unverified':
			return t(
				'This compatible endpoint is not verified yet. Enable native web search in the connection settings if the upstream supports built-in search tools.'
			);
		case 'provider_not_supported':
			return t('This provider does not expose model-native web search in HaloWebUI yet.');
		case 'connection_not_found':
			return t('HaloWebUI could not resolve the connection behind this model.');
		default:
			break;
	}

	if (support?.status === 'supported') {
		return t('Model-native web search is available for this model.');
	}
	if (support?.status === 'unknown') {
		return t('Native web search availability for this model is currently unknown.');
	}
	return t('Model-native web search is unavailable for this model.');
}

export function getNativeWebSearchAvailabilityNote(
	t: Translator,
	summary: NativeWebSearchSummary,
	scope: 'selection' | 'catalog' = 'selection'
): string {
	if (!summary.hasSelection) {
		return '';
	}

	const prefix =
		scope === 'selection' ? t('Current selection') : t('Currently loaded models');

	if (summary.allSupported) {
		return t('{{scope}}: all {{count}} models support native web search.', {
			scope: prefix,
			count: summary.total
		});
	}

	if (summary.allUnsupported) {
		return t('{{scope}}: native web search is unavailable for all models.', {
			scope: prefix
		});
	}

	return t('{{scope}}: {{supported}} native, {{unknown}} unverified, {{unsupported}} unavailable.', {
		scope: prefix,
		supported: summary.supportedCount,
		unknown: summary.unknownCount,
		unsupported: summary.unsupportedCount
	});
}

function buildNativeModeDescription(t: Translator): string {
	return t('Use model-native web search directly for all selected models.');
}

function buildAutoModeDescription(t: Translator, haloEnabled: boolean): string {
	return haloEnabled
		? t('Prefer model-native web search and keep HaloWebUI as fallback.')
		: t('Prefer native web search whenever it is supported.');
}

export function buildWebSearchModeOptions(
	t: Translator,
	config: WebSearchConfigLike | null | undefined,
	models: Array<ModelLike | null | undefined>
): WebSearchModeOption[] {
	const haloEnabled = Boolean(
		config?.features?.enable_halo_web_search ?? config?.features?.enable_web_search
	);
	const nativeEnabled = Boolean(config?.features?.enable_native_web_search);
	const summary = summarizeNativeWebSearchSupport(models);
	const nativeImpossible = summary.hasSelection && summary.allUnsupported;
	const autoImpossible = !haloEnabled && nativeImpossible;

	return [
		{
			value: 'off',
			label: t('Off'),
			description: t('Do not use any web search mode for this chat.')
		},
		...(haloEnabled
			? [
					{
						value: 'halo' as WebSearchMode,
						label: 'HaloWebUI',
						description: t(
							'Always use HaloWebUI web search for consistent behavior across selected models.'
						)
					}
				]
			: []),
		...(nativeEnabled
			? [
					{
						value: 'native' as WebSearchMode,
						label: t('模型原生联网'),
						description: buildNativeModeDescription(t),
						disabled: nativeImpossible
					},
					{
						value: 'auto' as WebSearchMode,
						label: t('Smart Web Search'),
						shortLabel: t('Smart'),
						description: buildAutoModeDescription(t, haloEnabled),
						disabled: autoImpossible,
						badge: haloEnabled && !autoImpossible ? t('Recommended') : undefined
					}
				]
			: [])
	];
}
