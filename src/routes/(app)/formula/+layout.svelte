<script lang="ts">
	import { getContext } from 'svelte';
	import { WEBUI_NAME, showSidebar, mobile } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	const i18n = getContext('i18n');
</script>

<svelte:head>
	<title>{$i18n.t('公式转换')} - {$WEBUI_NAME}</title>
</svelte:head>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="px-2.5 pt-1.5 backdrop-blur-xl drag-region">
		<div class="flex items-center gap-1">
			{#if $mobile}
				<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
					<Tooltip
						content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
						interactive={true}
					>
						<button
							id="sidebar-toggle-button"
							class="cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							on:click={() => {
								showSidebar.set(!$showSidebar);
							}}
						>
							<div class="self-center p-1.5">
								<Sidebar />
							</div>
						</button>
					</Tooltip>
				</div>
			{/if}

			<div class="flex items-center px-1.5 py-1 text-sm font-medium text-gray-700 dark:text-gray-300">
				{$i18n.t('公式转换')}
			</div>
		</div>
	</nav>

	<div class="pb-1 px-3 md:px-[18px] flex-1 max-h-full overflow-y-auto">
		<slot />
	</div>
</div>
