<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';
	import mermaid from 'mermaid';
	import { PaneGroup, Pane, PaneResizer } from 'paneforge';

	import { getContext, onDestroy, onMount, setContext, tick } from 'svelte';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { get, type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import {
		chatId,
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
		toolServers,
		activeChatIds
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
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import {
		getTemporaryChatAccess,
		getTemporaryChatNavigationPath,
		persistTemporaryChatOverride,
		resolveTemporaryChatEnabled
	} from '$lib/utils/temporary-chat';
	import {
		getPreferredWebSearchMode,
		normalizeWebSearchMode,
		type WebSearchMode
	} from '$lib/utils/web-search-mode';
	import { getFunctionPipeRootId } from '$lib/utils/image-generation';

	import { generateChatCompletion } from '$lib/apis/ollama';
	import {
		addTagById,
		createNewChat,
		deleteTagById,
		deleteTagsById,
		getAllTags,
		getChatById,
		getChatContextById,
		getChatList,
		updateChatById
	} from '$lib/apis/chats';
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

	export let chatIdProp = '';

	let loading = false;

	const eventTarget = new EventTarget();
	type PendingGeminiImage = {
		mimeType: string;
		parts: string[];
	};
	const pendingGeminiImages = new Map<string, Map<string, PendingGeminiImage>>();
	const buildImageDataUrl = (mimeType: string, data: string) =>
		`data:${mimeType};base64,${data}`;
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

	let navbarElement;

	let showEventConfirmation = false;
	let eventConfirmationTitle = '';
	let eventConfirmationMessage = '';
	let eventConfirmationInput = false;
	let eventConfirmationInputPlaceholder = '';
	let eventConfirmationInputValue = '';
	let eventCallback = null;

	let chatIdUnsubscriber: Unsubscriber | undefined;

	let selectedModels = [''];
	let atSelectedModel: Model | undefined;
	let selectedModelIds = [];
	$: selectedModelIds = atSelectedModel !== undefined ? [atSelectedModel.id] : selectedModels;

	let selectedToolIds = [];
	let imageGenerationEnabled = false;
	let imageGenerationOptions: {
		image_size?: string | null;
		aspect_ratio?: string | null;
		n?: number | null;
	} = {};
	let webSearchMode: WebSearchMode = 'off';
	let codeInterpreterEnabled = false;

	let chat = null;
	let tags = [];

	let history = {
		messages: {},
		currentId: null
	};

	// J-3-01: O(1) model lookup map — rebuilt reactively when $models changes
	let modelsMap: Map<string, Model> = new Map();
	$: {
		const m = new Map<string, Model>();
		for (const model of $models) {
			m.set(model.id, model);
		}
		modelsMap = m;
	}
	const getModelById = (id: string): Model | undefined => modelsMap.get(id);

	// J-3-01: Reactive flag to avoid calling createMessagesList just for emptiness check in template
	let hasMessages = false;
	$: hasMessages = history.currentId !== null;

	let taskIds = null;
	let messageQueue: { id: string; prompt: string; files: any[] }[] = [];

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

	// Bidirectional sync: Controls sidebar params ↔ inline ThinkingControl
	// 用缓存值打断 reactive 级联：正向同步更新缓存 → 反向 onChange 检测到缓存一致则跳过
	let _lastSyncedEffort: string | null = null;
	let _lastSyncedTokens: number | null = null;

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
				const textContent = message?.merged?.content ?? processDetails(message?.content ?? '');
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
											url: file.url
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
		const requestedWebSearchMode =
			$config?.features?.enable_web_search &&
			($user?.role === 'admin' || $user?.permissions?.features?.web_search)
				? normalizeWebSearchMode(webSearchMode, 'off')
				: 'off';
		const requestFiles = collectFloatingRequestFiles(messages);

		return {
			stream,
			model: model.id,
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
			tool_servers: $toolServers,
			features: {
				memory: $settings?.memory ?? false,
				image_generation:
					$config?.features?.enable_image_generation &&
					($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
						? imageGenerationEnabled
						: false,
				image_generation_options:
					imageGenerationEnabled &&
					$config?.features?.enable_image_generation &&
					($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
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
			model_item: getModelById(model.id),
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
		getPreferredWebSearchMode($settings, $config, 'off');

	const getImageGenerationOptionsPayload = () => {
		const raw = imageGenerationOptions ?? {};
		const payload = Object.fromEntries(
			Object.entries(raw).filter(([, value]) => value !== undefined && value !== null && value !== '')
		);
		return Object.keys(payload).length > 0 ? payload : undefined;
	};

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

		if (selectedToolIds.length > 0) {
			return {
				tab: 'tools' as const,
				id: selectedToolIds[0]
			};
		}

		return null;
	})();

	const resolveStoredWebSearchMode = (
		value: { webSearchMode?: unknown; webSearchEnabled?: unknown } | null | undefined,
		fallback: WebSearchMode = getPreferredDefaultWebSearchMode()
	): WebSearchMode => {
		if (value?.webSearchMode !== undefined) {
			return normalizeWebSearchMode(value.webSearchMode, fallback);
		}

		if (value?.webSearchEnabled === true) {
			return 'halo';
		}

		return fallback;
	};

	const getChatSessionStateKey = (id: string | null | undefined = $chatId || chatIdProp) =>
		`chat-session-state-${id && id !== '' ? id : 'new'}`;

	const safeParseStoredJson = <T>(rawValue: string | null | undefined, fallback: T): T => {
		if (!rawValue) {
			return fallback;
		}

		try {
			return JSON.parse(rawValue) as T;
		} catch {
			return fallback;
		}
	};

	const readLegacyInputSettings = (id: string | null | undefined = $chatId || chatIdProp) => {
		try {
			const input = JSON.parse(localStorage.getItem(`chat-input-${id ?? ''}`) || 'null');
			if (!input) {
				return null;
			}

			return {
				webSearchMode: input.webSearchMode,
				reasoningEffort: input.reasoningEffort,
				maxThinkingTokens: input.maxThinkingTokens,
				imageGenerationOptions: input.imageGenerationOptions
			};
		} catch {
			return null;
		}
	};

		const restoreChatSessionState = (id: string | null | undefined = $chatId || chatIdProp) => {
			try {
				const stored = JSON.parse(localStorage.getItem(getChatSessionStateKey(id)) || 'null');
				const state = stored ?? readLegacyInputSettings(id);

			if (!state) {
				return false;
			}

			webSearchMode = resolveStoredWebSearchMode(state);
			if (state.reasoningEffort !== undefined) {
				reasoningEffort = state.reasoningEffort ?? null;
			}
				if (state.maxThinkingTokens !== undefined) {
					maxThinkingTokens = state.maxThinkingTokens ?? null;
				}
				if (state.imageGenerationEnabled !== undefined) {
					imageGenerationEnabled = Boolean(state.imageGenerationEnabled);
				}
				if (state.imageGenerationOptions !== undefined) {
					imageGenerationOptions = state.imageGenerationOptions ?? {};
				}
				if (state.codeInterpreterEnabled !== undefined) {
					codeInterpreterEnabled = Boolean(state.codeInterpreterEnabled);
				}

				return true;
			} catch {
				return false;
			}
	};

	const persistChatSessionState = (id: string | null | undefined = $chatId || chatIdProp) => {
		localStorage.setItem(
			getChatSessionStateKey(id),
			JSON.stringify({
					webSearchMode: resolveStoredWebSearchMode(
						{ webSearchMode },
						getPreferredDefaultWebSearchMode()
					),
					imageGenerationEnabled,
					imageGenerationOptions,
					codeInterpreterEnabled,
					reasoningEffort,
					maxThinkingTokens
				})
			);
		};

	const removeChatSessionState = (id: string | null | undefined = $chatId || chatIdProp) => {
		localStorage.removeItem(getChatSessionStateKey(id));
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

	// 正向同步: Controls(params) → ThinkingControl(reasoningEffort/maxThinkingTokens)
	$: {
		const re = params?.reasoning_effort ?? null;
		const mt = params?.max_thinking_tokens ?? null;
		if (re !== _lastSyncedEffort || mt !== _lastSyncedTokens) {
			_lastSyncedEffort = re;
			_lastSyncedTokens = mt;
			if (re && re !== 'none' && re !== reasoningEffort) {
				reasoningEffort = re;
				maxThinkingTokens = null;
			} else if (mt !== null && mt > 0 && mt !== maxThinkingTokens) {
				maxThinkingTokens = mt;
				reasoningEffort = null;
			} else if (!re && (mt === null || mt === 0)) {
				reasoningEffort = re === 'none' ? 'none' : null;
				maxThinkingTokens = mt === 0 ? 0 : null;
			}
		}
	}

	$: if (chatIdProp) {
		(async () => {
			loading = true;

			prompt = '';
			files = [];
			selectedToolIds = [];
			webSearchMode = getPreferredDefaultWebSearchMode();
			imageGenerationEnabled = false;
			imageGenerationOptions = {};
			reasoningEffort = null;
			maxThinkingTokens = null;

			if (chatIdProp && (await loadChat())) {
				loading = false;
				await tick();
				restoreChatSessionState(chatIdProp);

				if (localStorage.getItem(`chat-input-${chatIdProp}`)) {
					try {
						const input = JSON.parse(localStorage.getItem(`chat-input-${chatIdProp}`));

						prompt = input.prompt;
						files = input.files;
						selectedToolIds = input.selectedToolIds;
						imageGenerationEnabled = input.imageGenerationEnabled;
						imageGenerationOptions = input.imageGenerationOptions ?? {};
					} catch (e) {}
				}

				window.setTimeout(() => scrollToBottom(), 0);
				const chatInput = document.getElementById('chat-input');
				chatInput?.focus();
			} else {
				await goto('/');
			}
		})();
	}

	$: if (selectedModels) {
		saveSessionSelectedModels();
	}

	const saveSessionSelectedModels = () => {
		if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
			return;
		}
		sessionStorage.selectedModels = JSON.stringify(selectedModels);
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
		if (sessionStorage.selectedModels) {
			try {
				const stored = JSON.parse(sessionStorage.selectedModels);
				const valid = stored.filter((id: string) => modelsMap.has(id));
				if (valid.length > 0) {
					selectedModels = valid;
				}
			} catch {}
		}
	}

	$: if ($tools && (atSelectedModel || selectedModels)) {
		setToolIds();
	}

	const setToolIds = async () => {
		if (!$tools) {
			tools.set(await getTools(localStorage.token));
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

	const showMessage = async (message) => {
		const _chatId = $chatId;
		let _messageId = message.id;

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
			if (localStorage.getItem(`chat-input-${chatIdProp}`)) {
				try {
					const input = JSON.parse(localStorage.getItem(`chat-input-${chatIdProp}`));
					prompt = input.prompt;
					files = input.files;
					selectedToolIds = input.selectedToolIds;
					imageGenerationEnabled = input.imageGenerationEnabled;
					imageGenerationOptions = input.imageGenerationOptions ?? {};
				} catch (e) {
					prompt = '';
					files = [];
					selectedToolIds = [];
					webSearchMode = getPreferredDefaultWebSearchMode();
					imageGenerationEnabled = false;
					imageGenerationOptions = {};
				}
			}
			restoreChatSessionState(chatIdProp);
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
		scrollObserver?.disconnect();
		cancelScheduledScrollToBottom();
		clearResponseAnimationControllers();
		window.removeEventListener('message', onMessageHandler);
		window.removeEventListener('chat:set-input', onSetInputHandler as EventListener);
		$socket?.off('chat-events', chatEventHandler);
	});

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

		if ($page.url.searchParams.get('models')) {
			selectedModels = $page.url.searchParams.get('models')?.split(',');
		} else if ($page.url.searchParams.get('model')) {
			const urlModels = $page.url.searchParams.get('model')?.split(',');

			if (urlModels.length === 1) {
				const m = getModelById(urlModels[0]);
				if (!m) {
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
					selectedModels = urlModels;
				}
			} else {
				selectedModels = urlModels;
			}
		} else if (!fresh) {
			if (sessionStorage.selectedModels) {
				const storedSelectedModels = safeParseStoredJson<string[] | null>(
					sessionStorage.selectedModels,
					null
				);
				if (Array.isArray(storedSelectedModels)) {
					selectedModels = storedSelectedModels;
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
			selectedModels = selectedModels.filter((modelId) => modelsMap.has(modelId));
			if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
				if (!fresh && $models.length > 0) {
					// Non-fresh: auto-select first available model as fallback
					selectedModels = [$models[0].id];
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
		clearResponseAnimationControllers();

		if (fresh) {
			chat = null;
			tags = [];
			taskIds = null;
			processing = '';
			atSelectedModel = undefined;
			prompt = '';
			files = [];
			selectedToolIds = [];
			imageGenerationEnabled = false;
			imageGenerationOptions = {};
			codeInterpreterEnabled = false;
		}

		chatFiles = [];
		params = {};

		if (fresh) {
			removeChatSessionState('');
			reasoningEffort = null;
			maxThinkingTokens = null;
			webSearchMode = getPreferredDefaultWebSearchMode();
		} else {
			reasoningEffort = null;
			maxThinkingTokens = null;
			webSearchMode = getPreferredDefaultWebSearchMode();
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
		} else if ($page.url.searchParams.get('tool-ids')) {
			selectedToolIds = $page.url.searchParams
				.get('tool-ids')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
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
			selectedModels = selectedModels.map((modelId) => (modelsMap.has(modelId) ? modelId : ''));
		}

		const userSettings = await getUserSettings(localStorage.token);

		if (userSettings) {
			settings.set(userSettings.ui);
			temporaryChatState = syncTemporaryChatState(userSettings.ui);
		} else {
			const localSettings = safeParseStoredJson(localStorage.getItem('settings'), {});
			settings.set(localSettings);
			temporaryChatState = syncTemporaryChatState(localSettings);
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

	const loadChat = async () => {
		const navigationId = chatIdProp;
		chatId.set(chatIdProp);
		tags = [];
		taskIds = null;
		const chatContextPromise = getChatContextById(localStorage.token, chatIdProp).catch(() => ({
			tags: [],
			task_ids: []
		}));

		chat = await getChatById(localStorage.token, $chatId).catch(async (error) => {
			await goto('/');
			return null;
		});

		if (navigationId !== chatIdProp) return null;

		if (chat) {
			const chatContent = chat.chat;

			if (chatContent) {
				selectedModels =
					(chatContent?.models ?? undefined) !== undefined
						? chatContent.models
						: [chatContent.models ?? ''];
				history =
					(chatContent?.history ?? undefined) !== undefined
						? chatContent.history
						: convertMessagesToHistory(chatContent.messages);

				chatTitle.set(chatContent.title);

				if (!$settings || Object.keys($settings).length === 0) {
					await settings.set(safeParseStoredJson(localStorage.getItem('settings'), {}));
				}

				params = chatContent?.params ?? {};
				chatFiles = chatContent?.files ?? [];

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

	const createResponseAnimationController = (
		message
	): ResponseAnimationController => ({
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

	const cancelScheduledScrollToBottom = () => {
		if (_scrollRafId !== null) {
			cancelAnimationFrame(_scrollRafId);
			_scrollRafId = null;
		}
		if (_scrollResetRafId !== null) {
			cancelAnimationFrame(_scrollResetRafId);
			_scrollResetRafId = null;
		}
	};

	const handleMessagesScroll = () => {
		if (!messagesContainerElement || isAutoScrolling) {
			return;
		}

		if (isNearBottom()) {
			userHasScrolled = false;
			autoScroll = true;
			return;
		}

		userHasScrolled = true;
		autoScroll = false;
	};

	let _scrollRafId: number | null = null;
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
				});
			}
		});
	};
	const chatCompletedHandler = async (chatId, modelId, responseMessageId, messages) => {
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
		}).catch((error) => {
			toast.error(`${error}`);
			messages.at(-1).error = { content: error };

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
				chat = await updateChatById(localStorage.token, chatId, {
					models: selectedModels,
					messages: messages,
					history: history,
					params: params,
					files: chatFiles
				});

				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
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
		}).catch((error) => {
			toast.error(`${error}`);
			messages.at(-1).error = { content: error };
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
				chat = await updateChatById(localStorage.token, chatId, {
					models: selectedModels,
					messages: messages,
					history: history,
					params: params,
					files: chatFiles
				});

				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
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
				modelName: getModelChatDisplayName(model) || model.id,
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
					model: model.id,
					modelName: getModelChatDisplayName(model) || model.id,
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
		const _selectedModels = selectedModels.map((modelId) =>
			modelsMap.has(modelId) ? modelId : ''
		);
		if (JSON.stringify(selectedModels) !== JSON.stringify(_selectedModels)) {
			selectedModels = _selectedModels;
		}

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
			validFiles.filter((file) => file.type !== 'image' && file.status === 'uploading').length > 0
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
				const queuedFiles = structuredClone(validFiles);
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

		const _files = structuredClone(validFiles);
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
		let selectedModelIds = modelId
			? [modelId]
			: atSelectedModel !== undefined
				? [atSelectedModel.id]
				: selectedModels;

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
					model: model.id,
					modelName: getModelChatDisplayName(model) || model.id,
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

					const chatEventEmitter = await getChatEventEmitter(model.id, _chatId);

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

		messages = messages
			.map((message, idx, arr) => {
				let textContent = message?.merged?.content ?? message.content;

				// Inject regeneration instruction into the last user message for API payload only
				if (
					_pendingInstruction &&
					message.role === 'user' &&
					idx === arr.findLastIndex((m) => m.role === 'user')
				) {
					textContent = `${textContent}\n\n${_pendingInstruction}`;
				}

				return {
					role: message.role,
					...((message.files?.filter((file) => file.type === 'image').length > 0 ?? false) &&
					message.role === 'user'
						? {
								content: [
									{
										type: 'text',
										text: textContent
									},
									...message.files
										.filter((file) => file.type === 'image')
										.map((file) => ({
											type: 'image_url',
											image_url: {
												url: file.url
											}
										}))
								]
							}
						: {
								content: textContent
							})
				};
			})
			.filter((message) => message?.role === 'user' || message?.content?.trim());

		const requestedWebSearchMode = (
			$config?.features?.enable_web_search &&
			($user?.role === 'admin' || $user?.permissions?.features?.web_search)
		)
			? normalizeWebSearchMode(webSearchMode, 'off')
			: 'off';

		const res = await generateOpenAIChatCompletion(
			localStorage.token,
			{
				stream: stream,
				model: model.id,
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
				tool_servers: $toolServers,

				features: {
					memory: $settings?.memory ?? false,
					image_generation:
						$config?.features?.enable_image_generation &&
						($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
							? imageGenerationEnabled
							: false,
					image_generation_options:
						imageGenerationEnabled &&
						$config?.features?.enable_image_generation &&
						($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
							? getImageGenerationOptionsPayload()
							: undefined,
					code_interpreter:
						$config?.features?.enable_code_interpreter &&
						($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
							? codeInterpreterEnabled
							: false,
					web_search: requestedWebSearchMode !== 'off',
					web_search_mode:
						requestedWebSearchMode !== 'off' ? requestedWebSearchMode : undefined
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
				model_item: getModelById(model.id),

				session_id: $socket?.id,
				chat_id: $chatId,
				id: responseMessageId,

				...(!$temporaryChatEnabled &&
				(messages.length == 1 ||
					(messages.length == 2 &&
						messages.at(0)?.role === 'system' &&
						messages.at(1)?.role === 'user')) &&
				(selectedModels[0] === model.id || atSelectedModel !== undefined)
					? {
							background_tasks: {
								title_generation: $settings?.title?.auto ?? true,
								tags_generation: $settings?.autoTags ?? true,
								follow_up_generation: $settings?.autoFollowUps ?? true
							}
						}
					: !$temporaryChatEnabled && ($settings?.autoFollowUps ?? true)
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
			toast.error(`${error}`);

			responseMessage.error = {
				content: error
			};
			responseMessage.done = true;

			history.messages[responseMessageId] = responseMessage;
			history.currentId = responseMessageId;
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
		if (family === 'rate_limited' || family === 'timeout') {
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
			toast.error(`${innerError.title ?? innerError.content ?? $i18n.t('error.title.upstream_service_error_no_status')}`);
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

			const model = getModelById(responseMessage.model);

			if (model) {
				await sendPromptSocket(history, model, responseMessage.id, _chatId);
			}
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
				const textStream = await createOpenAITextStream(
					res.body,
					$settings.splitLargeChunks
				);
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
				models: selectedModels,
				system: $settings.system ?? undefined,
				params: params,
				history: history,
				messages: createMessagesList(history, history.currentId),
				tags: [],
				timestamp: Date.now()
			});

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

	const saveChatHandler = async (_chatId, history) => {
		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				chat = await updateChatById(localStorage.token, _chatId, {
					models: selectedModels,
					history: history,
					messages: createMessagesList(history, history.currentId),
					params: params,
					files: chatFiles
				});
				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
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
			<Pane defaultSize={50} class="h-full flex relative max-w-full min-w-0 flex-col overflow-hidden">
				<Navbar
					bind:this={navbarElement}
					chat={{
						id: $chatId,
						chat: {
							title: $chatTitle,
							models: selectedModels,
							system: $settings.system ?? undefined,
							params: params,
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
					{#if $settings?.landingPageMode === 'chat' || hasMessages}
							<div
								class=" pb-2.5 flex flex-col justify-between w-full flex-auto overflow-auto h-0 max-w-full z-10 scrollbar-hidden"
								id="messages-container"
								bind:this={messagesContainerElement}
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
								bind:imageGenerationEnabled
								bind:imageGenerationOptions
								bind:codeInterpreterEnabled
								bind:webSearchMode
								bind:atSelectedModel
								bind:reasoningEffort
								bind:maxThinkingTokens
								toolServers={$toolServers}
								transparentBackground={$settings?.backgroundImageUrl ?? false}
								{stopResponse}
								{createMessagePair}
								onChange={(input) => {
									// 反向同步: ThinkingControl → Controls(params)
									const newRE = input.reasoningEffort ?? null;
									const newMT = input.maxThinkingTokens ?? null;
									const oldRE = params?.reasoning_effort ?? null;
									const oldMT = params?.max_thinking_tokens ?? null;
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
									webSearchMode = resolveStoredWebSearchMode(
										{ webSearchMode: input.webSearchMode },
										getPreferredDefaultWebSearchMode()
									);
									reasoningEffort = input.reasoningEffort ?? null;
									maxThinkingTokens = input.maxThinkingTokens ?? null;
									persistChatSessionState();

									if (input.prompt) {
										localStorage.setItem(`chat-input-${$chatId}`, JSON.stringify(input));
									} else {
										localStorage.removeItem(`chat-input-${$chatId}`);
									}
								}}
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
								bind:imageGenerationEnabled
								bind:imageGenerationOptions
								bind:codeInterpreterEnabled
								bind:webSearchMode
								bind:atSelectedModel
								bind:reasoningEffort
								bind:maxThinkingTokens
								transparentBackground={$settings?.backgroundImageUrl ?? false}
								toolServers={$toolServers}
								{stopResponse}
								{createMessagePair}
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
