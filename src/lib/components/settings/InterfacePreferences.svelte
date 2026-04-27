<script lang="ts">
	import { createEventDispatcher, getContext, onMount, onDestroy, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import { toast } from 'svelte-sonner';

	// @ts-ignore
	import { v4 as uuidv4 } from 'uuid';

	import { config, models, settings, theme, user } from '$lib/stores';
	import { banners as _banners } from '$lib/stores';
	import type { UserSettingsContext } from '$lib/types/user-settings';
	import type { Banner } from '$lib/types';
	import { getBackendConfig, getTaskConfig, updateTaskConfig } from '$lib/apis';
	import { getBanners, setBanners, setDefaultPromptSuggestions } from '$lib/apis/configs';
	import { updateUserInfo } from '$lib/apis/users';
	import { getUserPosition } from '$lib/utils';
	import { getLanguages, changeLanguage, translateWithDefault } from '$lib/i18n';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import { getModelSelectionId, resolveModelSelectionId } from '$lib/utils/model-identity';
	import { setTextScale } from '$lib/utils/text-scale';
	import { revealExpandedSection } from '$lib/utils/expanded-section-scroll';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import ThemeSelector from '$lib/components/common/ThemeSelector.svelte';
	import ManageModal from '$lib/components/chat/Settings/Personalization/ManageModal.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import InlineDirtyActions from '$lib/components/admin/Settings/InlineDirtyActions.svelte';
	import CodeHighlightThemePreview from '$lib/components/settings/CodeHighlightThemePreview.svelte';
	import MermaidThemePreview from '$lib/components/settings/MermaidThemePreview.svelte';
	import ChatTransitionPreview from '$lib/components/settings/ChatTransitionPreview.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';
	import {
		DEFAULT_CHAT_TRANSITION_MODE,
		DEFAULT_HIGHLIGHTER_THEME,
		DEFAULT_MERMAID_THEME,
		LOBE_HIGHLIGHTER_THEMES,
		LOBE_MERMAID_THEMES,
		resolveChatTransitionMode,
		type ChatTransitionMode,
		normalizeHighlighterTheme,
		normalizeMermaidTheme,
		type MermaidThemeId
	} from '$lib/utils/lobehub-chat-appearance';
	import {
		getPreferredWebSearchMode,
		normalizeWebSearchMode,
		type WebSearchMode
	} from '$lib/utils/web-search-mode';
	import {
		buildWebSearchModeOptions,
		getNativeWebSearchAvailabilityNote,
		summarizeNativeWebSearchSupport
	} from '$lib/utils/native-web-search';

	const dispatch = createEventDispatcher();
	const i18n: Writable<any> = getContext('i18n');
	const tr = (key: string, defaultValue: string, options: Record<string, any> = {}) =>
		translateWithDefault($i18n, key, defaultValue, options);
	const { getModels } = getContext<UserSettingsContext>('user-settings');

	export let saveSettings: Function;

	// When embedded inside a larger scroll page, avoid "full-height tab" layout.
	export let embedded: boolean = false;

	type SectionKey = 'appearance' | 'layout' | 'chat' | 'input' | 'advanced';

	// When set, only render the specified section (no collapsible header, flat content).
	// null = render all sections with collapsible headers (default behavior).
	export let activeSection: SectionKey | null = null;

	let loading = true;
	let modelsLoading = false;
	let modelsLoadError: string | null = null;

	// Appearance
	// Only expose system/dark/light in the UI, but keep legacy theme classes here so we can
	// reliably clean them up for users upgrading from older versions.
	const normalizeTheme = (rawTheme: string | null | undefined) => {
		if (rawTheme === 'system' || rawTheme === 'dark' || rawTheme === 'light') return rawTheme;
		if (rawTheme === 'oled-dark' || rawTheme === 'her' || rawTheme === 'rose-pine dark')
			return 'dark';
		if (rawTheme === 'rose-pine-dawn light') return 'light';
		return 'system';
	};
	let themes = ['dark', 'light', 'her', 'rose-pine dark', 'rose-pine-dawn light', 'oled-dark'];
	let selectedTheme = 'system';
	let highlighterTheme = DEFAULT_HIGHLIGHTER_THEME;
	let languages: Awaited<ReturnType<typeof getLanguages>> = [];
	let lang = '';
	let mermaidTheme: MermaidThemeId = DEFAULT_MERMAID_THEME;
	let notificationEnabled = false;

	let expandedSections = {
		appearance: true,
		layout: true,
		chat: true,
		input: true,
		advanced: true
	};
	let sectionEls: Record<string, HTMLElement> = {};

	// UI
	let backgroundImageUrl: string | null = null;
	let inputFiles: FileList | null = null;
	let filesInputElement: HTMLInputElement | null = null;

	let landingPageMode = '';
	let chatBubble = true;
	let widescreenMode = false;
	let chatDirection: 'LTR' | 'RTL' | 'auto' = 'auto';
	let showUsername = false;
	let showFeaturedAssistantsOnHome = true;
	let notificationSound = true;
	let showChatTitleInTab = true;
	let textScale: number | null = null;
	let collapseCodeBlocks = false;
	let collapseHistoricalLongResponses = true;
	let showInlineCitations = true;
	let showMessageOutline = true;
	let showFormulaQuickCopyButton = true;
	let expandDetails = false;

	// Chat behavior
	let titleAutoGenerate = true;
	let autoTags = true;
	let autoFollowUps = true;
	let detectArtifacts = true;
	let svgPreviewAutoOpen = true;
	let responseAutoCopy = false;
	let scrollOnBranchChange = true;
	let enableMessageQueue = true;
	let temporaryChatByDefault = false;
	let transitionMode: ChatTransitionMode = DEFAULT_CHAT_TRANSITION_MODE;
	let enableAutoScrollOnStreaming = true;
	let insertSuggestionPrompt = false;
	let keepFollowUpPrompts = false;
	let insertFollowUpPrompt = false;
	let regenerateMenu = true;
	let renderMarkdownInPreviews = true;
	let displayMultiModelResponsesInTabs = false;
	let stylizedPdfExport = true;
	let showFloatingActionButtons = true;
	let floatingActionButtons: Array<{
		id: string;
		label: string;
		input: boolean;
		prompt: string;
	}> | null = null;

	// Memory
	let enableMemory = false;
	let showManageModal = false;

	// Input
	let richTextInput = true;
	let promptAutocomplete = false;
	// Admin-level autocomplete config (only loaded for admins)
	let enableAutocompleteGeneration = false;
	let autocompleteGenerationInputMaxLength: number = -1;
	let showFormattingToolbar = false;
	let insertPromptAsRichText = false;
	let ctrlEnterToSend = false;
	let copyFormatted = false;
	let largeTextAsFile = false;
	let globalSystemPrompt = '';

	// Privacy / advanced
	let userLocation = false;
	let webSearchMode: WebSearchMode = 'off';
	let iframeSandboxAllowSameOrigin = false;
	let iframeSandboxAllowForms = false;

	// Voice / haptics
	let showEmojiInCall = false;
	let voiceInterruption = false;
	let hapticFeedback = false;

	// Files
	let imageCompression = false;
	let imageCompressionSize: { width: string; height: string } = { width: '', height: '' };
	let imageCompressionInChannels = true;
	let imageCompressionPreset = 'custom';

	// Admin-only: Banners & Prompt Suggestions (moved from tasks tab)
	let banners: Banner[] = [];
	let promptSuggestions: any[] = [];

	type PreferenceSectionKey = 'appearance' | 'layout' | 'input' | 'chat' | 'advanced';
	type SectionDirtyState = Record<PreferenceSectionKey, boolean>;

	type SectionSnapshot = {
		appearance: {
			selectedTheme: string;
			highlighterTheme: string;
			lang: string;
			backgroundImageUrl: string | null;
			mermaidTheme: MermaidThemeId;
			textScale: number | null;
			transitionMode: ChatTransitionMode;
			enableAutoScrollOnStreaming: boolean;
		};
		layout: {
			defaultModelId: string;
			showChatTitleInTab: boolean;
			showFeaturedAssistantsOnHome: boolean;
			landingPageMode: string;
			chatBubble: boolean;
			showUsername: boolean;
			widescreenMode: boolean;
			chatDirection: 'LTR' | 'RTL' | 'auto';
			notificationEnabled: boolean;
			notificationSound: boolean;
			banners: Banner[];
		};
		input: {
			richTextInput: boolean;
			promptAutocomplete: boolean;
			enableAutocompleteGeneration: boolean;
			autocompleteGenerationInputMaxLength: number;
			showFormattingToolbar: boolean;
			insertPromptAsRichText: boolean;
			largeTextAsFile: boolean;
			copyFormatted: boolean;
			ctrlEnterToSend: boolean;
			globalSystemPrompt: string;
			promptSuggestions: any[];
		};
		chat: {
			titleAutoGenerate: boolean;
			autoTags: boolean;
			autoFollowUps: boolean;
			detectArtifacts: boolean;
			svgPreviewAutoOpen: boolean;
			responseAutoCopy: boolean;
			scrollOnBranchChange: boolean;
			enableMessageQueue: boolean;
			temporaryChatByDefault: boolean;
			collapseCodeBlocks: boolean;
			collapseHistoricalLongResponses: boolean;
			showInlineCitations: boolean;
			showMessageOutline: boolean;
			showFormulaQuickCopyButton: boolean;
			expandDetails: boolean;
			insertSuggestionPrompt: boolean;
			keepFollowUpPrompts: boolean;
			insertFollowUpPrompt: boolean;
			regenerateMenu: boolean;
			renderMarkdownInPreviews: boolean;
			displayMultiModelResponsesInTabs: boolean;
			stylizedPdfExport: boolean;
			showFloatingActionButtons: boolean;
			floatingActionButtons: Array<{
				id: string;
				label: string;
				input: boolean;
				prompt: string;
			}> | null;
			enableMemory: boolean;
			showEmojiInCall: boolean;
			voiceInterruption: boolean;
			imageCompression: boolean;
			imageCompressionSize: {
				width: string;
				height: string;
			};
			imageCompressionInChannels: boolean;
		};
		advanced: {
			userLocation: boolean;
			webSearchMode: WebSearchMode;
			iframeSandboxAllowSameOrigin: boolean;
			iframeSandboxAllowForms: boolean;
			hapticFeedback: boolean;
		};
	};

	let defaultModelId = '';
	let appearanceSaving = false;
	let layoutSaving = false;
	let inputSaving = false;
	let chatSaving = false;
	let advancedSaving = false;
	let sectionSnapshot: SectionSnapshot;
	let dirtySections: SectionDirtyState = {
		appearance: false,
		layout: false,
		input: false,
		chat: false,
		advanced: false
	};
	let sectionDirtyState: SectionDirtyState = dirtySections;
	let initialSectionSnapshot: SectionSnapshot | null = null;
	let autoSyncSectionBaseline = false;
	let sectionBaselineSyncTimeout: ReturnType<typeof setTimeout> | null = null;
	const SECTION_BASELINE_SYNC_WINDOW_MS = 400;

	// Admin-only user preferences (still stored per-user)

	const normalizeModelId = (value: string | null | undefined) => String(value ?? '').trim();
	const resolveDefaultModelId = (value: string | null | undefined) => {
		const normalized = normalizeModelId(value);
		if (!normalized) return '';
		return resolveModelSelectionId($models ?? [], normalized, { preserveAmbiguous: true }) || normalized;
	};

	const normalizeImageCompressionSize = (
		value: { width?: string | number | null; height?: string | number | null } | null | undefined
	) => ({
		width: value?.width === null || value?.width === undefined ? '' : String(value.width),
		height: value?.height === null || value?.height === undefined ? '' : String(value.height)
	});

	const imageCompressionPresets = {
		auto: { width: '', height: '' },
		standard: { width: '1920', height: '1080' },
		medium: { width: '1280', height: '720' },
		small: { width: '800', height: '600' }
	};

	const detectPreset = (size: { width: string; height: string }): string => {
		for (const [key, preset] of Object.entries(imageCompressionPresets)) {
			if (preset.width === size.width && preset.height === size.height) {
				return key;
			}
		}
		return 'custom';
	};

	const applyPreset = (preset: string) => {
		if (preset !== 'custom' && imageCompressionPresets[preset]) {
			imageCompressionSize = { ...imageCompressionPresets[preset] };
			imageCompressionPreset = preset;
		}
	};

	const getEffectiveDefaultModelId = () => {
		return resolveDefaultModelId($settings?.models?.at(0));
	};

	const applyTheme = (rawTheme: string) => {
		const _theme = normalizeTheme(rawTheme);
		let themeToApply = _theme;

		if (_theme === 'system') {
			themeToApply = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
		}

		if (themeToApply === 'dark') {
			document.documentElement.style.setProperty('--color-gray-800', '#333');
			document.documentElement.style.setProperty('--color-gray-850', '#262626');
			document.documentElement.style.setProperty('--color-gray-900', '#171717');
			document.documentElement.style.setProperty('--color-gray-950', '#0d0d0d');
		}

		themes
			.filter((e) => e !== themeToApply)
			.forEach((e) => {
				e.split(' ').forEach((e) => {
					document.documentElement.classList.remove(e);
				});
			});

		themeToApply.split(' ').forEach((e) => {
			document.documentElement.classList.add(e);
		});

		const metaThemeColor = document.querySelector('meta[name="theme-color"]');
		if (metaThemeColor) {
			if (_theme === 'system') {
				const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
					? 'dark'
					: 'light';
				metaThemeColor.setAttribute('content', systemTheme === 'light' ? '#ffffff' : '#171717');
			} else {
				metaThemeColor.setAttribute('content', _theme === 'dark' ? '#171717' : '#ffffff');
			}
		}

		if (typeof window !== 'undefined' && window.applyTheme) {
			window.applyTheme();
		}
	};

	const commitThemeSelection = (rawTheme: string) => {
		const nextTheme = normalizeTheme(rawTheme);
		selectedTheme = nextTheme;
		theme.set(nextTheme);
		applyTheme(nextTheme);
	};

	const commitLanguageSelection = async (nextLang: string) => {
		lang = nextLang;
		await changeLanguage(nextLang);
	};

	const getThemeOptionLabel = (displayName: string, id: string) =>
		id === 'lobe-theme' ? tr('默认主题', 'Default Theme') : displayName;

	const commitTextScaleSelection = (scale: number | null) => {
		textScale = scale;
		setTextScale(textScale ?? 1);
	};

	// Avoid TS type assertions in Svelte markup expressions (can break parsing depending on tooling).
	const onCtrlEnterBehaviorChange = (e: CustomEvent<{ value: string }>) => {
		ctrlEnterToSend = e.detail.value === 'ctrl_enter';
	};

	const onWebSearchChange = (e: CustomEvent<{ value: string }>) => {
		webSearchMode = normalizeWebSearchMode(e.detail?.value, 'off');
	};

	$: nativeWebSearchCatalogSummary = summarizeNativeWebSearchSupport($models ?? []);
	$: webSearchModeOptions = buildWebSearchModeOptions(
		(key, options) => $i18n.t(key, options),
		$config,
		$models ?? []
	);
	$: currentWebSearchOption = webSearchModeOptions.find((option) => option.value === webSearchMode) ?? null;
	$: currentWebSearchModeDescription = currentWebSearchOption?.description ?? '';
	$: webSearchAvailabilityNote = getNativeWebSearchAvailabilityNote(
		(key, options) => $i18n.t(key, options),
		nativeWebSearchCatalogSummary,
		'catalog'
	);
	$: if (
		(($models ?? []).length > 0 || !$config?.features?.enable_native_web_search) &&
		!webSearchModeOptions.some((option) => option.value === webSearchMode && option.disabled !== true)
	) {
		webSearchMode =
			(['auto', 'halo', 'native', 'off'] as WebSearchMode[]).find((mode) =>
				webSearchModeOptions.some((option) => option.value === mode && option.disabled !== true)
			) ?? 'off';
	}

	const onBackgroundFileChange = async () => {
		if (!inputFiles || inputFiles.length === 0) return;
		const file = inputFiles[0];
		if (!['image/gif', 'image/webp', 'image/jpeg', 'image/png'].includes((file as any)?.type)) {
			inputFiles = null;
			return;
		}

		const reader = new FileReader();
		reader.onload = async (event) => {
			backgroundImageUrl = `${event?.target?.result ?? ''}` || null;
		};
		reader.readAsDataURL(file);
	};

	const clearBackgroundImage = () => {
		backgroundImageUrl = null;
		inputFiles = null;
	};

	const addFloatingAction = () => {
		if (floatingActionButtons === null) {
			floatingActionButtons = [
				{
					id: 'ask',
					label: $i18n.t('Ask'),
					input: true,
					prompt: '{{SELECTED_CONTENT}}\n\n\n{{INPUT_CONTENT}}'
				},
				{
					id: 'explain',
					label: $i18n.t('Explain'),
					input: false,
					prompt: `{{SELECTED_CONTENT}}\n\n\n${$i18n.t('Explain')}`
				}
			];
		}

		let idx = 0;
		let id = 'new-button';
		while ((floatingActionButtons ?? []).some((button) => button.id === id)) {
			idx += 1;
			id = `new-button-${idx}`;
		}

		floatingActionButtons = [
			...(floatingActionButtons ?? []),
			{
				id,
				label: $i18n.t('New Button'),
				input: true,
				prompt: '{{SELECTED_CONTENT}}\n\n\n{{INPUT_CONTENT}}'
			}
		];
	};

	const removeFloatingAction = (id: string) => {
		if (floatingActionButtons === null) {
			return;
		}

		floatingActionButtons = floatingActionButtons.filter((button) => button.id !== id);
		if (floatingActionButtons.length === 0) {
			floatingActionButtons = null;
		}
	};

	const setFloatingActionsMode = (mode: 'default' | 'custom') => {
		floatingActionButtons = mode === 'default' ? null : (floatingActionButtons ?? []);
	};

	const touchFloatingActions = () => {
		floatingActionButtons = cloneSettingsSnapshot(floatingActionButtons);
	};

	const touchBanners = () => {
		banners = cloneSettingsSnapshot(banners);
	};

	const transitionModeOptions: Array<{ label: string; value: ChatTransitionMode }> = [
		{ label: 'None', value: 'none' },
		{ label: 'Fade In', value: 'fadeIn' },
		{ label: 'Smooth', value: 'smooth' }
	];

	const buildSectionSnapshot = (): SectionSnapshot => ({
		appearance: {
			selectedTheme: normalizeTheme(selectedTheme),
			highlighterTheme: normalizeHighlighterTheme(highlighterTheme),
			lang,
			backgroundImageUrl,
			mermaidTheme: normalizeMermaidTheme(mermaidTheme),
			textScale,
			transitionMode,
			enableAutoScrollOnStreaming
		},
		layout: {
			showChatTitleInTab,
			showFeaturedAssistantsOnHome,
			landingPageMode,
			chatBubble,
			showUsername,
			widescreenMode,
			chatDirection,
			notificationEnabled,
			notificationSound,
			banners
		},
		input: {
			richTextInput,
			promptAutocomplete,
			enableAutocompleteGeneration,
			autocompleteGenerationInputMaxLength,
			showFormattingToolbar,
			insertPromptAsRichText,
			largeTextAsFile,
			copyFormatted,
			ctrlEnterToSend,
			globalSystemPrompt,
			promptSuggestions
		},
		chat: {
			defaultModelId: resolveDefaultModelId(defaultModelId),
			titleAutoGenerate,
			autoTags,
			autoFollowUps,
			detectArtifacts,
			svgPreviewAutoOpen,
			responseAutoCopy,
			scrollOnBranchChange,
			enableMessageQueue,
			temporaryChatByDefault,
			collapseCodeBlocks,
			collapseHistoricalLongResponses,
			showInlineCitations,
			showMessageOutline,
			showFormulaQuickCopyButton,
			expandDetails,
			insertSuggestionPrompt,
			keepFollowUpPrompts,
			insertFollowUpPrompt,
			regenerateMenu,
			renderMarkdownInPreviews,
			displayMultiModelResponsesInTabs,
			stylizedPdfExport,
			showFloatingActionButtons,
			floatingActionButtons,
			enableMemory,
			showEmojiInCall,
			voiceInterruption,
			imageCompression,
			imageCompressionSize: normalizeImageCompressionSize(imageCompressionSize),
			imageCompressionInChannels
		},
		advanced: {
			userLocation,
			webSearchMode: normalizeWebSearchMode(webSearchMode, 'off'),
			iframeSandboxAllowSameOrigin,
			iframeSandboxAllowForms,
			hapticFeedback
		}
	});

	const applyAppearanceSnapshot = (snapshot: SectionSnapshot['appearance']) => {
		selectedTheme = normalizeTheme(snapshot.selectedTheme);
		highlighterTheme = normalizeHighlighterTheme(snapshot.highlighterTheme);
		lang = snapshot.lang;
		backgroundImageUrl = snapshot.backgroundImageUrl;
		mermaidTheme = normalizeMermaidTheme(snapshot.mermaidTheme);
		textScale = snapshot.textScale;
		transitionMode = snapshot.transitionMode;
		enableAutoScrollOnStreaming = snapshot.enableAutoScrollOnStreaming;
	};

	const applyLayoutSnapshot = (snapshot: SectionSnapshot['layout']) => {
		defaultModelId = resolveDefaultModelId(snapshot.defaultModelId);
		showChatTitleInTab = snapshot.showChatTitleInTab;
		showFeaturedAssistantsOnHome = snapshot.showFeaturedAssistantsOnHome;
		landingPageMode = snapshot.landingPageMode;
		chatBubble = snapshot.chatBubble;
		showUsername = snapshot.showUsername;
		widescreenMode = snapshot.widescreenMode;
		chatDirection = snapshot.chatDirection;
		notificationEnabled = snapshot.notificationEnabled;
		notificationSound = snapshot.notificationSound;
		banners = snapshot.banners;
	};

	const applyInputSnapshot = (snapshot: SectionSnapshot['input']) => {
		richTextInput = snapshot.richTextInput;
		promptAutocomplete = snapshot.promptAutocomplete;
		enableAutocompleteGeneration = snapshot.enableAutocompleteGeneration;
		autocompleteGenerationInputMaxLength = snapshot.autocompleteGenerationInputMaxLength;
		showFormattingToolbar = snapshot.showFormattingToolbar;
		insertPromptAsRichText = snapshot.insertPromptAsRichText;
		largeTextAsFile = snapshot.largeTextAsFile;
		copyFormatted = snapshot.copyFormatted;
		ctrlEnterToSend = snapshot.ctrlEnterToSend;
		globalSystemPrompt = snapshot.globalSystemPrompt;
		promptSuggestions = snapshot.promptSuggestions;
	};

	const applyChatSnapshot = (snapshot: SectionSnapshot['chat']) => {
		titleAutoGenerate = snapshot.titleAutoGenerate;
		autoTags = snapshot.autoTags;
		autoFollowUps = snapshot.autoFollowUps;
		detectArtifacts = snapshot.detectArtifacts;
		svgPreviewAutoOpen = snapshot.svgPreviewAutoOpen;
		responseAutoCopy = snapshot.responseAutoCopy;
		scrollOnBranchChange = snapshot.scrollOnBranchChange;
		enableMessageQueue = snapshot.enableMessageQueue;
		temporaryChatByDefault = snapshot.temporaryChatByDefault;
		collapseCodeBlocks = snapshot.collapseCodeBlocks;
		collapseHistoricalLongResponses = snapshot.collapseHistoricalLongResponses;
		showInlineCitations = snapshot.showInlineCitations;
		showMessageOutline = snapshot.showMessageOutline;
		showFormulaQuickCopyButton = snapshot.showFormulaQuickCopyButton;
		expandDetails = snapshot.expandDetails;
		insertSuggestionPrompt = snapshot.insertSuggestionPrompt;
		keepFollowUpPrompts = snapshot.keepFollowUpPrompts;
		insertFollowUpPrompt = snapshot.insertFollowUpPrompt;
		regenerateMenu = snapshot.regenerateMenu;
		renderMarkdownInPreviews = snapshot.renderMarkdownInPreviews;
		displayMultiModelResponsesInTabs = snapshot.displayMultiModelResponsesInTabs;
		stylizedPdfExport = snapshot.stylizedPdfExport;
		showFloatingActionButtons = snapshot.showFloatingActionButtons;
		floatingActionButtons = cloneSettingsSnapshot(snapshot.floatingActionButtons);
		enableMemory = snapshot.enableMemory;
		showEmojiInCall = snapshot.showEmojiInCall;
		voiceInterruption = snapshot.voiceInterruption;
		imageCompression = snapshot.imageCompression;
		imageCompressionSize = cloneSettingsSnapshot(snapshot.imageCompressionSize);
		imageCompressionInChannels = snapshot.imageCompressionInChannels;
	};

	const applyAdvancedSnapshot = (snapshot: SectionSnapshot['advanced']) => {
		userLocation = snapshot.userLocation;
		webSearchMode = normalizeWebSearchMode(snapshot.webSearchMode, 'off');
		iframeSandboxAllowSameOrigin = snapshot.iframeSandboxAllowSameOrigin;
		iframeSandboxAllowForms = snapshot.iframeSandboxAllowForms;
		hapticFeedback = snapshot.hapticFeedback;
	};

	$: {
		selectedTheme;
		highlighterTheme;
		lang;
		backgroundImageUrl;
		mermaidTheme;
		textScale;
		defaultModelId;
		showChatTitleInTab;
		showFeaturedAssistantsOnHome;
		landingPageMode;
		chatBubble;
		showUsername;
		widescreenMode;
		chatDirection;
		notificationEnabled;
		notificationSound;
		banners;
		richTextInput;
		promptAutocomplete;
		enableAutocompleteGeneration;
		autocompleteGenerationInputMaxLength;
		showFormattingToolbar;
		insertPromptAsRichText;
		largeTextAsFile;
		copyFormatted;
		ctrlEnterToSend;
		globalSystemPrompt;
		promptSuggestions;
		titleAutoGenerate;
		autoTags;
		autoFollowUps;
		detectArtifacts;
		svgPreviewAutoOpen;
		responseAutoCopy;
		scrollOnBranchChange;
		enableMessageQueue;
		temporaryChatByDefault;
		transitionMode;
		enableAutoScrollOnStreaming;
		collapseCodeBlocks;
		collapseHistoricalLongResponses;
		showInlineCitations;
		showMessageOutline;
		showFormulaQuickCopyButton;
		expandDetails;
		insertSuggestionPrompt;
		keepFollowUpPrompts;
		insertFollowUpPrompt;
		regenerateMenu;
		renderMarkdownInPreviews;
		displayMultiModelResponsesInTabs;
		stylizedPdfExport;
		showFloatingActionButtons;
		floatingActionButtons;
		enableMemory;
		showEmojiInCall;
		voiceInterruption;
		hapticFeedback;
		imageCompression;
		imageCompressionSize;
		imageCompressionInChannels;
		userLocation;
		webSearchMode;
		iframeSandboxAllowSameOrigin;
		iframeSandboxAllowForms;
		sectionSnapshot = buildSectionSnapshot();
	}
	$: if (
		autoSyncSectionBaseline &&
		(initialSectionSnapshot === null ||
			!isSettingsSnapshotEqual(sectionSnapshot, initialSectionSnapshot))
	) {
		initialSectionSnapshot = cloneSettingsSnapshot(sectionSnapshot);
	}
	$: dirtySections = initialSectionSnapshot
		? {
				appearance: !isSettingsSnapshotEqual(
					sectionSnapshot.appearance,
					initialSectionSnapshot.appearance
				),
				layout: !isSettingsSnapshotEqual(sectionSnapshot.layout, initialSectionSnapshot.layout),
				input: !isSettingsSnapshotEqual(sectionSnapshot.input, initialSectionSnapshot.input),
				chat: !isSettingsSnapshotEqual(sectionSnapshot.chat, initialSectionSnapshot.chat),
				advanced: !isSettingsSnapshotEqual(
					sectionSnapshot.advanced,
					initialSectionSnapshot.advanced
				)
			}
		: {
				appearance: false,
				layout: false,
				input: false,
				chat: false,
				advanced: false
			};
	$: sectionDirtyState = dirtySections;

	// Per-section dirty change dispatch for parent page shell
	$: dispatch('sectionDirtyChange', { sections: dirtySections });

	// Unified dirty / save / reset API for parent page shell (hero InlineDirtyActions)
	$: anyDirty = Object.values(dirtySections).some(Boolean);
	let lastDirtyState: boolean | null = null;
	$: if (anyDirty !== lastDirtyState) {
		lastDirtyState = anyDirty;
		dispatch('dirtyChange', { value: anyDirty });
	}

	export const getSectionSaving = (section: SectionKey): boolean => {
		switch (section) {
			case 'appearance': return appearanceSaving;
			case 'layout': return layoutSaving;
			case 'input': return inputSaving;
			case 'chat': return chatSaving;
			case 'advanced': return advancedSaving;
		}
	};

	// Per-section save/reset exports for parent page shell
	export const saveSection = async (section: SectionKey) => {
		switch (section) {
			case 'appearance': return saveAppearanceChanges();
			case 'layout': return saveLayoutChanges();
			case 'input': return saveInputChanges();
			case 'chat': return saveChatChanges();
			case 'advanced': return saveAdvancedChanges();
		}
	};

	export const resetSection = async (section: SectionKey) => {
		switch (section) {
			case 'appearance': return resetAppearanceChanges();
			case 'layout': return resetLayoutChanges();
			case 'input': return resetInputChanges();
			case 'chat': return resetChatChanges();
			case 'advanced': return resetAdvancedChanges();
		}
	};

	export const save = async () => {
		if (dirtySections.appearance) await saveAppearanceChanges();
		if (dirtySections.layout) await saveLayoutChanges();
		if (dirtySections.input) await saveInputChanges();
		if (dirtySections.chat) await saveChatChanges();
		if (dirtySections.advanced) await saveAdvancedChanges();
	};

	export const reset = async () => {
		resetAppearanceChanges();
		resetLayoutChanges();
		resetInputChanges();
		resetChatChanges();
		resetAdvancedChanges();
	};

	const syncSectionBaseline = () => {
		initialSectionSnapshot = cloneSettingsSnapshot(buildSectionSnapshot());
	};

	const updateSectionBaseline = (section: PreferenceSectionKey) => {
		if (!initialSectionSnapshot) {
			syncSectionBaseline();
			return;
		}

		initialSectionSnapshot = {
			...initialSectionSnapshot,
			[section]: cloneSettingsSnapshot(buildSectionSnapshot()[section])
		};
	};

	const startSectionBaselineSync = () => {
		autoSyncSectionBaseline = true;
		if (sectionBaselineSyncTimeout) {
			clearTimeout(sectionBaselineSyncTimeout);
		}
		sectionBaselineSyncTimeout = setTimeout(() => {
			autoSyncSectionBaseline = false;
			sectionBaselineSyncTimeout = null;
		}, SECTION_BASELINE_SYNC_WINDOW_MS);
	};

	const ensureNotificationPermission = async () => {
		if (!notificationEnabled || typeof Notification === 'undefined') {
			return true;
		}

		if (Notification.permission === 'granted') {
			return true;
		}

		const permission = await Notification.requestPermission();
		if (permission === 'granted') {
			return true;
		}

		notificationEnabled = false;
		toast.error(
			$i18n.t(
				'Response notifications cannot be activated as the website permissions have been denied. Please visit your browser settings to grant the necessary access.'
			)
		);
		return false;
	};

	const syncUserLocationPreference = async () => {
		if (!userLocation) {
			return;
		}

		const position = await getUserPosition().catch((error) => {
			toast.error(error?.message ?? String(error));
			return null;
		});

		if (!position) {
			userLocation = false;
			return;
		}

		await updateUserInfo(localStorage.token, { location: position });
	};

	const saveAppearanceChanges = async () => {
		if (appearanceSaving) return;

		appearanceSaving = true;
		try {
			await saveSettings({
				backgroundImageUrl,
				highlighterTheme: normalizeHighlighterTheme(highlighterTheme),
				mermaidTheme: normalizeMermaidTheme(mermaidTheme),
				textScale,
				transitionMode,
				enableAutoScrollOnStreaming
			});
			localStorage.setItem('theme', normalizeTheme(selectedTheme));
			commitThemeSelection(selectedTheme);
			await commitLanguageSelection(lang);
			commitTextScaleSelection(textScale);
			await tick();
			startSectionBaselineSync();
			updateSectionBaseline('appearance');
			dispatch('save');
		} finally {
			appearanceSaving = false;
		}
	};

	const saveLayoutChanges = async () => {
		if (layoutSaving) return;

		layoutSaving = true;
		try {
			await ensureNotificationPermission();

			const payload: Record<string, any> = {
				showChatTitleInTab,
				showFeaturedAssistantsOnHome,
				landingPageMode,
				chatBubble,
				showUsername,
				widescreenMode,
				chatDirection,
				notificationEnabled,
				notificationSound
			};

			await saveSettings(payload);
			// Admin: save banners
			if ($user?.role === 'admin') {
				_banners.set(await setBanners(localStorage.token, banners));
			}
			await tick();
			startSectionBaselineSync();
			updateSectionBaseline('layout');
			dispatch('save');
		} finally {
			layoutSaving = false;
		}
	};

	const saveInputChanges = async () => {
		if (inputSaving) return;

		inputSaving = true;
		try {
			await saveSettings({
				richTextInput,
				promptAutocomplete,
				showFormattingToolbar,
				insertPromptAsRichText,
				largeTextAsFile,
				copyFormatted,
				ctrlEnterToSend,
				system: globalSystemPrompt.trim() ? globalSystemPrompt : ''
			});
			// Admin: save autocomplete generation task config
			if ($user?.role === 'admin') {
				const currentTaskConfig = await getTaskConfig(localStorage.token);
				await updateTaskConfig(localStorage.token, {
					...currentTaskConfig,
					ENABLE_AUTOCOMPLETE_GENERATION: enableAutocompleteGeneration,
					AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH: autocompleteGenerationInputMaxLength
				});
				await config.set(await getBackendConfig());
				// Admin: save default prompt suggestions
				await setDefaultPromptSuggestions(localStorage.token, promptSuggestions);
			}
			await tick();
			startSectionBaselineSync();
			updateSectionBaseline('input');
			dispatch('save');
		} finally {
			inputSaving = false;
		}
	};

	const saveChatChanges = async () => {
		if (chatSaving) return;

		chatSaving = true;
		try {
			await saveSettings({
				models: resolveDefaultModelId(defaultModelId) ? [resolveDefaultModelId(defaultModelId)] : [],
				title: {
					...($settings?.title ?? {}),
					auto: titleAutoGenerate
				},
				autoTags,
				autoFollowUps,
				detectArtifacts,
				svgPreviewAutoOpen,
				responseAutoCopy,
				scrollOnBranchChange,
				enableMessageQueue,
				temporaryChatByDefault,
				collapseCodeBlocks,
				collapseHistoricalLongResponses,
				showInlineCitations,
				showMessageOutline,
				showFormulaQuickCopyButton,
				expandDetails,
				insertSuggestionPrompt,
				keepFollowUpPrompts,
				insertFollowUpPrompt,
				regenerateMenu,
				renderMarkdownInPreviews,
				displayMultiModelResponsesInTabs,
				stylizedPdfExport,
				showFloatingActionButtons,
				floatingActionButtons,
				memory: enableMemory,
				voiceInterruption,
				showEmojiInCall,
				imageCompression,
				imageCompressionSize: normalizeImageCompressionSize(imageCompressionSize),
				imageCompressionInChannels
			});
			await tick();
			startSectionBaselineSync();
			updateSectionBaseline('chat');
			dispatch('save');
		} finally {
			chatSaving = false;
		}
	};

	const saveAdvancedChanges = async () => {
		if (advancedSaving) return;

		advancedSaving = true;
		try {
			await syncUserLocationPreference();
			await saveSettings({
				userLocation,
				webSearchMode: normalizeWebSearchMode(webSearchMode, 'off'),
				webSearch: null,
				iframeSandboxAllowSameOrigin,
				iframeSandboxAllowForms,
				hapticFeedback
			});
			await tick();
			startSectionBaselineSync();
			updateSectionBaseline('advanced');
			dispatch('save');
		} finally {
			advancedSaving = false;
		}
	};

	const resetAppearanceChanges = () => {
		if (!initialSectionSnapshot) return;
		applyAppearanceSnapshot(cloneSettingsSnapshot(initialSectionSnapshot.appearance));
	};

	const resetLayoutChanges = () => {
		if (!initialSectionSnapshot) return;
		applyLayoutSnapshot(cloneSettingsSnapshot(initialSectionSnapshot.layout));
	};

	const resetInputChanges = () => {
		if (!initialSectionSnapshot) return;
		applyInputSnapshot(cloneSettingsSnapshot(initialSectionSnapshot.input));
	};

	const resetChatChanges = () => {
		if (!initialSectionSnapshot) return;
		applyChatSnapshot(cloneSettingsSnapshot(initialSectionSnapshot.chat));
	};

	const resetAdvancedChanges = () => {
		if (!initialSectionSnapshot) return;
		applyAdvancedSnapshot(cloneSettingsSnapshot(initialSectionSnapshot.advanced));
	};

	let rootClass = 'flex flex-col space-y-6 text-sm';
	let bodyClass = 'space-y-6 overflow-y-auto scrollbar-hidden';
	$: if (($models?.length ?? 0) > 0 && defaultModelId) {
		const resolved = resolveDefaultModelId(defaultModelId);
		if (resolved && resolved !== defaultModelId) {
			defaultModelId = resolved;
		}
	}

	$: {
		rootClass = embedded
			? 'flex flex-col space-y-6 text-sm'
			: 'flex flex-col h-full justify-between space-y-6 text-sm';

		bodyClass = embedded
			? 'space-y-6 overflow-y-visible'
			: 'space-y-6 overflow-y-auto scrollbar-hidden h-full pr-2';
	}

	onMount(async () => {
		modelsLoading = true;
		modelsLoadError = null;
		const modelsPromise = getModels()
			.catch((error) => {
				console.error('Failed to load models for interface preferences', error);
				modelsLoadError =
					typeof error === 'string'
						? error
						: error instanceof Error
							? error.message
							: 'Failed to load models';
			})
			.finally(() => {
				modelsLoading = false;
			});

		const languagesPromise = getLanguages().catch((error) => {
			console.error('Failed to load languages', error);
			return [];
		});

		const adminTaskConfigPromise =
			$user?.role === 'admin'
				? getTaskConfig(localStorage.token).catch((error) => {
						console.error('Failed to load task config', error);
						return null;
					})
				: Promise.resolve(null);

		const adminBannersPromise =
			$user?.role === 'admin'
				? getBanners(localStorage.token).catch((error) => {
						console.error('Failed to load banners', error);
						return [];
					})
				: Promise.resolve([]);

		// Appearance
		selectedTheme = normalizeTheme(localStorage.theme);
		if (localStorage.theme !== selectedTheme) {
			localStorage.theme = selectedTheme;
			applyTheme(selectedTheme);
		}
		languages = await languagesPromise;
		highlighterTheme = normalizeHighlighterTheme(
			$settings?.highlighterTheme ?? DEFAULT_HIGHLIGHTER_THEME
		);
		lang = $i18n.language;
		mermaidTheme = normalizeMermaidTheme($settings?.mermaidTheme ?? DEFAULT_MERMAID_THEME);
		notificationEnabled = $settings?.notificationEnabled ?? false;

		titleAutoGenerate = $settings?.title?.auto ?? true;
		autoTags = $settings?.autoTags ?? true;
		autoFollowUps = $settings?.autoFollowUps ?? true;

		detectArtifacts = $settings?.detectArtifacts ?? true;
		svgPreviewAutoOpen = $settings?.svgPreviewAutoOpen ?? ($settings?.detectArtifacts ?? true);
		responseAutoCopy = $settings?.responseAutoCopy ?? false;
		showChatTitleInTab = $settings?.showChatTitleInTab ?? true;
		enableMessageQueue = $settings?.enableMessageQueue ?? true;
		temporaryChatByDefault = $settings?.temporaryChatByDefault ?? false;
		transitionMode = resolveChatTransitionMode($settings);
		enableAutoScrollOnStreaming = $settings?.enableAutoScrollOnStreaming ?? true;
		insertSuggestionPrompt = $settings?.insertSuggestionPrompt ?? false;
		keepFollowUpPrompts = $settings?.keepFollowUpPrompts ?? false;
		insertFollowUpPrompt = $settings?.insertFollowUpPrompt ?? false;
		regenerateMenu = $settings?.regenerateMenu ?? true;
		renderMarkdownInPreviews = $settings?.renderMarkdownInPreviews ?? true;
		displayMultiModelResponsesInTabs = $settings?.displayMultiModelResponsesInTabs ?? false;
		stylizedPdfExport = $settings?.stylizedPdfExport ?? true;
		showFloatingActionButtons = $settings?.showFloatingActionButtons ?? true;
		floatingActionButtons = $settings?.floatingActionButtons ?? null;
		enableMemory = $settings?.memory ?? false;

		showUsername = $settings?.showUsername ?? false;
		showFeaturedAssistantsOnHome = $settings?.showFeaturedAssistantsOnHome ?? true;

		showEmojiInCall = $settings?.showEmojiInCall ?? false;
		voiceInterruption = $settings?.voiceInterruption ?? false;

		richTextInput = $settings?.richTextInput ?? true;
		promptAutocomplete = $settings?.promptAutocomplete ?? false;
		// Admin: load autocomplete generation task config
		const adminTaskConfig = await adminTaskConfigPromise;
		if (adminTaskConfig) {
			enableAutocompleteGeneration = adminTaskConfig.ENABLE_AUTOCOMPLETE_GENERATION ?? false;
			autocompleteGenerationInputMaxLength =
				adminTaskConfig.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH ?? -1;
		}
		showFormattingToolbar = $settings?.showFormattingToolbar ?? false;
		insertPromptAsRichText = $settings?.insertPromptAsRichText ?? false;
		largeTextAsFile = $settings?.largeTextAsFile ?? false;
		copyFormatted = $settings?.copyFormatted ?? false;
		globalSystemPrompt = $settings?.system ?? '';

		collapseCodeBlocks = $settings?.collapseCodeBlocks ?? false;
		collapseHistoricalLongResponses = $settings?.collapseHistoricalLongResponses ?? true;
		showInlineCitations = $settings?.showInlineCitations ?? true;
		showMessageOutline = $settings?.showMessageOutline ?? true;
		showFormulaQuickCopyButton = $settings?.showFormulaQuickCopyButton ?? true;
		expandDetails = $settings?.expandDetails ?? false;

		landingPageMode = $settings?.landingPageMode ?? '';
		chatBubble = $settings?.chatBubble ?? true;
		widescreenMode = $settings?.widescreenMode ?? false;
		scrollOnBranchChange = $settings?.scrollOnBranchChange ?? true;
		chatDirection = $settings?.chatDirection ?? 'auto';
		userLocation = $settings?.userLocation ?? false;

		notificationSound = $settings?.notificationSound ?? true;
		textScale = $settings?.textScale ?? null;
		setTextScale(textScale ?? 1);

		hapticFeedback = $settings?.hapticFeedback ?? false;
		ctrlEnterToSend = $settings?.ctrlEnterToSend ?? false;

		imageCompression = $settings?.imageCompression ?? false;
		imageCompressionSize = normalizeImageCompressionSize($settings?.imageCompressionSize);
		imageCompressionInChannels = $settings?.imageCompressionInChannels ?? true;
		imageCompressionPreset = detectPreset(imageCompressionSize);

		defaultModelId = getEffectiveDefaultModelId();

		backgroundImageUrl = $settings?.backgroundImageUrl ?? null;
		webSearchMode = getPreferredWebSearchMode($settings, 'off');
		iframeSandboxAllowSameOrigin = $settings?.iframeSandboxAllowSameOrigin ?? false;
		iframeSandboxAllowForms = $settings?.iframeSandboxAllowForms ?? false;

		// Admin: load banners and prompt suggestions
		if ($user?.role === 'admin') {
			banners = await adminBannersPromise;
			promptSuggestions = $config?.default_prompt_suggestions ?? [];
		}

		await tick();
		startSectionBaselineSync();
		syncSectionBaseline();
		loading = false;

		void modelsPromise;
	});

	onDestroy(() => {
		if (sectionBaselineSyncTimeout) {
			clearTimeout(sectionBaselineSyncTimeout);
		}
	});

	const toggleSection = async (section: SectionKey) => {
		expandedSections[section] = !expandedSections[section];
		if (expandedSections[section]) {
			await revealExpandedSection(sectionEls[section]);
		}
	};

	export const baseSections: Array<{ key: SectionKey; titleKey: string; iconPaths: string[]; badgeColor: string; iconColor: string }> = [
		{
			key: 'appearance',
			titleKey: '界面设置',
			badgeColor: 'bg-amber-50 dark:bg-amber-950/30',
			iconColor: 'text-amber-500 dark:text-amber-400',
			iconPaths: [
				'M12 2.25a.75.75 0 0 1 .75.75v2.25a.75.75 0 0 1-1.5 0V3a.75.75 0 0 1 .75-.75ZM7.5 12a4.5 4.5 0 1 1 9 0 4.5 4.5 0 0 1-9 0ZM18.894 6.166a.75.75 0 0 0-1.06-1.06l-1.591 1.59a.75.75 0 1 0 1.06 1.061l1.591-1.59ZM21.75 12a.75.75 0 0 1-.75.75h-2.25a.75.75 0 0 1 0-1.5H21a.75.75 0 0 1 .75.75ZM17.834 18.894a.75.75 0 0 0 1.06-1.06l-1.59-1.591a.75.75 0 1 0-1.061 1.06l1.59 1.591ZM12 18a.75.75 0 0 1 .75.75V21a.75.75 0 0 1-1.5 0v-2.25A.75.75 0 0 1 12 18ZM7.758 17.303a.75.75 0 0 0-1.061-1.06l-1.591 1.59a.75.75 0 0 0 1.06 1.061l1.591-1.59ZM6 12a.75.75 0 0 1-.75.75H3a.75.75 0 0 1 0-1.5h2.25A.75.75 0 0 1 6 12ZM6.697 7.757a.75.75 0 0 0 1.06-1.06l-1.59-1.591a.75.75 0 0 0-1.061 1.06l1.59 1.591Z'
			]
		},
		{
			key: 'layout',
			titleKey: '显示布局',
			badgeColor: 'bg-blue-50 dark:bg-blue-950/30',
			iconColor: 'text-blue-500 dark:text-blue-400',
			iconPaths: [
				'M2.25 5.25a3 3 0 0 1 3-3h13.5a3 3 0 0 1 3 3V15a3 3 0 0 1-3 3h-3v.257c0 .597.237 1.17.659 1.591l.621.622a.75.75 0 0 1-.53 1.28h-9a.75.75 0 0 1-.53-1.28l.621-.622a2.25 2.25 0 0 0 .659-1.59V18h-3a3 3 0 0 1-3-3V5.25Zm1.5 0v7.5a1.5 1.5 0 0 0 1.5 1.5h13.5a1.5 1.5 0 0 0 1.5-1.5v-7.5a1.5 1.5 0 0 0-1.5-1.5H5.25a1.5 1.5 0 0 0-1.5 1.5Z'
			]
		},
		{
			key: 'chat',
			titleKey: '对话功能',
			badgeColor: 'bg-indigo-50 dark:bg-indigo-950/30',
			iconColor: 'text-indigo-500 dark:text-indigo-400',
			iconPaths: [
				'M4.804 21.644A6.707 6.707 0 0 0 6 21.75a6.721 6.721 0 0 0 3.583-1.029c.774.182 1.584.279 2.417.279 5.322 0 9.75-3.97 9.75-9 0-5.03-4.428-9-9.75-9s-9.75 3.97-9.75 9c0 2.409 1.025 4.587 2.674 6.192.232.226.277.428.254.543a3.73 3.73 0 0 1-.814 1.686.75.75 0 0 0 .44 1.223ZM8.25 10.875a1.125 1.125 0 1 0 0 2.25 1.125 1.125 0 0 0 0-2.25ZM10.875 12a1.125 1.125 0 1 1 2.25 0 1.125 1.125 0 0 1-2.25 0Zm4.875-1.125a1.125 1.125 0 1 0 0 2.25 1.125 1.125 0 0 0 0-2.25Z'
			]
		},
		{
			key: 'input',
			titleKey: '输入设置',
			badgeColor: 'bg-cyan-50 dark:bg-cyan-950/30',
			iconColor: 'text-cyan-500 dark:text-cyan-400',
			iconPaths: [
				'M7.5 3.375c0-1.036.84-1.875 1.875-1.875h5.25c1.035 0 1.875.84 1.875 1.875v.375h1.125C18.832 3.75 19.75 4.668 19.75 5.875v12.25c0 1.207-.918 2.125-2.125 2.125H6.375A2.125 2.125 0 0 1 4.25 18.125V5.875C4.25 4.668 5.168 3.75 6.375 3.75H7.5v-.375ZM6.375 5.25a.625.625 0 0 0-.625.625v12.25c0 .345.28.625.625.625h11.25c.345 0 .625-.28.625-.625V5.875a.625.625 0 0 0-.625-.625H6.375ZM9.375 3c-.207 0-.375.168-.375.375V4.5h6V3.375A.375.375 0 0 0 14.625 3h-5.25ZM7.5 8.25a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-.75.75h-1.5a.75.75 0 0 1-.75-.75v-1.5Zm5.25-.75a.75.75 0 0 0 0 1.5h1.5a.75.75 0 0 0 0-1.5h-1.5ZM7.5 13.5a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-.75.75h-1.5a.75.75 0 0 1-.75-.75v-1.5Zm5.25-.75a.75.75 0 0 0 0 1.5h1.5a.75.75 0 0 0 0-1.5h-1.5Z'
			]
		},
		{
			key: 'advanced',
			titleKey: '高级选项',
			badgeColor: 'bg-slate-50 dark:bg-slate-950/30',
			iconColor: 'text-slate-500 dark:text-slate-400',
			iconPaths: [
				'M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z',
				'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'
			]
		}
	];

	let sections: Array<{ key: SectionKey; title: string; iconPaths: string[]; badgeColor: string; iconColor: string }> = [];
	$: sections = baseSections.map((s) => ({ ...s, title: $i18n.t(s.titleKey) }));
	$: filteredSections = activeSection ? sections.filter((s) => s.key === activeSection) : sections;
</script>

<ManageModal bind:show={showManageModal} />

{#if loading}
	<div class="h-full w-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{:else}
	<div class={rootClass}>
		<input
			bind:this={filesInputElement}
			bind:files={inputFiles}
			type="file"
			hidden
			accept="image/*"
			on:change={onBackgroundFileChange}
		/>

		<div class={bodyClass}>
			<div class="max-w-6xl mx-auto space-y-6">
			{#each filteredSections as s (s.key)}
				<div
					bind:this={sectionEls[s.key]}
					class={activeSection ? '' : `scroll-mt-2 transition-all duration-300 ${sectionDirtyState[s.key] ? 'glass-section glass-section-dirty' : 'glass-section'}`}
				>
					{#if !activeSection}
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between px-5 py-4">
						<button
							type="button"
							class="flex min-w-0 flex-1 items-center justify-between gap-4 text-left rounded-2xl"
							on:click={() => toggleSection(s.key)}
						>
							<div class="flex items-center gap-3">
								<div class="glass-icon-badge {s.badgeColor}">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-[18px] {s.iconColor}"
									>
										{#each s.iconPaths as pathD}
											<path fill-rule="evenodd" d={pathD} clip-rule="evenodd" />
										{/each}
									</svg>
								</div>
								<span class="text-base font-semibold text-gray-800 dark:text-gray-100">{s.title}</span>
							</div>
							<div
								class="transform transition-transform duration-200 {expandedSections[s.key]
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-5 text-gray-400 dark:text-gray-500" />
							</div>
						</button>
					</div>
					{/if}

					{#if activeSection || expandedSections[s.key]}
						<div transition:slide={{ duration: 200, easing: quintOut }} class="{activeSection ? '' : 'px-5 pb-5'} space-y-3">
							{#if s.key === 'appearance'}
								<InlineDirtyActions
									dirty={dirtySections.appearance}
									saving={appearanceSaving}
									saveAsSubmit={false}
									on:reset={resetAppearanceChanges}
									on:save={saveAppearanceChanges}
								/>
								<div class="space-y-3">
									<div class="space-y-2">
										<div class="glass-item p-4">
											<div class="text-sm font-medium mb-2">{$i18n.t('Theme')}</div>
											<ThemeSelector bind:value={selectedTheme} />
										</div>
										<div class="glass-item p-4 space-y-4">
											<div
												class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
											>
												<div class="text-sm font-medium">
													{$i18n.t('Code Highlight Theme')}
												</div>
												<HaloSelect
													bind:value={highlighterTheme}
													className="w-full sm:w-72"
													searchEnabled={true}
													searchPlaceholder={$i18n.t('Search')}
													noResultsText={$i18n.t('No results found')}
													options={LOBE_HIGHLIGHTER_THEMES.map((item) => ({
														value: item.id,
														label: getThemeOptionLabel(item.displayName, item.id)
													}))}
												/>
											</div>
											<CodeHighlightThemePreview themeId={highlighterTheme} />
										</div>
										<div class="glass-item p-4 space-y-4">
											<div
												class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
											>
												<div class="text-sm font-medium">
													{$i18n.t('Mermaid Theme')}
												</div>
												<HaloSelect
													bind:value={mermaidTheme}
													className="w-full sm:w-72"
													options={LOBE_MERMAID_THEMES.map((item) => ({
														value: item.id,
														label: getThemeOptionLabel(item.displayName, item.id)
													}))}
												/>
											</div>
											<MermaidThemePreview themeId={mermaidTheme} />
										</div>
										<div class="space-y-2">
											<div class="glass-item px-4 py-4 space-y-4">
												<div
													class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
												>
													<div class="space-y-1">
														<div class="text-sm font-medium">
															{$i18n.t('Transition Animation')}
														</div>
														<div class="text-xs text-gray-500 dark:text-gray-400">
															{$i18n.t('Choose how streaming chat messages appear')}
														</div>
													</div>
													<div
														class="inline-flex items-center gap-1 self-start rounded-xl border border-gray-200/70 bg-white/90 p-1 shadow-xs dark:border-gray-700/60 dark:bg-gray-900/70"
													>
														{#each transitionModeOptions as option}
															<button
																type="button"
																class={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
																	transitionMode === option.value
																		? 'bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white'
																		: 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100'
																}`}
																on:click={() => {
																	transitionMode = option.value;
																}}
															>
																{$i18n.t(option.label)}
															</button>
														{/each}
													</div>
												</div>
												{#key transitionMode}
													<ChatTransitionPreview mode={transitionMode} />
												{/key}
											</div>
											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="space-y-1">
													<div class="text-sm font-medium">
														{$i18n.t('Auto-scroll during streaming')}
													</div>
													<div class="text-xs text-gray-500 dark:text-gray-400">
														{$i18n.t('Keep the viewport pinned to the latest tokens while the reply is streaming')}
													</div>
												</div>
												<Switch bind:state={enableAutoScrollOnStreaming} />
											</div>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Language')}
											</div>
											<HaloSelect
												bind:value={lang}
												options={languages.map((l) => ({ value: l['code'], label: l['title'] }))}
											/>
										</div>
										{#if $i18n.language === 'en-US'}
											<div class="text-xs text-gray-400 dark:text-gray-500 pl-1">
												Couldn't find your language?
												<a
													class="text-gray-500 dark:text-gray-400 font-medium underline"
													href="https://github.com/ztx888/HaloWebUI/blob/main/docs/CONTRIBUTING.md#-translations-and-internationalization"
													target="_blank"
												>
													Help us translate Halo WebUI!
												</a>
											</div>
										{/if}
										<div class="glass-item p-4">
											<div
												class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
											>
												<div class="text-sm font-medium">
													{$i18n.t('Chat Background Image')}
												</div>
												<div class="flex flex-wrap items-center gap-2 shrink-0">
													<button
														type="button"
														class="px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
														on:click={() => filesInputElement?.click()}
													>
														{$i18n.t('Choose')}
													</button>
													<button
														type="button"
														class="px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
														on:click={clearBackgroundImage}
													>
														{$i18n.t('Clear')}
													</button>
												</div>
											</div>
											{#if backgroundImageUrl}
												<div
													class="mt-3 rounded-lg overflow-hidden border border-gray-200 dark:border-gray-800"
												>
													<img
														src={backgroundImageUrl}
														alt="Background"
														class="w-full h-40 object-cover"
													/>
												</div>
											{:else}
												<div class="mt-3 text-xs text-gray-500">
													{$i18n.t('No background image selected.')}
												</div>
											{/if}
										</div>

										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('UI Scale')}
											</div>
											<div class="flex items-center gap-2.5 min-w-[16rem]">
												{#if textScale !== null}
													<input
														class="flex-1 h-1.5 accent-blue-500 cursor-pointer"
														type="range"
														min="1"
														max="1.5"
														step={0.01}
														bind:value={textScale}
													/>
													<span
														class="text-xs font-mono text-gray-500 dark:text-gray-400 w-10 text-right tabular-nums"
														>{Math.round((textScale ?? 1) * 100)}%</span
													>
												{/if}
												<button
													type="button"
													class="px-2.5 py-1 text-xs rounded-md border transition whitespace-nowrap
																{textScale === null
														? 'border-blue-300 dark:border-blue-600 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
														: 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-black/5 dark:hover:bg-white/5'}"
													on:click={() => {
														textScale = textScale === null ? 1 : null;
													}}
												>
													{textScale === null ? $i18n.t('Default') : $i18n.t('Reset')}
												</button>
											</div>
										</div>

									</div>
								</div>
							{:else if s.key === 'layout'}
								<InlineDirtyActions
									dirty={dirtySections.layout}
									saving={layoutSaving}
									saveAsSubmit={false}
									on:reset={resetLayoutChanges}
									on:save={saveLayoutChanges}
								/>
								<div
									class="space-y-3"
								>
								<div class="space-y-2">
									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Landing Page Mode')}
										</div>
										<HaloSelect
											bind:value={landingPageMode}
											options={[
												{ value: '', label: $i18n.t('Default') },
												{ value: 'chat', label: $i18n.t('Chat') }
											]}
										/>
									</div>

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Show featured assistants on home page')}
										</div>
										<Switch
											bind:state={showFeaturedAssistantsOnHome}
										/>
									</div>

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Chat direction')}
										</div>
										<HaloSelect
											bind:value={chatDirection}
											options={[
												{ value: 'auto', label: $i18n.t('Auto') },
												{ value: 'LTR', label: $i18n.t('LTR') },
												{ value: 'RTL', label: $i18n.t('RTL') }
											]}
										/>
									</div>

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Notifications')}
										</div>
										<Switch bind:state={notificationEnabled} />
									</div>

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Widescreen Mode')}
										</div>
										<Switch
											bind:state={widescreenMode}
										/>
									</div>

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Notification Sound')}
										</div>
										<Switch
											bind:state={notificationSound}
										/>
									</div>

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Chat Bubble UI')}
										</div>
										<Switch
											bind:state={chatBubble}
										/>
									</div>

									{#if !chatBubble}
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Display the username instead of You in the Chat')}
											</div>
											<Switch
												bind:state={showUsername}
											/>
										</div>
									{/if}

									<div class="flex items-center justify-between glass-item px-4 py-3">
										<div class="text-sm font-medium">
											{$i18n.t('Display chat title in tab')}
										</div>
										<Switch
											bind:state={showChatTitleInTab}
										/>
									</div>

									{#if $user?.role === 'admin'}
										<!-- Banners -->
										<div class="glass-item p-4">
											<div class="flex w-full justify-between items-center mb-3">
												<div class="text-sm font-medium">{$i18n.t('Banners')}</div>
												<button
													class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
													type="button"
													aria-label="Add Banner"
													on:click={() => {
														if (banners.length === 0 || banners.at(-1)?.content !== '') {
															banners = [
																...banners,
																{
																	id: uuidv4(),
																	type: '',
																	title: '',
																	content: '',
																	dismissible: true,
																	timestamp: Math.floor(Date.now() / 1000)
																}
															];
														}
													}}
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														viewBox="0 0 20 20"
														fill="currentColor"
														class="w-4 h-4"
													>
														<path
															d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z"
														/>
													</svg>
												</button>
											</div>

											<div class="flex flex-col space-y-2">
												{#each banners as banner, bannerIdx}
													<div class="flex justify-between items-center">
														<div
															class="flex flex-row flex-1 border rounded-lg border-gray-200 dark:border-gray-700 overflow-hidden"
														>
															<HaloSelect
																bind:value={banner.type}
																on:change={touchBanners}
																options={[
																	{ value: 'info', label: $i18n.t('Info') },
																	{ value: 'warning', label: $i18n.t('Warning') },
																	{ value: 'error', label: $i18n.t('Error') },
																	{ value: 'success', label: $i18n.t('Success') }
																]}
																placeholder={$i18n.t('Type')}
																className="w-fit capitalize rounded-l-lg text-xs"
															/>

															<input
																class="flex-1 py-2 px-3 text-xs bg-transparent outline-hidden"
																placeholder={$i18n.t('Content')}
																bind:value={banner.content}
																on:input={touchBanners}
															/>

															<div class="flex items-center px-2">
																<Tooltip
																	content={$i18n.t('Dismissible')}
																	className="flex h-fit items-center"
																>
																	<Switch bind:state={banner.dismissible} on:change={touchBanners} />
																</Tooltip>
															</div>
														</div>

														<button
															class="p-2 ml-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
															type="button"
															on:click={() => {
																banners.splice(bannerIdx, 1);
																banners = banners;
															}}
														>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 20 20"
																fill="currentColor"
																class="w-4 h-4"
															>
																<path
																	d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
																/>
															</svg>
														</button>
													</div>
												{/each}
											</div>
										</div>
									{/if}

								</div>
								</div>
							{:else if s.key === 'input'}
								<InlineDirtyActions
									dirty={dirtySections.input}
									saving={inputSaving}
									saveAsSubmit={false}
									on:reset={resetInputChanges}
									on:save={saveInputChanges}
								/>
								<div
									class="space-y-3"
								>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Rich Text Input for Chat')}
											</div>
											<Switch
												bind:state={richTextInput}
											/>
										</div>
										{#if $user?.role === 'admin'}
											<div class="glass-item px-4 py-3 space-y-3">
												<div class="flex items-center justify-between">
													<div class="text-sm font-medium">
														{$i18n.t('Autocomplete Generation')}
													</div>
													<Tooltip content={$i18n.t('Enable autocomplete generation for chat messages')}>
														<Switch
															bind:state={enableAutocompleteGeneration}
														/>
													</Tooltip>
												</div>
												{#if enableAutocompleteGeneration}
													<div>
														<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
															{$i18n.t('Autocomplete Generation Input Max Length')}
														</div>
														<input
															class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
															bind:value={autocompleteGenerationInputMaxLength}
															placeholder={$i18n.t(
																'-1 for no limit, or a positive integer for a specific limit'
															)}
														/>
													</div>
												{/if}
											</div>
										{/if}
										{#if $config?.features?.enable_autocomplete_generation || ($user?.role === 'admin' && enableAutocompleteGeneration)}
											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="text-sm font-medium">
													{$i18n.t('Prompt Autocompletion')}
												</div>
												<Switch
													bind:state={promptAutocomplete}
												/>
											</div>
										{/if}
										{#if richTextInput}
											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="text-sm font-medium">
													{$i18n.t('Show Formatting Toolbar')}
												</div>
												<Switch
													bind:state={showFormattingToolbar}
												/>
											</div>
											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="text-sm font-medium">
													{$i18n.t('Insert Prompt as Rich Text')}
												</div>
												<Switch
													bind:state={insertPromptAsRichText}
												/>
											</div>
										{/if}
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Paste Large Text as File')}
											</div>
											<Switch
												bind:state={largeTextAsFile}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Copy Formatted Text')}
											</div>
											<Switch
												bind:state={copyFormatted}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Enter Key Behavior')}
											</div>
											<HaloSelect
												value={ctrlEnterToSend ? 'ctrl_enter' : 'enter'}
												on:change={onCtrlEnterBehaviorChange}
												options={[
													{ value: 'enter', label: $i18n.t('Enter to Send') },
													{ value: 'ctrl_enter', label: $i18n.t('Ctrl+Enter to Send') }
												]}
											/>
										</div>
									</div>

									{#if $user?.role === 'admin'}
										<!-- Default Prompt Suggestions -->
										<div class="glass-item p-4">
											<div class="flex w-full justify-between items-center mb-3">
												<div class="text-sm font-medium">{$i18n.t('Default Prompt Suggestions')}</div>
												<button
													class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
													type="button"
													on:click={() => {
														if (
															promptSuggestions.length === 0 ||
															promptSuggestions.at(-1)?.content !== ''
														) {
															promptSuggestions = [
																...promptSuggestions,
																{ content: '', title: ['', ''] }
															];
														}
													}}
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														viewBox="0 0 20 20"
														fill="currentColor"
														class="w-4 h-4"
													>
														<path
															d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z"
														/>
													</svg>
												</button>
											</div>

											<div class="flex flex-col space-y-2">
												{#each promptSuggestions as prompt, promptIdx}
													<div
														class="flex border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
													>
														<div class="flex flex-col flex-1">
															<div class="flex border-b border-gray-200 dark:border-gray-700">
																<input
																	class="flex-1 px-3 py-2 text-xs bg-transparent outline-hidden border-r border-gray-200 dark:border-gray-700"
																	placeholder={$i18n.t('Title (e.g. Tell me a fun fact)')}
																	bind:value={prompt.title[0]}
																/>
																<input
																	class="flex-1 px-3 py-2 text-xs bg-transparent outline-hidden"
																	placeholder={$i18n.t('Subtitle (e.g. about the Roman Empire)')}
																	bind:value={prompt.title[1]}
																/>
															</div>
															<textarea
																class="px-3 py-2 text-xs w-full bg-transparent outline-hidden resize-none"
																placeholder={$i18n.t(
																	'Prompt (e.g. Tell me a fun fact about the Roman Empire)'
																)}
																rows="2"
																bind:value={prompt.content}
															/>
														</div>
														<button
															class="px-3 flex items-center hover:bg-gray-100 dark:hover:bg-gray-800 transition"
															type="button"
															on:click={() => {
																promptSuggestions.splice(promptIdx, 1);
																promptSuggestions = promptSuggestions;
															}}
														>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 20 20"
																fill="currentColor"
																class="w-4 h-4"
															>
																<path
																	d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
																/>
															</svg>
														</button>
													</div>
												{/each}
											</div>

											{#if promptSuggestions.length > 0}
												<div class="text-xs text-gray-500 mt-3">
													{$i18n.t(
														'Adjusting these settings will apply changes universally to all users.'
													)}
												</div>
											{/if}
										</div>
									{/if}

									<div class="glass-item p-4">
										<div class="flex items-start gap-3">
											<div class="shrink-0 size-9 rounded-2xl border border-sky-200/80 bg-linear-to-br from-sky-50 to-blue-100 text-sky-600 shadow-xs dark:border-sky-800/60 dark:from-sky-950/40 dark:to-blue-950/30 dark:text-sky-300 flex items-center justify-center">
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 24 24"
													fill="currentColor"
													class="size-4.5"
												>
													<path
														fill-rule="evenodd"
														d="M7.5 3.75A2.25 2.25 0 0 0 5.25 6v12A2.25 2.25 0 0 0 7.5 20.25h9A2.25 2.25 0 0 0 18.75 18V8.56a2.25 2.25 0 0 0-.659-1.591l-2.56-2.56A2.25 2.25 0 0 0 13.94 3.75H7.5Zm3 4.5a.75.75 0 0 1 .75-.75h2.25a.75.75 0 0 1 0 1.5h-2.25a.75.75 0 0 1-.75-.75Zm-1.5 3a.75.75 0 0 1 .75-.75h4.5a.75.75 0 0 1 0 1.5h-4.5a.75.75 0 0 1-.75-.75Zm0 3a.75.75 0 0 1 .75-.75h4.5a.75.75 0 0 1 0 1.5h-4.5a.75.75 0 0 1-.75-.75Z"
														clip-rule="evenodd"
													/>
												</svg>
											</div>
											<div class="min-w-0 flex-1">
												<div class="flex flex-wrap items-center gap-2">
													<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
														{$i18n.t('Global Default System Prompt')}
													</div>
													<span class="inline-flex items-center rounded-full border border-sky-200/80 bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:border-sky-800/70 dark:bg-sky-950/40 dark:text-sky-300">
														{$i18n.t('Applies to New Chats')}
													</span>
												</div>
												<p class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
													{$i18n.t(
														'New chats without a custom current-chat system prompt will inherit this setting. A current chat system prompt can override it.'
													)}
												</p>
											</div>
										</div>

										<div class="mt-3">
											<Textarea
												bind:value={globalSystemPrompt}
												rows={5}
												minSize={140}
												placeholder={$i18n.t(
													'Leave empty if you do not want a global default system prompt.'
												)}
												className="w-full rounded-xl border border-gray-200/80 bg-white/80 px-3.5 py-3 text-sm leading-6 text-gray-800 outline-hidden transition-colors focus:border-sky-300/80 dark:border-gray-700/70 dark:bg-gray-900/60 dark:text-gray-200 dark:focus:border-sky-500/60"
											/>
										</div>

										<div class="mt-3 rounded-xl border border-dashed border-gray-200/90 bg-gray-50/80 px-3 py-2 text-xs leading-5 text-gray-500 dark:border-gray-700/70 dark:bg-gray-900/40 dark:text-gray-400">
											{$i18n.t(
												'This default is not copied into each chat. Chats without an override inherit it dynamically.'
											)}
										</div>
									</div>

								</div>
							{:else if s.key === 'chat'}
								<InlineDirtyActions
									dirty={dirtySections.chat}
									saving={chatSaving}
									saveAsSubmit={false}
									on:reset={resetChatChanges}
									on:save={saveChatChanges}
								/>
								<div
									class="space-y-3"
								>
									<!-- Personal Default Model -->
									<div class="space-y-2">
										<div class="glass-item px-4 py-3">
											<div class="flex items-center justify-between gap-3">
												<div class="min-w-0">
													<div class="text-sm font-medium">
														{$i18n.t('Default Model')}
													</div>
													<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
														{$i18n.t('Applies to this account only')}
													</div>
												</div>
												<HaloSelect
													className="w-60 shrink-0"
													bind:value={defaultModelId}
													searchEnabled={true}
													placeholder={$i18n.t('Select a model')}
													searchPlaceholder={$i18n.t('Search a model')}
													noResultsText={$i18n.t('No results found')}
													options={[
														{ value: '', label: $i18n.t('None') },
														...($models ?? []).map((m) => ({
															value: getModelSelectionId(m),
															label: getModelChatDisplayName(m)
														}))
													]}
												/>
											</div>
											{#if modelsLoading && ($models?.length ?? 0) === 0}
												<div class="mt-2 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
													<Spinner className="size-3.5" />
													<span>{$i18n.t('Loading...')}</span>
												</div>
											{:else if modelsLoadError && ($models?.length ?? 0) === 0}
												<div class="mt-2 text-xs text-amber-600 dark:text-amber-400">
													{modelsLoadError}
												</div>
											{/if}
										</div>
									</div>

									<!-- Sub-group A: Auto Generation -->
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
										{$i18n.t('Auto Generation')}
									</div>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Title Auto-Generation')}
											</div>
											<Switch
												bind:state={titleAutoGenerate}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Follow-Up Auto-Generation')}
											</div>
											<Switch
												bind:state={autoFollowUps}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Chat Tags Auto-Generation')}
											</div>
											<Switch
												bind:state={autoTags}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Detect Artifacts Automatically')}
											</div>
											<Switch
												bind:state={detectArtifacts}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Auto-Open SVG Preview')}
											</div>
											<Switch
												bind:state={svgPreviewAutoOpen}
											/>
										</div>
									</div>

									<!-- Sub-group B: Display & Rendering -->
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1 mt-3">
										{$i18n.t('Display')}
									</div>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Always Collapse Code Blocks')}
											</div>
											<Switch
												bind:state={collapseCodeBlocks}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Collapse Historical Long Responses')}
											</div>
											<Switch
												bind:state={collapseHistoricalLongResponses}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{tr('显示正文引用标签', 'Show Inline Citations')}
											</div>
											<Switch
												bind:state={showInlineCitations}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Show Message Outline')}
											</div>
											<Switch
												bind:state={showMessageOutline}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Show Formula Quick Copy Button')}
											</div>
											<Switch
												bind:state={showFormulaQuickCopyButton}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Expand Tool and Detail Blocks by Default')}
											</div>
											<Switch
												bind:state={expandDetails}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Render Markdown in Previews')}
											</div>
											<Switch
												bind:state={renderMarkdownInPreviews}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Display Multi-model Responses in Tabs')}
											</div>
											<Switch
												bind:state={displayMultiModelResponsesInTabs}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Scroll to bottom when switching between branches')}
											</div>
											<Switch
												bind:state={scrollOnBranchChange}
											/>
										</div>
										<div class="glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{tr('PDF 导出说明', 'PDF export note')}
											</div>
											<div class="text-xs text-gray-500 dark:text-gray-400 mt-1.5 leading-5">
												{tr(
													'PDF 现已改为文档型导出，优先保证文字、列表、代码块和图片的稳定排版，不再跟随当前聊天页面视觉样式。',
													'PDF export now uses a document layout to keep text, lists, code blocks, and images stable instead of mirroring the current chat page style.'
												)}
											</div>
										</div>
									</div>

									<!-- Sub-group C: Interaction -->
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1 mt-3">
										{$i18n.t('Interaction')}
									</div>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Enable Message Queue')}
											</div>
											<Switch
												bind:state={enableMessageQueue}
											/>
										</div>
										{#if $user?.role === 'admin' || $user?.permissions?.chat?.temporary}
											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="text-sm font-medium">
													{$i18n.t('Temporary Chat by Default')}
												</div>
												<Switch
													bind:state={temporaryChatByDefault}
												/>
											</div>
										{/if}
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Auto-Copy Response to Clipboard')}
											</div>
											<Switch
												bind:state={responseAutoCopy}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Insert Suggestion Prompt to Input')}
											</div>
											<Switch
												bind:state={insertSuggestionPrompt}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Keep Follow-Up Prompts in Chat')}
											</div>
											<Switch
												bind:state={keepFollowUpPrompts}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Insert Follow-Up Prompt to Input')}
											</div>
											<Switch
												bind:state={insertFollowUpPrompt}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Regenerate Menu')}
											</div>
											<Switch
												bind:state={regenerateMenu}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Floating Quick Actions')}
											</div>
											<Switch
												bind:state={showFloatingActionButtons}
											/>
										</div>
										{#if showFloatingActionButtons}
											<div class="space-y-2">
												<div class="flex items-center justify-between gap-4">
													<div class="text-sm font-medium">
														{$i18n.t('Quick Actions')}
													</div>
													<HaloSelect
														value={floatingActionButtons === null ? 'default' : 'custom'}
														on:change={(e) => {
															setFloatingActionsMode(
																e.detail.value === 'custom' ? 'custom' : 'default'
															);
														}}
														options={[
															{ value: 'default', label: $i18n.t('Default') },
															{ value: 'custom', label: $i18n.t('Custom') }
														]}
													/>
												</div>

												{#if floatingActionButtons !== null}
													{#if floatingActionButtons.length === 0}
														<div class="text-xs text-gray-500">
															{$i18n.t('No action buttons configured yet.')}
														</div>
													{/if}
													{#each floatingActionButtons as button, buttonIdx (button.id)}
														<div
															class="rounded-lg border border-gray-200 dark:border-gray-700 p-2.5 space-y-2"
														>
															<div class="grid grid-cols-2 gap-2">
																<input
																	class=""
																	placeholder={$i18n.t('Button Label')}
																	bind:value={button.label}
																	on:change={touchFloatingActions}
																/>
																<input
																	class=""
																	placeholder={$i18n.t('Button ID')}
																	bind:value={button.id}
																	on:change={touchFloatingActions}
																/>
															</div>
															<div class="flex items-center justify-between gap-2">
																<div class="text-xs text-gray-500">
																	{$i18n.t('Require user input')}
																</div>
																<Switch
																	bind:state={button.input}
																	on:change={touchFloatingActions}
																/>
															</div>
															<textarea
																rows="3"
																class="w-full dark:bg-gray-850 rounded-lg px-3 py-2 text-sm bg-gray-50 outline-none border border-gray-200 dark:border-gray-700"
																placeholder={$i18n.t('Button Prompt')}
																bind:value={button.prompt}
																on:change={touchFloatingActions}
															/>
															<div class="flex justify-end">
																<button
																	type="button"
																	class="px-2.5 py-1.5 text-xs rounded-md border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
																	on:click={() => removeFloatingAction(button.id)}
																>
																	{$i18n.t('Remove')}
																</button>
															</div>
														</div>
													{/each}
													<div class="flex justify-end">
														<button
															type="button"
															class="px-3 py-1.5 text-xs rounded-md border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
															on:click={addFloatingAction}
														>
															{$i18n.t('Add')}
														</button>
													</div>
												{/if}
											</div>
										{/if}
									</div>

									<!-- Sub-group D: Memory -->
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1 mt-3">
										<Tooltip
											content={$i18n.t(
												'This is an experimental feature, it may not function as expected and is subject to change at any time.'
											)}
										>
											{$i18n.t('Memory')}
											<span class="normal-case">({$i18n.t('Experimental')})</span>
										</Tooltip>
									</div>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Enable Memory')}
											</div>
											<Switch
												bind:state={enableMemory}
											/>
										</div>
									</div>
									<div class="text-xs text-gray-500 dark:text-gray-400 mt-1.5 pl-1">
										{$i18n.t(
											"You can personalize your interactions with LLMs by adding memories through the 'Manage' button below, making them more helpful and tailored to you."
										)}
									</div>
									<div class="mt-2">
										<button
											type="button"
											class="px-3.5 py-1.5 font-medium hover:bg-black/5 dark:hover:bg-white/5 outline outline-1 outline-gray-300 dark:outline-gray-800 rounded-3xl text-sm"
											on:click={() => {
												showManageModal = true;
											}}
										>
											{$i18n.t('Manage')}
										</button>
									</div>

									<!-- Sub-group E: Voice & Media -->
									<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1 mt-3">
										{$i18n.t('Voice & Media')}
									</div>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Allow Voice Interruption in Call')}
											</div>
											<Switch
												bind:state={voiceInterruption}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Display Emoji in Call')}
											</div>
											<Switch
												bind:state={showEmojiInCall}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Image Compression')}
											</div>
											<Switch
												bind:state={imageCompression}
											/>
										</div>
										{#if imageCompression}
											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="text-sm font-medium">
													{$i18n.t('Image Max Compression Size')}
												</div>
												<HaloSelect
													bind:value={imageCompressionPreset}
													on:change={(e) => {
														const preset = e.detail;
														if (preset !== 'custom') {
															applyPreset(preset);
														}
													}}
													options={[
														{
															value: 'auto',
															label: tr(
																'自动（仅压缩文件大小）',
																'Auto (compress file size only)'
															)
														},
														{
															value: 'standard',
															label: tr('标准 (1920x1080)', 'Standard (1920x1080)')
														},
														{
															value: 'medium',
															label: tr('中等 (1280x720)', 'Medium (1280x720)')
														},
														{
															value: 'small',
															label: tr('小图 (800x600)', 'Small (800x600)')
														},
														{
															value: 'custom',
															label: tr('自定义', 'Custom')
														}
													]}
													className="w-64"
												/>
											</div>

											{#if imageCompressionPreset === 'custom'}
												<div class="flex items-center justify-between glass-item px-4 py-3">
													<div class="text-sm font-medium">
														{tr('自定义尺寸', 'Custom size')}
													</div>
													<div class="flex items-center gap-2">
														<input
															bind:value={imageCompressionSize.width}
															type="number"
															min="0"
															placeholder={tr('宽', 'Width')}
															class="w-24 dark:bg-gray-850 rounded-lg px-3 py-2 text-sm bg-gray-50 outline-none border border-gray-200 dark:border-gray-700 text-center"
														/>
														<span class="text-gray-500">x</span>
														<input
															bind:value={imageCompressionSize.height}
															type="number"
															min="0"
															placeholder={tr('高', 'Height')}
															class="w-24 dark:bg-gray-850 rounded-lg px-3 py-2 text-sm bg-gray-50 outline-none border border-gray-200 dark:border-gray-700 text-center"
														/>
													</div>
												</div>
											{/if}

											<div class="flex items-center justify-between glass-item px-4 py-3">
												<div class="text-sm font-medium">
													{$i18n.t('Compress Images in Channels')}
												</div>
												<Switch
													bind:state={imageCompressionInChannels}
												/>
											</div>
										{/if}
									</div>
								</div>
							{:else if s.key === 'advanced'}
								<InlineDirtyActions
									dirty={dirtySections.advanced}
									saving={advancedSaving}
									saveAsSubmit={false}
									on:reset={resetAdvancedChanges}
									on:save={saveAdvancedChanges}
								/>
								<div
									class="space-y-3"
								>
									<div class="space-y-2">
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Allow User Location')}
											</div>
											<Switch bind:state={userLocation} />
										</div>
											<div class="glass-item px-4 py-3 space-y-2">
												<div class="flex items-center justify-between gap-4">
													<div class="text-sm font-medium">
														{$i18n.t('Web Search in Chat')}
													</div>
													<HaloSelect
														value={webSearchMode}
														options={webSearchModeOptions.map((option) => ({
															value: option.value,
															label: option.label,
															description: option.description,
															descriptionTone: option.descriptionTone,
															disabled: option.disabled,
															badge: option.badge
														}))}
														className="w-52"
														on:change={onWebSearchChange}
													/>
												</div>
												{#if currentWebSearchModeDescription}
													<div class="text-xs leading-5 text-gray-500 dark:text-gray-400">
														{currentWebSearchModeDescription}
													</div>
												{/if}
												{#if webSearchAvailabilityNote}
													<div class="text-xs leading-5 text-gray-500 dark:text-gray-400">
														{webSearchAvailabilityNote}
													</div>
												{/if}
											</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('iframe Sandbox Allow Same Origin')}
											</div>
											<Switch
												bind:state={iframeSandboxAllowSameOrigin}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('iframe Sandbox Allow Forms')}
											</div>
											<Switch
												bind:state={iframeSandboxAllowForms}
											/>
										</div>
										<div class="flex items-center justify-between glass-item px-4 py-3">
											<div class="text-sm font-medium">
												{$i18n.t('Haptic Feedback')} ({$i18n.t('Only available on Android')})
											</div>
											<Switch
												bind:state={hapticFeedback}
											/>
										</div>
									</div>
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
			</div>
		</div>
	</div>
{/if}
