<script lang="ts">
	import { onMount, createEventDispatcher } from 'svelte';
	import { fade, scale } from 'svelte/transition';
	import { getSplashNotification } from '$lib/apis/configs';

	const dispatch = createEventDispatcher();

	let show = false;
	let title = '';
	let content = '';
	let renderedHtml = '';

	onMount(async () => {
		try {
			const data = await getSplashNotification();
			if (data && data.enabled && data.content && data.content.trim() !== '') {
				title = data.title || '公告通知';
				content = data.content;
				renderedHtml = markdownToHtml(content);
				// Always show notification on page load
				show = true;
			}
		} catch (e) {
			console.log('Splash notification not available:', e);
		}
	});

	const close = () => {
		show = false;
		dispatch('close');
	};


	function markdownToHtml(md: string): string {
		if (!md) return '';
		let html = md
			// Escape HTML
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			// Headers
			.replace(/^#### (.+)$/gm, '<h4>$1</h4>')
			.replace(/^### (.+)$/gm, '<h3>$1</h3>')
			.replace(/^## (.+)$/gm, '<h2>$1</h2>')
			.replace(/^# (.+)$/gm, '<h1>$1</h1>')
			// Bold and italic
			.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
			.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
			.replace(/\*(.+?)\*/g, '<em>$1</em>')
			// Code blocks
			.replace(/```[\s\S]*?```/g, (match) => {
				const code = match.replace(/```\w*\n?/g, '').replace(/```/g, '');
				return `<pre><code>${code}</code></pre>`;
			})
			// Inline code
			.replace(/`([^`]+)`/g, '<code>$1</code>')
			// Links
			.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
			// Horizontal rule
			.replace(/^---$/gm, '<hr>')
			// Unordered lists
			.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
			// Ordered lists
			.replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
			// Paragraphs
			.replace(/\n\n/g, '</p><p>')
			// Line breaks
			.replace(/\n/g, '<br>');

		// Wrap consecutive <li> in <ul>
		html = html.replace(/(<li>.*?<\/li>(?:<br>)?)+/g, (match) => {
			return '<ul>' + match.replace(/<br>/g, '') + '</ul>';
		});

		if (!html.startsWith('<h') && !html.startsWith('<ul') && !html.startsWith('<pre') && !html.startsWith('<hr')) {
			html = '<p>' + html + '</p>';
		}

		return html;
	}
</script>

{#if show}
	<!-- Overlay -->
	<div
		class="splash-overlay"
		transition:fade={{ duration: 300 }}
		on:click={close}
		on:keydown={(e) => e.key === 'Escape' && close()}
		role="button"
		tabindex="-1"
		aria-label="Close notification"
	></div>

	<!-- Modal -->
	<div
		class="splash-modal"
		transition:scale={{ duration: 400, start: 0.9 }}
		role="dialog"
		aria-modal="true"
		aria-labelledby="splash-title"
	>
		<!-- Header -->
		<div class="splash-header">
			<h2 class="splash-title" id="splash-title">{title}</h2>
			<button class="splash-close" on:click={close} aria-label="关闭">
				<svg fill="none" height="20" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" viewBox="0 0 24 24" width="20">
					<line x1="18" x2="6" y1="6" y2="18"></line>
					<line x1="6" x2="18" y1="6" y2="18"></line>
				</svg>
			</button>
		</div>

		<!-- Content -->
		<div class="splash-content" role="article">
			{@html renderedHtml}
		</div>

		<!-- Footer -->
		<div class="splash-footer">
			<button class="splash-confirm" on:click={close}>关闭</button>
		</div>
	</div>
{/if}

<style>
	/* Overlay - iOS-style frosted glass */
	.splash-overlay {
		position: fixed;
		top: 0;
		left: 0;
		width: 100%;
		height: 100%;
		background: rgba(0, 0, 0, 0.05);
		backdrop-filter: blur(1.5px) saturate(180%);
		-webkit-backdrop-filter: blur(1.5px) saturate(180%);
		z-index: 9998;
	}

	/* Modal - Liquid Glass effect */
	.splash-modal {
		position: fixed;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		background: linear-gradient(135deg,
			rgba(255, 255, 255, 0.9) 0%,
			rgba(255, 255, 255, 0.75) 100%);
		backdrop-filter: blur(40px) saturate(200%);
		-webkit-backdrop-filter: blur(40px) saturate(200%);
		border: 1px solid rgba(255, 255, 255, 0.3);
		border-radius: 20px;
		box-shadow:
			0 8px 32px rgba(0, 0, 0, 0.1),
			0 2px 8px rgba(0, 0, 0, 0.05),
			inset 0 1px 0 rgba(255, 255, 255, 0.6),
			0 0 0 1px rgba(255, 255, 255, 0.05);
		z-index: 9999;
		max-width: 90%;
		max-height: 80vh;
		width: 600px;
		overflow: hidden;
	}

	:global(html.dark) .splash-modal {
		background: linear-gradient(135deg,
			rgba(30, 30, 30, 0.85) 0%,
			rgba(30, 30, 30, 0.75) 100%);
		border: 1px solid rgba(255, 255, 255, 0.1);
		box-shadow:
			0 8px 32px rgba(0, 0, 0, 0.3),
			0 2px 8px rgba(0, 0, 0, 0.2),
			inset 0 1px 0 rgba(255, 255, 255, 0.1);
		color: #e5e7eb;
	}

	/* Header */
	.splash-header {
		padding: 20px 24px;
		border-bottom: 1px solid rgba(0, 0, 0, 0.06);
		display: flex;
		justify-content: center;
		align-items: center;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.2) 0%, rgba(255, 255, 255, 0) 100%);
		position: relative;
	}

	:global(html.dark) .splash-header {
		border-bottom: 1px solid rgba(255, 255, 255, 0.08);
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 100%);
	}

	/* Title with shimmer animation */
	.splash-title {
		font-size: 20px;
		font-weight: 600;
		margin: 0;
		background: linear-gradient(135deg, #111827 0%, #374151 100%);
		-webkit-background-clip: text;
		-webkit-text-fill-color: transparent;
		background-clip: text;
		position: relative;
		overflow: hidden;
		padding: 0 40px;
		display: inline-block;
	}

	.splash-title::before {
		content: '';
		position: absolute;
		top: 0;
		left: -100%;
		width: 100%;
		height: 100%;
		background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.6) 50%, transparent 100%);
		animation: shimmer 3s infinite;
		pointer-events: none;
	}

	:global(html.dark) .splash-title {
		background: linear-gradient(135deg, #f3f4f6 0%, #d1d5db 100%);
		-webkit-background-clip: text;
		-webkit-text-fill-color: transparent;
		background-clip: text;
	}

	:global(html.dark) .splash-title::before {
		background: linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.3) 50%, transparent 100%);
	}

	@keyframes shimmer {
		0% { left: -100%; }
		50%, 100% { left: 200%; }
	}

	/* Close button */
	.splash-close {
		position: absolute;
		right: 20px;
		top: 50%;
		transform: translateY(-50%);
		background: rgba(255, 255, 255, 0.3);
		backdrop-filter: blur(10px);
		-webkit-backdrop-filter: blur(10px);
		border: 1px solid rgba(255, 255, 255, 0.2);
		cursor: pointer;
		padding: 6px;
		display: flex;
		align-items: center;
		justify-content: center;
		border-radius: 8px;
		transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
		color: #6b7280;
		box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
	}

	.splash-close:hover {
		background: rgba(255, 255, 255, 0.5);
		transform: translateY(-50%) scale(1.05);
	}

	:global(html.dark) .splash-close {
		background: rgba(255, 255, 255, 0.1);
		border: 1px solid rgba(255, 255, 255, 0.1);
		color: #9ca3af;
	}

	:global(html.dark) .splash-close:hover {
		background: rgba(255, 255, 255, 0.15);
	}

	/* Content area */
	.splash-content {
		padding: 24px;
		padding-right: 20px;
		max-height: calc(80vh - 140px);
		overflow-y: auto;
		overflow-x: hidden;
		-webkit-overflow-scrolling: touch;
		box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.06);
		scroll-behavior: smooth;
		animation: contentFadeIn 0.6s ease-out 0.2s both;
	}

	:global(html.dark) .splash-content {
		box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.2);
	}

	@keyframes contentFadeIn {
		from { opacity: 0; transform: translateY(10px); }
		to { opacity: 1; transform: translateY(0); }
	}

	/* Markdown content styles */
	.splash-content :global(h1),
	.splash-content :global(h2),
	.splash-content :global(h3),
	.splash-content :global(h4) {
		margin-top: 1.5em;
		margin-bottom: 0.5em;
		font-weight: 600;
	}

	.splash-content :global(h1:first-child),
	.splash-content :global(h2:first-child),
	.splash-content :global(h3:first-child) {
		margin-top: 0;
	}

	.splash-content :global(p) {
		margin: 1em 0;
		line-height: 1.6;
	}

	.splash-content :global(ul),
	.splash-content :global(ol) {
		margin: 1em 0;
		padding-left: 2em;
	}

	.splash-content :global(li) {
		margin: 0.5em 0;
	}

	.splash-content :global(pre) {
		background: rgba(0, 0, 0, 0.05);
		backdrop-filter: blur(10px);
		padding: 1em;
		border-radius: 10px;
		overflow-x: auto;
		border: 1px solid rgba(0, 0, 0, 0.08);
	}

	:global(html.dark) .splash-content :global(pre) {
		background: rgba(255, 255, 255, 0.05);
		border: 1px solid rgba(255, 255, 255, 0.1);
	}

	.splash-content :global(code) {
		background: rgba(0, 0, 0, 0.05);
		padding: 0.2em 0.4em;
		border-radius: 4px;
		font-size: 0.9em;
	}

	:global(html.dark) .splash-content :global(code) {
		background: rgba(255, 255, 255, 0.1);
	}

	.splash-content :global(pre code) {
		background: none;
		padding: 0;
	}

	.splash-content :global(a) {
		color: #3b82f6;
		text-decoration: none;
		transition: color 0.2s ease;
	}

	.splash-content :global(a:hover) {
		text-decoration: underline;
		color: #2563eb;
	}

	:global(html.dark) .splash-content :global(a) {
		color: #60a5fa;
	}

	:global(html.dark) .splash-content :global(a:hover) {
		color: #93c5fd;
	}

	.splash-content :global(hr) {
		border: none;
		border-top: 1px solid rgba(0, 0, 0, 0.1);
		margin: 1.5em 0;
	}

	:global(html.dark) .splash-content :global(hr) {
		border-top: 1px solid rgba(255, 255, 255, 0.1);
	}

	/* Footer */
	.splash-footer {
		padding: 16px 24px 20px 24px;
		border-top: 1px solid rgba(0, 0, 0, 0.06);
		display: flex;
		justify-content: flex-end;
		align-items: center;
		background: linear-gradient(0deg, rgba(255, 255, 255, 0.2) 0%, rgba(255, 255, 255, 0) 100%);
	}

	:global(html.dark) .splash-footer {
		border-top: 1px solid rgba(255, 255, 255, 0.08);
		background: linear-gradient(0deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 100%);
	}

	/* Confirm button */
	.splash-confirm {
		background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
		backdrop-filter: blur(10px);
		color: white;
		border: 1px solid rgba(255, 255, 255, 0.2);
		padding: 10px 24px;
		border-radius: 12px;
		cursor: pointer;
		font-size: 15px;
		font-weight: 600;
		transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
		box-shadow:
			0 4px 12px rgba(59, 130, 246, 0.3),
			inset 0 1px 0 rgba(255, 255, 255, 0.3);
		position: relative;
		overflow: hidden;
	}

	.splash-confirm::before {
		content: '';
		position: absolute;
		top: 0;
		left: -100%;
		width: 100%;
		height: 100%;
		background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
		transition: left 0.5s;
	}

	.splash-confirm:hover::before {
		left: 100%;
	}

	.splash-confirm:hover {
		background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
		transform: translateY(-1px);
		box-shadow:
			0 6px 16px rgba(59, 130, 246, 0.4),
			inset 0 1px 0 rgba(255, 255, 255, 0.3);
	}

	.splash-confirm:active {
		transform: translateY(0);
		box-shadow:
			0 2px 8px rgba(59, 130, 246, 0.3),
			inset 0 1px 0 rgba(255, 255, 255, 0.2);
	}

	/* Custom scrollbar */
	.splash-content::-webkit-scrollbar {
		width: 8px;
	}

	.splash-content::-webkit-scrollbar-track {
		background: rgba(0, 0, 0, 0.05);
		border-radius: 4px;
	}

	.splash-content::-webkit-scrollbar-thumb {
		background: rgba(0, 0, 0, 0.2);
		border-radius: 4px;
		border: 2px solid transparent;
		background-clip: padding-box;
	}

	.splash-content::-webkit-scrollbar-thumb:hover {
		background: rgba(0, 0, 0, 0.3);
		background-clip: padding-box;
	}

	:global(html.dark) .splash-content::-webkit-scrollbar-track {
		background: rgba(255, 255, 255, 0.05);
	}

	:global(html.dark) .splash-content::-webkit-scrollbar-thumb {
		background: rgba(255, 255, 255, 0.2);
		background-clip: padding-box;
	}

	:global(html.dark) .splash-content::-webkit-scrollbar-thumb:hover {
		background: rgba(255, 255, 255, 0.3);
		background-clip: padding-box;
	}

	/* Mobile */
	@media (max-width: 640px) {
		.splash-modal {
			width: 90%;
			max-width: none;
			height: 85vh;
			max-height: none;
			border-radius: 16px;
		}

		.splash-header {
			padding: 16px 20px;
		}

		.splash-title {
			font-size: 18px;
			padding: 0 30px;
		}

		.splash-close {
			right: 16px;
			padding: 5px;
		}

		.splash-content {
			padding: 20px;
			padding-right: 16px;
			max-height: calc(85vh - 140px);
		}

		.splash-footer {
			padding: 16px 20px 24px 20px;
		}
	}
</style>
