<script lang="ts">
	import { createEventDispatcher, getContext, onDestroy, onMount, tick } from 'svelte';
	import { writable, type Writable } from 'svelte/store';

	import Markdown from './Markdown.svelte';
	import {
		artifactAutoOpenDismissedMessageId,
		artifactPreviewTarget,
		chatId,
		mobile,
		settings,
		showArtifacts,
		showControls,
		showOverview
	} from '$lib/stores';
	import FloatingButtons from '../ContentRenderer/FloatingButtons.svelte';
	import { createMessagesList } from '$lib/utils';
	import { getCitationEntries } from '$lib/utils/citations';
	import { resolveChatTransitionMode } from '$lib/utils/lobehub-chat-appearance';
	import {
		createEmptySelectionThreads,
		hashSelectionThreadSource,
		resolveSelectionAnchorRange,
		serializeSelectionRange,
		sameSelectionThreadAnchor,
		type PersistedSelectionThreads,
		type SelectionThread
	} from '$lib/utils/selection-threads';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	type ThreadLayout = {
		markerTop: number;
		markerLeft: number;
		cardTop: number;
		cardLeft: number;
		cardMaxWidth: number;
	};

	type PendingSelection = {
		quote: string;
		sourceMessageHash: string;
		anchor: SelectionThread['anchor'];
	};

	type SelectionThreadManager = {
		selectionThreadsStore: Writable<PersistedSelectionThreads>;
		expandedSelectionThreadId: Writable<string | null>;
		updateSelectionThreads: (
			updater:
				| PersistedSelectionThreads
				| ((current: PersistedSelectionThreads) => PersistedSelectionThreads),
			options?: { persist?: boolean; immediate?: boolean }
		) => void;
		persistSelectionThreads?: () => Promise<void>;
	};

	type VisibleBounds = {
		top: number;
		bottom: number;
	};

	type CopyPayload = {
		text: string;
		html: string;
	};

	export let id;
	export let content;
	export let history;
	export let model = null;
	export let sources = null;

	export let save = false;
	export let floatingButtons = true;
	export let actions = [];
	export let streaming = false;
	export let isLastMessage = false;
	export let forceExpand = false;

	export let onSourceClick = () => {};
	export let onTaskClick = () => {};

	export let onAddMessages = () => {};
	export let headings = [];

	const fallbackSelectionThreadsStore = writable(createEmptySelectionThreads());
	const fallbackExpandedSelectionThreadId = writable<string | null>(null);
	const selectionThreadManager =
		getContext<SelectionThreadManager | undefined>('selectionThreadManager') ?? null;
	const selectionThreadsStore =
		selectionThreadManager?.selectionThreadsStore ?? fallbackSelectionThreadsStore;
	const expandedSelectionThreadId =
		selectionThreadManager?.expandedSelectionThreadId ?? fallbackExpandedSelectionThreadId;
	const updateSelectionThreadsState =
		selectionThreadManager?.updateSelectionThreads ?? (() => {});

	let contentContainerElement: HTMLElement | null = null;
	let currentTransitionMode = 'none';
	let pendingSelection: PendingSelection | null = null;
	let pendingSelectionPosition: { top: number; left: number } | null = null;
	let currentMessageThreads: SelectionThread[] = [];
	let threadLayouts: Record<string, ThreadLayout> = {};
	let resizeObserver: ResizeObserver | undefined;
	let messagesContainerElement: HTMLElement | null = null;
	let syncThreadLayoutsRaf = 0;
	let hadThreadLayouts = false;

	const INLINE_CITATION_SELECTOR = '[data-inline-citation="true"]';

	const getVisibleBounds = (): VisibleBounds => {
		const parentRect = contentContainerElement?.getBoundingClientRect();
		const messagesRect = messagesContainerElement?.getBoundingClientRect();

		if (!parentRect) {
			return { top: 0, bottom: 0 };
		}

		if (!messagesRect) {
			return {
				top: parentRect.top,
				bottom: parentRect.bottom
			};
		}

		return {
			top: Math.max(parentRect.top, messagesRect.top),
			bottom: Math.min(parentRect.bottom, messagesRect.bottom)
		};
	};

	const normalizeCopyFragment = (container: HTMLElement) => {
		container.querySelectorAll(INLINE_CITATION_SELECTOR).forEach((node) => node.remove());

		container.querySelectorAll('table').forEach((table) => {
			table.style.borderCollapse = 'collapse';
			table.style.width = 'auto';
			table.style.tableLayout = 'auto';
		});

		container.querySelectorAll('th').forEach((th) => {
			th.style.whiteSpace = 'nowrap';
			th.style.padding = '4px 8px';
		});
	};

	const getPlainTextFromFragment = (container: HTMLElement): string => {
		const measurementHost = document.createElement('div');
		measurementHost.style.position = 'fixed';
		measurementHost.style.left = '-99999px';
		measurementHost.style.top = '0';
		measurementHost.style.opacity = '0';
		measurementHost.style.pointerEvents = 'none';
		measurementHost.style.width = '960px';
		measurementHost.appendChild(container);
		document.body.appendChild(measurementHost);

		const text = (measurementHost.innerText || measurementHost.textContent || '')
			.replace(/\u00a0/g, ' ')
			.trim();

		measurementHost.remove();
		return text;
	};

	const buildCopyPayloadFromClone = (clone: Node | DocumentFragment | null): CopyPayload | null => {
		if (!clone) {
			return null;
		}

		const container = document.createElement('div');
		container.appendChild(clone);
		normalizeCopyFragment(container);

		const text = getPlainTextFromFragment(container.cloneNode(true) as HTMLElement);
		const html = container.innerHTML.trim();

		if (text === '' && html === '') {
			return null;
		}

		return {
			text,
			html
		};
	};

	export function getCopyPayload(): CopyPayload | null {
		if (!contentContainerElement) {
			return null;
		}

		const fragment = document.createDocumentFragment();
		Array.from(contentContainerElement.childNodes).forEach((node) => {
			fragment.appendChild(node.cloneNode(true));
		});

		return buildCopyPayloadFromClone(fragment);
	}

	export function getSelectionCopyPayload(selection: Selection | null = window.getSelection()) {
		if (
			!contentContainerElement ||
			!selection ||
			selection.rangeCount === 0 ||
			selection.toString().trim() === ''
		) {
			return null;
		}

		const range = selection.getRangeAt(0);
		const selectionTouchesThisMessage =
			contentContainerElement.contains(range.commonAncestorContainer) ||
			contentContainerElement.contains(range.startContainer) ||
			contentContainerElement.contains(range.endContainer);

		if (!selectionTouchesThisMessage) {
			return null;
		}

		return buildCopyPayloadFromClone(range.cloneContents());
	}

	const handleContentCopy = (event: ClipboardEvent) => {
		const payload = getSelectionCopyPayload(window.getSelection());
		if (!payload || !event.clipboardData) {
			return;
		}

		event.preventDefault();
		event.clipboardData.setData('text/plain', payload.text);
		event.clipboardData.setData('text/html', payload.html);
	};

	const resetThreadLayoutsIfIdle = () => {
		if (!hadThreadLayouts && Object.keys(threadLayouts).length === 0) {
			return;
		}

		if (syncThreadLayoutsRaf) {
			cancelAnimationFrame(syncThreadLayoutsRaf);
			syncThreadLayoutsRaf = 0;
		}

		threadLayouts = {};
		hadThreadLayouts = false;
		clearSelectionHighlights();
	};

	const scheduleThreadLayoutSync = () => {
		if (currentMessageThreads.length === 0) {
			resetThreadLayoutsIfIdle();
			return;
		}

		if (syncThreadLayoutsRaf) {
			cancelAnimationFrame(syncThreadLayoutsRaf);
		}

		syncThreadLayoutsRaf = window.requestAnimationFrame(() => {
			syncThreadLayoutsRaf = 0;
			void syncThreadLayouts();
		});
	};

	// Long content truncation
	const MAX_CONTENT_HEIGHT = 2000;
	const MESSAGE_OUTLINE_SCROLL_OFFSET = 24;
	let isExpanded = false;
	let needsTruncation = false;
	let shouldCollapseHistoricalLongResponses = false;

	$: shouldCollapseHistoricalLongResponses =
		!forceExpand && !isLastMessage && ($settings?.collapseHistoricalLongResponses ?? true);

	$: currentMessageThreads = (($selectionThreadsStore.items ?? []) as SelectionThread[])
		.filter((thread) => thread.sourceMessageId === id)
		.sort((left, right) => left.createdAt - right.createdAt);

	function checkTruncation() {
		if (!contentContainerElement) return;
		if (streaming || isExpanded || !shouldCollapseHistoricalLongResponses) {
			needsTruncation = false;
			return;
		}
		needsTruncation = contentContainerElement.scrollHeight > MAX_CONTENT_HEIGHT;
	}

	$: if (contentContainerElement) {
		isLastMessage;
		shouldCollapseHistoricalLongResponses;

		if (streaming) {
			needsTruncation = false;
		} else {
			tick().then(checkTruncation);
		}
	}

	$: currentTransitionMode = resolveChatTransitionMode($settings);

	const highlightHeading = (headingElement: HTMLElement) => {
		headingElement.classList.remove('message-outline-anchor-target');
		void headingElement.offsetWidth;
		headingElement.classList.add('message-outline-anchor-target');
		headingElement.addEventListener(
			'animationend',
			() => {
				headingElement.classList.remove('message-outline-anchor-target');
			},
			{ once: true }
		);
	};

	const clearPendingSelection = () => {
		pendingSelection = null;
		pendingSelectionPosition = null;
	};

	const isEventInsideFloatingOverlay = (event: MouseEvent) => {
		const overlayId = `floating-buttons-${id}`;
		return event
			.composedPath()
			.some((node) => node instanceof HTMLElement && node.id === overlayId);
	};

	const updateMessageThreads = (
		updater: (threads: SelectionThread[]) => SelectionThread[],
		options?: { persist?: boolean; immediate?: boolean }
	) => {
		updateSelectionThreadsState((currentState) => {
			const otherThreads = currentState.items.filter(
				(thread) => thread.sourceMessageId !== id
			);
			const messageThreads = currentState.items.filter(
				(thread) => thread.sourceMessageId === id
			);

			return {
				version: 1,
				items: [...otherThreads, ...updater(messageThreads)].sort(
					(left, right) => left.createdAt - right.createdAt
				)
			};
		}, options);
	};

	const computeToolbarPosition = (rect: DOMRect, parentRect: DOMRect) => {
		const toolbarWidth = 220;
		const visibleBounds = getVisibleBounds();
		const preferredTop = rect.bottom - parentRect.top + 8;
		const fallbackTop = Math.max(0, rect.top - parentRect.top - 44);
		const nextTop =
			rect.bottom + 44 > visibleBounds.bottom && rect.top - 52 >= visibleBounds.top
				? fallbackTop
				: preferredTop;
		const left = Math.max(
			0,
			Math.min(rect.left - parentRect.left, Math.max(parentRect.width - toolbarWidth, 0))
		);

		return {
			top: Math.max(0, nextTop),
			left
		};
	};

	const computeThreadLayout = (rect: DOMRect, parentRect: DOMRect): ThreadLayout => {
		const markerWidth = 48;
		const cardMaxWidth = Math.min(parentRect.width, 384);
		const markerLeft = Math.max(
			0,
			Math.min(rect.right - parentRect.left + 8, Math.max(parentRect.width - markerWidth, 0))
		);
		const markerTop = Math.max(0, rect.bottom - parentRect.top - 10);
		const cardLeft = Math.max(
			0,
			Math.min(rect.left - parentRect.left, Math.max(parentRect.width - cardMaxWidth, 0))
		);
		const visibleBounds = getVisibleBounds();
		const estimatedCardHeight = 360;
		const belowTop = rect.bottom - parentRect.top + 12;
		const aboveTop = rect.top - parentRect.top - estimatedCardHeight - 12;
		const spaceBelow = visibleBounds.bottom - rect.bottom;
		const spaceAbove = rect.top - visibleBounds.top;
		const cardTop =
			spaceBelow < estimatedCardHeight + 16 && spaceAbove > spaceBelow
				? Math.max(0, aboveTop)
				: Math.max(0, belowTop);

		return {
			markerTop,
			markerLeft,
			cardTop,
			cardLeft,
			cardMaxWidth
		};
	};

	const clearSelectionHighlights = () => {
		try {
			if ('highlights' in CSS) {
				(CSS as any).highlights.delete('selection-thread');
				(CSS as any).highlights.delete('selection-thread-active');
			}
		} catch {
			// Graceful degradation
		}
	};

	const applySelectionHighlights = (regularRanges: Range[], activeRanges: Range[]) => {
		try {
			if (!('highlights' in CSS)) {
				return;
			}

			if (regularRanges.length > 0) {
				const regularHighlight = new (window as any).Highlight();
				regularRanges.forEach((range) => regularHighlight.add(range));
				(CSS as any).highlights.set('selection-thread', regularHighlight);
			} else {
				(CSS as any).highlights.delete('selection-thread');
			}

			if (activeRanges.length > 0) {
				const activeHighlight = new (window as any).Highlight();
				activeRanges.forEach((range) => activeHighlight.add(range));
				(CSS as any).highlights.set('selection-thread-active', activeHighlight);
			} else {
				(CSS as any).highlights.delete('selection-thread-active');
			}
		} catch {
			// Graceful degradation
		}
	};

	const syncThreadLayouts = async () => {
		if (!contentContainerElement) {
			threadLayouts = {};
			if (hadThreadLayouts) {
				clearSelectionHighlights();
			}
			hadThreadLayouts = false;
			return;
		}

		if (currentMessageThreads.length === 0) {
			resetThreadLayoutsIfIdle();
			return;
		}

		await tick();
		messagesContainerElement = document.getElementById('messages-container');

		const parentRect = contentContainerElement.getBoundingClientRect();
		const currentMessageHash = hashSelectionThreadSource(contentContainerElement.textContent ?? '');
		const nextLayouts: Record<string, ThreadLayout> = {};
		const regularRanges: Range[] = [];
		const activeRanges: Range[] = [];
		const invalidThreadIds: string[] = [];

		for (const thread of currentMessageThreads) {
			const range = resolveSelectionAnchorRange(contentContainerElement, thread.anchor, {
				preferOffsets: thread.sourceMessageHash === currentMessageHash
			});
			if (!range) {
				invalidThreadIds.push(thread.id);
				continue;
			}

			const rect = range.getBoundingClientRect();
			nextLayouts[thread.id] = computeThreadLayout(rect, parentRect);

			if (thread.id === $expandedSelectionThreadId) {
				activeRanges.push(range);
			} else {
				regularRanges.push(range);
			}
		}

		threadLayouts = nextLayouts;
		hadThreadLayouts = true;
		applySelectionHighlights(regularRanges, activeRanges);

		if (invalidThreadIds.length > 0) {
			updateMessageThreads(
				(threads) => threads.filter((thread) => !invalidThreadIds.includes(thread.id)),
				{ immediate: true }
			);
			if (invalidThreadIds.includes($expandedSelectionThreadId ?? '')) {
				expandedSelectionThreadId.set(null);
			}
		}
	};

	$: if (contentContainerElement) {
		currentMessageThreads;
		$expandedSelectionThreadId;
		scheduleThreadLayoutSync();
	}

	export async function scrollToHeading(headingId: string) {
		if (needsTruncation && !isExpanded) {
			isExpanded = true;
			await tick();
		}

		await tick();

		const headingElement = contentContainerElement?.querySelector?.(
			`[id="${headingId}"]`
		) as HTMLElement | null;
		const messagesContainer = document.getElementById('messages-container');

		if (!headingElement || !messagesContainer) {
			return;
		}

		const containerRect = messagesContainer.getBoundingClientRect();
		const headingRect = headingElement.getBoundingClientRect();
		const nextTop =
			messagesContainer.scrollTop +
			(headingRect.top - containerRect.top) -
			MESSAGE_OUTLINE_SCROLL_OFFSET;

		messagesContainer.scrollTo({
			top: Math.max(0, nextTop),
			behavior: 'smooth'
		});

		highlightHeading(headingElement);
	}

	const expandExistingThread = (threadId: string) => {
		clearPendingSelection();
		expandedSelectionThreadId.set(threadId);
		window.getSelection()?.removeAllRanges();
		scheduleThreadLayoutSync();
	};

	const minimizeExpandedThread = () => {
		if ($expandedSelectionThreadId) {
			expandedSelectionThreadId.set(null);
			scheduleThreadLayoutSync();
		}
	};

	const handleSelectionWithinMessage = () => {
		if (!contentContainerElement) {
			return;
		}

		const selection = window.getSelection();
		if (!selection || selection.rangeCount === 0 || selection.toString().trim() === '') {
			clearPendingSelection();
			return;
		}

		const range = selection.getRangeAt(0);
		if (!contentContainerElement.contains(range.commonAncestorContainer)) {
			clearPendingSelection();
			return;
		}

		const anchor = serializeSelectionRange(contentContainerElement, range);
		if (!anchor) {
			clearPendingSelection();
			return;
		}

		const match = currentMessageThreads.find((thread) =>
			sameSelectionThreadAnchor(thread.anchor, anchor)
		);
		const rect = range.getBoundingClientRect();
		const parentRect = contentContainerElement.getBoundingClientRect();
		const sourceMessageHash = hashSelectionThreadSource(contentContainerElement.textContent ?? '');

		if (match) {
			expandExistingThread(match.id);
			return;
		}

		minimizeExpandedThread();
		pendingSelection = {
			quote: anchor.exact,
			sourceMessageHash,
			anchor
		};
		pendingSelectionPosition = computeToolbarPosition(rect, parentRect);
		scheduleThreadLayoutSync();
	};

	const handleDocumentMouseUp = (event: MouseEvent) => {
		const clickedInsideFloatingOverlay = isEventInsideFloatingOverlay(event);

		setTimeout(() => {
			if (clickedInsideFloatingOverlay) {
				return;
			}

			const selection = window.getSelection();
			const hasSelection = Boolean(selection && selection.rangeCount > 0 && selection.toString().trim() !== '');
			const selectionRange = hasSelection && selection ? selection.getRangeAt(0) : null;
			const selectionTouchesThisMessage = Boolean(
				selectionRange &&
				contentContainerElement &&
				(contentContainerElement.contains(selectionRange.commonAncestorContainer) ||
					contentContainerElement.contains(selectionRange.startContainer) ||
					contentContainerElement.contains(selectionRange.endContainer))
			);
			const expandedThread = currentMessageThreads.find(
				(thread) => thread.id === $expandedSelectionThreadId
			);

			if (selectionTouchesThisMessage) {
				handleSelectionWithinMessage();
				return;
			}

			clearPendingSelection();

			if (!expandedThread) {
				return;
			}

			if (hasSelection) {
				return;
			}

			if (!expandedThread.pinned) {
				minimizeExpandedThread();
			}
		}, 0);
	};


	const keydownHandler = (event: KeyboardEvent) => {
		if (event.key !== 'Escape') {
			return;
		}

		clearPendingSelection();
		minimizeExpandedThread();
	};

	onMount(() => {
		messagesContainerElement = document.getElementById('messages-container');

		if (floatingButtons) {
			document.addEventListener('mouseup', handleDocumentMouseUp);
			document.addEventListener('keydown', keydownHandler);
			messagesContainerElement?.addEventListener('scroll', scheduleThreadLayoutSync, {
				passive: true
			});
			window.addEventListener('resize', scheduleThreadLayoutSync, { passive: true });
		}

		if (contentContainerElement) {
			resizeObserver = new ResizeObserver(() => {
				checkTruncation();
				scheduleThreadLayoutSync();
			});
			resizeObserver.observe(contentContainerElement);
		}

		scheduleThreadLayoutSync();
	});

	onDestroy(() => {
		if (floatingButtons) {
			document.removeEventListener('mouseup', handleDocumentMouseUp);
			document.removeEventListener('keydown', keydownHandler);
			messagesContainerElement?.removeEventListener('scroll', scheduleThreadLayoutSync);
			window.removeEventListener('resize', scheduleThreadLayoutSync);
		}
		resizeObserver?.disconnect();
		if (syncThreadLayoutsRaf) {
			cancelAnimationFrame(syncThreadLayoutsRaf);
			syncThreadLayoutsRaf = 0;
		}
		clearSelectionHighlights();
	});
</script>

<div class="relative overflow-visible">
	<div
		bind:this={contentContainerElement}
		data-inline-citations-hidden={($settings?.showInlineCitations ?? true) ? undefined : 'true'}
		class="relative message-selection-surface"
		on:copy={handleContentCopy}
		style={needsTruncation && !isExpanded
			? `max-height: ${MAX_CONTENT_HEIGHT}px; overflow: hidden;`
			: ''}
	>
		<Markdown
			bind:headings
			{id}
			content={content || ''}
			{model}
			{save}
			{streaming}
			transitionMode={currentTransitionMode}
			sourceIds={(sources ?? []).reduce((acc, s) => {
				if (!s || typeof s !== 'object') {
					return acc;
				}

				let ids = [];
				getCitationEntries(s).forEach(({ metadata }) => {
					if (model?.info?.meta?.capabilities?.citations == false) {
						ids.push('N/A');
						return;
					}

					const id = metadata?.source ?? 'N/A';

					if (metadata?.name) {
						ids.push(metadata.name);
						return;
					}

					if (
						typeof id === 'string' &&
						(id.startsWith('http://') || id.startsWith('https://'))
					) {
						ids.push(id);
					} else {
						ids.push(s?.source?.name ?? id);
					}
				});

				acc = [...acc, ...ids];

				return acc.filter((item, index) => acc.indexOf(item) === index);
			}, [])}
			{onSourceClick}
			{onTaskClick}
			on:update={(e) => {
				dispatch('update', e.detail);
			}}
			on:code={(e) => {
				const { lang, code } = e.detail;
				const normalizedLang = String(lang ?? '').toLowerCase();
				const isSvgCode =
					normalizedLang === 'svg' || (normalizedLang === 'xml' && code.includes('<svg'));
				const isHtmlArtifact = normalizedLang === 'html';
				const shouldAutoOpenSvgPreview =
					$settings?.svgPreviewAutoOpen ?? ($settings?.detectArtifacts ?? true);
				const autoOpenDismissed = $artifactAutoOpenDismissedMessageId === id;

				if (
					!$mobile &&
					$chatId &&
					!autoOpenDismissed &&
					((($settings?.detectArtifacts ?? true) && isHtmlArtifact) ||
						(shouldAutoOpenSvgPreview && isSvgCode))
				) {
					if (isSvgCode) {
						artifactPreviewTarget.set({ messageId: id, type: 'svg', content: code });
					} else {
						artifactPreviewTarget.set({ messageId: id, type: 'iframe' });
					}
					showOverview.set(false);
					showArtifacts.set(true);
					showControls.set(true);
				}
			}}
		/>
	</div>

	{#if floatingButtons && model}
		<FloatingButtons
			{id}
			model={model?.id}
			messages={createMessagesList(history, id)}
			{actions}
			{pendingSelection}
			{pendingSelectionPosition}
			threads={currentMessageThreads}
			{threadLayouts}
			activeThreadId={$expandedSelectionThreadId}
			onThreadsChange={updateMessageThreads}
			onSetActiveThread={(threadId) => {
				clearPendingSelection();
				expandedSelectionThreadId.set(threadId);
				scheduleThreadLayoutSync();
			}}
			onClearPendingSelection={clearPendingSelection}
			onAdd={({ modelId, parentId, messages }) => {
				onAddMessages({ modelId, parentId, messages });
			}}
		/>
	{/if}
</div>

{#if needsTruncation && !isExpanded}
	<div class="relative -mt-20 pt-20 bg-gradient-to-t from-white dark:from-gray-900 to-transparent">
		<div class="flex justify-center py-2">
			<button
				class="px-4 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-300
					bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700
					rounded-full border border-gray-200 dark:border-gray-700
					transition-colors duration-150"
				on:click={() => {
					isExpanded = true;
				}}
			>
				{$i18n.t('Show more')}
			</button>
		</div>
	</div>
{/if}

<style>
	:global(.message-outline-anchor) {
		scroll-margin-top: 1.5rem;
	}

	:global(.message-outline-anchor-target) {
		animation: message-outline-anchor-flash 900ms ease;
	}

	@keyframes message-outline-anchor-flash {
		0% {
			background-color: rgba(56, 189, 248, 0.18);
			box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.18);
		}

		45% {
			background-color: rgba(56, 189, 248, 0.28);
			box-shadow: 0 0 0 0.45rem rgba(56, 189, 248, 0.12);
		}

		100% {
			background-color: transparent;
			box-shadow: 0 0 0 0 rgba(56, 189, 248, 0);
		}
	}

	:global(::highlight(selection-thread)) {
		background: transparent;
		text-decoration: underline dashed 1.5px;
		text-decoration-color: rgba(59, 130, 246, 0.42);
		text-underline-offset: 3px;
	}

	:global(::highlight(selection-thread-active)) {
		background: rgba(56, 189, 248, 0.08);
		text-decoration: underline dashed 2px;
		text-decoration-color: rgba(14, 165, 233, 0.7);
		text-underline-offset: 3px;
	}

	:global(.message-selection-surface ::selection) {
		background: rgba(59, 130, 246, 0.24);
	}

	:global(.dark .message-selection-surface ::selection) {
		background: rgba(56, 189, 248, 0.3);
	}

	:global([data-inline-citations-hidden='true'] [data-inline-citation='true']) {
		display: none !important;
	}
</style>
