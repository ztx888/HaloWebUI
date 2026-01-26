<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { getContext } from 'svelte';
	
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { flyAndScale } from '$lib/utils/transitions';
	import { settings } from '$lib/stores';

	const i18n = getContext('i18n');

	export let isLastMessage = true;
	export let showContinue = true;
	export let showSummarize = true;
	export let showBranch = true;
	export let summarizing = false;
	export let branchingChat = false;

	export let onContinue: Function = () => {};
	export let onSummarize: Function = () => {};
	export let onBranch: Function = () => {};

	let show = false;

	const menuItemClass =
		'flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl';

	const closeMenu = () => {
		show = false;
	};
</script>

{#if showContinue || showSummarize || showBranch}
	<DropdownMenu.Root bind:open={show} closeFocus={false} typeahead={false}>
		<DropdownMenu.Trigger>
			<Tooltip content={$i18n.t('More')} placement="bottom">
				<button
					type="button"
					aria-label={$i18n.t('More')}
					class="{isLastMessage || ($settings?.highContrastMode ?? false)
						? 'visible'
						: 'invisible group-hover:visible'} p-1 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						aria-hidden="true"
						class="size-4"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M6 12h.01M12 12h.01M18 12h.01"
						/>
					</svg>
				</button>
			</Tooltip>
		</DropdownMenu.Trigger>

		<DropdownMenu.Content
			class="w-full max-w-[200px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
			sideOffset={-2}
			side="bottom"
			align="end"
			transition={flyAndScale}
		>
			{#if showContinue}
				<DropdownMenu.Item
					type="button"
					class={menuItemClass}
					on:click={(e) => {
						e.stopPropagation();
						e.preventDefault();
						onContinue();
						closeMenu();
					}}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						aria-hidden="true"
						class="size-4"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="m6.75 4.5 10.5 7.5-10.5 7.5V4.5z"
						/>
					</svg>
					<div class="flex items-center">{$i18n.t('Continue')}</div>
				</DropdownMenu.Item>
			{/if}

			{#if showSummarize}
				<DropdownMenu.Item
					type="button"
					class={`${menuItemClass} ${summarizing ? 'opacity-60 cursor-not-allowed' : ''}`}
					on:click={(e) => {
						e.stopPropagation();
						e.preventDefault();
						if (summarizing) return;
						onSummarize();
						closeMenu();
					}}
				>
					{#if summarizing}
						<Spinner className="size-4" />
					{:else}
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							aria-hidden="true"
							class="size-4"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M7.5 8.25h9m-9 4.5h6m-7.5 6h12a2.25 2.25 0 0 0 2.25-2.25v-13.5A2.25 2.25 0 0 0 18.75 2.25h-12A2.25 2.25 0 0 0 4.5 4.5v13.5A2.25 2.25 0 0 0 6.75 20.25z"
							/>
						</svg>
					{/if}
					<div class="flex items-center">{$i18n.t('Summarize')}</div>
				</DropdownMenu.Item>
			{/if}

			{#if showBranch}
				<DropdownMenu.Item
					type="button"
					class={`${menuItemClass} ${branchingChat ? 'opacity-60 cursor-not-allowed' : ''}`}
					on:click={(e) => {
						e.stopPropagation();
						e.preventDefault();
						if (branchingChat) return;
						onBranch();
						closeMenu();
					}}
				>
					{#if branchingChat}
						<Spinner className="size-4" />
					{:else}
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							aria-hidden="true"
							class="size-4"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M7.5 7.5h9m-9 9h9M12 7.5v9"
							/>
						</svg>
					{/if}
					<div class="flex items-center">{$i18n.t('Branch')}</div>
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/if}
