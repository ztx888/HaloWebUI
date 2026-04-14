<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';
	import { createPicker, getAuthToken } from '$lib/utils/google-drive-picker';
	import { pickAndDownloadFile } from '$lib/utils/onedrive-file-picker';

	import { onMount, tick, getContext, createEventDispatcher, onDestroy, afterUpdate } from 'svelte';
	const dispatch = createEventDispatcher();

	import {
		type Model,
		mobile,
		settings,
		showSidebar,
		models,
		config,
		showCallOverlay,
		tools,
		user as _user,
		showControls,
		TTSWorker
	} from '$lib/stores';

	import {
		blobToFile,
		compressImage,
		convertHeicToJpeg,
		createMessagesList,
		extractInputVariables,
		extractCurlyBraceWords,
		getAge,
		getCurrentDateTime,
		getFormattedDate,
		getFormattedTime,
		getUserPosition,
		getUserTimezone,
		getWeekday,
		isAnimatedImage,
		isHeicFile
	} from '$lib/utils';
	import {
		getFileUploadDiagnostic,
		getLocalizedFileUploadDiagnostic,
		localizeFileUploadError
	} from '$lib/utils/file-upload-errors';
	import { transcribeAudio } from '$lib/apis/audio';
	import { uploadFile } from '$lib/apis/files';
	import { generateAutoCompletion } from '$lib/apis';
	import { deleteFileById } from '$lib/apis/files';
	import { getSessionUser } from '$lib/apis/auths';

	import { WEBUI_BASE_URL, WEBUI_API_BASE_URL, PASTED_TEXT_CHARACTER_LIMIT } from '$lib/constants';

	import InputMenu from './MessageInput/InputMenu.svelte';
	import ImageContextPanel from './MessageInput/ImageContextPanel.svelte';
	import VoiceRecording from './MessageInput/VoiceRecording.svelte';
	import FilesOverlay from './MessageInput/FilesOverlay.svelte';
	import Commands from './MessageInput/Commands.svelte';
	import InputVariablesModal from './MessageInput/InputVariablesModal.svelte';
	import ThinkingControl from './MessageInput/ThinkingControl.svelte';
	import SendMenu from './MessageInput/SendMenu.svelte';
	import CommandSuggestionList from './MessageInput/CommandSuggestionList.svelte';

	import RichTextInput from '../common/RichTextInput.svelte';
	import { getSuggestionRenderer } from '../common/RichTextInput/suggestions';
	import Tooltip from '../common/Tooltip.svelte';
	import FileItem from '../common/FileItem.svelte';
	import Image from '../common/Image.svelte';
	import ModelIcon from '../common/ModelIcon.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import type { ChatAssistantSnapshot } from '$lib/utils/chat-assistants';
	import {
		isWebSearchEnabled,
		normalizeWebSearchMode,
		type WebSearchMode
	} from '$lib/utils/web-search-mode';
	import {
		buildWebSearchModeOptions
	} from '$lib/utils/native-web-search';

	import XMark from '../icons/XMark.svelte';
	import Headphone from '../icons/Headphone.svelte';
	import GlobeAlt from '../icons/GlobeAlt.svelte';
	import Photo from '../icons/Photo.svelte';
	import CommandLine from '../icons/CommandLine.svelte';
	import { KokoroWorker } from '$lib/workers/KokoroWorker';
	import ToolServersModal from './ToolServersModal.svelte';
	import Wrench from '../icons/Wrench.svelte';

	const i18n = getContext('i18n');

	export let transparentBackground = false;

	export let onChange: Function = () => {};
	export let createMessagePair: Function;
	export let stopResponse: Function;

	export let autoScroll = false;

	export let atSelectedModel: Model | undefined = undefined;
	export let selectedModels: [''];
	export let activeAssistant: ChatAssistantSnapshot | null = null;
	export let onDeactivateAssistant: (() => void) | null = null;

	let selectedModelIds = [];
	$: selectedModelIds = atSelectedModel !== undefined ? [atSelectedModel.id] : selectedModels;

	export let history;
	export let taskIds = null;

	export let prompt = '';
	export let files = [];

	export let toolServers = [];

	export let selectedToolIds = [];

	export let imageGenerationEnabled = false;
	export let imageGenerationOptions: {
		image_size?: string | null;
		aspect_ratio?: string | null;
		n?: number | null;
	} = {};
	export let webSearchMode: WebSearchMode = 'off';
	export let codeInterpreterEnabled = false;

	export let reasoningEffort: string | null = null;
	export let maxThinkingTokens: number | null = null;

	$: onChange({
		prompt,
		files,
		selectedToolIds,
		imageGenerationEnabled,
		imageGenerationOptions,
		webSearchMode,
		reasoningEffort,
		maxThinkingTokens
	});

	let suggestions = null;
	let command = '';
	let showInputVariablesModal = false;
	let inputVariables = {};
	let inputVariableValues = {};
	let inputVariablesModalCallback = (_variableValues) => {};

	const replaceVariablesInPlainText = (variables: Record<string, any>) => {
		prompt = prompt.replace(/{{\s*([^|}]+)(?:\|[^}]*)?\s*}}/g, (match, varName) => {
			const trimmedVarName = varName.trim();
			return Object.prototype.hasOwnProperty.call(variables, trimmedVarName)
				? String(variables[trimmedVarName])
				: match;
		});
	};

	const inputVariableHandler = async (text: string): Promise<string> => {
		inputVariables = extractInputVariables(text);

		if (Object.keys(inputVariables).length === 0) {
			return text;
		}

		showInputVariablesModal = true;
		return await new Promise<string>((resolve) => {
			inputVariablesModalCallback = (variableValues) => {
				inputVariableValues = { ...inputVariableValues, ...variableValues };
				if (typeof chatInputElement?.replaceVariables === 'function') {
					chatInputElement.replaceVariables(inputVariableValues);
				} else {
					replaceVariablesInPlainText(inputVariableValues);
				}
				showInputVariablesModal = false;
				resolve(text);
			};
		});
	};

	const textVariableHandler = async (text: string) => {
		if (text.includes('{{CLIPBOARD}}')) {
			const clipboardText = await navigator.clipboard.readText().catch(() => {
				toast.error($i18n.t('Failed to read clipboard contents'));
				return '{{CLIPBOARD}}';
			});

			const clipboardItems = await navigator.clipboard.read().catch((error) => {
				console.error('Failed to read clipboard items:', error);
				return [];
			});

			for (const item of clipboardItems) {
				for (const type of item.types) {
					if (type.startsWith('image/')) {
						const blob = await item.getType(type);
						const file = new File([blob], `clipboard-image.${type.split('/')[1]}`, {
							type
						});
						await inputFilesHandler([file]);
					}
				}
			}

			text = text.replaceAll('{{CLIPBOARD}}', clipboardText.replaceAll('\r\n', '\n'));
		}

		if (text.includes('{{USER_LOCATION}}')) {
			let location;
			try {
				location = await getUserPosition();
			} catch {
				toast.error($i18n.t('Location access not allowed'));
				location = 'LOCATION_UNKNOWN';
			}
			text = text.replaceAll('{{USER_LOCATION}}', String(location));
		}

		const sessionUser = await getSessionUser(localStorage.token).catch(() => null);

		if (text.includes('{{USER_NAME}}')) {
			text = text.replaceAll('{{USER_NAME}}', sessionUser?.name || 'User');
		}

		if (text.includes('{{USER_EMAIL}}') && sessionUser?.email) {
			text = text.replaceAll('{{USER_EMAIL}}', sessionUser.email);
		}

		if (text.includes('{{USER_BIO}}') && sessionUser?.bio) {
			text = text.replaceAll('{{USER_BIO}}', sessionUser.bio);
		}

		if (text.includes('{{USER_GENDER}}') && sessionUser?.gender) {
			text = text.replaceAll('{{USER_GENDER}}', sessionUser.gender);
		}

		if (text.includes('{{USER_BIRTH_DATE}}') && sessionUser?.date_of_birth) {
			text = text.replaceAll('{{USER_BIRTH_DATE}}', sessionUser.date_of_birth);
		}

		if (text.includes('{{USER_AGE}}') && sessionUser?.date_of_birth) {
			text = text.replaceAll('{{USER_AGE}}', getAge(sessionUser.date_of_birth));
		}

		if (text.includes('{{USER_LANGUAGE}}')) {
			text = text.replaceAll('{{USER_LANGUAGE}}', localStorage.getItem('locale') || 'en-US');
		}

		if (text.includes('{{CURRENT_DATE}}')) {
			text = text.replaceAll('{{CURRENT_DATE}}', getFormattedDate());
		}

		if (text.includes('{{CURRENT_TIME}}')) {
			text = text.replaceAll('{{CURRENT_TIME}}', getFormattedTime());
		}

		if (text.includes('{{CURRENT_DATETIME}}')) {
			text = text.replaceAll('{{CURRENT_DATETIME}}', getCurrentDateTime());
		}

		if (text.includes('{{CURRENT_TIMEZONE}}')) {
			text = text.replaceAll('{{CURRENT_TIMEZONE}}', getUserTimezone());
		}

		if (text.includes('{{CURRENT_WEEKDAY}}')) {
			text = text.replaceAll('{{CURRENT_WEEKDAY}}', getWeekday());
		}

		return text;
	};

	$: normalizedWebSearchMode = normalizeWebSearchMode(webSearchMode, 'off');
	$: webSearchActive = isWebSearchEnabled(normalizedWebSearchMode);
	$: webSearchFeatureEnabled =
		Boolean($config?.features?.enable_halo_web_search ?? $config?.features?.enable_web_search) ||
		Boolean($config?.features?.enable_native_web_search);
	$: selectedModelLookupIds = selectedModelIds.filter((id) => typeof id === 'string' && id.trim() !== '');
	$: selectedModelObjects = selectedModelIds
		.map((id) =>
			atSelectedModel && atSelectedModel.id === id ? atSelectedModel : $models.find((model) => model.id === id)
		)
		.filter(Boolean);
	$: hasResolvedSelectedModels =
		selectedModelLookupIds.length === 0 || selectedModelObjects.length === selectedModelLookupIds.length;
	$: primarySelectedModel =
		atSelectedModel ??
		(selectedModelObjects.length === 1 ? selectedModelObjects[0] : null);
	$: webSearchModeOptions = buildWebSearchModeOptions(
		(key, options) => $i18n.t(key, options),
		$config,
		hasResolvedSelectedModels ? selectedModelObjects : []
	);
	$: currentWebSearchOption =
		webSearchModeOptions.find((option) => option.value === normalizedWebSearchMode) ?? null;
	$: currentWebSearchModeLabel = currentWebSearchOption?.label ?? $i18n.t('Off');
	$: currentWebSearchBadgeLabel =
		normalizedWebSearchMode === 'native'
			? $i18n.t('Model Built-in')
			: normalizedWebSearchMode === 'auto'
				? $i18n.t('Smart')
				: currentWebSearchModeLabel;
	$: fallbackWebSearchMode =
		(['auto', 'halo', 'native', 'off'] as WebSearchMode[]).find((mode) =>
			webSearchModeOptions.some((option) => option.value === mode && !option.disabled)
		) ?? ('off' as WebSearchMode);

	const syncWebSearchModeWithOptions = () => {
		const normalizedMode = normalizeWebSearchMode(webSearchMode, 'off');
		if (
			hasResolvedSelectedModels &&
			!webSearchModeOptions.some(
				(option) => option.value === normalizedMode && option.disabled !== true
			) &&
			webSearchMode !== fallbackWebSearchMode
		) {
			webSearchMode = fallbackWebSearchMode;
		}
	};

	afterUpdate(() => {
		syncWebSearchModeWithOptions();
	});

	$: currentWebSearchTooltip = (() => {
		switch (normalizedWebSearchMode) {
			case 'auto':
				return $i18n.t('智能联网搜索已开启');
			case 'native':
				return $i18n.t('模型原生联网搜索已开启');
			case 'halo':
				return $i18n.t('HaloWebUI 联网搜索已开启');
			default:
				return '';
		}
	})();

	const featureBadgeBaseClass =
		'group shrink-0 rounded-full flex items-center border transition-colors duration-200 cursor-pointer bg-sky-50/90 hover:bg-sky-100/85 dark:bg-slate-800/70 dark:hover:bg-slate-800/90 border-sky-200/60 dark:border-sky-500/20';
	const webSearchBadgeClass = `${featureBadgeBaseClass} px-2.5 py-1.5 gap-1.5`;
	const compactFeatureBadgeClass = `${featureBadgeBaseClass} px-1.5 py-1.5 gap-1`;
	const featureBadgeLabelClass =
		'whitespace-nowrap text-slate-600 dark:text-slate-200 text-xs font-medium leading-none';
	const featureBadgeIconSlotClass = 'relative flex size-4 items-center justify-center';
	const featureBadgePrimaryIconMotionClass =
		'transition-all duration-200 ease-out group-hover:scale-75 group-hover:opacity-0 group-focus:scale-75 group-focus:opacity-0';
	const featureBadgeCloseIconMotionClass =
		'absolute inset-0 m-auto size-3 scale-75 opacity-0 transition-all duration-200 ease-out group-hover:scale-100 group-hover:opacity-100 group-focus:scale-100 group-focus:opacity-100';
	const webSearchIconClass = 'size-4 text-sky-500 dark:text-sky-300';
	const imageGenerationIconClass = 'size-4 text-teal-500 dark:text-teal-300';
	const codeInterpreterIconClass = 'size-4 text-violet-500 dark:text-violet-300';
	const webSearchCloseIconClass = `${featureBadgeCloseIconMotionClass} text-sky-600 dark:text-sky-300`;
	const imageGenerationCloseIconClass = `${featureBadgeCloseIconMotionClass} text-teal-600 dark:text-teal-300`;
	const codeInterpreterCloseIconClass = `${featureBadgeCloseIconMotionClass} text-violet-600 dark:text-violet-300`;

	let showTools = false;

	let loaded = false;
	let recording = false;

	let isComposing = false;

	let chatInputContainerElement;
	let chatInputElement;

	let filesInputElement;
	let commandsElement;

	let inputFiles;
	let dragged = false;

	let user = null;
	export let placeholder = '';

	let visionCapableModels = [];
	$: visionCapableModels = [...(atSelectedModel ? [atSelectedModel] : selectedModels)].filter(
		(model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.vision ?? true
	);

	const scrollToBottom = () => {
		const element = document.getElementById('messages-container');
		element.scrollTo({
			top: element.scrollHeight,
			behavior: 'auto'
		});
	};

	const getCommand = () => {
		const chatInput = document.getElementById('chat-input');
		if (!chatInput) {
			return '';
		}

		return chatInputElement?.getWordAtDocPos?.() ?? '';
	};

	const replaceCommandWithText = (text: string) => {
		const chatInput = document.getElementById('chat-input');
		if (!chatInput) {
			return;
		}

		chatInputElement?.replaceCommandWithText?.(text);
	};

	const insertTextAtCursor = async (text: string) => {
		const chatInput = document.getElementById('chat-input');
		if (!chatInput) {
			return;
		}

		text = await textVariableHandler(text);

		if (command) {
			replaceCommandWithText(text);
		} else {
			chatInputElement?.insertContent?.(text);
		}

		await tick();
		text = await inputVariableHandler(text);
		await tick();

		const chatInputContainer = document.getElementById('chat-input-container');
		if (chatInputContainer) {
			chatInputContainer.scrollTop = chatInputContainer.scrollHeight;
		}

		await tick();
		chatInputElement?.focus?.();
		chatInput?.dispatchEvent(new Event('input'));
	};

	const replaceLastWordInTextarea = (replacement: string) => {
		const lines = prompt.split('\n');
		const lastLine = lines.pop() ?? '';
		const words = lastLine.split(' ');
		words.pop();

		if (replacement) {
			words.push(replacement);
		}

		lines.push(words.join(' '));
		prompt = lines.join('\n');
	};

	const insertTextIntoTextareaPrompt = async (text: string) => {
		const textarea = document.getElementById('chat-input') as HTMLTextAreaElement | null;
		if (!textarea) {
			return;
		}

		text = await textVariableHandler(text);
		replaceLastWordInTextarea(text);

		await tick();
		text = await inputVariableHandler(text);
		await tick();

		textarea.focus();
		textarea.dispatchEvent(new Event('input'));
	};

	const screenCaptureHandler = async () => {
		try {
			// Request screen media
			const mediaStream = await navigator.mediaDevices.getDisplayMedia({
				video: { cursor: 'never' },
				audio: false
			});
			// Once the user selects a screen, temporarily create a video element
			const video = document.createElement('video');
			video.srcObject = mediaStream;
			// Ensure the video loads without affecting user experience or tab switching
			await video.play();
			// Set up the canvas to match the video dimensions
			const canvas = document.createElement('canvas');
			canvas.width = video.videoWidth;
			canvas.height = video.videoHeight;
			// Grab a single frame from the video stream using the canvas
			const context = canvas.getContext('2d');
			context.drawImage(video, 0, 0, canvas.width, canvas.height);
			// Stop all video tracks (stop screen sharing) after capturing the image
			mediaStream.getTracks().forEach((track) => track.stop());

			// bring back focus to this current tab, so that the user can see the screen capture
			window.focus();

			// Convert the canvas to a Base64 image URL
			const imageUrl = canvas.toDataURL('image/png');
			// Add the captured image to the files array to render it
			files = [...files, { type: 'image', url: imageUrl }];
			// Clean memory: Clear video srcObject
			video.srcObject = null;
		} catch (error) {
			// Handle any errors (e.g., user cancels screen sharing)
			console.error('Error capturing screen:', error);
		}
	};

	const getUploadLocalizeOptions = () => ({
		isAdmin: $_user?.role === 'admin'
	});

	const setUploadFailure = (tempItemId: string, error: unknown) => {
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

	const uploadFileHandler = async (file, fullContext: boolean = false) => {
		if ($_user?.role !== 'admin' && !($_user?.permissions?.chat?.file_upload ?? true)) {
			toast.error($i18n.t('You do not have permission to upload files.'));
			return null;
		}

		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: '',
			name: file.name,
			collection_name: '',
			status: 'uploading',
			size: file.size,
			error: '',
			errorTitle: '',
			errorHint: '',
			diagnostic: null,
			itemId: tempItemId,
			...(fullContext
				? { context: 'full', processing_mode: 'full_context' }
				: {})
		};

		if (fileItem.size == 0) {
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		files = [...files, fileItem];

		try {
			// During the file upload, file content is automatically extracted.
			const uploadedFile = await uploadFile(localStorage.token, file, {
				processingMode: fullContext ? 'full_context' : undefined
			});

			if (uploadedFile) {
				console.log('File upload completed:', {
					id: uploadedFile.id,
					name: fileItem.name,
					collection: uploadedFile?.meta?.collection_name
				});

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(
						localizeFileUploadError(
							uploadedFile.error,
							$i18n.t.bind($i18n),
							getUploadLocalizeOptions()
						)
					);
				}

				fileItem.status = 'uploaded';
				fileItem.file = uploadedFile;
				fileItem.id = uploadedFile.id;
				fileItem.collection_name =
					uploadedFile?.meta?.collection_name || uploadedFile?.collection_name;
				fileItem.processing_mode = uploadedFile?.meta?.processing_mode;
				if (uploadedFile?.meta?.processing_mode === 'full_context') {
					fileItem.context = 'full';
				} else if (uploadedFile?.meta?.processing_mode !== 'native_file') {
					delete fileItem.context;
				}
				fileItem.url = `${WEBUI_API_BASE_URL}/files/${uploadedFile.id}`;

				files = files;
			} else {
				setUploadFailure(tempItemId, new Error($i18n.t('Failed to upload file.')));
			}
		} catch (e) {
			setUploadFailure(tempItemId, e);
		}
	};

	const inputFilesHandler = async (inputFiles) => {
		console.log('Input files handler called with:', inputFiles);
		for (let file of inputFiles) {
			console.log('Processing file:', {
				name: file.name,
				type: file.type,
				size: file.size,
				extension: file.name.split('.').at(-1)
			});

			if (
				($config?.file?.max_size ?? null) !== null &&
				file.size > ($config?.file?.max_size ?? 0) * 1024 * 1024
			) {
				console.log('File exceeds max size limit:', {
					fileSize: file.size,
					maxSize: ($config?.file?.max_size ?? 0) * 1024 * 1024
				});
				toast.error(
					$i18n.t(`File size should not exceed {{maxSize}} MB.`, {
						maxSize: $config?.file?.max_size
					})
				);
				continue;
			}

			// Convert HEIC/HEIF to JPEG before processing
			if (isHeicFile(file)) {
				try {
					file = await convertHeicToJpeg(file);
				} catch (err) {
					console.error('HEIC conversion failed:', err);
					toast.error($i18n.t('Failed to convert HEIC image'));
					continue;
				}
			}

			if (
				['image/gif', 'image/webp', 'image/jpeg', 'image/png', 'image/avif'].includes(file['type'])
			) {
				if (visionCapableModels.length === 0) {
					toast.error($i18n.t('Selected model(s) do not support image inputs'));
					continue;
				}
				let reader = new FileReader();
				reader.onload = async (event) => {
					let imageUrl = event.target.result;

					if (($settings?.imageCompression ?? false) && !isAnimatedImage(file)) {
						const width = $settings?.imageCompressionSize?.width ?? null;
						const height = $settings?.imageCompressionSize?.height ?? null;

						if (width || height) {
							imageUrl = await compressImage(imageUrl, width, height);
						}
					}

					files = [
						...files,
						{
							type: 'image',
							url: `${imageUrl}`
						}
					];
				};
				reader.readAsDataURL(file);
			} else {
				uploadFileHandler(file);
			}
		}
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			console.log('Escape');
			dragged = false;
		}
	};

	const onDragOver = (e) => {
		e.preventDefault();

		// Check if a file is being dragged.
		if (e.dataTransfer?.types?.includes('Files')) {
			dragged = true;
		} else {
			dragged = false;
		}
	};

	const onDragLeave = () => {
		dragged = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();
		console.log(e);

		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);
			if (inputFiles && inputFiles.length > 0) {
				console.log(inputFiles);
				inputFilesHandler(inputFiles);
			}
		}

		dragged = false;
	};

	onMount(async () => {
		suggestions = ['@', '/', '#', '$'].map((char) => ({
			char,
			render: getSuggestionRenderer(CommandSuggestionList, {
				i18n,
				onSelect: (event) => {
					const { type, data } = event;

					if (type === 'model') {
						atSelectedModel = data;
					}

					document.getElementById('chat-input')?.focus();
				},
				insertTextHandler: insertTextAtCursor,
				onUpload: (event) => {
					const { type, data } = event;

					if (type === 'file') {
						if (files.find((file) => file.id === data.id)) {
							return;
						}

						files = [
							...files,
							{
								...data,
								status: 'processed'
							}
						];
					} else {
						if (files.find((file) => file.url === data || file.name === data)) {
							return;
						}

						dispatch('upload', event);
					}
				}
			})
		}));

		loaded = true;

		window.setTimeout(() => {
			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		}, 0);

		window.addEventListener('keydown', handleKeyDown);

		await tick();

		const dropzoneElement = document.getElementById('chat-container');

		dropzoneElement?.addEventListener('dragover', onDragOver);
		dropzoneElement?.addEventListener('drop', onDrop);
		dropzoneElement?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', handleKeyDown);

		const dropzoneElement = document.getElementById('chat-container');

		if (dropzoneElement) {
			dropzoneElement?.removeEventListener('dragover', onDragOver);
			dropzoneElement?.removeEventListener('drop', onDrop);
			dropzoneElement?.removeEventListener('dragleave', onDragLeave);
		}
	});
</script>

<FilesOverlay show={dragged} />

<ToolServersModal bind:show={showTools} {selectedToolIds} />
<InputVariablesModal
	bind:show={showInputVariablesModal}
	variables={inputVariables}
	onSave={inputVariablesModalCallback}
/>

{#if loaded}
	<div class="w-full font-primary">
		<div class=" mx-auto inset-x-0 bg-transparent flex justify-center">
			<div
				class="flex flex-col px-3 {($settings?.widescreenMode ?? null)
					? 'max-w-full'
					: 'max-w-5xl'} w-full"
			>
				<div class="relative">
					{#if autoScroll === false && history?.currentId}
						<div
							class=" absolute -top-12 left-0 right-0 flex justify-center z-30 pointer-events-none"
						>
							<button
								class=" bg-white/80 dark:bg-gray-800/80 backdrop-blur-sm border border-gray-200 dark:border-gray-600 p-1.5 rounded-full pointer-events-auto shadow-sm hover:bg-white dark:hover:bg-gray-700 transition-all"
								on:click={() => {
									autoScroll = true;
									scrollToBottom();
								}}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="w-5 h-5"
								>
									<path
										fill-rule="evenodd"
										d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
										clip-rule="evenodd"
									/>
								</svg>
							</button>
						</div>
					{/if}
				</div>

				<div class="w-full relative">
					{#if atSelectedModel !== undefined || selectedToolIds.length > 0 || webSearchActive || imageGenerationEnabled || codeInterpreterEnabled}
						<div
							class="px-3 pb-0.5 pt-1.5 text-left w-full flex flex-col absolute bottom-0 left-0 right-0 bg-linear-to-t from-white dark:from-gray-900 z-10"
						>
							{#if atSelectedModel !== undefined}
								<div class="flex items-center justify-between w-full">
									<div class="pl-[1px] flex items-center gap-2 text-sm dark:text-gray-500">
										<ModelIcon
											alt="model profile"
											className="size-3.5 max-w-[28px] rounded-lg"
											src={$models.find((model) => model.id === atSelectedModel.id)?.info?.meta
												?.profile_image_url ??
												$models.find((model) => model.id === atSelectedModel.id)?.meta
													?.profile_image_url ??
												($i18n.language === 'dg-DG'
													? `/doge.png`
													: `${WEBUI_BASE_URL}/static/favicon.png`)}
										/>
										<div class="translate-y-[0.5px]">
											Talking to <span class=" font-medium"
												>{getModelChatDisplayName(atSelectedModel)}</span
											>
										</div>
									</div>
									<div>
										<button
											class="flex items-center dark:text-gray-500"
											on:click={() => {
												atSelectedModel = undefined;
											}}
										>
											<XMark />
										</button>
									</div>
								</div>
							{/if}
						</div>
					{/if}

					{#if !($settings?.richTextInput ?? true)}
						<Commands
							bind:this={commandsElement}
							bind:prompt
							bind:files
							insertTextHandler={insertTextIntoTextareaPrompt}
							on:upload={(e) => {
								dispatch('upload', e.detail);
							}}
							on:select={(e) => {
								const data = e.detail;

								if (data?.type === 'model') {
									atSelectedModel = data.data;
								}

								document.getElementById('chat-input')?.focus();
							}}
						/>
					{/if}
				</div>
			</div>
		</div>

		<div class="bg-transparent pb-3 pt-1">
			<div
				class="{($settings?.widescreenMode ?? null)
					? 'max-w-full'
					: 'max-w-4xl'} px-2.5 mx-auto inset-x-0"
			>
				<div class="">
					<input
						bind:this={filesInputElement}
						bind:files={inputFiles}
						type="file"
						hidden
						multiple
						on:change={async () => {
							if (inputFiles && inputFiles.length > 0) {
								const _inputFiles = Array.from(inputFiles);
								inputFilesHandler(_inputFiles);
							} else {
								toast.error($i18n.t(`File not found.`));
							}

							filesInputElement.value = '';
						}}
					/>

					{#if recording}
						<VoiceRecording
							bind:recording
							on:cancel={async () => {
								recording = false;

								await tick();
								document.getElementById('chat-input')?.focus();
							}}
							on:confirm={async (e) => {
								const { text, filename } = e.detail;
								prompt = `${prompt}${text} `;

								recording = false;

								await tick();
								document.getElementById('chat-input')?.focus();

								if ($settings?.speechAutoSend ?? false) {
									dispatch('submit', prompt);
								}
							}}
						/>
					{:else}
						<form
							class="w-full flex gap-1.5"
							on:submit|preventDefault={() => {
								// check if selectedModels support image input
								dispatch('submit', prompt);
							}}
						>
							<div
								class="flex-1 flex flex-col relative w-full rounded-3xl border border-gray-200/50 dark:border-gray-700/20 hover:border-gray-300/60 dark:hover:border-gray-600/40 focus-within:border-primary-300/40 dark:focus-within:border-primary-500/25 shadow-sm dark:shadow-none focus-within:shadow-lg focus-within:shadow-primary-500/5 dark:focus-within:shadow-primary-400/[0.07] transition-all duration-300 px-1 pt-1 bg-white/80 dark:bg-white/[0.04] backdrop-blur-xl dark:text-gray-100"
								dir={$settings?.chatDirection ?? 'auto'}
							>
								<ImageContextPanel
									currentModel={primarySelectedModel}
									{imageGenerationEnabled}
									bind:imageGenerationOptions
									on:advanced={() => {
										showControls.set(true);
									}}
								/>

								{#if files.length > 0}
									<div class="px-2.5 mt-0.5 mb-1.5 pt-1.5 flex items-end gap-2 overflow-x-auto scrollbar-none">
										{#each files as file, fileIdx}
											{#if file.type === 'image'}
												<div class="relative group shrink-0">
													<div class="relative flex items-center rounded-xl ring-1 ring-gray-200/60 dark:ring-white/10">
														<Image
															src={file.url}
															alt="input"
															imageClassName=" size-14 rounded-xl object-cover"
														/>
														{#if atSelectedModel ? visionCapableModels.length === 0 : selectedModels.length !== visionCapableModels.length}
															<Tooltip
																className=" absolute top-1 left-1"
																content={$i18n.t('{{ models }}', {
																	models: [
																		...(atSelectedModel ? [atSelectedModel] : selectedModels)
																	]
																		.filter((id) => !visionCapableModels.includes(id))
																		.join(', ')
																})}
															>
																<svg
																	xmlns="http://www.w3.org/2000/svg"
																	viewBox="0 0 24 24"
																	fill="currentColor"
																	class="size-4 fill-yellow-300"
																>
																	<path
																		fill-rule="evenodd"
																		d="M9.401 3.003c1.155-2 4.043-2 5.197 0l7.355 12.748c1.154 2-.29 4.5-2.599 4.5H4.645c-2.309 0-3.752-2.5-2.598-4.5L9.4 3.003ZM12 8.25a.75.75 0 0 1 .75.75v3.75a.75.75 0 0 1-1.5 0V9a.75.75 0 0 1 .75-.75Zm0 8.25a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Z"
																		clip-rule="evenodd"
																	/>
																</svg>
															</Tooltip>
														{/if}
													</div>
													<div class=" absolute -top-1.5 -right-1.5">
														<button
															class="bg-gray-900/70 dark:bg-gray-700/90 text-white border border-white/20 dark:border-gray-500/30 rounded-full group-hover:visible invisible transition backdrop-blur-sm p-px"
															type="button"
															on:click={() => {
																files.splice(fileIdx, 1);
																files = files;
															}}
														>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 20 20"
																fill="currentColor"
																class="size-3.5"
															>
																<path
																	d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
																/>
															</svg>
														</button>
													</div>
												</div>
											{:else}
												<FileItem
													className="w-60 shrink-0"
													item={file}
													name={file.name}
													type={file.type}
													size={file?.size}
													loading={file.status === 'uploading'}
													dismissible={true}
													edit={true}
													on:dismiss={async () => {
														if (file.type !== 'collection' && !file?.collection) {
															if (file.id) {
																// This will handle both file deletion and Chroma cleanup
																await deleteFileById(localStorage.token, file.id);
															}
														}

														// Remove from UI state
														files.splice(fileIdx, 1);
														files = files;
													}}
													on:click={() => {
														console.log(file);
													}}
												/>
											{/if}
										{/each}
									</div>
								{/if}

								<div class="px-2.5">
									{#if $settings?.richTextInput ?? true}
										<div
											class="scrollbar-hidden text-left bg-transparent dark:text-gray-100 outline-hidden w-full pt-3 px-1 resize-none h-fit max-h-80 overflow-auto"
											id="chat-input-container"
										>
											<RichTextInput
												bind:this={chatInputElement}
												value={prompt}
												id="chat-input"
												messageInput={true}
												showFormattingToolbar={$settings?.showFormattingToolbar ?? false}
												insertPromptAsRichText={$settings?.insertPromptAsRichText ?? false}
												shiftEnter={!($settings?.ctrlEnterToSend ?? false) &&
													(!$mobile ||
														!(
															'ontouchstart' in window ||
															navigator.maxTouchPoints > 0 ||
															navigator.msMaxTouchPoints > 0
														))}
												placeholder={placeholder ? placeholder : $i18n.t('How can I help you today?')}
												largeTextAsFile={$settings?.largeTextAsFile ?? false}
												autocomplete={$config?.features?.enable_autocomplete_generation &&
													($settings?.promptAutocomplete ?? false)}
												{suggestions}
												onChange={(content) => {
													prompt = content.md;
													command = getCommand();
												}}
												generateAutoCompletion={async (text) => {
													if (selectedModelIds.length === 0 || !selectedModelIds.at(0)) {
														toast.error($i18n.t('Please select a model first.'));
													}

													const res = await generateAutoCompletion(
														localStorage.token,
														selectedModelIds.at(0),
														text,
														history?.currentId
															? createMessagesList(history, history.currentId)
															: null
													).catch((error) => {
														console.log(error);

														return null;
													});

													console.log(res);
													return res;
												}}
												oncompositionstart={() => (isComposing = true)}
												oncompositionend={() => (isComposing = false)}
												on:keydown={async (e) => {
													e = e.detail.event;

													const isCtrlPressed = e.ctrlKey || e.metaKey;
													const suggestionsContainerElement =
														document.getElementById('suggestions-container');

													if (e.key === 'Escape') {
														stopResponse();
													}

													if (isCtrlPressed && e.key === 'Enter' && e.shiftKey) {
														e.preventDefault();
														createMessagePair(prompt);
													}

													if (prompt === '' && e.key == 'ArrowUp') {
														e.preventDefault();

														const userMessageElement = [
															...document.getElementsByClassName('user-message')
														]?.at(-1);

														if (userMessageElement) {
															userMessageElement.scrollIntoView({ block: 'center' });
															const editButton = [
																...document.getElementsByClassName('edit-user-message-button')
															]?.at(-1);

															editButton?.click();
														}
													}

													if (!suggestionsContainerElement) {
														if (
															!$mobile ||
															!(
																'ontouchstart' in window ||
																navigator.maxTouchPoints > 0 ||
																navigator.msMaxTouchPoints > 0
															)
														) {
															if (isComposing) {
																return;
															}

															const enterPressed =
																($settings?.ctrlEnterToSend ?? false)
																	? (e.key === 'Enter' || e.keyCode === 13) && isCtrlPressed
																	: (e.key === 'Enter' || e.keyCode === 13) && !e.shiftKey;

															if (enterPressed) {
																e.preventDefault();
																if (prompt !== '' || files.length > 0) {
																	dispatch('submit', prompt);
																}
															}
														}
													}

													if (e.key === 'Escape') {
														atSelectedModel = undefined;
														selectedToolIds = [];
														webSearchMode = 'off';
														imageGenerationEnabled = false;
													}
												}}
												on:paste={async (e) => {
													e = e.detail.event;

													const clipboardData = e.clipboardData || window.clipboardData;

													if (clipboardData && clipboardData.items) {
														for (const item of clipboardData.items) {
															if (item.type.indexOf('image') !== -1) {
																let blob = item.getAsFile();
																if (blob && isHeicFile(blob)) {
																	try {
																		blob = await convertHeicToJpeg(blob);
																	} catch (err) {
																		console.error('HEIC paste conversion failed:', err);
																		continue;
																	}
																}
																const reader = new FileReader();

																reader.onload = function (e) {
																	files = [
																		...files,
																		{
																			type: 'image',
																			url: `${e.target.result}`
																		}
																	];
																};

																reader.readAsDataURL(blob);
															} else if (item.type === 'text/plain') {
																if ($settings?.largeTextAsFile ?? false) {
																	const text = clipboardData.getData('text/plain');

																	if (text.length > PASTED_TEXT_CHARACTER_LIMIT) {
																		e.preventDefault();
																		const blob = new Blob([text], { type: 'text/plain' });
																		const file = new File([blob], `Pasted_Text_${Date.now()}.txt`, {
																			type: 'text/plain'
																		});

																		await uploadFileHandler(file, true);
																	}
																}
															}
														}
													}
												}}
											/>
										</div>
									{:else}
										<textarea
											id="chat-input"
											dir="auto"
											bind:this={chatInputElement}
											class="scrollbar-hidden bg-transparent dark:text-gray-100 outline-hidden w-full pt-3 px-1 resize-none"
											placeholder={placeholder ? placeholder : $i18n.t('How can I help you today?')}
											bind:value={prompt}
											on:compositionstart={() => (isComposing = true)}
											on:compositionend={() => (isComposing = false)}
											on:keydown={async (e) => {
												const isCtrlPressed = e.ctrlKey || e.metaKey; // metaKey is for Cmd key on Mac

												const commandsContainerElement =
													document.getElementById('commands-container');

												if (e.key === 'Escape') {
													stopResponse();
												}

												// Command/Ctrl + Shift + Enter to submit a message pair
												if (isCtrlPressed && e.key === 'Enter' && e.shiftKey) {
													e.preventDefault();
													createMessagePair(prompt);
												}

												if (prompt === '' && e.key == 'ArrowUp') {
													e.preventDefault();

													const userMessageElement = [
														...document.getElementsByClassName('user-message')
													]?.at(-1);

													const editButton = [
														...document.getElementsByClassName('edit-user-message-button')
													]?.at(-1);

													console.log(userMessageElement);

													userMessageElement.scrollIntoView({ block: 'center' });
													editButton?.click();
												}

												if (commandsContainerElement) {
													if (commandsContainerElement && e.key === 'ArrowUp') {
														e.preventDefault();
														commandsElement.selectUp();

														const commandOptionButton = [
															...document.getElementsByClassName('selected-command-option-button')
														]?.at(-1);
														commandOptionButton.scrollIntoView({ block: 'center' });
													}

													if (commandsContainerElement && e.key === 'ArrowDown') {
														e.preventDefault();
														commandsElement.selectDown();

														const commandOptionButton = [
															...document.getElementsByClassName('selected-command-option-button')
														]?.at(-1);
														commandOptionButton.scrollIntoView({ block: 'center' });
													}

													if (commandsContainerElement && e.key === 'Enter') {
														e.preventDefault();

														const commandOptionButton = [
															...document.getElementsByClassName('selected-command-option-button')
														]?.at(-1);

														if (e.shiftKey) {
															prompt = `${prompt}\n`;
														} else if (commandOptionButton) {
															commandOptionButton?.click();
														} else {
															document.getElementById('send-message-button')?.click();
														}
													}

													if (commandsContainerElement && e.key === 'Tab') {
														e.preventDefault();

														const commandOptionButton = [
															...document.getElementsByClassName('selected-command-option-button')
														]?.at(-1);

														commandOptionButton?.click();
													}
												} else {
													if (
														!$mobile ||
														!(
															'ontouchstart' in window ||
															navigator.maxTouchPoints > 0 ||
															navigator.msMaxTouchPoints > 0
														)
													) {
														if (isComposing) {
															return;
														}

														// Prevent Enter key from creating a new line
														const isCtrlPressed = e.ctrlKey || e.metaKey;
														const enterPressed =
															($settings?.ctrlEnterToSend ?? false)
																? (e.key === 'Enter' || e.keyCode === 13) && isCtrlPressed
																: (e.key === 'Enter' || e.keyCode === 13) && !e.shiftKey;

														console.log('Enter pressed:', enterPressed);

														if (enterPressed) {
															e.preventDefault();
														}

														// Submit the prompt when Enter key is pressed
														if ((prompt !== '' || files.length > 0) && enterPressed) {
															dispatch('submit', prompt);
														}
													}
												}

												if (e.key === 'Tab') {
													const words = extractCurlyBraceWords(prompt);

													if (words.length > 0) {
														const word = words.at(0);
														const fullPrompt = prompt;

														prompt = prompt.substring(0, word?.endIndex + 1);
														await tick();

														e.target.scrollTop = e.target.scrollHeight;
														prompt = fullPrompt;
														await tick();

														e.preventDefault();
														e.target.setSelectionRange(word?.startIndex, word.endIndex + 1);
													}

													e.target.style.height = '';
													e.target.style.height = Math.min(e.target.scrollHeight, 320) + 'px';
												}

												if (e.key === 'Escape') {
													console.log('Escape');
													atSelectedModel = undefined;
													selectedToolIds = [];
													webSearchMode = 'off';
													imageGenerationEnabled = false;
												}
											}}
											rows="1"
											on:input={async (e) => {
												e.target.style.height = '';
												e.target.style.height = Math.min(e.target.scrollHeight, 320) + 'px';
											}}
											on:focus={async (e) => {
												e.target.style.height = '';
												e.target.style.height = Math.min(e.target.scrollHeight, 320) + 'px';
											}}
											on:paste={async (e) => {
												const clipboardData = e.clipboardData || window.clipboardData;

												if (clipboardData && clipboardData.items) {
													for (const item of clipboardData.items) {
														if (item.type.indexOf('image') !== -1) {
															let blob = item.getAsFile();
															if (blob && isHeicFile(blob)) {
																try {
																	blob = await convertHeicToJpeg(blob);
																} catch (err) {
																	console.error('HEIC paste conversion failed:', err);
																	continue;
																}
															}
															const reader = new FileReader();

															reader.onload = function (e) {
																files = [
																	...files,
																	{
																		type: 'image',
																		url: `${e.target.result}`
																	}
																];
															};

															reader.readAsDataURL(blob);
														} else if (item.type === 'text/plain') {
															if ($settings?.largeTextAsFile ?? false) {
																const text = clipboardData.getData('text/plain');

																if (text.length > PASTED_TEXT_CHARACTER_LIMIT) {
																	e.preventDefault();
																	const blob = new Blob([text], { type: 'text/plain' });
																	const file = new File([blob], `Pasted_Text_${Date.now()}.txt`, {
																		type: 'text/plain'
																	});

																	await uploadFileHandler(file, true);
																}
															}
														}
													}
												}
											}}
										/>
									{/if}
								</div>

								<div class=" flex justify-between mt-1.5 mb-3 mx-0.5 max-w-full" dir="ltr">
									<div class="ml-1 self-end flex items-center flex-1 max-w-[80%] gap-0.5">
														<InputMenu
															bind:selectedToolIds
															bind:webSearchMode
															{webSearchModeOptions}
															bind:imageGenerationEnabled
															bind:codeInterpreterEnabled
											{screenCaptureHandler}
											{inputFilesHandler}
											uploadFilesHandler={() => {
												filesInputElement.click();
											}}
											uploadGoogleDriveHandler={async () => {
												try {
													const fileData = await createPicker();
													if (fileData) {
														const file = new File([fileData.blob], fileData.name, {
															type: fileData.blob.type
														});
														await uploadFileHandler(file);
													} else {
														console.log('No file was selected from Google Drive');
													}
												} catch (error) {
													console.error('Google Drive Error:', error);
													toast.error(
														$i18n.t('Error accessing Google Drive: {{error}}', {
															error: error.message
														})
													);
												}
											}}
											uploadOneDriveHandler={async () => {
												try {
													const fileData = await pickAndDownloadFile();
													if (fileData) {
														const file = new File([fileData.blob], fileData.name, {
															type: fileData.blob.type || 'application/octet-stream'
														});
														await uploadFileHandler(file);
													} else {
														console.log('No file was selected from OneDrive');
													}
												} catch (error) {
													console.error('OneDrive Error:', error);
												}
											}}
											onClose={async () => {
												await tick();

												const chatInput = document.getElementById('chat-input');
												chatInput?.focus();
											}}
										>
											<button
												class="bg-transparent hover:bg-gray-100 text-gray-800 dark:text-white dark:hover:bg-gray-800 transition rounded-full p-1.5 outline-hidden focus:outline-hidden"
												type="button"
												aria-label="More"
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 20 20"
													fill="currentColor"
													class="size-5"
												>
													<path
														d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z"
													/>
												</svg>
											</button>
										</InputMenu>

										<div class="flex gap-1 items-center overflow-x-auto scrollbar-none flex-1">
											{#if toolServers.length + selectedToolIds.length > 0}
												<Tooltip
													content={$i18n.t('{{COUNT}} Available Tools', {
														COUNT: toolServers.length + selectedToolIds.length
													})}
												>
													<button
														class="translate-y-[0.5px] flex gap-1 items-center text-gray-600 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 rounded-lg p-1 self-center transition"
														aria-label="Available Tools"
														type="button"
														on:click={() => {
															showTools = !showTools;
														}}
													>
														<Wrench className="size-4" strokeWidth="1.75" />

														<span class="text-sm font-medium text-gray-600 dark:text-gray-300">
															{toolServers.length + selectedToolIds.length}
														</span>
													</button>
												</Tooltip>
											{/if}

											{#if activeAssistant}
												<Tooltip
													content={`当前助手：${activeAssistant.name}${activeAssistant.description ? `\n${activeAssistant.description}` : ''}`}
													placement="top"
												>
													<button
														type="button"
														class={`${featureBadgeBaseClass} px-2.5 py-1.5 gap-1.5 max-w-[13rem]`}
														aria-label={`关闭助手 ${activeAssistant.name}`}
														on:click={() => {
															onDeactivateAssistant?.();
														}}
													>
														<span class="shrink-0 text-sm leading-none">
															{activeAssistant.emoji}
														</span>
														<span class={`${featureBadgeLabelClass} truncate`}>
															{activeAssistant.name}
														</span>
														<XMark
															className="size-3 text-slate-500 dark:text-slate-300 shrink-0"
															strokeWidth="2.25"
														/>
													</button>
												</Tooltip>
											{/if}

											{#if $_user}
													{#if webSearchFeatureEnabled && ($_user.role === 'admin' || $_user?.permissions?.features?.web_search) && webSearchActive}
													<Tooltip content={`${currentWebSearchTooltip}，点击关闭`} placement="top">
														<button
															type="button"
															class={webSearchBadgeClass}
															aria-label={$i18n.t('关闭联网搜索')}
															on:click={() => {
																webSearchMode = 'off';
															}}
														>
															<span class={featureBadgeIconSlotClass}>
																<GlobeAlt
																	className={`${webSearchIconClass} ${featureBadgePrimaryIconMotionClass}`}
																	strokeWidth="1.75"
																/>
																<XMark className={webSearchCloseIconClass} strokeWidth="2.5" />
															</span>
															<span class={featureBadgeLabelClass}>
																{currentWebSearchBadgeLabel}
															</span>
														</button>
													</Tooltip>
												{/if}

												{#if $config?.features?.enable_image_generation && ($_user.role === 'admin' || $_user?.permissions?.features?.image_generation) && imageGenerationEnabled}
													<Tooltip content={$i18n.t('已开启AI绘图，点击关闭')} placement="top">
														<button
															type="button"
															class={compactFeatureBadgeClass}
															aria-label={$i18n.t('关闭AI绘图')}
															on:click={() => {
																imageGenerationEnabled = false;
															}}
														>
															<span class={featureBadgeIconSlotClass}>
																<Photo
																	className={`${imageGenerationIconClass} ${featureBadgePrimaryIconMotionClass}`}
																	strokeWidth="1.75"
																/>
																<XMark className={imageGenerationCloseIconClass} strokeWidth="2.5" />
															</span>
														</button>
													</Tooltip>
												{/if}

												{#if $config?.features?.enable_code_interpreter && ($_user.role === 'admin' || $_user?.permissions?.features?.code_interpreter) && codeInterpreterEnabled}
													<Tooltip content={$i18n.t('已开启代码解释器，点击关闭')} placement="top">
														<button
															type="button"
															class={compactFeatureBadgeClass}
															aria-label={$i18n.t('关闭代码解释器')}
															on:click={() => {
																codeInterpreterEnabled = false;
															}}
														>
															<span class={featureBadgeIconSlotClass}>
																<CommandLine
																	className={`${codeInterpreterIconClass} ${featureBadgePrimaryIconMotionClass}`}
																	strokeWidth="1.75"
																/>
																<XMark className={codeInterpreterCloseIconClass} strokeWidth="2.5" />
															</span>
														</button>
													</Tooltip>
												{/if}
											{/if}

											<ThinkingControl
												bind:reasoningEffort
												bind:maxThinkingTokens
												model={primarySelectedModel}
											/>
										</div>
									</div>

									<div class="self-end flex space-x-1 mr-1 shrink-0">
										{#if (!history?.currentId || history.messages[history.currentId]?.done == true) && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.stt ?? true))}
											<Tooltip content={$i18n.t('Record voice')}>
												<button
													id="voice-input-button"
													class=" text-gray-600 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 transition rounded-full p-[7px] mr-0.5 self-center"
													type="button"
													on:click={async () => {
														try {
															let stream = await navigator.mediaDevices
																.getUserMedia({ audio: true })
																.catch(function (err) {
																	toast.error(
																		$i18n.t(
																			`Permission denied when accessing microphone: {{error}}`,
																			{
																				error: err
																			}
																		)
																	);
																	return null;
																});

															if (stream) {
																recording = true;
																const tracks = stream.getTracks();
																tracks.forEach((track) => track.stop());
															}
															stream = null;
														} catch {
															toast.error($i18n.t('Permission denied when accessing microphone'));
														}
													}}
													aria-label="Voice Input"
												>
													<svg
														xmlns="http://www.w3.org/2000/svg"
														viewBox="0 0 24 24"
														fill="none"
														stroke="currentColor"
														stroke-width="2"
														stroke-linecap="round"
														stroke-linejoin="round"
														class="w-5 h-5"
													>
														<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
														<path d="M19 10v2a7 7 0 0 1-14 0v-2" />
														<line x1="12" x2="12" y1="19" y2="22" />
														<line x1="8" x2="16" y1="22" y2="22" />
													</svg>
												</button>
											</Tooltip>
										{/if}

										{#if (taskIds && taskIds.length > 0) || (history.currentId && history.messages[history.currentId]?.done != true)}
											<div class=" flex items-center">
												<Tooltip content={$i18n.t('Stop')}>
													<button
														class="bg-white hover:bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-white dark:hover:bg-gray-800 transition rounded-full p-[7px]"
														aria-label={$i18n.t('Stop')}
														on:click={() => {
															stopResponse();
														}}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 24 24"
															fill="currentColor"
															class="size-5"
														>
															<path
																fill-rule="evenodd"
																d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm6-2.438c0-.724.588-1.312 1.313-1.312h4.874c.725 0 1.313.588 1.313 1.313v4.874c0 .725-.588 1.313-1.313 1.313H9.564a1.312 1.312 0 01-1.313-1.313V9.564z"
																clip-rule="evenodd"
															/>
														</svg>
													</button>
												</Tooltip>
											</div>
										{:else if prompt === '' && files.length === 0 && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.call ?? true))}
											<div class=" flex items-center">
												<Tooltip content={$i18n.t('Call')}>
													<button
														class=" bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full p-[7px] self-center"
														type="button"
														on:click={async () => {
															if (selectedModels.length > 1) {
																toast.error($i18n.t('Select only one model to call'));

																return;
															}

															if ($config.audio.stt.engine === 'web') {
																toast.error(
																	$i18n.t('Call feature is not supported when using Web STT engine')
																);

																return;
															}
															// check if user has access to getUserMedia
															try {
																let stream = await navigator.mediaDevices.getUserMedia({
																	audio: true
																});
																// If the user grants the permission, proceed to show the call overlay

																if (stream) {
																	const tracks = stream.getTracks();
																	tracks.forEach((track) => track.stop());
																}

																stream = null;

																if ($settings.audio?.tts?.engine === 'browser-kokoro') {
																	// If the user has not initialized the TTS worker, initialize it
																	if (!$TTSWorker) {
																		await TTSWorker.set(
																			new KokoroWorker({
																				dtype: $settings.audio?.tts?.engineConfig?.dtype ?? 'fp32'
																			})
																		);

																		await $TTSWorker.init();
																	}
																}

																showCallOverlay.set(true);
																showControls.set(true);
															} catch (err) {
																// If the user denies the permission or an error occurs, show an error message
																toast.error(
																	$i18n.t('Permission denied when accessing media devices')
																);
															}
														}}
														aria-label="Call"
													>
														<!-- 波形图标 - 灵动非对称版 -->
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 24 24"
															fill="none"
															stroke="currentColor"
															stroke-width="2.6"
															stroke-linecap="round"
															stroke-linejoin="round"
															class="size-5"
														>
															<path d="M5 8.5v7" />
															<path d="M10 5v14" />
															<path d="M15 7.5v9" />
															<path d="M20 10v4" />
														</svg>
													</button>
												</Tooltip>
											</div>
										{:else}
											<div class="flex items-center group">
												{#if !(prompt === '' && files.length === 0)}
													<SendMenu
														showThinkingOptions={true}
														onSend={() => {
															dispatch('submit', prompt);
														}}
														onSendWithThinking={(effort) => {
															reasoningEffort = effort;
															dispatch('submit', prompt);
														}}
														onSendToNewChat={() => {
															createMessagePair(prompt);
														}}
													/>
												{/if}

												<Tooltip content={$i18n.t('Send message')}>
													<button
														id="send-message-button"
														class="{!(prompt === '' && files.length === 0)
															? 'bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 '
															: 'text-white bg-gray-200 dark:text-gray-900 dark:bg-gray-700 disabled'} transition rounded-full p-[7px] self-center"
														type="submit"
														disabled={prompt === '' && files.length === 0}
														aria-label={$i18n.t('Send message')}
													>
														<svg
															xmlns="http://www.w3.org/2000/svg"
															viewBox="0 0 16 16"
															fill="currentColor"
															class="size-5"
														>
															<path
																fill-rule="evenodd"
																d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
																clip-rule="evenodd"
															/>
														</svg>
													</button>
												</Tooltip>
											</div>
										{/if}
									</div>
								</div>
							</div>
						</form>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}
