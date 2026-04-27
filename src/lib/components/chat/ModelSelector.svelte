<script lang="ts">
	import { models, modelsError, modelsStatus, user } from '$lib/stores';
	import { getContext } from 'svelte';
	import Selector from './ModelSelector/Selector.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import { getModelSelectionId, resolveModelSelectionId } from '$lib/utils/model-identity';
	import { getTemporaryChatAccess } from '$lib/utils/temporary-chat';
	const i18n = getContext('i18n');

	export let selectedModels = [''];
	export let disabled = false;

	export let showSetDefault = true;

	// Stable items array: only recomputed when $models reference changes
	$: selectorItems = $models.map((model) => ({
		value: getModelSelectionId(model),
		label: getModelChatDisplayName(model),
		model: model
	}));

	$: if (selectedModels.length > 0 && $models.length > 0) {
		const normalizedModels = selectedModels.map((model) =>
			resolveModelSelectionId($models, model, { preserveAmbiguous: true, preserveMissing: true })
		);
		if (JSON.stringify(normalizedModels) !== JSON.stringify(selectedModels)) {
			selectedModels = normalizedModels;
		}
	}

	let temporaryChatAccess = { allowed: true, enforced: false };
	$: temporaryChatAccess = getTemporaryChatAccess($user);
</script>

<div class="flex flex-col w-full items-start">
	{#if $modelsStatus === 'loading' && $models.length === 0}
		<div class="flex items-center gap-2 text-xs text-gray-500 ml-1 pb-1">
			<Spinner className="size-3.5" />
			<span>Loading models...</span>
		</div>
	{:else if $modelsStatus === 'error' && $models.length === 0}
		<div class="text-xs text-gray-500 ml-1 pb-1">
			<span>Failed to load models</span>
			{#if $modelsError}
				<span class="text-gray-400">: {$modelsError}</span>
			{/if}
		</div>
	{/if}

	{#if selectedModels.length <= 1}
		<!-- 单模型：保持原有的完整下拉选择器样式 -->
		{#each selectedModels as selectedModel, selectedModelIdx}
			<div class="flex flex-wrap w-full max-w-fit items-center gap-1.5">
				<div class="overflow-hidden">
					<div class="max-w-full">
						<Selector
							id={`${selectedModelIdx}`}
							placeholder={$i18n.t('Select a model')}
							items={selectorItems}
							showSetDefaultAction={showSetDefault && selectedModelIdx === 0}
							showTemporaryChatControl={temporaryChatAccess.allowed && !temporaryChatAccess.enforced}
							bind:value={selectedModel}
						/>
					</div>
				</div>

				{#if $user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true)}
					<div class="shrink-0">
						<Tooltip content={$i18n.t('Add Model')}>
							<button
								class="inline-flex items-center justify-center
									size-7 rounded-lg
									text-gray-500 dark:text-gray-400
									bg-transparent
									border border-transparent
									hover:bg-gray-100/80 dark:hover:bg-gray-800/60
									hover:border-gray-200/50 dark:hover:border-gray-700/30
									hover:text-gray-700 dark:hover:text-gray-200
									active:scale-[0.92]
									transition-all duration-150
									disabled:opacity-40 disabled:pointer-events-none"
								{disabled}
								on:click={() => {
									selectedModels = [...selectedModels, ''];
								}}
								aria-label="Add Model"
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-3.5"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M12 6v12m6-6H6" />
								</svg>
							</button>
						</Tooltip>
					</div>
				{/if}
			</div>
		{/each}
	{:else}
		<!-- 多模型：紧凑水平 chips 流式布局 -->
		<div class="flex flex-wrap items-center gap-1.5 max-w-full">
			{#each selectedModels as selectedModel, selectedModelIdx}
				{@const modelItem = selectorItems.find((item) => item.value === selectedModel)}
				<div class="flex items-center gap-0.5 group/chip">
					<!-- 模型 Selector 下拉（点击弹出更换模型） -->
					<div class="overflow-hidden max-w-[200px]">
						<Selector
							id={`${selectedModelIdx}`}
							placeholder={$i18n.t('Select a model')}
							items={selectorItems}
							showSetDefaultAction={showSetDefault && selectedModelIdx === 0}
							showTemporaryChatControl={selectedModelIdx === 0 && temporaryChatAccess.allowed && !temporaryChatAccess.enforced}
							triggerClassName="text-sm"
							bind:value={selectedModel}
						/>
					</div>
					<!-- 删除按钮（第一个模型不显示删除，只在 hover 时显示） -->
					{#if selectedModelIdx > 0}
						<Tooltip content={$i18n.t('Remove Model')}>
							<button
								class="inline-flex items-center justify-center
									size-5 rounded-md
									text-gray-400 dark:text-gray-500
									bg-transparent
									opacity-0 group-hover/chip:opacity-100
									hover:bg-red-50/80 dark:hover:bg-red-900/20
									hover:text-red-500 dark:hover:text-red-400
									active:scale-[0.88]
									transition-all duration-150
									disabled:opacity-40 disabled:pointer-events-none"
								{disabled}
								on:click={() => {
									selectedModels.splice(selectedModelIdx, 1);
									selectedModels = selectedModels;
								}}
								aria-label="Remove Model"
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2.5"
									stroke="currentColor"
									class="size-3"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
								</svg>
							</button>
						</Tooltip>
					{/if}
				</div>
			{/each}

			<!-- 添加模型按钮 - 与 chips 同行 -->
			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true)}
				<Tooltip content={$i18n.t('Add Model')}>
					<button
						class="inline-flex items-center justify-center
							size-6 rounded-lg
							text-gray-400 dark:text-gray-500
							bg-transparent
							border border-dashed border-gray-300/60 dark:border-gray-600/40
							hover:bg-gray-100/80 dark:hover:bg-gray-800/60
							hover:border-gray-400/60 dark:hover:border-gray-500/50
							hover:text-gray-600 dark:hover:text-gray-300
							active:scale-[0.92]
							transition-all duration-150
							disabled:opacity-40 disabled:pointer-events-none"
						{disabled}
						on:click={() => {
							selectedModels = [...selectedModels, ''];
						}}
						aria-label="Add Model"
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							class="size-3"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M12 6v12m6-6H6" />
						</svg>
					</button>
				</Tooltip>
			{/if}
		</div>
	{/if}
</div>
