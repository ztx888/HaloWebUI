<script lang="ts">
	import { getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import AccessControl from '$lib/components/workspace/common/AccessControl.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let title = '';
	export let resourceName = '';
	export let accessControl: any = null;
	export let isShared = false;
	export let saving = false;
	export let onSubmit: (accessControl: any) => Promise<void> = async () => {};
	export let onDisable: (() => Promise<void>) | null = null;

	let localAccessControl: any = null;

	const cloneAccessControl = (value: any) =>
		value == null
			? null
			: {
					read: {
						group_ids: [...(value?.read?.group_ids ?? [])],
						user_ids: [...(value?.read?.user_ids ?? [])]
					},
					write: {
						group_ids: [...(value?.write?.group_ids ?? [])],
						user_ids: [...(value?.write?.user_ids ?? [])]
					}
				};

	$: if (show) {
		localAccessControl = cloneAccessControl(accessControl);
	}

	const handleSubmit = async () => {
		if (saving) return;
		await onSubmit(localAccessControl);
	};

	const handleDisable = async () => {
		if (saving || !onDisable) return;
		await onDisable();
	};
</script>

<Modal size="sm" bind:show>
	<div class="px-5 pt-4 pb-5">
		<div class="flex items-center justify-between">
			<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
				{title || '共享工具'}
			</div>
			<button
				class="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-200"
				type="button"
				on:click={() => {
					show = false;
				}}
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-5">
					<path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
				</svg>
			</button>
		</div>

		<div class="mt-3 rounded-xl border border-emerald-200/70 bg-emerald-50/80 px-4 py-3 text-xs leading-5 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/20 dark:text-emerald-300">
			<div class="font-medium">{resourceName || '共享外部工具'}</div>
			<div class="mt-1">
				共享后，普通用户会直接使用管理员保存的连接和密钥，不需要再单独配置。
			</div>
		</div>

		<div class="mt-4">
			<AccessControl
				bind:accessControl={localAccessControl}
				accessRoles={['read']}
				allowPublic={true}
				allowUserSelection={true}
			/>
		</div>

		<div class="mt-5 flex justify-between gap-2">
			{#if isShared && onDisable}
				<button
					class="rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 transition hover:bg-red-50 dark:border-red-900/60 dark:text-red-300 dark:hover:bg-red-950/30 disabled:cursor-not-allowed disabled:opacity-50"
					type="button"
					disabled={saving}
					on:click={handleDisable}
				>
					{$i18n.t('停止共享')}
				</button>
			{:else}
				<div></div>
			{/if}

			<button
				class="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-gray-900"
				type="button"
				disabled={saving}
				on:click={handleSubmit}
			>
				{isShared ? $i18n.t('保存') : '开启共享'}
			</button>
		</div>
	</div>
</Modal>
