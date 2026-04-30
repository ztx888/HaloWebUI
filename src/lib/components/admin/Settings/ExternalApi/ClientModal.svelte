<script lang="ts">
	import type { Writable } from 'svelte/store';
	import { createEventDispatcher, getContext } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import { getModelIdentityAliases, getModelSelectionId } from '$lib/utils/model-identity';

	const i18n: Writable<any> = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let client: any = null;
	export let users: any[] = [];
	export let models: any[] = [];

	let name = '';
	let ownerUserId = '';
	let allowTools = false;
	let enabled = true;
	let rpmLimit = 60;
	let note = '';
	let protocolOpenAI = true;
	let protocolAnthropic = true;
	let allowedModelIds: string[] = [];
	let modelSearch = '';
	let prevShow = false;

	const resetForm = (currentClient: any) => {
		name = currentClient?.name ?? '';
		ownerUserId = currentClient?.owner_user_id ?? users?.[0]?.id ?? '';
		allowTools = currentClient?.allow_tools ?? false;
		enabled = currentClient?.enabled ?? true;
		rpmLimit = currentClient?.rpm_limit ?? 60;
		note = currentClient?.note ?? '';
		const protocols = new Set(
			(currentClient?.allowed_protocols ?? ['openai', 'anthropic']).map((item) => String(item))
		);
		protocolOpenAI = protocols.has('openai');
		protocolAnthropic = protocols.has('anthropic');
		allowedModelIds = Array.isArray(currentClient?.allowed_model_ids)
			? [...currentClient.allowed_model_ids]
			: [];
		modelSearch = '';
	};

	$: {
		if (show && !prevShow) {
			resetForm(client);
		}
		prevShow = show;
	}

	$: filteredModels = (models ?? []).filter((model) => {
		const keyword = modelSearch.trim().toLowerCase();
		if (!keyword) return true;

		return [
			getModelChatDisplayName(model),
			getModelSelectionId(model),
			model?.name,
			model?.id
		]
			.filter(Boolean)
			.some((value) => String(value).toLowerCase().includes(keyword));
	});

	const getModelValue = (model: any) => getModelSelectionId(model) || String(model?.id ?? '').trim();

	const getModelAliases = (model: any) =>
		getModelIdentityAliases(model).filter((value) => String(value).trim());

	const isModelSelected = (model: any) =>
		getModelAliases(model).some((value) => allowedModelIds.includes(value));

	const toggleModel = (model: any, checked: boolean) => {
		const aliases = getModelAliases(model);
		const value = getModelValue(model);
		if (!value) return;

		if (checked) {
			allowedModelIds = Array.from(
				new Set([...allowedModelIds.filter((item) => !aliases.includes(item)), value])
			);
			return;
		}

		allowedModelIds = allowedModelIds.filter((item) => !aliases.includes(item));
	};

	const buildProtocolList = () => {
		const protocols = [];
		if (protocolOpenAI) protocols.push('openai');
		if (protocolAnthropic) protocols.push('anthropic');
		return protocols;
	};

	const submitHandler = () => {
		dispatch('submit', {
			id: client?.id ?? null,
			data: {
				name,
				owner_user_id: ownerUserId,
				allowed_protocols: buildProtocolList(),
				allowed_model_ids: allowedModelIds,
				allow_tools: allowTools,
				rpm_limit: Number(rpmLimit) || 0,
				note: note || null,
				enabled
			}
		});
		show = false;
	};
</script>

<Modal size="md" bind:show>
	<div class="px-5 pt-4 pb-5 text-sm dark:text-gray-100">
		<div class="flex items-center justify-between pb-2">
			<div class="text-lg font-medium">{client ? '编辑外部客户端' : '新增外部客户端'}</div>
			<button type="button" on:click={() => (show = false)}>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-5 h-5">
					<path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
				</svg>
			</button>
		</div>

		<form class="space-y-3" on:submit|preventDefault={submitHandler}>
			<div>
				<label class="mb-1 block text-sm font-medium">名称</label>
				<input class="w-full" bind:value={name} placeholder="例如：Acme Codex Gateway" required />
			</div>

			<div>
				<label class="mb-1 block text-sm font-medium">绑定用户</label>
				<HaloSelect
					bind:value={ownerUserId}
					className="w-full"
					searchEnabled={true}
					placeholder="选择一个内部用户"
					options={(users ?? []).map((user) => ({
						value: user.id,
						label: `${user.name || user.email} (${user.email})`
					}))}
				/>
			</div>

			<div class="grid grid-cols-2 gap-3">
				<label class="flex items-center justify-between rounded-xl border border-gray-200 dark:border-gray-700 px-3 py-2">
					<span>OpenAI 协议</span>
					<input type="checkbox" bind:checked={protocolOpenAI} />
				</label>
				<label class="flex items-center justify-between rounded-xl border border-gray-200 dark:border-gray-700 px-3 py-2">
					<span>Anthropic 协议</span>
					<input type="checkbox" bind:checked={protocolAnthropic} />
				</label>
			</div>

			<div class="grid grid-cols-2 gap-3">
				<label class="flex items-center justify-between rounded-xl border border-gray-200 dark:border-gray-700 px-3 py-2">
					<span>允许工具调用</span>
					<input type="checkbox" bind:checked={allowTools} />
				</label>
				<label class="flex items-center justify-between rounded-xl border border-gray-200 dark:border-gray-700 px-3 py-2">
					<span>启用</span>
					<input type="checkbox" bind:checked={enabled} />
				</label>
			</div>

			<div>
				<label class="mb-1 block text-sm font-medium">每分钟请求上限</label>
				<input class="w-full" type="number" min="0" bind:value={rpmLimit} />
			</div>

			<div>
				<label class="mb-1 block text-sm font-medium">允许的模型</label>
				<input
					class="mb-2 w-full"
					bind:value={modelSearch}
					placeholder="搜索模型名称或 ID"
				/>
				<div class="max-h-48 overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700 p-2 space-y-1">
					{#if filteredModels.length === 0}
						<div class="px-2 py-6 text-center text-xs text-gray-400">
							没有找到匹配的模型
						</div>
					{:else}
						{#each filteredModels as model}
							<label class="flex items-start gap-2 rounded-lg px-2 py-1 hover:bg-gray-50 dark:hover:bg-gray-800/50">
								<input
									type="checkbox"
									checked={isModelSelected(model)}
									on:change={(event) => {
										const target = event.currentTarget;
										const checked = target instanceof HTMLInputElement ? target.checked : false;
										toggleModel(model, checked);
									}}
								/>
								<div class="min-w-0">
									<div class="truncate text-sm">
										{getModelChatDisplayName(model) || model.name || model.id}
									</div>
									<div class="truncate text-xs text-gray-500">
										{getModelValue(model) || model.id}
									</div>
								</div>
							</label>
						{/each}
					{/if}
				</div>
			</div>

			<div>
				<label class="mb-1 block text-sm font-medium">备注</label>
				<textarea class="w-full" rows="3" bind:value={note} placeholder="可填写客户名、用途或接入说明"></textarea>
			</div>

			<div class="flex justify-end gap-2 pt-2">
				<button type="button" class="px-4 py-2 rounded-xl bg-gray-100 dark:bg-gray-800" on:click={() => (show = false)}>
					取消
				</button>
				<button
					type="submit"
					class="px-4 py-2 rounded-xl bg-blue-500 text-white disabled:opacity-50"
					disabled={!name.trim() || !ownerUserId || buildProtocolList().length === 0}
				>
					保存
				</button>
			</div>
		</form>
	</div>
</Modal>
