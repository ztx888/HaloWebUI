<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { channels, models } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Hashtag from '$lib/components/icons/Hashtag.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { searchUsers } from '$lib/apis/users';

	const i18n = getContext('i18n');

	export let query = '';
	export let command: (payload: { id: string; label: string }) => void;
	export let selectedIndex = 0;
	export let triggerChar = '@';
	export let modelSuggestions = false;
	export let userSuggestions = false;
	export let channelSuggestions = false;

	let _models = [];
	let _users = [];
	let _channels = [];
	let filteredItems = [];

	$: filteredItems = [..._users, ..._models, ..._channels].filter(
		(item) =>
			item.label.toLowerCase().includes(query.toLowerCase()) ||
			item.id.toLowerCase().includes(query.toLowerCase())
	);

	const getUserList = async () => {
		const res = await searchUsers(localStorage.token, query).catch((error) => {
			console.error('Error searching users:', error);
			return null;
		});

		if (res) {
			_users = [...res.users.map((item) => ({ type: 'user', id: item.id, label: item.name }))].sort(
				(a, b) => a.label.localeCompare(b.label)
			);
		}
	};

	$: if (query !== null && userSuggestions) {
		getUserList();
	}

	const select = (index: number) => {
		const item = filteredItems[index];
		if (!item) {
			return;
		}

		command({
			id: `${item.type === 'user' ? 'U' : item.type === 'model' ? 'M' : 'C'}:${item.id}|${item.label}`,
			label: item.label
		});
	};

	const onKeyDown = (event: KeyboardEvent) => {
		if (!['ArrowUp', 'ArrowDown', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
			return false;
		}

		if (event.key === 'ArrowUp') {
			selectedIndex = Math.max(0, selectedIndex - 1);
			document.querySelector(`[data-selected="true"]`)?.scrollIntoView({
				block: 'center',
				inline: 'nearest',
				behavior: 'instant'
			});
			return true;
		}

		if (event.key === 'ArrowDown') {
			selectedIndex = Math.min(selectedIndex + 1, filteredItems.length - 1);
			document.querySelector(`[data-selected="true"]`)?.scrollIntoView({
				block: 'center',
				inline: 'nearest',
				behavior: 'instant'
			});
			return true;
		}

		if (event.key === 'Enter' || event.key === 'Tab') {
			select(selectedIndex);
			if (event.key === 'Enter') {
				event.preventDefault();
			}
			return true;
		}

		return event.key === 'Escape';
	};

	// @ts-ignore
	export function _onKeyDown(event: KeyboardEvent) {
		return onKeyDown(event);
	}

	onMount(() => {
		if (channelSuggestions) {
			_channels = [
				...$channels
					.filter((channel) => channel?.type !== 'dm')
					.map((channel) => ({
						type: 'channel',
						id: channel.id,
						label: channel.name,
						data: channel
					}))
			];
		} else {
			if (userSuggestions) {
				getUserList();
			}

			if (modelSuggestions) {
				_models = [
					...$models
						.filter((model) => !model?.direct)
						.map((model) => ({
							type: 'model',
							id: model.id,
							label: model.name,
							data: model
						}))
				];
			}
		}
	});

	const hasPublicReadGrant = (grants: any) =>
		Array.isArray(grants) &&
		grants.some(
			(grant) =>
				grant?.principal_type === 'user' &&
				grant?.principal_id === '*' &&
				grant?.permission === 'read'
		);

	const isPublicChannel = (channel: any): boolean => {
		if (channel?.type === 'group') {
			if (typeof channel?.is_private === 'boolean') {
				return !channel.is_private;
			}
			return hasPublicReadGrant(channel?.access_grants);
		}
		return hasPublicReadGrant(channel?.access_grants);
	};
</script>

{#if filteredItems.length}
	<div
		class="mention-list text-black dark:text-white rounded-2xl shadow-lg border border-gray-200 dark:border-gray-800 flex flex-col bg-white dark:bg-gray-850 w-72 p-1"
		id="suggestions-container"
	>
		<div class="overflow-y-auto scrollbar-thin max-h-60">
			{#each filteredItems as item, index}
				{#if index === 0 || item?.type !== filteredItems[index - 1]?.type}
					<div class="px-2 text-xs text-gray-500 py-1">
						{#if item?.type === 'user'}
							{$i18n.t('Users')}
						{:else if item?.type === 'model'}
							{$i18n.t('Models')}
						{:else if item?.type === 'channel'}
							{$i18n.t('Channels')}
						{/if}
					</div>
				{/if}

				<Tooltip content={item?.id} placement="top-start">
					<button
						type="button"
						class="flex items-center justify-between px-2.5 py-1.5 rounded-xl w-full text-left {index ===
						selectedIndex
							? 'bg-gray-50 dark:bg-gray-800 selected-command-option-button'
							: ''}"
						data-selected={index === selectedIndex}
						on:click={() => select(index)}
						on:mousemove={() => {
							selectedIndex = index;
						}}
					>
						{#if item.type === 'channel'}
							<div class="size-4 justify-center flex items-center mr-0.5">
								{#if isPublicChannel(item?.data)}
									<Hashtag className="size-3" strokeWidth="2.5" />
								{:else}
									<LockClosed className="size-[15px]" strokeWidth="2" />
								{/if}
							</div>
						{:else if item.type === 'model'}
							<img
								src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${item.id}&lang=${$i18n.language}`}
								alt={item?.data?.name ?? item.id}
								class="rounded-full size-5 items-center mr-2"
								on:error={(e) => {
									e.currentTarget.src = '/favicon.png';
								}}
							/>
						{:else if item.type === 'user'}
							<img
								src={`${WEBUI_API_BASE_URL}/users/${item.id}/profile/image`}
								alt={item?.label ?? item.id}
								class="rounded-full size-5 items-center mr-2"
								on:error={(e) => {
									e.currentTarget.src = '/favicon.png';
								}}
							/>
						{/if}

						<div class="truncate flex-1 pr-2">{item.label}</div>
						<div class="shrink-0 text-xs text-gray-500">
							{#if item.type === 'user'}
								{$i18n.t('User')}
							{:else if item.type === 'model'}
								{$i18n.t('Model')}
							{:else if item.type === 'channel'}
								{$i18n.t('Channel')}
							{/if}
						</div>
					</button>
				</Tooltip>
			{/each}
		</div>
	</div>
{/if}
