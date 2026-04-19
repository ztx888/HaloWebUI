<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';

	import { tick, getContext, onMount, onDestroy } from 'svelte';

	const i18n = getContext('i18n');

	import { config, mobile, settings, socket, user } from '$lib/stores';
	import {
		blobToFile,
		compressImage,
		convertHeicToJpeg,
		extractInputVariables,
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
		buildIgnoredFailedFilesMessage,
		getFileUploadDiagnostic,
		getLocalizedFileUploadDiagnostic,
		isFailedUploadFile,
		localizeFileUploadError
	} from '$lib/utils/file-upload-errors';

	import Tooltip from '../common/Tooltip.svelte';
	import RichTextInput from '../common/RichTextInput.svelte';
	import VoiceRecording from '../chat/MessageInput/VoiceRecording.svelte';
	import InputVariablesModal from '../chat/MessageInput/InputVariablesModal.svelte';
	import CommandSuggestionList from '../chat/MessageInput/CommandSuggestionList.svelte';
	import InputMenu from './MessageInput/InputMenu.svelte';
	import { deleteFileById, uploadFile } from '$lib/apis/files';
	import { PASTED_TEXT_CHARACTER_LIMIT, WEBUI_API_BASE_URL } from '$lib/constants';
	import FileItem from '../common/FileItem.svelte';
	import Image from '../common/Image.svelte';
	import { transcribeAudio } from '$lib/apis/audio';
	import FilesOverlay from '../chat/MessageInput/FilesOverlay.svelte';
	import { getSuggestionRenderer } from '../common/RichTextInput/suggestions';
	import MentionList from './MessageInput/MentionList.svelte';
	import { getSessionUser } from '$lib/apis/auths';

	export let placeholder = $i18n.t('Send a Message');
	export let transparentBackground = false;

	export let id = null;

	let draggedOver = false;
	let dragCounter = 0;

	let recording = false;
	let content = '';
	let files = [];
	let chatInputElement;
	let command = '';
	let suggestions = null;

	let filesInputElement;
	let inputFiles;
	let showInputVariablesModal = false;
	let inputVariables = {};
	let inputVariableValues = {};
	let inputVariablesModalCallback = (_variableValues) => {};

	export let typingUsers = [];
	export let userSuggestions = true;
	export let channelSuggestions = false;

	export let onSubmit: Function;
	export let onChange: Function;
	export let scrollEnd = true;
	export let scrollToBottom: Function = () => {};

	const IMAGE_INPUT_MIME_TYPES = [
		'image/gif',
		'image/webp',
		'image/jpeg',
		'image/png',
		'image/avif'
	];

	const buildUploadedImageContentUrl = (fileId: string) =>
		`${WEBUI_API_BASE_URL}/files/${fileId}/content`;

	const revokePreviewUrl = (value: unknown) => {
		if (typeof value === 'string' && value.startsWith('blob:')) {
			URL.revokeObjectURL(value);
		}
	};

	const createNamedImageFile = (blob: Blob, namePrefix: string) => {
		const mimeType = blob.type || 'image/png';
		const extension = mimeType.split('/').at(1)?.split('+').at(0) || 'png';
		const existingName = blob instanceof File ? blob.name : '';
		const filename = existingName || `${namePrefix}_${Date.now()}.${extension}`;
		return new File([blob], filename, { type: mimeType });
	};

	const normalizeInputFileForMessage = (file) => {
		if (!file || typeof file !== 'object') {
			return file;
		}

		if (file.type === 'image') {
			return Object.fromEntries(
				Object.entries({
					type: 'image',
					id: file.id,
					name: file.name,
					url: file.id ? buildUploadedImageContentUrl(file.id) : file.url,
					size: file.size,
					content_type: file.content_type
				}).filter(([, value]) => value !== undefined && value !== null && value !== '')
			);
		}

		return structuredClone(file);
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
				chatInputElement?.replaceVariables?.(inputVariableValues);
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

			const clipboardItems = await navigator.clipboard.read().catch(() => []);
			for (const item of clipboardItems) {
				for (const type of item.types) {
					if (type.startsWith('image/')) {
						const blob = await item.getType(type);
						const file = new File([blob], `clipboard-image.${type.split('/')[1]}`, { type });
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

	const getCommand = () => {
		const chatInput = document.getElementById(`chat-input-${id}`);
		if (!chatInput) {
			return '';
		}
		return chatInputElement?.getWordAtDocPos?.() ?? '';
	};

	const replaceCommandWithText = (text: string) => {
		const chatInput = document.getElementById(`chat-input-${id}`);
		if (!chatInput) {
			return;
		}
		chatInputElement?.replaceCommandWithText?.(text);
	};

	const insertTextAtCursor = async (text: string) => {
		const chatInput = document.getElementById(`chat-input-${id}`);
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

		chatInputElement?.focus?.();
		chatInput.dispatchEvent(new Event('input'));
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

			const imageBlob = await new Promise<Blob | null>((resolve) =>
				canvas.toBlob(resolve, 'image/png')
			);
			if (!imageBlob) {
				throw new Error('Failed to capture screen image');
			}

			await uploadImageFileHandler(createNamedImageFile(imageBlob, 'Channel_Screen_Capture'));
			// Clean memory: Clear video srcObject
			video.srcObject = null;
		} catch (error) {
			// Handle any errors (e.g., user cancels screen sharing)
			console.error('Error capturing screen:', error);
		}
	};

	const inputFilesHandler = async (inputFiles) => {
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

			if (isHeicFile(file)) {
				try {
					file = await convertHeicToJpeg(file);
				} catch (error) {
					console.error('HEIC conversion failed:', error);
					toast.error($i18n.t('Failed to convert HEIC image'));
					continue;
				}
			}

			if (IMAGE_INPUT_MIME_TYPES.includes(file['type'])) {
				if (
					($settings?.imageCompression ?? false) &&
					($settings?.imageCompressionInChannels ?? true) &&
					!isAnimatedImage(file)
				) {
					const width = $settings?.imageCompressionSize?.width ?? null;
					const height = $settings?.imageCompressionSize?.height ?? null;

					if (width || height) {
						const tempPreviewUrl = URL.createObjectURL(file);
						const imageUrl = await compressImage(tempPreviewUrl, width, height).finally(() => {
							revokePreviewUrl(tempPreviewUrl);
						});
						const response = await fetch(imageUrl);
						const imageBlob = await response.blob();
						file = createNamedImageFile(
							imageBlob,
							file.name.replace(/\.[^.]+$/, '') || 'Channel_Image'
						);
					}
				}

				await uploadImageFileHandler(file);
			} else {
				await uploadFileHandler(file);
			}
		}
	};

	const uploadImageFileHandler = async (file: File) => {
		const tempItemId = uuidv4();
		const previewUrl = URL.createObjectURL(file);
		const fileItem = {
			type: 'image',
			id: null,
			url: '',
			name: file.name,
			size: file.size,
			content_type: file.type,
			status: 'uploading',
			error: '',
			errorTitle: '',
			errorHint: '',
			diagnostic: null,
			itemId: tempItemId,
			preview_url: previewUrl
		};

		if (fileItem.size == 0) {
			revokePreviewUrl(previewUrl);
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		files = [...files, fileItem];

		try {
			const uploadedFile = await uploadFile(localStorage.token, file, { process: false });

			if (uploadedFile) {
				if (uploadedFile.error) {
					toast.warning(
						localizeFileUploadError(uploadedFile.error, $i18n.t.bind($i18n), {
							isAdmin: $user?.role === 'admin'
						})
					);
				}

				fileItem.status = 'uploaded';
				fileItem.id = uploadedFile.id;
				fileItem.name = uploadedFile?.meta?.name ?? file.name;
				fileItem.size = uploadedFile?.meta?.size ?? file.size;
				fileItem.content_type = uploadedFile?.meta?.content_type ?? file.type;
				fileItem.url = buildUploadedImageContentUrl(uploadedFile.id);
				revokePreviewUrl(fileItem.preview_url);
				delete fileItem.preview_url;

				files = files;
			} else {
				setUploadFailure(tempItemId, new Error($i18n.t('Failed to upload file.')));
			}
		} catch (e) {
			setUploadFailure(tempItemId, e);
		}
	};

	const removeInputFile = async (fileIdx: number) => {
		const file = files[fileIdx];
		if (!file) {
			return;
		}

		if (file.itemId && file.id && file.type !== 'collection' && !file?.collection) {
			try {
				await deleteFileById(localStorage.token, file.id);
			} catch (error) {
				console.error('Failed to delete uploaded file:', error);
			}
		}

		revokePreviewUrl(file?.preview_url);
		files.splice(fileIdx, 1);
		files = files;
	};

	const uploadFileHandler = async (file) => {
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
			itemId: tempItemId
		};

		if (fileItem.size == 0) {
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		files = [...files, fileItem];

		try {
			// During the file upload, file content is automatically extracted.
			const uploadedFile = await uploadFile(localStorage.token, file);

			if (uploadedFile) {
				console.log('File upload completed:', {
					id: uploadedFile.id,
					name: fileItem.name,
					collection: uploadedFile?.meta?.collection_name
				});

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(
						localizeFileUploadError(uploadedFile.error, $i18n.t.bind($i18n), {
							isAdmin: $user?.role === 'admin'
						})
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

	const setUploadFailure = (tempItemId: string, error: unknown) => {
		const localized = getLocalizedFileUploadDiagnostic(error, $i18n.t.bind($i18n), {
			isAdmin: $user?.role === 'admin'
		});
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

		toast.error(localizeFileUploadError(error, $i18n.t.bind($i18n), { isAdmin: $user?.role === 'admin' }));
	};

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			console.log('Escape');
			dragCounter = 0;
			draggedOver = false;
		}
	};

	const onDragEnter = (e) => {
		if (!e.dataTransfer?.types?.includes('Files')) return;
		dragCounter++;
		draggedOver = true;
	};

	const onDragOver = (e) => {
		e.preventDefault();
	};

	const onDragLeave = () => {
		if (dragCounter > 0) dragCounter--;
		if (dragCounter === 0) draggedOver = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();

		dragCounter = 0;
		draggedOver = false;

		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);
			if (inputFiles && inputFiles.length > 0) {
				await inputFilesHandler(inputFiles);
			}
		}
	};

	const submitHandler = async () => {
		const uploadingFiles = files.filter((file) => file.status === 'uploading');
		if (uploadingFiles.length > 0) {
			toast.error(
				$i18n.t(`Oops! There are files still uploading. Please wait for the upload to complete.`)
			);
			return;
		}

		const failedFiles = files.filter((file) => isFailedUploadFile(file));
		const validFiles = files.filter((file) => !isFailedUploadFile(file));

		if (content === '' && validFiles.length === 0) {
			if (failedFiles.length > 0) {
				toast.warning(
					$i18n.t(
						'All selected files failed to upload or index. Remove them or upload them again before sending.'
					)
				);
			}
			return;
		}

		if (failedFiles.length > 0) {
			toast.warning(buildIgnoredFailedFilesMessage(failedFiles, $i18n.t.bind($i18n)));
		}

		onSubmit({
			content,
			data: {
				files: validFiles.map((file) => normalizeInputFileForMessage(file))
			}
		});

		content = '';
		files = failedFiles;

		await tick();
		chatInputElement?.setText?.('');
		chatInputElement?.focus?.();
	};

	$: if (content) {
		onChange();
	}

	onMount(async () => {
		suggestions = [
			{
				char: '@',
				render: getSuggestionRenderer(MentionList, {
					i18n,
					triggerChar: '@',
					userSuggestions,
					channelSuggestions: false
				})
			},
			...(channelSuggestions
				? [
						{
							char: '#',
							render: getSuggestionRenderer(MentionList, {
								i18n,
								triggerChar: '#',
								channelSuggestions: true
							})
						}
					]
				: []),
			{
				char: '/',
				render: getSuggestionRenderer(CommandSuggestionList, {
					i18n,
					onSelect: () => {
						document.getElementById(`chat-input-${id}`)?.focus();
					},
					insertTextHandler: insertTextAtCursor,
					onUpload: (event) => {
						const { type, data } = event;
						if (type === 'file') {
							if (files.find((file) => file.id === data.id)) {
								return;
							}
							files = [...files, { ...data, status: 'processed' }];
						}
					}
				})
			}
		];

		window.setTimeout(() => {
			const chatInput = document.getElementById(`chat-input-${id}`);
			chatInput?.focus();
		}, 0);

		window.addEventListener('keydown', handleKeyDown);
		await tick();

		const dropzoneElement = document.getElementById('channel-container');

		dropzoneElement?.addEventListener('dragenter', onDragEnter);
		dropzoneElement?.addEventListener('dragover', onDragOver);
		dropzoneElement?.addEventListener('drop', onDrop);
		dropzoneElement?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		console.log('destroy');
		window.removeEventListener('keydown', handleKeyDown);

		const dropzoneElement = document.getElementById('channel-container');

		if (dropzoneElement) {
			dropzoneElement?.removeEventListener('dragenter', onDragEnter);
			dropzoneElement?.removeEventListener('dragover', onDragOver);
			dropzoneElement?.removeEventListener('drop', onDrop);
			dropzoneElement?.removeEventListener('dragleave', onDragLeave);
		}

		for (const file of files) {
			revokePreviewUrl(file?.preview_url);
		}
	});
</script>

<FilesOverlay show={draggedOver} />
<InputVariablesModal
	bind:show={showInputVariablesModal}
	variables={inputVariables}
	onSave={inputVariablesModalCallback}
/>

<input
	bind:this={filesInputElement}
	bind:files={inputFiles}
	type="file"
	hidden
	multiple
	on:change={async () => {
		if (inputFiles && inputFiles.length > 0) {
			await inputFilesHandler(Array.from(inputFiles));
		} else {
			toast.error($i18n.t(`File not found.`));
		}

		filesInputElement.value = '';
	}}
/>
<div class="bg-transparent">
	<div
		class="{($settings?.widescreenMode ?? null)
			? 'max-w-full'
			: 'max-w-6xl'} px-2.5 mx-auto inset-x-0 relative"
	>
		<div class="absolute top-0 left-0 right-0 mx-auto inset-x-0 bg-transparent flex justify-center">
			<div class="flex flex-col px-3 w-full">
				<div class="relative">
					{#if scrollEnd === false}
						<div
							class=" absolute -top-12 left-0 right-0 flex justify-center z-30 pointer-events-none"
						>
							<button
								class=" bg-white border border-gray-100 dark:border-none dark:bg-white/20 p-1.5 rounded-full pointer-events-auto"
								on:click={() => {
									scrollEnd = true;
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

				<div class="relative">
					<div class=" -mt-5">
						{#if typingUsers.length > 0}
							<div class=" text-xs px-4 mb-1">
								<span class=" font-normal text-black dark:text-white">
									{typingUsers.map((user) => user.name).join(', ')}
								</span>
								{$i18n.t('is typing...')}
							</div>
						{/if}
					</div>
				</div>
			</div>
		</div>

		<div class="">
			{#if recording}
				<VoiceRecording
					bind:recording
					on:cancel={async () => {
						recording = false;

						await tick();
						document.getElementById(`chat-input-${id}`)?.focus();
					}}
					on:confirm={async (e) => {
						const { text, filename } = e.detail;
						content = `${content}${text} `;
						recording = false;

						await tick();
						document.getElementById(`chat-input-${id}`)?.focus();
					}}
				/>
			{:else}
				<form
					class="w-full flex gap-1.5"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div
						class="flex-1 flex flex-col relative w-full rounded-3xl px-1 bg-gray-600/5 dark:bg-gray-400/5 dark:text-gray-100"
						dir={$settings?.chatDirection ?? 'auto'}
					>
						{#if files.length > 0}
							<div class="mx-2 mt-2.5 -mb-1 flex flex-wrap gap-2">
								{#each files as file, fileIdx}
									{#if file.type === 'image'}
										<div class=" relative group">
											<div class="relative">
												<Image
													src={file.preview_url || file.url}
													alt="input"
													imageClassName=" h-16 w-16 rounded-xl object-cover"
												/>
											</div>
											<div class=" absolute -top-1 -right-1">
												<button
													class=" bg-white text-black border border-white rounded-full group-hover:visible invisible transition"
													type="button"
													on:click={async () => {
														await removeInputFile(fileIdx);
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
										</div>
									{:else}
										<FileItem
											item={file}
											name={file.name}
											type={file.type}
											size={file?.size}
											loading={file.status === 'uploading'}
											dismissible={true}
											edit={true}
											on:dismiss={async () => {
												await removeInputFile(fileIdx);
											}}
											on:click={() => {
												console.log(file);
											}}
										/>
									{/if}
								{/each}
							</div>
						{/if}

						<div class="px-2.5 relative">
							<div
								class="scrollbar-hidden font-primary text-left bg-transparent dark:text-gray-100 outline-hidden w-full pt-3 px-1 rounded-xl resize-none h-fit max-h-80 overflow-auto"
							>
								<RichTextInput
									bind:this={chatInputElement}
									value={content}
									id={`chat-input-${id}`}
									messageInput={true}
									showFormattingToolbar={$settings?.showFormattingToolbar ?? false}
									insertPromptAsRichText={$settings?.insertPromptAsRichText ?? false}
									shiftEnter={!$mobile ||
										!(
											'ontouchstart' in window ||
											navigator.maxTouchPoints > 0 ||
											navigator.msMaxTouchPoints > 0
										)}
									{placeholder}
									largeTextAsFile={$settings?.largeTextAsFile ?? false}
									{suggestions}
									onChange={(nextContent) => {
										content = nextContent.md;
										command = getCommand();
									}}
									on:keydown={async (e) => {
										e = e.detail.event;
										const suggestionsContainerElement =
											document.getElementById('suggestions-container');
											if (
												!suggestionsContainerElement &&
												(!$mobile ||
												!(
													'ontouchstart' in window ||
													navigator.maxTouchPoints > 0 ||
													navigator.msMaxTouchPoints > 0
												))
											) {
												if (e.keyCode === 13 && !e.shiftKey) {
													e.preventDefault();
												}

												if ((content !== '' || files.length > 0) && e.keyCode === 13 && !e.shiftKey) {
													submitHandler();
												}
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
														} catch (error) {
															console.error('HEIC paste conversion failed:', error);
															continue;
														}
													}
													await uploadImageFileHandler(
														createNamedImageFile(blob, 'Channel_Pasted_Image')
													);
												} else if (item.type === 'text/plain' && ($settings?.largeTextAsFile ?? false)) {
													const text = clipboardData.getData('text/plain');
													if (text.length > PASTED_TEXT_CHARACTER_LIMIT) {
														e.preventDefault();
														const blob = new Blob([text], { type: 'text/plain' });
														const file = new File([blob], `Pasted_Text_${Date.now()}.txt`, {
															type: 'text/plain'
														});
														await uploadFileHandler(file);
													}
												}
											}
										}
									}}
								/>
							</div>
						</div>

						<div class=" flex justify-between mb-2.5 mt-1.5 mx-0.5">
							<div class="ml-1 self-end flex space-x-1">
								<InputMenu
									{screenCaptureHandler}
									uploadFilesHandler={() => {
										filesInputElement.click();
									}}
								>
									<button
										class="bg-transparent hover:bg-white/80 text-gray-800 dark:text-white dark:hover:bg-gray-800 transition rounded-full p-1.5 outline-hidden focus:outline-hidden"
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
							</div>

							<div class="self-end flex space-x-1 mr-1">
								{#if content === ''}
									<Tooltip content={$i18n.t('Record voice')}>
										<button
											id="voice-input-button"
											class=" text-gray-600 dark:text-gray-300 hover:text-gray-700 dark:hover:text-gray-200 transition rounded-full p-1.5 mr-0.5 self-center"
											type="button"
											on:click={async () => {
												try {
													let stream = await navigator.mediaDevices
														.getUserMedia({ audio: true })
														.catch(function (err) {
															toast.error(
																$i18n.t(`Permission denied when accessing microphone: {{error}}`, {
																	error: err
																})
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
												viewBox="0 0 20 20"
												fill="currentColor"
												class="w-5 h-5 translate-y-[0.5px]"
											>
												<path d="M7 4a3 3 0 016 0v6a3 3 0 11-6 0V4z" />
												<path
													d="M5.5 9.643a.75.75 0 00-1.5 0V10c0 3.06 2.29 5.585 5.25 5.954V17.5h-1.5a.75.75 0 000 1.5h4.5a.75.75 0 000-1.5h-1.5v-1.546A6.001 6.001 0 0016 10v-.357a.75.75 0 00-1.5 0V10a4.5 4.5 0 01-9 0v-.357z"
												/>
											</svg>
										</button>
									</Tooltip>
								{/if}

								<div class=" flex items-center">
									<div class=" flex items-center">
										<Tooltip content={$i18n.t('Send message')}>
											<button
												id="send-message-button"
												class="{content !== '' || files.length !== 0
													? 'bg-black text-white hover:bg-gray-900 dark:bg-white dark:text-black dark:hover:bg-gray-100 '
													: 'text-white bg-gray-200 dark:text-gray-900 dark:bg-gray-700 disabled'} transition rounded-full p-1.5 self-center"
												type="submit"
												disabled={content === '' && files.length === 0}
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
								</div>
							</div>
						</div>
					</div>
				</form>
			{/if}
		</div>
	</div>
</div>
