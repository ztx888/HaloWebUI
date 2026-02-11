<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { models } from '$lib/stores';
	import { getTaskConfig, updateTaskConfig } from '$lib/apis';
	import type { Writable } from 'svelte/store';
	const i18n: Writable<any> = getContext('i18n');

	export let saveHandler: Function;

	let mathOcrModelId = '';
	let loading = true;

	onMount(async () => {
		const config = await getTaskConfig(localStorage.token);
		mathOcrModelId = config?.MATH_OCR_MODEL_ID ?? '';
		loading = false;
	});

	const submitHandler = async () => {
		const currentConfig = await getTaskConfig(localStorage.token);
		const updatedConfig = {
			...currentConfig,
			MATH_OCR_MODEL_ID: mathOcrModelId
		};
		await updateTaskConfig(localStorage.token, updatedConfig);
		saveHandler();
	};
</script>

<form class="flex flex-col h-full justify-between text-sm" on:submit|preventDefault={submitHandler}>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		{#if !loading}
			<div class="mb-3">
				<div class="mb-1.5 text-sm font-medium">{$i18n.t('公式转换模型')}</div>
				<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
					{$i18n.t('设置公式转换页面使用的模型。前台用户不可手动切换模型。')}
				</div>
				<div class="flex gap-2">
					<select
						class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-none"
						bind:value={mathOcrModelId}
					>
						<option value="">{$i18n.t('使用默认模型')}</option>
						{#each $models as model}
							<option value={model.id}>{model.name}</option>
						{/each}
					</select>
				</div>
			</div>
		{:else}
			<div class="flex justify-center py-8">
				<div class="text-gray-500">{$i18n.t('Loading...')}</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
