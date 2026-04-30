<script lang="ts">
	import { onMount, getContext, setContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import MenuLines from '$lib/components/icons/MenuLines.svelte';
	import { WEBUI_NAME, config, mobile, showSidebar, user } from '$lib/stores';
	import { ensureModels, refreshModels } from '$lib/services/models';
	import { getErrorDetail } from '$lib/apis/response';
	import { saveUserSettingsPatch } from '$lib/utils/user-settings';

	const i18n: Writable<any> = getContext('i18n');

	// Expose a single, consistent way for every Settings section to persist user UI settings.
	const getModels = async () => {
		return await ensureModels(localStorage.token, { reason: 'settings' });
	};

	const saveSettings = async (updated: any, options: { refreshModels?: boolean } = {}) => {
		try {
			await saveUserSettingsPatch(localStorage.token, updated);

			if (options.refreshModels) {
				// Refresh model list when settings affect model availability/behavior.
				await refreshModels(localStorage.token, { force: true, reason: 'settings-save' });
			}
		} catch (error) {
			const isConflict = (error as { status?: number })?.status === 409;
			toast.error(
				isConflict
					? $i18n.t(
							'Settings changed in another tab. The latest settings have been reloaded; please review and save again.'
						)
					: getErrorDetail(error, $i18n.t('Failed to update settings'))
			);
			if (error && typeof error === 'object') {
				(error as { __toastShown?: boolean }).__toastShown = true;
			}
			throw error;
		}
	};

	setContext('user-settings', { saveSettings, getModels });

	let loaded = false;

	$: isAdmin = $user?.role === 'admin';

	onMount(() => {
		loaded = true;
		void ensureModels(localStorage.token, { reason: 'settings-layout' }).catch(() => {});
		if ($user?.role !== 'admin' && $page.url.pathname === '/settings') {
			goto('/settings/interface');
		}
	});

	let currentPath = '';
	let activeLinks = {
		general: false,
		interface: false,
		connections: false,
		tools: false,
		audio: false,
		dataManagement: false,
		account: false,
		// users removed - merged into account
		functions: false,
		models: false,
		documents: false,
		webSearch: false,
		codeExecution: false,
		images: false,
		analytics: false,
		haloclaw: false,
		externalApi: false
	};
	$: currentPath = $page.url.pathname;
	$: {
		const path = currentPath || '';
		activeLinks = {
			general: path === '/settings',
			interface: path.startsWith('/settings/interface'),
			connections: path.startsWith('/settings/connections'),
			tools: path.startsWith('/settings/tools'),
			audio: path.startsWith('/settings/audio') || path.startsWith('/settings/system-audio'),
			dataManagement: path.startsWith('/settings/chats'),
			account: path.startsWith('/settings/account'),
			// users removed - merged into account
			functions: path.startsWith('/settings/functions'),
			models: path.startsWith('/settings/models'),
			documents: path.startsWith('/settings/documents'),
			webSearch: path.startsWith('/settings/web-search'),
			codeExecution: path.startsWith('/settings/code-execution'),
			images: path.startsWith('/settings/images'),
			analytics: path.startsWith('/settings/analytics'),
			haloclaw: path.startsWith('/settings/haloclaw'),
			externalApi: path.startsWith('/settings/external-api')
		};
	}

	const navLinkClass = (active: boolean) =>
		`px-2 py-1.5 min-w-fit rounded-lg flex-1 lg:flex-none flex items-center transition ${
			active ? '' : 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'
		}`;
</script>

<svelte:head>
	<title>{$i18n.t('Settings')} | {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<div class="relative flex flex-col w-full h-screen max-h-[100dvh] max-w-full">
		<nav class="px-2.5 pt-1 backdrop-blur-xl drag-region">
			<div class="flex items-center gap-1">
				<div class="{$mobile ? '' : 'hidden'} self-center flex flex-none items-center">
					<button
						id="sidebar-toggle-button"
						class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition"
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label="Toggle Sidebar"
					>
						<div class="m-auto self-center">
							<MenuLines />
						</div>
					</button>
				</div>

				<div class="flex items-center text-sm font-semibold px-1 py-1">
					{$i18n.t('Settings')}
				</div>
			</div>
		</nav>

		<div
			class="pb-1 px-[18px] flex-1 max-h-full overflow-y-auto"
			id="settings-container"
		>
			<div class="flex flex-col lg:flex-row w-full h-full min-h-0 pb-2 lg:space-x-4">
				<div
					id="settings-tabs-container"
					class="flex flex-row overflow-x-auto gap-2.5 max-w-full lg:gap-1 lg:flex-col lg:flex-none lg:w-44 dark:text-gray-200 text-sm font-medium text-left scrollbar-none"
				>
					{#if isAdmin}
					<a class={navLinkClass(activeLinks.general)} href="/settings">{$i18n.t('General')}</a>
				{/if}
					<a class={navLinkClass(activeLinks.interface)} href="/settings/interface"
						>{$i18n.t('Interface')}</a
					>

					<a class={navLinkClass(activeLinks.connections)} href="/settings/connections"
						>{$i18n.t('Connections')}</a
					>

						<a class={navLinkClass(activeLinks.tools)} href="/settings/tools"
							>{$i18n.t('Tool Integrations', { defaultValue: $i18n.t('Tools') })}</a
						>

						<a class={navLinkClass(activeLinks.audio)} href="/settings/audio">{$i18n.t('Audio')}</a>
						<a class={navLinkClass(activeLinks.dataManagement)} href="/settings/chats">{$i18n.t('Database')}</a>
						<a class={navLinkClass(activeLinks.account)} href="/settings/account"
							>{$i18n.t('Account Management', { defaultValue: $i18n.t('Account') })}</a
						>
					{#if isAdmin}
						<a class={navLinkClass(activeLinks.models)} href="/settings/models"
							>{$i18n.t('Model Management')}</a
						>
						<a class={navLinkClass(activeLinks.documents)} href="/settings/documents"
							>{$i18n.t('Documents')}</a
						>
						<a class={navLinkClass(activeLinks.webSearch)} href="/settings/web-search"
							>{$i18n.t('Web Search')}</a
						>
						<a class={navLinkClass(activeLinks.codeExecution)} href="/settings/code-execution"
							>{$i18n.t('Code Execution')}</a
						>
						<a class={navLinkClass(activeLinks.images)} href="/settings/images"
							>{$i18n.t('Images')}</a
						>
						<a class={navLinkClass(activeLinks.analytics)} href="/settings/analytics"
							>{$i18n.t('Analytics')}</a
						>
						<a class={navLinkClass(activeLinks.externalApi)} href="/settings/external-api">
							外部 API
						</a>
						<a class={navLinkClass(activeLinks.haloclaw)} href="/settings/haloclaw"
							>{$i18n.t('HaloClaw')}</a
						>
					{/if}
				</div>

				<div class="min-w-0 flex-1 min-h-0">
					<slot />
				</div>
			</div>
		</div>
	</div>
{/if}
