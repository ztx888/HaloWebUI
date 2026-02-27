<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { user } from '$lib/stores';
	import {
		createTradeTicket,
		getCreditConfig,
		listCreditLog,
		receiveRedemptionCode
	} from '$lib/apis/credit';
	import { toast } from 'svelte-sonner';
	import { getSessionUser } from '$lib/apis/auths';
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	type Model = {
		id: string;
		name: string;
	};
	type APIParams = {
		model: Model;
	};
	type Usage = {
		total_price: number;
		prompt_unit_price: number;
		completion_unit_price: number;
		request_unit_price: number;
		completion_tokens: number;
		prompt_tokens: number;
	};
	type LogDetail = {
		desc: string;
		api_params: APIParams;
		usage: Usage;
	};
	type Log = {
		id: string;
		credit: string;
		detail: LogDetail;
		created_at: number;
	};
	let page = 1;
	let hasMore = true;
	let logs: Array<Log> = [];
	const loadLogs = async (append: boolean) => {
		const data = await listCreditLog(localStorage.token, page).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (data.length === 0) {
			hasMore = false;
		}
		if (append) {
			logs = [...logs, ...data];
		} else {
			logs = data;
		}
	};
	const nextLogs = async () => {
		page++;
		await loadLogs(true);
	};

	let credit = 0;
	let payType = 'alipay';
	let payTypes = [
		{
			code: 'alipay',
			title: $i18n.t('Alipay')
		},
		{
			code: 'wxpay',
			title: $i18n.t('WXPay')
		}
	];
	let amount = null;

	// redemption code variables
	let showRedemptionModal = false;
	let redemptionCode = '';
	let isSubmittingRedemption = false;

	let config = {
		CREDIT_EXCHANGE_RATIO: 0,
		EZFP_PAY_PRIORITY: 'qrcode'
	};

	let tradeInfo = {
		detail: {
			code: -1,
			msg: '',
			payurl: '',
			qrcode: '',
			urlscheme: '',
			img: '',
			imgDisplayUrl: ''
		}
	};

	const showQRCode = (detail: object): Boolean => {
		if (detail?.img) {
			tradeInfo.detail.imgDisplayUrl = detail.img;
			return true;
		}

		if (detail?.qrcode) {
			document.getElementById('trade-qrcode').innerHTML = '';
			new QRCode(document.getElementById('trade-qrcode'), {
				text: detail.qrcode,
				width: 128,
				height: 128,
				colorDark: '#000000',
				colorLight: '#ffffff',
				correctLevel: QRCode.CorrectLevel.H
			});
			return true;
		}

		return false;
	};

	const redirectLink = (detail: object): Boolean => {
		if (detail?.payurl) {
			window.location.href = detail.payurl;
			return true;
		}

		if (detail?.urlscheme) {
			window.location.href = detail.urlscheme;
			return true;
		}

		return false;
	};

	const handleAddCreditClick = async () => {
		const res = await createTradeTicket(localStorage.token, payType, amount).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			tradeInfo = res;
			if (tradeInfo.detail === undefined) {
				toast.error('init payment failed');
				return;
			}

			const detail = tradeInfo.detail;
			if (detail?.code !== 1) {
				toast.error(tradeInfo?.detail?.msg);
				return;
			}

			if (config.EZFP_PAY_PRIORITY === 'qrcode') {
				if (showQRCode(detail)) {
					return;
				}
				redirectLink(detail);
			} else {
				if (redirectLink(detail)) {
					return;
				}
				showQRCode(detail);
			}
		}
	};

	const handleWeChatClick = async () => {
		payType = 'wxpay';
		await handleAddCreditClick();
	};

	const handleAlipayClick = async () => {
		payType = 'alipay';
		await handleAddCreditClick();
	};

	const formatDate = (t: number): string => {
		return new Date(t * 1000).toLocaleString();
	};

	const formatDesc = (log: Log): string => {
		const usage = log?.detail?.usage ?? {};
		if (usage && Object.keys(usage).length > 0) {
			if (usage.total_price !== undefined && usage.total_price !== null) {
				return `-${Math.round(usage.total_price * 1e6) / 1e6}`;
			}
			if (usage.request_unit_price) {
				return `-${usage.request_unit_price / 1e6}`;
			}
			if (usage.prompt_unit_price || usage.completion_unit_price) {
				return `-${Math.round(usage.prompt_tokens * usage.prompt_unit_price + usage.completion_tokens * usage.completion_unit_price) / 1e6}`;
			}
		}
		return log?.detail?.desc;
	};

	const doInit = async () => {
		const sessionUser = await getSessionUser(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		await user.set(sessionUser);

		const res = await getCreditConfig(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			config = res;
		}

		credit = $user?.credit ? $user?.credit : 0;
		tradeInfo = {};
		document.getElementById('trade-qrcode').innerHTML = '';

		await loadLogs(false);
	};

	onMount(async () => {
		await doInit();
	});

	const handleRedeemCode = async () => {
		if (!redemptionCode || !redemptionCode.trim()) {
			toast.error($i18n.t('Please enter a valid redemption code'));
			return;
		}

		isSubmittingRedemption = true;

		try {
			await receiveRedemptionCode(localStorage.token, redemptionCode.trim());
			toast.success($i18n.t('Redemption code applied successfully'));
			redemptionCode = '';
			showRedemptionModal = false;

			// refresh user data and logs
			await doInit();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			isSubmittingRedemption = false;
		}
	};

	const handleOpenRedemptionModal = () => {
		redemptionCode = '';
		showRedemptionModal = true;
	};

	const handleCloseRedemptionModal = () => {
		redemptionCode = '';
		showRedemptionModal = false;
	};
</script>

<div class="flex flex-col h-full justify-between text-sm">
	<div class=" space-y-3 lg:max-h-full">
		<div class="space-y-1">
			<div class="pt-0.5">
				<div class="flex flex-col w-full">
					<div class="mb-1 text-base font-medium flex items-center justify-between">
						<span>{$i18n.t('Credit')}</span>
						<button
							on:click={handleOpenRedemptionModal}
							class="text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 font-medium"
						>
							{$i18n.t('Redeem Code')}
						</button>
					</div>
					<div class="flex items-center">
						<div>{credit}</div>
						<button class="ml-1" on:click={() => doInit()}>
							<svg
								viewBox="0 0 1024 1024"
								xmlns="http://www.w3.org/2000/svg"
								width="16"
								height="16"
							>
								<path
									d="M832 512a32 32 0 0 0-32 32c0 158.784-129.216 288-288 288s-288-129.216-288-288 129.216-288 288-288c66.208 0 129.536 22.752 180.608 64H608a32 32 0 0 0 0 64h160a32 32 0 0 0 32-32V192a32 32 0 0 0-64 0v80.96A350.464 350.464 0 0 0 512 192C317.92 192 160 349.92 160 544s157.92 352 352 352 352-157.92 352-352a32 32 0 0 0-32-32"
									fill="#3E3A39"
								></path>
							</svg>
						</button>
					</div>
				</div>
			</div>

			<hr class=" border-gray-100 dark:border-gray-700/10 my-2.5 w-full" />

			<div class="pt-0.5">
				<div class="flex flex-col w-full">
					<div class="mb-1 text-base font-medium">{$i18n.t('Add Credit')}</div>

					<div class="text-xs text-orange-400 dark:text-orange-500">
						{$i18n.t(
							'The exchange ratio of legal currency to credit: 1:{{ratio}}; Currently Equal to {{credit}} credit',
							{
								ratio: config.CREDIT_EXCHANGE_RATIO,
								credit: (amount ?? 0) * config.CREDIT_EXCHANGE_RATIO
							}
						)}
					</div>

					<form
						class="flex flex-col h-full justify-between text-sm"
						on:submit|preventDefault={async () => {
							await handleAddCreditClick();
						}}
					>
						<div class="flex w-full justify-between">
							<div class="w-[80px] self-center text-xs font-medium">{$i18n.t('Pay Type')}</div>
							<div class="mt-2 w-full flex items-center relative justify-end">
								<select
									class="pt-[2px] pb-[2px] w-full pr-8 rounded-sm text-xs outline-hidden text-right bg-gray-50 dark:text-gray-300 dark:bg-gray-850"
									bind:value={payType}
								>
									{#each payTypes as payType}
										<option value={payType['code']}>{payType['title']}</option>
									{/each}
								</select>
							</div>
						</div>

						<div class="mt-2 flex w-full justify-between">
							<div class="w-[80px] self-center text-xs font-medium">
								{$i18n.t('Currency Amount')}
							</div>
							<div class="w-full flex items-center relative">
								<input
									class="w-full text-sm placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden text-end"
									type="number"
									bind:value={amount}
									autocomplete="off"
									required
									placeholder={$i18n.t('Please input amount')}
								/>
							</div>
						</div>

						<div class="flex w-full justify-end mt-6">
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center"
								type="submit"
							>
								{$i18n.t('Submit')}
							</button>
						</div>
					</form>
				</div>
			</div>

			<div class="max-h-[14rem] flex flex-col items-center w-full">
				<div id="trade-qrcode" class="max-h-[128px] max-w-[128px]"></div>
				{#if tradeInfo?.detail?.imgDisplayUrl}
					<img
						src={tradeInfo?.detail?.imgDisplayUrl}
						alt="trade qrcode"
						class="object-contain max-h-[128px] max-w-[128px]"
					/>
				{/if}
				{#if tradeInfo?.detail?.qrcode || tradeInfo?.detail?.imgDisplayUrl}
					<div class="mt-2">
						{$i18n.t('Please refresh after payment')}
					</div>
				{/if}
			</div>

			{#if !tradeInfo?.detail?.qrcode && !tradeInfo?.detail?.imgDisplayUrl}
				<hr class=" border-gray-100 dark:border-gray-700/10 my-2.5 w-full" />

				<div class="pt-0.5">
					<div class="flex flex-col w-full">
						<div class="mb-1 text-base font-medium">{$i18n.t('Credit Log')}</div>
						<div
							class="overflow-y-scroll max-h-[14rem] flex flex-col scrollbar-hidden relative whitespace-nowrap overflow-x-auto max-w-full rounded-sm"
						>
							{#if logs.length === 0 && hasMore}
								<div class="my-10">
									<Spinner className="size-5" />
								</div>
							{:else if logs.length > 0}
								<table
									class="w-full text-sm text-left text-gray-500 dark:text-gray-400 table-fixed max-w-full rounded-sm}"
								>
									<thead
										class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-850 dark:text-gray-400 -translate-y-0.5"
									>
										<tr>
											<th scope="col" class="px-3 py-1.5 select-none w-3">
												{$i18n.t('Date')}
											</th>
											<th scope="col" class="px-3 py-1.5 select-none w-3">
												{$i18n.t('Credit')}
											</th>
											<th scope="col" class="px-3 py-1.5 select-none w-3">
												{$i18n.t('Model')}
											</th>
											<th scope="col" class="px-3 py-1.5 select-none w-3">
												{$i18n.t('Description')}
											</th>
										</tr>
									</thead>
									<tbody>
										{#each logs as log}
											<tr class="bg-white dark:bg-gray-900 dark:border-gray-850 text-xs group">
												<td
													class="px-3 py-1.5 text-left font-medium text-gray-900 dark:text-white w-fit"
												>
													<div class="line-clamp-1">
														{formatDate(log.created_at)}
													</div>
												</td>
												<td
													class="px-3 py-1.5 text-left font-medium text-gray-900 dark:text-white w-fit"
												>
													<div class="line-clamp-1">
														{parseFloat(log.credit).toFixed(6)}
													</div>
												</td>
												<td
													class="px-3 py-1.5 text-left font-medium text-gray-900 dark:text-white w-fit"
												>
													<div class="truncate">
														{log.detail?.api_params?.model?.name ||
															log.detail?.api_params?.model?.id ||
															'- -'}
													</div>
												</td>
												<td
													class="px-3 py-1.5 text-left font-medium text-gray-900 dark:text-white w-fit"
												>
													<div class="line-clamp-1">
														{formatDesc(log)}
													</div>
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
								{#if hasMore}
									<button
										class="text-xs mt-2"
										type="button"
										on:click={() => {
											nextLogs(true);
										}}
									>
										{$i18n.t('Load More')}
									</button>
								{/if}
							{:else}
								<div>{$i18n.t('No Log')}</div>
							{/if}
						</div>
					</div>
				</div>
			{/if}
		</div>
	</div>

	<Modal size="sm" bind:show={showRedemptionModal}>
		<div>
			<div class=" flex justify-between dark:text-gray-300 px-5 pt-4">
				<div class=" text-lg font-medium self-center">{$i18n.t('Redeem Credit Code')}</div>
				<button
					class="self-center"
					on:click={() => {
						showRedemptionModal = false;
					}}
				>
					<XMark className={'size-5'} />
				</button>
			</div>

			<div class="flex flex-col md:flex-row w-full px-4 pb-3 md:space-x-4 dark:text-gray-200">
				<div class="flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
					<form class="flex flex-col w-full" on:submit|preventDefault={() => handleRedeemCode()}>
						<div class="px-1">
							<div class="flex flex-col w-full mb-3 mt-3">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
										bind:value={redemptionCode}
										placeholder={$i18n.t('Enter your redemption code')}
										type="text"
										required
									/>
								</div>
							</div>
						</div>
						<div class="flex justify-end text-sm font-medium">
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {isSubmittingRedemption
									? ' cursor-not-allowed'
									: ''}"
								type="submit"
								disabled={isSubmittingRedemption}
							>
								{$i18n.t('Redeem')}

								{#if isSubmittingRedemption}
									<div class="ml-2 self-center">
										<Spinner />
									</div>
								{/if}
							</button>
						</div>
					</form>
				</div>
			</div>
		</div>
	</Modal>
</div>
