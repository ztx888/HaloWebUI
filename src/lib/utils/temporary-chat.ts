const TEMPORARY_CHAT_OVERRIDE_KEY = 'temporary-chat-override';

type TemporaryChatOptions = {
	defaultEnabled?: boolean;
	enforced?: boolean;
	allowed?: boolean;
};

type TemporaryChatUser = {
	role?: string | null;
	permissions?: {
		chat?: {
			temporary?: unknown;
			temporary_enforced?: unknown;
		} | null;
	} | null;
} | null | undefined;

type TemporaryChatAccess = {
	allowed: boolean;
	enforced: boolean;
};

type TemporaryChatNavigationOptions = TemporaryChatOptions & {
	currentUrl: URL;
	enabled: boolean;
	pathname?: string;
};

const parseTemporaryChatValue = (value: string | null | undefined): boolean | null => {
	if (value === 'true') {
		return true;
	}

	if (value === 'false') {
		return false;
	}

	return null;
};

const parseBooleanLike = (value: unknown): boolean | null => {
	if (typeof value === 'boolean') {
		return value;
	}

	if (typeof value === 'string') {
		return parseTemporaryChatValue(value);
	}

	return null;
};

export const getTemporaryChatAccess = (user: TemporaryChatUser): TemporaryChatAccess => {
	// Admins should always be able to review saved chats and opt in/out of temporary mode.
	if (user?.role === 'admin') {
		return {
			allowed: true,
			enforced: false
		};
	}

	const allowed =
		user?.role === 'user'
			? (parseBooleanLike(user?.permissions?.chat?.temporary) ?? true)
			: true;

	return {
		allowed,
		enforced: allowed && (parseBooleanLike(user?.permissions?.chat?.temporary_enforced) ?? false)
	};
};

export const resolveTemporaryChatEnabled = ({
	searchParams,
	defaultEnabled = false,
	enforced = false,
	allowed = true
}: TemporaryChatOptions & {
	searchParams?: URLSearchParams;
}) => {
	if (!allowed) {
		return false;
	}

	if (enforced) {
		return true;
	}

	const urlValue = parseTemporaryChatValue(searchParams?.get('temporary-chat'));
	if (urlValue !== null) {
		return urlValue;
	}

	if (typeof sessionStorage !== 'undefined') {
		const overrideValue = parseTemporaryChatValue(
			sessionStorage.getItem(TEMPORARY_CHAT_OVERRIDE_KEY)
		);
		if (overrideValue !== null) {
			return overrideValue;
		}
	}

	return defaultEnabled;
};

export const persistTemporaryChatOverride = (
	enabled: boolean,
	{ defaultEnabled = false, enforced = false, allowed = true }: TemporaryChatOptions = {}
) => {
	if (typeof sessionStorage === 'undefined') {
		return;
	}

	if (!allowed || enforced || enabled === defaultEnabled) {
		sessionStorage.removeItem(TEMPORARY_CHAT_OVERRIDE_KEY);
		return;
	}

	sessionStorage.setItem(TEMPORARY_CHAT_OVERRIDE_KEY, String(enabled));
};

export const getTemporaryChatNavigationPath = ({
	currentUrl,
	enabled,
	defaultEnabled = false,
	enforced = false,
	allowed = true,
	pathname
}: TemporaryChatNavigationOptions) => {
	const nextUrl = new URL(currentUrl.toString());

	if (pathname) {
		nextUrl.pathname = pathname;
	}

	nextUrl.searchParams.delete('temporary-chat');

	if (allowed && !enforced && enabled !== defaultEnabled) {
		nextUrl.searchParams.set('temporary-chat', String(enabled));
	}

	return `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`;
};
