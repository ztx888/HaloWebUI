<script lang="ts">
	import { getOllamaVersion } from '$lib/apis/ollama';
	import { WEBUI_BUILD_HASH, WEBUI_VERSION } from '$lib/constants';
	import { WEBUI_NAME, config, showChangelog } from '$lib/stores';
	import { pwaInstallState, promptPWAInstall } from '$lib/utils/pwa';
	import { onMount, getContext } from 'svelte';

	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	let ollamaVersion = '';

	let updateAvailable = null;
	let currentVersion = WEBUI_VERSION;
	let version = {
		current: '',
		latest: ''
	};

	$: currentVersion = $config?.version ?? WEBUI_VERSION;

	const checkForVersionUpdates = async () => {
		updateAvailable = null;
		version = {
			current: currentVersion,
			latest: currentVersion
		};
		updateAvailable = false;
	};

	onMount(async () => {
		ollamaVersion = await getOllamaVersion(localStorage.token).catch((error) => {
			return '';
		});

		checkForVersionUpdates();
	});

	const getInstallHint = () => {
		if ($pwaInstallState.manualHint === 'android') {
			return $i18n.t(
				'On Android Chrome or Samsung Internet, open the browser menu and choose Install app or Add to Home screen.'
			);
		}

		if ($pwaInstallState.manualHint === 'ios') {
			return $i18n.t(
				'On iPhone or iPad Safari, tap Share and then choose Add to Home Screen.'
			);
		}

		return $i18n.t(
			'If your browser does not show a direct install button, use the browser menu to install this site as an app.'
		);
	};
</script>

<div class="flex flex-col h-full justify-between space-y-3 text-sm mb-6 max-w-6xl mx-auto w-full">
	<div class=" space-y-3 overflow-y-auto max-h-[28rem] lg:max-h-full">
		<div>
			<div class=" mb-2.5 text-sm font-medium flex space-x-2 items-center">
				<div>
					{$WEBUI_NAME}
					{$i18n.t('Version')}
				</div>
			</div>
			<div class="flex w-full justify-between items-center">
				<div class="flex flex-col text-xs text-gray-700 dark:text-gray-200">
					<div class="flex gap-1">
						<Tooltip content={WEBUI_BUILD_HASH}>
							V{currentVersion}
						</Tooltip>

						<span
							class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-600 dark:bg-amber-900/25 dark:text-amber-400 leading-none"
						>
							测试版
						</span>
					</div>

					<button
						class=" underline flex items-center space-x-1 text-xs text-gray-500 dark:text-gray-500"
						on:click={() => {
							showChangelog.set(true);
						}}
					>
						<div>{$i18n.t("See what's new")}</div>
					</button>
				</div>

				<button
					class=" text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition rounded-lg font-medium"
					on:click={() => {
						checkForVersionUpdates();
					}}
				>
					{$i18n.t('Check for updates')}
				</button>
			</div>
		</div>

		<hr class=" border-gray-100 dark:border-gray-850" />

		<div>
			<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Install App')}</div>
			<div class="flex flex-col gap-2 text-xs text-gray-600 dark:text-gray-300">
				<div>
					{$i18n.t(
						'Install Halo WebUI on your device for a cleaner mobile experience. Offline support only covers the app shell and settings pages; chats and sync still require a network connection.'
					)}
				</div>

				<div class="flex flex-wrap items-center gap-2">
					{#if $pwaInstallState.installed}
						<span
							class="inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
						>
							{$i18n.t('Installed')}
						</span>
						<span class="text-gray-500 dark:text-gray-400">
							{$i18n.t('Halo WebUI is already installed on this device.')}
						</span>
					{:else if $pwaInstallState.canInstall}
						<button
							class="text-xs px-3 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition rounded-lg font-medium"
							on:click={async () => {
								await promptPWAInstall();
							}}
						>
							{$i18n.t('Install Halo WebUI')}
						</button>
						<span class="text-gray-500 dark:text-gray-400">
							{$i18n.t('The browser can install Halo WebUI directly on this device.')}
						</span>
					{:else}
						<span class="text-gray-500 dark:text-gray-400">{getInstallHint()}</span>
					{/if}
				</div>
			</div>
		</div>

		{#if ollamaVersion}
			<hr class=" border-gray-100 dark:border-gray-850" />

			<div>
				<div class=" mb-2.5 text-sm font-medium">{$i18n.t('Ollama Version')}</div>
				<div class="flex w-full">
					<div class="flex-1 text-xs text-gray-700 dark:text-gray-200">
						{ollamaVersion ?? 'N/A'}
					</div>
				</div>
			</div>
		{/if}

		<hr class=" border-gray-100 dark:border-gray-850" />

		{#if $config?.license_metadata}
			<div class="mb-2 text-xs">
				{#if !$WEBUI_NAME.includes('Halo WebUI')}
					<span class=" text-gray-500 dark:text-gray-300 font-medium">{$WEBUI_NAME}</span> -
				{/if}

				<span class=" capitalize">{$config?.license_metadata?.type}</span> license purchased by
				<span class=" capitalize">{$config?.license_metadata?.organization_name}</span>
			</div>
		{:else}
			<div class="flex space-x-1">
				<a href="https://github.com/ztx888/HaloWebUI" target="_blank">
					<img
						alt="Github Repo"
						src="https://img.shields.io/github/stars/ztx888/HaloWebUI?style=social&label=Star us on Github"
					/>
				</a>
			</div>
		{/if}

		<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
			Emoji graphics provided by
			<a href="https://github.com/jdecked/twemoji" target="_blank">Twemoji</a>, licensed under
			<a href="https://creativecommons.org/licenses/by/4.0/" target="_blank">CC-BY 4.0</a>.
		</div>

		<div>
			<pre
				class="text-xs text-gray-400 dark:text-gray-500">Copyright (c) {new Date().getFullYear()} <a
					href="https://openwebui.com"
					target="_blank"
					class="underline">Open WebUI (Timothy Jaeryang Baek)</a
				>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
</pre>
		</div>

		<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
			{$i18n.t('Created by')}
			<a
				class=" text-gray-500 dark:text-gray-300 font-medium"
				href="https://github.com/tjbck"
				target="_blank">Timothy J. Baek</a
			>
		</div>
	</div>
</div>
