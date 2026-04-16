<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';

	import ProfileImage from '../Messages/ProfileImage.svelte';
	import ModelIcon from '$lib/components/common/ModelIcon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Heart from '$lib/components/icons/Heart.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import { getRenderableMessageError } from '$lib/utils/chat-message-errors';

	type $$Props = NodeProps;
	export let data: $$Props['data'];
	export let selected = false;

	$: isCurrent = data?.isCurrent ?? false;
	$: isOnCurrentPath = data?.isOnCurrentPath ?? false;
	$: renderableMessageError = getRenderableMessageError(data?.message?.error, data?.message?.files);
	$: renderableMessageErrorRecord =
		renderableMessageError &&
		renderableMessageError !== true &&
		typeof renderableMessageError === 'object'
			? (renderableMessageError as Record<string, unknown>)
			: null;
	$: renderableMessageErrorContent =
		renderableMessageErrorRecord
			? `${renderableMessageErrorRecord.content ?? ''}`
			: '';
</script>

<div
	class={`group relative w-60 h-20 overflow-hidden rounded-2xl border px-4 py-3 transition-all duration-200 ${
		selected
			? 'border-sky-300 bg-sky-50/95 ring-2 ring-sky-200/80 shadow-[0_14px_30px_-18px_rgba(14,165,233,0.75)] dark:border-sky-400/80 dark:bg-sky-950/45 dark:ring-sky-400/30'
			: isOnCurrentPath
				? 'border-sky-200/80 bg-sky-50/60 shadow-[0_10px_24px_-18px_rgba(14,165,233,0.5)] dark:border-sky-500/30 dark:bg-sky-950/25'
				: 'border-gray-200 bg-white shadow-md hover:-translate-y-0.5 hover:border-sky-200 hover:shadow-[0_12px_28px_-20px_rgba(14,165,233,0.55)] dark:border-gray-900 dark:bg-black dark:hover:border-sky-500/30'
	}`}
>
	<div
		class={`pointer-events-none absolute inset-x-0 top-0 h-1 transition-opacity duration-200 ${
			selected || isCurrent
				? 'bg-gradient-to-r from-sky-400 via-cyan-400 to-emerald-400 opacity-100'
				: isOnCurrentPath
					? 'bg-gradient-to-r from-sky-300/70 via-cyan-300/60 to-emerald-300/60 opacity-100 dark:from-sky-400/50 dark:via-cyan-400/40 dark:to-emerald-400/40'
					: 'opacity-0'
		}`}
	></div>
	<Tooltip
		content={renderableMessageErrorContent || data?.message?.content}
		class="w-full"
		allowHTML={false}
	>
		{#if data.message.role === 'user'}
			<div class="flex w-full">
				<div class="shrink-0">
					<ProfileImage
						src={data.user?.profile_image_url ?? '/user.png'}
						className={'size-5 -translate-y-[1px]'}
					/>
				</div>
				<div class="ml-2">
					<div class=" flex justify-between items-center">
						<div class="text-xs text-black dark:text-white font-medium line-clamp-1">
							{data?.user?.name ?? 'User'}
						</div>
					</div>

					{#if renderableMessageErrorContent}
						<div class="text-red-500 line-clamp-2 text-xs mt-0.5">{renderableMessageErrorContent}</div>
					{:else}
						<div class="text-gray-500 line-clamp-2 text-xs mt-0.5">{data.message.content}</div>
					{/if}
				</div>
			</div>
		{:else}
			<div class="flex w-full">
				<div class="shrink-0">
					<ModelIcon
						src={data?.model?.info?.meta?.profile_image_url ??
							data?.model?.meta?.profile_image_url ??
							'/static/favicon.png'}
						alt="model profile"
						className="size-5 rounded-lg -translate-y-[1px]"
					/>
				</div>

				<div class="ml-2">
					<div class=" flex justify-between items-center">
						<div class="text-xs text-black dark:text-white font-medium line-clamp-1">
							{getModelChatDisplayName(data?.model) || data?.message?.model || 'Assistant'}
						</div>

						<button
							class={data?.message?.favorite ? '' : 'invisible group-hover:visible'}
							on:click={() => {
								data.message.favorite = !(data?.message?.favorite ?? false);
							}}
						>
							<Heart
								className="size-3 {data?.message?.favorite
									? 'fill-red-500 stroke-red-500'
									: 'hover:fill-red-500 hover:stroke-red-500'} "
								strokeWidth="2.5"
							/>
						</button>
					</div>

					{#if renderableMessageErrorContent}
						<div class="text-red-500 line-clamp-2 text-xs mt-0.5">
							{renderableMessageErrorContent}
						</div>
					{:else}
						<div class="text-gray-500 line-clamp-2 text-xs mt-0.5">{data.message.content}</div>
					{/if}
				</div>
			</div>
		{/if}
	</Tooltip>
	<Handle
		type="target"
		position={Position.Top}
		class={`w-2 rounded-full ${
			selected || isOnCurrentPath ? '!bg-sky-400' : 'bg-gray-300 dark:bg-gray-900'
		}`}
	/>
	<Handle
		type="source"
		position={Position.Bottom}
		class={`w-2 rounded-full ${
			selected || isOnCurrentPath ? '!bg-sky-400' : 'bg-gray-300 dark:bg-gray-900'
		}`}
	/>
</div>
