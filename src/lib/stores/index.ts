import { APP_NAME } from '$lib/constants';
import { type Writable, writable } from 'svelte/store';
import type { ModelConfig } from '$lib/apis';
import type { Banner } from '$lib/types';
import type { Socket } from 'socket.io-client';

import emojiShortCodes from '$lib/emoji-shortcodes.json';

// Backend
export const WEBUI_NAME = writable(APP_NAME);
export const config: Writable<Config | undefined> = writable(undefined);
export const user: Writable<SessionUser | undefined> = writable(undefined);

// Electron App
export const isApp = writable(false);
export const appInfo = writable(null);
export const appData = writable(null);

// Frontend
export const MODEL_DOWNLOAD_POOL = writable({});

export const mobile = writable(false);

export const socket: Writable<null | Socket> = writable(null);
export const activeUserIds: Writable<null | string[]> = writable(null);
export const USAGE_POOL: Writable<null | string[]> = writable(null);

export const theme = writable('system');

export const shortCodesToEmojis = writable(
	Object.entries(emojiShortCodes).reduce((acc, [key, value]) => {
		if (typeof value === 'string') {
			acc[value] = key;
		} else {
			for (const v of value) {
				acc[v] = key;
			}
		}

		return acc;
	}, {})
);

export const TTSWorker = writable(null);

// Global audio queue: prevents overlapping TTS playback across messages.
// Only one message's TTS plays at a time.
export const activeAudioId = writable<string | null>(null);

export const chatId = writable('');
export const chatTitle = writable('');

// Chat IDs currently generating responses (for sidebar activity indicators)
export const activeChatIds: Writable<Set<string>> = writable(new Set());

export const channels = writable([]);
export const chats = writable(null);
export const pinnedChats = writable([]);
export const tags = writable([]);

export const models: Writable<Model[]> = writable([]);
export const modelsStatus: Writable<'idle' | 'loading' | 'ready' | 'error'> = writable('idle');
export const modelsError: Writable<string | null> = writable(null);

export const prompts: Writable<null | Prompt[]> = writable(null);
export const skills: Writable<any[]> = writable([]);
export const knowledge: Writable<null | Document[]> = writable(null);
export const tools = writable(null);
export const functions = writable(null);

export const toolServers = writable([]);

export const banners: Writable<Banner[]> = writable([]);

export const settings: Writable<Settings> = writable({});

// Cached configs to avoid repeated API calls
export const ollamaConfigCache = writable(null);
export const openaiConfigCache = writable(null);
export const geminiConfigCache = writable(null);
export const anthropicConfigCache = writable(null);
export const connectionsConfigCache = writable(null);

export const showSidebar = writable(false);
export const showArchivedChats = writable(false);
export const showChangelog = writable(false);

export const showControls = writable(false);
export const showOverview = writable(false);
export const showArtifacts = writable(false);
export const showCallOverlay = writable(false);
export const artifactPreviewTarget: Writable<
	{ messageId?: string; type?: 'svg' | 'iframe'; content?: string } | null
> = writable(null);

export const temporaryChatEnabled = writable(false);
export const scrollPaginationEnabled = writable(false);
export const currentChatPage = writable(1);

export const isLastActiveTab = writable(true);
export const playingNotificationSound = writable(false);

export type Model = OpenAIModel | OllamaModel;

export type NativeWebSearchSupport = {
	status: 'supported' | 'unknown' | 'unsupported';
	reason?: string;
	source?: string;
	provider?: string;
	official?: boolean;
	configured?: boolean | null;
	supported?: boolean;
	can_attempt?: boolean;
	connection_name?: string;
};

type BaseModel = {
	id: string;
	name: string;
	info?: ModelConfig;
	owned_by: 'ollama' | 'openai' | 'google' | 'anthropic';
	native_web_search_supported?: boolean;
	native_web_search_support?: NativeWebSearchSupport;
};

export interface OpenAIModel extends BaseModel {
	owned_by: 'openai';
	external: boolean;
	source?: string;
}

export interface OllamaModel extends BaseModel {
	owned_by: 'ollama';
	details: OllamaModelDetails;
	size: number;
	description: string;
	model: string;
	modified_at: string;
	digest: string;
	ollama?: {
		name?: string;
		model?: string;
		modified_at: string;
		size?: number;
		digest?: string;
		details?: {
			parent_model?: string;
			format?: string;
			family?: string;
			families?: string[];
			parameter_size?: string;
			quantization_level?: string;
		};
		urls?: number[];
	};
}

type OllamaModelDetails = {
	parent_model: string;
	format: string;
	family: string;
	families: string[] | null;
	parameter_size: string;
	quantization_level: string;
};

type Settings = {
	models?: string[];
	backgroundImageUrl?: string | null;
	conversationMode?: boolean;
	speechAutoSend?: boolean;
	responseAutoPlayback?: boolean;
	responseAutoCopy?: boolean;
	audio?: AudioSettings;
	showUsername?: boolean;
	highContrastMode?: boolean;
	showChatTitleInTab?: boolean;
	notificationEnabled?: boolean;
	notificationSound?: boolean;
	notificationSoundAlways?: boolean;
	title?: TitleSettings;
	autoTags?: boolean;
	autoFollowUps?: boolean;
	detectArtifacts?: boolean;
	svgPreviewAutoOpen?: boolean;
	splitLargeDeltas?: boolean;
	chatDirection?: 'LTR' | 'RTL' | 'auto';
	landingPageMode?: string;
	chatBubble?: boolean;
	widescreenMode?: boolean;
	showUpdateToast?: boolean;
	showChangelog?: boolean;
	showEmojiInCall?: boolean;
	voiceInterruption?: boolean;
	hapticFeedback?: boolean;
	textScale?: number | null;
	highlighterTheme?: string;
	enableMessageQueue?: boolean;
	mermaidTheme?: string;
	temporaryChatByDefault?: boolean;
	chatFadeStreamingText?: boolean;
	insertSuggestionPrompt?: boolean;
	keepFollowUpPrompts?: boolean;
	insertFollowUpPrompt?: boolean;
	regenerateMenu?: boolean;
	collapseCodeBlocks?: boolean;
	expandDetails?: boolean;
	renderMarkdownInPreviews?: boolean;
	displayMultiModelResponsesInTabs?: boolean;
	scrollOnBranchChange?: boolean;
	stylizedPdfExport?: boolean;
	showFloatingActionButtons?: boolean;
	floatingActionButtons?:
		| {
				id: string;
				label: string;
				input: boolean;
				prompt: string;
		  }[]
		| null;
	richTextInput?: boolean;
	promptAutocomplete?: boolean;
	showFormattingToolbar?: boolean;
	insertPromptAsRichText?: boolean;
	ctrlEnterToSend?: boolean;
	copyFormatted?: boolean;
	largeTextAsFile?: boolean;
	webSearch?: null | 'always';
	webSearchMode?: 'off' | 'halo' | 'native' | 'auto';
	userLocation?: boolean;
	iframeSandboxAllowSameOrigin?: boolean;
	iframeSandboxAllowForms?: boolean;
	imageCompression?: boolean;
	imageCompressionSize?: {
		width: string;
		height: string;
	};
	imageCompressionInChannels?: boolean;

	system?: string;
	requestFormat?: string;
	keepAlive?: string;
	seed?: number;
	temperature?: string;
	repeat_penalty?: string;
	top_k?: string;
	top_p?: string;
	num_ctx?: string;
	num_batch?: string;
	num_keep?: string;
	options?: ModelOptions;
};

type ModelOptions = {
	stop?: boolean;
};

type AudioSettings = {
	STTEngine?: string;
	TTSEngine?: string;
	speaker?: string;
	model?: string;
	nonLocalVoices?: boolean;
};

type TitleSettings = {
	auto?: boolean;
	model?: string;
	modelExternal?: string;
	prompt?: string;
};

type Prompt = {
	command: string;
	user_id: string;
	title: string;
	content: string;
	timestamp: number;
};

type Document = {
	collection_name: string;
	filename: string;
	name: string;
	title: string;
};

type Config = {
	status: boolean;
	name: string;
	version: string;
	default_locale: string;
	default_models: string;
	default_prompt_suggestions: PromptSuggestion[];
	features: {
		auth: boolean;
		auth_trusted_header: boolean;
		enable_api_key: boolean;
		enable_signup: boolean;
		enable_login_form: boolean;
		enable_web_search?: boolean;
		enable_halo_web_search?: boolean;
		enable_native_web_search?: boolean;
		default_web_search_mode?: 'off' | 'halo' | 'native' | 'auto';
		enable_google_drive_integration: boolean;
		enable_onedrive_integration: boolean;
		enable_image_generation: boolean;
		enable_admin_export: boolean;
		enable_admin_chat_access: boolean;
		enable_community_sharing: boolean;
		enable_autocomplete_generation: boolean;
	};
	oauth: {
		providers: {
			[key: string]: string;
		};
	};
};

type PromptSuggestion = {
	content: string;
	title: [string, string];
};

type SessionUser = {
	id: string;
	email: string;
	name: string;
	role: string;
	profile_image_url: string;
};
