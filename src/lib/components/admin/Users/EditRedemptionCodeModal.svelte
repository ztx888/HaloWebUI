<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Modal from '$lib/components/common/Modal.svelte';
	import { updateRedemptionCode } from '$lib/apis/credit';
	import XMark from '$lib/components/icons/XMark.svelte';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	export let show = false;
	export let code: any = null;

	let purpose = '';
	let amount = 0;
	let expiredAt = '';
	let loading = false;

	// add flag to prevent reinitializing form data
	let initialized = false;

	// check if code is already used
	$: isCodeUsed = code?.received_at;

	$: if (show && code && !initialized) {
		// populate form when modal opens with code data
		purpose = code.purpose || '';
		amount = parseFloat(code.amount) || 0;
		expiredAt = code.expired_at ? formatDatetimeLocal(code.expired_at) : '';
		loading = false;
		initialized = true;
	}

	// reset initialization flag when modal closes
	$: if (!show) {
		initialized = false;
	}

	const submitHandler = async () => {
		if (!code) {
			toast.error($i18n.t('No redemption code selected'));
			return;
		}

		// prevent editing used codes
		if (isCodeUsed) {
			toast.error($i18n.t('Cannot edit a used redemption code'));
			return;
		}

		if (!purpose.trim()) {
			toast.error($i18n.t('Topic is required'));
			return;
		}

		if (amount <= 0) {
			toast.error($i18n.t('Amount must be greater than 0'));
			return;
		}

		const expiredAtTimestamp = expiredAt
			? Math.floor(new Date(expiredAt).getTime() / 1000)
			: undefined;

		if (expiredAtTimestamp && expiredAtTimestamp <= Math.floor(Date.now() / 1000)) {
			toast.error($i18n.t('Expiration time must be in the future'));
			return;
		}

		loading = true;

		try {
			await updateRedemptionCode(
				localStorage.token,
				code.code,
				purpose.trim(),
				amount,
				expiredAtTimestamp
			);

			toast.success($i18n.t('Redemption code updated successfully'));
			dispatch('save');
			show = false;
		} catch (error) {
			toast.error(`Failed to update redemption code: ${error}`);
		} finally {
			loading = false;
		}
	};

	// format datetime-local input value
	const formatDatetimeLocal = (timestamp?: number): string => {
		if (!timestamp) return '';
		const date = new Date(timestamp * 1000);
		return date.toISOString().slice(0, 16);
	};

	const formatDate = (timestamp: number): string => {
		return timestamp ? new Date(timestamp * 1000).toLocaleString() : '-';
	};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-300 px-5 pt-4">
			<div class="text-lg font-medium self-center">{$i18n.t('Edit Redemption Code')}</div>
			<button
				class="self-center"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-4 pb-3 md:space-x-4 dark:text-gray-200">
			<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				{#if code}
					<div class="flex flex-col w-full mb-4 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
						<div class="text-sm font-medium mb-2">{$i18n.t('Redemption Information')}</div>
						<div class="text-xs space-y-1">
							<div>
								<span class="font-medium">{$i18n.t('Redemption Code')}:</span>
								<span class="font-mono">{code.code}</span>
							</div>
							<div>
								<span class="font-medium">{$i18n.t('Created At')}:</span>
								{formatDate(code.created_at)}
							</div>
							{#if code.received_at}
								<div>
									<span class="font-medium">{$i18n.t('Used At')}:</span>
									{formatDate(code.received_at)}
								</div>
								<div>
									<span class="font-medium">{$i18n.t('Used By')}:</span>
									{code.username || code.user_id}
								</div>
							{/if}
							{#if code.expired_at}
								<div>
									<span class="font-medium">{$i18n.t('Expiration Time')}:</span>
									{formatDate(code.expired_at)}
								</div>
							{/if}
						</div>
					</div>
				{/if}

				<form
					class="flex flex-col w-full"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div class="flex flex-col gap-3">
						<div>
							<div class="text-sm font-medium py-1">{$i18n.t('Topic')} *</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
								placeholder={$i18n.t('Enter topic for this code')}
								bind:value={purpose}
								disabled={isCodeUsed}
								required
								maxlength="255"
							/>
						</div>

						<div>
							<div class="text-sm font-medium py-1">{$i18n.t('Credit Amount')} *</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
								placeholder={$i18n.t('Credit amount')}
								bind:value={amount}
								type="number"
								step="0.01"
								min="0.01"
								disabled={isCodeUsed}
								required
							/>
						</div>

						<div>
							<div class="text-sm font-medium py-1">
								{$i18n.t('Expiration Time')} ({$i18n.t('Optional')})
							</div>
							<input
								class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
								bind:value={expiredAt}
								type="datetime-local"
								disabled={isCodeUsed}
							/>
							<div class="text-xs text-gray-500 mt-1">
								{$i18n.t('Leave empty for no expiration')}
							</div>
						</div>
					</div>

					{#if isCodeUsed}
						<div class="text-red-500 text-xs mt-2">
							{$i18n.t('This redemption code has already been used and cannot be edited')}
						</div>
					{:else}
						<div class="flex justify-end pt-3">
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
								type="submit"
								disabled={loading}
							>
								{#if loading}
									<div
										class="animate-spin inline-block w-4 h-4 border-[3px] border-current border-t-transparent text-white rounded-full"
									></div>
									{$i18n.t('Updating...')}
								{:else}
									{$i18n.t('Update')}
								{/if}
							</button>
						</div>
					{/if}
				</form>
			</div>
		</div>
	</div>
</Modal>
