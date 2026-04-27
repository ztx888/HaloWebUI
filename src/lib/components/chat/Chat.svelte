<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';
	import mermaid from 'mermaid';
	import { PaneGroup, Pane, PaneResizer } from 'paneforge';

	import { getContext, onDestroy, onMount, setContext, tick } from 'svelte';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { get, writable, type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { WEBUI_BASE_URL, WEBUI_API_BASE_URL } from '$lib/constants';

	import {
		chatId,
		chatListRefreshRevision,
		chatListRefreshTarget,
		chats,
		config,
		type Model,
		models,
		tags as allTags,
		settings,
		showSidebar,
		WEBUI_NAME,
		banners,
		user,
		socket,
		showControls,
		showCallOverlay,
		currentChatPage,
		temporaryChatEnabled,
		mobile,
		showOverview,
		chatTitle,
		showArtifacts,
		tools,
		skills as skillsStore,
		toolServers,
		activeChatIds,
		overviewFocusedMessageId,
		selectedAssistantScene
	} from '$lib/stores';
	import {
		convertMessagesToHistory,
		copyToClipboard,
		getMessageContentParts,
		createMessagesList,
		extractSentencesForAudio,
		promptTemplate,
		splitStream,
		sleep,
		removeDetails,
		getPromptVariables,
		processDetails
	} from '$lib/utils';
	import {
		createEmptySelectionThreads,
		normalizeSelectionThreads,
		type PersistedSelectionThreads
	} from '$lib/utils/selection-threads';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import {
		buildModelIdentityLookup,
		getModelCleanId,
		getModelRef,
		getModelSelectionId,
		resolveModelSelectionId
	} from '$lib/utils/model-identity';
	import {
		buildModelSelectionHint,
		resolveChatModelSelection,
		resolveChatModelSelections,
		type ChatModelResolution
	} from '$lib/utils/chat-model-recovery';
	import {
		type ChatAssistantSnapshot,
		PENDING_ASSISTANT_STORAGE_KEY,
		toChatAssistantSnapshot
	} from '$lib/utils/chat-assistants';
	import {
		getTemporaryChatAccess,
		getTemporaryChatNavigationPath,
		persistTemporaryChatOverride,
		resolveTemporaryChatEnabled
	} from '$lib/utils/temporary-chat';
	import {
		getPreferredWebSearchMode,
		normalizeWebSearchMode,
		normalizeWebSearchModeSource,
		type WebSearchMode,
		type WebSearchModeSource
	} from '$lib/utils/web-search-mode';
	import { getFunctionPipeRootId } from '$lib/utils/image-generation';
	import { isDedicatedImageGenerationModel } from '$lib/utils/model-capabilities';
	import { applyUserSettingsSnapshot } from '$lib/utils/user-settings';
	import { buildWebSearchModeOptions } from '$lib/utils/native-web-search';

	import { generateChatCompletion } from '$lib/apis/ollama';
	import {
		addTagById,
		branchChatById,
		createNewChat,
		deleteTagById,
		deleteTagsById,
		getAllTags,
		getChatById,
		getChatContextById,
		getChatList,
		updateChatById,
		updateChatComposerStateById
	} from '$lib/apis/chats';
	import { getModelById as getWorkspaceModelById } from '$lib/apis/models';
	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { processWeb, processWebSearch, processYoutubeVideo } from '$lib/apis/retrieval';
	import { createOpenAITextStream } from '$lib/apis/streaming';
	import { queryMemory } from '$lib/apis/memories';
	import { uploadFile } from '$lib/apis/files';
	import { getAndUpdateUserLocation, getUserSettings } from '$lib/apis/users';
	import {
		chatCompleted,
		generateQueries,
		chatAction,
		generateMoACompletion,
		stopTask
	} from '$lib/apis';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { ensureModels } from '$lib/services/models';

	import Banner from '../common/Banner.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';
	import MessageQueue from '$lib/components/chat/MessageInput/MessageQueue.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from '$lib/components/chat/Navbar.svelte';
	import ChatControls from './ChatControls.svelte';
	import EventConfirmDialog from '../common/ConfirmDialog.svelte';
	import Placeholder from './Placeholder.svelte';
	import NotificationToast from '../NotificationToast.svelte';
	import Spinner from '../common/Spinner.svelte';
	import {
		buildIgnoredFailedFilesMessage,
		getFileUploadDiagnostic,
		getLocalizedFileUploadDiagnostic,
		isFailedUploadFile,
		localizeFileUploadError
	} from '$lib/utils/file-upload-errors';

	const MESSAGE_OUTLINE_IDLE_MS = 900;
	const MESSAGE_OUTLINE_SCROLL_INTENT_WINDOW_MS = 220;
	const MESSAGE_OUTLINE_SCROLLBAR_DRAG_PRIME_MS = 1400;
	const MESSAGE_OUTLINE_SCROLL_KEYS = new Set([
		'ArrowUp',
		'ArrowDown',
		'PageUp',
		'PageDown',
		'Home',
		'End',
		' ',
		'Spacebar'
	]);

	type MessageOutlineVisibilityContext = {
		scrollVisibleStore: Writable<boolean>;
		reveal: () => void;
	};

	export let chatIdProp = '';

	let loading = false;

	const eventTarget = new EventTarget();
	type PendingGeminiImage = {
		mimeType: string;
		parts: string[];
	};
	const pendingGeminiImages = new Map<string, Map<string, PendingGeminiImage>>();
	const OPENWEBUI_FILE_URL_SCHEME = 'openwebui-file://';
	const buildImageDataUrl = (mimeType: string, data: string) => `data:${mimeType};base64,${data}`;
	const mergeMessageFiles = (existing: any[] = [], incoming: any[] = []) => {
		const merged = [];
		const seen = new Set<string>();

		for (const file of [...existing, ...incoming]) {
			if (!file || typeof file !== 'object') continue;
			const normalized = JSON.parse(JSON.stringify(file));
			const key = JSON.stringify(normalized);
			if (seen.has(key)) continue;
			seen.add(key);
			merged.push(normalized);
		}

		return merged;
	};
	const isInlineDataImageUrl = (value: unknown): value is string =>
		typeof value === 'string' && value.startsWith('data:image/');
	const buildChatImageContentUrl = (id: string) => `${WEBUI_API_BASE_URL}/files/${id}/content`;
	const extractChatImageFileId = (file: any): string | null => {
		const directId = typeof file?.id === 'string' && file.id.trim() ? file.id.trim() : null;
		if (directId) {
			return directId;
		}

		const url = typeof file?.url === 'string' ? file.url : '';
		const match = url.match(/\/api\/v1\/files\/([^/?#]+)(?:\/content)?(?:[?#].*)?$/);
		return match?.[1] ?? null;
	};
	const sanitizeImageFileRef = (file: any) => {
		const fileId = extractChatImageFileId(file);
		const url =
			fileId !== null
				? buildChatImageContentUrl(fileId)
				: typeof file?.url === 'string'
					? file.url
					: '';

		if (!url) {
			return null;
		}

		return Object.fromEntries(
			Object.entries({
				type: 'image',
				id: fileId ?? undefined,
				name:
					typeof file?.name === 'string' && file.name
						? file.name
						: file?.file?.meta?.name ?? undefined,
				url,
				size:
					typeof file?.size === 'number'
						? file.size
						: file?.file?.meta?.size ?? undefined,
				content_type:
					typeof file?.content_type === 'string' && file.content_type
						? file.content_type
						: file?.file?.meta?.content_type ?? undefined
			}).filter(([, value]) => value !== undefined && value !== null && value !== '')
		);
	};
	const buildModelImageRequestUrl = (file: any) => {
		const fileId = extractChatImageFileId(file);
		if (fileId) {
			return `${OPENWEBUI_FILE_URL_SCHEME}${fileId}`;
		}

		return typeof file?.url === 'string' ? file.url : '';
	};
	const normalizeInputFileForMessage = (file: any) => {
		if (!file || typeof file !== 'object') {
			return file;
		}

		if (file.type === 'image') {
			return sanitizeImageFileRef(file) ?? structuredClone(file);
		}

		return structuredClone(file);
	};
	const uploadInlineImageForPersistence = async (file: any) => {
		if (!file || typeof file !== 'object' || !isInlineDataImageUrl(file?.url)) {
			return sanitizeImageFileRef(file) ?? structuredClone(file);
		}

		try {
			const response = await fetch(file.url);
			const imageBlob = await response.blob();
			if (!imageBlob || imageBlob.size === 0) {
				return structuredClone(file);
			}

			const mimeType = imageBlob.type || 'image/png';
			const extension = mimeType.split('/').at(1)?.split('+').at(0) || 'png';
			const fileName =
				typeof file?.name === 'string' && file.name
					? file.name
					: `Chat_Image_${Date.now()}.${extension}`;
			const uploadedFile = await uploadFile(
				localStorage.token,
				new File([imageBlob], fileName, { type: mimeType }),
				{ process: false }
			);

			if (!uploadedFile?.id) {
				return structuredClone(file);
			}

			const normalizedRef = sanitizeImageFileRef({
				...file,
				id: uploadedFile.id,
				name: uploadedFile?.meta?.name ?? fileName,
				size: uploadedFile?.meta?.size ?? imageBlob.size,
				content_type: uploadedFile?.meta?.content_type ?? mimeType
			});
			return normalizedRef ? structuredClone(normalizedRef) : structuredClone(file);
		} catch (error) {
			console.error('Failed to normalize inline image before saving chat:', error);
			return structuredClone(file);
		}
	};
	const historyHasInlineDataImages = (historyState) =>
		Object.values(historyState?.messages ?? {}).some((message: any) =>
			Array.isArray(message?.files)
				? message.files.some(
						(file: any) => file?.type === 'image' && isInlineDataImageUrl(file?.url)
					)
				: false
		);
	const normalizeHistoryForPersistence = async (historyState) => {
		if (!historyState?.messages || !historyHasInlineDataImages(historyState)) {
			return { history: historyState, changed: false };
		}

		const normalizedHistory = structuredClone(historyState);
		let changed = false;

		for (const [messageId, message] of Object.entries(normalizedHistory.messages ?? {}) as [string, any][]) {
			if (!Array.isArray(message?.files) || message.files.length === 0) {
				continue;
			}

			const normalizedFiles = [];
			let messageChanged = false;

			for (const file of message.files) {
				if (file?.type === 'image') {
					const normalizedFile = await uploadInlineImageForPersistence(file);
					normalizedFiles.push(normalizedFile);
					if (JSON.stringify(normalizedFile) !== JSON.stringify(file)) {
						messageChanged = true;
					}
				} else {
					normalizedFiles.push(structuredClone(file));
				}
			}

			if (messageChanged) {
				normalizedHistory.messages[messageId] = {
					...message,
					files: normalizedFiles
				};
				changed = true;
			}
		}

		return {
			history: changed ? normalizedHistory : historyState,
			changed
		};
	};
	let controlPane;
	let controlPaneComponent;

	let autoScroll = true;
	let processing = '';
	let messagesContainerElement: HTMLDivElement;
	let scrollSentinel: HTMLDivElement;
	let scrollObserver: IntersectionObserver;
	let userHasScrolled = false;
	let isAutoScrolling = false;
	let _scrollResetRafId: number | null = null;
	let _overviewFocusRafId: number | null = null;
	let overviewPinnedMessageId: string | null = null;
	let overviewNavigationInFlight = false;
	let _overviewNavigationTimer: ReturnType<typeof setTimeout> | null = null;
	let messageOutlineHideTimer: ReturnType<typeof setTimeout> | null = null;
	let messageOutlineLastUserIntentAt = 0;
	let messageOutlineScrollbarDragPrimedUntil = 0;
	const messageOutlineVisibleStore = writable(false);

	let navbarElement;

	let showEventConfirmation = false;
	let eventConfirmationTitle = '';
	let eventConfirmationMessage = '';
	let eventConfirmationInput = false;
	let eventConfirmationInputPlaceholder = '';
	let eventConfirmationInputValue = '';
	let eventCallback = null;

	let chatIdUnsubscriber: Unsubscriber | undefined;
	let selectedAssistantSceneUnsubscriber: Unsubscriber | undefined;

	let selectedModels = [''];
	let atSelectedModel: Model | undefined;
	let selectedModelIds = [];
	$: selectedModelIds =
		atSelectedModel !== undefined ? [getModelSelectionId(atSelectedModel)] : selectedModels;
	let activeAssistant: ChatAssistantSnapshot | null = null;

	let selectedToolIds = [];
	let toolSelectionTouched = false;
	let selectedSkillIds = [];
	let skillSelectionTouched = false;
	let imageGenerationEnabled = false;
	type ImageGenerationOptions = {
		model?: string | null;
		model_ref?: Record<string, unknown> | null;
		image_size?: string | null;
		aspect_ratio?: string | null;
		resolution?: string | null;
		n?: number | null;
		negative_prompt?: string | null;
		credential_source?: string | null;
		connection_index?: number | null;
		steps?: number | null;
		background?: string | null;
	};
	let imageGenerationOptions: ImageGenerationOptions = {};
	let webSearchMode: WebSearchMode = 'off';
	let webSearchModeSource: WebSearchModeSource = 'default';
	let codeInterpreterEnabled = false;

	let chat = null;
	let tags = [];

	let history = {
		messages: {},
		currentId: null
	};
	let selectionThreads: PersistedSelectionThreads = createEmptySelectionThreads();
	const selectionThreadsStore = writable<PersistedSelectionThreads>(selectionThreads);
	const expandedSelectionThreadId = writable<string | null>(null);
	let selectionThreadsPersistTimeout: ReturnType<typeof setTimeout> | null = null;
	let composerStatePersistTimeout: ReturnType<typeof setTimeout> | null = null;
	let pendingChatSave: Promise<void> = Promise.resolve();
	let pendingComposerStateSave: Promise<void> = Promise.resolve();
	let hasPersistedComposerState = false;
	let composerStateSyncReady = false;
	let lastRequestedChatIdProp = '';
	let activeChatLoadToken = 0;

	// J-3-01: O(1) model lookup map — rebuilt reactively when $models changes
	let modelsMap: Map<string, Model> = new Map();
	$: {
		const lookup = buildModelIdentityLookup($models);
		modelsMap = lookup.byId;
	}
	const getModelById = (id: string): Model | undefined => modelsMap.get(id);
	const getCanonicalModelId = (id: string): string =>
		resolveModelSelectionId($models, id, { preserveAmbiguous: true });
	const getModelRequestId = (model: Model): string => getModelSelectionId(model) || model.id;
	const MODEL_CONNECTION_AMBIGUOUS_CODE = 'model_connection_ambiguous';
	const MODEL_CONNECTION_STALE_CODE = 'model_connection_stale';
	const getModelResolutionDetail = (error: unknown) => {
		if (!error || typeof error !== 'object') return null;

		const detail =
			'detail' in error && error.detail && typeof error.detail === 'object' ? error.detail : error;
		if (!detail || typeof detail !== 'object') return null;
		const payload = detail as Record<string, unknown>;

		const code = typeof payload.code === 'string' ? payload.code : '';
		if (code !== MODEL_CONNECTION_AMBIGUOUS_CODE && code !== MODEL_CONNECTION_STALE_CODE) {
			return null;
		}

		return {
			code,
			message: typeof payload.message === 'string' ? payload.message : '',
			requestedModelId:
				typeof payload.requested_model_id === 'string' ? payload.requested_model_id : '',
			candidates: Array.isArray(payload.candidates)
				? payload.candidates.map((candidate) => `${candidate ?? ''}`.trim()).filter(Boolean)
				: []
		};
	};
	const openModelSelector = async (index = 0, searchValue = '') => {
		const button = document.getElementById(`model-selector-${index}-button`) as HTMLButtonElement | null;
		if (!button) return;
		button.click();
		await tick();
		const input = document.getElementById('model-search-input') as HTMLInputElement | null;
		if (!input) return;
		input.focus();
		if (searchValue) {
			input.value = searchValue;
			input.dispatchEvent(new Event('input'));
		}
	};
	const promptModelReselection = async ({
		index = 0,
		rawModelId = '',
		ambiguous = false
	}: {
		index?: number;
		rawModelId?: string;
		ambiguous?: boolean;
	}) => {
		const selectorIndex = Math.max(0, index);
		if (selectorIndex >= 0) {
			const nextSelectedModels = [...selectedModels];
			while (nextSelectedModels.length <= selectorIndex) {
				nextSelectedModels.push('');
			}
			nextSelectedModels[selectorIndex] = '';
			selectedModels = nextSelectedModels;
		}

		if (ambiguous) {
			toast.error(
				$i18n.t(
					'This chat was saved in an older version with only the model name. Multiple connections now share that model. Please reselect the correct model with its connection suffix.'
				)
			);
		} else {
			toast.error(
				$i18n.t(
					'The saved model connection is no longer available. Please reselect the correct model with its connection suffix.'
				)
			);
		}

		await openModelSelector(selectorIndex, rawModelId);
	};
	const isBlockingModelResolution = (resolution: ChatModelResolution | null | undefined) =>
		resolution?.status === 'stale' || resolution?.status === 'ambiguous';
	const promptModelResolution = async (
		resolution: ChatModelResolution,
		index = 0
	) => {
		await promptModelReselection({
			index,
			rawModelId: resolution.searchValue || resolution.value,
			ambiguous: resolution.status === 'ambiguous'
		});
	};
	const resolveSelectedModel = (modelId: unknown, index = 0) =>
		resolveChatModelSelection($models, {
			value: modelId,
			...(buildPersistedModelSelectionHints(selectedModels)[index] ?? {})
		});
	const findBlockingSelectedModelResolution = () => {
		for (const [index, modelId] of selectedModels.entries()) {
			const resolution = resolveSelectedModel(modelId, index);
			if (isBlockingModelResolution(resolution)) {
				return { index, resolution };
			}
		}
		return null;
	};
	const resolveMessageModel = (message: any) =>
		resolveChatModelSelection($models, {
			value: message?.model,
			model_ref: message?.model_ref,
			display_name: message?.modelName
		});
	const applyResolvedMessageModel = (message: any, resolution: ChatModelResolution) => {
		if (!message || resolution.status !== 'resolved' || !resolution.model) {
			return;
		}

		message.model = resolution.value;
		message.modelName = getModelChatDisplayName(resolution.model) || resolution.model.id || resolution.value;
		const modelRef = getModelRef(resolution.model);
		if (modelRef) {
			message.model_ref = modelRef;
		}
	};
	const getChatModelSelectionHints = (chatContent: any) =>
		Array.isArray(chatContent?.model_selection_hints)
			? chatContent.model_selection_hints
			: [];
	const buildPersistedModelSelectionHints = (modelIds: string[] = selectedModels) =>
		modelIds.map((modelId) => {
			const model = getModelById(modelId);
			return (
				buildModelSelectionHint(model) ?? {
					selection_id: `${modelId ?? ''}`.trim(),
					model_id: `${modelId ?? ''}`.trim()
				}
			);
		});
	const recoverLoadedChatModelState = (
		chatContent: any,
		loadedHistory: any,
		loadedModels: unknown
	) => {
		const rawModels = Array.isArray(loadedModels) ? loadedModels : [loadedModels ?? ''];
		const hints = getChatModelSelectionHints(chatContent);
		const selectedResolutions = resolveChatModelSelections($models, rawModels, hints);
		const latestResolvedByIndex = new Map<number, { resolution: ChatModelResolution; timestamp: number }>();

		for (const message of Object.values(loadedHistory?.messages ?? {}) as any[]) {
			if (!message || typeof message !== 'object') continue;

			if (Array.isArray(message.models)) {
				message.models = message.models.map((modelId: unknown, index: number) => {
					const resolution = resolveChatModelSelection($models, { value: modelId });
					return resolution.status === 'resolved' ? resolution.value : modelId;
				});
			}

			if (message.role !== 'assistant') continue;

			const resolution = resolveMessageModel(message);
			if (resolution.status === 'resolved') {
				applyResolvedMessageModel(message, resolution);
				const modelIdx =
					typeof message.modelIdx === 'number'
						? message.modelIdx
						: Number.isInteger(Number(message.modelIdx))
							? Number(message.modelIdx)
							: 0;
				const timestamp = Number(message.timestamp ?? 0);
				const previous = latestResolvedByIndex.get(modelIdx);
				if (!previous || timestamp >= previous.timestamp) {
					latestResolvedByIndex.set(modelIdx, { resolution, timestamp });
				}
			}
		}

		const nextSelectedModels = selectedResolutions.map((resolution, index) => {
			if (resolution.status === 'resolved') return resolution.value;
			const inferred = latestResolvedByIndex.get(index)?.resolution;
			if (inferred?.status === 'resolved') return inferred.value;
			return resolution.value;
		});
		for (const [index, item] of latestResolvedByIndex.entries()) {
			if (index >= nextSelectedModels.length && item.resolution.status === 'resolved') {
				while (nextSelectedModels.length < index) {
					nextSelectedModels.push('');
				}
				nextSelectedModels[index] = item.resolution.value;
			}
		}

		if (nextSelectedModels.length === 0 && latestResolvedByIndex.size > 0) {
			return Array.from(latestResolvedByIndex.entries())
				.sort(([left], [right]) => left - right)
				.map(([, item]) => item.resolution.value);
		}

		return nextSelectedModels.length > 0 ? nextSelectedModels : [''];
	};
	const getVisibleSkillIds = () =>
		($skillsStore ?? []).map((skill) => String(skill?.id ?? '')).filter((id) => id);
	const filterVisibleSkillIds = (ids: string[] = []) => {
		const visible = new Set(getVisibleSkillIds());
		return ids.filter((id) => visible.has(id));
	};
	const arraysEqual = (left: string[] = [], right: string[] = []) =>
		left.length === right.length && left.every((value, index) => value === right[index]);
	const extractSkillIdsFromText = (text: string) => {
		const matches = [...String(text ?? '').matchAll(/<\$([\w.\-:/]+)(?:\|[^>]+)?>/g)];
		return Array.from(
			new Set(matches.map((match) => String(match?.[1] ?? '').trim()).filter(Boolean))
		);
	};
	const stripSkillTagsFromText = (text: string) =>
		String(text ?? '')
			.replace(/<\$([\w.\-:/]+)(?:\|[^>]+)?>\s*/g, '')
			.trim();
	const collectRequestSkillIds = (messages: any[] = []) => {
		const ids = new Set<string>(skillSelectionTouched ? selectedSkillIds : []);
		for (const message of messages ?? []) {
			if (message?.role !== 'user') {
				continue;
			}

			const content = message?.content;
			if (typeof content === 'string') {
				for (const skillId of extractSkillIdsFromText(content)) {
					ids.add(skillId);
				}
				continue;
			}

			if (Array.isArray(content)) {
				for (const item of content) {
					if (item?.type === 'text') {
						for (const skillId of extractSkillIdsFromText(item?.text ?? '')) {
							ids.add(skillId);
						}
					}
				}
			}
		}
		return Array.from(ids);
	};

	const normalizeReasoningEffortValue = (value: unknown): string | null => {
		if (value === null || value === undefined) {
			return null;
		}

		const normalized = String(value).trim().toLowerCase();
		return normalized === '' ? null : normalized;
	};

	const normalizeThinkingTokenValue = (value: unknown): number | null => {
		if (value === null || value === undefined || value === '') {
			return null;
		}

		const parsed = Number(value);
		return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
	};

	const getResolvedSelectedModelIds = () =>
		selectedModelIds.filter((id): id is string => typeof id === 'string' && id.trim() !== '');

	const getSingleSelectedReasoningModel = (): Model | null => {
		const ids = getResolvedSelectedModelIds();
		if (ids.length !== 1) {
			return null;
		}

		return getModelById(ids[0]) ?? null;
	};

	const getSingleSelectedDedicatedImageModel = (): Model | null => {
		const model = getSingleSelectedReasoningModel();
		if (!model) {
			return null;
		}

		return isDedicatedImageGenerationModel(getModelCleanId(model) || model.id) ? model : null;
	};

	const canUseChatImageGeneration = () =>
		Boolean($config?.features?.enable_image_generation) &&
		($user?.role === 'admin' || $user?.permissions?.features?.image_generation);

	const isImageGenerationActiveForRequest = () =>
		imageGenerationEnabled || Boolean(getSingleSelectedDedicatedImageModel());

	const getModelDefaultReasoningEffort = (model: Model | null | undefined): string | null =>
		normalizeReasoningEffortValue((model as any)?.info?.params?.reasoning_effort ?? null);

	const getResolvedSelectedWebSearchModels = (): Model[] => {
		const ids = getResolvedSelectedModelIds();
		if (ids.length === 0) {
			return [];
		}

		const resolved = ids
			.map((id) => getModelById(id))
			.filter((model): model is Model => Boolean(model));
		return resolved.length === ids.length ? resolved : [];
	};

	const getModelBuiltinWebSearchPreference = (model: Model | null | undefined): boolean | null => {
		const value =
			(model as any)?.info?.meta?.builtin_tool_config?.ENABLE_WEB_SEARCH_TOOL ??
			(model as any)?.meta?.builtin_tool_config?.ENABLE_WEB_SEARCH_TOOL;
		return typeof value === 'boolean' ? value : null;
	};

	const pickModelDefaultWebSearchMode = (selectedModels: Model[]): WebSearchMode => {
		const availableModes = new Set(
			buildWebSearchModeOptions(
				(key, options) => get(i18n).t(key, options),
				$config,
				selectedModels
			)
				.filter((option) => option.disabled !== true)
				.map((option) => option.value)
		);

		return (
			(['auto', 'native', 'halo', 'off'] as WebSearchMode[]).find((mode) =>
				availableModes.has(mode)
			) ?? 'off'
		);
	};

	const getSelectionDrivenWebSearchState = (): {
		mode: WebSearchMode;
		source: WebSearchModeSource;
	} | null => {
		const requestedIds = selectedModelIds.filter(
			(id): id is string => typeof id === 'string' && id.trim() !== ''
		);
		const resolvedModels = getResolvedSelectedWebSearchModels();

		if (requestedIds.length > 0 && resolvedModels.length !== requestedIds.length) {
			return null;
		}

		const fallbackMode = getPreferredDefaultWebSearchMode();
		if (resolvedModels.length === 0) {
			return { mode: fallbackMode, source: 'default' };
		}

		const preferences = resolvedModels.map(getModelBuiltinWebSearchPreference);
		if (resolvedModels.length > 1 && preferences.some((value) => value === false)) {
			return { mode: 'off', source: 'model' };
		}

		if (preferences.some((value) => value === true)) {
			return {
				mode: pickModelDefaultWebSearchMode(resolvedModels),
				source: 'model'
			};
		}

		return { mode: fallbackMode, source: 'default' };
	};

	// J-3-01: Reactive flag to avoid calling createMessagesList just for emptiness check in template
	let hasMessages = false;
	$: hasMessages = history.currentId !== null;
	$: {
		const messageIds = new Set(Object.keys(history?.messages ?? {}));
		const nextItems = selectionThreads.items.filter((thread) =>
			messageIds.has(thread.sourceMessageId)
		);

		if (nextItems.length !== selectionThreads.items.length) {
			updateSelectionThreads(
				{
					version: 1,
					items: nextItems
				},
				{ immediate: true }
			);
		}
	}

	let taskIds = null;
	let messageQueue: { id: string; prompt: string; files: any[] }[] = [];
	let branchingMessageId: string | null = null;

	// Temporary instruction for regeneration with modifications (e.g. "more concise")
	let _pendingInstruction: string | null = null;

	// Chat Input
	let prompt = '';
	let chatFiles = [];
	let files = [];
	let params = {};

	let reasoningEffort: string | null = null;
		let maxThinkingTokens: number | null = null;
		let lastFreshChatRequest = '';
		// Flag to prevent sessionStorage recovery from overriding a deliberate fresh chat reset
		let freshChatActive = false;
		let webSearchSelectionSyncReady = false;

	// Bidirectional sync: Controls sidebar params ↔ inline ThinkingControl
	// 用缓存值打断 reactive 级联：正向同步更新缓存 → 反向 onChange 检测到缓存一致则跳过
	let _lastSyncedEffort: string | null = null;
	let _lastSyncedTokens: number | null = null;
	let reasoningSelectionTrackingReady = false;
	let lastReasoningSelectionKey = '';

	const syncReasoningUiState = (effort: unknown, tokens: unknown) => {
		const normalizedEffort = normalizeReasoningEffortValue(effort);
		const normalizedTokens = normalizeThinkingTokenValue(tokens);

		_lastSyncedEffort = normalizedEffort;
		_lastSyncedTokens = normalizedTokens;

		if (reasoningEffort !== normalizedEffort) {
			reasoningEffort = normalizedEffort;
		}

		if (maxThinkingTokens !== normalizedTokens) {
			maxThinkingTokens = normalizedTokens;
		}
	};

	const syncReasoningParamsState = (effort: unknown, tokens: unknown) => {
		const normalizedEffort = normalizeReasoningEffortValue(effort);
		const normalizedTokens = normalizeThinkingTokenValue(tokens);
		const currentEffort = normalizeReasoningEffortValue(params?.reasoning_effort ?? null);
		const currentTokens = normalizeThinkingTokenValue(params?.max_thinking_tokens ?? null);

		if (currentEffort === normalizedEffort && currentTokens === normalizedTokens) {
			return;
		}

		params = {
			...params,
			reasoning_effort: normalizedEffort,
			max_thinking_tokens: normalizedTokens
		};
	};

	const setSharedReasoningState = ({
		effort,
		tokens,
		syncParams = true
	}: {
		effort: unknown;
		tokens: unknown;
		syncParams?: boolean;
	}) => {
		syncReasoningUiState(effort, tokens);

		if (syncParams) {
			syncReasoningParamsState(effort, tokens);
		}
	};

	const applyReasoningSelectionDefaults = () => {
		const ids = getResolvedSelectedModelIds();
		const singleModel = getSingleSelectedReasoningModel();

		if (ids.length !== 1 || !singleModel) {
			setSharedReasoningState({ effort: null, tokens: null });
			return;
		}

		setSharedReasoningState({
			effort: getModelDefaultReasoningEffort(singleModel),
			tokens: null
		});
	};

	const resetReasoningSelectionTracking = () => {
		reasoningSelectionTrackingReady = false;
		lastReasoningSelectionKey = '';
	};

	const initializeReasoningSelectionTracking = () => {
		lastReasoningSelectionKey = getResolvedSelectedModelIds().join('|');
		reasoningSelectionTrackingReady = true;
	};

	const activateAssistant = (value: Record<string, unknown> | ChatAssistantSnapshot | null) => {
		const assistant = toChatAssistantSnapshot(value as Record<string, unknown> | null);
		if (!assistant) {
			return;
		}

		activeAssistant = assistant;
		params = {
			...params,
			system: assistant.prompt
		};
		persistChatComposerState();
	};

	const deactivateAssistant = () => {
		const assistantPrompt = activeAssistant?.prompt ?? null;
		activeAssistant = null;

		if (assistantPrompt && params?.system === assistantPrompt) {
			const { system: _system, ...rest } = params;
			params = rest;
		}

		persistChatComposerState();
	};

	const getRequestStopTokens = () => {
		const rawStop = params?.stop ?? $settings?.params?.stop;
		if (!rawStop) {
			return undefined;
		}

		return String(rawStop)
			.split(',')
			.map((token) => token.trim())
			.filter(Boolean)
			.map((str) => decodeURIComponent(JSON.parse('"' + str.replace(/\"/g, '\\"') + '"')));
	};

	const collectFloatingRequestFiles = (messages) =>
		messages
			.flatMap((message) =>
				(message?.files ?? []).filter((item) =>
					['doc', 'file', 'collection', 'web_search_results'].includes(item.type)
				)
			)
			.filter(
				(item, index, array) =>
					array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
			);

	const buildFloatingRequestMessages = async (messages) => {
		const systemPrompt =
			params?.system || $settings?.system
				? promptTemplate(
						params?.system ?? $settings?.system ?? '',
						$user?.name,
						$settings?.userLocation
							? await getAndUpdateUserLocation(localStorage.token).catch((err) => {
									console.error(err);
									return undefined;
								})
							: undefined
					)
				: null;

		return [
			systemPrompt
				? {
						role: 'system',
						content: systemPrompt
					}
				: undefined,
			...messages.map((message) => {
				const textContent = stripSkillTagsFromText(
					message?.merged?.content ?? processDetails(message?.content ?? '')
				);
				const imageFiles = (message?.files ?? []).filter((file) => file.type === 'image');

				return {
					role: message.role,
					...(imageFiles.length > 0 && message.role === 'user'
						? {
								content: [
									{
										type: 'text',
										text: textContent
									},
									...imageFiles.map((file) => ({
										type: 'image_url',
										image_url: {
											url: buildModelImageRequestUrl(file)
										}
									}))
								]
							}
						: {
								content: textContent
							})
				};
			})
		].filter(
			(message) =>
				message &&
				(message.role === 'user' ||
					(typeof message.content === 'string' && message.content.trim()) ||
					Array.isArray(message.content))
		);
	};

	const shouldIncludeUsageStreamOption = (model) => {
		const usageCapability = model?.info?.meta?.capabilities?.usage;
		if (typeof usageCapability === 'boolean') {
			return usageCapability;
		}

		// Base OpenAI-compatible models often have no workspace capability metadata,
		// but many compatible upstreams still support `stream_options.include_usage`.
		return model?.owned_by === 'openai';
	};

	const buildFloatingChatRequest = async ({
		modelId,
		messages,
		stream = true
	}: {
		modelId: string;
		messages: any[];
		stream?: boolean;
	}) => {
		const model = getModelById(modelId);
		if (!model) {
			throw new Error(`Model ${modelId} not found`);
		}

		const requestMessages = await buildFloatingRequestMessages(messages);
		const requestSkillIds = collectRequestSkillIds(messages);
		const requestedWebSearchMode = canUseChatWebSearch()
			? normalizeWebSearchMode(webSearchMode, 'off')
			: 'off';
		const requestFiles = collectFloatingRequestFiles(messages);
		const imageGenerationActive = canUseChatImageGeneration()
			? isImageGenerationActiveForRequest()
			: false;

		return {
			stream,
			model: getModelRequestId(model),
			messages: requestMessages,
			params: {
				...$settings?.params,
				...params,
				...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
				...(maxThinkingTokens != null && maxThinkingTokens > 0
					? { thinking: { type: 'enabled', budget_tokens: maxThinkingTokens } }
					: {}),
				format: $settings.requestFormat ?? undefined,
				keep_alive: $settings.keepAlive ?? undefined,
				stop: getRequestStopTokens()
			},
			files: requestFiles.length > 0 ? requestFiles : undefined,
			tool_ids: selectedToolIds.length > 0 ? selectedToolIds : undefined,
			skill_ids: requestSkillIds.length > 0 ? requestSkillIds : undefined,
			skill_selection_touched: skillSelectionTouched ? true : undefined,
			tool_servers: $toolServers,
			features: {
				memory: $settings?.memory ?? false,
				image_generation: imageGenerationActive,
				image_generation_options: imageGenerationActive
					? getImageGenerationOptionsPayload()
					: undefined,
				code_interpreter:
					$config?.features?.enable_code_interpreter &&
					($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
						? codeInterpreterEnabled
						: false,
				web_search: requestedWebSearchMode !== 'off',
				web_search_mode: requestedWebSearchMode !== 'off' ? requestedWebSearchMode : undefined
			},
			variables: {
				...getPromptVariables(
					$user?.name,
					$settings?.userLocation
						? await getAndUpdateUserLocation(localStorage.token).catch((err) => {
								console.error(err);
								return undefined;
							})
						: undefined
				)
			},
			session_id: $socket?.id ?? undefined,
			chat_id: $chatId ?? undefined,
			model_item: model,
			...(stream && shouldIncludeUsageStreamOption(model)
				? {
						stream_options: {
							include_usage: true
						}
					}
				: {})
		};
	};

	setContext('floatingChatRequestFactory', buildFloatingChatRequest);

	const getPreferredDefaultWebSearchMode = (): WebSearchMode =>
		getPreferredWebSearchMode($settings, 'off');

	const isChatWebSearchFeatureEnabled = () =>
		Boolean($config?.features?.enable_halo_web_search ?? $config?.features?.enable_web_search) ||
		Boolean($config?.features?.enable_native_web_search);

	const canUseChatWebSearch = () =>
		isChatWebSearchFeatureEnabled() &&
		($user?.role === 'admin' || $user?.permissions?.features?.web_search);

	$: {
		const dedicatedImageModel = getSingleSelectedDedicatedImageModel();
		if (dedicatedImageModel && canUseChatImageGeneration() && !imageGenerationEnabled) {
			imageGenerationEnabled = true;
		}
	}

	const decodeTokenUserId = (token: string | null | undefined): string | null => {
		if (!token || typeof atob !== 'function') {
			return null;
		}

		try {
			const [, payload = ''] = token.split('.');
			if (!payload) {
				return null;
			}

			const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
			const padded = normalized.padEnd(
				normalized.length + ((4 - (normalized.length % 4)) % 4),
				'='
			);
			const decoded = JSON.parse(atob(padded));
			const value = typeof decoded?.id === 'string' ? decoded.id.trim() : '';
			return value || null;
		} catch {
			return null;
		}
	};

	const getChatStorageUserScope = (): string => {
		const directUserId = typeof $user?.id === 'string' ? $user.id.trim() : '';
		if (directUserId) {
			return directUserId;
		}

		if (typeof localStorage !== 'undefined') {
			const tokenUserId = decodeTokenUserId(localStorage.token);
			if (tokenUserId) {
				return tokenUserId;
			}
		}

		return 'anonymous';
	};

	const buildScopedStorageKey = (baseKey: string) => `${baseKey}::${getChatStorageUserScope()}`;

	const readStorageItem = (
		storage: Storage,
		scopedKey: string,
		legacyKey?: string | null
	): { value: string | null; usedLegacy: boolean } => {
		const scopedValue = storage.getItem(scopedKey);
		if (scopedValue !== null) {
			return { value: scopedValue, usedLegacy: false };
		}

		if (!legacyKey || legacyKey === scopedKey) {
			return { value: null, usedLegacy: false };
		}

		const legacyValue = storage.getItem(legacyKey);
		return { value: legacyValue, usedLegacy: legacyValue !== null };
	};

	const migrateStorageItem = (
		storage: Storage,
		scopedKey: string,
		legacyKey: string | null | undefined,
		value: string | null
	) => {
		if (value === null) {
			return;
		}

		storage.setItem(scopedKey, value);
		if (legacyKey && legacyKey !== scopedKey) {
			storage.removeItem(legacyKey);
		}
	};

	const getImageGenerationOptionsPayload = () => {
		const payload = sanitizeChatImageGenerationOptions(imageGenerationOptions);
		const dedicatedImageModel = getSingleSelectedDedicatedImageModel();
		if (dedicatedImageModel?.id) {
			payload.model = getModelRequestId(dedicatedImageModel);
			const modelRef = getModelRef(dedicatedImageModel);
			if (modelRef) {
				payload.model_ref = modelRef;
			}
		}
		return Object.keys(payload).length > 0 ? payload : undefined;
	};

	const chatImageGenerationOptionKeys = [
		'model',
		'model_ref',
		'image_size',
		'aspect_ratio',
		'resolution',
		'n',
		'negative_prompt',
		'credential_source',
		'connection_index',
		'steps',
		'background'
	] as const;

	const sanitizeChatImageGenerationOptions = (options: unknown): ImageGenerationOptions => {
		if (!options || typeof options !== 'object' || Array.isArray(options)) {
			return {};
		}

		const raw = options as Record<string, unknown>;
		const payload: Record<string, unknown> = {};
		for (const key of chatImageGenerationOptionKeys) {
			const value = raw[key];
			if (value === undefined || value === null || value === '') {
				continue;
			}
			if (key === 'model_ref' && (typeof value !== 'object' || Array.isArray(value))) {
				continue;
			}
			payload[key] = value;
		}

		return payload as ImageGenerationOptions;
	};

	const supportsToolValvesContext = (id: string | null | undefined): id is string =>
		Boolean(id) && !String(id).startsWith('mcp:') && !String(id).startsWith('server:');

	$: currentValvesContext = (() => {
		const activeModelId =
			atSelectedModel?.id ??
			(selectedModelIds.length === 1 && selectedModelIds[0] ? selectedModelIds[0] : null);
		if (activeModelId) {
			const model = getModelById(activeModelId);
			if (model?.pipe) {
				return {
					tab: 'functions' as const,
					id: getFunctionPipeRootId(activeModelId)
				};
			}
		}

		const preferredToolId = selectedToolIds.find((id) => supportsToolValvesContext(id));
		if (preferredToolId) {
			return {
				tab: 'tools' as const,
				id: preferredToolId
			};
		}

		return null;
	})();

	const resolveStoredWebSearchState = (
		value:
			| {
					webSearchMode?: unknown;
					webSearchEnabled?: unknown;
					webSearchModeSource?: unknown;
					webSearchModeTouched?: unknown;
			  }
			| null
			| undefined,
		fallback: WebSearchMode = getPreferredDefaultWebSearchMode()
	): { mode: WebSearchMode; source: WebSearchModeSource } => {
		const hasLegacyState =
			value?.webSearchMode !== undefined || value?.webSearchEnabled === true;

		if (value?.webSearchMode !== undefined) {
			return {
				mode: normalizeWebSearchMode(value.webSearchMode, fallback),
				source:
					value?.webSearchModeSource !== undefined
						? normalizeWebSearchModeSource(value.webSearchModeSource, 'default')
						: value?.webSearchModeTouched === true || hasLegacyState
							? 'user'
							: 'default'
			};
		}

		if (value?.webSearchEnabled === true) {
			return {
				mode: 'halo',
				source:
					value?.webSearchModeSource !== undefined
						? normalizeWebSearchModeSource(value.webSearchModeSource, 'default')
						: value?.webSearchModeTouched === true || hasLegacyState
							? 'user'
							: 'default'
			};
		}

		return {
			mode: fallback,
			source:
				value?.webSearchModeSource !== undefined
					? normalizeWebSearchModeSource(value.webSearchModeSource, 'default')
					: value?.webSearchModeTouched === true
						? 'user'
						: 'default'
		};
	};

	const getLegacyChatSessionStateKey = (id: string | null | undefined = $chatId || chatIdProp) =>
		`chat-session-state-${id && id !== '' ? id : 'new'}`;

	const getChatSessionStateKey = (id: string | null | undefined = $chatId || chatIdProp) =>
		buildScopedStorageKey(getLegacyChatSessionStateKey(id));

	const getLegacyChatInputStateKey = (id: string | null | undefined = $chatId || chatIdProp) =>
		`chat-input-${id ?? ''}`;

	const getChatInputStateKey = (id: string | null | undefined = $chatId || chatIdProp) =>
		buildScopedStorageKey(getLegacyChatInputStateKey(id));

	const getLegacySelectedModelsStorageKey = () => 'selectedModels';

	const getSelectedModelsStorageKey = () =>
		buildScopedStorageKey(getLegacySelectedModelsStorageKey());

	const safeParseStoredJson = <T,>(rawValue: string | null | undefined, fallback: T): T => {
		if (!rawValue) {
			return fallback;
		}

		try {
			return JSON.parse(rawValue) as T;
		} catch {
			return fallback;
		}
	};

	const buildComposerStatePayload = () => ({
		selected_tool_ids: selectedToolIds,
		tool_selection_touched: toolSelectionTouched,
		selected_skill_ids: selectedSkillIds,
		skill_selection_touched: skillSelectionTouched,
		web_search_mode: webSearchMode,
		web_search_mode_source: webSearchModeSource,
		image_generation_enabled: imageGenerationEnabled,
		image_generation_options: sanitizeChatImageGenerationOptions(imageGenerationOptions),
		code_interpreter_enabled: codeInterpreterEnabled,
		reasoning_effort: reasoningEffort,
		max_thinking_tokens: maxThinkingTokens
	});

	const buildLocalChatSessionState = () => ({
		...buildComposerStatePayload(),
		webSearchMode: webSearchMode,
		webSearchModeSource: webSearchModeSource,
		webSearchModeTouched: webSearchModeSource === 'user',
		selectedToolIds,
		toolSelectionTouched,
		selectedSkillIds,
		skillSelectionTouched,
		activeAssistant,
		systemPrompt: typeof params?.system === 'string' ? params.system : null,
		imageGenerationEnabled,
		imageGenerationOptions: sanitizeChatImageGenerationOptions(imageGenerationOptions),
		codeInterpreterEnabled,
		reasoningEffort,
		maxThinkingTokens
	});

	const applyComposerState = (
		state: Record<string, any> | null | undefined,
		options: { markPersisted?: boolean } = {}
	) => {
		if (!state || typeof state !== 'object') {
			return false;
		}

		const hasComposerKeys = [
			'selected_tool_ids',
			'selectedToolIds',
			'selected_skill_ids',
			'selectedSkillIds',
			'web_search_mode',
			'webSearchMode',
			'image_generation_enabled',
			'imageGenerationEnabled',
			'code_interpreter_enabled',
			'codeInterpreterEnabled',
			'reasoning_effort',
			'reasoningEffort',
			'max_thinking_tokens',
			'maxThinkingTokens'
		].some((key) => key in state);
		if (!hasComposerKeys) {
			return false;
		}

		const restoredToolIds = state.selected_tool_ids ?? state.selectedToolIds;
		if (Array.isArray(restoredToolIds)) {
			selectedToolIds = restoredToolIds.map((id) => String(id ?? '').trim()).filter(Boolean);
		}
		if (
			state.tool_selection_touched !== undefined ||
			state.toolSelectionTouched !== undefined
		) {
			toolSelectionTouched = Boolean(
				state.tool_selection_touched ?? state.toolSelectionTouched
			);
		}

		const restoredSkillIds = state.selected_skill_ids ?? state.selectedSkillIds;
		if (Array.isArray(restoredSkillIds)) {
			selectedSkillIds = restoredSkillIds.map((id) => String(id ?? '').trim()).filter(Boolean);
		}
		if (
			state.skill_selection_touched !== undefined ||
			state.skillSelectionTouched !== undefined
		) {
			skillSelectionTouched = Boolean(
				state.skill_selection_touched ?? state.skillSelectionTouched
			);
		}

		const restoredWebSearchState = resolveStoredWebSearchState({
			webSearchMode: state.web_search_mode ?? state.webSearchMode,
			webSearchModeSource: state.web_search_mode_source ?? state.webSearchModeSource,
			webSearchModeTouched:
				state.web_search_mode_source !== undefined ||
				state.webSearchModeSource !== undefined
					? normalizeWebSearchModeSource(
							state.web_search_mode_source ?? state.webSearchModeSource,
							'default'
						) === 'user'
					: state.webSearchModeTouched
		});
		webSearchMode = restoredWebSearchState.mode;
		webSearchModeSource = restoredWebSearchState.source;

		if (
			state.image_generation_enabled !== undefined ||
			state.imageGenerationEnabled !== undefined
		) {
			imageGenerationEnabled = Boolean(
				state.image_generation_enabled ?? state.imageGenerationEnabled
			);
		}
		if (
			state.image_generation_options !== undefined ||
			state.imageGenerationOptions !== undefined
		) {
			imageGenerationOptions = sanitizeChatImageGenerationOptions(
				state.image_generation_options ?? state.imageGenerationOptions ?? {}
			);
		}
		if (
			state.code_interpreter_enabled !== undefined ||
			state.codeInterpreterEnabled !== undefined
		) {
			codeInterpreterEnabled = Boolean(
				state.code_interpreter_enabled ?? state.codeInterpreterEnabled
			);
		}
		if (state.reasoning_effort !== undefined || state.reasoningEffort !== undefined) {
			reasoningEffort =
				normalizeReasoningEffortValue(
					state.reasoning_effort ?? state.reasoningEffort ?? null
				) ?? null;
		}
		if (
			state.max_thinking_tokens !== undefined ||
			state.maxThinkingTokens !== undefined
		) {
			maxThinkingTokens = normalizeThinkingTokenValue(
				state.max_thinking_tokens ?? state.maxThinkingTokens ?? null
			);
		}

		hasPersistedComposerState = options.markPersisted === true;
		return true;
	};

	const readChatInputState = (id: string | null | undefined = $chatId || chatIdProp) => {
		const scopedKey = getChatInputStateKey(id);
		const legacyKey = getLegacyChatInputStateKey(id);
		const { value, usedLegacy } = readStorageItem(localStorage, scopedKey, legacyKey);
		const parsed = safeParseStoredJson<Record<string, any> | null>(value, null);

		if (parsed && usedLegacy) {
			migrateStorageItem(localStorage, scopedKey, legacyKey, value);
		}

		return parsed;
	};

	const writeChatInputState = (
		input: Record<string, any>,
		id: string | null | undefined = $chatId || chatIdProp
	) => {
		const scopedKey = getChatInputStateKey(id);
		const legacyKey = getLegacyChatInputStateKey(id);
		const serialized = JSON.stringify(input);
		localStorage.setItem(scopedKey, serialized);
		if (legacyKey !== scopedKey) {
			localStorage.removeItem(legacyKey);
		}
	};

	const removeChatInputState = (id: string | null | undefined = $chatId || chatIdProp) => {
		const scopedKey = getChatInputStateKey(id);
		const legacyKey = getLegacyChatInputStateKey(id);
		localStorage.removeItem(scopedKey);
		if (legacyKey !== scopedKey) {
			localStorage.removeItem(legacyKey);
		}
	};

	const restoreChatSessionState = (id: string | null | undefined = $chatId || chatIdProp) => {
		try {
			const scopedKey = getChatSessionStateKey(id);
			const legacyKey = getLegacyChatSessionStateKey(id);
			const { value, usedLegacy } = readStorageItem(localStorage, scopedKey, legacyKey);
			const stored = safeParseStoredJson<Record<string, any> | null>(value, null);
			if (stored && usedLegacy) {
				migrateStorageItem(localStorage, scopedKey, legacyKey, value);
			}

			const state = stored ?? readChatInputState(id);
			if (!state) {
				return false;
			}

			applyComposerState(state, { markPersisted: false });
			activeAssistant = toChatAssistantSnapshot(state.activeAssistant ?? null);

			if (typeof state.systemPrompt === 'string') {
				params = {
					...params,
					system: state.systemPrompt
				};
			} else if (activeAssistant?.prompt) {
				params = {
					...params,
					system: activeAssistant.prompt
				};
			}

			return true;
		} catch {
			return false;
		}
	};

	const persistChatSessionState = (id: string | null | undefined = $chatId || chatIdProp) => {
		const scopedKey = getChatSessionStateKey(id);
		const legacyKey = getLegacyChatSessionStateKey(id);
		localStorage.setItem(scopedKey, JSON.stringify(buildLocalChatSessionState()));
		if (legacyKey !== scopedKey) {
			localStorage.removeItem(legacyKey);
		}
	};

	const persistChatComposerState = (id: string | null | undefined = $chatId || chatIdProp) => {
		persistChatSessionState(id);
		if (!composerStateSyncReady || !id || $temporaryChatEnabled) {
			return;
		}

		if (composerStatePersistTimeout) {
			clearTimeout(composerStatePersistTimeout);
			composerStatePersistTimeout = null;
		}

		composerStatePersistTimeout = setTimeout(() => {
			pendingComposerStateSave = pendingComposerStateSave
				.catch(() => undefined)
				.then(async () => {
					await updateChatComposerStateById(localStorage.token, id, buildComposerStatePayload());
				})
				.catch((error) => {
					console.error(error);
				});
		}, 250);
	};

	const handleMessageInputChange = (input) => {
		const newRE = normalizeReasoningEffortValue(input.reasoningEffort ?? null);
		const newMT = normalizeThinkingTokenValue(input.maxThinkingTokens ?? null);
		const oldRE = normalizeReasoningEffortValue(params?.reasoning_effort ?? null);
		const oldMT = normalizeThinkingTokenValue(params?.max_thinking_tokens ?? null);
		if (newRE !== oldRE || newMT !== oldMT) {
			const finalMT = newRE ? null : newMT;
			_lastSyncedEffort = newRE;
			_lastSyncedTokens = finalMT;
			params = {
				...params,
				reasoning_effort: newRE,
				max_thinking_tokens: finalMT
			};
		}

		const nextWebSearchState = resolveStoredWebSearchState(
			{
				webSearchMode: input.webSearchMode,
				webSearchModeSource: input.webSearchModeSource,
				webSearchModeTouched: input.webSearchModeTouched
			},
			getPreferredDefaultWebSearchMode()
		);
		webSearchMode = nextWebSearchState.mode;
		webSearchModeSource = nextWebSearchState.source;
		selectedToolIds = Array.isArray(input.selectedToolIds)
			? input.selectedToolIds.map((id) => String(id ?? '').trim()).filter(Boolean)
			: selectedToolIds;
		toolSelectionTouched = Boolean(input.toolSelectionTouched ?? toolSelectionTouched);
		selectedSkillIds = Array.isArray(input.selectedSkillIds)
			? input.selectedSkillIds.map((id) => String(id ?? '').trim()).filter(Boolean)
			: selectedSkillIds;
		skillSelectionTouched = Boolean(input.skillSelectionTouched ?? skillSelectionTouched);
		reasoningEffort = newRE;
		maxThinkingTokens = newMT;

		const persistedInput = {
			...input,
			webSearchMode: nextWebSearchState.mode,
			webSearchModeSource: nextWebSearchState.source,
			webSearchModeTouched: nextWebSearchState.source === 'user'
		};

		if (input.prompt) {
			writeChatInputState(persistedInput, $chatId);
		} else {
			removeChatInputState($chatId);
		}
	};

	const removeChatSessionState = (id: string | null | undefined = $chatId || chatIdProp) => {
		const scopedKey = getChatSessionStateKey(id);
		const legacyKey = getLegacyChatSessionStateKey(id);
		localStorage.removeItem(scopedKey);
		if (legacyKey !== scopedKey) {
			localStorage.removeItem(legacyKey);
		}
	};

	const migrateChatSessionState = (
		fromId: string | null | undefined,
		toId: string | null | undefined
	) => {
		const fromKey = getChatSessionStateKey(fromId);
		const toKey = getChatSessionStateKey(toId);
		if (fromKey === toKey) {
			return;
		}

		const state = localStorage.getItem(fromKey);
		if (!state) {
			return;
		}

		localStorage.setItem(toKey, state);
		localStorage.removeItem(fromKey);
	};

	const setSelectionThreadsState = (nextState: PersistedSelectionThreads) => {
		selectionThreads = normalizeSelectionThreads(nextState);
		selectionThreadsStore.set(selectionThreads);
	};

	const normalizeSelectionThreadsForRuntime = (nextState: PersistedSelectionThreads) =>
		normalizeSelectionThreads(nextState, {
			coerceStreamingToInterrupted: true
		});

	const setRuntimeSelectionThreadsState = (nextState: PersistedSelectionThreads) => {
		selectionThreads = normalizeSelectionThreadsForRuntime(nextState);
		selectionThreadsStore.set(selectionThreads);
	};

	const persistSelectionThreads = async () => {
		if (selectionThreadsPersistTimeout) {
			clearTimeout(selectionThreadsPersistTimeout);
			selectionThreadsPersistTimeout = null;
		}

		if (!$chatId || $temporaryChatEnabled) {
			return;
		}

		await saveChatHandler($chatId, history, {
			selectionThreads
		});
	};

	const scheduleSelectionThreadsPersist = (delay = 250) => {
		if (selectionThreadsPersistTimeout) {
			clearTimeout(selectionThreadsPersistTimeout);
			selectionThreadsPersistTimeout = null;
		}

		if ($temporaryChatEnabled || !$chatId) {
			return;
		}

		selectionThreadsPersistTimeout = setTimeout(() => {
			void persistSelectionThreads();
		}, delay);
	};

	const updateSelectionThreads = (
		nextStateOrUpdater:
			| PersistedSelectionThreads
			| ((current: PersistedSelectionThreads) => PersistedSelectionThreads),
		options: {
			persist?: boolean;
			immediate?: boolean;
		} = {}
	) => {
		const { persist = true, immediate = false } = options;
		const nextState =
			typeof nextStateOrUpdater === 'function'
				? nextStateOrUpdater(selectionThreads)
				: nextStateOrUpdater;

		setSelectionThreadsState(nextState);

		if (!persist) {
			return;
		}

		if (immediate) {
			void persistSelectionThreads();
		} else {
			scheduleSelectionThreadsPersist();
		}
	};

	setContext('selectionThreadManager', {
		selectionThreadsStore,
		expandedSelectionThreadId,
		updateSelectionThreads,
		persistSelectionThreads
	});

	const clearMessageOutlineHideTimer = () => {
		if (messageOutlineHideTimer) {
			clearTimeout(messageOutlineHideTimer);
			messageOutlineHideTimer = null;
		}
	};

	const revealMessageOutline = () => {
		if ($mobile) {
			return;
		}

		messageOutlineLastUserIntentAt = performance.now();
		messageOutlineVisibleStore.set(true);
		clearMessageOutlineHideTimer();
		messageOutlineHideTimer = setTimeout(() => {
			messageOutlineVisibleStore.set(false);
			messageOutlineHideTimer = null;
		}, MESSAGE_OUTLINE_IDLE_MS);
	};

	const primeMessageOutlineScrollbarDrag = () => {
		messageOutlineScrollbarDragPrimedUntil =
			performance.now() + MESSAGE_OUTLINE_SCROLLBAR_DRAG_PRIME_MS;
	};

	const clearMessageOutlineScrollbarDragPrime = () => {
		messageOutlineScrollbarDragPrimedUntil = 0;
	};

	const shouldRevealMessageOutlineFromScroll = () => {
		const now = performance.now();

		return (
			now - messageOutlineLastUserIntentAt <= MESSAGE_OUTLINE_SCROLL_INTENT_WINDOW_MS ||
			messageOutlineScrollbarDragPrimedUntil >= now
		);
	};

	const isEditableMessageOutlineTarget = (target: EventTarget | null) => {
		if (!(target instanceof HTMLElement)) {
			return false;
		}

		if (target.isContentEditable || target.closest('[contenteditable="true"]')) {
			return true;
		}

		return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
	};

	const handleMessageOutlineWheel = () => {
		revealMessageOutline();
	};

	const handleMessageOutlineTouchMove = () => {
		revealMessageOutline();
	};

	const handleMessageOutlinePointerDown = (event: PointerEvent) => {
		if (!messagesContainerElement || event.target !== messagesContainerElement) {
			return;
		}

		primeMessageOutlineScrollbarDrag();
	};

	const handleMessageOutlineKeydown = (event: KeyboardEvent) => {
		if (!messagesContainerElement || event.defaultPrevented) {
			return;
		}

		if (!MESSAGE_OUTLINE_SCROLL_KEYS.has(event.key)) {
			return;
		}

		if (isEditableMessageOutlineTarget(event.target)) {
			return;
		}

		const activeElement = document.activeElement;
		if (
			activeElement &&
			activeElement !== document.body &&
			!messagesContainerElement.contains(activeElement)
		) {
			return;
		}

		revealMessageOutline();
	};

	setContext<MessageOutlineVisibilityContext>('messageOutlineVisibility', {
		scrollVisibleStore: messageOutlineVisibleStore,
		reveal: revealMessageOutline
	});

	const buildPersistedChatData = (
		historyState,
		messages = createMessagesList(historyState, historyState.currentId),
		persistedSelectionThreads: PersistedSelectionThreads = selectionThreads
	) => ({
		models: selectedModels,
		model_selection_hints: buildPersistedModelSelectionHints(selectedModels),
		history: historyState,
		messages,
		params,
		files: chatFiles,
		assistant: activeAssistant ?? undefined,
		composer_state: buildComposerStatePayload(),
		selectionThreads: persistedSelectionThreads
	});

	$: {
		const currentSystemPrompt = typeof params?.system === 'string' ? params.system : null;
		currentSystemPrompt;
		activeAssistant;

		persistChatSessionState($chatId || chatIdProp);
	}

	$: {
		const composerStateSignature = JSON.stringify(buildComposerStatePayload());
		composerStateSignature;
		persistChatComposerState($chatId || chatIdProp);
	}

	// 正向同步: Controls(params) → ThinkingControl(reasoningEffort/maxThinkingTokens)
	$: {
		const paramEffort = normalizeReasoningEffortValue(params?.reasoning_effort ?? null);
		const paramTokens = normalizeThinkingTokenValue(params?.max_thinking_tokens ?? null);
		const resolvedModelIds = getResolvedSelectedModelIds();
		const singleModel = getSingleSelectedReasoningModel();
		const defaultEffort = getModelDefaultReasoningEffort(singleModel);
		const hasSingleModelSelection = resolvedModelIds.length === 1 && !!singleModel;

		if (resolvedModelIds.length !== 1) {
			if (paramEffort !== null || paramTokens !== null) {
				setSharedReasoningState({ effort: null, tokens: null });
			} else {
				syncReasoningUiState(null, null);
			}
		} else if (hasSingleModelSelection && defaultEffort !== null && paramEffort === null && paramTokens === null) {
			setSharedReasoningState({ effort: defaultEffort, tokens: null });
		} else if (paramTokens !== null && paramTokens > 0) {
			setSharedReasoningState({ effort: null, tokens: paramTokens, syncParams: false });
		} else {
			syncReasoningUiState(paramEffort, paramTokens);
		}
	}

	$: {
		const selectionKey = getResolvedSelectedModelIds().join('|');
		if (!reasoningSelectionTrackingReady) {
			lastReasoningSelectionKey = selectionKey;
		} else if (selectionKey !== lastReasoningSelectionKey) {
			lastReasoningSelectionKey = selectionKey;
			applyReasoningSelectionDefaults();
		}
	}

	$: {
		const selectionDrivenState = getSelectionDrivenWebSearchState();
		if (
			webSearchSelectionSyncReady &&
			webSearchModeSource !== 'user' &&
			selectionDrivenState &&
			(webSearchMode !== selectionDrivenState.mode ||
				webSearchModeSource !== selectionDrivenState.source)
		) {
			webSearchMode = selectionDrivenState.mode;
			webSearchModeSource = selectionDrivenState.source;
			persistChatComposerState();
		}
	}

	$: if (!chatIdProp) {
		lastRequestedChatIdProp = '';
	} else if (lastRequestedChatIdProp !== chatIdProp) {
		lastRequestedChatIdProp = chatIdProp;
		const targetChatId = chatIdProp;
		const loadToken = ++activeChatLoadToken;

		(async () => {
			loading = true;
			cancelPendingAutoScrollFrames();
			composerStateSyncReady = false;
			resetReasoningSelectionTracking();
			webSearchSelectionSyncReady = false;

			prompt = '';
			files = [];
			selectedToolIds = [];
			toolSelectionTouched = false;
			selectedSkillIds = [];
			skillSelectionTouched = false;
			hasPersistedComposerState = false;
			webSearchMode = getPreferredDefaultWebSearchMode();
			webSearchModeSource = 'default';
			imageGenerationEnabled = false;
			imageGenerationOptions = {};
			reasoningEffort = null;
			maxThinkingTokens = null;

			const loaded = await loadChat(targetChatId);

			if (loadToken !== activeChatLoadToken || targetChatId !== chatIdProp) {
				return;
			}

			if (!loaded) {
				await goto('/');
				return;
			}

			if (!hasPersistedComposerState) {
				restoreChatSessionState(targetChatId);
			}

			const input = readChatInputState(targetChatId);
			if (input) {
				try {
					prompt = input.prompt;
					files = input.files;
				} catch (e) {}
			}

			loading = false;
			await tick();
			scrollToBottomImmediately();
			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
			composerStateSyncReady = true;
			webSearchSelectionSyncReady = true;
			initializeReasoningSelectionTracking();
		})();
	}

	$: if (selectedModels) {
		saveSessionSelectedModels();
	}

	const saveSessionSelectedModels = () => {
		if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
			return;
		}
		const scopedKey = getSelectedModelsStorageKey();
		const legacyKey = getLegacySelectedModelsStorageKey();
		sessionStorage.setItem(scopedKey, JSON.stringify(selectedModels));
		if (legacyKey !== scopedKey) {
			sessionStorage.removeItem(legacyKey);
		}
	};

	// When models finish loading after page refresh, restore selection from sessionStorage
	// (initNewChat may have run before models were available, causing modelsMap validation to clear the selection)
	// Skip restoration when freshChatActive is true — the empty selection is intentional.
	$: if (
		modelsMap.size > 0 &&
		!chatIdProp &&
		!freshChatActive &&
		(selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === ''))
	) {
		const scopedKey = getSelectedModelsStorageKey();
		const legacyKey = getLegacySelectedModelsStorageKey();
		const { value, usedLegacy } = readStorageItem(sessionStorage, scopedKey, legacyKey);
		if (value) {
			try {
				const stored = JSON.parse(value);
				const valid = stored
					.map((id: string) => {
						const resolution = resolveChatModelSelection($models, { value: id });
						return resolution.status === 'resolved' ? resolution.value : resolution.value;
					})
					.filter((id: string) => id);
				if (valid.length > 0) {
					selectedModels = valid;
					if (usedLegacy) {
						migrateStorageItem(sessionStorage, scopedKey, legacyKey, value);
					}
				}
			} catch {}
		}
	}

	$: if ($tools && (atSelectedModel || selectedModels)) {
		setToolIds();
	}

	$: if ($user && $skillsStore && (atSelectedModel || selectedModels)) {
		setSkillIds();
	}

	$: if ($skillsStore?.length) {
		const filteredSkillIds = filterVisibleSkillIds(selectedSkillIds);
		if (!arraysEqual(filteredSkillIds, selectedSkillIds)) {
			selectedSkillIds = filteredSkillIds;
		}
	}

	const setToolIds = async () => {
		if (!$tools) {
			tools.set(await getTools(localStorage.token));
		}

		if (hasPersistedComposerState || toolSelectionTouched) {
			return;
		}

		if (selectedModels.length !== 1 && !atSelectedModel) {
			return;
		}

		const model = atSelectedModel ?? getModelById(selectedModels[0]);
		if (model) {
			selectedToolIds = (model?.info?.meta?.toolIds ?? []).filter((id) =>
				$tools.find((t) => t.id === id)
			);
		}
	};

	let isLoadingSkills = false;
	let hasResolvedSkills = false;
	const setSkillIds = async () => {
		if (!$user || !localStorage.token) {
			return;
		}

		if (!hasResolvedSkills && (!$skillsStore || $skillsStore.length === 0)) {
			if (isLoadingSkills) {
				return;
			}
			isLoadingSkills = true;
			try {
				const latestSkills = (await getSkills(localStorage.token).catch(() => null)) ?? [];
				hasResolvedSkills = true;
				// 关键：仅在内容真正变化时 set，避免写入新数组引用触发响应式循环
				const current = $skillsStore ?? [];
				const changed =
					latestSkills.length !== current.length ||
					latestSkills.some((s, i) => s?.id !== current[i]?.id);
				if (changed) {
					skillsStore.set(latestSkills);
				}
				if (latestSkills.length === 0 && selectedSkillIds.length > 0) {
					selectedSkillIds = [];
				}
			} finally {
				isLoadingSkills = false;
			}
		}

		if (hasPersistedComposerState || skillSelectionTouched) {
			return;
		}

		if (selectedModels.length !== 1 && !atSelectedModel) {
			return;
		}

		const model = atSelectedModel ?? getModelById(selectedModels[0]);
		if (model) {
			selectedSkillIds = filterVisibleSkillIds(model?.info?.meta?.skillIds ?? []);
		}
	};

	const armOverviewNavigationEnd = (delay = 450) => {
		if (_overviewNavigationTimer !== null) {
			clearTimeout(_overviewNavigationTimer);
		}

		_overviewNavigationTimer = setTimeout(() => {
			overviewNavigationInFlight = false;
			_overviewNavigationTimer = null;
		}, delay);
	};

	const showMessage = async (
		message,
		options: {
			source?: 'overview' | 'default';
		} = {}
	) => {
		const _chatId = $chatId;
		let _messageId = message.id;
		const isOverviewSource = options.source === 'overview';

		let messageChildrenIds = [];
		if (_messageId === null) {
			messageChildrenIds = Object.keys(history.messages).filter(
				(id) => history.messages[id].parentId === null
			);
		} else {
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		while (messageChildrenIds.length !== 0) {
			_messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		history.currentId = _messageId;
		if (isOverviewSource) {
			overviewPinnedMessageId = message.id;
			overviewNavigationInFlight = true;
			armOverviewNavigationEnd();
		} else {
			overviewPinnedMessageId = null;
		}
		overviewFocusedMessageId.set(message.id);

		await tick();
		await tick();
		await tick();

		const messageElement = document.getElementById(`message-${message.id}`);
		if (messageElement) {
			messageElement.scrollIntoView({ behavior: 'smooth' });
		}

		await tick();
		saveChatHandler(_chatId, history);
	};

	const chatEventHandler = async (event, cb) => {
		if (event.chat_id === $chatId) {
			await tick();
			let message = history.messages[event.message_id];

			if (message) {
				const type = event?.data?.type ?? null;
				const data = event?.data?.data ?? null;

				if (type === 'status') {
					if (message?.statusHistory) {
						message.statusHistory.push(data);
					} else {
						message.statusHistory = [data];
					}
				} else if (type === 'chat:completion') {
					await chatCompletionEventHandler(data, message, event.chat_id);
				} else if (type === 'chat:message:delta' || type === 'message') {
					getResponseAnimationController(message).enqueue(data.content ?? '');
				} else if (type === 'chat:message' || type === 'replace') {
					await releaseResponseAnimationController(message.id, { flush: false });
					message.content = data.content;
				} else if (type === 'chat:message:files' || type === 'files') {
					message.files = mergeMessageFiles(message.files, data.files ?? []);
					if (shouldAutoScrollOnStreaming()) {
						scrollToBottom();
					}
				} else if (type === 'chat:message:follow_ups') {
					message.followUps = data?.follow_ups ?? [];
					if (shouldAutoScrollOnStreaming()) {
						scrollToBottom();
					}
				} else if (type === 'chat:title') {
					chatTitle.set(data);
					currentChatPage.set(1);
					await chats.set(await getChatList(localStorage.token, $currentChatPage));
				} else if (type === 'chat:tags') {
					chat = await getChatById(localStorage.token, $chatId);
					allTags.set(await getAllTags(localStorage.token));
				} else if (type === 'source' || type === 'citation') {
					if (data?.type === 'code_execution') {
						// Code execution; update existing code execution by ID, or add new one.
						if (!message?.code_executions) {
							message.code_executions = [];
						}

						const existingCodeExecutionIndex = message.code_executions.findIndex(
							(execution) => execution.id === data.id
						);

						if (existingCodeExecutionIndex !== -1) {
							message.code_executions[existingCodeExecutionIndex] = data;
						} else {
							message.code_executions.push(data);
						}

						message.code_executions = message.code_executions;
					} else {
						// Regular source.
						if (message?.sources) {
							message.sources.push(data);
						} else {
							message.sources = [data];
						}
					}
				} else if (type === 'notification') {
					const toastType = data?.type ?? 'info';
					const toastContent = data?.content ?? '';

					if (toastType === 'success') {
						toast.success(toastContent);
					} else if (toastType === 'error') {
						toast.error(toastContent);
					} else if (toastType === 'warning') {
						toast.warning(toastContent);
					} else {
						toast.info(toastContent);
					}
				} else if (type === 'confirmation') {
					eventCallback = cb;

					eventConfirmationInput = false;
					showEventConfirmation = true;

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
				} else if (type === 'execute') {
					eventCallback = cb;

					try {
						// Use Function constructor to evaluate code in a safer way
						const asyncFunction = new Function(`return (async () => { ${data.code} })()`);
						const result = await asyncFunction(); // Await the result of the async function

						if (cb) {
							cb(result);
						}
					} catch (error) {
						console.error('Error executing code:', error);
					}
				} else if (type === 'input') {
					eventCallback = cb;

					eventConfirmationInput = true;
					showEventConfirmation = true;

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
					eventConfirmationInputPlaceholder = data.placeholder;
					eventConfirmationInputValue = data?.value ?? '';
				} else {
					// no-op: unknown event type
				}

				history.messages[event.message_id] = message;
			}
		}
	};

	const onMessageHandler = async (event: {
		origin: string;
		data: { type: string; text: string };
	}) => {
		if (event.origin !== window.origin) {
			return;
		}

		// Replace with your iframe's origin
		if (event.data.type === 'input:prompt') {
			console.debug(event.data.text);

			const inputElement = document.getElementById('chat-input');

			if (inputElement) {
				prompt = event.data.text;
				inputElement.focus();
			}
		}

		if (event.data.type === 'action:submit') {
			console.debug(event.data.text);

			if (prompt !== '') {
				await tick();
				submitPrompt(prompt);
			}
		}

		if (event.data.type === 'input:prompt:submit') {
			console.debug(event.data.text);

			if (event.data.text !== '') {
				await tick();
				submitPrompt(event.data.text);
			}
		}
	};

	const onSetInputHandler = async (event: CustomEvent<{ prompt: string }>) => {
		const text = event?.detail?.prompt ?? '';
		if (!text) {
			return;
		}

		prompt = text;
		await tick();

		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();
	};

	onMount(async () => {
		window.addEventListener('message', onMessageHandler);
		window.addEventListener('chat:set-input', onSetInputHandler as EventListener);
		window.addEventListener('keydown', handleMessageOutlineKeydown, true);
		window.addEventListener('pointerup', clearMessageOutlineScrollbarDragPrime, true);
		window.addEventListener('pointercancel', clearMessageOutlineScrollbarDragPrime, true);
		$socket?.on('chat-events', chatEventHandler);

		if (!chatIdProp && !$chatId) {
			chatIdUnsubscriber = chatId.subscribe(async (value) => {
				if (!value) {
					await tick(); // Wait for DOM updates
					await initNewChat();
				}
			});
		} else if (chatIdProp) {
			if ($temporaryChatEnabled) {
				await goto('/');
			}
		}

		if (!chatIdProp) {
			const input = readChatInputState(chatIdProp);
			if (input) {
				try {
					prompt = input.prompt;
					files = input.files;
				} catch (e) {
					prompt = '';
					files = [];
					selectedToolIds = [];
					toolSelectionTouched = false;
					selectedSkillIds = [];
					skillSelectionTouched = false;
					webSearchMode = getPreferredDefaultWebSearchMode();
					webSearchModeSource = 'default';
					imageGenerationEnabled = false;
					imageGenerationOptions = {};
				}
			}
			restoreChatSessionState(chatIdProp);
			composerStateSyncReady = true;
			webSearchSelectionSyncReady = true;
		}

		showControls.subscribe(async (value) => {
			if (controlPane && !$mobile) {
				try {
					if (value) {
						await controlPaneComponent.openPane();
					} else {
						controlPane.collapse();
					}
				} catch (e) {
					// ignore
				}
			}

			if (!value) {
				showCallOverlay.set(false);
				showOverview.set(false);
				showArtifacts.set(false);
			}
		});

		selectedAssistantSceneUnsubscriber = selectedAssistantScene.subscribe((assistantScene) => {
			if (assistantScene && activeAssistant) {
				deactivateAssistant();
			}

			if (
				assistantScene?.id &&
				!chatIdProp &&
				!$chatId &&
				JSON.stringify(selectedModels) !== JSON.stringify([assistantScene.id])
			) {
				selectedModels = [assistantScene.id];
			}
		});

		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();

		chats.subscribe(() => {});

		if ($models.length === 0) {
			void ensureModels(localStorage.token, { reason: 'chat' }).catch((error) => {
				const msg = error instanceof Error ? error.message : `${error}`;
				toast.error(msg);
			});
		}

		// Restore follow mode when the bottom sentinel becomes visible again.
		await tick();
		if (scrollSentinel && messagesContainerElement) {
			scrollObserver = new IntersectionObserver(
				([entry]) => {
					if (entry.isIntersecting) {
						userHasScrolled = false;
						autoScroll = true;
					} else if (
						!isAutoScrolling &&
						(userHasScrolled ||
							!isStreamingResponseActive() ||
							!($settings?.enableAutoScrollOnStreaming ?? true))
					) {
						autoScroll = false;
					}
				},
				{ root: messagesContainerElement, threshold: 0 }
			);
			scrollObserver.observe(scrollSentinel);
		}
	});

	onDestroy(() => {
		chatIdUnsubscriber?.();
		selectedAssistantSceneUnsubscriber?.();
		clearMessageOutlineHideTimer();
		messageOutlineVisibleStore.set(false);
		if (selectionThreadsPersistTimeout) {
			clearTimeout(selectionThreadsPersistTimeout);
			selectionThreadsPersistTimeout = null;
		}
		if (composerStatePersistTimeout) {
			clearTimeout(composerStatePersistTimeout);
			composerStatePersistTimeout = null;
		}
		scrollObserver?.disconnect();
		cancelScheduledScrollToBottom();
		clearResponseAnimationControllers();
		overviewPinnedMessageId = null;
		overviewNavigationInFlight = false;
		overviewFocusedMessageId.set(null);
		window.removeEventListener('message', onMessageHandler);
		window.removeEventListener('chat:set-input', onSetInputHandler as EventListener);
		window.removeEventListener('keydown', handleMessageOutlineKeydown, true);
		window.removeEventListener('pointerup', clearMessageOutlineScrollbarDragPrime, true);
		window.removeEventListener('pointercancel', clearMessageOutlineScrollbarDragPrime, true);
		$socket?.off('chat-events', chatEventHandler);
	});

	$: if ($showOverview) {
		void tick().then(() => {
			scheduleOverviewFocusedMessageSync();
		});
	} else {
		overviewPinnedMessageId = null;
		overviewNavigationInFlight = false;
		overviewFocusedMessageId.set(null);
	}

	$: if ($showOverview && history.currentId) {
		void tick().then(() => {
			scheduleOverviewFocusedMessageSync();
		});
	}

	// File upload functions

	const getUploadLocalizeOptions = () => ({
		isAdmin: $user?.role === 'admin'
	});

	const setLocalUploadFailure = (tempItemId: string, error: unknown) => {
		const localized = getLocalizedFileUploadDiagnostic(
			error,
			$i18n.t.bind($i18n),
			getUploadLocalizeOptions()
		);
		const diagnostic = getFileUploadDiagnostic(error) ?? {
			code: localized.code,
			title: localized.title,
			message: localized.message,
			hint: localized.hint,
			blocking: localized.blocking
		};

		files = files.map((item) =>
			item?.itemId === tempItemId
				? {
						...item,
						status: 'failed',
						error: localized.message,
						errorTitle: localized.title,
						errorHint: localized.hint,
						diagnostic
					}
				: item
		);

		toast.error(localizeFileUploadError(error, $i18n.t.bind($i18n), getUploadLocalizeOptions()));
	};

	const uploadGoogleDriveFile = async (fileData) => {
		console.log('Starting uploadGoogleDriveFile with:', {
			id: fileData.id,
			name: fileData.name,
			url: fileData.url,
			headers: {
				Authorization: `Bearer ${token}`
			}
		});

		// Validate input
		if (!fileData?.id || !fileData?.name || !fileData?.url || !fileData?.headers?.Authorization) {
			throw new Error('Invalid file data provided');
		}

		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: fileData.url,
			name: fileData.name,
			collection_name: '',
			status: 'uploading',
			error: '',
			errorTitle: '',
			errorHint: '',
			diagnostic: null,
			itemId: tempItemId,
			size: 0
		};

		try {
			files = [...files, fileItem];
			console.log('Processing web file with URL:', fileData.url);

			// Configure fetch options with proper headers
			const fetchOptions = {
				headers: {
					Authorization: fileData.headers.Authorization,
					Accept: '*/*'
				},
				method: 'GET'
			};

			// Attempt to fetch the file
			console.log('Fetching file content from Google Drive...');
			const fileResponse = await fetch(fileData.url, fetchOptions);

			if (!fileResponse.ok) {
				const errorText = await fileResponse.text();
				throw new Error(`Failed to fetch file (${fileResponse.status}): ${errorText}`);
			}

			// Get content type from response
			const contentType = fileResponse.headers.get('content-type') || 'application/octet-stream';
			console.log('Response received with content-type:', contentType);

			// Convert response to blob
			console.log('Converting response to blob...');
			const fileBlob = await fileResponse.blob();

			if (fileBlob.size === 0) {
				throw new Error('Retrieved file is empty');
			}

			console.log('Blob created:', {
				size: fileBlob.size,
				type: fileBlob.type || contentType
			});

			// Create File object with proper MIME type
			const file = new File([fileBlob], fileData.name, {
				type: fileBlob.type || contentType
			});

			console.log('File object created:', {
				name: file.name,
				size: file.size,
				type: file.type
			});

			if (file.size === 0) {
				throw new Error('Created file is empty');
			}

			// Upload file to server
			console.log('Uploading file to server...');
			const uploadedFile = await uploadFile(localStorage.token, file);

			if (!uploadedFile) {
				throw new Error('Server returned null response for file upload');
			}

			console.log('File uploaded successfully:', uploadedFile);

			// Update file item with upload results
			fileItem.status = 'uploaded';
			fileItem.file = uploadedFile;
			fileItem.id = uploadedFile.id;
			fileItem.size = file.size;
			fileItem.collection_name = uploadedFile?.meta?.collection_name;
			fileItem.processing_mode = uploadedFile?.meta?.processing_mode;
			if (uploadedFile?.meta?.processing_mode === 'full_context') {
				fileItem.context = 'full';
			}
			fileItem.url = `${WEBUI_API_BASE_URL}/files/${uploadedFile.id}`;

			files = files;
			toast.success($i18n.t('File uploaded successfully'));
		} catch (e) {
			console.error('Error uploading file:', e);
			setLocalUploadFailure(tempItemId, e);
		}
	};

	const uploadWeb = async (url) => {
		console.log(url);

		const fileItem = {
			type: 'doc',
			name: url,
			collection_name: '',
			status: 'uploading',
			url: url,
			error: ''
		};

		try {
			files = [...files, fileItem];
			const res = await processWeb(localStorage.token, '', url);

			if (res) {
				fileItem.status = 'uploaded';
				fileItem.collection_name = res.collection_name;
				fileItem.file = {
					...res.file,
					...fileItem.file
				};

				files = files;
			}
		} catch (e) {
			// Remove the failed doc from the files array
			files = files.filter((f) => f.name !== url);
			toast.error(JSON.stringify(e));
		}
	};

	const uploadYoutubeTranscription = async (url) => {
		console.log(url);

		const fileItem = {
			type: 'doc',
			name: url,
			collection_name: '',
			status: 'uploading',
			context: 'full',
			url: url,
			error: ''
		};

		try {
			files = [...files, fileItem];
			const res = await processYoutubeVideo(localStorage.token, url);

			if (res) {
				fileItem.status = 'uploaded';
				fileItem.collection_name = res.collection_name;
				fileItem.file = {
					...res.file,
					...fileItem.file
				};
				files = files;
			}
		} catch (e) {
			// Remove the failed doc from the files array
			files = files.filter((f) => f.name !== url);
			toast.error(`${e}`);
		}
	};

	const syncTemporaryChatState = (settingsSource = $settings) => {
		const defaultEnabled = settingsSource?.temporaryChatByDefault ?? false;
		const { allowed, enforced } = getTemporaryChatAccess($user);
		const enabled = resolveTemporaryChatEnabled({
			searchParams: $page.url.searchParams,
			defaultEnabled,
			enforced,
			allowed
		});

		persistTemporaryChatOverride(enabled, { defaultEnabled, enforced, allowed });
		temporaryChatEnabled.set(enabled);

		return { enabled, defaultEnabled, enforced, allowed };
	};

	//////////////////////////
	// Web functions
	//////////////////////////

	const initNewChat = async (options: { fresh?: boolean } = {}) => {
		const fresh = options.fresh ?? false;
		freshChatActive = fresh;
		composerStateSyncReady = false;
		resetReasoningSelectionTracking();
		webSearchSelectionSyncReady = false;

		if ($page.url.searchParams.get('models')) {
			selectedModels = $page.url.searchParams.get('models')?.split(',');
		} else if ($page.url.searchParams.get('model')) {
			const urlModels = $page.url.searchParams.get('model')?.split(',');

			if (urlModels.length === 1) {
				const m = getModelById(urlModels[0]);
				if (!m) {
					selectedModels = [getCanonicalModelId(urlModels[0]) || ''];
					const modelSelectorButton = document.getElementById('model-selector-0-button');
					if (modelSelectorButton) {
						modelSelectorButton.click();
						await tick();

						const modelSelectorInput = document.getElementById('model-search-input');
						if (modelSelectorInput) {
							modelSelectorInput.focus();
							modelSelectorInput.value = urlModels[0];
							modelSelectorInput.dispatchEvent(new Event('input'));
						}
					}
				} else {
					selectedModels = urlModels.map((id) => getCanonicalModelId(id)).filter(Boolean);
				}
			} else {
				selectedModels = urlModels.map((id) => getCanonicalModelId(id)).filter(Boolean);
			}
		} else if (!fresh && $selectedAssistantScene?.id) {
			selectedModels = [$selectedAssistantScene.id];
		} else if (!fresh) {
			const scopedKey = getSelectedModelsStorageKey();
			const legacyKey = getLegacySelectedModelsStorageKey();
			const { value, usedLegacy } = readStorageItem(sessionStorage, scopedKey, legacyKey);
			if (value) {
				const storedSelectedModels = safeParseStoredJson<string[] | null>(value, null);
				if (Array.isArray(storedSelectedModels)) {
					selectedModels = storedSelectedModels;
					if (usedLegacy) {
						migrateStorageItem(sessionStorage, scopedKey, legacyKey, value);
					}
				}
			} else {
				if ($settings?.models) {
					selectedModels = $settings?.models;
				}
			}
		} else if ($settings?.models) {
			selectedModels = $settings?.models;
		} else if (fresh) {
			// fresh=true but no default model configured — reset to empty so user must choose
			selectedModels = [''];
		}

		// Only validate against modelsMap when models are actually loaded.
		// On page refresh, initNewChat() may run before models load from API —
		// filtering against an empty modelsMap would discard the valid sessionStorage value.
		// The recovery block (line 573) and ModelSelector validation handle deferred validation.
		if (modelsMap.size > 0) {
			const hadExplicitSelectedModels = selectedModels.some(
				(modelId) => `${modelId ?? ''}`.trim() !== ''
			);
			selectedModels = selectedModels
				.map((modelId) => {
					const resolution = resolveChatModelSelection($models, { value: modelId });
					return resolution.status === 'resolved' ? resolution.value : resolution.value;
				})
				.filter((modelId) => modelId);
			if (
				selectedModels.length === 0 ||
				(selectedModels.length === 1 && selectedModels[0] === '')
			) {
				if (hadExplicitSelectedModels) {
					selectedModels = [''];
				} else if (!fresh && $models.length > 0) {
					// Non-fresh: auto-select first available model as fallback
					selectedModels = [getModelSelectionId($models[0])];
				} else {
					// Fresh with no default: keep empty so user must choose
					selectedModels = [''];
				}
			}
		}

		let temporaryChatState = syncTemporaryChatState();
		messageQueue = [];

		await showControls.set(false);
		await showCallOverlay.set(false);
		await showOverview.set(false);
		await showArtifacts.set(false);

		if ($page.url.pathname.includes('/c/')) {
			window.history.replaceState(
				window.history.state,
				'',
				getTemporaryChatNavigationPath({
					currentUrl: new URL(window.location.href),
					enabled: temporaryChatState.enabled,
					defaultEnabled: temporaryChatState.defaultEnabled,
					enforced: temporaryChatState.enforced,
					allowed: temporaryChatState.allowed,
					pathname: '/'
				})
			);
		}

		resetAutoScrollLock();

		await chatId.set('');
		await chatTitle.set('');

		history = {
			messages: {},
			currentId: null
		};
		setSelectionThreadsState(createEmptySelectionThreads());
		expandedSelectionThreadId.set(null);
		clearResponseAnimationControllers();

		if (fresh) {
			chat = null;
			tags = [];
			taskIds = null;
			processing = '';
			atSelectedModel = undefined;
			activeAssistant = null;
			prompt = '';
			files = [];
			selectedToolIds = [];
			toolSelectionTouched = false;
			selectedSkillIds = [];
			skillSelectionTouched = false;
			hasPersistedComposerState = false;
			imageGenerationEnabled = false;
			imageGenerationOptions = {};
			codeInterpreterEnabled = false;
		}

		chatFiles = [];
		params = {};
		hasPersistedComposerState = false;
		selectedToolIds = [];
		toolSelectionTouched = false;
		selectedSkillIds = [];
		skillSelectionTouched = false;

		if (fresh) {
			removeChatSessionState('');
			removeChatInputState('');
			reasoningEffort = null;
			maxThinkingTokens = null;
			webSearchMode = getPreferredDefaultWebSearchMode();
			webSearchModeSource = 'default';
		} else {
			reasoningEffort = null;
			maxThinkingTokens = null;
			webSearchMode = getPreferredDefaultWebSearchMode();
			webSearchModeSource = 'default';
			restoreChatSessionState('');
			if (reasoningEffort) {
				params.reasoning_effort = reasoningEffort;
			}
			if (maxThinkingTokens != null) {
				params.max_thinking_tokens = maxThinkingTokens;
			}
		}

		if ($page.url.searchParams.get('youtube')) {
			uploadYoutubeTranscription(
				`https://www.youtube.com/watch?v=${$page.url.searchParams.get('youtube')}`
			);
		}
		if ($page.url.searchParams.get('web-search') === 'true') {
			webSearchMode = 'halo';
			webSearchModeSource = 'user';
		}

		if ($page.url.searchParams.get('image-generation') === 'true') {
			imageGenerationEnabled = true;
		}

		if ($page.url.searchParams.get('tools')) {
			selectedToolIds = $page.url.searchParams
				.get('tools')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
			toolSelectionTouched = true;
		} else if ($page.url.searchParams.get('tool-ids')) {
			selectedToolIds = $page.url.searchParams
				.get('tool-ids')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
			toolSelectionTouched = true;
		}

		if ($page.url.searchParams.get('skills')) {
			selectedSkillIds = $page.url.searchParams
				.get('skills')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
			skillSelectionTouched = true;
		} else if ($page.url.searchParams.get('skill-ids')) {
			selectedSkillIds = $page.url.searchParams
				.get('skill-ids')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
			skillSelectionTouched = true;
		}

		if ($page.url.searchParams.get('call') === 'true') {
			showCallOverlay.set(true);
			showControls.set(true);
		}

		if ($page.url.searchParams.get('q')) {
			prompt = $page.url.searchParams.get('q') ?? '';

			if (prompt) {
				await tick();
				submitPrompt(prompt);
			}
		}

		// Only validate model IDs when models are actually loaded
		if (modelsMap.size > 0) {
			selectedModels = selectedModels.map((modelId) => {
				const resolution = resolveChatModelSelection($models, { value: modelId });
				return resolution.status === 'resolved' ? resolution.value : resolution.value;
			});
		}

		const userSettings = await getUserSettings(localStorage.token);

		if (userSettings) {
			applyUserSettingsSnapshot(userSettings, get(settings) ?? {});
			temporaryChatState = syncTemporaryChatState(userSettings.ui);
		} else {
			const fallbackSettings = get(settings) ?? {};
			settings.set(fallbackSettings);
			temporaryChatState = syncTemporaryChatState(fallbackSettings);
		}

		if (fresh && $page.url.searchParams.get('web-search') !== 'true') {
			webSearchMode = getPreferredWebSearchMode(userSettings?.ui ?? $settings, 'off');
			webSearchModeSource = 'default';
		}

		if (fresh) {
			const pendingAssistant = toChatAssistantSnapshot(
				safeParseStoredJson<Record<string, unknown> | null>(
					sessionStorage.getItem(PENDING_ASSISTANT_STORAGE_KEY),
					null
				)
			);
			sessionStorage.removeItem(PENDING_ASSISTANT_STORAGE_KEY);

			if (pendingAssistant) {
				selectedAssistantScene.set(null);
				activateAssistant(pendingAssistant);
			}
		}

		if (window.location.pathname === '/') {
			window.history.replaceState(
				window.history.state,
				'',
				getTemporaryChatNavigationPath({
					currentUrl: new URL(window.location.href),
					enabled: temporaryChatState.enabled,
					defaultEnabled: temporaryChatState.defaultEnabled,
					enforced: temporaryChatState.enforced,
					allowed: temporaryChatState.allowed,
					pathname: '/'
				})
			);
		}

		const chatInput = document.getElementById('chat-input');
		setTimeout(() => chatInput?.focus(), 0);
		composerStateSyncReady = true;
		webSearchSelectionSyncReady = true;
		initializeReasoningSelectionTracking();

		if (fresh && $page.url.searchParams.get('fresh-chat') === 'true') {
			const url = new URL($page.url);
			url.searchParams.delete('fresh-chat');
			window.history.replaceState(history.state, '', `${url.pathname}${url.search}${url.hash}`);
		}
	};

	$: {
		const freshChatRequested =
			!chatIdProp &&
			$page.url.pathname === '/' &&
			$page.url.searchParams.get('fresh-chat') === 'true';
		const requestKey = freshChatRequested ? $page.url.toString() : '';

		if (freshChatRequested && requestKey !== lastFreshChatRequest) {
			lastFreshChatRequest = requestKey;
			(async () => {
				await tick();
				await initNewChat({ fresh: true });
			})();
		} else if (!freshChatRequested) {
			lastFreshChatRequest = '';
		}
	}

	const loadChat = async (targetChatId: string = chatIdProp) => {
		const navigationId = targetChatId;
		chatId.set(targetChatId);
		tags = [];
		taskIds = null;
		const chatContextPromise = getChatContextById(localStorage.token, targetChatId).catch(() => ({
			tags: [],
			task_ids: []
		}));

		chat = await getChatById(localStorage.token, targetChatId).catch(() => null);

		if (navigationId !== chatIdProp) return null;

		if (chat) {
			const chatContent = chat.chat;

			if (chatContent) {
				if ($models.length === 0) {
					await ensureModels(localStorage.token, { reason: 'chat-history-model-recovery' }).catch(() => {});
					await tick();
				}

				const loadedModels =
					(chatContent?.models ?? undefined) !== undefined ? chatContent.models : chatContent.model;
				history =
					(chatContent?.history ?? undefined) !== undefined
						? chatContent.history
						: convertMessagesToHistory(chatContent.messages);
				selectedModels =
					modelsMap.size > 0
						? recoverLoadedChatModelState(chatContent, history, loadedModels)
						: Array.isArray(loadedModels)
							? loadedModels
							: [loadedModels ?? ''];

				chatTitle.set(chatContent.title);

				params = chatContent?.params ?? {};
				activeAssistant = toChatAssistantSnapshot(chatContent?.assistant ?? null);
				chatFiles = chatContent?.files ?? [];
				hasPersistedComposerState = false;
				applyComposerState(chatContent?.composer_state, { markPersisted: true });
				setRuntimeSelectionThreadsState(normalizeSelectionThreads(chatContent?.selectionThreads));
				expandedSelectionThreadId.set(null);

				if (chat.assistant_id) {
					if ($models.length === 0) {
						await ensureModels(localStorage.token, { reason: 'chat-assistant-scene' }).catch(() => {});
						await tick();
					}

					const assistantScene =
						getModelById(chat.assistant_id) ??
						(await getWorkspaceModelById(localStorage.token, chat.assistant_id).catch(() => null));

					selectedAssistantScene.set(assistantScene ?? null);
				} else {
					selectedAssistantScene.set(null);
				}

				void (async () => {
					const nextContext = await chatContextPromise;

					if (navigationId !== chatIdProp) return;

					tags = nextContext?.tags ?? [];
					taskIds = nextContext?.task_ids ?? [];
					reconcileLoadedAssistantMessages(taskIds);
				})();

				resetAutoScrollLock();
				return true;
			} else {
				return null;
			}
		}
	};

	const reconcileLoadedAssistantMessages = (activeTaskIds: string[] | null) => {
		const hasPendingTask = Array.isArray(activeTaskIds) && activeTaskIds.length > 0;
		const pendingAssistantIds = new Set<string>();

		if (hasPendingTask) {
			for (const [messageId, message] of Object.entries(history.messages)) {
				if (message?.role === 'assistant' && message.done === false) {
					pendingAssistantIds.add(messageId);
				}
			}

			const currentMessage = history.currentId ? history.messages[history.currentId] : null;
			if (currentMessage?.role === 'assistant') {
				pendingAssistantIds.add(currentMessage.id);

				const parentMessage = currentMessage.parentId
					? history.messages[currentMessage.parentId]
					: null;
				for (const siblingId of parentMessage?.childrenIds ?? []) {
					const sibling = history.messages[siblingId];
					if (sibling?.role === 'assistant' && sibling.done !== true) {
						pendingAssistantIds.add(siblingId);
					}
				}
			}

			if (pendingAssistantIds.size === 0) {
				const latestAssistantEntry = Object.entries(history.messages)
					.filter(([, message]) => message?.role === 'assistant')
					.sort(([, a], [, b]) => (a?.timestamp ?? 0) - (b?.timestamp ?? 0))
					.at(-1);

				if (latestAssistantEntry) {
					pendingAssistantIds.add(latestAssistantEntry[0]);
				}
			}
		}

		for (const [messageId, message] of Object.entries(history.messages)) {
			if (message?.role !== 'assistant') {
				continue;
			}

			if (hasPendingTask && pendingAssistantIds.has(messageId)) {
				message.done = false;
			} else {
				message.done = true;
			}
		}

		activeChatIds.update((ids) => {
			const next = new Set(ids);
			if (hasPendingTask && $chatId) {
				next.add($chatId);
			} else {
				next.delete($chatId);
			}
			return next;
		});
	};

	const isNearBottom = () => {
		if (!messagesContainerElement) {
			return true;
		}

		return (
			messagesContainerElement.scrollHeight -
				messagesContainerElement.scrollTop -
				messagesContainerElement.clientHeight <
			20
		);
	};

	const isStreamingResponseActive = () => {
		if (Array.isArray(taskIds) && taskIds.length > 0) {
			return true;
		}

		if (!history.currentId) {
			return false;
		}

		const currentMessage = history.messages?.[history.currentId];
		if (!currentMessage) {
			return false;
		}

		if (currentMessage.role === 'assistant') {
			return currentMessage.done !== true;
		}

		return (currentMessage.childrenIds ?? []).some((messageId) => {
			const childMessage = history.messages?.[messageId];
			return childMessage?.role === 'assistant' && childMessage?.done !== true;
		});
	};

	const shouldAutoScrollOnStreaming = () => {
		return ($settings?.enableAutoScrollOnStreaming ?? true) && !userHasScrolled;
	};

	type ResponseAnimationController = {
		destroy: () => void;
		enqueue: (text: string) => void;
		flush: () => Promise<void>;
	};

	const responseAnimationControllers = new Map<string, ResponseAnimationController>();

	const emitLatestMessageSentence = (message) => {
		if (!($showCallOverlay || $settings?.responseAutoPlayback)) {
			return;
		}

		const messageContentParts = getMessageContentParts(
			message.content,
			$config?.audio?.tts?.split_on ?? 'punctuation'
		);
		messageContentParts.pop();

		if (
			messageContentParts.length > 0 &&
			messageContentParts[messageContentParts.length - 1] !== message.lastSentence
		) {
			message.lastSentence = messageContentParts[messageContentParts.length - 1];
			eventTarget.dispatchEvent(
				new CustomEvent('chat', {
					detail: {
						id: message.id,
						content: messageContentParts[messageContentParts.length - 1]
					}
				})
			);
		}
	};

	const appendAnimatedMessageContent = (message, text: string) => {
		if (!text || (message.content == '' && text == '\n')) {
			return;
		}

		message.content += text;

		if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
			navigator.vibrate(5);
		}

		emitLatestMessageSentence(message);
		history.messages[message.id] = message;

		if (shouldAutoScrollOnStreaming()) {
			scrollToBottom();
		}
	};

	const createResponseAnimationController = (message): ResponseAnimationController => ({
		destroy() {},
		enqueue(text: string) {
			appendAnimatedMessageContent(message, text);
		},
		async flush() {}
	});

	const getResponseAnimationController = (message) => {
		let controller = responseAnimationControllers.get(message.id);
		if (controller) {
			return controller;
		}

		controller = createResponseAnimationController(message);
		responseAnimationControllers.set(message.id, controller);
		return controller;
	};

	const releaseResponseAnimationController = async (
		messageId: string,
		{ flush = true }: { flush?: boolean } = {}
	) => {
		const controller = responseAnimationControllers.get(messageId);
		if (!controller) {
			return;
		}

		if (flush) {
			await controller.flush();
		}

		controller.destroy();
		responseAnimationControllers.delete(messageId);
	};

	const clearResponseAnimationControllers = () => {
		for (const controller of responseAnimationControllers.values()) {
			controller.destroy();
		}
		responseAnimationControllers.clear();
	};

	const resetAutoScrollLock = () => {
		userHasScrolled = false;
		autoScroll = true;
	};

	const resolveOverviewFocusedMessageId = () => {
		if (
			overviewPinnedMessageId &&
			history.messages?.[overviewPinnedMessageId] &&
			(overviewNavigationInFlight || !messagesContainerElement)
		) {
			return overviewPinnedMessageId;
		}

		if (!messagesContainerElement) {
			return history.currentId ?? null;
		}

		const containerRect = messagesContainerElement.getBoundingClientRect();
		const visibleMessages = Array.from(
			messagesContainerElement.querySelectorAll<HTMLElement>('[id^="message-"]')
		)
			.map((element) => {
				const messageId = element.id.replace(/^message-/, '');
				return {
					messageId,
					rect: element.getBoundingClientRect()
				};
			})
			.filter(
				({ messageId, rect }) =>
					Boolean(history.messages?.[messageId]) &&
					rect.bottom >= containerRect.top + 8 &&
					rect.top <= containerRect.bottom - 8
			);

		if (visibleMessages.length === 0) {
			if (overviewPinnedMessageId && history.messages?.[overviewPinnedMessageId]) {
				return overviewPinnedMessageId;
			}
			return history.currentId ?? null;
		}

		if (
			overviewPinnedMessageId &&
			visibleMessages.some(({ messageId }) => messageId === overviewPinnedMessageId)
		) {
			return overviewPinnedMessageId;
		}

		const anchorPadding = Math.min(72, containerRect.height / 2);
		const anchorY =
			containerRect.top +
			Math.min(
				Math.max(containerRect.height * 0.32, anchorPadding),
				containerRect.height - anchorPadding
			);

		const containingMessage = visibleMessages.find(
			({ rect }) => rect.top <= anchorY && rect.bottom >= anchorY
		);

		if (containingMessage) {
			return containingMessage.messageId;
		}

		return visibleMessages.reduce((closest, current) => {
			const currentDistance = Math.abs((current.rect.top + current.rect.bottom) / 2 - anchorY);
			const closestDistance = Math.abs((closest.rect.top + closest.rect.bottom) / 2 - anchorY);
			return currentDistance < closestDistance ? current : closest;
		}).messageId;
	};

	const syncOverviewFocusedMessage = () => {
		if (!$showOverview) {
			return;
		}

		overviewFocusedMessageId.set(resolveOverviewFocusedMessageId());
	};

	const scheduleOverviewFocusedMessageSync = () => {
		if (!$showOverview || _overviewFocusRafId !== null) {
			return;
		}

		_overviewFocusRafId = requestAnimationFrame(() => {
			_overviewFocusRafId = null;
			syncOverviewFocusedMessage();
		});
	};

	const cancelScheduledScrollToBottom = () => {
		if (_scrollRafId !== null) {
			cancelAnimationFrame(_scrollRafId);
			_scrollRafId = null;
		}
		if (_scrollResetRafId !== null) {
			cancelAnimationFrame(_scrollResetRafId);
			_scrollResetRafId = null;
		}
		if (_overviewFocusRafId !== null) {
			cancelAnimationFrame(_overviewFocusRafId);
			_overviewFocusRafId = null;
		}
		if (_overviewNavigationTimer !== null) {
			clearTimeout(_overviewNavigationTimer);
			_overviewNavigationTimer = null;
		}
	};

	const handleMessagesScroll = () => {
		if (!messagesContainerElement || isAutoScrolling) {
			return;
		}

		if (overviewNavigationInFlight) {
			autoScroll = isNearBottom();
			if (autoScroll) {
				userHasScrolled = false;
			}
			armOverviewNavigationEnd(120);
			return;
		}

		if (shouldRevealMessageOutlineFromScroll()) {
			revealMessageOutline();
		}

		if (overviewPinnedMessageId) {
			overviewPinnedMessageId = null;
		}

		if (isNearBottom()) {
			userHasScrolled = false;
			autoScroll = true;
			scheduleOverviewFocusedMessageSync();
			return;
		}

		userHasScrolled = true;
		autoScroll = false;
		scheduleOverviewFocusedMessageSync();
	};

	let _scrollRafId: number | null = null;
	const cancelPendingAutoScrollFrames = () => {
		if (_scrollRafId !== null) {
			cancelAnimationFrame(_scrollRafId);
			_scrollRafId = null;
		}

		if (_scrollResetRafId !== null) {
			cancelAnimationFrame(_scrollResetRafId);
			_scrollResetRafId = null;
		}

		isAutoScrolling = false;
	};

	const scrollToBottomImmediately = () => {
		if (!messagesContainerElement) {
			return;
		}

		cancelPendingAutoScrollFrames();
		resetAutoScrollLock();
		userHasScrolled = false;
		autoScroll = true;
		messagesContainerElement.scrollTop = messagesContainerElement.scrollHeight;
		scheduleOverviewFocusedMessageSync();
	};

	const scrollToBottom = async (behavior: ScrollBehavior = 'auto') => {
		if (_scrollRafId !== null) return;
		resetAutoScrollLock();
		_scrollRafId = requestAnimationFrame(() => {
			_scrollRafId = null;
			if (messagesContainerElement) {
				isAutoScrolling = true;
				messagesContainerElement.scrollTo({
					top: messagesContainerElement.scrollHeight,
					behavior
				});
				_scrollResetRafId = requestAnimationFrame(() => {
					_scrollResetRafId = null;
					isAutoScrolling = false;
					autoScroll = isNearBottom();
					scheduleOverviewFocusedMessageSync();
				});
			}
		});
	};
	const chatCompletedHandler = async (chatId, modelId, responseMessageId, messages) => {
		const responseModelIndex = history.messages[responseMessageId]?.modelIdx ?? 0;
		const responseModelResolution = resolveMessageModel(history.messages[responseMessageId]);
		if (
			isBlockingModelResolution(responseModelResolution) ||
			responseModelResolution.status !== 'resolved'
		) {
			await promptModelResolution(responseModelResolution, responseModelIndex);
			history.messages[responseMessageId].error = {
				type: 'model_resolution_error',
				content: responseModelResolution.status === 'ambiguous'
					? $i18n.t('Model connection is ambiguous. Please select the model again.')
					: $i18n.t('Model connection is unavailable. Please select the model again.')
			};
			await saveChatHandler(chatId, history);
			return;
		}
		applyResolvedMessageModel(history.messages[responseMessageId], responseModelResolution);
		modelId = responseModelResolution.value;
		const res = await chatCompleted(localStorage.token, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.usage ? { usage: m.usage } : {}),
				...(m.sources ? { sources: m.sources } : {})
			})),
			model_item: getModelById(modelId),
			chat_id: chatId,
			session_id: $socket?.id,
			id: responseMessageId
		}).catch(async (error) => {
			const resolutionDetail = getModelResolutionDetail(error);
			if (resolutionDetail) {
				await promptModelReselection({
					index: responseModelIndex,
					rawModelId: resolutionDetail.requestedModelId || `${modelId ?? ''}`.trim(),
					ambiguous: resolutionDetail.code === MODEL_CONNECTION_AMBIGUOUS_CODE
				});
				messages.at(-1).error = {
					type: 'model_resolution_error',
					content: resolutionDetail.message || formatError(error),
					detail: resolutionDetail
				};
				return null;
			}

			toast.error(formatError(error));
			messages.at(-1).error = { content: formatError(error) };

			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				if (message?.id) {
					// Add null check for message and message.id
					history.messages[message.id] = {
						...history.messages[message.id],
						...(history.messages[message.id].content !== message.content
							? { originalContent: history.messages[message.id].content }
							: {}),
						...message
					};
				}
			}
		}

		await tick();

		if ($chatId == chatId) {
			if (!$temporaryChatEnabled) {
				await saveChatHandler(chatId, history, {
					messages
				});
			}
		}

		taskIds = null;

		const parentId = history.messages[responseMessageId]?.parentId;
		const hasPendingSibling =
			parentId &&
			(history.messages[parentId]?.childrenIds ?? []).some(
				(id) => history.messages[id] && history.messages[id].done !== true
			);

		if (!hasPendingSibling && messageQueue.length > 0) {
			const next = messageQueue[0];
			messageQueue = messageQueue.slice(1);

			files = next.files;
			await tick();
			await submitPrompt(next.prompt);
		}
	};

	const chatActionHandler = async (chatId, actionId, modelId, responseMessageId, event = null) => {
		const messages = createMessagesList(history, responseMessageId);
		const responseModelIndex = history.messages[responseMessageId]?.modelIdx ?? 0;
		const responseModelResolution = resolveMessageModel(history.messages[responseMessageId]);
		if (
			isBlockingModelResolution(responseModelResolution) ||
			responseModelResolution.status !== 'resolved'
		) {
			await promptModelResolution(responseModelResolution, responseModelIndex);
			history.messages[responseMessageId].error = {
				type: 'model_resolution_error',
				content: responseModelResolution.status === 'ambiguous'
					? $i18n.t('Model connection is ambiguous. Please select the model again.')
					: $i18n.t('Model connection is unavailable. Please select the model again.')
			};
			await saveChatHandler(chatId, history);
			return;
		}
		applyResolvedMessageModel(history.messages[responseMessageId], responseModelResolution);
		modelId = responseModelResolution.value;

		const res = await chatAction(localStorage.token, actionId, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.sources ? { sources: m.sources } : {})
			})),
			...(event ? { event: event } : {}),
			model_item: getModelById(modelId),
			chat_id: chatId,
			session_id: $socket?.id,
			id: responseMessageId
		}).catch(async (error) => {
			const resolutionDetail = getModelResolutionDetail(error);
			if (resolutionDetail) {
				await promptModelReselection({
					index: responseModelIndex,
					rawModelId: resolutionDetail.requestedModelId || `${modelId ?? ''}`.trim(),
					ambiguous: resolutionDetail.code === MODEL_CONNECTION_AMBIGUOUS_CODE
				});
				messages.at(-1).error = {
					type: 'model_resolution_error',
					content: resolutionDetail.message || formatError(error),
					detail: resolutionDetail
				};
				return null;
			}

			toast.error(formatError(error));
			messages.at(-1).error = { content: formatError(error) };
			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				history.messages[message.id] = {
					...history.messages[message.id],
					...(history.messages[message.id].content !== message.content
						? { originalContent: history.messages[message.id].content }
						: {}),
					...message
				};
			}
		}

		if ($chatId == chatId) {
			if (!$temporaryChatEnabled) {
				await saveChatHandler(chatId, history, {
					messages
				});
			}
		}
	};

	const getChatEventEmitter = async (modelId: string, chatId: string = '') => {
		return setInterval(() => {
			$socket?.emit('usage', {
				action: 'chat',
				model: modelId,
				chat_id: chatId
			});
		}, 1000);
	};

	const resolveBranchPointMessageId = (sourceMessageId: string): string | null => {
		const sourceMessage = history.messages?.[sourceMessageId];
		if (!sourceMessage) {
			return null;
		}

		if (sourceMessage.role === 'assistant') {
			return sourceMessageId;
		}

		const currentPath = createMessagesList(history, history.currentId);
		const sourceIndex = currentPath.findIndex((message) => message.id === sourceMessageId);
		const nextMessage = sourceIndex >= 0 ? currentPath[sourceIndex + 1] : null;

		if (
			nextMessage?.role === 'assistant' &&
			nextMessage?.parentId === sourceMessageId &&
			nextMessage?.done === true
		) {
			return nextMessage.id;
		}

		return sourceMessageId;
	};

	const branchMessageToCurrentChat = async (sourceMessageId: string) => {
		if (
			!sourceMessageId ||
			branchingMessageId !== null ||
			!$chatId ||
			$chatId === 'local' ||
			$temporaryChatEnabled
		) {
			return;
		}

		const branchPointMessageId = resolveBranchPointMessageId(sourceMessageId);
		if (!branchPointMessageId) {
			toast.error($i18n.t('Failed to create branch'));
			return;
		}

		branchingMessageId = sourceMessageId;

		try {
			const branchedChat = await branchChatById(
				localStorage.token,
				$chatId,
				branchPointMessageId
			);

			if (!branchedChat?.id) {
				throw new Error($i18n.t('Failed to create branch'));
			}

			const targetUrl = `/c/${branchedChat.id}`;
			chatTitle.set(branchedChat.title);
			chatId.set(branchedChat.id);
			chatListRefreshTarget.set({
				id: branchedChat.id,
				title: branchedChat.title,
				updated_at: branchedChat.updated_at,
				created_at: branchedChat.created_at,
				assistant_id: branchedChat.assistant_id ?? null
			});
			chatListRefreshRevision.update((value) => value + 1);
			await goto(targetUrl);
			toast.success($i18n.t('Switched to new branch'));
		} catch (error) {
			toast.error(
				typeof error === 'string' && error
					? error
					: error instanceof Error && error.message
						? error.message
						: $i18n.t('Failed to create branch')
			);
		} finally {
			branchingMessageId = null;
		}
	};

	const createMessagePair = async (userPrompt) => {
		prompt = '';
		if (selectedModels.length === 0) {
			toast.error($i18n.t('Model not selected'));
		} else {
			const modelId = selectedModels[0];
			const model = getModelById(modelId);

			const messages = createMessagesList(history, history.currentId);
			const parentMessage = messages.length !== 0 ? messages.at(-1) : null;

			const userMessageId = uuidv4();
			const responseMessageId = uuidv4();

			const userMessage = {
				id: userMessageId,
				parentId: parentMessage ? parentMessage.id : null,
				childrenIds: [responseMessageId],
				role: 'user',
				content: userPrompt ? userPrompt : `[PROMPT] ${userMessageId}`,
				timestamp: Math.floor(Date.now() / 1000)
			};

			const responseMessage = {
				id: responseMessageId,
				parentId: userMessageId,
				childrenIds: [],
				role: 'assistant',
				content: `[RESPONSE] ${responseMessageId}`,
				done: true,
				model: modelId,
				modelName: getModelChatDisplayName(model) || model?.id || modelId,
				...(getModelRef(model) ? { model_ref: getModelRef(model) } : {}),
				modelIdx: 0,
				timestamp: Math.floor(Date.now() / 1000)
			};

			if (parentMessage) {
				parentMessage.childrenIds.push(userMessageId);
				history.messages[parentMessage.id] = parentMessage;
			}
			history.messages[userMessageId] = userMessage;
			history.messages[responseMessageId] = responseMessage;

			history.currentId = responseMessageId;

			await tick();

			if (autoScroll) {
				scrollToBottom();
			}

			if (messages.length === 0) {
				await initChatHandler(history);
			} else {
				await saveChatHandler($chatId, history);
			}
		}
	};

	const addMessages = async ({ modelId, parentId, messages }) => {
		const model = getModelById(modelId);

		let parentMessage = history.messages[parentId];
		let currentParentId = parentMessage ? parentMessage.id : null;
		for (const message of messages) {
			let messageId = uuidv4();

			if (message.role === 'user') {
				const userMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = userMessage;
				parentMessage = userMessage;
				currentParentId = messageId;
			} else {
				const responseMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					done: true,
					model: model ? getModelRequestId(model) : modelId,
					modelName: getModelChatDisplayName(model) || model?.id || modelId,
					...(getModelRef(model) ? { model_ref: getModelRef(model) } : {}),
					modelIdx: 0,
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = responseMessage;
				parentMessage = responseMessage;
				currentParentId = messageId;
			}
		}

		history.currentId = currentParentId;
		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		if (messages.length === 0) {
			await initChatHandler(history);
		} else {
			await saveChatHandler($chatId, history);
		}
	};

	const consumeGeminiImageDelta = (
		messageId: string,
		imageDelta: any
	): { type: 'image'; url: string } | null => {
		if (!imageDelta || typeof imageDelta !== 'object') {
			return null;
		}

		const imageId = typeof imageDelta.id === 'string' && imageDelta.id ? imageDelta.id : null;
		if (!imageId) {
			return null;
		}

		let messageImages = pendingGeminiImages.get(messageId);
		if (!messageImages) {
			messageImages = new Map<string, PendingGeminiImage>();
			pendingGeminiImages.set(messageId, messageImages);
		}

		const mimeType =
			typeof imageDelta.mime_type === 'string' && imageDelta.mime_type
				? imageDelta.mime_type
				: 'image/png';
		const data = typeof imageDelta.data === 'string' ? imageDelta.data : '';
		const pending = messageImages.get(imageId) ?? { mimeType, parts: [] };

		pending.mimeType = mimeType || pending.mimeType;
		if (data) {
			pending.parts.push(data);
		}
		messageImages.set(imageId, pending);

		if (imageDelta.final !== true) {
			return null;
		}

		const imageFile = {
			type: 'image' as const,
			url: buildImageDataUrl(pending.mimeType, pending.parts.join(''))
		};
		messageImages.delete(imageId);
		if (messageImages.size === 0) {
			pendingGeminiImages.delete(messageId);
		}
		return imageFile;
	};

	const clearPendingGeminiImages = (messageId: string, warnOnIncomplete = false) => {
		const messageImages = pendingGeminiImages.get(messageId);
		if (!messageImages) {
			return;
		}
		if (warnOnIncomplete && messageImages.size > 0) {
			console.warn('Discarding incomplete streamed Gemini image(s)', {
				messageId,
				count: messageImages.size
			});
		}
		pendingGeminiImages.delete(messageId);
	};

	const chatCompletionEventHandler = async (data, message, chatId) => {
		const { id, done, choices, content, sources, error, usage, files } = data;

		if (files) {
			message.files = mergeMessageFiles(message.files, files);
			if (shouldAutoScrollOnStreaming()) {
				scrollToBottom();
			}
		}

		if (error) {
			clearPendingGeminiImages(message.id, true);
			if (typeof error === 'object' && error !== null && 'content' in error) {
				message.error = error;
			} else {
				await handleOpenAIError(error, message);
			}
		}

		if (sources) {
			message.sources = sources;
		}

		if (choices) {
			if (choices[0]?.message?.content) {
				await releaseResponseAnimationController(message.id, { flush: false });
				appendAnimatedMessageContent(message, choices[0]?.message?.content);
			} else {
				const delta = choices[0]?.delta ?? {};
				const imageFile = consumeGeminiImageDelta(message.id, delta?.image);
				if (imageFile) {
					message.files = mergeMessageFiles(message.files, [imageFile]);
					if (shouldAutoScrollOnStreaming()) {
						scrollToBottom();
					}
				}

				const value = delta?.content ?? '';
				if (!value) {
					// Partial image chunks are buffered until the final fragment arrives.
				} else {
					getResponseAnimationController(message).enqueue(value);
				}
			}
		}

		if (content) {
			// REALTIME_CHAT_SAVE is disabled
			await releaseResponseAnimationController(message.id, { flush: false });
			message.content = content;

			if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
				navigator.vibrate(5);
			}

			emitLatestMessageSentence(message);
		}

		if (usage) {
			message.usage = usage;
		}

		history.messages[message.id] = message;

		if (done) {
			await releaseResponseAnimationController(message.id);
			clearPendingGeminiImages(message.id, true);
			message.done = true;
			message.completedAt = Date.now() / 1000;

			if ($settings.responseAutoCopy) {
				copyToClipboard(message.content);
			}

			if ($settings.responseAutoPlayback && !$showCallOverlay) {
				await tick();
				document.getElementById(`speak-button-${message.id}`)?.click();
			}

			// J-3-03: Skip final TTS sentence parsing for non-voice users
			if ($showCallOverlay || $settings?.responseAutoPlayback) {
				// Emit chat event for TTS
				let lastMessageContentPart =
					getMessageContentParts(
						message.content,
						$config?.audio?.tts?.split_on ?? 'punctuation'
					)?.at(-1) ?? '';
				if (lastMessageContentPart) {
					eventTarget.dispatchEvent(
						new CustomEvent('chat', {
							detail: { id: message.id, content: lastMessageContentPart }
						})
					);
				}
			}
			eventTarget.dispatchEvent(
				new CustomEvent('chat:finish', {
					detail: {
						id: message.id,
						content: message.content
					}
				})
			);
			activeChatIds.update((ids) => {
				ids.delete($chatId);
				return new Set(ids);
			});

			history.messages[message.id] = message;
			await chatCompletedHandler(
				chatId,
				message.model,
				message.id,
				createMessagesList(history, message.id)
			);
		}

		if (shouldAutoScrollOnStreaming()) {
			scrollToBottom();
		}
	};

	//////////////////////////
	// Chat functions
	//////////////////////////

	const submitPrompt = async (userPrompt, { _raw = false } = {}) => {
		const messages = createMessagesList(history, history.currentId);
		const blockingSelection = findBlockingSelectedModelResolution();
		const _selectedModels = selectedModels.map((modelId, index) => {
			const resolution = resolveSelectedModel(modelId, index);
			return resolution.status === 'resolved' ? resolution.value : '';
		});

		const failedFiles = files.filter((file) => isFailedUploadFile(file));
		const validFiles = files.filter((file) => !isFailedUploadFile(file));

		if (userPrompt === '' && validFiles.length === 0) {
			if (failedFiles.length > 0) {
				toast.warning(
					$i18n.t(
						'All selected files failed to upload or index. Remove them or upload them again before sending.'
					)
				);
				return;
			}
			toast.error($i18n.t('Please enter a prompt'));
			return;
		}
		if (blockingSelection) {
			await promptModelResolution(blockingSelection.resolution, blockingSelection.index);
			return;
		}
		if (JSON.stringify(selectedModels) !== JSON.stringify(_selectedModels)) {
			selectedModels = _selectedModels;
		}
		if (selectedModels.includes('')) {
			toast.error($i18n.t('Model not selected'));
			return;
		}

		if (messages.length != 0 && messages.at(-1).error && !messages.at(-1).content) {
			// Error in response
			toast.error($i18n.t(`Oops! There was an error in the previous response.`));
			return;
		}
		if (
			validFiles.length > 0 &&
			validFiles.filter((file) => file.status === 'uploading').length > 0
		) {
			toast.error(
				$i18n.t(`Oops! There are files still uploading. Please wait for the upload to complete.`)
			);
			return;
		}
		if (
			($config?.file?.max_count ?? null) !== null &&
			validFiles.length + chatFiles.length > $config?.file?.max_count
		) {
			toast.error(
				$i18n.t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: $config?.file?.max_count
				})
			);
			return;
		}

		const hasPendingTask = Array.isArray(taskIds) && taskIds.length > 0;
		const hasRunningResponse = messages.length !== 0 && messages.at(-1).done != true;
		if (hasPendingTask || hasRunningResponse) {
			if ($settings?.enableMessageQueue ?? true) {
				const queuedFiles = validFiles.map((file) => normalizeInputFileForMessage(file));
				if (failedFiles.length > 0) {
					toast.warning(buildIgnoredFailedFilesMessage(failedFiles, $i18n.t.bind($i18n)));
				}
				messageQueue = [...messageQueue, { id: uuidv4(), prompt: userPrompt, files: queuedFiles }];
				prompt = '';
				files = structuredClone(failedFiles);
				return;
			}

			await stopResponse();
			await tick();
		}

		prompt = '';

		// Reset chat input textarea
		if (!($settings?.richTextInput ?? true)) {
			const chatInputElement = document.getElementById('chat-input');

			if (chatInputElement) {
				await tick();
				chatInputElement.style.height = '';
			}
		}

		if (failedFiles.length > 0) {
			toast.warning(buildIgnoredFailedFilesMessage(failedFiles, $i18n.t.bind($i18n)));
		}

		const _files = validFiles.map((file) => normalizeInputFileForMessage(file));
		chatFiles.push(..._files.filter((item) => ['doc', 'file', 'collection'].includes(item.type)));
		chatFiles = chatFiles.filter(
			// Remove duplicates
			(item, index, array) =>
				array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
		);

		files = structuredClone(failedFiles);
		prompt = '';

		// Create user message
		let userMessageId = uuidv4();
		let userMessage = {
			id: userMessageId,
			parentId: messages.length !== 0 ? messages.at(-1).id : null,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			files: _files.length > 0 ? _files : undefined,
			timestamp: Math.floor(Date.now() / 1000), // Unix epoch
			models: selectedModels
		};

		// Add message to history and Set currentId to messageId
		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		// Append messageId to childrenIds of parent message
		if (messages.length !== 0) {
			history.messages[messages.at(-1).id].childrenIds.push(userMessageId);
		}

		// focus on chat input
		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();

		saveSessionSelectedModels();

		await sendPrompt(history, userPrompt, userMessageId, { newChat: true });
	};

	const sendPrompt = async (
		_history,
		prompt: string,
		parentId: string,
		{ modelId = null, modelIdx = null, newChat = false } = {}
	) => {
		if (autoScroll) {
			scrollToBottom();
		}

		let _chatId = $chatId;
		_history = structuredClone(_history);

		const responseMessageIds: Record<PropertyKey, string> = {};
		// If modelId is provided, use it, else use selected model
		let selectedModelIds = [];
		if (modelId) {
			const requestedModelId = `${modelId ?? ''}`.trim();
			const sourceMessage =
				typeof modelIdx === 'number'
					? Object.values(history.messages).find(
							(message: any) => message?.role === 'assistant' && message?.model === requestedModelId
						)
					: null;
			const resolution = resolveChatModelSelection($models, {
				value: requestedModelId,
				model_ref: (sourceMessage as any)?.model_ref,
				display_name: (sourceMessage as any)?.modelName
			});
			if (isBlockingModelResolution(resolution) || resolution.status !== 'resolved') {
				await promptModelResolution(resolution, typeof modelIdx === 'number' ? modelIdx : 0);
				return;
			}
			selectedModelIds = [resolution.value];
		} else {
			const rawSelectedModelIds =
				atSelectedModel !== undefined ? [getModelSelectionId(atSelectedModel)] : selectedModels;
			for (const [index, rawModelId] of rawSelectedModelIds.entries()) {
				const resolution =
					atSelectedModel !== undefined
						? resolveChatModelSelection($models, { value: rawModelId })
						: resolveSelectedModel(rawModelId, index);
				if (isBlockingModelResolution(resolution) || resolution.status !== 'resolved') {
					await promptModelResolution(resolution, index);
					return;
				}
				selectedModelIds.push(resolution.value);
			}
			if (
				atSelectedModel === undefined &&
				JSON.stringify(selectedModels) !== JSON.stringify(selectedModelIds)
			) {
				selectedModels = selectedModelIds;
			}
		}

		// Create response messages for each selected model
		for (const [_modelIdx, modelId] of selectedModelIds.entries()) {
			const model = getModelById(modelId);

			if (model) {
				let responseMessageId = uuidv4();
				let responseMessage = {
					parentId: parentId,
					id: responseMessageId,
					childrenIds: [],
					role: 'assistant',
					content: '',
					model: getModelRequestId(model),
					modelName: getModelChatDisplayName(model) || model.id,
					...(getModelRef(model) ? { model_ref: getModelRef(model) } : {}),
					modelIdx: modelIdx ? modelIdx : _modelIdx,
					userContext: null,
					timestamp: Math.floor(Date.now() / 1000), // Unix epoch
					...(_pendingInstruction ? { instruction: _pendingInstruction } : {})
				};

				// Add message to history and Set currentId to messageId
				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;

				// Append messageId to childrenIds of parent message
				if (parentId !== null && history.messages[parentId]) {
					// Add null check before accessing childrenIds
					history.messages[parentId].childrenIds = [
						...history.messages[parentId].childrenIds,
						responseMessageId
					];
				}

				responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`] = responseMessageId;
			}
		}
		history = history;

		// Create new chat if newChat is true and first user message
		if (newChat && _history.messages[_history.currentId].parentId === null) {
			_chatId = await initChatHandler(_history);
		}

		await tick();

		_history = structuredClone(history);
		// Save chat after all messages have been created
		await saveChatHandler(_chatId, _history);

		await Promise.all(
			selectedModelIds.map(async (modelId, _modelIdx) => {
				console.log('modelId', modelId);
				const model = getModelById(modelId);

				if (model) {
					const messages = createMessagesList(_history, parentId);
					// If there are image files, check if model is vision capable
					const hasImages = messages.some((message) =>
						message.files?.some((file) => file.type === 'image')
					);

					if (hasImages && !(model.info?.meta?.capabilities?.vision ?? true)) {
						toast.error(
							$i18n.t('Model {{modelName}} is not vision capable', {
								modelName: getModelChatDisplayName(model) || model.id
							})
						);
					}

					let responseMessageId =
						responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`];
					let responseMessage = _history.messages[responseMessageId];

					let userContext = null;
					if ($settings?.memory ?? false) {
						if (userContext === null) {
							const res = await queryMemory(localStorage.token, prompt).catch((error) => {
								toast.error(`${error}`);
								return null;
							});
							if (res) {
								const memoryDocuments = res?.documents?.[0] ?? [];
								const memoryMetadatas = res?.metadatas?.[0] ?? [];

								if (memoryDocuments.length > 0) {
									userContext = memoryDocuments.reduce((acc, doc, index) => {
										const createdAtTimestamp = memoryMetadatas[index]?.created_at;
										const createdAtDate = createdAtTimestamp
											? new Date(createdAtTimestamp * 1000).toISOString().split('T')[0]
											: null;
										return `${acc}${index + 1}.${createdAtDate ? ` [${createdAtDate}].` : ''} ${doc}\n`;
									}, '');
								}

								console.log(userContext);
							}
						}
					}
					responseMessage.userContext = userContext;

					const chatEventEmitter = await getChatEventEmitter(getModelRequestId(model), _chatId);

					resetAutoScrollLock();
					scrollToBottom();
					await sendPromptSocket(_history, model, responseMessageId, _chatId);

					if (chatEventEmitter) clearInterval(chatEventEmitter);
				} else {
					toast.error($i18n.t(`Model {{modelId}} not found`, { modelId }));
				}
			})
		);

		currentChatPage.set(1);
		chats.set(await getChatList(localStorage.token, $currentChatPage));
	};

	const sendPromptSocket = async (_history, model, responseMessageId, _chatId) => {
		const responseMessage = _history.messages[responseMessageId];
		const userMessage = _history.messages[responseMessage.parentId];

		let files = structuredClone(chatFiles);
		files.push(
			...(userMessage?.files ?? []).filter((item) =>
				['doc', 'file', 'collection'].includes(item.type)
			),
			...(responseMessage?.files ?? []).filter((item) => ['web_search_results'].includes(item.type))
		);
		// Remove duplicates
		files = files.filter(
			(item, index, array) =>
				array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
		);

		resetAutoScrollLock();
		scrollToBottom();
		eventTarget.dispatchEvent(
			new CustomEvent('chat:start', {
				detail: {
					id: responseMessageId
				}
			})
		);
		activeChatIds.update((ids) => {
			ids.add(_chatId);
			return new Set(ids);
		});
		await tick();

		const stream =
			model?.info?.params?.stream_response ??
			$settings?.params?.stream_response ??
			params?.stream_response ??
			true;

		let messages = [
			params?.system || $settings.system || (responseMessage?.userContext ?? null)
				? {
						role: 'system',
						content: `${promptTemplate(
							params?.system ?? $settings?.system ?? '',
							$user?.name,
							$settings?.userLocation
								? await getAndUpdateUserLocation(localStorage.token).catch((err) => {
										console.error(err);
										return undefined;
									})
								: undefined
						)}${
							(responseMessage?.userContext ?? null)
								? `\n\nUser Context:\n${responseMessage?.userContext ?? ''}`
								: ''
						}`
					}
				: undefined,
			...createMessagesList(_history, responseMessageId).map((message) => ({
				...message,
				content: processDetails(message.content)
			}))
		].filter((message) => message);

		// 自定义上下文条数：只保留最近 N 条非系统消息，系统提示词始终保留
		const maxHistoryMessages =
			params?.max_history_messages ??
			$settings?.params?.max_history_messages ??
			null;
		if (typeof maxHistoryMessages === 'number' && maxHistoryMessages > 0) {
			const hasSystem = messages[0]?.role === 'system';
			const systemMsg = hasSystem ? messages[0] : null;
			const historyOnly = (hasSystem ? messages.slice(1) : messages).filter(
				(message, idx, arr) =>
					!(
						idx === arr.length - 1 &&
						message?.id === responseMessageId &&
						message?.role === 'assistant' &&
						!`${message?.content ?? ''}`.trim()
					)
			);
			const truncated = historyOnly.slice(-maxHistoryMessages);
			const fallbackUserMessage = historyOnly.findLast((message) => message?.role === 'user');
			const limitedHistory =
				truncated.some((message) => message?.role === 'user')
					? truncated
					: fallbackUserMessage
						? [
								...(truncated.some((message) => message?.id === fallbackUserMessage.id)
									? []
									: [fallbackUserMessage]),
								...truncated
							]
						: truncated;

			messages = systemMsg ? [systemMsg, ...limitedHistory] : limitedHistory;
		}

		const requestSkillIds = collectRequestSkillIds(messages);
		const requestedWebSearchMode = canUseChatWebSearch()
			? normalizeWebSearchMode(webSearchMode, 'off')
			: 'off';
		const imageGenerationActive = canUseChatImageGeneration()
			? isImageGenerationActiveForRequest()
			: false;

		messages = messages
			.map((message, idx, arr) => {
				let textContent = stripSkillTagsFromText(message?.merged?.content ?? message.content);

				// Inject regeneration instruction into the last user message for API payload only
				if (
					_pendingInstruction &&
					message.role === 'user' &&
					idx === arr.findLastIndex((m) => m.role === 'user')
				) {
					textContent = `${textContent}\n\n${_pendingInstruction}`;
				}

				const imageFiles = message.files?.filter((file) => file.type === 'image') ?? [];
				const includeImagesInContent =
					imageFiles.length > 0 && (message.role === 'user' || imageGenerationActive);

				return {
					role: message.role,
					...(includeImagesInContent
						? {
								content: [
									...(textContent
										? [
												{
													type: 'text',
													text: textContent
												}
											]
										: []),
									...imageFiles.map((file) => ({
										type: 'image_url',
										image_url: {
											url: buildModelImageRequestUrl(file)
										}
									}))
								]
							}
						: {
								content: textContent
							})
				};
			})
			.filter((message) => {
				if (message?.role === 'user') {
					return true;
				}
				if (Array.isArray(message?.content)) {
					return message.content.length > 0;
				}
				return message?.content?.trim();
			});

		const res = await generateOpenAIChatCompletion(
			localStorage.token,
			{
				stream: stream,
				model: getModelRequestId(model),
				messages: messages,
				params: {
					...$settings?.params,
					...params,
					...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
					...(maxThinkingTokens != null && maxThinkingTokens > 0
						? { thinking: { type: 'enabled', budget_tokens: maxThinkingTokens } }
						: {}),

					format: $settings.requestFormat ?? undefined,
					keep_alive: $settings.keepAlive ?? undefined,
					stop:
						(params?.stop ?? $settings?.params?.stop ?? undefined)
							? (params?.stop.split(',').map((token) => token.trim()) ?? $settings.params.stop).map(
									(str) => decodeURIComponent(JSON.parse('"' + str.replace(/\"/g, '\\"') + '"'))
								)
							: undefined
				},

				files: (files?.length ?? 0) > 0 ? files : undefined,
				tool_ids: selectedToolIds.length > 0 ? selectedToolIds : undefined,
				skill_ids: requestSkillIds.length > 0 ? requestSkillIds : undefined,
				skill_selection_touched: skillSelectionTouched ? true : undefined,
				tool_servers: $toolServers,

				features: {
					memory: $settings?.memory ?? false,
					image_generation: imageGenerationActive,
					image_generation_options: imageGenerationActive
						? getImageGenerationOptionsPayload()
						: undefined,
					code_interpreter:
						$config?.features?.enable_code_interpreter &&
						($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
							? codeInterpreterEnabled
							: false,
					web_search: requestedWebSearchMode !== 'off',
					web_search_mode: requestedWebSearchMode !== 'off' ? requestedWebSearchMode : undefined
				},
				variables: {
					...getPromptVariables(
						$user?.name,
						$settings?.userLocation
							? await getAndUpdateUserLocation(localStorage.token).catch((err) => {
									console.error(err);
									return undefined;
								})
							: undefined
					)
				},
				model_item: model,

				session_id: $socket?.id,
				chat_id: $chatId,
				id: responseMessageId,

				...(!$temporaryChatEnabled &&
				!imageGenerationActive &&
				(messages.length == 1 ||
					(messages.length == 2 &&
						messages.at(0)?.role === 'system' &&
						messages.at(1)?.role === 'user')) &&
				(selectedModels[0] === getModelRequestId(model) || atSelectedModel !== undefined)
					? {
							background_tasks: {
								title_generation: $settings?.title?.auto ?? true,
								tags_generation: $settings?.autoTags ?? true,
								follow_up_generation: $settings?.autoFollowUps ?? true
							}
						}
					: !$temporaryChatEnabled &&
					  !imageGenerationActive &&
					  ($settings?.autoFollowUps ?? true)
						? {
								background_tasks: {
									follow_up_generation: true
								}
							}
						: {}),

				...(stream && shouldIncludeUsageStreamOption(model)
					? {
							stream_options: {
								include_usage: true
							}
						}
					: {})
			},
			`${WEBUI_BASE_URL}/api`
		).catch(async (error) => {
			const resolutionDetail = getModelResolutionDetail(error);
			if (resolutionDetail) {
				await promptModelReselection({
					index: responseMessage.modelIdx ?? 0,
					rawModelId: resolutionDetail.requestedModelId || getModelRequestId(model),
					ambiguous: resolutionDetail.code === MODEL_CONNECTION_AMBIGUOUS_CODE
				});
				responseMessage.error = {
					type: 'model_resolution_error',
					content: resolutionDetail.message || `${error}`,
					detail: resolutionDetail
				};
				responseMessage.done = true;
				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;
				return null;
			}

			await handleOpenAIError(error, responseMessage);
			return null;
		});

		if (res) {
			if (res.error) {
				await handleOpenAIError(res.error, responseMessage);
			} else {
				if (taskIds) {
					taskIds.push(res.task_id);
				} else {
					taskIds = [res.task_id];
				}
			}
		}

		await tick();
		scrollToBottom();
	};

	const extractOpenAIErrorMessage = (innerError) => {
		if (!innerError) {
			return '';
		}

		if (typeof innerError === 'string') {
			return innerError;
		}

		if (typeof innerError === 'object') {
			if ('detail' in innerError && typeof innerError.detail === 'string') {
				return innerError.detail;
			}
			if (
				'detail' in innerError &&
				innerError.detail &&
				typeof innerError.detail === 'object' &&
				'message' in innerError.detail &&
				typeof innerError.detail.message === 'string'
			) {
				return innerError.detail.message;
			}

			if ('error' in innerError) {
				if (
					typeof innerError.error === 'object' &&
					innerError.error !== null &&
					'message' in innerError.error &&
					typeof innerError.error.message === 'string'
				) {
					return innerError.error.message;
				}

				if (typeof innerError.error === 'string') {
					return innerError.error;
				}
			}

			if ('message' in innerError && typeof innerError.message === 'string') {
				return innerError.message;
			}
		}

		try {
			return JSON.stringify(innerError);
		} catch {
			return String(innerError);
		}
	};

	const REQUEST_INCOMPATIBLE_PATTERNS = [
		'unknown parameter',
		'unsupported parameter',
		'unsupported value',
		'unsupported type',
		'invalid value',
		'invalid_request_error',
		'schema',
		'not supported',
		'not support',
		'unexpected field',
		'extra fields',
		'tool_choice',
		'stream_options',
		'response_format',
		'input_image',
		'input_file',
		'messages[',
		'tools['
	];

	const extractOpenAIErrorStatus = (errorMessage: string): number | null => {
		const message = `${errorMessage ?? ''}`;
		const patterns = [
			/Responses API upstream error \((\d{3})\)/i,
			/\bHTTP\s*(\d{3})\b/i,
			/\bstatus\s*[:=]\s*(\d{3})\b/i
		];

		for (const pattern of patterns) {
			const match = message.match(pattern);
			if (!match) {
				continue;
			}

			const status = Number.parseInt(match[1], 10);
			if (Number.isFinite(status) && status >= 400) {
				return status;
			}
		}

		return null;
	};

	const inferOpenAIErrorFamily = (status: number | null, errorMessage: string): string => {
		const message = `${errorMessage ?? ''}`.toLowerCase();

		if (status === 401 || status === 403) {
			return 'auth_error';
		}
		if (status === 404) {
			return 'model_not_found';
		}
		if (status === 429) {
			return 'rate_limited';
		}
		if (status === 524) {
			return 'cloudflare_timeout';
		}
		if (status === 408 || status === 504) {
			return 'timeout';
		}
		if (status === 400) {
			return 'request_incompatible';
		}
		if (status !== null && status >= 500) {
			return 'upstream_service_error';
		}
		if (REQUEST_INCOMPATIBLE_PATTERNS.some((pattern) => message.includes(pattern))) {
			return 'request_incompatible';
		}
		return 'upstream_service_error';
	};

	const extractOpenAIErrorEvidence = (errorMessage: string) => {
		const message = `${errorMessage ?? ''}`;
		const lowerMessage = message.toLowerCase();

		const interfaceHint =
			lowerMessage.includes('responses api') || lowerMessage.includes('/responses')
				? 'Responses API'
				: lowerMessage.includes('chat completions') || lowerMessage.includes('/chat/completions')
					? 'Chat Completions API'
					: lowerMessage.includes('embeddings') || lowerMessage.includes('/embeddings')
						? 'Embeddings API'
						: '';

		const host = message.match(/\bfrom\s+([a-z0-9.-]+\.[a-z]{2,})(?::\d+)?\b/i)?.[1] ?? '';
		const parameter =
			message.match(/Unknown parameter:\s*['"]([^'"]+)['"]/i)?.[1] ??
			message.match(/['"]param['"]\s*:\s*['"]([^'"]+)['"]/i)?.[1] ??
			message.match(/\bparameter\s*[:=]?\s*['"]([^'"]+)['"]/i)?.[1] ??
			'';
		const code =
			message.match(/['"]code['"]\s*:\s*['"]([^'"]+)['"]/i)?.[1] ??
			message.match(/\berror code\s*[:=]?\s*([a-z0-9_.-]+)\b/i)?.[1] ??
			'';

		return {
			...(interfaceHint ? { interface: interfaceHint } : {}),
			...(host ? { host } : {}),
			...(parameter ? { parameter } : {}),
			...(code ? { code } : {})
		};
	};

	const buildOpenAIErrorReasons = (family: string, status: number | null): string[] => {
		if (family === 'request_incompatible') {
			return [
				'api_request_interface_mismatch',
				'api_request_model_mismatch',
				'api_request_feature_not_supported',
				'api_proxy_schema_mismatch'
			];
		}
		if (family === 'auth_error') {
			return ['api_auth_error'];
		}
		if (family === 'model_not_found') {
			return ['api_model_not_found'];
		}
		if (family === 'rate_limited') {
			return ['api_rate_limit', 'api_quota_exceeded'];
		}
		if (family === 'cloudflare_timeout') {
			return ['api_cloudflare_origin_timeout', 'api_request_timeout', 'proxy_error'];
		}
		if (family === 'timeout') {
			return ['api_request_timeout'];
		}
		if (status === 500) {
			return ['api_server_error', 'proxy_error'];
		}
		return ['api_upstream_error', 'proxy_error'];
	};

	const buildOpenAIErrorSuggestion = (family: string, status: number | null): string => {
		if (family === 'request_incompatible') {
			return 'check_request_compatibility';
		}
		if (family === 'auth_error') {
			return 'check_api_key';
		}
		if (family === 'rate_limited' || family === 'timeout' || family === 'cloudflare_timeout') {
			return 'wait_retry';
		}
		if (family === 'upstream_service_error' && status !== null && status >= 500) {
			return status === 500 ? 'retry_or_switch' : 'wait_retry';
		}
		return 'retry_or_switch';
	};

	const buildOpenAIErrorTitle = (family: string, status: number | null): string => {
		const statusValue = status ?? 'unknown';
		switch (family) {
			case 'request_incompatible':
				return status
					? $i18n.t('error.title.request_incompatible', { status: statusValue })
					: $i18n.t('error.title.request_incompatible_no_status');
			case 'auth_error':
				return status
					? $i18n.t('error.title.auth_error', { status: statusValue })
					: $i18n.t('error.title.auth_error_no_status');
			case 'model_not_found':
				return status
					? $i18n.t('error.title.model_not_found', { status: statusValue })
					: $i18n.t('error.title.model_not_found_no_status');
			case 'rate_limited':
				return status
					? $i18n.t('error.title.rate_limited', { status: statusValue })
					: $i18n.t('error.title.rate_limited_no_status');
			case 'timeout':
				return status
					? $i18n.t('error.title.timeout', { status: statusValue })
					: $i18n.t('error.title.timeout_no_status');
			case 'cloudflare_timeout':
				return status
					? $i18n.t('error.title.cloudflare_timeout', { status: statusValue })
					: $i18n.t('error.title.cloudflare_timeout_no_status');
			default:
				return status
					? $i18n.t('error.title.upstream_service_error', { status: statusValue })
					: $i18n.t('error.title.upstream_service_error_no_status');
		}
	};

	const buildOpenAIErrorBody = (
		family: string,
		evidence: { interface?: string; host?: string; parameter?: string; code?: string }
	): string => {
		const lines = [$i18n.t(`error.body.${family}`)];
		if (evidence.interface) {
			lines.push($i18n.t('error.evidence.interface', { value: evidence.interface }));
		}
		if (evidence.parameter) {
			lines.push($i18n.t('error.evidence.parameter', { value: evidence.parameter }));
		} else if (evidence.code) {
			lines.push($i18n.t('error.evidence.code', { value: evidence.code }));
		}
		if (evidence.host && lines.length < 3) {
			lines.push($i18n.t('error.evidence.host', { value: evidence.host }));
		}
		return lines.filter(Boolean).join('\n');
	};

	const buildLocalizedOpenAIError = (errorMessage: string) => {
		const message = `${errorMessage ?? ''}`.trim();
		if (!message) {
			return null;
		}

		const status = extractOpenAIErrorStatus(message);
		const family = inferOpenAIErrorFamily(status, message);
		const evidence = extractOpenAIErrorEvidence(message);
		const title = buildOpenAIErrorTitle(family, status);
		const body = buildOpenAIErrorBody(family, evidence);

		return {
			type: 'api_error',
			family,
			status,
			title,
			body,
			content: title,
			reasons: buildOpenAIErrorReasons(family, status),
			suggestion: buildOpenAIErrorSuggestion(family, status),
			raw_message: message,
			evidence
		};
	};

	const handleOpenAIError = async (error, responseMessage) => {
		let innerError;

		if (error) {
			innerError = error;
		}

		console.error(innerError);

		if (
			innerError &&
			typeof innerError === 'object' &&
			innerError.type === 'api_error' &&
			('title' in innerError || 'content' in innerError)
		) {
			toast.error(
				`${innerError.title ?? innerError.content ?? $i18n.t('error.title.upstream_service_error_no_status')}`
			);
			responseMessage.error = innerError;
			responseMessage.done = true;

			if (responseMessage.statusHistory) {
				responseMessage.statusHistory = responseMessage.statusHistory.filter(
					(status) => status.action !== 'knowledge_search'
				);
			}

			history.messages[responseMessage.id] = responseMessage;
			return;
		}

		const errorMessage = extractOpenAIErrorMessage(innerError);
		const localizedError = buildLocalizedOpenAIError(errorMessage);

		if (localizedError) {
			toast.error(localizedError.content.split('\n')[0]);
			responseMessage.error = localizedError;
		} else {
			if (errorMessage) {
				toast.error(errorMessage);
			}

			responseMessage.error = {
				content: $i18n.t(`Uh-oh! There was an issue with the response.`) + '\n' + errorMessage
			};
		}

		responseMessage.done = true;

		if (responseMessage.statusHistory) {
			responseMessage.statusHistory = responseMessage.statusHistory.filter(
				(status) => status.action !== 'knowledge_search'
			);
		}

		history.messages[responseMessage.id] = responseMessage;
	};

	const stopResponse = async () => {
		if (taskIds) {
			for (const taskId of taskIds) {
				const res = await stopTask(localStorage.token, taskId).catch((error) => {
					toast.error(`${error}`);
					return null;
				});
			}

			taskIds = null;

			const responseMessage = history.messages[history.currentId];
			// Set all response messages to done
			for (const messageId of history.messages[responseMessage.parentId].childrenIds) {
				history.messages[messageId].done = true;
				history.messages[messageId].completedAt = Date.now() / 1000;
			}

			history.messages[history.currentId] = responseMessage;

			if (autoScroll) {
				scrollToBottom();
			}
		}
	};

	const submitMessage = async (parentId, prompt) => {
		let userPrompt = prompt;
		let userMessageId = uuidv4();

		let userMessage = {
			id: userMessageId,
			parentId: parentId,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			models: selectedModels
		};

		if (parentId !== null) {
			history.messages[parentId].childrenIds = [
				...history.messages[parentId].childrenIds,
				userMessageId
			];
		}

		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		await sendPrompt(history, userPrompt, userMessageId);
	};

	const regenerateResponse = async (
		message,
		options: { reasoningEffort?: string; webSearch?: boolean; instruction?: string } = {}
	) => {
		if (history.currentId) {
			let userMessage = history.messages[message.parentId];
			let userPrompt = userMessage.content;

			if (autoScroll) {
				scrollToBottom();
			}

			// Temporarily override params if options provided
			const origReasoningEffort = reasoningEffort;
			const origWebSearchMode = webSearchMode;
			if (options.reasoningEffort) reasoningEffort = options.reasoningEffort;
			if (options.webSearch) {
				webSearchMode =
					normalizeWebSearchMode(webSearchMode, 'off') !== 'off'
						? normalizeWebSearchMode(webSearchMode, 'off')
						: getPreferredDefaultWebSearchMode();
			}
			if (options.instruction) _pendingInstruction = options.instruction;

			try {
				if ((userMessage?.models ?? [...selectedModels]).length == 1) {
					await sendPrompt(history, userPrompt, userMessage.id);
				} else {
					await sendPrompt(history, userPrompt, userMessage.id, {
						modelId: message.model,
						modelIdx: message.modelIdx
					});
				}
			} finally {
				reasoningEffort = origReasoningEffort;
				webSearchMode = origWebSearchMode;
				_pendingInstruction = null;
			}
		}
	};

	const continueResponse = async () => {
		const _chatId = $chatId;

		if (history.currentId && history.messages[history.currentId].done == true) {
			const responseMessage = history.messages[history.currentId];
			responseMessage.done = false;
			await tick();

			const requestedModelId = `${responseMessage?.model ?? ''}`.trim();
			const resolution = resolveMessageModel(responseMessage);
			if (isBlockingModelResolution(resolution) || resolution.status !== 'resolved') {
				await promptModelResolution(resolution, responseMessage.modelIdx ?? 0);
				responseMessage.done = true;
				history.messages[history.currentId] = responseMessage;
				return;
			}

			const model = getModelById(resolution.value);

			if (model) {
				responseMessage.model = getModelRequestId(model);
				const modelRef = getModelRef(model);
				if (modelRef) {
					responseMessage.model_ref = modelRef;
				}
				history.messages[history.currentId] = responseMessage;
				await sendPromptSocket(history, model, responseMessage.id, _chatId);
				return;
			}

			responseMessage.done = true;
			history.messages[history.currentId] = responseMessage;
			await promptModelReselection({
				index: responseMessage.modelIdx ?? 0,
				rawModelId: requestedModelId,
				ambiguous: false
			});
		}
	};

	const mergeResponses = async (messageId, responses, _chatId) => {
		const message = history.messages[messageId];
		const mergedResponse = {
			status: true,
			content: ''
		};
		message.merged = mergedResponse;
		history.messages[messageId] = message;

		try {
			const [res, controller] = await generateMoACompletion(
				localStorage.token,
				message.model,
				history.messages[message.parentId].content,
				responses
			);

			if (res && res.ok && res.body) {
				const textStream = await createOpenAITextStream(res.body, $settings.splitLargeChunks);
				for await (const update of textStream) {
					const { value, image, done, sources, error, usage } = update;
					if (error || done) {
						break;
					}

					const appendValue = image?.markdown ?? value;
					if (!appendValue) {
						continue;
					}
					if (mergedResponse.content == '' && appendValue == '\n') {
						continue;
					} else {
						mergedResponse.content += appendValue;
						history.messages[messageId] = message;
					}

					if (shouldAutoScrollOnStreaming()) {
						scrollToBottom();
					}
				}

				await saveChatHandler(_chatId, history);
			} else {
				console.error(res);
			}
		} catch (e) {
			console.error(e);
		}
	};

	const initChatHandler = async (history) => {
		let _chatId = $chatId;

		if (!$temporaryChatEnabled) {
			chat = await createNewChat(localStorage.token, {
				id: _chatId,
				title: $i18n.t('New Chat'),
				system: $settings.system ?? undefined,
				tags: [],
				timestamp: Date.now(),
				...buildPersistedChatData(history)
			}, null, $selectedAssistantScene?.id ?? null);

			_chatId = chat.id;
			await chatId.set(_chatId);
			migrateChatSessionState('', _chatId);

			await chats.set(await getChatList(localStorage.token, $currentChatPage));
			currentChatPage.set(1);

			window.history.replaceState(history.state, '', `/c/${_chatId}`);
		} else {
			_chatId = 'local';
			await chatId.set('local');
			migrateChatSessionState('', _chatId);
		}
		await tick();

		return _chatId;
	};

	const saveChatHandler = async (
		_chatId,
		historyState,
		options: { selectionThreads?: PersistedSelectionThreads; messages?: any[] } = {}
	) => {
		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				const persistedSelectionThreads = options.selectionThreads ?? selectionThreads;
				const { history: normalizedHistory, changed } =
					await normalizeHistoryForPersistence(historyState);
				const persistedMessages =
					options.messages && !changed
						? options.messages
						: createMessagesList(normalizedHistory, normalizedHistory.currentId);

				if (changed && history === historyState) {
					history = normalizedHistory;
				}

				const payload = buildPersistedChatData(
					normalizedHistory,
					persistedMessages,
					persistedSelectionThreads
				);

				pendingChatSave = pendingChatSave
					.catch(() => undefined)
					.then(async () => {
						chat = await updateChatById(localStorage.token, _chatId, payload);
						currentChatPage.set(1);
						await chats.set(await getChatList(localStorage.token, $currentChatPage));
					});

				await pendingChatSave;
			}
		}
	};
</script>

<svelte:head>
	<title>
		{$settings?.showChatTitleInTab !== false && $chatTitle
			? `${$chatTitle.length > 30 ? `${$chatTitle.slice(0, 30)}...` : $chatTitle} | ${$WEBUI_NAME}`
			: `${$WEBUI_NAME}`}
	</title>
</svelte:head>

<audio id="audioElement" src="" style="display: none;" />

<EventConfirmDialog
	bind:show={showEventConfirmation}
	title={eventConfirmationTitle}
	message={eventConfirmationMessage}
	input={eventConfirmationInput}
	inputPlaceholder={eventConfirmationInputPlaceholder}
	inputValue={eventConfirmationInputValue}
	on:confirm={(e) => {
		if (e.detail) {
			eventCallback(e.detail);
		} else {
			eventCallback(true);
		}
	}}
	on:cancel={() => {
		eventCallback(false);
	}}
/>

<div class="h-screen max-h-[100dvh] w-full max-w-full flex flex-col relative" id="chat-container">
	{#if !loading}
		{#if $settings?.backgroundImageUrl ?? null}
			<div
				class="absolute top-0 left-0 w-full h-full z-0"
				style="background-image: url('{$settings.backgroundImageUrl}'); background-size: cover; background-position: center;"
			/>
			<div
				class="absolute top-0 left-0 w-full h-full bg-linear-to-t from-white/20 to-white/60 dark:from-gray-900/40 dark:to-gray-900/60 backdrop-blur-[2px] z-0"
			/>
		{:else}
			<div
				class="absolute top-0 left-0 w-full h-full bg-linear-to-b from-primary-50/30 to-white/20 dark:from-primary-900/10 dark:to-gray-950/20 pointer-events-none z-0"
			/>
		{/if}

		<PaneGroup direction="horizontal" class="w-full h-full">
			<Pane
				defaultSize={50}
				class="h-full flex relative max-w-full min-w-0 flex-col overflow-hidden"
			>
				<Navbar
					bind:this={navbarElement}
					chat={{
						id: $chatId,
						chat: {
							title: $chatTitle,
							models: selectedModels,
							system: $settings.system ?? undefined,
							params: params,
							assistant: activeAssistant ?? undefined,
							history: history,
							timestamp: Date.now()
						}
					}}
					{history}
					title={$chatTitle}
					bind:selectedModels
					shareEnabled={!!history.currentId}
					{initNewChat}
				/>

				<div class="flex flex-col flex-auto z-10 w-full min-w-0 @container">
					{#if ($settings?.landingPageMode === 'chat' && !$selectedAssistantScene) || hasMessages}
						<div
							class=" pb-2.5 flex flex-col justify-between w-full flex-auto overflow-auto h-0 max-w-full z-10 scrollbar-hidden"
							id="messages-container"
							bind:this={messagesContainerElement}
							on:wheel|passive={handleMessageOutlineWheel}
							on:touchmove|passive={handleMessageOutlineTouchMove}
							on:pointerdown={handleMessageOutlinePointerDown}
							on:scroll={handleMessagesScroll}
						>
							<div class=" h-full w-full flex flex-col">
								<Messages
									chatId={$chatId}
									bind:history
									bind:autoScroll
									bind:prompt
									{selectedModels}
									{atSelectedModel}
									{sendPrompt}
									{showMessage}
									{submitMessage}
									{continueResponse}
									{regenerateResponse}
									{mergeResponses}
									{chatActionHandler}
									{addMessages}
									onBranchMessage={branchMessageToCurrentChat}
									{branchingMessageId}
									branchSupported={Boolean($chatId && $chatId !== 'local' && !$temporaryChatEnabled)}
									initialMessagesCount={chatIdProp ? 6 : 20}
									messagesLoadStep={chatIdProp ? 6 : 20}
									deferOffscreenRendering={Boolean(chatIdProp)}
									bottomPadding={files.length > 0}
								/>
								<div bind:this={scrollSentinel} class="h-px w-full shrink-0" />
							</div>
						</div>

						<div class=" pb-[1rem]">
							<MessageQueue
								queue={messageQueue}
								onEdit={(id) => {
									const item = messageQueue.find((m) => m.id === id);
									if (item) {
										prompt = item.prompt;
										files = item.files;
										messageQueue = messageQueue.filter((m) => m.id !== id);
									}
								}}
								onDelete={(id) => {
									messageQueue = messageQueue.filter((m) => m.id !== id);
								}}
								onClearAll={() => {
									messageQueue = [];
								}}
							/>

							<MessageInput
								{history}
								{taskIds}
								{selectedModels}
								bind:files
								bind:prompt
								bind:autoScroll
								bind:selectedToolIds
								bind:toolSelectionTouched
								bind:selectedSkillIds
								bind:skillSelectionTouched
								bind:imageGenerationEnabled
								bind:imageGenerationOptions
								bind:codeInterpreterEnabled
								bind:webSearchMode
								{webSearchModeSource}
								bind:atSelectedModel
								bind:reasoningEffort
								bind:maxThinkingTokens
								{activeAssistant}
								onDeactivateAssistant={deactivateAssistant}
								toolServers={$toolServers}
								transparentBackground={$settings?.backgroundImageUrl ?? false}
								{stopResponse}
								{createMessagePair}
								onChange={handleMessageInputChange}
								on:upload={async (e) => {
									const { type, data } = e.detail;

									if (type === 'web') {
										await uploadWeb(data);
									} else if (type === 'youtube') {
										await uploadYoutubeTranscription(data);
									} else if (type === 'google-drive') {
										await uploadGoogleDriveFile(data);
									}
								}}
								on:submit={async (e) => {
									if (e.detail || files.length > 0) {
										await tick();
										submitPrompt(
											($settings?.richTextInput ?? true)
												? e.detail.replaceAll('\n\n', '\n')
												: e.detail
										);
									}
								}}
							/>

							<div
								class="absolute bottom-1 text-xs text-gray-500 text-center line-clamp-1 right-0 left-0"
							>
								<!-- {$i18n.t('LLMs can make mistakes. Verify important information.')} -->
							</div>
						</div>
					{:else}
						<div class="overflow-auto w-full h-full flex items-center">
							<Placeholder
								{history}
								{selectedModels}
								bind:files
								bind:prompt
								bind:autoScroll
								bind:selectedToolIds
								bind:toolSelectionTouched
								bind:selectedSkillIds
								bind:skillSelectionTouched
								bind:imageGenerationEnabled
								bind:imageGenerationOptions
								bind:codeInterpreterEnabled
								bind:webSearchMode
								{webSearchModeSource}
								bind:atSelectedModel
								bind:reasoningEffort
								bind:maxThinkingTokens
								{activeAssistant}
								onActivateAssistant={activateAssistant}
								onDeactivateAssistant={deactivateAssistant}
								transparentBackground={$settings?.backgroundImageUrl ?? false}
								toolServers={$toolServers}
								{stopResponse}
								{createMessagePair}
								onChange={handleMessageInputChange}
								on:upload={async (e) => {
									const { type, data } = e.detail;

									if (type === 'web') {
										await uploadWeb(data);
									} else if (type === 'youtube') {
										await uploadYoutubeTranscription(data);
									}
								}}
								on:submit={async (e) => {
									if (e.detail || files.length > 0) {
										await tick();
										submitPrompt(
											($settings?.richTextInput ?? true)
												? e.detail.replaceAll('\n\n', '\n')
												: e.detail
										);
									}
								}}
							/>
						</div>
					{/if}
				</div>
			</Pane>

			<ChatControls
				bind:this={controlPaneComponent}
				bind:history
				bind:chatFiles
				bind:params
				bind:files
				bind:pane={controlPane}
				chatId={$chatId}
				modelId={selectedModelIds?.at(0) ?? null}
				models={selectedModelIds.reduce((a, e, i, arr) => {
					const model = getModelById(e);
					if (model) {
						return [...a, model];
					}
					return a;
				}, [])}
				{submitPrompt}
				{stopResponse}
				{showMessage}
				{eventTarget}
				{imageGenerationEnabled}
				{currentValvesContext}
			/>
		</PaneGroup>
	{:else if loading}
		<div class=" flex items-center justify-center h-full w-full">
			<div class="m-auto">
				<Spinner />
			</div>
		</div>
	{/if}
</div>
