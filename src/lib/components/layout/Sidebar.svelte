<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { v4 as uuidv4 } from 'uuid';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		user,
		chats,
		settings,
		chatId,
		tags,
		showSidebar,
		mobile,
		showArchivedChats,
		pinnedChats,
		scrollPaginationEnabled,
		currentChatPage,
		temporaryChatEnabled,
		channels,
		socket,
		config,
		isApp
	} from '$lib/stores';
	import { onMount, getContext, tick, onDestroy } from 'svelte';

	const i18n = getContext('i18n');

	import {
		deleteChatById,
		getChatList,
		getAllTags,
		getChatListBySearchText,
		createNewChat,
		getPinnedChatList,
		toggleChatPinnedStatusById,
		getChatPinnedStatusById,
		getChatById,
		updateChatFolderIdById,
		importChat
	} from '$lib/apis/chats';
	import { createNewFolder, getFolders, updateFolderParentIdById } from '$lib/apis/folders';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import ArchivedChatsModal from './Sidebar/ArchivedChatsModal.svelte';
	import UserMenu from './Sidebar/UserMenu.svelte';
	import ChatItem from './Sidebar/ChatItem.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Loader from '../common/Loader.svelte';
	import AddFilesPlaceholder from '../AddFilesPlaceholder.svelte';
	import SearchInput from './Sidebar/SearchInput.svelte';
	import Folder from '../common/Folder.svelte';
	import Plus from '../icons/Plus.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Folders from './Sidebar/Folders.svelte';
	import { getChannels, createNewChannel } from '$lib/apis/channels';
	import ChannelModal from './Sidebar/ChannelModal.svelte';
	import ChannelItem from './Sidebar/ChannelItem.svelte';
	import ChatBubblePlus from '../icons/ChatBubblePlus.svelte';
	import Home from '../icons/Home.svelte';
	import Search from '../icons/Search.svelte';
	import ArchiveBox from '../icons/ArchiveBox.svelte';

	const BREAKPOINT = 768;

	type SidebarStyle = 'flat' | 'card';
	const SIDEBAR_STYLE_QUERY_KEY = 'sidebarStyle';

	const normalizeSidebarStyle = (style: string | null): SidebarStyle | null => {
		if (style === 'flat' || style === 'card') return style;
		return null;
	};

	let sidebarStyle: SidebarStyle = 'flat';

	$: {
		const fromQuery = normalizeSidebarStyle(
			$page?.url?.searchParams?.get(SIDEBAR_STYLE_QUERY_KEY) ?? null
		);
		sidebarStyle = fromQuery ?? 'flat';
	}

	$: iconButtonClass =
		sidebarStyle === 'card'
			? 'group flex items-center justify-center w-11 h-11 rounded-2xl bg-white/85 dark:bg-gray-900/55 border border-gray-200/70 dark:border-gray-800/70 shadow-sm hover:bg-white dark:hover:bg-gray-900/75 hover:shadow-md transition active:scale-[0.98]'
			: 'group flex items-center justify-center w-11 h-11 rounded-xl bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 transition active:scale-[0.98]';

	$: actionItemClass =
		sidebarStyle === 'card'
			? 'flex items-center gap-2.5 rounded-2xl bg-white/70 dark:bg-gray-900/45 border border-gray-200/60 dark:border-gray-800/60 shadow-sm hover:bg-white/85 dark:hover:bg-gray-900/60 hover:shadow-md transition w-full px-3 py-2'
			: 'flex items-center gap-2.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition w-full px-3 py-2';

	$: userItemClass =
		sidebarStyle === 'card'
			? 'flex items-center rounded-2xl bg-white/70 dark:bg-gray-900/45 border border-gray-200/60 dark:border-gray-800/60 hover:bg-white/85 dark:hover:bg-gray-900/60 transition w-full px-2 py-2'
			: 'flex items-center rounded-xl py-2 px-2 w-full hover:bg-gray-100 dark:hover:bg-gray-850 transition';

	$: avatarContainerClass =
		sidebarStyle === 'card'
			? 'shrink-0 w-9 h-9 rounded-full overflow-hidden ring-2 ring-gray-200 dark:ring-gray-700 group-hover:ring-blue-300 dark:group-hover:ring-blue-600 transition'
			: 'shrink-0 w-8 h-8 rounded-full overflow-hidden ring-1 ring-gray-200 dark:ring-gray-700 group-hover:ring-blue-300 dark:group-hover:ring-blue-600 transition';

	$: brandLinkClass =
		sidebarStyle === 'card'
			? 'flex items-center gap-2 px-2 py-1.5 rounded-2xl bg-white/60 dark:bg-gray-900/40 border border-gray-200/60 dark:border-gray-800/60 hover:bg-white/80 dark:hover:bg-gray-900/55 transition'
			: 'flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition';

	let navElement;
	let search = '';

	let shiftKey = false;

	let selectedChatId = null;
	let showDropdown = false;
	let showPinnedChat = true;

	let showCreateChannel = false;

	// Pagination variables
	let chatListLoading = false;
	let allChatsLoaded = false;

	let folders = {};
	let newFolderId = null;

	const initFolders = async () => {
		const folderList = await getFolders(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return [];
		});

		folders = {};

		// First pass: Initialize all folder entries
		for (const folder of folderList) {
			// Ensure folder is added to folders with its data
			folders[folder.id] = { ...(folders[folder.id] || {}), ...folder };

			if (newFolderId && folder.id === newFolderId) {
				folders[folder.id].new = true;
				newFolderId = null;
			}
		}

		// Second pass: Tie child folders to their parents
		for (const folder of folderList) {
			if (folder.parent_id) {
				// Ensure the parent folder is initialized if it doesn't exist
				if (!folders[folder.parent_id]) {
					folders[folder.parent_id] = {}; // Create a placeholder if not already present
				}

				// Initialize childrenIds array if it doesn't exist and add the current folder id
				folders[folder.parent_id].childrenIds = folders[folder.parent_id].childrenIds
					? [...folders[folder.parent_id].childrenIds, folder.id]
					: [folder.id];

				// Sort the children by updated_at field
				folders[folder.parent_id].childrenIds.sort((a, b) => {
					return folders[b].updated_at - folders[a].updated_at;
				});
			}
		}
	};

	const createFolder = async (name = 'Untitled') => {
		if (name === '') {
			toast.error($i18n.t('Folder name cannot be empty.'));
			return;
		}

		const rootFolders = Object.values(folders).filter((folder) => folder.parent_id === null);
		if (rootFolders.find((folder) => folder.name.toLowerCase() === name.toLowerCase())) {
			// If a folder with the same name already exists, append a number to the name
			let i = 1;
			while (
				rootFolders.find((folder) => folder.name.toLowerCase() === `${name} ${i}`.toLowerCase())
			) {
				i++;
			}

			name = `${name} ${i}`;
		}

		// Add a dummy folder to the list to show the user that the folder is being created
		const tempId = uuidv4();
		folders = {
			...folders,
			tempId: {
				id: tempId,
				name: name,
				created_at: Date.now(),
				updated_at: Date.now()
			}
		};

		const res = await createNewFolder(localStorage.token, name).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			newFolderId = res.id;
			await initFolders();
		}
	};

	const initChannels = async () => {
		await channels.set(await getChannels(localStorage.token));
	};

	const initChatList = async () => {
		// Reset pagination variables
		tags.set(await getAllTags(localStorage.token));
		pinnedChats.set(await getPinnedChatList(localStorage.token));
		initFolders();

		currentChatPage.set(1);
		allChatsLoaded = false;

		if (search) {
			await chats.set(await getChatListBySearchText(localStorage.token, search, $currentChatPage));
		} else {
			await chats.set(await getChatList(localStorage.token, $currentChatPage));
		}

		// Enable pagination
		scrollPaginationEnabled.set(true);
	};

	const loadMoreChats = async () => {
		chatListLoading = true;

		currentChatPage.set($currentChatPage + 1);

		let newChatList = [];

		if (search) {
			newChatList = await getChatListBySearchText(localStorage.token, search, $currentChatPage);
		} else {
			newChatList = await getChatList(localStorage.token, $currentChatPage);
		}

		// once the bottom of the list has been reached (no results) there is no need to continue querying
		allChatsLoaded = newChatList.length === 0;
		await chats.set([...($chats ? $chats : []), ...newChatList]);

		chatListLoading = false;
	};

	let searchDebounceTimeout;

	const searchDebounceHandler = async () => {
		console.log('search', search);
		chats.set(null);

		if (searchDebounceTimeout) {
			clearTimeout(searchDebounceTimeout);
		}

		if (search === '') {
			await initChatList();
			return;
		} else {
			searchDebounceTimeout = setTimeout(async () => {
				allChatsLoaded = false;
				currentChatPage.set(1);
				await chats.set(await getChatListBySearchText(localStorage.token, search));

				if ($chats.length === 0) {
					tags.set(await getAllTags(localStorage.token));
				}
			}, 1000);
		}
	};

	const importChatHandler = async (items, pinned = false, folderId = null) => {
		console.log('importChatHandler', items, pinned, folderId);
		for (const item of items) {
			console.log(item);
			if (item.chat) {
				await importChat(localStorage.token, item.chat, item?.meta ?? {}, pinned, folderId);
			}
		}

		initChatList();
	};

	const inputFilesHandler = async (files) => {
		console.log(files);

		for (const file of files) {
			const reader = new FileReader();
			reader.onload = async (e) => {
				const content = e.target.result;

				try {
					const chatItems = JSON.parse(content);
					importChatHandler(chatItems);
				} catch {
					toast.error($i18n.t(`Invalid file format.`));
				}
			};

			reader.readAsText(file);
		}
	};

	const tagEventHandler = async (type, tagName, chatId) => {
		console.log(type, tagName, chatId);
		if (type === 'delete') {
			initChatList();
		} else if (type === 'add') {
			initChatList();
		}
	};

	let draggedOver = false;

	const onDragOver = (e) => {
		e.preventDefault();

		// Check if a file is being draggedOver.
		if (e.dataTransfer?.types?.includes('Files')) {
			draggedOver = true;
		} else {
			draggedOver = false;
		}
	};

	const onDragLeave = () => {
		draggedOver = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();
		console.log(e); // Log the drop event

		// Perform file drop check and handle it accordingly
		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);

			if (inputFiles && inputFiles.length > 0) {
				console.log(inputFiles); // Log the dropped files
				inputFilesHandler(inputFiles); // Handle the dropped files
			}
		}

		draggedOver = false; // Reset draggedOver status after drop
	};

	let touchstart;
	let touchend;

	function checkDirection() {
		const screenWidth = window.innerWidth;
		const swipeDistance = Math.abs(touchend.screenX - touchstart.screenX);
		if (touchstart.clientX < 40 && swipeDistance >= screenWidth / 8) {
			if (touchend.screenX < touchstart.screenX) {
				showSidebar.set(false);
			}
			if (touchend.screenX > touchstart.screenX) {
				showSidebar.set(true);
			}
		}
	}

	const onTouchStart = (e) => {
		touchstart = e.changedTouches[0];
		console.log(touchstart.clientX);
	};

	const onTouchEnd = (e) => {
		touchend = e.changedTouches[0];
		checkDirection();
	};

	const onKeyDown = (e) => {
		if (e.key === 'Shift') {
			shiftKey = true;
		}
	};

	const onKeyUp = (e) => {
		if (e.key === 'Shift') {
			shiftKey = false;
		}
	};

	const onFocus = () => {};

	const onBlur = () => {
		shiftKey = false;
		selectedChatId = null;
	};

	onMount(async () => {
		showPinnedChat = localStorage?.showPinnedChat ? localStorage.showPinnedChat === 'true' : true;

		mobile.subscribe((value) => {
			if ($showSidebar && value) {
				showSidebar.set(false);
			}

			if ($showSidebar && !value) {
				const navElement = document.getElementsByTagName('nav')[0];
				if (navElement) {
					navElement.style['-webkit-app-region'] = 'drag';
				}
			}

			if (!$showSidebar && !value) {
				showSidebar.set(true);
			}
		});

		showSidebar.set(!$mobile ? localStorage.sidebar === 'true' : false);
		showSidebar.subscribe((value) => {
			localStorage.sidebar = value;

			// nav element is not available on the first render
			const navElement = document.getElementsByTagName('nav')[0];

			if (navElement) {
				if ($mobile) {
					if (!value) {
						navElement.style['-webkit-app-region'] = 'drag';
					} else {
						navElement.style['-webkit-app-region'] = 'no-drag';
					}
				} else {
					navElement.style['-webkit-app-region'] = 'drag';
				}
			}
		});

		await initChannels();
		await initChatList();

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);

		window.addEventListener('touchstart', onTouchStart);
		window.addEventListener('touchend', onTouchEnd);

		window.addEventListener('focus', onFocus);
		window.addEventListener('blur-sm', onBlur);

		const dropZone = document.getElementById('sidebar');

		dropZone?.addEventListener('dragover', onDragOver);
		dropZone?.addEventListener('drop', onDrop);
		dropZone?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		window.removeEventListener('keydown', onKeyDown);
		window.removeEventListener('keyup', onKeyUp);

		window.removeEventListener('touchstart', onTouchStart);
		window.removeEventListener('touchend', onTouchEnd);

		window.removeEventListener('focus', onFocus);
		window.removeEventListener('blur-sm', onBlur);

		const dropZone = document.getElementById('sidebar');

		dropZone?.removeEventListener('dragover', onDragOver);
		dropZone?.removeEventListener('drop', onDrop);
		dropZone?.removeEventListener('dragleave', onDragLeave);
	});
</script>

<ArchivedChatsModal
	bind:show={$showArchivedChats}
	on:change={async () => {
		await initChatList();
	}}
/>

<ChannelModal
	bind:show={showCreateChannel}
	onSubmit={async ({ name, access_control }) => {
		const res = await createNewChannel(localStorage.token, {
			name: name,
			access_control: access_control
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			$socket.emit('join-channels', { auth: { token: $user?.token } });
			await initChannels();
			showCreateChannel = false;
		}
	}}
/>

<!-- svelte-ignore a11y-no-static-element-interactions -->

<!-- 移动端遮罩层 -->
{#if $showSidebar && $mobile}
	<div
		class=" {$isApp
			? ' ml-[4.5rem] md:ml-0'
			: ''} fixed md:hidden z-40 top-0 right-0 left-0 bottom-0 bg-black/60 w-full min-h-screen h-screen flex justify-center overflow-hidden overscroll-contain"
		on:mousedown={() => {
			showSidebar.set(false);
		}}
	/>
{/if}

<div
	bind:this={navElement}
	id="sidebar"
	role="navigation"
	aria-label="Chat sidebar"
	class="h-screen max-h-[100dvh] min-h-screen select-none
		{$isApp ? `ml-[4.5rem] md:ml-0 ` : ''}
		shrink-0 bg-gray-50/80 dark:bg-[#0a0a0f]/80 backdrop-blur-xl border-r border-gray-200/50 dark:border-gray-800/50 text-gray-900 dark:text-gray-200
		text-sm fixed md:relative z-50 top-0 left-0 overflow-hidden transform-gpu transition-[width,max-width,transform] duration-300 ease-in-out
		will-change-transform {!$mobile
		? $showSidebar
			? 'w-[260px] max-w-[260px] translate-x-0'
			: 'w-[60px] max-w-[60px] translate-x-0'
		: $showSidebar
			? 'w-[260px] max-w-[260px] translate-x-0'
			: 'w-[0px] -translate-x-[260px]'}"
	style="will-change: width, transform;"
	data-state={$showSidebar ? 'expanded' : $mobile ? 'hidden' : 'collapsed'}
	data-style={sidebarStyle}
>
	<div
		class="py-2 flex flex-col h-screen max-h-[100dvh] overflow-x-hidden z-50 transition-all duration-300 ease-in-out
			{$showSidebar || $mobile ? 'w-[260px]' : 'w-[60px]'}"
	>
		<!-- 顶栏：Logo + 折叠按钮 -->
		<div
			class="shrink-0 flex items-center justify-between px-2 {$showSidebar || $mobile
				? ''
				: 'flex-col gap-2'}"
		>
			{#if $showSidebar || $mobile}
				<!-- 展开状态：Logo左边，折叠按钮右边 -->
				<a
					href="/?fresh-chat=true"
					class={brandLinkClass}
					on:click|preventDefault={async () => {
						selectedChatId = null;
						await goto('/?fresh-chat=true');
						if ($mobile) {
							showSidebar.set(false);
						}
					}}
					draggable="false"
				>
					<svg class="size-6" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
						<path
							class="fill-none stroke-gray-900 dark:stroke-white"
							style="stroke-width: 14; stroke-linecap: round;"
							d="M60 17 A43 43 0 1 1 17 60"
						/>
						<circle class="fill-gray-900 dark:fill-white" cx="60" cy="60" r="13" />
					</svg>
					<span class="font-semibold text-sm text-gray-800 dark:text-gray-100 whitespace-nowrap"
						>Halo WebUI</span
					>
				</a>
				<Tooltip content={$i18n.t($showSidebar ? 'Collapse sidebar' : 'Expand sidebar')}>
					<button
						class={iconButtonClass}
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label={$i18n.t($showSidebar ? 'Collapse sidebar' : 'Expand sidebar')}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="1.75"
							stroke="currentColor"
							class="size-5"
						>
							<rect x="3" y="4" width="18" height="16" rx="2" />
							<path stroke-linecap="round" stroke-linejoin="round" d="M9 4v16" />
							{#if $showSidebar}
								<path stroke-linecap="round" stroke-linejoin="round" d="M16 8l-3 4 3 4" />
							{:else}
								<path stroke-linecap="round" stroke-linejoin="round" d="M12 8l3 4-3 4" />
							{/if}
						</svg>
					</button>
				</Tooltip>
			{:else}
				<!-- 折叠状态：垂直图标 -->
				<Tooltip content={$i18n.t($showSidebar ? 'Collapse sidebar' : 'Expand sidebar')}>
					<button
						class={iconButtonClass}
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label={$i18n.t($showSidebar ? 'Collapse sidebar' : 'Expand sidebar')}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="1.75"
							stroke="currentColor"
							class="size-5"
						>
							<rect x="3" y="4" width="18" height="16" rx="2" />
							<path stroke-linecap="round" stroke-linejoin="round" d="M9 4v16" />
							{#if $showSidebar}
								<path stroke-linecap="round" stroke-linejoin="round" d="M16 8l-3 4 3 4" />
							{:else}
								<path stroke-linecap="round" stroke-linejoin="round" d="M12 8l3 4-3 4" />
							{/if}
						</svg>
					</button>
				</Tooltip>
			{/if}
		</div>

		{#if $showSidebar || $mobile}
			<!-- 新对话：独立一行 -->
			<div class="flex text-gray-700 dark:text-gray-200 px-2 mt-1">
				<a
					id="sidebar-new-chat-button"
					class={actionItemClass + ' no-drag-region'}
					href="/"
					draggable="false"
					aria-label={$i18n.t('New Chat')}
					on:click={async () => {
						selectedChatId = null;
						await goto('/');
						const newChatButton = document.getElementById('new-chat-button');
						setTimeout(() => {
							newChatButton?.click();
							if ($mobile) {
								showSidebar.set(false);
							}
						}, 0);
					}}
				>
					<ChatBubblePlus className="size-5" strokeWidth="2" />
					<span class="text-sm font-medium whitespace-nowrap">{$i18n.t('New Chat')}</span>
				</a>
			</div>
		{/if}

		<!-- {#if $user?.role === 'admin'}
			<div class="px-1.5 flex justify-center text-gray-800 dark:text-gray-200">
				<a
					class="grow flex items-center space-x-3 rounded-lg px-2 py-[7px] hover:bg-gray-100 dark:hover:bg-gray-900 transition"
					href="/home"
					on:click={() => {
						selectedChatId = null;
						chatId.set('');

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
					draggable="false"
				>
					<div class="self-center">
						<Home strokeWidth="2" className="size-[1.1rem]" />
					</div>

					<div class="flex self-center translate-y-[0.5px]">
						<div class=" self-center font-medium text-sm font-primary">{$i18n.t('Home')}</div>
					</div>
				</a>
			</div>
		{/if} -->

		{#if ($user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools) && ($showSidebar || $mobile)}
			<div class="flex text-gray-700 dark:text-gray-200 px-2">
				<a
					class={actionItemClass}
					href="/workspace"
					on:click={() => {
						selectedChatId = null;
						chatId.set('');

						if ($mobile) {
							showSidebar.set(false);
						}
					}}
					draggable="false"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						stroke-linecap="round"
						stroke-linejoin="round"
						class="size-5"
					>
						<path d="M12 12h.01" />
						<path d="M16 6V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
						<path d="M22 13a18.15 18.15 0 0 1-20 0" />
						<rect width="20" height="14" x="2" y="6" rx="2" />
					</svg>
					<span class="text-sm font-medium whitespace-nowrap">{$i18n.t('Workspace')}</span>
				</a>
			</div>
		{/if}

		{#if !$showSidebar && !$mobile}
			<div class="mt-3 px-2 flex flex-col items-center gap-2 text-gray-700 dark:text-gray-200">
				<Tooltip content={$i18n.t('New Chat')}>
					<a
						id="sidebar-new-chat-button"
						class={iconButtonClass + ' no-drag-region'}
						href="/"
						draggable="false"
						on:click={async () => {
							selectedChatId = null;
							await goto('/');
							const newChatButton = document.getElementById('new-chat-button');
							setTimeout(() => {
								newChatButton?.click();
							}, 0);
						}}
					>
						<ChatBubblePlus className="size-5" strokeWidth="2" />
					</a>
				</Tooltip>

				{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models || $user?.permissions?.workspace?.knowledge || $user?.permissions?.workspace?.prompts || $user?.permissions?.workspace?.tools}
					<Tooltip content={$i18n.t('Workspace')}>
						<a
							class={iconButtonClass}
							href="/workspace"
							on:click={() => {
								selectedChatId = null;
								chatId.set('');
							}}
							draggable="false"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="2"
								stroke="currentColor"
								stroke-linecap="round"
								stroke-linejoin="round"
								class="size-5"
							>
								<path d="M12 12h.01" />
								<path d="M16 6V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
								<path d="M22 13a18.15 18.15 0 0 1-20 0" />
								<rect width="20" height="14" x="2" y="6" rx="2" />
							</svg>
						</a>
					</Tooltip>
				{/if}

				<div class="w-full h-px bg-gray-200/70 dark:bg-gray-800/70 my-1" />

				<Tooltip content={$i18n.t('Search')}>
					<button
						class={iconButtonClass}
						on:click={async () => {
							showSidebar.set(true);
							await tick();
							document.querySelector('#chat-search input')?.focus();
						}}
						aria-label={$i18n.t('Search')}
					>
						<Search className="size-5" strokeWidth="2" />
					</button>
				</Tooltip>

				<Tooltip content={$i18n.t('Archived Chats')}>
					<button
						class={iconButtonClass}
						on:click={() => {
							showArchivedChats.set(true);
						}}
						aria-label={$i18n.t('Archived Chats')}
					>
						<ArchiveBox className="size-5" strokeWidth="2" />
					</button>
				</Tooltip>
			</div>
		{/if}

		{#if $showSidebar || $mobile}
			<div class="shrink-0 relative {$temporaryChatEnabled ? 'opacity-20' : ''}">
				{#if $temporaryChatEnabled}
					<div class="absolute z-40 w-full h-full flex justify-center"></div>
				{/if}

				<SearchInput
					bind:value={search}
					uiStyle={sidebarStyle}
					on:input={searchDebounceHandler}
					placeholder={$i18n.t('Search')}
					showClearButton={true}
				/>
			</div>
		{/if}

		{#if $showSidebar || $mobile}
			<div
				class="sidebar-scroll relative flex flex-col flex-1 overflow-y-auto overflow-x-hidden {$temporaryChatEnabled
					? 'opacity-20'
					: ''}"
			>
				{#if $config?.features?.enable_channels && ($user?.role === 'admin' || $channels.length > 0) && !search}
					<Folder
						className="px-2 mt-0.5"
						name={$i18n.t('Channels')}
						dragAndDrop={false}
						onAdd={async () => {
							if ($user?.role === 'admin') {
								await tick();

								setTimeout(() => {
									showCreateChannel = true;
								}, 0);
							}
						}}
						onAddLabel={$i18n.t('Create Channel')}
					>
						{#each $channels as channel}
							<ChannelItem
								{channel}
								onUpdate={async () => {
									await initChannels();
								}}
							/>
						{/each}
					</Folder>
				{/if}

				<Folder
					collapsible={!search}
					className="px-2 mt-0.5"
					name={$i18n.t('Chats')}
					onAdd={() => {
						createFolder();
					}}
					onAddLabel={$i18n.t('New Folder')}
					on:import={(e) => {
						importChatHandler(e.detail);
					}}
					on:drop={async (e) => {
						const { type, id, item } = e.detail;

						if (type === 'chat') {
							let chat = await getChatById(localStorage.token, id).catch((error) => {
								return null;
							});
							if (!chat && item) {
								chat = await importChat(localStorage.token, item.chat, item?.meta ?? {});
							}

							if (chat) {
								console.log(chat);
								if (chat.folder_id) {
									const res = await updateChatFolderIdById(localStorage.token, chat.id, null).catch(
										(error) => {
											toast.error(`${error}`);
											return null;
										}
									);
								}

								if (chat.pinned) {
									const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
								}

								initChatList();
							}
						} else if (type === 'folder') {
							if (folders[id].parent_id === null) {
								return;
							}

							const res = await updateFolderParentIdById(localStorage.token, id, null).catch(
								(error) => {
									toast.error(`${error}`);
									return null;
								}
							);

							if (res) {
								await initFolders();
							}
						}
					}}
				>
					{#if $temporaryChatEnabled}
						<div class="absolute z-40 w-full h-full flex justify-center"></div>
					{/if}

					{#if !search && $pinnedChats.length > 0}
						<div class="flex flex-col space-y-1 rounded-xl">
							<Folder
								className=""
								bind:open={showPinnedChat}
								on:change={(e) => {
									localStorage.setItem('showPinnedChat', e.detail);
									console.log(e.detail);
								}}
								on:import={(e) => {
									importChatHandler(e.detail, true);
								}}
								on:drop={async (e) => {
									const { type, id, item } = e.detail;

									if (type === 'chat') {
										let chat = await getChatById(localStorage.token, id).catch((error) => {
											return null;
										});
										if (!chat && item) {
											chat = await importChat(localStorage.token, item.chat, item?.meta ?? {});
										}

										if (chat) {
											console.log(chat);
											if (chat.folder_id) {
												const res = await updateChatFolderIdById(
													localStorage.token,
													chat.id,
													null
												).catch((error) => {
													toast.error(`${error}`);
													return null;
												});
											}

											if (!chat.pinned) {
												const res = await toggleChatPinnedStatusById(localStorage.token, chat.id);
											}

											initChatList();
										}
									}
								}}
								name={$i18n.t('Pinned')}
							>
								<div
									class="ml-3 pl-1 mt-[1px] flex flex-col overflow-y-auto scrollbar-hidden border-s border-gray-100 dark:border-gray-900"
								>
									{#each $pinnedChats as chat, idx}
										<ChatItem
											className=""
											uiStyle={sidebarStyle}
											id={chat.id}
											title={chat.title}
											{shiftKey}
											selected={selectedChatId === chat.id}
											on:select={() => {
												selectedChatId = chat.id;
											}}
											on:unselect={() => {
												selectedChatId = null;
											}}
											on:change={async () => {
												initChatList();
											}}
											on:tag={(e) => {
												const { type, name } = e.detail;
												tagEventHandler(type, name, chat.id);
											}}
										/>
									{/each}
								</div>
							</Folder>
						</div>
					{/if}

					{#if !search && folders}
						<Folders
							{folders}
							uiStyle={sidebarStyle}
							on:import={(e) => {
								const { folderId, items } = e.detail;
								importChatHandler(items, false, folderId);
							}}
							on:update={async (e) => {
								initChatList();
							}}
							on:change={async () => {
								initChatList();
							}}
						/>
					{/if}

					<div class=" flex-1 flex flex-col overflow-y-auto scrollbar-hidden">
						<div class="pt-1.5">
							{#if $chats}
								{#each $chats as chat, idx}
									{#if idx === 0 || (idx > 0 && chat.time_range !== $chats[idx - 1].time_range)}
										{#if idx !== 0}
											<div class="mx-2 my-2 border-t border-gray-200/60 dark:border-gray-800"></div>
										{/if}
										<div
											class="w-full px-3 py-1.5 text-[11px] text-gray-400 dark:text-gray-500 font-semibold uppercase tracking-wider"
										>
											{$i18n.t(chat.time_range)}
											<!-- localisation keys for time_range to be recognized from the i18next parser (so they don't get automatically removed):
							{$i18n.t('Today')}
							{$i18n.t('Yesterday')}
							{$i18n.t('Previous 7 days')}
							{$i18n.t('Previous 30 days')}
							{$i18n.t('January')}
							{$i18n.t('February')}
							{$i18n.t('March')}
							{$i18n.t('April')}
							{$i18n.t('May')}
							{$i18n.t('June')}
							{$i18n.t('July')}
							{$i18n.t('August')}
							{$i18n.t('September')}
							{$i18n.t('October')}
							{$i18n.t('November')}
							{$i18n.t('December')}
							-->
										</div>
									{/if}

									<ChatItem
										className=""
										uiStyle={sidebarStyle}
										id={chat.id}
										title={chat.title}
										{shiftKey}
										selected={selectedChatId === chat.id}
										on:select={() => {
											selectedChatId = chat.id;
										}}
										on:unselect={() => {
											selectedChatId = null;
										}}
										on:change={async () => {
											initChatList();
										}}
										on:tag={(e) => {
											const { type, name } = e.detail;
											tagEventHandler(type, name, chat.id);
										}}
									/>
								{/each}

								{#if $scrollPaginationEnabled && !allChatsLoaded}
									<Loader
										on:visible={(e) => {
											if (!chatListLoading) {
												loadMoreChats();
											}
										}}
									>
										<div
											class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
										>
											<Spinner className=" size-4" />
											<div class=" ">Loading...</div>
										</div>
									</Loader>
								{/if}
							{:else}
								<div
									class="w-full flex justify-center py-1 text-xs animate-pulse items-center gap-2"
								>
									<Spinner className=" size-4" />
									<div class=" ">Loading...</div>
								</div>
							{/if}
						</div>
					</div>
				</Folder>
			</div>
		{/if}

		<!-- 底部用户区 -->
		<div class="shrink-0 mt-auto px-2 pt-2 pb-1 border-t border-gray-200/60 dark:border-gray-800">
			<div class="flex flex-col font-primary">
				{#if $user !== undefined && $user !== null}
					<UserMenu
						role={$user?.role}
						on:show={(e) => {
							if (e.detail === 'archived-chat') {
								showArchivedChats.set(true);
							}
						}}
					>
						<button
							class="group transition active:scale-[0.99] {$showSidebar || $mobile
								? userItemClass
								: iconButtonClass + ' mx-auto'}"
							on:click={() => {
								showDropdown = !showDropdown;
							}}
						>
							<div class="{avatarContainerClass} {$showSidebar || $mobile ? 'mr-3' : ''}">
								<img
									src={$user?.profile_image_url || '/user.png'}
									class="w-full h-full object-cover rounded-full"
									alt="User profile"
									draggable="false"
								/>
							</div>
							{#if $showSidebar || $mobile}
								<div class="flex-1 text-left min-w-0">
									<div class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
										{$user?.name}
									</div>
									<div class="text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">
										{$user?.role === 'admin' ? 'Admin' : 'User'}
									</div>
								</div>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-4 text-gray-400"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="M8.25 15L12 18.75 15.75 15m-7.5-6L12 5.25 15.75 9"
									/>
								</svg>
							{/if}
						</button>
					</UserMenu>
				{/if}
			</div>
		</div>
	</div>
</div>
