<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { getUsageConfig, setUsageConfig } from '$lib/apis/configs';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let config = null;

	const submitHandler = async () => {
		await setUsageConfig(localStorage.token, config);
	};

	onMount(async () => {
		const res = await getUsageConfig(localStorage.token);

		if (res) {
			config = res;
		}
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await submitHandler();
		saveHandler();
	}}
>
	<div class="space-y-4 overflow-y-scroll scrollbar-hidden h-full pr-2">
		{#if config}
			<div class="max-w-5xl mx-auto">
				<!-- Basic Credit Settings -->
				<div class="mb-4">
					<div class="flex items-center justify-between mb-3">
						<div class="text-base font-medium">{$i18n.t('Credit Configuration')}</div>
						<div class="flex items-center gap-2">
							<div class="text-sm font-medium text-gray-700 dark:text-gray-300">{$i18n.t('No Charge When Empty Response')}</div>
							<Switch bind:state={config.CREDIT_NO_CHARGE_EMPTY_RESPONSE} />
						</div>
					</div>
					
					<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800">
						<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('No Credit Message')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.CREDIT_NO_CREDIT_MSG}
									required
									placeholder={$i18n.t('Message to show when user has no credits')}
								/>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">
									{$i18n.t('Credit Exchange Ratio')}
								</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.CREDIT_EXCHANGE_RATIO}
									type="number"
									step="0.0001"
									required
								/>
								<div class="mt-1 text-xs text-gray-400">
									{$i18n.t('The exchange ratio of legal currency to credit. If you need a discount, please set it to be greater than 1')}
								</div>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Default Credit for User')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.CREDIT_DEFAULT_CREDIT}
									type="number"
									step="0.0001"
									required
								/>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Minimum Cost Per Request')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_MINIMUM_COST}
									type="number"
									step="0.0001"
									required
								/>
							</div>
						</div>
					</div>
				</div>

				<!-- Token Calculation -->
				<div class="mb-4">
					<div class="mb-3 text-base font-medium">{$i18n.t('Token Calculation')}</div>
					<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800">
						<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Model Prefix to Remove')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE}
									placeholder={$i18n.t('e.g. "openai/"')}
								/>
								<div class="mt-1 text-xs text-gray-400">{$i18n.t('Remove characters that prefix matched')}</div>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Default Encoding Model')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_DEFAULT_ENCODING_MODEL}
									required
									placeholder="gpt-3.5-turbo"
								/>
								<div class="mt-1 text-xs text-gray-400">{$i18n.t('Fallback encoding model (tiktoken)')}</div>
							</div>
						</div>
					</div>
				</div>

				<!-- Pricing Configuration -->
				<div class="mb-4">
					<div class="mb-3 text-base font-medium">{$i18n.t('Global Pricing')}</div>
					<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800">
						<div class="text-sm font-medium mb-3 text-gray-700 dark:text-gray-300">{$i18n.t('Feature Price (Per 1M Requests)')}</div>
						<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Image Generation')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE}
									type="number"
									step="0.000001"
								/>
							</div>
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Code Execution')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE}
									type="number"
									step="0.000001"
								/>
							</div>
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Web Search')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE}
									type="number"
									step="0.000001"
								/>
							</div>
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Direct Tool Servers')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE}
									type="number"
									step="0.000001"
								/>
							</div>
						</div>

						<hr class="border-gray-100 dark:border-gray-800 my-4" />
						
						<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Embedding Price (Per 1M Tokens)')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE}
									type="number"
									step="0.000001"
								/>
							</div>
						</div>
					</div>
				</div>
				
				<!-- Custom Price Configuration with JSON Editor feel -->
				<div class="mb-4">
					<div class="mb-3 text-base font-medium">{$i18n.t('Custom Price Configuration')}</div>
					<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800">
						<div class="text-xs text-gray-500 mb-2">
							{$i18n.t('JSON array for custom function/tool billing patterns. Example: [{"name": "web search", "cost": 1000000}]')}
						</div>
						<textarea
							class="w-full rounded-lg py-2 px-4 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition font-mono"
							bind:value={config.USAGE_CUSTOM_PRICE_CONFIG}
							placeholder={$i18n.t('Enter JSON array configuration')}
							rows="6"
						/>
					</div>
				</div>

				<!-- Alipay Settings -->
				<div class="mb-4">
					<div class="flex items-center gap-2 mb-3">
						<div class="w-6 h-6 rounded bg-blue-500 flex items-center justify-center text-white text-xs font-bold">支</div>
						<div class="text-base font-medium">{$i18n.t('Alipay Configuration')}</div>
					</div>
					
					<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800">
						<div class="grid grid-cols-1 gap-4">
							<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Server URL')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.ALIPAY_SERVER_URL}
										placeholder="https://openapi.alipay.com/gateway.do"
									/>
								</div>
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('AppID')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.ALIPAY_APP_ID}
									/>
								</div>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('App Private Key (PKCS#1)')}</div>
								<SensitiveInput
									outerClassName="w-full rounded-lg bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus-within:border-gray-300 dark:focus-within:border-gray-700 transition"
									inputClassName="w-full bg-transparent px-3 py-2 text-sm outline-none"
									bind:value={config.ALIPAY_APP_PRIVATE_KEY}
									required={false}
								/>
							</div>
							
							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Alipay Public Key')}</div>
								<SensitiveInput
									outerClassName="w-full rounded-lg bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus-within:border-gray-300 dark:focus-within:border-gray-700 transition"
									inputClassName="w-full bg-transparent px-3 py-2 text-sm outline-none"
									bind:value={config.ALIPAY_ALIPAY_PUBLIC_KEY}
									required={false}
								/>
							</div>

							<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Callback Host')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.ALIPAY_CALLBACK_HOST}
										placeholder="https://your-domain.com"
									/>
									<div class="mt-1 text-xs text-gray-400">{$i18n.t('Protocol and domain only, no path')}</div>
								</div>
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Product Code')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.ALIPAY_PRODUCT_CODE}
										placeholder="QUICK_MSECURITY_PAY"
									/>
								</div>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Recharge Options')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.ALIPAY_AMOUNT_CONTROL}
									placeholder="e.g. 10,20,50-100"
								/>
								<div class="mt-1 text-xs text-gray-400">{$i18n.t('Comma separated specific amounts or dash separated ranges')}</div>
							</div>
						</div>
					</div>
				</div>

				<!-- EZFP Payment Settings -->
				<div class="mb-4">
					<div class="flex items-center gap-2 mb-3">
						<div class="w-6 h-6 rounded bg-green-500 flex items-center justify-center text-white text-xs font-bold">E</div>
						<div class="text-base font-medium">{$i18n.t('EZFP Configuration')}</div>
					</div>
					
					<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800">
						<div class="grid grid-cols-1 gap-4">
							<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Endpoint URL')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.EZFP_ENDPOINT}
									/>
								</div>
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Merchant PID')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.EZFP_PID}
									/>
								</div>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Merchant Key')}</div>
								<SensitiveInput
									outerClassName="w-full rounded-lg bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus-within:border-gray-300 dark:focus-within:border-gray-700 transition"
									inputClassName="w-full bg-transparent px-3 py-2 text-sm outline-none"
									bind:value={config.EZFP_KEY}
									required={false}
								/>
							</div>

							<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Callback Host')}</div>
									<input
										class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
										bind:value={config.EZFP_CALLBACK_HOST}
									/>
								</div>
								<div>
									<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Payment Type Priority')}</div>
									<div class="relative">
										<select
											class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition appearance-none"
											bind:value={config.EZFP_PAY_PRIORITY}
										>
											<option value="qrcode">{$i18n.t('QRCode')}</option>
											<option value="link">{$i18n.t('Redirect Link')}</option>
										</select>
										<div class="absolute inset-y-0 right-0 flex items-center px-2 pointer-events-none text-gray-500">
											<svg class="w-4 h-4 fill-current" viewBox="0 0 20 20"><path d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" fill-rule="evenodd"></path></svg>
										</div>
									</div>
								</div>
							</div>

							<div>
								<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Recharge Options')}</div>
								<input
									class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
									bind:value={config.EZFP_AMOUNT_CONTROL}
									placeholder="e.g. 10,20,50-100"
								/>
							</div>
						</div>
					</div>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-4 py-2 text-sm font-semibold bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full shadow-sm"
			type="submit"
		>
			{$i18n.t('Save Configuration')}
		</button>
	</div>
</form>
