<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		getRedemptionCodes,
		deleteRedemptionCode,
		exportRedemptionCodes
	} from '$lib/apis/credit';
	import CreateRedemptionCodeModal from './CreateRedemptionCodeModal.svelte';
	import EditRedemptionCodeModal from './EditRedemptionCodeModal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	const i18n = getContext('i18n');

	let page = 1;
	let limit = 30;
	let total = null;
	let keyword = '';
	let codes: any[] = [];
	
	// Debounce search
	let searchTimeout;
	const handleSearchInput = () => {
		clearTimeout(searchTimeout);
		searchTimeout = setTimeout(() => {
			page = 1;
			loadCodes();
		}, 500);
	};

	let showCreateModal = false;
	let showEditModal = false;
	let selectedCode: any = null;

	const loadCodes = async () => {
		try {
			const data = await getRedemptionCodes(localStorage.token, page, limit, keyword);
			if (data) {
				total = data.total ?? 0;
				codes = data.results ?? [];
			}
		} catch (error) {
			toast.error(`Failed to load redemption codes: ${error}`);
		}
	};

	let showDeleteConfirmDialog = false;
	let deleteCreditLogID = '';
	const handleDelete = async (code: string) => {
		try {
			await deleteRedemptionCode(localStorage.token, code);
			toast.success($i18n.t('Redemption code deleted successfully'));
			await loadCodes();
		} catch (error) {
			toast.error(`Failed to delete redemption code: ${error}`);
		}
	};

	const handleEdit = (code: any) => {
		selectedCode = code;
		showEditModal = true;
	};

	const handleExport = async () => {
		if (!keyword.trim()) {
			toast.error($i18n.t('Please enter a keyword to export'));
			return;
		}

		try {
			const response = await exportRedemptionCodes(localStorage.token, keyword);
			if (response) {
				const blob = await response.blob();
				const url = window.URL.createObjectURL(blob);
				const a = document.createElement('a');
				a.href = url;
				a.download = `${keyword}.csv`;
				document.body.appendChild(a);
				a.click();
				window.URL.revokeObjectURL(url);
				document.body.removeChild(a);
				toast.success($i18n.t('Export completed'));
			}
		} catch (error) {
			toast.error(`Export failed: ${error}`);
		}
	};

	const formatDate = (timestamp: number): string => {
		return timestamp ? new Date(timestamp * 1000).toLocaleString() : '-';
	};

	const getStatusText = (code: any): string => {
		if (code.received_at) {
			return $i18n.t('Used');
		}
		if (code.expired_at && code.expired_at < Date.now() / 1000) {
			return $i18n.t('Expired');
		}
		return $i18n.t('Available');
	};

	const getStatusClass = (code: any): string => {
		if (code.received_at) {
			return 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300';
		}
		if (code.expired_at && code.expired_at < Date.now() / 1000) {
			return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400';
		}
		return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400';
	};

	const hideCode = (code: string): string => {
		return code.replace(/(.{4})(.*)(.{4})/, '$1****$3');
	};

	$: if (page) {
		loadCodes();
	}
</script>

<CreateRedemptionCodeModal
	bind:show={showCreateModal}
	on:save={async () => {
		page = 1;
		await loadCodes();
	}}
/>

<EditRedemptionCodeModal
	bind:show={showEditModal}
	bind:code={selectedCode}
	on:save={async () => {
		await loadCodes();
	}}
/>

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	on:confirm={() => {
		handleDelete(deleteCreditLogID);
	}}
/>

<div class="h-full flex flex-col space-y-4">
	<!-- Header & Toolbar -->
	<div class="flex flex-col md:flex-row justify-between items-center gap-4 bg-white dark:bg-gray-900 p-2 rounded-lg">
		<div class="text-xl font-semibold text-gray-800 dark:text-gray-100 flex items-center gap-2">
			{$i18n.t('Redemption Codes')}
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
					bind:value={keyword}
					on:input={handleSearchInput}
					placeholder={$i18n.t('Search by code or topic')}
				/>
			</div>

			<Tooltip content={$i18n.t('Create Redemption Code')}>
				<button
					class="p-2 rounded-lg hover:bg-gray-50 text-gray-700 hover:text-blue-600 dark:text-gray-300 dark:hover:bg-gray-850 dark:hover:text-blue-400 transition"
					on:click={() => {
						showCreateModal = true;
					}}
				>
					<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-5 h-5">
						<path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
					</svg>
				</button>
			</Tooltip>

			<Tooltip content={$i18n.t('Export Redemption Codes')}>
				<button
					class="p-2 rounded-lg hover:bg-gray-50 text-gray-700 hover:text-green-600 dark:text-gray-300 dark:hover:bg-gray-850 dark:hover:text-green-400 transition"
					on:click={handleExport}
				>
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="w-5 h-5">
						<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
						<path fill-rule="evenodd" d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z" clip-rule="evenodd" />
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
		{:else if codes.length === 0}
			<div class="flex-1 flex items-center justify-center text-gray-500">
				{$i18n.t('No redemption codes found')}
			</div>
		{:else}
			<div class="overflow-x-auto scrollbar-hidden flex-1 relative rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
				<table class="w-full text-sm text-left text-gray-500 dark:text-gray-400">
					<thead class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-800 dark:text-gray-400 sticky top-0 z-10">
						<tr>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Code')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Topic')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Amount')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Status')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Used By')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Used At')}</th>
							<th scope="col" class="px-6 py-3 font-semibold">{$i18n.t('Expires')}</th>
							<th scope="col" class="px-6 py-3 font-semibold text-right">{$i18n.t('Actions')}</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-gray-100 dark:divide-gray-800">
						{#each codes as code}
							<tr class="bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition">
								<td class="px-6 py-4 whitespace-nowrap font-mono text-xs text-gray-900 dark:text-white">
									{hideCode(code.code)}
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-gray-900 dark:text-white">
									{code.purpose}
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-gray-900 dark:text-white font-medium">
									{parseFloat(code.amount).toFixed(2)}
								</td>
								<td class="px-6 py-4 whitespace-nowrap">
									<span class={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusClass(code)}`}>
										{getStatusText(code)}
									</span>
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-gray-600 dark:text-gray-400">
									{code.username || '-'}
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-gray-600 dark:text-gray-400">
									{formatDate(code.received_at)}
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-gray-600 dark:text-gray-400">
									{formatDate(code.expired_at)}
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-right">
									<div class="flex items-center justify-end space-x-2">
										<Tooltip content={$i18n.t('Edit')}>
											<button
												class="p-1.5 rounded-md hover:bg-blue-50 text-gray-500 hover:text-blue-600 dark:hover:bg-gray-800 dark:hover:text-blue-400 transition"
												on:click={() => handleEdit(code)}
											>
												<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
													<path stroke-linecap="round" stroke-linejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125" />
												</svg>
											</button>
										</Tooltip>

										<Tooltip content={$i18n.t('Delete')}>
											<button
												class="p-1.5 rounded-md hover:bg-red-50 text-gray-500 hover:text-red-600 dark:hover:bg-gray-800 dark:hover:text-red-400 transition"
												on:click={() => {
													deleteCreditLogID = code.code;
													showDeleteConfirmDialog = true;
												}}
											>
												<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
													<path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
												</svg>
											</button>
										</Tooltip>
									</div>
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
