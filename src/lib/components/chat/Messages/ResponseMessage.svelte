<script lang="ts">
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';

	import { createEventDispatcher } from 'svelte';
	import { onMount, tick, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType, t } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	const dispatch = createEventDispatcher();

	import {
		config,
		mobile,
		models,
		settings,
		TTSWorker,
		activeAudioId,
		user
	} from '$lib/stores';
	import { synthesizeOpenAISpeech } from '$lib/apis/audio';
	import { imageGenerations } from '$lib/apis/images';
	// [REACTION_FEATURE] Commented out - reaction feature disabled for now
	// import {
	// 	addChatMessageReaction,
	// 	removeChatMessageReaction,
	// 	getChatMessageReactions
	// } from '$lib/apis/chats';
	import {
		copyToClipboard as _copyToClipboard,
		approximateToHumanReadable,
		getMessageContentParts,
		sanitizeResponseContent,
		formatDate,
		removeAllDetails,
		stripThinkingBlocks
	} from '$lib/utils';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import Name from './Name.svelte';
	import ModelIcon from '$lib/components/common/ModelIcon.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import WebSearchResults from './ResponseMessage/WebSearchResults.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';

	import {
		ChevronLeft,
		ChevronRight,
		PencilLine,
		Copy,
		Volume2,
		VolumeX,
		ImagePlus,
		Info,
		PlayCircle,
		RefreshCw,
		Trash2,
		ListPlus,
		AlignLeft,
		Lightbulb,
		Globe,
		ArrowRight
	} from 'lucide-svelte';
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import Error from './Error.svelte';
	import Citations from './Citations.svelte';
	import CodeExecutions from './CodeExecutions.svelte';
	import ContentRenderer from './ContentRenderer.svelte';
	import MessageOutline from './MessageOutline.svelte';
	import ThinkingIndicator from './ThinkingIndicator.svelte';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import type { HeadingItem } from '$lib/utils/headings';

	interface MessageType {
		id: string;
		model: string;
		content: string;
		files?: { type: string; url: string }[];
		timestamp: number;
		role: string;
		statusHistory?: {
			done: boolean;
			action: string;
			description: string;
			urls?: string[];
			query?: string;
		}[];
		status?: {
			done: boolean;
			action: string;
			description: string;
			urls?: string[];
			query?: string;
		};
		done: boolean;
		completedAt?: number;
		usage?: Record<string, unknown>;
		error?: boolean | { content: string };
		sources?: string[];
		followUps?: string[];
		code_executions?: {
			uuid: string;
			name: string;
			code: string;
			language?: string;
			result?: {
				error?: string;
				output?: string;
				files?: { name: string; url: string }[];
			};
		}[];
		info?: {
			openai?: boolean;
			prompt_tokens?: number;
			completion_tokens?: number;
			total_tokens?: number;
			eval_count?: number;
			eval_duration?: number;
			prompt_eval_count?: number;
			prompt_eval_duration?: number;
			total_duration?: number;
			load_duration?: number;
			usage?: unknown;
		};
	}

	export let chatId = '';
	export let history;
	export let messageId;

	let message: MessageType = history.messages?.[messageId] as MessageType;
	$: message = history.messages?.[messageId] as MessageType;

	function getVisibleAssistantOutput(content: string): string {
		return sanitizeResponseContent(
			removeAllDetails(stripThinkingBlocks(content ?? '')).replace(/<tool_calls\b[^>]*\/>/gi, '')
		);
	}

	$: hasVisibleAssistantOutput = getVisibleAssistantOutput(message?.content ?? '') !== '';
	$: hasVisibleThinkingOutput =
		/<details\b[^>]*type="reasoning"/i.test(message?.content ?? '') ||
		/<(think|thinking|reasoning)\b[^>]*>/i.test(message?.content ?? '');
	$: displayStatusHistory = (
		message?.statusHistory ?? [...(message?.status ? [message?.status] : [])]
	).filter((status) => {
		if (status?.action === 'model_request') {
			return false;
		}

		if (hasVisibleAssistantOutput && status?.action === 'tool_loading') {
			return false;
		}

		return true;
	});

	export let siblings;

	export let gotoMessage: Function = () => {};
	export let showPreviousMessage: Function;
	export let showNextMessage: Function;

	export let updateChat: Function;
	export let editMessage: Function;
	export let saveMessage: Function;
	export let actionMessage: Function;
	export let deleteMessage: Function;

	export let submitMessage: Function;
	export let continueResponse: Function;
	export let regenerateResponse: Function;

	export let addMessages: Function;

	export let isLastMessage = true;
	export let readOnly = false;

	let buttonsContainerElement: HTMLDivElement;
	let citationsRef: any = null;
	let buttonsScrollBound = false;

	function setupButtonsScroll() {
		if (buttonsContainerElement && !buttonsScrollBound) {
			buttonsScrollBound = true;
			buttonsContainerElement.addEventListener('wheel', function (event) {
				event.preventDefault();
				if (event.deltaY !== 0) {
					buttonsContainerElement.scrollLeft += event.deltaY;
				}
			});
		}
	}

	$: if (buttonsContainerElement) {
		setupButtonsScroll();
	}
	let showDeleteConfirm = false;
	let showRegenerateConfirm = false;
	let showRegenerateMenu = false;
	let regenerateInput = '';

	$: modelSupportsThinking = model?.info?.meta?.capabilities?.reasoning ?? false;

	// [REACTION_FEATURE] Commented out - reaction feature disabled for now
	// let reactions: { name: string; user_ids: string[]; count: number }[] = [];
	// let showReactionPicker = false;
	// let reactionBtnEl: HTMLButtonElement;
	// let reactionPickerPos = { top: 0, left: 0 };
	// const QUICK_REACTIONS = ['+1', '-1', 'heart', 'laughing', 'thinking_face', 'eyes'];
	//
	// let _reactionsLoadedFor = '';
	// const loadReactions = async () => {
	// 	if (!chatId || !message?.id) return;
	// 	const key = `${chatId}:${message.id}`;
	// 	if (_reactionsLoadedFor === key) return;
	// 	_reactionsLoadedFor = key;
	// 	try {
	// 		reactions = (await getChatMessageReactions(localStorage.token, chatId, message.id)) ?? [];
	// 	} catch (e) {
	// 		console.error('loadReactions failed:', chatId, message.id, e);
	// 		reactions = [];
	// 	}
	// };
	//
	// $: if (chatId && message?.id) {
	// 	loadReactions();
	// }
	//
	// const toggleReaction = async (name: string) => {
	// 	if (!chatId || !message.id) return;
	// 	const existing = reactions.find((r) => r.name === name);
	// 	const hasMyReaction = existing?.user_ids?.includes($user?.id);
	//
	// 	// Optimistic update
	// 	if (hasMyReaction) {
	// 		reactions = reactions
	// 			.map((r) =>
	// 				r.name === name
	// 					? { ...r, user_ids: r.user_ids.filter((id) => id !== $user?.id), count: r.count - 1 }
	// 					: r
	// 			)
	// 			.filter((r) => r.count > 0);
	// 	} else if (existing) {
	// 		reactions = reactions.map((r) =>
	// 			r.name === name
	// 				? { ...r, user_ids: [...r.user_ids, $user?.id], count: r.count + 1 }
	// 				: r
	// 		);
	// 	} else {
	// 		reactions = [...reactions, { name, user_ids: [$user?.id], count: 1 }];
	// 	}
	//
	// 	try {
	// 		const updated = hasMyReaction
	// 			? await removeChatMessageReaction(localStorage.token, chatId, message.id, name)
	// 			: await addChatMessageReaction(localStorage.token, chatId, message.id, name);
	// 		if (updated) reactions = updated;
	// 	} catch {
	// 		_reactionsLoadedFor = '';
	// 		await loadReactions();
	// 	}
	// };

	let model = null;
	$: model = $models.find((m) => m.id === message.model);
	$: stats = getStatsDisplay(message);

	const doRegenerate = () => {
		regenerateResponse(message);
		(model?.actions ?? []).forEach((action) => {
			dispatch('action', {
				id: action.id,
				event: { id: 'regenerate-response', data: { messageId: message.id } }
			});
		});
	};

	let edit = false;
	let editedContent = '';
	let editTextAreaElement: HTMLTextAreaElement;
	let contentRendererRef: any = null;
	let messageHeadings: HeadingItem[] = [];
	$: canShowMessageOutline =
		!edit && !$mobile && ($settings?.showMessageOutline ?? true) && messageHeadings.length >= 1;

	let messageIndexEdit = false;

	let audioParts: Record<number, HTMLAudioElement | null> = {};
	let speaking = false;
	let speakingIdx: number | undefined;

	let loadingSpeech = false;
	let generatingImage = false;

	// Global audio queue: stop this message's TTS if another message starts playing
	$: if (speaking && $activeAudioId !== null && $activeAudioId !== messageId) {
		try {
			speechSynthesis.cancel();
			if (speakingIdx !== undefined && audioParts[speakingIdx]) {
				audioParts[speakingIdx]!.pause();
				audioParts[speakingIdx]!.currentTime = 0;
			}
		} catch {}
		speaking = false;
		speakingIdx = undefined;
	}

	const setInputText = (prompt: string) => {
		window.dispatchEvent(
			new CustomEvent('chat:set-input', {
				detail: { prompt }
			})
		);
	};

	const copyToClipboard = async (text) => {
		text = removeAllDetails(text);

		const res = await _copyToClipboard(text, $settings?.copyFormatted ?? false);
		if (res) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	const playAudio = (idx: number) => {
		return new Promise<void>((res) => {
			speakingIdx = idx;
			const audio = audioParts[idx];

			if (!audio) {
				return res();
			}

			audio.play();
			audio.onended = async () => {
				await new Promise((r) => setTimeout(r, 300));

				if (Object.keys(audioParts).length - 1 === idx) {
					speaking = false;
					activeAudioId.set(null);
				}

				res();
			};
		});
	};

	const toggleSpeakMessage = async () => {
		if (speaking) {
			try {
				speechSynthesis.cancel();

				if (speakingIdx !== undefined && audioParts[speakingIdx]) {
					audioParts[speakingIdx]!.pause();
					audioParts[speakingIdx]!.currentTime = 0;
				}
			} catch {}

			speaking = false;
			speakingIdx = undefined;
			activeAudioId.set(null);
			return;
		}

		if (!(message?.content ?? '').trim().length) {
			toast.info($i18n.t('No content to speak'));
			return;
		}

		// Claim global audio — this stops any other message's TTS
		activeAudioId.set(messageId);

		speaking = true;

		if ($config.audio.tts.engine === '') {
			let voices = [];
			const getVoicesLoop = setInterval(() => {
				voices = speechSynthesis.getVoices();
				if (voices.length > 0) {
					clearInterval(getVoicesLoop);

					// Per-model voice override, fallback to global
					const modelVoices = $settings?.audio?.tts?.modelVoices ?? {};
					const targetVoice =
						modelVoices[message.model] ||
						$settings?.audio?.tts?.voice ||
						$config?.audio?.tts?.voice;

					const voice = voices?.filter((v) => v.voiceURI === targetVoice)?.at(0) ?? undefined;

					console.log(voice);

					const speak = new SpeechSynthesisUtterance(stripThinkingBlocks(message.content));
					speak.rate = $settings.audio?.tts?.playbackRate ?? 1;

					console.log(speak);

					speak.onend = () => {
						speaking = false;
						activeAudioId.set(null);
						if ($settings.conversationMode) {
							document.getElementById('voice-input-button')?.click();
						}
					};

					if (voice) {
						speak.voice = voice;
					}

					speechSynthesis.speak(speak);
				}
			}, 100);
		} else {
			loadingSpeech = true;

			const messageContentParts: string[] = getMessageContentParts(
				message.content,
				$config?.audio?.tts?.split_on ?? 'punctuation'
			);

			if (!messageContentParts.length) {
				console.log('No content to speak');
				toast.info($i18n.t('No content to speak'));

				speaking = false;
				loadingSpeech = false;
				return;
			}

			console.debug('Prepared message content for TTS', messageContentParts);

			audioParts = messageContentParts.reduce(
				(acc, _sentence, idx) => {
					acc[idx] = null;
					return acc;
				},
				{} as typeof audioParts
			);

			let lastPlayedAudioPromise = Promise.resolve(); // Initialize a promise that resolves immediately

			if ($settings.audio?.tts?.engine === 'browser-kokoro') {
				if (!$TTSWorker) {
					await TTSWorker.set(
						new KokoroWorker({
							dtype: $settings.audio?.tts?.engineConfig?.dtype ?? 'fp32'
						})
					);

					await $TTSWorker.init();
				}

				for (const [idx, sentence] of messageContentParts.entries()) {
					const kokoroModelVoices = $settings?.audio?.tts?.modelVoices ?? {};
					const kokoroVoice =
						kokoroModelVoices[message.model] ||
						$settings?.audio?.tts?.voice ||
						$config?.audio?.tts?.voice;
					const blob = await $TTSWorker
						.generate({
							text: sentence,
							voice: kokoroVoice
						})
						.catch((error) => {
							console.error(error);
							toast.error(`${error}`);

							speaking = false;
							loadingSpeech = false;
						});

					if (blob) {
						const audio = new Audio(blob);
						audio.playbackRate = $settings.audio?.tts?.playbackRate ?? 1;

						audioParts[idx] = audio;
						loadingSpeech = false;
						lastPlayedAudioPromise = lastPlayedAudioPromise.then(() => playAudio(idx));
					}
				}
			} else {
				for (const [idx, sentence] of messageContentParts.entries()) {
					// Per-model voice override for OpenAI TTS
					const openaiModelVoices = $settings?.audio?.tts?.modelVoices ?? {};
					let openaiVoice: string;
					if (openaiModelVoices[message.model]) {
						openaiVoice = openaiModelVoices[message.model];
					} else if ($settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice) {
						openaiVoice = $settings?.audio?.tts?.voice ?? $config?.audio?.tts?.voice;
					} else {
						openaiVoice = $config?.audio?.tts?.voice;
					}
					const res = await synthesizeOpenAISpeech(localStorage.token, openaiVoice, sentence).catch(
						(error) => {
							console.error(error);
							toast.error(`${error}`);

							speaking = false;
							loadingSpeech = false;
						}
					);

					if (res) {
						const blob = await res.blob();
						const blobUrl = URL.createObjectURL(blob);
						const audio = new Audio(blobUrl);
						audio.playbackRate = $settings.audio?.tts?.playbackRate ?? 1;

						audioParts[idx] = audio;
						loadingSpeech = false;
						lastPlayedAudioPromise = lastPlayedAudioPromise.then(() => playAudio(idx));
					}
				}
			}
		}
	};

	let preprocessedDetailsCache = [];

	function preprocessForEditing(content: string): string {
		// Replace <details>...</details> with unique ID placeholder
		const detailsBlocks = [];
		let i = 0;

		content = content.replace(/<details[\s\S]*?<\/details>/gi, (match) => {
			detailsBlocks.push(match);
			return `<details id="__DETAIL_${i++}__"/>`;
		});

		// Store original blocks in the editedContent or globally (see merging later)
		preprocessedDetailsCache = detailsBlocks;

		return content;
	}

	function postprocessAfterEditing(content: string): string {
		const restoredContent = content.replace(
			/<details id="__DETAIL_(\d+)__"\/>/g,
			(_, index) => preprocessedDetailsCache[parseInt(index)] || ''
		);

		return restoredContent;
	}

	const editMessageHandler = async () => {
		edit = true;

		editedContent = preprocessForEditing(message.content);

		await tick();

		editTextAreaElement.style.height = '';
		editTextAreaElement.style.height = `${editTextAreaElement.scrollHeight}px`;
	};

	const editMessageConfirmHandler = async () => {
		const messageContent = postprocessAfterEditing(editedContent ? editedContent : '');
		editMessage(message.id, messageContent, false);

		edit = false;
		editedContent = '';

		await tick();
	};

	const saveAsCopyHandler = async () => {
		const messageContent = postprocessAfterEditing(editedContent ? editedContent : '');

		editMessage(message.id, messageContent);

		edit = false;
		editedContent = '';

		await tick();
	};

	const cancelEditMessage = async () => {
		edit = false;
		editedContent = '';
		await tick();
	};

	const generateImage = async (message: MessageType) => {
		generatingImage = true;
		const res = await imageGenerations(localStorage.token, message.content).catch((error) => {
			toast.error(`${error}`);
		});
		console.log(res);

		if (res) {
			const files = res.map((image) => ({
				type: 'image',
				url: `${image.url}`
			}));

			saveMessage(message.id, {
				...message,
				files: files
			});
		}

		generatingImage = false;
	};

	const deleteMessageHandler = async () => {
		deleteMessage(message.id);
	};

	$: if (!edit) {
		(async () => {
			await tick();
		})();
	}

	// Token 用量 — 生成毛玻璃卡片 HTML（供 Tooltip 渲染）
	function formatUsageHtml(usage: unknown): string {
		if (!usage || typeof usage !== 'object') return '';

		const data = usage as Record<string, unknown>;
		const input = data.prompt_tokens ?? data.input_tokens;
		const output = data.completion_tokens ?? data.output_tokens;
		const total = data.total_tokens;
		const compDetails = (data.completion_tokens_details ?? data.output_tokens_details) as Record<string, unknown> | null;
		const reasoning = compDetails?.reasoning_tokens;
		const promptDetails = (data.prompt_tokens_details ?? data.input_tokens_details) as Record<string, unknown> | null;
		const cached = promptDetails?.cached_tokens;

		const dk = document.documentElement.classList.contains('dark');
		const bg = dk ? 'rgba(30,32,42,0.88)' : 'rgba(255,255,255,0.88)';
		const bd = dk ? 'rgba(75,85,99,0.5)' : 'rgba(209,213,219,0.3)';
		const dv = dk ? 'rgba(75,85,99,0.4)' : 'rgba(229,231,235,0.6)';
		const lb = dk ? '#9ca3af' : '#6b7280';
		const vl = dk ? '#d1d5db' : '#374151';
		const hr = dk ? '#f3f4f6' : '#111827';
		const dm = dk ? '#4b5563' : '#d1d5db';

		const num = (v: unknown) => (typeof v === 'number' ? v.toLocaleString() : null);
		const rows: [string, string | null][] = [
			['输入 Token', num(input)],
			['输出 Token', num(output)],
			['推理 Token', num(reasoning)],
			['缓存 Token', num(cached)]
		];

		let h = `<div style="background:${bg};backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid ${bd};border-radius:1rem;padding:10px 14px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);min-width:150px">`;

		if (typeof total === 'number') {
			h += `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid ${dv}">`;
			h += `<span style="font-size:12px;font-weight:500;color:${lb}">总消耗</span>`;
			h += `<span style="font-size:18px;font-weight:600;font-variant-numeric:tabular-nums;color:${hr}">${total.toLocaleString()}</span>`;
			h += `</div>`;
		}

		h += `<div style="display:flex;flex-direction:column;gap:4px">`;
		for (const [label, val] of rows) {
			h += `<div style="display:flex;justify-content:space-between;align-items:center;font-size:12px">`;
			h += `<span style="color:${lb}">${label}</span>`;
			h += val !== null
				? `<span style="font-weight:500;font-variant-numeric:tabular-nums;color:${vl}">${val}</span>`
				: `<span style="color:${vl};font-style:italic">未返回</span>`;
			h += `</div>`;
		}
		h += `</div></div>`;

		return h;
	}

	function getStatsDisplay(
		msg: MessageType
	): { speed: string; tokens: string; elapsed: string } | null {
		if (!msg.done) return null;
		const usage = msg.usage as Record<string, unknown> | undefined;
		if (!usage) return null;

		let speed: string | null = null;
		let tokens: string | null = null;
		let elapsed: string | null = null;

		// — Tokens —
		const total = usage.total_tokens as number | undefined;
		const input = (usage.prompt_tokens ?? usage.input_tokens) as number | undefined;
		const output = (usage.completion_tokens ?? usage.output_tokens) as number | undefined;
		if (typeof total === 'number' && total > 0) {
			tokens = `${total}`;
		} else if (typeof input === 'number' && typeof output === 'number') {
			tokens = `${input + output}`;
		}

		// — Elapsed（Ollama: total_duration 纳秒 → 秒；否则用前端时间差）—
		if (typeof usage.total_duration === 'number' && (usage.total_duration as number) > 0) {
			elapsed = ((usage.total_duration as number) / 1e9).toFixed(2);
		} else if (msg.completedAt && msg.timestamp && msg.completedAt > msg.timestamp) {
			elapsed = (msg.completedAt - msg.timestamp).toFixed(2);
		}

		// — Speed（Ollama: response_token/s；否则 completion_tokens / elapsed）—
		if (typeof usage['response_token/s'] === 'number') {
			speed = (usage['response_token/s'] as number).toFixed(2);
		} else if (typeof output === 'number' && output > 0 && elapsed) {
			const sec = parseFloat(elapsed);
			if (sec > 0) speed = (output / sec).toFixed(2);
		}

		if (!speed && !tokens && !elapsed) return null;
		return { speed: speed ?? '', tokens: tokens ?? '', elapsed: elapsed ?? '' };
	}

	onMount(async () => {
		await tick();
		setupButtonsScroll();
	});
</script>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete message?')}
	on:confirm={() => {
		deleteMessageHandler();
	}}
/>

<!-- [REACTION_FEATURE] Commented out - reaction picker disabled for now
{#if showReactionPicker}
	<div class="fixed inset-0 z-40" on:click|stopPropagation={() => { showReactionPicker = false; }}></div>
	<div
		class="fixed z-50 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 p-1.5 flex gap-0.5"
		style="top: {reactionPickerPos.top - 44}px; left: {reactionPickerPos.left}px;"
	>
		{#each QUICK_REACTIONS as name}
			<button
				class="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition text-base leading-none"
				on:click|stopPropagation={() => {
					toggleReaction(name);
					showReactionPicker = false;
				}}
			>
				{#if $shortCodesToEmojis[name]}
					<img
						src="/assets/emojis/{$shortCodesToEmojis[name].toLowerCase()}.svg"
						alt={name}
						class="w-5 h-5"
					/>
				{:else}
					:{name}:
				{/if}
			</button>
		{/each}
	</div>
{/if}
-->

<DeleteConfirmDialog
	bind:show={showRegenerateConfirm}
	title={$i18n.t('Regenerate with {{modelName}}?', { modelName: model?.name ?? message.model })}
	on:confirm={() => {
		doRegenerate();
	}}
/>

{#key message.id}
	<div
		class=" flex w-full message-{message.id} group/message relative"
		id="message-{message.id}"
		dir={$settings.chatDirection}
	>
		<div
			class={`shrink-0 ml-0.5 sm:ml-0 ltr:mr-1.5 rtl:ml-1.5 ltr:sm:mr-3 rtl:sm:ml-3 relative z-10`}
		>
			<div class="relative">
					<ModelIcon
						src={model?.info?.meta?.profile_image_url ??
							model?.meta?.profile_image_url ??
							($i18n.language === 'dg-DG' ? `/doge.png` : `${WEBUI_BASE_URL}/static/favicon.png`)}
						alt="model profile"
						bare={true}
						className="size-[26px] sm:size-[34px] rounded-xl -translate-y-[1px] ring-2 ring-white/60 dark:ring-white/20"
					/>
				<!-- Status indicator dot -->
				<div
					class="absolute -bottom-0.5 -right-0.5 size-1.5 sm:size-2 translate-x-px -translate-y-px bg-green-400 rounded-full ring-1 sm:ring-[1.5px] ring-white dark:ring-gray-900 animate-pulse"
				/>
			</div>
		</div>

		<div class="flex-auto w-0 sm:pl-1 relative z-10">
			<Name>
				<Tooltip content={getModelChatDisplayName(model) || message.model} placement="top-start">
					<span class="line-clamp-1 text-black dark:text-white font-semibold">
						{getModelChatDisplayName(model) || message.model}
					</span>
				</Tooltip>

				{#if message.timestamp}
					<div
						class=" self-center text-xs invisible group-hover/message:visible text-gray-500 dark:text-gray-400 font-medium first-letter:capitalize ml-0.5 translate-y-[1px]"
					>
						<Tooltip content={dayjs(message.timestamp * 1000).format('LLLL')}>
							<span class="line-clamp-1">{formatDate(message.timestamp * 1000)}</span>
						</Tooltip>
					</div>
				{/if}

				{#if message.editCount}
					<div
						class="self-center text-xs invisible group-hover/message:visible text-gray-400 dark:text-gray-500 ml-1 translate-y-[1px]"
					>
						<Tooltip
							content={message.lastEditAt
								? $i18n.t('Last edited') + ': ' + dayjs(message.lastEditAt * 1000).format('LLLL')
								: ''}
						>
							<span class="italic"
								>({$i18n.t('edited')} {message.editCount > 1 ? `×${message.editCount}` : ''})</span
							>
						</Tooltip>
					</div>
				{/if}
			</Name>

			{#if stats && (stats.speed || stats.tokens || stats.elapsed)}
				<div class="text-gray-500 dark:text-gray-400 mt-1 ml-0.5 text-xs sm:text-sm">
					{#if stats.speed}速度: {stats.speed} T/s{/if}{#if stats.speed && (stats.tokens || stats.elapsed)}{' | '}{/if}{#if stats.tokens}消耗:
						{stats.tokens} Token{/if}{#if stats.tokens && stats.elapsed}{' | '}{/if}{#if stats.elapsed}耗时:
						{stats.elapsed} s{/if}
				</div>
			{/if}

			{#if message.instruction}
				<div
					class="flex items-baseline gap-1.5 mt-1 ml-0.5 text-xs text-gray-400 dark:text-gray-500 italic"
				>
					<RefreshCw class="w-3.5 h-3.5 shrink-0 translate-y-[1px]" strokeWidth={1.5} />
					<span class="line-clamp-1">{message.instruction.replace(/^请/, '')}</span>
				</div>
			{/if}

			<div class="mt-1.5 -ml-4 w-[calc(100%+1rem)] sm:ml-0 sm:w-auto">
				<div class="chat-{message.role} w-full min-w-full markdown-prose">
					<div>
						{#if message.content !== '' || message.error}
							<!-- Only show status section when content is streaming (not during initial loading) -->
							{#if displayStatusHistory.length > 0}
								{@const status = displayStatusHistory.at(-1)}
								{#if !status?.hidden}
									<div class="status-description flex items-center gap-2 py-0.5">
										{#if status?.done === false}
											<div class="">
												<Spinner className="size-4" />
											</div>
										{/if}

										{#if status?.action === 'web_search' && status?.urls}
											<WebSearchResults {status}>
												<div class="flex flex-col justify-center -space-y-0.5">
													<div
														class="{status?.done === false
															? 'shimmer'
															: ''} text-base line-clamp-1 text-wrap"
													>
														<!-- $i18n.t("Generating search query") -->
														<!-- $i18n.t("No search query generated") -->

														<!-- $i18n.t('Searched {{count}} sites') -->
														{#if status?.description.includes('{{count}}')}
															{$i18n.t(status?.description, {
																count: status?.count ?? status?.urls?.length ?? 0,
																failed: status?.failed ?? 0,
																searchQuery: status?.query
															})}
														{:else if status?.description === 'No search query generated'}
															{$i18n.t('No search query generated')}
														{:else if status?.description === 'Generating search query'}
															{$i18n.t('Generating search query')}
														{:else}
															{$i18n.t(status?.description, {
																count: status?.count,
																failed: status?.failed ?? 0,
																searchQuery: status?.query
															})}
														{/if}
													</div>
												</div>
											</WebSearchResults>
										{:else if status?.action === 'knowledge_search'}
											<div class="flex flex-col justify-center -space-y-0.5">
												<div
													class="{status?.done === false
														? 'shimmer'
														: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
												>
													{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
														searchQuery: status.query
													})}
												</div>
											</div>
										{:else if status?.action === 'native_tool_fallback'}
											<div
												class="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800/60"
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 20 20"
													fill="currentColor"
													class="size-4 text-amber-500 dark:text-amber-400 flex-shrink-0"
												>
													<path
														fill-rule="evenodd"
														d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 6zm0 9a1 1 0 100-2 1 1 0 000 2z"
														clip-rule="evenodd"
													/>
												</svg>
												<div class="text-amber-700 dark:text-amber-300 text-sm font-medium">
													{status?.description}
												</div>
											</div>
										{:else}
											<div class="flex flex-col justify-center -space-y-0.5">
												<div
													class="{status?.done === false
														? 'shimmer'
														: ''} text-gray-500 dark:text-gray-500 text-base line-clamp-1 text-wrap"
												>
													<!-- $i18n.t(`Searching "{{searchQuery}}"`) -->
													{#if status?.description.includes('{{searchQuery}}')}
														{$i18n.t(status?.description, {
															searchQuery: status?.query
														})}
													{:else if status?.description === 'No search query generated'}
														{$i18n.t('No search query generated')}
													{:else if status?.description === 'Generating search query'}
														{$i18n.t('Generating search query')}
													{:else}
														{$i18n.t(status?.description, {
															count: status?.count,
															failed: status?.failed ?? 0,
															searchQuery: status?.query
														})}
													{/if}
												</div>
											</div>
										{/if}
									</div>
								{/if}
							{/if}
						{/if}

						{#if message?.files && message.files?.filter((f) => f.type === 'image').length > 0}
							<div class="my-1 w-full flex overflow-x-auto gap-2 flex-wrap">
								{#each message.files as file}
									<div>
										{#if file.type === 'image'}
											<Image src={file.url} alt={message.content} />
										{:else}
											<FileItem
												item={file}
												url={file.url}
												name={file.name}
												type={file.type}
												size={file?.size}
												colorClassName="bg-white dark:bg-gray-850 "
											/>
										{/if}
									</div>
								{/each}
							</div>
						{/if}

						{#if edit === true}
							<div class="w-full bg-gray-50 dark:bg-gray-800 rounded-3xl px-5 py-3 my-2">
								<textarea
									id="message-edit-{message.id}"
									bind:this={editTextAreaElement}
									class=" bg-transparent outline-hidden w-full resize-none"
									bind:value={editedContent}
									on:input={(e) => {
										e.target.style.height = '';
										e.target.style.height = `${e.target.scrollHeight}px`;
									}}
									on:keydown={(e) => {
										if (e.key === 'Escape') {
											document.getElementById('close-edit-message-button')?.click();
										}

										const isCmdOrCtrlPressed = e.metaKey || e.ctrlKey;
										const isEnterPressed = e.key === 'Enter';

										if (isCmdOrCtrlPressed && isEnterPressed) {
											document.getElementById('confirm-edit-message-button')?.click();
										}
									}}
								/>

								<div class=" mt-2 mb-1 flex justify-between text-sm font-medium">
									<div>
										<button
											id="save-new-message-button"
											class=" px-4 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-800 dark:hover:bg-gray-700 border border-gray-100 dark:border-gray-700 text-gray-700 dark:text-gray-200 transition rounded-3xl"
											on:click={() => {
												saveAsCopyHandler();
											}}
										>
											{$i18n.t('Save As Copy')}
										</button>
									</div>

									<div class="flex space-x-1.5">
										<button
											id="close-edit-message-button"
											class="px-4 py-2 bg-white dark:bg-gray-900 hover:bg-gray-100 text-gray-800 dark:text-gray-100 transition rounded-3xl"
											on:click={() => {
												cancelEditMessage();
											}}
										>
											{$i18n.t('Cancel')}
										</button>

										<button
											id="confirm-edit-message-button"
											class=" px-4 py-2 bg-gray-900 dark:bg-white hover:bg-gray-850 text-gray-100 dark:text-gray-800 transition rounded-3xl"
											on:click={() => {
												editMessageConfirmHandler();
											}}
										>
											{$i18n.t('Save')}
										</button>
									</div>
								</div>
							</div>
						{:else}
							<div
								class="relative min-w-0 flex-1 overflow-visible message-outline-host {canShowMessageOutline
									? 'message-outline-host-active'
									: ''}"
							>
								{#if canShowMessageOutline}
									<MessageOutline
										headings={messageHeadings}
										onSelect={(heading) => {
											contentRendererRef?.scrollToHeading?.(heading.id);
										}}
									/>
								{/if}

								<div class="message-outline-body">
									<div
										class="w-full flex flex-col relative {!message.done ? 'streaming-fade' : ''}"
										id="response-content-container"
									>
										{#if !message.done && !message.error && !hasVisibleAssistantOutput && !hasVisibleThinkingOutput}
											<!-- Keep the waiting indicator visible even before backend status steps arrive -->
											<ThinkingIndicator
												statusHistory={displayStatusHistory}
												messageTimestamp={message.timestamp}
											/>
										{/if}

										{#if message.content === '' && message.done && !message.error && !(message?.files?.length > 0)}
											<!-- Empty response: model returned 0 tokens without error -->
											<Error
												content={$i18n.t(
													'Model returned an empty response. Try resending or switching models.'
												)}
											/>
										{:else if message.content && message.error !== true}
											<!-- always show message contents even if there's an error -->
											<!-- unless message.error === true which is legacy error handling, where the error message is stored in message.content -->
											<ContentRenderer
												bind:this={contentRendererRef}
												bind:headings={messageHeadings}
												id={message.id}
												{history}
												content={message.content}
												streaming={!message.done}
												{isLastMessage}
												sources={message.sources}
												floatingButtons={message?.done &&
													!readOnly &&
													($settings?.showFloatingActionButtons ?? true)}
												actions={$settings?.floatingActionButtons ?? []}
												save={!readOnly}
												{model}
												onTaskClick={async (e) => {
													console.log(e);
												}}
												onSourceClick={async (_id, idx) => {
													citationsRef?.openCitationByIndex?.(idx);
												}}
												onAddMessages={({ modelId, parentId, messages }) => {
													addMessages({ modelId, parentId, messages });
												}}
												on:update={(e) => {
													const { raw, oldContent, newContent } = e.detail;

													history.messages[message.id].content = history.messages[
														message.id
													].content.replace(raw, raw.replace(oldContent, newContent));

													updateChat();
												}}
												on:select={(e) => {
													const { type, content } = e.detail;

													if (type === 'explain') {
														submitMessage(
															message.id,
															`Explain this section to me in more detail\n\n\`\`\`\n${content}\n\`\`\``
														);
													} else if (type === 'ask') {
														const input = e.detail?.input ?? '';
														submitMessage(
															message.id,
															`\`\`\`\n${content}\n\`\`\`\n${input}`
														);
													}
												}}
											/>
										{/if}

										{#if message?.error}
											<Error
												content={message?.error === true ? message.content : message?.error}
											/>
										{/if}

										{#if message.code_executions}
											<CodeExecutions codeExecutions={message.code_executions} />
										{/if}
									</div>

									<div class="message-outline-toolbar-row flex items-end mt-2 gap-3 flex-wrap">
						{#if (message?.sources || message?.citations) && (model?.info?.meta?.capabilities?.citations ?? true)}
							<div class="flex-shrink-0">
								<Citations
									bind:this={citationsRef}
									id={message?.id}
									sources={message?.sources ?? message?.citations}
								/>
							</div>
						{/if}
						{#if message.done || siblings.length > 1}
							<div
								bind:this={buttonsContainerElement}
								class="flex items-center gap-0.5 overflow-x-auto buttons text-gray-600 dark:text-gray-300 px-1.5 h-[37px] rounded-xl {isLastMessage
									? 'visible opacity-100'
									: 'invisible group-hover/message:visible opacity-0 group-hover/message:opacity-100'} transition-all duration-300 bg-white/60 dark:bg-gray-800/60 backdrop-blur-xl shadow-sm border border-gray-200/50 dark:border-gray-700/50 w-fit min-w-0 max-w-full toolbar-appear"
							>
								{#if siblings.length > 1}
									<div class="flex self-center min-w-fit" dir="ltr">
										<button
											class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-lg transition-all duration-200 hover:scale-110 active:scale-95"
											on:click={() => {
												showPreviousMessage(message);
											}}
										>
											<ChevronLeft class="size-3.5" strokeWidth={2.5} />
										</button>

										{#if messageIndexEdit}
											<div
												class="text-sm flex justify-center font-semibold self-center dark:text-gray-100 min-w-fit"
											>
												<input
													id="message-index-input-{message.id}"
													type="number"
													value={siblings.indexOf(message.id) + 1}
													min="1"
													max={siblings.length}
													on:focus={(e) => {
														e.target.select();
													}}
													on:blur={(e) => {
														gotoMessage(message, e.target.value - 1);
														messageIndexEdit = false;
													}}
													on:keydown={(e) => {
														if (e.key === 'Enter') {
															gotoMessage(message, e.target.value - 1);
															messageIndexEdit = false;
														}
													}}
													class="bg-transparent font-semibold self-center dark:text-gray-100 min-w-fit outline-hidden"
												/>/{siblings.length}
											</div>
										{:else}
											<!-- svelte-ignore a11y-no-static-element-interactions -->
											<div
												class="text-xs tracking-wider font-medium self-center text-gray-500 dark:text-gray-300 min-w-fit tabular-nums"
												on:dblclick={async () => {
													messageIndexEdit = true;

													await tick();
													const input = document.getElementById(
														`message-index-input-${message.id}`
													);
													if (input) {
														input.focus();
														input.select();
													}
												}}
											>
												{siblings.indexOf(message.id) + 1}/{siblings.length}
											</div>
										{/if}

										<button
											class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-lg transition-all duration-200 hover:scale-110 active:scale-95"
											on:click={() => {
												showNextMessage(message);
											}}
										>
											<ChevronRight class="size-3.5" strokeWidth={2.5} />
										</button>
									</div>
									{#if message.done}
										<div
											class="w-px h-4 bg-gray-300/40 dark:bg-gray-600/40 mx-0.5 self-center"
										></div>
									{/if}
								{/if}

								{#if message.done}
									{#if !readOnly}
										{#if $user?.role === 'user' ? ($user?.permissions?.chat?.edit ?? true) : true}
											<Tooltip content={$i18n.t('Edit')} placement="bottom">
												<button
													class="{isLastMessage
														? 'visible'
														: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
													on:click={() => {
														editMessageHandler();
													}}
												>
													<PencilLine class="w-4 h-4" strokeWidth={2} />
												</button>
											</Tooltip>
										{/if}
									{/if}

									<Tooltip content={$i18n.t('Copy')} placement="bottom">
										<button
											class="{isLastMessage
												? 'visible'
												: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 copy-response-button"
											on:click={() => {
												copyToClipboard(message.content);
											}}
										>
											<Copy class="w-4 h-4" strokeWidth={2} />
										</button>
									</Tooltip>

									<!-- [REACTION_FEATURE] Commented out - reaction button disabled for now
								{#if !readOnly}
									<div>
										<Tooltip content={$i18n.t('React')} placement="bottom">
											<button
												bind:this={reactionBtnEl}
												class="{isLastMessage
													? 'visible'
													: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
												on:click={() => {
													if (!showReactionPicker && reactionBtnEl) {
														const rect = reactionBtnEl.getBoundingClientRect();
														reactionPickerPos = { top: rect.top, left: rect.left };
													}
													showReactionPicker = !showReactionPicker;
												}}
											>
												<SmilePlus class="w-4 h-4" strokeWidth={2} />
											</button>
										</Tooltip>
									</div>
								{/if}
								-->

									{#if $user?.role === 'admin' || ($user?.permissions?.chat?.tts ?? true)}
										<Tooltip content={$i18n.t('Read Aloud')} placement="bottom">
											<button
												id="speak-button-{message.id}"
												class="{isLastMessage
													? 'visible'
													: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
												on:click={() => {
													if (!loadingSpeech) {
														toggleSpeakMessage();
													}
												}}
											>
												{#if loadingSpeech}
													<svg
														class=" w-4 h-4"
														fill="currentColor"
														viewBox="0 0 24 24"
														xmlns="http://www.w3.org/2000/svg"
													>
														<style>
															.spinner_S1WN {
																animation: spinner_MGfb 0.8s linear infinite;
																animation-delay: -0.8s;
															}

															.spinner_Km9P {
																animation-delay: -0.65s;
															}

															.spinner_JApP {
																animation-delay: -0.5s;
															}

															@keyframes spinner_MGfb {
																93.75%,
																100% {
																	opacity: 0.2;
																}
															}
														</style>
														<circle class="spinner_S1WN" cx="4" cy="12" r="3" />
														<circle class="spinner_S1WN spinner_Km9P" cx="12" cy="12" r="3" />
														<circle class="spinner_S1WN spinner_JApP" cx="20" cy="12" r="3" />
													</svg>
												{:else if speaking}
													<VolumeX class="w-4 h-4" strokeWidth={2} />
												{:else}
													<Volume2 class="w-4 h-4" strokeWidth={2} />
												{/if}
											</button>
										</Tooltip>
									{/if}

									{#if $config?.features.enable_image_generation && ($user?.role === 'admin' || $user?.permissions?.features?.image_generation) && !readOnly}
										<Tooltip content={$i18n.t('Generate Image')} placement="bottom">
											<button
												class="{isLastMessage
													? 'visible'
													: 'invisible group-hover:visible'}  p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
												on:click={() => {
													if (!generatingImage) {
														generateImage(message);
													}
												}}
											>
												{#if generatingImage}
													<svg
														class=" w-4 h-4"
														fill="currentColor"
														viewBox="0 0 24 24"
														xmlns="http://www.w3.org/2000/svg"
													>
														<style>
															.spinner_S1WN {
																animation: spinner_MGfb 0.8s linear infinite;
																animation-delay: -0.8s;
															}

															.spinner_Km9P {
																animation-delay: -0.65s;
															}

															.spinner_JApP {
																animation-delay: -0.5s;
															}

															@keyframes spinner_MGfb {
																93.75%,
																100% {
																	opacity: 0.2;
																}
															}
														</style>
														<circle class="spinner_S1WN" cx="4" cy="12" r="3" />
														<circle class="spinner_S1WN spinner_Km9P" cx="12" cy="12" r="3" />
														<circle class="spinner_S1WN spinner_JApP" cx="20" cy="12" r="3" />
													</svg>
												{:else}
													<ImagePlus class="w-4 h-4" strokeWidth={2} />
												{/if}
											</button>
										</Tooltip>
									{/if}

									{#if message.usage}
										<Tooltip
											content={formatUsageHtml(message.usage)}
											placement="bottom"
											offset={[0, 8]}
											tippyOptions={{
												theme: 'none',
												maxWidth: 'none',
												duration: [100, 75],
												onShow(instance) {
													const box = instance.popper.firstElementChild;
													if (box) {
														box.style.background = 'transparent';
														box.style.border = 'none';
														box.style.boxShadow = 'none';
														box.style.borderRadius = '0';
													}
													const tc = box?.querySelector('.tippy-content');
													if (tc) tc.style.padding = '0';
												}
											}}
										>
											<button
												class="{isLastMessage
													? 'visible'
													: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
												id="info-{message.id}"
											>
												<Info class="w-4 h-4" strokeWidth={2} />
											</button>
										</Tooltip>
									{/if}

									<div class="w-px h-4 bg-gray-300/40 dark:bg-gray-600/40 mx-0.5 self-center"></div>

									{#if !readOnly}
										{#if isLastMessage}
											<Tooltip content={$i18n.t('Continue Response')} placement="bottom">
												<button
													type="button"
													id="continue-response-button"
													class="{isLastMessage
														? 'visible'
														: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 regenerate-response-button"
													on:click={() => {
														continueResponse();
													}}
												>
													<PlayCircle class="w-4 h-4" strokeWidth={2} />
												</button>
											</Tooltip>
										{/if}

										{#if $settings?.regenerateMenu ?? true}
											<Dropdown
												bind:show={showRegenerateMenu}
												side="top"
												align="start"
												on:change={(e) => {
													if (!e.detail) regenerateInput = '';
												}}
											>
												<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
													<button
														type="button"
														class="{isLastMessage
															? 'visible'
															: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 regenerate-response-button"
														aria-label={$i18n.t('Regenerate')}
													>
														<RefreshCw class="w-4 h-4" strokeWidth={2} />
													</button>
												</Tooltip>

												<div slot="content">
													<DropdownMenu.Content
														class="w-60 rounded-2xl px-1.5 py-1.5 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
														sideOffset={8}
														side="top"
														align="start"
														transition={flyAndScale}
													>
														<!-- 自定义指令输入框 -->
														<div class="px-1.5 py-1.5">
															<form
																class="flex items-center gap-2 px-3 py-2 bg-gray-50 dark:bg-gray-800 rounded-xl"
																on:submit|preventDefault={() => {
																	if (regenerateInput.trim()) {
																		showRegenerateMenu = false;
																		regenerateResponse(message, {
																			instruction: regenerateInput.trim()
																		});
																		regenerateInput = '';
																	}
																}}
															>
																<input
																	type="text"
																	bind:value={regenerateInput}
																	placeholder={$i18n.t('Request changes to reply...')}
																	class="w-full bg-transparent text-sm outline-none placeholder:text-gray-400 dark:placeholder:text-gray-500"
																/>
																<button
																	type="submit"
																	class="p-1 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors {regenerateInput.trim()
																		? 'opacity-100'
																		: 'opacity-30 pointer-events-none'}"
																	disabled={!regenerateInput.trim()}
																>
																	<ArrowRight class="w-4 h-4" />
																</button>
															</form>
														</div>

														<hr class="border-black/5 dark:border-white/5 my-0.5" />

														<!-- 重试 -->
														<DropdownMenu.Item
															class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-xl cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
															on:click={() => {
																showRegenerateMenu = false;
																doRegenerate();
															}}
														>
															<RefreshCw class="w-4 h-4 shrink-0" strokeWidth={1.75} />
															<span>{$i18n.t('Retry')}</span>
														</DropdownMenu.Item>

														<!-- 添加详细信息 -->
														<DropdownMenu.Item
															class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-xl cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
															on:click={() => {
																showRegenerateMenu = false;
																regenerateResponse(message, {
																	instruction: $i18n.t('Please provide a more detailed response')
																});
															}}
														>
															<ListPlus class="w-4 h-4 shrink-0" strokeWidth={1.75} />
															<span>{$i18n.t('Add more detail')}</span>
														</DropdownMenu.Item>

														<!-- 更加简洁 -->
														<DropdownMenu.Item
															class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-xl cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
															on:click={() => {
																showRegenerateMenu = false;
																regenerateResponse(message, {
																	instruction: $i18n.t('Please respond more concisely')
																});
															}}
														>
															<AlignLeft class="w-4 h-4 shrink-0" strokeWidth={1.75} />
															<span>{$i18n.t('More concise')}</span>
														</DropdownMenu.Item>

														<!-- 思考时间更长（条件显示） -->
														{#if modelSupportsThinking}
															<DropdownMenu.Item
																class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-xl cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
																on:click={() => {
																	showRegenerateMenu = false;
																	regenerateResponse(message, { reasoningEffort: 'high' });
																}}
															>
																<Lightbulb class="w-4 h-4 shrink-0" strokeWidth={1.75} />
																<span>{$i18n.t('Think longer')}</span>
															</DropdownMenu.Item>
														{/if}

														<!-- 搜索网页（条件显示） -->
														{#if $config?.features?.enable_web_search}
															<DropdownMenu.Item
																class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-xl cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
																on:click={() => {
																	showRegenerateMenu = false;
																	regenerateResponse(message, { webSearch: true });
																}}
															>
																<Globe class="w-4 h-4 shrink-0" strokeWidth={1.75} />
																<span>{$i18n.t('Search the web')}</span>
															</DropdownMenu.Item>
														{/if}
													</DropdownMenu.Content>
												</div>
											</Dropdown>
										{:else}
											<Tooltip content={$i18n.t('Regenerate')} placement="bottom">
												<button
													type="button"
													class="{isLastMessage
														? 'visible'
														: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 regenerate-response-button"
													on:click={() => {
														doRegenerate();
													}}
												>
													<RefreshCw class="w-4 h-4" strokeWidth={2} />
												</button>
											</Tooltip>
										{/if}

										<Tooltip content={$i18n.t('Delete')} placement="bottom">
											<button
												type="button"
												id="delete-response-button"
												class="{isLastMessage
													? 'visible'
													: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 regenerate-response-button"
												on:click={() => {
													showDeleteConfirm = true;
												}}
											>
												<Trash2 class="w-4 h-4" strokeWidth={2} />
											</button>
										</Tooltip>

										{#if isLastMessage}
											{#each model?.actions ?? [] as action}
												<Tooltip content={action.name} placement="bottom">
													<button
														type="button"
														class="{isLastMessage
															? 'visible'
															: 'invisible group-hover:visible'} p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
														on:click={() => {
															actionMessage(action.id, message);
														}}
													>
														{#if action.icon_url}
															<div class="size-4">
																<img
																	src={action.icon_url}
																	class="w-4 h-4 {action.icon_url.includes('svg')
																		? 'dark:invert-[80%]'
																		: ''}"
																	style="fill: currentColor;"
																	alt={action.name}
																/>
															</div>
														{:else}
															<Sparkles strokeWidth="2.1" className="size-4" />
														{/if}
													</button>
												</Tooltip>
											{/each}
										{/if}
									{/if}
								{/if}
							</div>
						{/if}
					</div>

					<!-- [REACTION_FEATURE] Commented out - reaction display disabled for now
					{#if reactions.length > 0}
						<div class="flex flex-wrap gap-1.5 mt-1.5">
							{#each reactions as reaction}
								<button
									class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border transition-colors {reaction.user_ids.includes($user?.id)
										? 'bg-blue-50 dark:bg-blue-900/20 border-blue-300 dark:border-blue-700'
										: 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-750'}"
									on:click={() => toggleReaction(reaction.name)}
								>
									{#if $shortCodesToEmojis[reaction.name]}
										<img
											src="/assets/emojis/{$shortCodesToEmojis[reaction.name].toLowerCase()}.svg"
											alt={reaction.name}
											class="w-3.5 h-3.5"
										/>
									{:else}
										:{reaction.name}:
									{/if}
									<span class="text-gray-500 dark:text-gray-400 tabular-nums">{reaction.count}</span>
								</button>
							{/each}
						</div>
					{/if}
					-->

					{#if (isLastMessage || ($settings?.keepFollowUpPrompts ?? false)) && message.done && !readOnly && (message?.followUps ?? []).length > 0}
						<div class="mt-2 flex flex-wrap gap-2">
							{#each message.followUps as followUp}
								<button
									type="button"
									class="text-xs px-2.5 py-1.5 rounded-full border border-gray-200 dark:border-gray-700 hover:bg-black/5 dark:hover:bg-white/5 transition"
									on:click={() => {
										if ($settings?.insertFollowUpPrompt ?? false) {
											setInputText(followUp);
										} else {
											submitMessage(message?.id, followUp);
										}
									}}
								>
									{followUp}
								</button>
							{/each}
						</div>
					{/if}
								</div>
							</div>
						{/if}
					</div>
				</div>
			</div>
		</div>
	</div>
	{/key}

<style>
	.message-outline-host {
		--message-outline-gutter: 0px;
	}

	.message-outline-host-active {
		--message-outline-gutter: 24px;
	}

	.message-outline-body {
		display: flex;
		flex-direction: column;
		min-width: 0;
		padding-inline-start: var(--message-outline-gutter);
		transition: padding-inline-start 140ms ease;
	}

	.buttons::-webkit-scrollbar {
		display: none; /* for Chrome, Safari and Opera */
	}

	.buttons {
		-ms-overflow-style: none; /* IE and Edge */
		scrollbar-width: none; /* Firefox */
	}

	.buttons button {
		position: relative;
	}

	.buttons button::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: inherit;
		opacity: 0;
		transition: opacity 0.2s ease;
		background: radial-gradient(circle at center, currentColor 0%, transparent 70%);
		pointer-events: none;
	}

	.buttons button:hover::after {
		opacity: 0.04;
	}

	.toolbar-appear {
		animation: toolbar-fade-in 0.3s ease-out both;
	}

	@keyframes toolbar-fade-in {
		from {
			opacity: 0;
			transform: translateY(4px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}
</style>
