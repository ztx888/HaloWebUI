<script>
	import { io } from 'socket.io-client';

	import { onMount, tick, setContext } from 'svelte';
	import {
		config,
		user,
		settings,
		theme,
		WEBUI_NAME,
		mobile,
		socket,
		activeUserIds,
		USAGE_POOL,
		chatId,
		chats,
		currentChatPage,
		tags,
		temporaryChatEnabled,
		isLastActiveTab,
		isApp,
		appInfo,
		toolServers
	} from '$lib/stores';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Toaster, toast } from 'svelte-sonner';

	import { executeToolServer, getBackendConfig } from '$lib/apis';
	import { getSessionUser } from '$lib/apis/auths';
	import { APP_NAME } from '$lib/constants';

	import '../tailwind.css';
	import '../app.css';

	import 'tippy.js/dist/tippy.css';

	import { WEBUI_BASE_URL, WEBUI_HOSTNAME } from '$lib/constants';
	import i18n, { initI18n, getLanguages, changeLanguage } from '$lib/i18n';
	import { bestMatchingLanguage } from '$lib/utils';
	import {
		approvePyodideConsent,
		canUsePyodideRuntime,
		getPyodideDownloadSummary,
		getPyodidePackagesForCode,
		hasPyodideConsent,
		usesRemotePyodideRuntime
	} from '$lib/utils/browser-ai-assets';
	import { localizeCommonError } from '$lib/utils/common-errors';
	import { initScrollbarAutohide } from '$lib/utils/scrollbars';
	import { setTextScale } from '$lib/utils/text-scale';
	import { getAllTags, getChatList } from '$lib/apis/chats';
	import NotificationToast from '$lib/components/NotificationToast.svelte';
	import AppSidebar from '$lib/components/app/AppSidebar.svelte';

	setContext('i18n', i18n);

	const bc = new BroadcastChannel('active-tab-channel');
	const PYODIDE_DISABLED_MESSAGE = 'Pyodide is disabled in this build.';
	const CHAT_COMPLETION_NOTIFICATION_TTL = 60 * 1000;

	let loaded = false;
	let currentSocket = null;
	let unsubscribeUser = null;
	const notifiedChatCompletions = new Map();

	const BREAKPOINT = 768;

	$: setTextScale($settings?.textScale ?? 1);

	const formatError = (error) =>
		localizeCommonError(error, (key, options) => $i18n.t(key, options));

	const normalizeTheme = (rawTheme) => {
		if (rawTheme === 'system' || rawTheme === 'dark' || rawTheme === 'light') return rawTheme;
		if (rawTheme === 'oled-dark' || rawTheme === 'her' || rawTheme === 'rose-pine dark')
			return 'dark';
		if (rawTheme === 'rose-pine-dawn light') return 'light';
		return 'system';
	};

	const handleSocketConnectError = (err) => {
		console.log('connect_error', err);
	};

	const handleSocketConnect = () => {
		console.log('connected', currentSocket?.id);
	};

	const handleSocketReconnectAttempt = (attempt) => {
		console.log('reconnect_attempt', attempt);
	};

	const handleSocketReconnectFailed = () => {
		console.log('reconnect_failed');
	};

	const handleSocketDisconnect = (reason, details) => {
		console.log(`Socket ${currentSocket?.id} disconnected due to ${reason}`);
		if (details) {
			console.log('Additional details:', details);
		}
	};

	const handleSocketUserList = (data) => {
		console.log('user-list', data);
		activeUserIds.set(data.user_ids);
	};

	const handleSocketUsage = (data) => {
		console.log('usage', data);
		USAGE_POOL.set(data['models']);
	};

	const detachSocketEventHandlers = (socketInstance) => {
		socketInstance?.off('chat-events', chatEventHandler);
		socketInstance?.off('channel-events', channelEventHandler);
		socketInstance?.off('connect_error', handleSocketConnectError);
		socketInstance?.off('connect', handleSocketConnect);
		socketInstance?.off('reconnect_attempt', handleSocketReconnectAttempt);
		socketInstance?.off('reconnect_failed', handleSocketReconnectFailed);
		socketInstance?.off('disconnect', handleSocketDisconnect);
		socketInstance?.off('user-list', handleSocketUserList);
		socketInstance?.off('usage', handleSocketUsage);
	};

	const attachSocketEventHandlers = (socketInstance) => {
		if (!socketInstance) {
			return;
		}

		socketInstance.off('chat-events', chatEventHandler);
		socketInstance.off('channel-events', channelEventHandler);

		if ($user) {
			socketInstance.on('chat-events', chatEventHandler);
			socketInstance.on('channel-events', channelEventHandler);
		}
	};

	const teardownSocket = (socketInstance) => {
		if (!socketInstance) {
			return;
		}

		detachSocketEventHandlers(socketInstance);
		socketInstance.disconnect();

		if (currentSocket === socketInstance) {
			currentSocket = null;
		}

		if ($socket === socketInstance) {
			socket.set(null);
		}
	};

	const shouldShowChatCompletionNotification = (event, data) => {
		const now = Date.now();
		for (const [key, timestamp] of notifiedChatCompletions) {
			if (now - timestamp > CHAT_COMPLETION_NOTIFICATION_TTL) {
				notifiedChatCompletions.delete(key);
			}
		}

		const notificationKey = `${event.chat_id}:${event.message_id ?? data?.title ?? 'unknown'}`;
		if (notifiedChatCompletions.has(notificationKey)) {
			return false;
		}

		notifiedChatCompletions.set(notificationKey, now);
		return true;
	};

	const setupSocket = async (enableWebsocket) => {
		teardownSocket(currentSocket ?? $socket);

		const _socket = io(`${WEBUI_BASE_URL}` || undefined, {
			reconnection: true,
			reconnectionDelay: 1000,
			reconnectionDelayMax: 5000,
			randomizationFactor: 0.5,
			path: '/ws/socket.io',
			transports: enableWebsocket ? ['websocket'] : ['polling', 'websocket'],
			auth: { token: localStorage.token }
		});

		currentSocket = _socket;
		socket.set(_socket);

		_socket.on('connect_error', handleSocketConnectError);
		_socket.on('connect', handleSocketConnect);
		_socket.on('reconnect_attempt', handleSocketReconnectAttempt);
		_socket.on('reconnect_failed', handleSocketReconnectFailed);
		_socket.on('disconnect', handleSocketDisconnect);
		_socket.on('user-list', handleSocketUserList);
		_socket.on('usage', handleSocketUsage);
		attachSocketEventHandlers(_socket);
	};

	const executePythonAsWorker = async (id, code, cb) => {
		if (!canUsePyodideRuntime()) {
			if (cb) {
				cb({
					stdout: null,
					stderr: PYODIDE_DISABLED_MESSAGE,
					result: null
				});
			}
			return;
		}

		let result = null;
		let stdout = null;
		let stderr = null;

		let executing = true;
		const packages = getPyodidePackagesForCode(code);

		if (usesRemotePyodideRuntime() && !hasPyodideConsent()) {
			const shouldContinue = window.confirm(getPyodideDownloadSummary(packages));
			if (!shouldContinue) {
				executing = false;
				if (cb) {
					cb({
						stdout: null,
						stderr: '已取消下载浏览器 Python 运行时。',
						result: null
					});
				}
				return;
			}
			approvePyodideConsent();
		}

		const { default: PyodideWorker } = await import('$lib/workers/pyodide.worker?worker');
		const pyodideWorker = new PyodideWorker();

		pyodideWorker.postMessage({
			id: id,
			code: code,
			packages: packages
		});

		setTimeout(() => {
			if (executing) {
				executing = false;
				stderr = 'Execution Time Limit Exceeded';
				pyodideWorker.terminate();

				if (cb) {
					cb(
						JSON.parse(
							JSON.stringify(
								{
									stdout: stdout,
									stderr: stderr,
									result: result
								},
								(_key, value) => (typeof value === 'bigint' ? value.toString() : value)
							)
						)
					);
				}
			}
		}, 60000);

		pyodideWorker.onmessage = (event) => {
			console.log('pyodideWorker.onmessage', event);
			const { id, ...data } = event.data;

			console.log(id, data);

			data['stdout'] && (stdout = data['stdout']);
			data['stderr'] && (stderr = data['stderr']);
			data['result'] && (result = data['result']);

			if (cb) {
				cb(
					JSON.parse(
						JSON.stringify(
							{
								stdout: stdout,
								stderr: stderr,
								result: result
							},
							(_key, value) => (typeof value === 'bigint' ? value.toString() : value)
						)
					)
				);
			}

			executing = false;
		};

		pyodideWorker.onerror = (event) => {
			console.log('pyodideWorker.onerror', event);

			if (cb) {
				cb(
					JSON.parse(
						JSON.stringify(
							{
								stdout: stdout,
								stderr: stderr,
								result: result
							},
							(_key, value) => (typeof value === 'bigint' ? value.toString() : value)
						)
					)
				);
			}
			executing = false;
		};
	};

	const executeTool = async (data, cb) => {
		const toolServer = $settings?.toolServers?.find((server) => server.url === data.server?.url);
		const toolServerData = $toolServers?.find((server) => server.url === data.server?.url);

		console.log('executeTool', data, toolServer);

		if (toolServer) {
			console.log(toolServer);
			const res = await executeToolServer(
				(toolServer?.auth_type ?? 'bearer') === 'bearer' ? toolServer?.key : localStorage.token,
				toolServer.url,
				data?.name,
				data?.params,
				toolServerData
			);

			console.log('executeToolServer', res);
			if (cb) {
				cb(JSON.parse(JSON.stringify(res)));
			}
		} else {
			if (cb) {
				cb(
					JSON.parse(
						JSON.stringify({
							error: 'Tool Server Not Found'
						})
					)
				);
			}
		}
	};

	const chatEventHandler = async (event, cb) => {
		const chat = $page.url.pathname.includes(`/c/${event.chat_id}`);

		let isFocused = document.visibilityState !== 'visible';
		if (window.electronAPI) {
			const res = await window.electronAPI.send({
				type: 'window:isFocused'
			});
			if (res) {
				isFocused = res.isFocused;
			}
		}

		await tick();
		const type = event?.data?.type ?? null;
		const data = event?.data?.data ?? null;

		if ((event.chat_id !== $chatId && !$temporaryChatEnabled) || isFocused) {
			if (type === 'chat:completion') {
				const { done, content, title } = data;

				if (done) {
					if (!shouldShowChatCompletionNotification(event, data)) {
						return;
					}

					if ($isLastActiveTab) {
						if ($settings?.notificationEnabled ?? false) {
							new Notification(`${title} | ${APP_NAME}`, {
								body: content,
								icon: `${WEBUI_BASE_URL}/static/favicon.png`
							});
						}
					}

					toast.custom(NotificationToast, {
						componentProps: {
							onClick: () => {
								goto(`/c/${event.chat_id}`);
							},
							content: content,
							title: title
						},
						duration: 15000,
						unstyled: true
					});
				}
			} else if (type === 'chat:title') {
				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
			} else if (type === 'chat:tags') {
				tags.set(await getAllTags(localStorage.token));
			}
		} else if (data?.session_id === $socket.id) {
			if (type === 'execute:python') {
				console.log('execute:python', data);
				executePythonAsWorker(data.id, data.code, cb);
			} else if (type === 'execute:tool') {
				console.log('execute:tool', data);
				executeTool(data, cb);
			} else if (type === 'request:chat:completion') {
				// Legacy "directConnections" path (browser makes the OpenAI-compatible request).
				// This UI now uses server-side connections, so we explicitly reject to avoid hanging tasks.
				try {
					cb({
						error: {
							message: 'Direct connections are disabled. Please use Settings > Connections.',
							type: 'direct_connections_disabled'
						}
					});
				} catch (e) {
					// no-op
				} finally {
					try {
						const channel = data?.channel;
						if (channel) {
							$socket.emit(channel, { done: true });
						}
					} catch (e) {
						// no-op
					}
				}
			} else {
				console.log('chatEventHandler', event);
			}
		}
	};

	const channelEventHandler = async (event) => {
		if (event.data?.type === 'typing') {
			return;
		}

		// check url path
		const channel = $page.url.pathname.includes(`/channels/${event.channel_id}`);

		let isFocused = document.visibilityState !== 'visible';
		if (window.electronAPI) {
			const res = await window.electronAPI.send({
				type: 'window:isFocused'
			});
			if (res) {
				isFocused = res.isFocused;
			}
		}

		if ((!channel || isFocused) && event?.user?.id !== $user?.id) {
			await tick();
			const type = event?.data?.type ?? null;
			const data = event?.data?.data ?? null;

			if (type === 'message') {
				if ($isLastActiveTab) {
					if ($settings?.notificationEnabled ?? false) {
						new Notification(`${data?.user?.name} (#${event?.channel?.name}) | ${APP_NAME}`, {
							body: data?.content,
							icon: data?.user?.profile_image_url ?? `${WEBUI_BASE_URL}/static/favicon.png`
						});
					}
				}

				toast.custom(NotificationToast, {
					componentProps: {
						onClick: () => {
							goto(`/channels/${event.channel_id}`);
						},
						content: data?.content,
						title: event?.channel?.name
					},
					duration: 15000,
					unstyled: true
				});
			}
		}
	};

	onMount(async () => {
		initScrollbarAutohide();

		if (typeof window !== 'undefined' && window.applyTheme) {
			window.applyTheme();
		}

		if (window?.electronAPI) {
			const info = await window.electronAPI.send({
				type: 'app:info'
			});

			if (info) {
				isApp.set(true);
				appInfo.set(info);

				const data = await window.electronAPI.send({
					type: 'app:data'
				});

				if (data) {
					appData.set(data);
				}
			}
		}

		// Listen for messages on the BroadcastChannel
		bc.onmessage = (event) => {
			if (event.data === 'active') {
				isLastActiveTab.set(false); // Another tab became active
			}
		};

		// Set yourself as the last active tab when this tab is focused
		const handleVisibilityChange = () => {
			if (document.visibilityState === 'visible') {
				isLastActiveTab.set(true); // This tab is now the active tab
				bc.postMessage('active'); // Notify other tabs that this tab is active
			}
		};

		// Add event listener for visibility state changes
		document.addEventListener('visibilitychange', handleVisibilityChange);

		// Call visibility change handler initially to set state on load
		handleVisibilityChange();

		const normalizedTheme = normalizeTheme(localStorage.theme);
		if (localStorage.theme !== normalizedTheme) {
			localStorage.theme = normalizedTheme;
		}
		theme.set(normalizedTheme);

		mobile.set(window.innerWidth < BREAKPOINT);

		const onResize = () => {
			if (window.innerWidth < BREAKPOINT) {
				mobile.set(true);
			} else {
				mobile.set(false);
			}
		};
		window.addEventListener('resize', onResize);

		unsubscribeUser = user.subscribe((value) => {
			if (value) {
				attachSocketEventHandlers(currentSocket);
			} else {
				currentSocket?.off('chat-events', chatEventHandler);
				currentSocket?.off('channel-events', channelEventHandler);
			}
		});

		let backendConfig = null;
		try {
			backendConfig = await getBackendConfig();
			console.log('Backend config:', backendConfig);
		} catch (error) {
			console.error('Error loading backend config:', error);
		}
		// Initialize i18n even if we didn't get a backend config,
		// so `/error` can show something that's not `undefined`.

		initI18n(localStorage?.locale);
		if (!localStorage.locale) {
			const languages = await getLanguages();
			const browserLanguages = navigator.languages
				? navigator.languages
				: [navigator.language || navigator.userLanguage];
			const lang = backendConfig.default_locale
				? backendConfig.default_locale
				: bestMatchingLanguage(languages, browserLanguages, 'en-US');
			changeLanguage(lang);
		}

		if (backendConfig) {
			// Save Backend Status to Store
			// Branding: treat APP_NAME as source of truth for frontend identity.
			// Backend config name may still be the upstream default ("Open WebUI"), which causes title flicker.
			await config.set({ ...backendConfig, name: APP_NAME });
			await WEBUI_NAME.set(APP_NAME);

			if ($config) {
				await setupSocket($config.features?.enable_websocket ?? true);

				const currentUrl = `${window.location.pathname}${window.location.search}`;
				const encodedUrl = encodeURIComponent(currentUrl);

				if (localStorage.token) {
					// Get Session User Info
					const sessionUser = await getSessionUser(localStorage.token).catch((error) => {
						toast.error(formatError(error));
						return null;
					});

					if (sessionUser) {
						// Save Session User to Store
						$socket.emit('user-join', { auth: { token: sessionUser.token } });

						await user.set(sessionUser);
					} else {
						// Redirect Invalid Session User to /auth Page
						localStorage.removeItem('token');
						await goto(`/auth?redirect=${encodedUrl}`);
					}
				} else {
					// Don't redirect if we're already on the auth page
					// Needed because we pass in tokens from OAuth logins via URL fragments
					if ($page.url.pathname !== '/auth') {
						await goto(`/auth?redirect=${encodedUrl}`);
					}
				}
			}
		} else {
			// Redirect to /error when Backend Not Detected
			await goto(`/error`);
		}

		await tick();
		document.getElementById('splash-screen')?.remove();
		loaded = true;

		return () => {
			unsubscribeUser?.();
			unsubscribeUser = null;
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			bc.onmessage = null;
			bc.close();
			teardownSocket(currentSocket);
			window.removeEventListener('resize', onResize);
		};
	});
</script>

<svelte:head>
	<title>{$WEBUI_NAME}</title>
	<link
		crossorigin="anonymous"
		rel="icon"
		href="{WEBUI_BASE_URL}/static/{$theme === 'dark' ||
		($theme === 'system' &&
			typeof window !== 'undefined' &&
			window.matchMedia('(prefers-color-scheme: dark)').matches)
			? 'favicon-dark.png'
			: 'favicon.png'}"
	/>

	<!-- rosepine themes have been disabled as it's not up to date with our latest version. -->
	<!-- feel free to make a PR to fix if anyone wants to see it return -->
	<!-- <link rel="stylesheet" type="text/css" href="/themes/rosepine.css" />
	<link rel="stylesheet" type="text/css" href="/themes/rosepine-dawn.css" /> -->
</svelte:head>

{#if loaded}
	<a
		href="#main-content"
		class="sr-only focus:not-sr-only focus:absolute focus:z-[9999] focus:top-2 focus:left-2 focus:px-4 focus:py-2 focus:bg-white focus:dark:bg-gray-900 focus:text-sm focus:rounded-lg focus:shadow-lg"
	>
		{$i18n?.t?.('Skip to content') ?? 'Skip to content'}
	</a>
	{#if $isApp}
		<div class="flex flex-row h-screen">
			<AppSidebar />

			<div id="main-content" role="main" class="w-full flex-1 max-w-[calc(100%-4.5rem)]">
				<slot />
			</div>
		</div>
	{:else}
		<div id="main-content" role="main">
			<slot />
		</div>
	{/if}
{/if}

<Toaster
	theme={$theme.includes('dark')
		? 'dark'
		: $theme === 'system'
			? window.matchMedia('(prefers-color-scheme: dark)').matches
				? 'dark'
				: 'light'
			: 'light'}
	richColors
	closeButton
	position="top-right"
	toastOptions={{
		duration: 4000
	}}
/>
