<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getSplashNotificationAdmin, setSplashNotificationAdmin } from '$lib/apis/configs';
	import Switch from '$lib/components/common/Switch.svelte';

	import type { Writable } from 'svelte/store';
	const i18n: Writable<any> = getContext('i18n');

	export let saveHandler: Function;

	let config = {
		SPLASH_NOTIFICATION_ENABLED: false,
		SPLASH_NOTIFICATION_TITLE: '公告通知',
		SPLASH_NOTIFICATION_CONTENT: ''
	};

	let loading = true;

	onMount(async () => {
		const data = await getSplashNotificationAdmin(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			config = { ...config, ...data };
		}
		loading = false;
	});

	const submitHandler = async () => {
		const res = await setSplashNotificationAdmin(localStorage.token, config).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			saveHandler();
		}
	};
</script>

{#if !loading}
<form
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={() => {
		submitHandler();
	}}
>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		<!-- Enable/Disable Toggle -->
		<div class="mb-4">
			<div class="flex items-center justify-between mb-3">
				<div class="text-base font-medium">{$i18n.t('Splash Notification')}</div>
				<div class="flex items-center gap-2">
					<div class="text-sm font-medium text-gray-700 dark:text-gray-300">{$i18n.t('Enable')}</div>
					<Switch bind:state={config.SPLASH_NOTIFICATION_ENABLED} />
				</div>
			</div>

			<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800 space-y-4">
				<!-- Title -->
				<div>
					<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Notification Title')}</div>
					<input
						class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition"
						bind:value={config.SPLASH_NOTIFICATION_TITLE}
						placeholder={$i18n.t('Enter notification title')}
					/>
				</div>

				<!-- Content (Markdown) -->
				<div>
					<div class="text-xs font-medium text-gray-500 mb-1.5">
						{$i18n.t('Notification Content')}
						<span class="text-gray-400 ml-1">({$i18n.t('Supports Markdown')})</span>
					</div>
					<textarea
						class="w-full rounded-lg py-2 px-3 text-sm bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 outline-none focus:border-gray-300 dark:focus:border-gray-700 transition font-mono"
						rows="15"
						bind:value={config.SPLASH_NOTIFICATION_CONTENT}
						placeholder={$i18n.t('Enter notification content in Markdown format...\n\nExample:\n# Welcome\nThis is a **notification** message.\n\n- Item 1\n- Item 2')}
					></textarea>
				</div>

				<!-- Preview -->
				{#if config.SPLASH_NOTIFICATION_CONTENT}
					<div>
						<div class="text-xs font-medium text-gray-500 mb-1.5">{$i18n.t('Preview')}</div>
						<div class="rounded-lg p-4 bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800 prose dark:prose-invert prose-sm max-w-none overflow-auto max-h-80">
							{@html markdownToHtml(config.SPLASH_NOTIFICATION_CONTENT)}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>

	<div class="flex justify-end pt-3">
		<button
			class="px-4 py-2 text-sm font-semibold bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-xl transition"
			type="submit"
		>
			{$i18n.t('Save Configuration')}
		</button>
	</div>
</form>
{/if}

<script context="module" lang="ts">
	function markdownToHtml(md: string): string {
		if (!md) return '';
		// Simple markdown to HTML converter
		let html = md
			// Escape HTML
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			// Headers
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
			// Unordered lists
			.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
			// Ordered lists
			.replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
			// Paragraphs (double newlines)
			.replace(/\n\n/g, '</p><p>')
			// Single newlines to <br>
			.replace(/\n/g, '<br>');

		// Wrap consecutive <li> in <ul>
		html = html.replace(/(<li>.*?<\/li>(?:<br>)?)+/g, (match) => {
			return '<ul>' + match.replace(/<br>/g, '') + '</ul>';
		});

		// Wrap in paragraph if not already
		if (!html.startsWith('<h') && !html.startsWith('<ul') && !html.startsWith('<pre')) {
			html = '<p>' + html + '</p>';
		}

		return html;
	}
</script>
