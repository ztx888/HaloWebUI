<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';

	const i18n: Writable<any> = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import AddServerModal from '$lib/components/AddServerModal.svelte';

	export let onDelete: () => void = () => {};
	export let onSubmit: (connection: any) => void = () => {};
	export let onShare: (() => void) | null = null;

export let connection: any = null;
export let direct: boolean = false;
export let shareActive: boolean = false;
export let showShareAction: boolean = false;
export let shareScopeLabel: string = '';

	let showConfigModal = false;
	let showDeleteConfirmDialog = false;

	const handleSubmit = (c: any) => {
		connection = c;
		onSubmit(c);
	};
</script>

<AddServerModal
	edit
	{direct}
	bind:show={showConfigModal}
	{connection}
	onDelete={() => {
		showDeleteConfirmDialog = true;
	}}
	onSubmit={handleSubmit}
/>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		onDelete();
		showConfigModal = false;
	}}
/>

<div class="flex w-full gap-2 items-center">
	<Tooltip
		className="w-full relative"
		content={$i18n.t(`WebUI will make requests to "{{url}}"`, {
			url: `${connection?.url}/${connection?.path ?? 'openapi.json'}`
		})}
		placement="top-start"
	>
		{#if !(connection?.config?.enable ?? true)}
			<div
				class="absolute top-0 bottom-0 left-0 right-0 opacity-60 bg-white dark:bg-gray-900 z-10"
			></div>
		{/if}
		<div class="flex w-full">
			<div class="flex-1 relative">
				<input
					class=" outline-hidden w-full bg-transparent"
					placeholder={$i18n.t('API Base URL')}
					bind:value={connection.url}
					autocomplete="off"
				/>
			</div>

			{#if (connection?.auth_type ?? 'bearer') === 'bearer'}
				<SensitiveInput
					inputClassName=" outline-hidden bg-transparent w-full"
					placeholder={$i18n.t('API Key')}
					bind:value={connection.key}
					required={false}
				/>
			{/if}
		</div>
	</Tooltip>

	<div class="flex gap-1">
		{#if showShareAction && onShare}
			<Tooltip content={shareActive ? '共享设置' : '开启共享'} className="self-start">
				<button
					class="self-center p-1 rounded-lg transition {shareActive
						? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:hover:bg-emerald-950/50'
						: 'bg-transparent hover:bg-gray-100 dark:bg-gray-900 dark:hover:bg-gray-850'}"
					on:click={() => {
						onShare?.();
					}}
					type="button"
				>
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-4">
						<path d="M15 8a3 3 0 1 0-2.83-4H12a3 3 0 0 0 .17 1l-4.6 2.3a3 3 0 0 0-2.4-1.2 3 3 0 1 0 0 6c.87 0 1.66-.37 2.2-.95l4.8 2.4A3 3 0 0 0 12 15a3 3 0 1 0 .17-1l-4.8-2.4A2.98 2.98 0 0 0 8 10c0-.35-.06-.68-.17-.99l4.6-2.3c.54.79 1.45 1.3 2.47 1.3Z" />
					</svg>
				</button>
			</Tooltip>
			{#if shareActive}
				<span class="self-center rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
					共享中
				</span>
				{#if shareScopeLabel}
					<span class="self-center rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800/70 dark:text-slate-300">
						{shareScopeLabel}
					</span>
				{/if}
			{/if}
		{/if}
		<Tooltip content={$i18n.t('Configure')} className="self-start">
			<button
				class="self-center p-1 bg-transparent hover:bg-gray-100 dark:bg-gray-900 dark:hover:bg-gray-850 rounded-lg transition"
				on:click={() => {
					showConfigModal = true;
				}}
				type="button"
			>
				<Cog6 />
			</button>
		</Tooltip>
	</div>
</div>
