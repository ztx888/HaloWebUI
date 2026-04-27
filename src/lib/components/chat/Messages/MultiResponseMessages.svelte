<script lang="ts">
	import dayjs from 'dayjs';
	import { onMount, tick, getContext } from 'svelte';
	import { createEventDispatcher } from 'svelte';

	import { mobile, settings, models } from '$lib/stores';

	import { generateMoACompletion } from '$lib/apis';
	import { updateChatById } from '$lib/apis/chats';
	import { createOpenAITextStream } from '$lib/apis/streaming';

	import ResponseMessage from './ResponseMessage.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Merge from '$lib/components/icons/Merge.svelte';
	import { getModelChatDisplayName } from '$lib/utils/model-display';
	import { findModelByIdentity } from '$lib/utils/model-identity';

	import Markdown from './Markdown.svelte';
	import Name from './Name.svelte';
	import Skeleton from './Skeleton.svelte';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	const i18n = getContext('i18n');
	dayjs.extend(localizedFormat);

	export let chatId;
	export let history;
	export let messageId;

	export let isLastMessage;
	export let readOnly = false;

	export let updateChat: Function;
	export let editMessage: Function;
	export let saveMessage: Function;
	export let actionMessage: Function;

	export let submitMessage: Function;
	export let deleteMessage: Function;

	export let continueResponse: Function;
	export let regenerateResponse: Function;
	export let mergeResponses: Function;

	export let addMessages: Function;
	export let onBranchMessage: Function = () => {};
	export let branchingMessageId: string | null = null;
	export let branchSupported = false;

	export let triggerScroll: Function;

	const dispatch = createEventDispatcher();

	let currentMessageId;
	let parentMessage;
	let groupedMessageIds = {};
	let groupedMessageIdsIdx = {};
	let selectedModelIdx = null;

	let message = history.messages?.[messageId];
	$: message = history.messages?.[messageId];

	const gotoMessage = async (modelIdx, messageIdx) => {
		// Clamp messageIdx to ensure it's within valid range
		groupedMessageIdsIdx[modelIdx] = Math.max(
			0,
			Math.min(messageIdx, groupedMessageIds[modelIdx].messageIds.length - 1)
		);

		// Get the messageId at the specified index
		let messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];

		// Traverse the branch to find the deepest child message
		let messageChildrenIds = history.messages[messageId].childrenIds;
		while (messageChildrenIds.length !== 0) {
			messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[messageId].childrenIds;
		}

		// Update the current message ID in history
		history.currentId = messageId;

		// Await UI updates
		await tick();
		await updateChat();

		// Trigger scrolling after navigation
		triggerScroll();
	};

	const showPreviousMessage = async (modelIdx) => {
		groupedMessageIdsIdx[modelIdx] = Math.max(0, groupedMessageIdsIdx[modelIdx] - 1);

		let messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];

		let messageChildrenIds = history.messages[messageId].childrenIds;

		while (messageChildrenIds.length !== 0) {
			messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[messageId].childrenIds;
		}

		history.currentId = messageId;

		await tick();
		await updateChat();
		triggerScroll();
	};

	const showNextMessage = async (modelIdx) => {
		groupedMessageIdsIdx[modelIdx] = Math.min(
			groupedMessageIds[modelIdx].messageIds.length - 1,
			groupedMessageIdsIdx[modelIdx] + 1
		);

		let messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];

		let messageChildrenIds = history.messages[messageId].childrenIds;

		while (messageChildrenIds.length !== 0) {
			messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[messageId].childrenIds;
		}

		history.currentId = messageId;

		await tick();
		await updateChat();
		triggerScroll();
	};

	const initHandler = async () => {
		await tick();

		currentMessageId = messageId;
		parentMessage = history.messages[messageId].parentId
			? history.messages[history.messages[messageId].parentId]
			: null;

		groupedMessageIds = parentMessage?.models.reduce((a, model, modelIdx) => {
			// Find all messages that are children of the parent message and have the same model
			let modelMessageIds = parentMessage?.childrenIds
				.map((id) => history.messages[id])
				.filter((m) => m?.modelIdx === modelIdx)
				.map((m) => m.id);

			// Legacy support for messages that don't have a modelIdx
			// Find all messages that are children of the parent message and have the same model
			if (modelMessageIds.length === 0) {
				let modelMessages = parentMessage?.childrenIds
					.map((id) => history.messages[id])
					.filter((m) => m?.model === model);

				modelMessages.forEach((m) => {
					m.modelIdx = modelIdx;
				});

				modelMessageIds = modelMessages.map((m) => m.id);
			}

			return {
				...a,
				[modelIdx]: { messageIds: modelMessageIds }
			};
		}, {});

		groupedMessageIdsIdx = parentMessage?.models.reduce((a, model, modelIdx) => {
			const idx = groupedMessageIds[modelIdx].messageIds.findIndex((id) => id === messageId);
			if (idx !== -1) {
				selectedModelIdx = modelIdx;
				return {
					...a,
					[modelIdx]: idx
				};
			} else {
				return {
					...a,
					[modelIdx]: groupedMessageIds[modelIdx].messageIds.length - 1
				};
			}
		}, {});

		if (selectedModelIdx === null) {
			selectedModelIdx = Object.keys(groupedMessageIds ?? {}).at(0) ?? null;
		}

		await tick();
	};

	const mergeResponsesHandler = async () => {
		const responses = Object.keys(groupedMessageIds).map((modelIdx) => {
			const { messageIds } = groupedMessageIds[modelIdx];
			const messageId = messageIds[groupedMessageIdsIdx[modelIdx]];

			return history.messages[messageId].content;
		});
		mergeResponses(messageId, responses, chatId);
	};

	const onGroupClick = async (_messageId, modelIdx) => {
		if (messageId != _messageId) {
			let currentMessageId = _messageId;
			let messageChildrenIds = history.messages[currentMessageId].childrenIds;
			while (messageChildrenIds.length !== 0) {
				currentMessageId = messageChildrenIds.at(-1);
				messageChildrenIds = history.messages[currentMessageId].childrenIds;
			}
			history.currentId = currentMessageId;
			selectedModelIdx = modelIdx;

			await tick();
			await updateChat();
			triggerScroll();
		}
	};

	// Keyboard navigation for tab switching
	const handleTabKeydown = (event: KeyboardEvent) => {
		const modelKeys = Object.keys(groupedMessageIds);
		const currentIdx = modelKeys.indexOf(String(selectedModelIdx));
		let newIdx = -1;

		if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
			event.preventDefault();
			newIdx = (currentIdx + 1) % modelKeys.length;
		} else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
			event.preventDefault();
			newIdx = (currentIdx - 1 + modelKeys.length) % modelKeys.length;
		}

		if (newIdx >= 0) {
			const modelIdx = modelKeys[newIdx];
			const _messageId = groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]];
			selectedModelIdx = modelIdx;
			onGroupClick(_messageId, modelIdx);
		}
	};

	// Horizontal scroll: convert vertical wheel to horizontal
	const handleTabWheel = (event: WheelEvent) => {
		const container = event.currentTarget as HTMLElement;
		if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
			container.scrollLeft += event.deltaY;
			event.preventDefault();
		}
	};

	onMount(async () => {
		await initHandler();
		await tick();

		if ($settings?.scrollOnBranchChange ?? true) {
			const messageElement = document.getElementById(`message-${messageId}`);
			if (messageElement) {
				messageElement.scrollIntoView({ block: 'start' });
			}
		}
	});
</script>

{#if parentMessage}
	<div>
		<div
			class="flex snap-x snap-mandatory overflow-x-auto scrollbar-hidden"
			id="responses-container-{chatId}-{parentMessage.id}"
			on:wheel|passive={(event) => {
				const container = event.currentTarget;
				if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
					container.scrollLeft += event.deltaY;
				}
			}}
		>
			{#if $settings?.displayMultiModelResponsesInTabs ?? false}
				<div class="w-full">
					<div class="flex w-full mb-4 border-b border-gray-200 dark:border-gray-850">
						<div
							class="flex gap-2 overflow-x-auto scrollbar-none text-sm font-medium pt-1"
							role="tablist"
							aria-label="Model responses"
							on:keydown={handleTabKeydown}
							on:wheel|passive={handleTabWheel}
						>
							{#each Object.keys(groupedMessageIds) as modelIdx}
								{#if groupedMessageIdsIdx[modelIdx] !== undefined && groupedMessageIds[modelIdx].messageIds.length > 0}
									{@const _messageId =
										groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]}
									{@const tabModel = findModelByIdentity($models, history.messages[_messageId]?.model)}
									<button
										role="tab"
										aria-selected={selectedModelIdx == modelIdx}
										tabindex={selectedModelIdx == modelIdx ? 0 : -1}
										class="min-w-fit pb-1.5 px-2.5 border-b-2 transition {selectedModelIdx ==
										modelIdx
											? 'border-gray-400 dark:border-gray-300'
											: 'border-transparent opacity-50'}"
										on:click={() => {
											selectedModelIdx = modelIdx;
											onGroupClick(_messageId, modelIdx);
										}}
									>
										{tabModel
											? getModelChatDisplayName(tabModel) || tabModel.name
											: history.messages[_messageId]?.modelName || history.messages[_messageId]?.model}
									</button>
								{/if}
							{/each}
						</div>
					</div>

					{#if selectedModelIdx !== null}
						{@const _messageId =
							groupedMessageIds[selectedModelIdx].messageIds[
								groupedMessageIdsIdx[selectedModelIdx]
							]}
						<div role="tabpanel">
							{#key history.currentId}
								{#if message}
									<ResponseMessage
										{chatId}
										{history}
										messageId={_messageId}
										isLastMessage={true}
										siblings={groupedMessageIds[selectedModelIdx].messageIds}
										gotoMessage={(message, messageIdx) => gotoMessage(selectedModelIdx, messageIdx)}
										showPreviousMessage={() => showPreviousMessage(selectedModelIdx)}
										showNextMessage={() => showNextMessage(selectedModelIdx)}
										{updateChat}
										{editMessage}
										{saveMessage}
										{deleteMessage}
										{actionMessage}
										{submitMessage}
										{continueResponse}
										regenerateResponse={async (message) => {
											regenerateResponse(message);
											await tick();
											groupedMessageIdsIdx[selectedModelIdx] =
												groupedMessageIds[selectedModelIdx].messageIds.length - 1;
										}}
										{addMessages}
										{onBranchMessage}
										{branchingMessageId}
										{branchSupported}
										{readOnly}
									/>
								{/if}
							{/key}
						</div>
					{/if}
				</div>
			{:else}
				{#each Object.keys(groupedMessageIds) as modelIdx}
					{#if groupedMessageIdsIdx[modelIdx] !== undefined && groupedMessageIds[modelIdx].messageIds.length > 0}
						{@const _messageId =
							groupedMessageIds[modelIdx].messageIds[groupedMessageIdsIdx[modelIdx]]}

						<div
							class="snap-center w-full max-w-full m-1 rounded-2xl p-5 transition-all duration-200 {history.messages[messageId]
								?.modelIdx == modelIdx
								? `border-[1.5px] border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-md ${
										$mobile ? 'min-w-full' : 'min-w-80'
									}`
								: `border border-dashed border-gray-200 dark:border-gray-800 opacity-75 hover:opacity-90 ${
										$mobile ? 'min-w-full' : 'min-w-80'
									}`}"
							on:click={() => {
								onGroupClick(_messageId, modelIdx);
							}}
						>
							{#key history.currentId}
								{#if message}
									<ResponseMessage
										{chatId}
										{history}
										messageId={_messageId}
										isLastMessage={true}
										siblings={groupedMessageIds[modelIdx].messageIds}
										gotoMessage={(message, messageIdx) => gotoMessage(modelIdx, messageIdx)}
										showPreviousMessage={() => showPreviousMessage(modelIdx)}
										showNextMessage={() => showNextMessage(modelIdx)}
										{updateChat}
										{editMessage}
										{saveMessage}
										{deleteMessage}
										{actionMessage}
										{submitMessage}
										{continueResponse}
										regenerateResponse={async (message) => {
											regenerateResponse(message);
											await tick();
											groupedMessageIdsIdx[modelIdx] =
												groupedMessageIds[modelIdx].messageIds.length - 1;
										}}
										{addMessages}
										{onBranchMessage}
										{branchingMessageId}
										{branchSupported}
										{readOnly}
									/>
								{/if}
							{/key}
						</div>
					{/if}
				{/each}
			{/if}
		</div>

		{#if !readOnly}
			{#if !Object.keys(groupedMessageIds).find((modelIdx) => {
				const { messageIds } = groupedMessageIds[modelIdx];
				const _messageId = messageIds[groupedMessageIdsIdx[modelIdx]];
				return !history.messages[_messageId]?.done ?? false;
			})}
				<div class="flex justify-end">
					<div class="w-full">
						{#if history.messages[messageId]?.merged?.status}
							{@const message = history.messages[messageId]?.merged}

							<div class="w-full rounded-xl pl-5 pr-2 py-2">
								<Name>
									Merged Response

									{#if message.timestamp}
										<span
											class=" self-center invisible group-hover:visible text-gray-400 text-xs font-medium uppercase ml-0.5 -mt-0.5"
										>
											{dayjs(message.timestamp * 1000).format('LT')}
										</span>
									{/if}
								</Name>

								<div class="mt-1 markdown-prose w-full min-w-full">
									{#if (message?.content ?? '') === ''}
										<Skeleton />
									{:else}
										<Markdown id={`merged`} content={message.content ?? ''} />
									{/if}
								</div>
							</div>
						{/if}
					</div>

					{#if isLastMessage}
						<div class=" shrink-0 text-gray-600 dark:text-gray-500 mt-1">
							<Tooltip content={$i18n.t('Merge Responses')} placement="bottom">
								<button
									type="button"
									id="merge-response-button"
									class="{true
										? 'visible'
										: 'invisible group-hover:visible'} p-1 hover:bg-black/5 dark:hover:bg-white/5 rounded-lg dark:hover:text-white hover:text-black transition regenerate-response-button"
									on:click={() => {
										mergeResponsesHandler();
									}}
								>
									<Merge className=" size-5 " />
								</button>
							</Tooltip>
						</div>
					{/if}
				</div>
			{/if}
		{/if}
	</div>
{/if}
