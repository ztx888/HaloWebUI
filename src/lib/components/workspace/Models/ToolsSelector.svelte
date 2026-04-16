<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import { getContext, onMount } from 'svelte';

	export let tools = [];

	let _tools = {};

	export let selectedToolIds = [];

	const i18n = getContext('i18n');

	onMount(() => {
		_tools = tools.reduce((acc, tool) => {
			acc[tool.id] = {
				...tool,
				selected: selectedToolIds.includes(tool.id)
			};

			return acc;
		}, {});
	});
</script>

<div>
	<div class="text-sm font-medium mb-2">{$i18n.t('Tools')}</div>

	<div class="text-xs text-gray-500 dark:text-gray-400 mb-3">
		{$i18n.t('To select toolkits here, add them to the "Tools" workspace first.')}
	</div>

	{#if tools.length > 0}
		<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
			{#each Object.keys(_tools) as tool}
				<label class="flex items-center gap-2.5 py-1.5 px-2 rounded-lg hover:bg-gray-50/50 dark:hover:bg-gray-800/30 cursor-pointer transition-colors">
					<Checkbox
						state={_tools[tool].selected ? 'checked' : 'unchecked'}
						on:change={(e) => {
							_tools[tool].selected = e.detail === 'checked';
							selectedToolIds = Object.keys(_tools).filter((t) => _tools[t].selected);
						}}
					/>
					<div class="min-w-0">
						<div class="flex items-center gap-1.5">
							<span class="text-sm capitalize font-medium truncate">{_tools[tool].name}</span>
							{#if _tools[tool]?.meta?.source === 'shared'}
								<span class="shrink-0 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">
									共享
								</span>
							{/if}
						</div>
						{#if _tools[tool]?.meta?.source === 'shared' && _tools[tool]?.meta?.owner_name}
							<div class="text-[11px] text-gray-500 dark:text-gray-400">
								管理员：{_tools[tool].meta.owner_name}
							</div>
						{/if}
					</div>
				</label>
			{/each}
		</div>
	{/if}
</div>
