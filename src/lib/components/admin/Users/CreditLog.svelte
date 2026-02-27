<script lang="ts">
	import Pagination from '$lib/components/common/Pagination.svelte';
	import { getContext, onMount } from 'svelte';
	import { listAllCreditLog } from '$lib/apis/credit';
	import { toast } from 'svelte-sonner';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DeleteCreditLogModal from '$lib/components/admin/Users/DeleteCreditLogModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let page = 1;
	let limit = 30;
	let total = null;

	let query = '';
	
	// Create a debounced query function
	let queryTimeout;
	const handleQueryChange = () => {
		clearTimeout(queryTimeout);
		queryTimeout = setTimeout(() => {
			page = 1;
			doQuery();
		}, 500);
	};

	let logs = [];
	const doQuery = async () => {
		const data = await listAllCreditLog(localStorage.token, page, limit, query).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			total = data?.total ?? 0;
			logs = data?.results ?? [];
		}
	};

	const formatDate = (t: number): string => {
		return new Date(t * 1000).toLocaleString();
	};

	const formatDesc = (log): string => {
		const usage = log?.detail?.usage ?? {};
		if (usage && Object.keys(usage).length > 0) {
			if (usage.total_price !== undefined && usage.total_price !== null) {
				return `-${Math.round(usage.total_price * 1e6) / 1e6}`;
			}
			if (usage.request_unit_price) {
				return `-${usage.request_unit_price / 1e6}`;
			}
			if (usage.prompt_unit_price || usage.completion_unit_price) {
				return `-${Math.round((usage.prompt_tokens * usage.prompt_unit_price + usage.completion_tokens * usage.completion_unit_price) * 1e6) / 1e6}`;
			}
		}
		return log?.detail?.desc || '-';
	};

	let showDeleteLogModal = false;

	// Watch page changes, but manual trigger for query changes
	$: if (page) {
		doQuery();
	}
</script>

<DeleteCreditLogModal
	bind:show={showDeleteLogModal}
	on:save={async () => {
		page = 1;
		await doQuery();
	}}
/>

<div class="h-full flex flex-col space-y-4">
	<!-- Header & Toolbar -->
	<div class="flex flex-col md:flex-row justify-between items-center gap-4 bg-white dark:bg-gray-900 p-2 rounded-lg">
		<div class="text-xl font-semibold text-gray-800 dark:text-gray-100 flex items-center gap-2">
			{$i18n.t('Credit Log')}
		</div>

		<div class="flex flex-1 w-full md:w-auto gap-2 items-center justify-end">
			<div class="flex-1 md:max-w-xs relative bg-gray-50 dark:bg-gray-850 rounded-lg border border-gray-100 dark:border-gray-800">
				<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
						<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
					</svg>
				</div>
				<input
					class="w-full pl-10 pr-3 py-2 bg-transparent text-sm border-none outline-none focus:ring-0 text-gray-700 dark:text-gray-200"
					bind:value={query}
					on:input={handleQueryChange}
					placeholder={$i18n.t('Search Username')}
				/>
			</div>

			<Tooltip content={$i18n.t('Clear Logs')}>
				<button
					class="p-2 rounded-lg hover:bg-red-50 text-gray-500 hover:text-red-600 dark:hover:bg-red-900/10 dark:hover:text-red-400 transition"
					on:click={() => {
						showDeleteLogModal = !showDeleteLogModal;
					}}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-5 h-5">
						<path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
					</svg>
				</button>
			</Tooltip>
		</div>
	</div>

	<!-- Content Area -->
	<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-4 border border-gray-100 dark:border-gray-800 flex-1 flex flex-col min-h-0">
		{#if total === null}
			<div class="flex-1 flex items-center justify-center">
				<Spinner className="size-6" />
			</div>
		{:else if logs.length === 0}
			<div class="flex-1 flex items-center justify-center text-gray-500">
				{$i18n.t('No logs found')}
			</div>
		{:else}
			<div class="overflow-x-auto scrollbar-hidden flex-1 relative rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
				<table class="w-full text-sm text-left text-gray-500 dark:text-gray-400">
					<thead class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-800 dark:text-gray-400 sticky top-0 z-10">
						<tr>
							<th scope="col" class="px-6 py-3 font-semibold w-40">{$i18n.t('Date')}</th>
							<th scope="col" class="px-6 py-3 font-semibold w-40">{$i18n.t('User')}</th>
							<th scope="col" class="px-6 py-3 font-semibold w-32">{$i18n.t('Credit')}</th>
							<th scope="col" class="px-6 py-3 font-semibold w-40">{$i18n.t('Model')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Description')}</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
						{#each logs as log}
							<tr class="bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition duration-150">
								<td class="px-6 py-4 whitespace-nowrap text-gray-900 dark:text-white">
									{formatDate(log.created_at)}
								</td>
								<td class="px-6 py-4 whitespace-nowrap">
									<div class="font-medium text-gray-900 dark:text-white">{log.username || log.user_id}</div>
								</td>
								<td class="px-6 py-4 whitespace-nowrap">
									<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300">
										{parseFloat(log.credit).toFixed(6)}
									</span>
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-gray-700 dark:text-gray-300">
									{log.detail?.api_params?.model?.name || log.detail?.api_params?.model?.id || '-'}
								</td>
								<td class="px-6 py-4 text-gray-600 dark:text-gray-400 max-w-xs truncate" title={formatDesc(log)}>
									{formatDesc(log)}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			
			<div class="mt-4 flex justify-end">
				<Pagination bind:page count={total} perPage={limit} />
			</div>
		{/if}
	</div>
</div>
