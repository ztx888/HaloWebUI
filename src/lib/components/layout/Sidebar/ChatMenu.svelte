<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { getContext, createEventDispatcher, tick } from 'svelte';

	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	const dispatch = createEventDispatcher();

	import ChatPdfPreview from '$lib/components/chat/ChatPdfPreview.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { Pin, PinOff, PencilLine, Copy, Archive, Share2, Download, Trash2 } from 'lucide-svelte';
	import {
		getChatById,
		getChatPinnedStatusById,
		toggleChatPinnedStatusById
	} from '$lib/apis/chats';
	import { settings } from '$lib/stores';
	import { createMessagesList } from '$lib/utils';
	import type { ChatPdfExportMode } from '$lib/utils/chat-pdf-export';

	const i18n = getContext('i18n');

	export let shareHandler: Function;
	export let cloneChatHandler: Function;
	export let archiveChatHandler: Function;
	export let renameHandler: Function;
	export let deleteHandler: Function;
	export let onClose: Function;

	export let chatId = '';

	let show = false;
	let pinned = false;
	let showPdfPreview = false;
	let pdfPreviewMode: ChatPdfExportMode = 'stylized';
	let pdfPreviewContainer: HTMLDivElement | null = null;
	let pdfPreviewChat = null;
	let pdfPreviewDarkMode = false;

	const pinHandler = async () => {
		await toggleChatPinnedStatusById(localStorage.token, chatId);
		dispatch('change');
	};

	const checkPinned = async () => {
		pinned = await getChatPinnedStatusById(localStorage.token, chatId);
	};

	const getChatAsText = async (chat) => {
		const history = chat.chat.history;
		const messages = createMessagesList(history, history.currentId);
		const chatText = messages.reduce((a, message, i, arr) => {
			return `${a}### ${message.role.toUpperCase()}\n${message.content}\n\n`;
		}, '');

		return chatText.trim();
	};

	const downloadTxt = async () => {
		const chat = await getChatById(localStorage.token, chatId);
		if (!chat) {
			return;
		}

		const chatText = await getChatAsText(chat);
		let blob = new Blob([chatText], {
			type: 'text/plain'
		});

		saveAs(blob, `chat-${chat.chat.title}.txt`);
	};

	const loadPdfExporter = async () => {
		if (import.meta.env.DEV) {
			return await import(/* @vite-ignore */ `/src/lib/utils/chat-pdf-export.ts?t=${Date.now()}`);
		}

		return await import('$lib/utils/chat-pdf-export');
	};

	const downloadPdf = async () => {
		const targetChat = await getChatById(localStorage.token, chatId);
		if (!targetChat?.chat?.history) {
			toast.error($i18n.t('Failed to export PDF'));
			return;
		}

		pdfPreviewMode = $settings?.stylizedPdfExport ?? true ? 'stylized' : 'compact';
		pdfPreviewDarkMode =
			pdfPreviewMode === 'stylized' && document.documentElement.classList.contains('dark');
		pdfPreviewChat = targetChat;
		showPdfPreview = true;

		await tick();

		if (!pdfPreviewContainer) {
			showPdfPreview = false;
			pdfPreviewChat = null;
			toast.error($i18n.t('Failed to export PDF'));
			return;
		}

		try {
			const { exportChatPdfFromElement } = await loadPdfExporter();
			await exportChatPdfFromElement({
				sourceElement: pdfPreviewContainer,
				title: targetChat?.chat?.title,
				mode: pdfPreviewMode,
				darkMode: pdfPreviewDarkMode
			});
		} catch (error) {
			console.error('Error generating PDF', error);
			toast.error($i18n.t('Failed to export PDF'));
		} finally {
			showPdfPreview = false;
			pdfPreviewChat = null;
			pdfPreviewContainer = null;
		}
	};

	const downloadJSONExport = async () => {
		const chat = await getChatById(localStorage.token, chatId);

		if (chat) {
			let blob = new Blob([JSON.stringify([chat])], {
				type: 'application/json'
			});
			saveAs(blob, `chat-export-${Date.now()}.json`);
		}
	};

	$: if (show) {
		checkPinned();
	}
</script>

<ChatPdfPreview
	bind:container={pdfPreviewContainer}
	chat={pdfPreviewChat}
	visible={showPdfPreview}
	mode={pdfPreviewMode}
	darkMode={pdfPreviewDarkMode}
/>

<Dropdown
	bind:show
	on:change={(e) => {
		if (e.detail === false) {
			onClose();
		}
	}}
>
	<Tooltip content={$i18n.t('More')}>
		<slot />
	</Tooltip>

	<div slot="content">
		<DropdownMenu.Content
			class="select-none w-full max-w-[200px] rounded-xl px-1 py-1.5 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
			sideOffset={-2}
			side="bottom"
			align="start"
			transition={flyAndScale}
		>
			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					pinHandler();
				}}
			>
				{#if pinned}
					<PinOff class="size-4" strokeWidth={2} />
					<div class="flex items-center">{$i18n.t('Unpin')}</div>
				{:else}
					<Pin class="size-4" strokeWidth={2} />
					<div class="flex items-center">{$i18n.t('Pin')}</div>
				{/if}
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					renameHandler();
				}}
			>
				<PencilLine class="size-4" strokeWidth={2} />
				<div class="flex items-center">{$i18n.t('Rename')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					cloneChatHandler();
				}}
			>
				<Copy class="size-4" strokeWidth={2} />
				<div class="flex items-center">{$i18n.t('Clone')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					archiveChatHandler();
				}}
			>
				<Archive class="size-4" strokeWidth={2} />
				<div class="flex items-center">{$i18n.t('Archive')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					shareHandler();
				}}
			>
				<Share2 class="size-4" strokeWidth={2} />
				<div class="flex items-center">{$i18n.t('Share')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Sub>
				<DropdownMenu.SubTrigger
					class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				>
					<Download class="size-4" strokeWidth={2} />

					<div class="flex items-center">{$i18n.t('Download')}</div>
				</DropdownMenu.SubTrigger>
				<DropdownMenu.SubContent
					class="select-none w-full rounded-xl p-1 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg border border-gray-300/30 dark:border-gray-700/50"
					transition={flyAndScale}
					sideOffset={8}
				>
					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
						on:click={() => {
							downloadJSONExport();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Export chat (.json)')}</div>
					</DropdownMenu.Item>
					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
						on:click={() => {
							downloadTxt();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('Plain text (.txt)')}</div>
					</DropdownMenu.Item>

					<DropdownMenu.Item
						class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
						on:click={() => {
							downloadPdf();
						}}
					>
						<div class="flex items-center line-clamp-1">{$i18n.t('PDF document (.pdf)')}</div>
					</DropdownMenu.Item>
				</DropdownMenu.SubContent>
			</DropdownMenu.Sub>
			<hr class="border-gray-100 dark:border-gray-800 my-1" />
			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-2 text-sm cursor-pointer text-red-500 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/50 rounded-md"
				on:click={() => {
					deleteHandler();
				}}
			>
				<Trash2 class="size-4" strokeWidth={2} />
				<div class="flex items-center">{$i18n.t('Delete')}</div>
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	</div>
</Dropdown>
