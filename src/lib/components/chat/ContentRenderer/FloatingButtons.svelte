<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';
	import { getContext, onDestroy, tick } from 'svelte';

	import { chatCompletion } from '$lib/apis/openai';
	import { createOpenAITextStream } from '$lib/apis/streaming';
	import ChatBubble from '$lib/components/icons/ChatBubble.svelte';
	import LightBlub from '$lib/components/icons/LightBlub.svelte';
	import { mobile, settings } from '$lib/stores';
	import {
		buildSelectionThreadPrompt,
		interruptSelectionThread,
		materializeSelectionThreadMessages,
		sameSelectionThreadAnchor,
		type SelectionThread
	} from '$lib/utils/selection-threads';
	import Markdown from '../Messages/Markdown.svelte';
	import Skeleton from '../Messages/Skeleton.svelte';

	const i18n = getContext('i18n');

	type FloatingAction = {
		id: string;
		label: string;
		icon?: any;
		input?: boolean;
		prompt: string;
	};

	type FloatingChatRequestFactory = ((args: {
		modelId: string;
		messages: any[];
		stream?: boolean;
	}) => Promise<Record<string, unknown>>) | undefined;

	type PendingSelection = {
		quote: string;
		sourceMessageHash: string;
		anchor: SelectionThread['anchor'];
	};

	type OverlayPosition = {
		top: number;
		left: number;
	};

	type ThreadLayout = {
		markerTop: number;
		markerLeft: number;
		cardTop: number;
		cardLeft: number;
		cardMaxWidth: number;
	};

	export let id = '';
	export let model = null;
	export let messages = [];
	export let actions: FloatingAction[] = [];
	export let pendingSelection: PendingSelection | null = null;
	export let pendingSelectionPosition: OverlayPosition | null = null;
	export let threads: SelectionThread[] = [];
	export let threadLayouts: Record<string, ThreadLayout> = {};
	export let activeThreadId: string | null = null;
	export let onThreadsChange: (
		updater: (threads: SelectionThread[]) => SelectionThread[],
		options?: { persist?: boolean; immediate?: boolean }
	) => void = () => {};
	export let onSetActiveThread: (threadId: string | null) => void = () => {};
	export let onClearPendingSelection: () => void = () => {};
	export let onAdd = () => {};

	const floatingChatRequestFactory =
		getContext<FloatingChatRequestFactory>('floatingChatRequestFactory');

	const defaultActions: FloatingAction[] = [
		{
			id: 'ask',
			label: $i18n.t('Ask'),
			icon: ChatBubble,
			input: true,
			prompt: '{{SELECTED_CONTENT}}\n\n\n{{INPUT_CONTENT}}'
		},
		{
			id: 'explain',
			label: $i18n.t('Explain'),
			icon: LightBlub,
			input: false,
			prompt: `{{SELECTED_CONTENT}}\n\n\n${$i18n.t('Explain')}`
		}
	];
	$: resolvedActions = (actions ?? []).length > 0 ? actions : defaultActions;
	$: activeThread = threads.find((thread) => thread.id === activeThreadId) ?? null;
	$: unreadThreadIds = unreadThreadIds.filter((threadId) =>
		threads.some((thread) => thread.id === threadId)
	);
	$: if (activeThreadId) {
		unreadThreadIds = unreadThreadIds.filter((threadId) => threadId !== activeThreadId);
	}

	let unreadThreadIds: string[] = [];
	let lastFocusedThreadSignature = '';

	const requestControllers = new Map<string, AbortController>();

	const getActionById = (actionId?: string | null) =>
		resolvedActions.find((action) => action.id === actionId) ??
		resolvedActions.find((action) => action.input) ??
		resolvedActions[0];

	const getThreadBusy = (thread: SelectionThread | null) => {
		if (!thread) {
			return false;
		}

		return thread.turns.some(
			(turn) => turn.role === 'assistant' && turn.state === 'streaming'
		);
	};

	const getLastAssistantTurn = (thread: SelectionThread | null) => {
		if (!thread) {
			return null;
		}

		for (let index = thread.turns.length - 1; index >= 0; index -= 1) {
			const turn = thread.turns[index];
			if (turn.role === 'assistant') {
				return turn;
			}
		}

		return null;
	};

	const getThreadMarkerTone = (thread: SelectionThread) => {
		const lastAssistantTurn = getLastAssistantTurn(thread);

		if (lastAssistantTurn?.role === 'assistant' && lastAssistantTurn.state === 'streaming') {
			return 'running';
		}

		if (unreadThreadIds.includes(thread.id)) {
			return 'new';
		}

		if (thread.pinned) {
			return 'pinned';
		}

		return 'default';
	};

	const getThreadMarkerLabel = (thread: SelectionThread) => {
		const tone = getThreadMarkerTone(thread);

		if (tone === 'pinned') {
			return $i18n.t('Pinned');
		}

		if (tone === 'running') {
			return $i18n.t('Running');
		}

		return 'AI';
	};

	const getThreadStatusText = (thread: SelectionThread) => {
		const lastAssistantTurn = getLastAssistantTurn(thread);

		if (lastAssistantTurn?.role === 'assistant' && lastAssistantTurn.state === 'streaming') {
			return $i18n.t('Running');
		}

		if (thread.pinned) {
			return $i18n.t('Pinned');
		}

		if (thread.addedToConversationAt) {
			return $i18n.t('Added to conversation');
		}

		return null;
	};

	const markThreadUnread = (threadId: string) => {
		if (!unreadThreadIds.includes(threadId)) {
			unreadThreadIds = [...unreadThreadIds, threadId];
		}
	};

	const updateThread = (
		threadId: string,
		updater: (thread: SelectionThread) => SelectionThread,
		options?: { persist?: boolean; immediate?: boolean }
	) => {
		onThreadsChange(
			(currentThreads) =>
				currentThreads.map((thread) => (thread.id === threadId ? updater(thread) : thread)),
			options
		);
	};


	const createThreadFromSelection = (action: FloatingAction) => {
		if (!pendingSelection) {
			return null;
		}

		const existingThread = threads.find((thread) =>
			sameSelectionThreadAnchor(thread.anchor, pendingSelection.anchor)
		);
		if (existingThread) {
			onSetActiveThread(existingThread.id);
			onClearPendingSelection();
			window.getSelection()?.removeAllRanges();
			return existingThread;
		}

		const nextThread: SelectionThread = {
			id: uuidv4(),
			sourceMessageId: id,
			sourceMessageHash: pendingSelection.sourceMessageHash,
			anchor: pendingSelection.anchor,
			quote: pendingSelection.quote,
			pinned: false,
			draft: '',
			turns: [],
			createdAt: Date.now(),
			updatedAt: Date.now(),
			actionId: action.id
		};

		onThreadsChange((currentThreads) => [...currentThreads, nextThread]);
		onSetActiveThread(nextThread.id);
		onClearPendingSelection();
		window.getSelection()?.removeAllRanges();

		return nextThread;
	};

	const buildRequestMessages = (
		thread: SelectionThread,
		appendedUserTurn?: {
			id: string;
			role: 'user';
			displayContent: string;
			requestContent: string;
		}
	) => {
		return [
			...messages.map((message) => ({
				role: message.role,
				content: message.content
			})),
			...[...thread.turns, ...(appendedUserTurn ? [appendedUserTurn] : [])].map((turn) =>
				turn.role === 'user'
					? {
							role: 'user',
							content: turn.requestContent
						}
					: {
							role: 'assistant',
							content: turn.content
						}
			)
		];
	};

	const autoScrollThread = async (threadId: string) => {
		await tick();
		const container = document.getElementById(`selection-thread-body-${threadId}`);
		if (!container) {
			return;
		}

		if (container.scrollHeight - container.clientHeight <= container.scrollTop + 50) {
			container.scrollTop = container.scrollHeight;
		}
	};

	const runThreadRequest = async (args: {
		threadId: string;
		displayContent: string;
		requestContent: string;
	}) => {
		if (!model) {
			toast.error($i18n.t('Model not selected'));
			return;
		}

		const thread = threads.find((item) => item.id === args.threadId);
		if (!thread) {
			return;
		}

		const userTurn = {
			id: uuidv4(),
			role: 'user' as const,
			displayContent: args.displayContent,
			requestContent: args.requestContent
		};
		const assistantTurnId = uuidv4();

		updateThread(args.threadId, (currentThread) => ({
			...currentThread,
			draft: '',
			turns: [
				...currentThread.turns,
				userTurn,
				{
					id: assistantTurnId,
					role: 'assistant',
					content: '',
					state: 'streaming'
				}
			],
			updatedAt: Date.now()
		}));

		onSetActiveThread(args.threadId);

		try {
			const requestBody = floatingChatRequestFactory
				? await floatingChatRequestFactory({
						modelId: model,
						messages: buildRequestMessages(thread, userTurn),
						stream: true
					})
				: {
						model,
						messages: buildRequestMessages(thread, userTurn),
						stream: true
					};

			const [res, controller] = await chatCompletion(localStorage.token, requestBody);
			requestControllers.set(args.threadId, controller);

			if (!(res && res.ok && res.body)) {
				updateThread(args.threadId, (currentThread) => ({
					...currentThread,
					turns: currentThread.turns.map((turn) =>
						turn.id === assistantTurnId && turn.role === 'assistant'
							? {
									...turn,
									state: 'error'
								}
							: turn
					),
					updatedAt: Date.now()
				}));
				toast.error($i18n.t('An error occurred while fetching the explanation'));
				return;
			}

			const textStream = await createOpenAITextStream(
				res.body,
				$settings?.splitLargeChunks ?? false
			);

			for await (const update of textStream) {
				const { value, image, done, error, usage } = update;
				if (done) {
					updateThread(
						args.threadId,
						(currentThread) => ({
							...currentThread,
							turns: currentThread.turns.map((turn) =>
								turn.id === assistantTurnId && turn.role === 'assistant'
									? {
											...turn,
											state: 'done',
											...(usage ? { usage } : {})
										}
									: turn
							),
							updatedAt: Date.now()
						}),
						{ immediate: true }
					);
					if (activeThreadId !== args.threadId) {
						markThreadUnread(args.threadId);
					}
					await autoScrollThread(args.threadId);
					break;
				}

				if (error) {
					console.error(error);
					updateThread(
						args.threadId,
						(currentThread) => ({
							...currentThread,
							turns: currentThread.turns.map((turn) =>
								turn.id === assistantTurnId && turn.role === 'assistant'
									? {
											...turn,
											state: 'error'
										}
									: turn
							),
							updatedAt: Date.now()
						}),
						{ immediate: true }
					);
					toast.error($i18n.t('An error occurred while fetching the explanation'));
					break;
				}

				const appendValue = image?.markdown ?? value;
				if (!appendValue) {
					continue;
				}

				updateThread(args.threadId, (currentThread) => ({
					...currentThread,
					turns: currentThread.turns.map((turn) =>
						turn.id === assistantTurnId && turn.role === 'assistant'
							? {
									...turn,
									content: `${turn.content}${appendValue}`
								}
							: turn
					),
					updatedAt: Date.now()
				}));
				await autoScrollThread(args.threadId);
			}
		} catch (error) {
			if ((error as Error)?.name !== 'AbortError') {
				console.error(error);
				updateThread(
					args.threadId,
					(currentThread) => ({
						...currentThread,
						turns: currentThread.turns.map((turn) =>
							turn.id === assistantTurnId && turn.role === 'assistant'
								? {
										...turn,
										state: 'error'
									}
								: turn
						),
						updatedAt: Date.now()
					}),
					{ immediate: true }
				);
				toast.error($i18n.t('An error occurred while fetching the explanation'));
			}
		} finally {
			requestControllers.delete(args.threadId);
		}
	};

	const handleAction = async (action: FloatingAction) => {
		const thread = createThreadFromSelection(action);
		if (!thread) {
			return;
		}

		if (action.input) {
			await tick();
			document.getElementById(`selection-thread-input-${thread.id}`)?.focus();
			return;
		}

		await runThreadRequest({
			threadId: thread.id,
			displayContent: action.label,
			requestContent: buildSelectionThreadPrompt(action.prompt, thread.quote)
		});
	};

	const handleDraftSend = async (threadId: string) => {
		const thread = threads.find((item) => item.id === threadId);
		if (!thread) {
			return;
		}

		const displayContent = thread.draft.trim();
		if (!displayContent || getThreadBusy(thread)) {
			return;
		}

		const action = getActionById(thread.actionId);
		const requestContent =
			thread.turns.length === 0
				? buildSelectionThreadPrompt(action.prompt, thread.quote, displayContent)
				: displayContent;

		await runThreadRequest({
			threadId,
			displayContent,
			requestContent
		});
	};

	const handleDraftInput = (threadId: string, event: Event) => {
		const target = event.currentTarget;
		if (!(target instanceof HTMLInputElement)) {
			return;
		}

		updateThread(threadId, (thread) => ({
			...thread,
			draft: target.value,
			updatedAt: Date.now()
		}));
	};

	const handleDraftKeydown = (threadId: string, event: KeyboardEvent) => {
		if (event.key === 'Enter' && !event.shiftKey) {
			event.preventDefault();
			void handleDraftSend(threadId);
		}
	};

	const toggleThreadPin = (threadId: string) => {
		updateThread(threadId, (thread) => ({
			...thread,
			pinned: !thread.pinned,
			updatedAt: Date.now()
		}));
	};

	const minimizeThread = () => {
		onSetActiveThread(null);
	};

	const deleteThread = (threadId: string) => {
		const snapshot = threads.find((thread) => thread.id === threadId);
		if (!snapshot) {
			return;
		}

		requestControllers.get(threadId)?.abort();
		requestControllers.delete(threadId);
		unreadThreadIds = unreadThreadIds.filter((currentId) => currentId !== threadId);
		onThreadsChange((currentThreads) => currentThreads.filter((thread) => thread.id !== threadId), {
			immediate: true
		});
		if (activeThreadId === threadId) {
			onSetActiveThread(null);
		}

		const restoredSnapshot = interruptSelectionThread({
			...snapshot,
			updatedAt: Date.now()
		});

		toast.message($i18n.t('Selection thread deleted'), {
			action: {
				label: $i18n.t('Undo'),
				onClick: () => {
					onThreadsChange(
						(currentThreads) =>
							[...currentThreads, restoredSnapshot].sort(
								(left, right) => left.createdAt - right.createdAt
							),
						{ immediate: true }
					);
				}
			}
		});
	};

	const handleAddToConversation = (thread: SelectionThread) => {
		if (thread.addedToConversationAt) {
			return;
		}

		onAdd({
			modelId: model,
			parentId: id,
			messages: materializeSelectionThreadMessages(thread)
		});
		updateThread(
			thread.id,
			(currentThread) => ({
				...currentThread,
				addedToConversationAt: Date.now(),
				updatedAt: Date.now()
			}),
			{ immediate: true }
		);
	};

	$: {
		const focusThreadId = activeThread && !getThreadBusy(activeThread) ? activeThread.id : null;
		const nextSignature =
			focusThreadId && activeThread ? `${focusThreadId}:${activeThread.turns.length}` : '';

		if (nextSignature && nextSignature !== lastFocusedThreadSignature) {
			lastFocusedThreadSignature = nextSignature;
			tick().then(() => {
				if (!focusThreadId || activeThreadId !== focusThreadId) {
					return;
				}

				document.getElementById(`selection-thread-input-${focusThreadId}`)?.focus();
			});
		} else if (!nextSignature) {
			lastFocusedThreadSignature = '';
		}
	}

	onDestroy(() => {
		const runningThreadIds = new Set(requestControllers.keys());
		requestControllers.forEach((controller) => controller.abort());
		requestControllers.clear();

		if (runningThreadIds.size > 0) {
			onThreadsChange(
				(currentThreads) =>
					currentThreads.map((thread) =>
						runningThreadIds.has(thread.id) ? interruptSelectionThread(thread) : thread
					),
				{ persist: false }
			);
		}
	});
</script>

<div id={`floating-buttons-${id}`} class="absolute inset-0 z-40 pointer-events-none">
	{#if pendingSelection && pendingSelectionPosition}
		<div
			class="absolute pointer-events-auto floating-panel-appear"
			style={`top:${pendingSelectionPosition.top}px; left:${pendingSelectionPosition.left}px;`}
			on:mousedown|stopPropagation
			on:mouseup|stopPropagation
		>
			<div
				class="flex flex-row gap-0.5 shrink-0 px-1.5 py-1 bg-white/95 dark:bg-gray-800/95 backdrop-blur-xl text-gray-600 dark:text-gray-300 rounded-xl shadow-sm border border-gray-200/50 dark:border-gray-700/50"
			>
				{#each resolvedActions as action}
					<button
						class="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-xl flex items-center gap-1 min-w-fit transition-all duration-200 hover:scale-110 active:scale-95 dark:hover:text-white hover:text-black"
						on:click={() => handleAction(action)}
					>
						{#if action.icon}
							<svelte:component this={action.icon} className="size-3 shrink-0" />
						{/if}
						<div class="shrink-0">{action.label}</div>
					</button>
				{/each}
			</div>
		</div>
	{/if}

	{#each threads as thread (thread.id)}
		{@const layout = threadLayouts[thread.id]}
		{@const lastAssistantTurn = getLastAssistantTurn(thread)}
		{#if layout && thread.id !== activeThreadId}
			{@const markerTone = getThreadMarkerTone(thread)}
			<button
				class={`absolute pointer-events-auto selection-thread-marker floating-panel-appear selection-thread-marker-${markerTone}`}
				style={`top:${layout.markerTop}px; left:${layout.markerLeft}px;`}
				on:mousedown|stopPropagation
				on:mouseup|stopPropagation
				on:click={() => onSetActiveThread(thread.id)}
				aria-label={$i18n.t('Open selection thread')}
			>
				<span class="text-[10px] font-semibold">{getThreadMarkerLabel(thread)}</span>
				{#if thread.pinned}
					<span class="selection-thread-dot bg-amber-400"></span>
				{:else if lastAssistantTurn?.role === 'assistant' && lastAssistantTurn.state === 'streaming'}
					<span class="selection-thread-dot bg-emerald-500 animate-pulse"></span>
				{:else if unreadThreadIds.includes(thread.id)}
					<span class="selection-thread-dot bg-sky-500"></span>
				{/if}
			</button>
		{/if}
	{/each}

	{#if activeThread}
		{@const layout = threadLayouts[activeThread.id]}
		{@const activeBusy = getThreadBusy(activeThread)}
		<div
			class={$mobile
				? 'fixed inset-x-3 bottom-3 pointer-events-auto selection-thread-sheet'
				: 'absolute pointer-events-auto floating-panel-appear'}
			style={$mobile || !layout
				? undefined
				: `top:${layout.cardTop}px; left:${layout.cardLeft}px; max-width:${layout.cardMaxWidth}px; width:min(24rem, calc(100vw - 2rem));`}
			on:mousedown|stopPropagation
			on:mouseup|stopPropagation
		>
			<div class="relative bg-white/95 dark:bg-gray-800/95 backdrop-blur-xl dark:text-gray-100 rounded-2xl shadow-lg border border-gray-200/50 dark:border-gray-700/50 selection-thread-card">
				<div class="absolute top-2 right-2 flex items-center gap-1 z-10">
					<button
						class="selection-thread-icon-button"
						on:click={() => toggleThreadPin(activeThread.id)}
						aria-label={activeThread.pinned ? $i18n.t('Unpin') : $i18n.t('Pin')}
					>
						<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
							<path
								d="M6.28 4.22a.75.75 0 0 1 .53-.22h6.38a.75.75 0 0 1 .53 1.28l-1.72 1.72v2.56l1.72 1.72a.75.75 0 0 1-.53 1.28H10.75v3.75a.75.75 0 0 1-1.5 0v-3.75H6.81a.75.75 0 0 1-.53-1.28L8 9.56V7L6.28 5.28a.75.75 0 0 1 0-1.06Z"
							/>
						</svg>
					</button>
					<button
						class="selection-thread-icon-button"
						on:click={minimizeThread}
						aria-label={$i18n.t('Minimize')}
					>
						<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
							<path d="M5 9.25a.75.75 0 0 0 0 1.5h10a.75.75 0 0 0 0-1.5H5Z" />
						</svg>
					</button>
					<button
						class="selection-thread-icon-button"
						on:click={() => deleteThread(activeThread.id)}
						aria-label={$i18n.t('Delete')}
					>
						<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
							<path
								fill-rule="evenodd"
								d="M8.75 3a.75.75 0 0 0-.75.75V4H5.75a.75.75 0 0 0 0 1.5h.44l.63 9.48A2.25 2.25 0 0 0 9.06 17h1.88a2.25 2.25 0 0 0 2.24-2.02l.63-9.48h.44a.75.75 0 0 0 0-1.5H12V3.75A.75.75 0 0 0 11.25 3h-2.5Zm1.5 1V4h-.5v-.25h.5Z"
								clip-rule="evenodd"
							/>
						</svg>
					</button>
				</div>

					<div class="bg-blue-50/40 dark:bg-blue-900/10 rounded-t-2xl px-4 py-3 pr-20">
						<div class="flex items-start justify-between gap-3">
							<div class="flex items-start gap-2 min-w-0">
								<div class="w-0.5 self-stretch rounded-full bg-blue-400/60 dark:bg-blue-500/50 shrink-0 min-h-4"></div>
								<div class="text-xs text-gray-500 dark:text-gray-400 line-clamp-4 italic leading-relaxed">
									{activeThread.quote}
								</div>
							</div>
							{#if getThreadStatusText(activeThread)}
								<span class="selection-thread-status-chip">
									{getThreadStatusText(activeThread)}
								</span>
							{/if}
						</div>
					</div>

				<div class="px-4 py-3">
					<div
						id={`selection-thread-body-${activeThread.id}`}
						class="max-h-80 overflow-y-auto w-full space-y-3 pr-1"
					>
						{#if activeThread.turns.length === 0}
							<div class="text-sm text-gray-500 dark:text-gray-400">
								{$i18n.t('Ask a follow-up question about this selection')}
							</div>
						{:else}
							{#each activeThread.turns as turn (turn.id)}
								{#if turn.role === 'user'}
									<div class="rounded-2xl bg-gray-100/90 dark:bg-gray-700/70 px-3 py-2 text-sm text-gray-700 dark:text-gray-100 whitespace-pre-wrap">
										{turn.displayContent}
									</div>
								{:else}
									<div class="rounded-2xl border border-gray-200/70 dark:border-gray-700/70 bg-white/70 dark:bg-gray-900/40 px-3 py-2 text-sm">
										{#if turn.content.trim() === '' && turn.state === 'streaming'}
											<Skeleton size="sm" />
										{:else if turn.content.trim() === '' && turn.state === 'error'}
											<div class="text-red-500 dark:text-red-400">
												{$i18n.t('An error occurred while fetching the explanation')}
											</div>
										{:else}
											<Markdown id={`${activeThread.id}-${turn.id}`} content={turn.content} />
										{/if}
									</div>
								{/if}
							{/each}
						{/if}
					</div>

					<div class="pt-3 flex items-center gap-2">
						<input
							id={`selection-thread-input-${activeThread.id}`}
							type="text"
							class="selection-thread-input"
							value={activeThread.draft}
							placeholder={$i18n.t('Ask a question')}
							disabled={activeBusy}
							on:input={(event) => handleDraftInput(activeThread.id, event)}
							on:keydown={(event) => handleDraftKeydown(activeThread.id, event)}
						/>
						<button
							class="selection-thread-send-button"
							disabled={activeBusy || activeThread.draft.trim() === ''}
							on:click={() => handleDraftSend(activeThread.id)}
						>
							<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-4">
								<path
									fill-rule="evenodd"
									d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
									clip-rule="evenodd"
								/>
							</svg>
						</button>
					</div>

					{#if !activeBusy && activeThread.turns.some((turn) => turn.role === 'assistant' && turn.state === 'done')}
						<div class="flex justify-end pt-3">
							<button
								class="inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200/60 bg-gray-100/80 px-3.5 py-1.5 text-xs leading-none font-medium text-gray-600 transition-colors duration-150 hover:bg-gray-200 active:opacity-70 dark:border-gray-600/40 dark:bg-gray-700/60 dark:text-gray-300 dark:hover:bg-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
								disabled={activeBusy || Boolean(activeThread.addedToConversationAt)}
								on:click={() => handleAddToConversation(activeThread)}
							>
								<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5 shrink-0">
									<path d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z" />
								</svg>
								{activeThread.addedToConversationAt
									? $i18n.t('Added to conversation')
									: $i18n.t('Add to conversation')}
							</button>
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.floating-panel-appear {
		animation: floating-fade-in 0.22s ease-out both;
	}

	.selection-thread-marker {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		border-radius: 9999px;
		border: 1px solid rgba(186, 230, 253, 0.7);
		background: rgba(255, 255, 255, 0.95);
		padding: 0.25rem 0.5rem;
		color: rgb(3, 105, 161);
		box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
		backdrop-filter: blur(12px);
	}

	.selection-thread-marker-running {
		border-color: rgba(52, 211, 153, 0.55);
		color: rgb(5, 150, 105);
	}

	.selection-thread-marker-new {
		border-color: rgba(56, 189, 248, 0.55);
		color: rgb(3, 105, 161);
	}

	.selection-thread-marker-pinned {
		border-color: rgba(251, 191, 36, 0.55);
		color: rgb(180, 83, 9);
	}

	.selection-thread-dot {
		display: inline-flex;
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 9999px;
	}

	.selection-thread-status-chip {
		display: inline-flex;
		align-items: center;
		flex-shrink: 0;
		border-radius: 9999px;
		background: rgba(255, 255, 255, 0.75);
		padding: 0.2rem 0.5rem;
		font-size: 0.6875rem;
		line-height: 1;
		font-weight: 600;
		color: rgb(8, 145, 178);
		border: 1px solid rgba(125, 211, 252, 0.45);
	}

	.selection-thread-card {
		width: min(24rem, calc(100vw - 2rem));
	}

	.selection-thread-sheet .selection-thread-card {
		width: 100%;
	}

	.selection-thread-icon-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0.25rem;
		border-radius: 0.5rem;
		color: rgb(156, 163, 175);
		transition:
			background-color 150ms ease,
			color 150ms ease;
	}

	.selection-thread-input {
		height: 2.5rem;
		flex: 1 1 auto;
		border-radius: 9999px;
		border: 1px solid rgba(229, 231, 235, 0.7);
		background: rgba(255, 255, 255, 0.8);
		padding: 0 1rem;
		font-size: 0.875rem;
		color: rgb(55, 65, 81);
		outline: none;
		transition:
			border-color 150ms ease,
			background-color 150ms ease,
			color 150ms ease;
	}

	.selection-thread-send-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.5rem;
		height: 2.5rem;
		border-radius: 9999px;
		background: rgb(17, 24, 39);
		color: rgb(255, 255, 255);
		transition:
			background-color 150ms ease,
			color 150ms ease,
			opacity 150ms ease;
	}

	.selection-thread-icon-button:hover {
		background: rgba(0, 0, 0, 0.05);
		color: rgb(75, 85, 99);
	}

	.selection-thread-input:focus {
		border-color: rgb(125, 211, 252);
	}

	.selection-thread-send-button:hover:not(:disabled) {
		background: rgb(31, 41, 55);
	}

	.selection-thread-send-button:disabled {
		cursor: not-allowed;
		background: rgb(229, 231, 235);
		color: rgb(156, 163, 175);
	}

	:global(.dark) .selection-thread-marker {
		border-color: rgba(12, 74, 110, 0.6);
		background: rgba(17, 24, 39, 0.95);
		color: rgb(125, 211, 252);
	}

	:global(.dark) .selection-thread-status-chip {
		background: rgba(15, 23, 42, 0.55);
		color: rgb(125, 211, 252);
		border-color: rgba(56, 189, 248, 0.35);
	}

	:global(.dark) .selection-thread-icon-button:hover {
		background: rgba(255, 255, 255, 0.05);
		color: rgb(243, 244, 246);
	}

	:global(.dark) .selection-thread-input {
		border-color: rgba(55, 65, 81, 0.7);
		background: rgba(17, 24, 39, 0.6);
		color: rgb(243, 244, 246);
	}

	:global(.dark) .selection-thread-input:focus {
		border-color: rgb(56, 189, 248);
	}

	:global(.dark) .selection-thread-send-button {
		background: rgb(255, 255, 255);
		color: rgb(0, 0, 0);
	}

	:global(.dark) .selection-thread-send-button:hover:not(:disabled) {
		background: rgb(243, 244, 246);
	}

	:global(.dark) .selection-thread-send-button:disabled {
		background: rgb(55, 65, 81);
		color: rgb(107, 114, 128);
	}

	@keyframes floating-fade-in {
		from {
			opacity: 0;
			transform: translateY(4px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}
</style>
