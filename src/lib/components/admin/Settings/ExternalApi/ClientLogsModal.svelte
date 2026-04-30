<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { getContext } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';

	const i18n: Writable<any> = getContext('i18n');

	export let show = false;
	export let client: any = null;
	export let logs: any[] = [];

	function formatTime(ts: number) {
		if (!ts) return '';
		return new Date(ts).toLocaleString('zh-CN');
	}
</script>

<Modal size="lg" bind:show>
	<div class="px-5 pt-4 pb-5 text-sm dark:text-gray-100">
		<div class="flex items-center justify-between pb-2">
			<div class="text-lg font-medium">
				最近调用日志
				{#if client}
					<span class="ml-1 text-sm text-gray-400">- {client.name}</span>
				{/if}
			</div>
			<button type="button" on:click={() => (show = false)}>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-5 h-5">
					<path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
				</svg>
			</button>
		</div>

		<div class="max-h-[32rem] overflow-y-auto space-y-2">
			{#if logs.length === 0}
				<div class="py-8 text-center text-gray-400">暂无日志</div>
			{:else}
				{#each logs as log}
					<div class="rounded-2xl border border-gray-200 dark:border-gray-700 px-4 py-3">
						<div class="flex flex-wrap items-center gap-2 text-xs text-gray-500">
							<span>{formatTime(log.created_at)}</span>
							<span>{log.protocol}</span>
							<span>{log.endpoint}</span>
							<span>HTTP {log.status_code}</span>
							{#if log.model}<span>{log.model}</span>{/if}
							{#if log.tools_used}<span>工具</span>{/if}
						</div>
						<div class="mt-1 text-sm">
							{#if log.error}
								<div class="text-red-500 break-words">{log.error}</div>
							{:else}
								<div class="text-gray-700 dark:text-gray-200">
									prompt={log.prompt_tokens ?? '-'} / completion={log.completion_tokens ?? '-'} / latency={log.latency_ms ?? '-'}ms
								</div>
							{/if}
						</div>
					</div>
				{/each}
			{/if}
		</div>
	</div>
</Modal>
