<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { folders } from '$lib/stores';
	import { getFolders } from '$lib/apis/folders';
	import { searchKnowledgeBases, searchKnowledgeFiles } from '$lib/apis/knowledge';
	import { getNoteById, getNotes } from '$lib/apis/notes';
	import {
		isValidHttpUrl,
		isYoutubeUrl
	} from '$lib/utils';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/Document.svelte';
	import Database from '$lib/components/icons/BookOpen.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Youtube from '$lib/components/icons/GlobeAlt.svelte';
	import Folder from '$lib/components/icons/FolderOpen.svelte';
	import Bookmark from '$lib/components/icons/Bookmark.svelte';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	export let query = '';
	export let onSelect = (_event) => {};
	export let filteredItems = [];

	let selectedIdx = 0;
	let items = [];
	let folderItems = [];
	let knowledgeItems = [];
	let fileItems = [];
	let noteItems = [];
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	const decodeString = (str: string) => {
		try {
			return decodeURIComponent(str);
		} catch {
			return str;
		}
	};

	$: items = [...folderItems, ...knowledgeItems, ...fileItems, ...noteItems];
	$: filteredItems = [
		...(query.startsWith('http')
			? isYoutubeUrl(query)
				? [{ type: 'youtube', name: query, description: query }]
				: [{ type: 'web', name: query, description: query }]
			: []),
		...items
	];

	$: if (query) {
		selectedIdx = 0;
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
	};

	const selectKnowledgeItem = async (item) => {
		if (!item) {
			return;
		}

		if (item.type === 'note') {
			const fullNote = await getNoteById(localStorage.token, item.id).catch(() => null);
			onSelect({
				type: 'knowledge',
				data:
					fullNote !== null
						? {
								id: `note-${fullNote.id}`,
								name: fullNote.title,
								type: 'note',
								docs: [
									{
										content: fullNote.content,
										metadata: {
											source: `note:${fullNote.title}`,
											name: fullNote.title
										}
									}
								],
								status: 'processed'
							}
						: {
								id: `note-${item.id}`,
								name: item.name,
								type: 'note',
								docs: [],
								status: 'processed'
							}
			});
			return;
		}

		onSelect({
			type: 'knowledge',
			data: item
		});
	};

	export const select = async () => {
		const item = filteredItems[selectedIdx];
		if (!item) {
			return;
		}

		if (item.type === 'youtube' || item.type === 'web') {
			onSelect({
				type: 'web',
				data: item.name
			});
			return;
		}

		await selectKnowledgeItem(item);
	};

	const getItems = () => {
		getFolderItems();
		getKnowledgeItems();
		getKnowledgeFileItems();
	};

	const getFolderItems = async () => {
		folderItems = ($folders ?? [])
			.map((folder) => ({
				...folder,
				type: 'folder',
				description: 'Folder',
				title: folder.name
			}))
			.filter((folder) => folder.name.toLowerCase().includes(query.toLowerCase()));
	};

	const getKnowledgeItems = async () => {
		const res = await searchKnowledgeBases(localStorage.token, query).catch(() => null);
		if (res) {
			knowledgeItems = (res.items ?? []).map((item) => ({
				...item,
				type: 'collection'
			}));
		}
	};

	const getKnowledgeFileItems = async () => {
		const res = await searchKnowledgeFiles(localStorage.token, query).catch(() => null);
		if (res) {
			fileItems = (res.items ?? []).map((item) => ({
				...item,
				type: 'file',
				name: item.filename ?? item.name,
				description: item.collection ? item.collection.name : ''
			}));
		}
	};

	const getNoteItems = async () => {
		const res = await getNotes(localStorage.token).catch(() => []);
		noteItems = (res ?? [])
			.filter((item) => {
				const haystack = `${item?.title ?? ''} ${item?.content ?? ''}`.toLowerCase();
				return haystack.includes(query.toLowerCase());
			})
			.map((item) => ({
				id: item.id,
				name: item.title,
				description: (item.content ?? '').replace(/\s+/g, ' ').trim().slice(0, 120),
				type: 'note'
			}));
	};

	onMount(async () => {
		if ($folders === null) {
			folders.set(await getFolders(localStorage.token));
		}
		getItems();
		await getNoteItems();
		await tick();
	});

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getItems();
			getNoteItems();
		}, 200);
	}
</script>

{#if filteredItems.length > 0 || query.startsWith('http')}
	{#each filteredItems as item, idx}
		{#if idx === 0 || item?.type !== filteredItems[idx - 1]?.type}
			<div class="px-2 text-xs text-gray-500 py-1">
				{#if item?.type === 'folder'}
					{$i18n.t('Folders')}
				{:else if item?.type === 'collection'}
					{$i18n.t('Collections')}
				{:else if item?.type === 'file'}
					{$i18n.t('Files')}
				{:else if item?.type === 'note'}
					{$i18n.t('Notes')}
				{/if}
			</div>
		{/if}

		{#if !['youtube', 'web'].includes(item.type)}
			<button
				class="px-2 py-1 rounded-xl w-full text-left flex justify-between items-center {idx ===
				selectedIdx
					? 'bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button'
					: ''}"
				type="button"
				data-selected={idx === selectedIdx}
				on:click={() => void selectKnowledgeItem(item)}
				on:mousemove={() => {
					selectedIdx = idx;
				}}
			>
				<div class="text-black dark:text-gray-100 flex items-center gap-1">
					<Tooltip
						content={item?.type === 'file'
							? `${item?.collection?.name ?? ''} > ${$i18n.t('File')}`
							: item?.type === 'collection'
								? $i18n.t('Collection')
								: item?.type === 'note'
									? $i18n.t('Note')
									: $i18n.t('Folder')}
						placement="top"
					>
						{#if item?.type === 'collection'}
							<Database className="size-4" />
						{:else if item?.type === 'folder'}
							<Folder className="size-4" />
						{:else if item?.type === 'note'}
							<Bookmark className="size-4" />
						{:else}
							<DocumentPage className="size-4" />
						{/if}
					</Tooltip>

					<Tooltip content={decodeString(item?.name)} placement="top-start">
						<div class="line-clamp-1 flex-1">{decodeString(item?.name)}</div>
					</Tooltip>
				</div>
			</button>
		{/if}
	{/each}

	{#if isYoutubeUrl(query)}
		<button
			class="px-2 py-1 rounded-xl w-full text-left bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button"
			type="button"
			data-selected={selectedIdx === filteredItems.findIndex((item) => item.type === 'youtube')}
			on:click={() => {
				if (isValidHttpUrl(query)) {
					onSelect({ type: 'web', data: query });
				} else {
					toast.error(
						$i18n.t('Oops! Looks like the URL is invalid. Please double-check and try again.')
					);
				}
			}}
		>
			<div class="text-black dark:text-gray-100 line-clamp-1 flex items-center gap-1">
				<Tooltip content={$i18n.t('YouTube')} placement="top">
					<Youtube className="size-4" />
				</Tooltip>
				<div class="truncate flex-1">{query}</div>
			</div>
		</button>
	{:else if query.startsWith('http')}
		<button
			class="px-2 py-1 rounded-xl w-full text-left bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button"
			type="button"
			data-selected={selectedIdx === filteredItems.findIndex((item) => item.type === 'web')}
			on:click={() => {
				if (isValidHttpUrl(query)) {
					onSelect({ type: 'web', data: query });
				} else {
					toast.error(
						$i18n.t('Oops! Looks like the URL is invalid. Please double-check and try again.')
					);
				}
			}}
		>
			<div class="text-black dark:text-gray-100 line-clamp-1 flex items-center gap-1">
				<Tooltip content={$i18n.t('Web')} placement="top">
					<GlobeAlt className="size-4" />
				</Tooltip>
				<div class="truncate flex-1">{query}</div>
			</div>
		</button>
	{/if}
{/if}
