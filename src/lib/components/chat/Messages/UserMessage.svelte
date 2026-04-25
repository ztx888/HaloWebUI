<script lang="ts">
	import dayjs from 'dayjs';
	import { toast } from 'svelte-sonner';
	import { tick, getContext, onMount } from 'svelte';

	import { models, settings } from '$lib/stores';
	import { user as _user } from '$lib/stores';
	import { copyToClipboard as _copyToClipboard, formatDate } from '$lib/utils';

	import Name from './Name.svelte';
	import ProfileImage from './ProfileImage.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Markdown from './Markdown.svelte';
	import Image from '$lib/components/common/Image.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import { ChevronLeft, ChevronRight, PencilLine, Copy, Trash2, GitBranchPlus } from 'lucide-svelte';

	import localizedFormat from 'dayjs/plugin/localizedFormat';

	const i18n = getContext('i18n');
	dayjs.extend(localizedFormat);

	export let user;

	export let history;
	export let messageId;

	export let siblings;

	export let gotoMessage: Function;
	export let showPreviousMessage: Function;
	export let showNextMessage: Function;

	export let editMessage: Function;
	export let deleteMessage: Function;
	export let onBranchMessage: Function = () => {};
	export let branchingMessageId: string | null = null;
	export let branchSupported = false;

	export let isFirstMessage: boolean;
	export let readOnly: boolean;

	let showDeleteConfirm = false;

	let messageIndexEdit = false;

	let edit = false;
	let editedContent = '';
	let messageEditTextAreaElement: HTMLTextAreaElement;
	let messageEditScrollElement: HTMLDivElement;
	let isBranching = false;
	let branchTooltip = '';

	let message = history.messages?.[messageId];
	$: message = history.messages?.[messageId];
	$: isBranching = branchingMessageId === message?.id;
	$: branchTooltip = $i18n.t(isBranching ? 'Creating branch...' : 'Create branch');

	const copyToClipboard = async (text) => {
		const res = await _copyToClipboard(text);
		if (res) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	const editMessageHandler = async () => {
		edit = true;
		editedContent = message.content;

		await tick();

		resizeMessageEditTextArea();

		messageEditTextAreaElement?.focus();
	};

	const resizeMessageEditTextArea = (
		textarea: HTMLTextAreaElement | null = messageEditTextAreaElement
	) => {
		if (!textarea) {
			return;
		}

		const previousHeight = textarea.offsetHeight;
		const previousScrollTop = messageEditScrollElement?.scrollTop ?? 0;

		textarea.style.height = 'auto';
		textarea.style.height = `${textarea.scrollHeight}px`;

		if (messageEditScrollElement) {
			const nextScrollTop =
				previousHeight > 0
					? Math.max(previousScrollTop + textarea.offsetHeight - previousHeight, 0)
					: previousScrollTop;
			messageEditScrollElement.scrollTop = nextScrollTop;

			requestAnimationFrame(() => {
				if (messageEditScrollElement) {
					messageEditScrollElement.scrollTop = nextScrollTop;
				}
			});
		}
	};

	const editMessageConfirmHandler = async (submit = true) => {
		editMessage(message.id, editedContent, submit);

		edit = false;
		editedContent = '';
	};

	const cancelEditMessage = () => {
		edit = false;
		editedContent = '';
	};

	const deleteMessageHandler = async () => {
		deleteMessage(message.id);
	};

	onMount(() => {
		// console.log('UserMessage mounted');
	});
</script>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete message?')}
	on:confirm={() => {
		deleteMessageHandler();
	}}
/>

<div class=" flex w-full user-message" dir={$settings.chatDirection} id="message-{message.id}">
	{#if !($settings?.chatBubble ?? true)}
		<div class={`shrink-0 ltr:mr-1.5 rtl:ml-1.5 ltr:sm:mr-3 rtl:sm:ml-3`}>
			<ProfileImage
				src={message.user
					? ($models.find((m) => m.id === message.user)?.info?.meta?.profile_image_url ??
						$models.find((m) => m.id === message.user)?.meta?.profile_image_url ??
						'/user.png')
					: (user?.profile_image_url ?? '/user.png')}
				className={'size-[26px] sm:size-[34px]'}
			/>
		</div>
	{/if}
	<div class="flex-auto w-0 max-w-full sm:pl-1">
		{#if !($settings?.chatBubble ?? true)}
			<div>
				<Name>
					{#if message.user}
						{$i18n.t('You')}
						<span class=" text-gray-500 text-sm font-medium">{message?.user ?? ''}</span>
					{:else if $settings.showUsername || $_user.name !== user.name}
						{user.name}
					{:else}
						{$i18n.t('You')}
					{/if}

					{#if message.timestamp}
						<div
							class=" self-center text-xs invisible group-hover:visible text-gray-500 dark:text-gray-400 font-medium first-letter:capitalize ml-0.5 translate-y-[1px]"
						>
							<Tooltip content={dayjs(message.timestamp * 1000).format('LLLL')}>
								<span class="line-clamp-1">{formatDate(message.timestamp * 1000)}</span>
							</Tooltip>
						</div>
					{/if}
				</Name>
			</div>
		{/if}

		<div class="chat-{message.role} w-full min-w-full markdown-prose">
			{#if message.files}
				<div class="mt-2.5 mb-1 w-full flex flex-col justify-end overflow-x-auto gap-1 flex-wrap">
					{#each message.files as file}
						<div class={($settings?.chatBubble ?? true) ? 'self-end' : ''}>
							{#if file.type === 'image'}
								<Image
									src={file.url}
									className="w-fit max-w-full outline-hidden focus:outline-hidden"
									imageClassName="rounded-lg chat-user-attachment-image"
								/>
							{:else}
								<FileItem
									item={file}
									url={file.url}
									name={file.name}
									type={file.type}
									size={file?.size}
									colorClassName="bg-white dark:bg-gray-850 "
								/>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			{#if message.content !== ''}
				{#if edit === true}
					<div class=" w-full bg-gray-50 dark:bg-gray-800 rounded-3xl px-5 py-3 mb-2">
						<div bind:this={messageEditScrollElement} class="max-h-96 overflow-auto">
							<textarea
								id="message-edit-{message.id}"
								bind:this={messageEditTextAreaElement}
								class=" bg-transparent outline-hidden w-full resize-none"
								bind:value={editedContent}
								on:focus={() => {
									resizeMessageEditTextArea();
								}}
								on:input={() => {
									resizeMessageEditTextArea();
								}}
								on:keydown={(e) => {
									if (e.key === 'Escape') {
										document.getElementById('close-edit-message-button')?.click();
									}

									const isCmdOrCtrlPressed = e.metaKey || e.ctrlKey;
									const isEnterPressed = e.key === 'Enter';

									if (isCmdOrCtrlPressed && isEnterPressed) {
										document.getElementById('confirm-edit-message-button')?.click();
									}
								}}
							/>
						</div>

						<div class=" mt-2 mb-1 flex justify-between text-sm font-medium">
							<div>
								<button
									id="save-edit-message-button"
									class=" px-4 py-2 bg-gray-50 hover:bg-gray-100 dark:bg-gray-800 dark:hover:bg-gray-700 border border-gray-100 dark:border-gray-700 text-gray-700 dark:text-gray-200 transition rounded-3xl"
									on:click={() => {
										editMessageConfirmHandler(false);
									}}
								>
									{$i18n.t('Save')}
								</button>
							</div>

							<div class="flex space-x-1.5">
								<button
									id="close-edit-message-button"
									class="px-4 py-2 bg-white dark:bg-gray-900 hover:bg-gray-100 text-gray-800 dark:text-gray-100 transition rounded-3xl"
									on:click={() => {
										cancelEditMessage();
									}}
								>
									{$i18n.t('Cancel')}
								</button>

								<button
									id="confirm-edit-message-button"
									class=" px-4 py-2 bg-gray-900 dark:bg-white hover:bg-gray-850 text-gray-100 dark:text-gray-800 transition rounded-3xl"
									on:click={() => {
										editMessageConfirmHandler();
									}}
								>
									{$i18n.t('Send')}
								</button>
							</div>
						</div>
					</div>
				{:else}
					<div class="w-full">
						<div class="flex {($settings?.chatBubble ?? true) ? 'justify-end pb-1' : 'w-full'}">
							<div
								class={($settings?.chatBubble ?? true)
									? `max-w-[75%] px-4 py-2.5 rounded-2xl ${
											message.files ? 'rounded-tr-lg' : 'rounded-br-lg'
										} bg-gray-100/60 dark:bg-gray-800/50 backdrop-blur-xl text-gray-800 dark:text-gray-100`
									: 'w-full'}
							>
								{#if message.content}
									<div class="text-[15px]">
										<Markdown id={message.id} content={message.content} />
									</div>
								{/if}
							</div>
						</div>

						<div
							class="flex items-center gap-0.5 text-gray-600 dark:text-gray-300 px-1.5 h-[37px] rounded-xl invisible group-hover:visible opacity-0 group-hover:opacity-100 transition-all duration-300 bg-white/60 dark:bg-gray-800/60 backdrop-blur-xl shadow-sm border border-gray-200/50 dark:border-gray-700/50 w-fit {($settings?.chatBubble ??
							true)
								? 'ml-auto'
								: ''}"
						>
							{#if !($settings?.chatBubble ?? true)}
								{#if siblings.length > 1}
									<div class="flex self-center" dir="ltr">
										<button
											class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-lg transition-all duration-200 hover:scale-110 active:scale-95"
											on:click={() => {
												showPreviousMessage(message);
											}}
										>
											<ChevronLeft class="size-3.5" strokeWidth={2.5} />
										</button>

										{#if messageIndexEdit}
											<div
												class="text-sm flex justify-center font-semibold self-center dark:text-gray-100 min-w-fit"
											>
												<input
													id="message-index-input-{message.id}"
													type="number"
													value={siblings.indexOf(message.id) + 1}
													min="1"
													max={siblings.length}
													on:focus={(e) => {
														e.target.select();
													}}
													on:blur={(e) => {
														gotoMessage(message, e.target.value - 1);
														messageIndexEdit = false;
													}}
													on:keydown={(e) => {
														if (e.key === 'Enter') {
															gotoMessage(message, e.target.value - 1);
															messageIndexEdit = false;
														}
													}}
													class="bg-transparent font-semibold self-center dark:text-gray-100 min-w-fit outline-hidden"
												/>/{siblings.length}
											</div>
										{:else}
											<!-- svelte-ignore a11y-no-static-element-interactions -->
											<div
												class="text-xs tracking-wider font-medium self-center text-gray-500 dark:text-gray-300 min-w-fit tabular-nums"
												on:dblclick={async () => {
													messageIndexEdit = true;

													await tick();
													const input = document.getElementById(
														`message-index-input-${message.id}`
													);
													if (input) {
														input.focus();
														input.select();
													}
												}}
											>
												{siblings.indexOf(message.id) + 1}/{siblings.length}
											</div>
										{/if}

										<button
											class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-lg transition-all duration-200 hover:scale-110 active:scale-95"
											on:click={() => {
												showNextMessage(message);
											}}
										>
											<ChevronRight class="size-3.5" strokeWidth={2.5} />
										</button>
									</div>
								{/if}
							{/if}
							{#if !readOnly}
								{#if !($settings?.chatBubble ?? true) && siblings.length > 1}
									<div class="w-px h-4 bg-gray-300/40 dark:bg-gray-600/40 mx-0.5 self-center"></div>
								{/if}
								<Tooltip content={$i18n.t('Edit')} placement="bottom">
									<button
										class="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 edit-user-message-button"
										on:click={() => {
											editMessageHandler();
										}}
									>
										<PencilLine class="w-4 h-4" strokeWidth={2} />
									</button>
								</Tooltip>
							{/if}

							<Tooltip content={$i18n.t('Copy')} placement="bottom">
								<button
									class="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
									on:click={() => {
										copyToClipboard(message.content);
									}}
								>
									<Copy class="w-4 h-4" strokeWidth={2} />
								</button>
							</Tooltip>

							{#if !readOnly && branchSupported}
								<Tooltip content={branchTooltip} placement="bottom">
									<button
										class="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100"
										on:click={() => {
											onBranchMessage(message.id);
										}}
										disabled={isBranching}
										aria-busy={isBranching}
									>
										<GitBranchPlus
											class={`w-4 h-4 ${isBranching ? 'animate-spin' : ''}`}
											strokeWidth={2}
										/>
									</button>
								</Tooltip>
							{/if}

							{#if !readOnly && (!isFirstMessage || siblings.length > 1)}
								<Tooltip content={$i18n.t('Delete')} placement="bottom">
									<button
										class="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl dark:hover:text-white hover:text-black transition-all duration-200 hover:scale-110 active:scale-95"
										on:click={() => {
											showDeleteConfirm = true;
										}}
									>
										<Trash2 class="w-4 h-4" strokeWidth={2} />
									</button>
								</Tooltip>
							{/if}

							{#if $settings?.chatBubble ?? true}
								{#if siblings.length > 1}
									<div class="flex self-center" dir="ltr">
										<button
											class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-lg transition-all duration-200 hover:scale-110 active:scale-95"
											on:click={() => {
												showPreviousMessage(message);
											}}
										>
											<ChevronLeft class="size-3.5" strokeWidth={2.5} />
										</button>

										{#if messageIndexEdit}
											<div
												class="text-sm flex justify-center font-semibold self-center dark:text-gray-100 min-w-fit"
											>
												<input
													id="message-index-input-{message.id}"
													type="number"
													value={siblings.indexOf(message.id) + 1}
													min="1"
													max={siblings.length}
													on:focus={(e) => {
														e.target.select();
													}}
													on:blur={(e) => {
														gotoMessage(message, e.target.value - 1);
														messageIndexEdit = false;
													}}
													on:keydown={(e) => {
														if (e.key === 'Enter') {
															gotoMessage(message, e.target.value - 1);
															messageIndexEdit = false;
														}
													}}
													class="bg-transparent font-semibold self-center dark:text-gray-100 min-w-fit outline-hidden"
												/>/{siblings.length}
											</div>
										{:else}
											<!-- svelte-ignore a11y-no-static-element-interactions -->
											<div
												class="text-xs tracking-wider font-medium self-center text-gray-500 dark:text-gray-300 min-w-fit tabular-nums"
												on:dblclick={async () => {
													messageIndexEdit = true;

													await tick();
													const input = document.getElementById(
														`message-index-input-${message.id}`
													);
													if (input) {
														input.focus();
														input.select();
													}
												}}
											>
												{siblings.indexOf(message.id) + 1}/{siblings.length}
											</div>
										{/if}

										<button
											class="self-center p-1 hover:bg-black/5 dark:hover:bg-white/5 dark:hover:text-white hover:text-black rounded-lg transition-all duration-200 hover:scale-110 active:scale-95"
											on:click={() => {
												showNextMessage(message);
											}}
										>
											<ChevronRight class="size-3.5" strokeWidth={2.5} />
										</button>
									</div>
								{/if}
							{/if}
						</div>
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>
