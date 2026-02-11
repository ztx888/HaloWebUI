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

	const formatModel = (log): string => {
		return log.detail?.api_params?.model?.name || log.detail?.api_params?.model?.id || '-';
	};

	const formatCredit = (credit): string => {
		return parseFloat(credit).toFixed(6);
	};

	const isDebit = (credit): boolean => {
		return Number(credit) < 0;
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

<div class="credit-log-page h-full flex flex-col gap-5 pb-6">
	<div class="toolbar-shell rounded-2xl p-4 md:p-5">
		<div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
			<div class="space-y-1">
				<div class="text-xl md:text-2xl font-semibold text-slate-900 dark:text-slate-100 tracking-tight font-['Avenir_Next','PingFang_SC','Microsoft_YaHei',sans-serif]">
					{$i18n.t('Credit Log')}
				</div>
				<div class="text-xs md:text-sm text-slate-600 dark:text-slate-300">
					{$i18n.t('Explore usage records, model costs, and charge details')}
				</div>
			</div>

			<div class="stats-chip">
				<span class="stats-chip-dot"></span>
				<span class="text-xs text-slate-700 dark:text-slate-300">
					{$i18n.t('Total')}: {total ?? 0}
				</span>
			</div>
		</div>

		<div class="flex flex-1 w-full md:w-auto gap-2 items-center justify-end mt-4">
			<div class="field-shell flex-1 md:max-w-sm relative">
				<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
						<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
					</svg>
				</div>
				<input
					class="w-full pl-10 pr-3 py-2.5 bg-transparent text-sm border-none outline-none focus:ring-0 text-slate-700 dark:text-slate-200"
					bind:value={query}
					on:input={handleQueryChange}
					placeholder={$i18n.t('Search Username')}
				/>
			</div>

			<Tooltip content={$i18n.t('Clear Logs')}>
				<button
					class="trash-btn p-2 rounded-xl transition"
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

	<div class="content-shell rounded-2xl p-3 md:p-4 flex-1 flex flex-col min-h-0">
		{#if total === null}
			<div class="flex-1 flex items-center justify-center">
				<Spinner className="size-6" />
			</div>
		{:else if logs.length === 0}
			<div class="flex-1 flex items-center justify-center text-slate-500 dark:text-slate-400">
				{$i18n.t('No logs found')}
			</div>
		{:else}
			<div class="table-shell overflow-x-auto scrollbar-hidden flex-1 relative rounded-xl">
				<table class="w-full text-sm text-left text-slate-600 dark:text-slate-300">
					<thead class="text-[11px] tracking-[0.16em] uppercase text-slate-700 dark:text-slate-300 sticky top-0 z-10">
						<tr>
							<th scope="col" class="px-6 py-3.5 font-semibold w-44">{$i18n.t('Date')}</th>
							<th scope="col" class="px-6 py-3.5 font-semibold w-44">{$i18n.t('User')}</th>
							<th scope="col" class="px-6 py-3.5 font-semibold w-36">{$i18n.t('Credit')}</th>
							<th scope="col" class="px-6 py-3.5 font-semibold w-48">{$i18n.t('Model')}</th>
							<th scope="col" class="px-6 py-3.5 font-semibold min-w-[280px]">{$i18n.t('Description')}</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-slate-200/70 dark:divide-slate-700/70">
						{#each logs as log}
							<tr class="bg-white/80 dark:bg-slate-900/65 hover:bg-sky-50/80 dark:hover:bg-slate-800/60 transition duration-150">
								<td class="px-6 py-4 whitespace-nowrap text-slate-900 dark:text-slate-100">
									{formatDate(log.created_at)}
								</td>
								<td class="px-6 py-4 whitespace-nowrap">
									<div class="font-semibold text-slate-900 dark:text-slate-100">{log.username || log.user_id}</div>
								</td>
								<td class="px-6 py-4 whitespace-nowrap">
									<span class="credit-pill" class:credit-pill-debit={isDebit(log.credit)} class:credit-pill-credit={!isDebit(log.credit)}>
										{formatCredit(log.credit)}
									</span>
								</td>
								<td class="px-6 py-4 whitespace-nowrap text-slate-700 dark:text-slate-300">
									{formatModel(log)}
								</td>
								<td class="px-6 py-4 text-slate-600 dark:text-slate-400 max-w-[420px] truncate" title={formatDesc(log)}>
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

<style>
	.credit-log-page {
		--panel-bg: linear-gradient(140deg, rgba(255, 255, 255, 0.92), rgba(243, 247, 251, 0.84));
	}

	.toolbar-shell,
	.content-shell {
		border: 1px solid rgba(148, 163, 184, 0.24);
		background: var(--panel-bg);
		backdrop-filter: blur(9px);
		box-shadow: 0 10px 26px rgba(15, 23, 42, 0.08);
	}

	.stats-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		padding: 0.42rem 0.72rem;
		border-radius: 9999px;
		border: 1px solid rgba(148, 163, 184, 0.26);
		background: rgba(241, 245, 249, 0.86);
	}

	.stats-chip-dot {
		width: 0.45rem;
		height: 0.45rem;
		border-radius: 9999px;
		background: #0ea5e9;
		box-shadow: 0 0 0 5px rgba(14, 165, 233, 0.16);
	}

	.field-shell {
		border-radius: 0.9rem;
		border: 1px solid rgba(148, 163, 184, 0.25);
		background: rgba(248, 250, 252, 0.88);
		transition: all 0.2s ease;
	}

	.field-shell:focus-within {
		border-color: rgba(14, 165, 233, 0.52);
		box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.14);
		background: rgba(255, 255, 255, 0.95);
	}

	.trash-btn {
		border: 1px solid rgba(148, 163, 184, 0.24);
		color: rgb(71 85 105);
		background: rgba(255, 255, 255, 0.7);
	}

	.trash-btn:hover {
		border-color: rgba(239, 68, 68, 0.45);
		background: rgba(254, 226, 226, 0.7);
		color: rgb(220 38 38);
	}

	.table-shell {
		border: 1px solid rgba(148, 163, 184, 0.26);
		background: rgba(255, 255, 255, 0.85);
		box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
	}

	.table-shell thead {
		background: linear-gradient(180deg, rgba(241, 245, 249, 0.95), rgba(248, 250, 252, 0.88));
	}

	.credit-pill {
		display: inline-flex;
		align-items: center;
		padding: 0.2rem 0.62rem;
		border-radius: 9999px;
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.02em;
	}

	.credit-pill-debit {
		color: rgb(185 28 28);
		background: linear-gradient(140deg, rgba(254, 202, 202, 0.8), rgba(254, 226, 226, 0.85));
	}

	.credit-pill-credit {
		color: rgb(5 150 105);
		background: linear-gradient(140deg, rgba(167, 243, 208, 0.75), rgba(220, 252, 231, 0.85));
	}

	:global(.dark) .credit-log-page {
		--panel-bg: linear-gradient(136deg, rgba(15, 23, 42, 0.83), rgba(30, 41, 59, 0.74));
	}

	:global(.dark) .toolbar-shell,
	:global(.dark) .content-shell,
	:global(.dark) .table-shell {
		border-color: rgba(71, 85, 105, 0.56);
		background: rgba(15, 23, 42, 0.66);
		box-shadow: 0 10px 26px rgba(2, 6, 23, 0.44);
	}

	:global(.dark) .field-shell {
		border-color: rgba(71, 85, 105, 0.6);
		background: rgba(15, 23, 42, 0.7);
	}

	:global(.dark) .field-shell:focus-within {
		border-color: rgba(34, 211, 238, 0.6);
		box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.15);
	}

	:global(.dark) .stats-chip {
		background: rgba(30, 41, 59, 0.75);
		border-color: rgba(71, 85, 105, 0.6);
	}

	:global(.dark) .trash-btn {
		background: rgba(30, 41, 59, 0.66);
		color: rgb(203 213 225);
		border-color: rgba(71, 85, 105, 0.58);
	}

	:global(.dark) .trash-btn:hover {
		background: rgba(127, 29, 29, 0.24);
		color: rgb(252 165 165);
		border-color: rgba(248, 113, 113, 0.55);
	}

	:global(.dark) .table-shell thead {
		background: linear-gradient(180deg, rgba(30, 41, 59, 0.95), rgba(15, 23, 42, 0.9));
	}

	@media (max-width: 768px) {
		.credit-log-page {
			gap: 0.85rem;
		}

		.table-shell table {
			min-width: 850px;
		}
	}
</style>
