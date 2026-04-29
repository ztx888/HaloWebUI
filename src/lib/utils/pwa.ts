import { WEBUI_BUILD_HASH } from '$lib/constants';
import { writable } from 'svelte/store';

type PWAPlatform = 'android' | 'ios' | 'desktop' | 'unknown';
type PWABrowser = 'chrome' | 'samsung' | 'safari' | 'other';
type PWAManualHint = 'none' | 'android' | 'ios' | 'generic';

interface BeforeInstallPromptEvent extends Event {
	prompt: () => Promise<void>;
	userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

export type PWAInstallState = {
	installed: boolean;
	canInstall: boolean;
	serviceWorkerRegistered: boolean;
	platform: PWAPlatform;
	browser: PWABrowser;
	manualHint: PWAManualHint;
};

const DEFAULT_STATE: PWAInstallState = {
	installed: false,
	canInstall: false,
	serviceWorkerRegistered: false,
	platform: 'unknown',
	browser: 'other',
	manualHint: 'generic'
};

export const pwaInstallState = writable<PWAInstallState>(DEFAULT_STATE);

let deferredInstallPrompt: BeforeInstallPromptEvent | null = null;
let initialized = false;

const detectPlatform = (): PWAPlatform => {
	if (typeof navigator === 'undefined') {
		return 'unknown';
	}

	const ua = navigator.userAgent.toLowerCase();
	const isIos =
		/iphone|ipad|ipod/.test(ua) ||
		(navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
	if (isIos) {
		return 'ios';
	}

	if (/android/.test(ua)) {
		return 'android';
	}

	if (/macintosh|windows|linux|cros/.test(ua)) {
		return 'desktop';
	}

	return 'unknown';
};

const detectBrowser = (): PWABrowser => {
	if (typeof navigator === 'undefined') {
		return 'other';
	}

	const ua = navigator.userAgent;
	if (/SamsungBrowser/i.test(ua)) {
		return 'samsung';
	}

	if (/CriOS|Chrome/i.test(ua) && !/Edg|OPR|SamsungBrowser/i.test(ua)) {
		return 'chrome';
	}

	if (/Safari/i.test(ua) && !/Chrome|CriOS|Edg|OPR|SamsungBrowser/i.test(ua)) {
		return 'safari';
	}

	return 'other';
};

const isStandalone = (): boolean => {
	if (typeof window === 'undefined') {
		return false;
	}

	return (
		window.matchMedia('(display-mode: standalone)').matches ||
		(window.navigator as Navigator & { standalone?: boolean }).standalone === true
	);
};

const computeManualHint = (installed: boolean, canInstall: boolean, platform: PWAPlatform) => {
	if (installed || canInstall) {
		return 'none';
	}

	if (platform === 'ios') {
		return 'ios';
	}

	if (platform === 'android') {
		return 'android';
	}

	return 'generic';
};

const syncPWAState = (partial: Partial<PWAInstallState> = {}) => {
	pwaInstallState.update((current) => {
		const platform = detectPlatform();
		const installed = partial.installed ?? isStandalone();
		const canInstall = partial.canInstall ?? Boolean(deferredInstallPrompt);

		return {
			installed,
			canInstall,
			serviceWorkerRegistered:
				partial.serviceWorkerRegistered ?? current.serviceWorkerRegistered ?? false,
			platform,
			browser: detectBrowser(),
			manualHint: computeManualHint(installed, canInstall, platform)
		};
	});
};

const registerServiceWorker = async () => {
	if (typeof window === 'undefined' || !('serviceWorker' in navigator)) {
		syncPWAState({ serviceWorkerRegistered: false });
		return;
	}

	const isLocalHttp = ['localhost', '127.0.0.1'].includes(window.location.hostname);
	const isSecureContextForPWA = window.location.protocol === 'https:' || isLocalHttp;
	if (!isSecureContextForPWA) {
		syncPWAState({ serviceWorkerRegistered: false });
		return;
	}

	try {
		const registration = await navigator.serviceWorker.register(
			`/sw.js?build=${encodeURIComponent(WEBUI_BUILD_HASH)}`,
			{ scope: '/' }
		);
		syncPWAState({ serviceWorkerRegistered: true });
		void registration.update().catch(() => {});
	} catch (error) {
		console.error('Failed to register Halo PWA service worker.', error);
		syncPWAState({ serviceWorkerRegistered: false });
	}
};

export const initPWAInstallSupport = async () => {
	if (typeof window === 'undefined') {
		return () => {};
	}

	if (initialized) {
		syncPWAState();
		return () => {};
	}

	initialized = true;

	const displayModeMedia = window.matchMedia('(display-mode: standalone)');
	const handleDisplayModeChange = () => syncPWAState();
	const handleBeforeInstallPrompt = (event: Event) => {
		const promptEvent = event as BeforeInstallPromptEvent;
		promptEvent.preventDefault();
		deferredInstallPrompt = promptEvent;
		syncPWAState({ canInstall: true });
	};
	const handleAppInstalled = () => {
		deferredInstallPrompt = null;
		syncPWAState({ installed: true, canInstall: false });
	};

	if (typeof displayModeMedia.addEventListener === 'function') {
		displayModeMedia.addEventListener('change', handleDisplayModeChange);
	} else if (typeof displayModeMedia.addListener === 'function') {
		displayModeMedia.addListener(handleDisplayModeChange);
	}

	window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt as EventListener);
	window.addEventListener('appinstalled', handleAppInstalled);

	syncPWAState();
	await registerServiceWorker();

	return () => {
		if (typeof displayModeMedia.removeEventListener === 'function') {
			displayModeMedia.removeEventListener('change', handleDisplayModeChange);
		} else if (typeof displayModeMedia.removeListener === 'function') {
			displayModeMedia.removeListener(handleDisplayModeChange);
		}

		window.removeEventListener(
			'beforeinstallprompt',
			handleBeforeInstallPrompt as EventListener
		);
		window.removeEventListener('appinstalled', handleAppInstalled);
		initialized = false;
	};
};

export const promptPWAInstall = async (): Promise<boolean> => {
	if (!deferredInstallPrompt) {
		syncPWAState();
		return false;
	}

	try {
		await deferredInstallPrompt.prompt();
		const choice = await deferredInstallPrompt.userChoice;
		const accepted = choice?.outcome === 'accepted';
		deferredInstallPrompt = null;
		syncPWAState({
			canInstall: false,
			...(accepted ? { installed: true } : {})
		});
		return accepted;
	} catch (error) {
		console.error('Failed to show Halo PWA install prompt.', error);
		deferredInstallPrompt = null;
		syncPWAState({ canInstall: false });
		return false;
	}
};
