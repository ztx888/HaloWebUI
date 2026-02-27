<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Modal from '$lib/components/common/Modal.svelte';
	import { createRedemptionCodes } from '$lib/apis/credit';
	import XMark from '$lib/components/icons/XMark.svelte';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	export let show = false;

	let purpose = '';
	let count = 1;
	let amount = 0;
	let expiredAt = '';
	let loading = false;

	$: if (show) {
		// reset form when modal opens
		purpose = '';
		count = 1;
		amount = 0;
		expiredAt = '';
		loading = false;
	}

	const submitHandler = async () => {
		if (!purpose.trim()) {
			toast.error($i18n.t('Topic is required'));
			return;
		}

		if (count < 1 || count > 1000) {
			toast.error($i18n.t('Count must be between 1 and 1000'));
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
			const result = await createRedemptionCodes(
				localStorage.token,
				purpose.trim(),
				count,
				amount,
				expiredAtTimestamp
			);

			if (result) {
				toast.success(
					$i18n.t('Successfully created {{total}} redemption codes', { total: result.total })
				);
				dispatch('save');
				show = false;
			}
		} catch (error) {
			toast.error(`Failed to create redemption codes: ${error}`);
		} finally {
			loading = false;
		}
	};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-300 px-5 pt-4">
			<div class="text-lg font-medium self-center">{$i18n.t('Create Redemption Codes')}</div>
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
				<form
					class="flex flex-col w-full"
					on:submit|preventDefault={() => {
						submitHandler();
					}}
				>
					<div class="px-1">
						<div class="flex flex-col w-full">
							<div>
								<div class="text-sm font-medium py-1">{$i18n.t('Topic')} *</div>
								<input
									class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600"
									placeholder={$i18n.t('Enter topic for these codes')}
									bind:value={purpose}
									required
									maxlength="255"
								/>
							</div>

							<div class="grid grid-cols-2 gap-3">
								<div>
									<div class="text-sm font-medium py-1">{$i18n.t('Count')} *</div>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600"
										placeholder={$i18n.t('Number of redemption codes')}
										bind:value={count}
										type="number"
										step="1"
										min="1"
										max="1000"
										required
									/>
								</div>

								<div>
									<div class="text-sm font-medium py-1">{$i18n.t('Credit Amount')} *</div>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600"
										placeholder={$i18n.t('Credit amount')}
										bind:value={amount}
										type="number"
										step="0.01"
										min="0.01"
										required
									/>
								</div>
							</div>

							<div>
								<div class="text-sm font-medium py-1">
									{$i18n.t('Expiration Time')} ({$i18n.t('Optional')})
								</div>
								<input
									class="w-full rounded-lg py-2 px-4 text-sm dark:text-gray-300 dark:bg-gray-850 outline-none border border-gray-200 dark:border-gray-600"
									bind:value={expiredAt}
									type="datetime-local"
								/>
								<div class="text-xs text-gray-500 mt-1">
									{$i18n.t('Leave empty for no expiration')}
								</div>
							</div>
						</div>

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
									{$i18n.t('Creating...')}
								{:else}
									{$i18n.t('Create')}
								{/if}
							</button>
						</div>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>
