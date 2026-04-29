const CURRENT_URL = new URL(self.location.href);
const BUILD_HASH = CURRENT_URL.searchParams.get('build') || 'dev-build';
const CACHE_PREFIX = 'halo-pwa';
const SHELL_CACHE = `${CACHE_PREFIX}-shell-${BUILD_HASH}`;
const ASSET_CACHE = `${CACHE_PREFIX}-assets-${BUILD_HASH}`;

const PRECACHE_URLS = [
	'/',
	'/settings',
	'/settings/interface',
	'/manifest.json',
	'/static/favicon.png',
	'/static/favicon-dark.png',
	'/static/favicon-96x96.png',
	'/static/apple-touch-icon.png',
	'/static/web-app-manifest-192x192.png',
	'/static/web-app-manifest-512x512.png',
	'/static/web-app-manifest-maskable-192x192.png',
	'/static/web-app-manifest-maskable-512x512.png'
];

const EXCLUDED_PREFIXES = [
	'/api',
	'/ws',
	'/cache',
	'/openai',
	'/ollama',
	'/gemini',
	'/grok',
	'/anthropic'
];

const isCacheableAsset = (pathname) =>
	pathname.startsWith('/_app/') ||
	pathname.startsWith('/assets/') ||
	pathname.startsWith('/static/');

const shouldBypass = (request, url) => {
	if (request.method !== 'GET') {
		return true;
	}

	if (url.origin !== self.location.origin) {
		return true;
	}

	return EXCLUDED_PREFIXES.some((prefix) => url.pathname.startsWith(prefix));
};

const precacheUrl = async (cache, url) => {
	try {
		const response = await fetch(url, { cache: 'no-store' });
		if (response.ok) {
			await cache.put(url, response.clone());
		}
	} catch (error) {
		// Ignore individual precache failures so one missing asset doesn't block install.
	}
};

self.addEventListener('install', (event) => {
	event.waitUntil(
		(async () => {
			const cache = await caches.open(SHELL_CACHE);
			await Promise.allSettled(PRECACHE_URLS.map((url) => precacheUrl(cache, url)));
			await self.skipWaiting();
		})()
	);
});

self.addEventListener('activate', (event) => {
	event.waitUntil(
		(async () => {
			const cacheNames = await caches.keys();
			await Promise.all(
				cacheNames.map((name) => {
					if (name.startsWith(CACHE_PREFIX) && name !== SHELL_CACHE && name !== ASSET_CACHE) {
						return caches.delete(name);
					}
					return Promise.resolve(false);
				})
			);
			await self.clients.claim();
		})()
	);
});

const handleNavigationRequest = async (request) => {
	try {
		const response = await fetch(request);
		const cache = await caches.open(SHELL_CACHE);
		if (response.ok) {
			await cache.put(request, response.clone());
		}
		return response;
	} catch (error) {
		const cache = await caches.open(SHELL_CACHE);
		return (
			(await cache.match(request, { ignoreSearch: true })) ||
			(await cache.match('/')) ||
			(await cache.match('/settings'))
		);
	}
};

const handleAssetRequest = async (request) => {
	const cache = await caches.open(ASSET_CACHE);
	const cached = await cache.match(request);
	if (cached) {
		return cached;
	}

	const response = await fetch(request);
	if (response.ok) {
		await cache.put(request, response.clone());
	}
	return response;
};

const handleManifestRequest = async (request) => {
	try {
		const response = await fetch(request, { cache: 'no-store' });
		const cache = await caches.open(SHELL_CACHE);
		if (response.ok) {
			await cache.put(request, response.clone());
		}
		return response;
	} catch (error) {
		const cache = await caches.open(SHELL_CACHE);
		return (await cache.match(request)) || (await cache.match('/manifest.json'));
	}
};

self.addEventListener('fetch', (event) => {
	const { request } = event;
	const url = new URL(request.url);

	if (shouldBypass(request, url)) {
		return;
	}

	if (request.mode === 'navigate') {
		event.respondWith(handleNavigationRequest(request));
		return;
	}

	if (url.pathname === '/manifest.json') {
		event.respondWith(handleManifestRequest(request));
		return;
	}

	if (isCacheableAsset(url.pathname)) {
		event.respondWith(handleAssetRequest(request));
	}
});
