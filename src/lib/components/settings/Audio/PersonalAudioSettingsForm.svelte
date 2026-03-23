<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import { user, settings, config, models } from '$lib/stores';
	import { getVoices as _getVoices } from '$lib/apis/audio';
	import {
		approveKokoroConsent,
		hasKokoroConsent,
		KOKORO_MODEL_DOWNLOAD_MB
	} from '$lib/utils/browser-ai-assets';
	import { revealExpandedSection } from '$lib/utils/expanded-section-scroll';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';
	const dispatch = createEventDispatcher();

	// Personal audio preferences form rendered inside /settings/audio.
	const i18n = getContext('i18n');

	export let saveSettings: Function;
	export let embedded = false;
	export let showSubmit = true;
	export let showScopeBadges = false;
	export let scopeLabel = '';
	export let scopeBadgeClass = 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300';
	export let visualVariant: 'default' | 'document-card' | 'flat' = 'default';
	export let defaultExpandedSections = { stt: true, tts: true, voice: true };

	// Audio
	let speechAutoSend = false;
	let responseAutoPlayback = false;
	let nonLocalVoices = false;

	let STTEngine = '';
	let STTLanguage = '';
	let sttLanguageOptions = [];

	// Common STT language options (BCP-47 codes)
	$: sttLanguageOptions = [
		{ value: '', label: $i18n.t('Auto-detect') },
		{ value: 'en', label: $i18n.t('English') },
		{ value: 'zh', label: $i18n.t('中文 (Chinese)') },
		{ value: 'ja', label: $i18n.t('日本語 (Japanese)') },
		{ value: 'ko', label: $i18n.t('한국어 (Korean)') },
		{ value: 'es', label: $i18n.t('Español (Spanish)') },
		{ value: 'fr', label: $i18n.t('Français (French)') },
		{ value: 'de', label: $i18n.t('Deutsch (German)') },
		{ value: 'pt', label: $i18n.t('Português (Portuguese)') },
		{ value: 'ru', label: $i18n.t('Русский (Russian)') },
		{ value: 'ar', label: $i18n.t('العربية (Arabic)') },
		{ value: 'hi', label: $i18n.t('हिन्दी (Hindi)') },
		{ value: 'it', label: $i18n.t('Italiano (Italian)') },
		{ value: 'nl', label: $i18n.t('Nederlands (Dutch)') },
		{ value: 'pl', label: $i18n.t('Polski (Polish)') },
		{ value: 'tr', label: $i18n.t('Türkçe (Turkish)') },
		{ value: 'vi', label: $i18n.t('Tiếng Việt (Vietnamese)') },
		{ value: 'th', label: $i18n.t('ไทย (Thai)') },
		{
			value: 'uk',
			label: $i18n.t('Українська (Ukrainian)')
		},
		{ value: 'sv', label: $i18n.t('Svenska (Swedish)') }
	];

	let TTSEngine = '';
	let TTSEngineConfig = {};

	let TTSModel = null;
	let TTSModelProgress = null;
	let TTSModelLoading = false;

	let voices = [];
	let voice = '';

	// Per-model TTS voice overrides: { modelId: voiceName }
	let modelVoices: Record<string, string> = {};

	// Audio speed control
	let playbackRate: string | number = '1';
	const speedOptions = [2, 1.75, 1.5, 1.25, 1, 0.75, 0.5];
	let canUseSTT = false;
	let canUseTTS = false;
	let expandedSections = { stt: true, tts: true, voice: true };
	let sectionEl_stt: HTMLElement;
	let sectionEl_tts: HTMLElement;
	let sectionEl_voice: HTMLElement;
	let initialSnapshot = null;
	let KokoroTTSClass = null;
	let kokoroConsentAccepted = false;
	let lastNonKokoroEngine = '';

	export let isDirty = false;
	let lastDirtyState: boolean | null = null;

	$: isDocumentCard = visualVariant === 'document-card';
	$: isFlat = visualVariant === 'flat';

	const toggleSection = async (section: 'stt' | 'tts' | 'voice') => {
		expandedSections[section] = !expandedSections[section];
		if (expandedSections[section]) {
			const el = { stt: sectionEl_stt, tts: sectionEl_tts, voice: sectionEl_voice }[section];
			await revealExpandedSection(el);
		}
	};

	$: canUseSTT = $user?.role === 'admin' || ($user?.permissions?.chat?.stt ?? true);
	$: canUseTTS = $user?.role === 'admin' || ($user?.permissions?.chat?.tts ?? true);

	const getVoices = async () => {
		if (TTSEngine === 'browser-kokoro') {
			if (!kokoroConsentAccepted) {
				voices = [];
				return;
			}
			if (!TTSModel) {
				await loadKokoro();
			}

			voices = Object.entries(TTSModel.voices).map(([key, value]) => {
				return {
					id: key,
					name: value.name,
					localService: false
				};
			});
		} else {
			if ($config.audio.tts.engine === '') {
				const getVoicesLoop = setInterval(async () => {
					voices = await speechSynthesis.getVoices();

					// do your loop
					if (voices.length > 0) {
						clearInterval(getVoicesLoop);
					}
				}, 100);
			} else {
				const res = await _getVoices(localStorage.token).catch((e) => {
					toast.error(`${e}`);
				});

				if (res) {
					console.log(res);
					voices = res.voices;
				}
			}
		}
	};

	const toggleResponseAutoPlayback = async () => {
		responseAutoPlayback = !responseAutoPlayback;
	};

	const toggleSpeechAutoSend = async () => {
		speechAutoSend = !speechAutoSend;
	};

	const buildUserPatch = () => {
		const patch: Record<string, any> = {};

		if (canUseSTT) {
			patch.audio = {
				...(patch.audio ?? {}),
				stt: {
					engine: STTEngine !== '' ? STTEngine : undefined,
					language: STTLanguage !== '' ? STTLanguage : undefined
				}
			};
			patch.speechAutoSend = speechAutoSend;
		}

		if (canUseTTS) {
			// Clean modelVoices: remove empty values
			const cleanedModelVoices: Record<string, string> = {};
			for (const [k, v] of Object.entries(modelVoices)) {
				if (v && v.trim()) cleanedModelVoices[k] = v;
			}

			patch.audio = {
				...(patch.audio ?? {}),
				tts: {
					engine: TTSEngine !== '' ? TTSEngine : undefined,
					engineConfig: TTSEngineConfig,
					playbackRate: normalizePlaybackRate(playbackRate),
					voice: voice !== '' ? voice : undefined,
					defaultVoice: $config?.audio?.tts?.voice ?? '',
					nonLocalVoices: $config.audio.tts.engine === '' ? nonLocalVoices : undefined,
					modelVoices: Object.keys(cleanedModelVoices).length > 0 ? cleanedModelVoices : undefined
				}
			};
			patch.responseAutoPlayback = responseAutoPlayback;
		}

		return patch;
	};

	const normalizePlaybackRate = (value: string | number | null | undefined) => {
		const parsed = Number(value);
		return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
	};

	const buildSnapshot = (
		currentCanUseSTT: boolean,
		currentCanUseTTS: boolean,
		currentSTTEngine: string,
		currentSTTLanguage: string,
		currentSpeechAutoSend: boolean,
		currentTTSEngine: string,
		currentTTSEngineConfig: Record<string, any>,
		currentResponseAutoPlayback: boolean,
		currentPlaybackRate: string | number,
		currentVoice: string,
		currentNonLocalVoices: boolean,
		currentModelVoices: Record<string, string>
	) => ({
		stt: currentCanUseSTT
			? {
					STTEngine: currentSTTEngine,
					STTLanguage: currentSTTLanguage,
					speechAutoSend: currentSpeechAutoSend
				}
			: null,
		tts: currentCanUseTTS
			? {
					TTSEngine: currentTTSEngine,
					TTSEngineConfig: currentTTSEngineConfig,
					responseAutoPlayback: currentResponseAutoPlayback,
					playbackRate: normalizePlaybackRate(currentPlaybackRate)
				}
			: null,
		voice: currentCanUseTTS
			? {
					voice: currentVoice,
					nonLocalVoices: currentNonLocalVoices,
					modelVoices: currentModelVoices
				}
			: null
	});

	// Audio lists load asynchronously; keep dirty tracking tied to real form values, not timing windows.
	const syncBaseline = () => {
		initialSnapshot = cloneSettingsSnapshot(
			buildSnapshot(
				canUseSTT,
				canUseTTS,
				STTEngine,
				STTLanguage,
				speechAutoSend,
				TTSEngine,
				TTSEngineConfig,
				responseAutoPlayback,
				playbackRate,
				voice,
				nonLocalVoices,
				modelVoices
			)
		);
	};

	const saveUserAudioSettings = async () => {
		if (TTSEngine === 'browser-kokoro' && !kokoroConsentAccepted) {
			toast.error('请先确认下载浏览器语音模型后再启用 Kokoro.js。');
			return;
		}

		const patch = buildUserPatch();

		if (Object.keys(patch).length > 0) {
			await saveSettings(patch);
		}

		await tick();
		syncBaseline();
		dispatch('save');
	};

	export const save = async () => {
		await saveUserAudioSettings();
	};

	let snapshot = {
		stt: null,
		tts: null,
		voice: null
	};
	$: snapshot = buildSnapshot(
		canUseSTT,
		canUseTTS,
		STTEngine,
		STTLanguage,
		speechAutoSend,
		TTSEngine,
		TTSEngineConfig,
		responseAutoPlayback,
		playbackRate,
		voice,
		nonLocalVoices,
		modelVoices
	);
	$: isDirty = !!(initialSnapshot && !isSettingsSnapshotEqual(snapshot, initialSnapshot));
	$: if (lastDirtyState !== isDirty) {
		lastDirtyState = isDirty;
		dispatch('dirtyChange', { value: isDirty });
	}

	const onSubmitHandler = async () => {
		if (!showSubmit) {
			return;
		}

		await saveUserAudioSettings();
	};

	onMount(async () => {
		kokoroConsentAccepted = hasKokoroConsent();
		expandedSections = {
			stt: defaultExpandedSections?.stt ?? true,
			tts: defaultExpandedSections?.tts ?? true,
			voice: defaultExpandedSections?.voice ?? true
		};

		playbackRate = String($settings.audio?.tts?.playbackRate ?? 1);
		speechAutoSend = $settings.speechAutoSend ?? false;
		responseAutoPlayback = $settings.responseAutoPlayback ?? false;

		STTEngine = $settings?.audio?.stt?.engine ?? '';
		STTLanguage = $settings?.audio?.stt?.language ?? '';

		TTSEngine = $settings?.audio?.tts?.engine ?? '';
		TTSEngineConfig = $settings?.audio?.tts?.engineConfig ?? {};

		if ($settings?.audio?.tts?.defaultVoice === $config.audio.tts.voice) {
			voice = $settings?.audio?.tts?.voice ?? $config.audio.tts.voice ?? '';
		} else {
			voice = $config.audio.tts.voice ?? '';
		}

		nonLocalVoices = $settings.audio?.tts?.nonLocalVoices ?? false;
		modelVoices = $settings?.audio?.tts?.modelVoices ?? {};

		syncBaseline();
		await getVoices();
		await tick();
	});

	$: if (TTSEngine !== 'browser-kokoro') {
		lastNonKokoroEngine = TTSEngine;
	}

	$: if (TTSEngine && TTSEngineConfig) {
		onTTSEngineChange();
	}

	const getKokoroTTS = async () => {
		if (!KokoroTTSClass) {
			KokoroTTSClass = (await import('kokoro-js')).KokoroTTS;
		}

		return KokoroTTSClass;
	};

	const onTTSEngineChange = async () => {
		if (TTSEngine === 'browser-kokoro') {
			if (!kokoroConsentAccepted) {
				return;
			}
			await loadKokoro();
		}
	};

	const loadKokoro = async () => {
		if (TTSEngine === 'browser-kokoro') {
			voices = [];

			if (TTSEngineConfig?.dtype) {
				TTSModel = null;
				TTSModelProgress = null;
				TTSModelLoading = true;

				const model_id = 'onnx-community/Kokoro-82M-v1.0-ONNX';
				const KokoroTTS = await getKokoroTTS();

				try {
					TTSModel = await KokoroTTS.from_pretrained(model_id, {
						dtype: TTSEngineConfig.dtype, // Options: "fp32", "fp16", "q8", "q4", "q4f16"
						device: !!navigator?.gpu ? 'webgpu' : 'wasm', // Detect WebGPU
						progress_callback: (e) => {
							TTSModelProgress = e;
							console.log(e);
						}
					});

					await getVoices();
				} catch (e) {
					toast.error(`${e}`);
				} finally {
					TTSModelLoading = false;
				}

				// const rawAudio = await tts.generate(inputText, {
				// 	// Use `tts.list_voices()` to list all available voices
				// 	voice: voice
				// });

				// const blobUrl = URL.createObjectURL(await rawAudio.toBlob());
				// const audio = new Audio(blobUrl);

				// audio.play();
			}
		}
	};

	export const reset = async () => {
		if (!initialSnapshot) return;

		const next = cloneSettingsSnapshot(initialSnapshot);

		if (next.stt) {
			STTEngine = next.stt.STTEngine;
			STTLanguage = next.stt.STTLanguage;
			speechAutoSend = next.stt.speechAutoSend;
		}

		if (next.tts) {
			TTSEngine = next.tts.TTSEngine;
			TTSEngineConfig = cloneSettingsSnapshot(next.tts.TTSEngineConfig);
			responseAutoPlayback = next.tts.responseAutoPlayback;
			playbackRate = String(next.tts.playbackRate);
		}

		if (next.voice) {
			voice = next.voice.voice;
			nonLocalVoices = next.voice.nonLocalVoices;
			modelVoices = cloneSettingsSnapshot(next.voice.modelVoices);
		}

		await getVoices();
	};
</script>

{#if isFlat}
	<!-- ====== Flat variant: glass-items directly, no wrapper cards/folding ====== -->
	<div class="space-y-3">
		{#if canUseSTT}
			<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
				{$i18n.t('STT Settings')}
			</div>

			{#if $config.audio.stt.engine !== 'web'}
				<div class="glass-item px-4 py-3">
					<div class="flex items-center justify-between">
						<div class="text-sm font-medium">{$i18n.t('Speech-to-Text Engine')}</div>
						<HaloSelect
							className="w-fit"
							bind:value={STTEngine}
							placeholder={$i18n.t('Select an engine')}
							options={[
								{ value: '', label: $i18n.t('Default') },
								{ value: 'web', label: $i18n.t('Web API') }
							]}
						/>
					</div>
				</div>
			{/if}

			<div class="glass-item px-4 py-3">
				<div class="flex items-center justify-between">
					<div class="text-sm font-medium">{$i18n.t('STT Language')}</div>
					<HaloSelect
						className="w-fit"
						bind:value={STTLanguage}
						options={sttLanguageOptions}
					/>
				</div>
			</div>

			<div class="flex items-center justify-between glass-item px-4 py-3">
				<div class="text-sm font-medium">
					{$i18n.t('Instant Auto-Send After Voice Transcription')}
				</div>
				<Switch bind:state={speechAutoSend} />
			</div>
		{/if}

		{#if canUseTTS}
			<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
				{$i18n.t('TTS Settings')}
			</div>

			<div class="glass-item px-4 py-3">
				<div class="flex items-center justify-between">
					<div class="text-sm font-medium">{$i18n.t('Text-to-Speech Engine')}</div>
					<HaloSelect
						className="w-fit"
						bind:value={TTSEngine}
						placeholder={$i18n.t('Select an engine')}
						options={[
							{ value: '', label: $i18n.t('Default') },
							{ value: 'browser-kokoro', label: $i18n.t('Kokoro.js (Browser)') }
						]}
					/>
				</div>
			</div>

			{#if TTSEngine === 'browser-kokoro' && !kokoroConsentAccepted}
				<div class="glass-item px-4 py-3 border border-amber-200/70 bg-amber-50/80 dark:border-amber-900/60 dark:bg-amber-950/20">
					<div class="text-sm font-medium text-amber-800 dark:text-amber-200">
						首次启用需要下载浏览器端语音模型
					</div>
					<div class="mt-1 text-xs leading-relaxed text-amber-700 dark:text-amber-300">
						将下载约 {KOKORO_MODEL_DOWNLOAD_MB} MB 模型资源，仅对当前浏览器生效，下载完成后会缓存，后续一般不需要重复下载。
					</div>
					<div class="mt-3 flex gap-2">
						<button
							class="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-amber-700"
							type="button"
							on:click={async () => {
								approveKokoroConsent();
								kokoroConsentAccepted = true;
								await loadKokoro();
							}}
						>
							下载并启用
						</button>
						<button
							class="rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 dark:bg-transparent dark:text-amber-200 dark:hover:bg-amber-900/30"
							type="button"
							on:click={() => {
								TTSEngine = lastNonKokoroEngine;
							}}
						>
							暂不
						</button>
					</div>
				</div>
			{/if}

			{#if TTSEngine === 'browser-kokoro' && kokoroConsentAccepted}
				<div class="glass-item px-4 py-3">
					<div class="flex items-center justify-between">
						<div class="text-sm font-medium">{$i18n.t('Kokoro.js Dtype')}</div>
						<HaloSelect
							className="w-fit"
							bind:value={TTSEngineConfig.dtype}
							placeholder="Select dtype"
							options={[
								{ value: 'fp32', label: 'fp32' },
								{ value: 'fp16', label: 'fp16' },
								{ value: 'q8', label: 'q8' },
								{ value: 'q4', label: 'q4' }
							]}
						/>
					</div>
				</div>
			{/if}

			<div class="flex items-center justify-between glass-item px-4 py-3">
				<div class="text-sm font-medium">{$i18n.t('Auto-playback response')}</div>
				<Switch bind:state={responseAutoPlayback} />
			</div>

			<div class="glass-item px-4 py-3">
				<div class="flex items-center justify-between">
					<div class="text-sm font-medium">{$i18n.t('Speech Playback Speed')}</div>
					<HaloSelect
						className="w-fit"
						bind:value={playbackRate}
						options={speedOptions.map((s) => ({ value: String(s), label: `${s}x` }))}
					/>
				</div>
			</div>

			<!-- Voice Selection -->
			<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
				{$i18n.t('Set Voice')}
			</div>

			{#if TTSEngine === 'browser-kokoro' && kokoroConsentAccepted}
				{#if TTSModel}
					<div class="glass-item p-4">
						<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
							{$i18n.t('Voice')}
						</div>
						<input
							list="voice-list"
							class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
							bind:value={voice}
							placeholder={$i18n.t('Select a voice')}
						/>
						<datalist id="voice-list">
							{#each voices as v}
								<option value={v.id}>{v.name}</option>
							{/each}
						</datalist>
					</div>
				{:else}
					<div class="glass-item p-4">
						<div class="flex gap-2 items-center mb-1.5">
							<Spinner className="size-4" />
							<div class="text-sm font-medium shimmer">
								{$i18n.t('Loading Kokoro.js...')}
								{TTSModelProgress && TTSModelProgress.status === 'progress'
									? `(${Math.round(TTSModelProgress.progress * 10) / 10}%)`
									: ''}
							</div>
						</div>
						<div class="text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('Please do not close the settings page while loading the model.')}
						</div>
					</div>
				{/if}
			{:else if $config.audio.tts.engine === ''}
				<div class="glass-item p-4">
					<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
						{$i18n.t('Voice')}
					</div>
					<div class="w-full max-w-[15rem]">
						<HaloSelect
							className="w-full"
							bind:value={voice}
							options={[
								{ value: '', label: $i18n.t('Default') },
								...voices
									.filter((v) => nonLocalVoices || v.localService === true)
									.map((v) => ({ value: v.name, label: v.name }))
							]}
						/>
					</div>
				</div>

				<div class="flex items-center justify-between glass-item px-4 py-3">
					<div class="text-sm font-medium">{$i18n.t('Allow non-local voices')}</div>
					<Switch bind:state={nonLocalVoices} />
				</div>
			{:else if $config.audio.tts.engine !== ''}
				<div class="glass-item p-4">
					<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
						{$i18n.t('Voice')}
					</div>
					<input
						list="voice-list"
						class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
						bind:value={voice}
						placeholder={$i18n.t('Select a voice')}
					/>
					<datalist id="voice-list">
						{#each voices as v}
							<option value={v.id}>{v.name}</option>
						{/each}
					</datalist>
				</div>
			{/if}
		{/if}
	</div>
{:else}
	<!-- ====== Default / Document-Card variant ====== -->
	<form
		class={`flex flex-col space-y-3 text-sm max-w-6xl mx-auto w-full ${embedded ? '' : 'h-full justify-between'}`}
		on:submit|preventDefault={onSubmitHandler}
	>
		<div class={`space-y-3 ${embedded ? '' : 'overflow-y-auto max-h-[28rem] lg:max-h-full'}`}>
			{#if canUseSTT}
				<div
					bind:this={sectionEl_stt}
					class={`scroll-mt-2 ${
						isDocumentCard
							? 'rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden'
							: ''
					}`}
				>
					{#if isDocumentCard}
						<button
							type="button"
							class="w-full flex items-center justify-between px-4 py-3 text-left"
							on:click={() => toggleSection('stt')}
						>
							<div class="mb-0 text-sm font-medium flex items-center gap-2">
								<span>{$i18n.t('STT Settings')}</span>
								{#if showScopeBadges && scopeLabel}
									<span class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
										>{scopeLabel}</span
									>
								{/if}
							</div>

							<div
								class="transform transition-transform duration-200 {expandedSections.stt
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-4 text-gray-400" />
							</div>
						</button>
					{/if}

					{#if !isDocumentCard || expandedSections.stt}
						<div
							class={isDocumentCard ? 'px-4 pb-4 border-t border-gray-100 dark:border-gray-800' : ''}
							transition:slide={{ duration: 180, easing: quintOut }}
						>
							{#if !isDocumentCard}
								<div class="mb-1 text-sm font-medium flex items-center gap-2">
									<span>{$i18n.t('STT Settings')}</span>
									{#if showScopeBadges && scopeLabel}
										<span
											class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
											>{scopeLabel}</span
										>
									{/if}
								</div>
							{/if}

							{#if $config.audio.stt.engine !== 'web'}
								<div class="flex w-full items-center justify-between gap-3 py-1">
									<div class="min-w-0 pr-3 text-xs font-medium">
										{$i18n.t('Speech-to-Text Engine')}
									</div>
									<div class="relative flex shrink-0 items-center max-w-full">
										<HaloSelect
											className="w-fit text-xs text-right"
											bind:value={STTEngine}
											placeholder={$i18n.t('Select an engine')}
											options={[
												{ value: '', label: $i18n.t('Default') },
												{ value: 'web', label: $i18n.t('Web API') }
											]}
										/>
									</div>
								</div>
							{/if}

							<div class="flex w-full items-center justify-between gap-3 py-1">
								<div class="min-w-0 pr-3 text-xs font-medium">{$i18n.t('STT Language')}</div>
								<div class="relative flex shrink-0 items-center max-w-full">
									<HaloSelect
										className="w-fit text-xs text-right"
										bind:value={STTLanguage}
										options={sttLanguageOptions}
									/>
								</div>
							</div>

							<div class="py-0.5 flex w-full justify-between">
								<div class="self-center text-xs font-medium">
									{$i18n.t('Instant Auto-Send After Voice Transcription')}
								</div>

								<button
									class="p-1 px-3 text-xs flex rounded-sm transition"
									on:click={() => {
										toggleSpeechAutoSend();
									}}
									type="button"
								>
									{#if speechAutoSend === true}
										<span class="ml-2 self-center">{$i18n.t('On')}</span>
									{:else}
										<span class="ml-2 self-center">{$i18n.t('Off')}</span>
									{/if}
								</button>
							</div>
						</div>
					{/if}
				</div>
			{/if}

			{#if canUseTTS}
				<div
					bind:this={sectionEl_tts}
					class={`scroll-mt-2 ${
						isDocumentCard
							? 'rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden'
							: ''
					}`}
				>
					{#if isDocumentCard}
						<button
							type="button"
							class="w-full flex items-center justify-between px-4 py-3 text-left"
							on:click={() => toggleSection('tts')}
						>
							<div class="mb-0 text-sm font-medium flex items-center gap-2">
								<span>{$i18n.t('TTS Settings')}</span>
								{#if showScopeBadges && scopeLabel}
									<span class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
										>{scopeLabel}</span
									>
								{/if}
							</div>

							<div
								class="transform transition-transform duration-200 {expandedSections.tts
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-4 text-gray-400" />
							</div>
						</button>
					{/if}

					{#if !isDocumentCard || expandedSections.tts}
						<div
							class={isDocumentCard ? 'px-4 pb-4 border-t border-gray-100 dark:border-gray-800' : ''}
							transition:slide={{ duration: 180, easing: quintOut }}
						>
							{#if !isDocumentCard}
								<div class="mb-1 text-sm font-medium flex items-center gap-2">
									<span>{$i18n.t('TTS Settings')}</span>
									{#if showScopeBadges && scopeLabel}
										<span
											class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
											>{scopeLabel}</span
										>
									{/if}
								</div>
							{/if}

							<div class="flex w-full items-center justify-between gap-3 py-1">
								<div class="min-w-0 pr-3 text-xs font-medium">{$i18n.t('Text-to-Speech Engine')}</div>

								<div class="relative flex shrink-0 items-center max-w-full">
									<HaloSelect
										className="w-fit text-xs text-right"
										bind:value={TTSEngine}
										placeholder={$i18n.t('Select an engine')}
										options={[
											{ value: '', label: $i18n.t('Default') },
											{ value: 'browser-kokoro', label: $i18n.t('Kokoro.js (Browser)') }
										]}
									/>
								</div>
							</div>

							{#if TTSEngine === 'browser-kokoro' && !kokoroConsentAccepted}
								<div class="rounded-xl border border-amber-200/70 bg-amber-50/80 px-3 py-3 text-xs leading-relaxed text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/20 dark:text-amber-300">
									<div class="font-medium text-amber-800 dark:text-amber-200">
										首次启用需要下载浏览器端语音模型
									</div>
									<div class="mt-1">
										将下载约 {KOKORO_MODEL_DOWNLOAD_MB} MB 模型资源，仅对当前浏览器生效，下载完成后会缓存，后续一般不需要重复下载。
									</div>
									<div class="mt-3 flex gap-2">
										<button
											class="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-amber-700"
											type="button"
											on:click={async () => {
												approveKokoroConsent();
												kokoroConsentAccepted = true;
												await loadKokoro();
											}}
										>
											下载并启用
										</button>
										<button
											class="rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 dark:bg-transparent dark:text-amber-200 dark:hover:bg-amber-900/30"
											type="button"
											on:click={() => {
												TTSEngine = lastNonKokoroEngine;
											}}
										>
											暂不
										</button>
									</div>
								</div>
							{/if}

							{#if TTSEngine === 'browser-kokoro' && kokoroConsentAccepted}
								<div class="flex w-full items-center justify-between gap-3 py-1">
									<div class="min-w-0 pr-3 text-xs font-medium">{$i18n.t('Kokoro.js Dtype')}</div>

									<div class="relative flex shrink-0 items-center max-w-full">
										<HaloSelect
											className="w-fit text-xs text-right"
											bind:value={TTSEngineConfig.dtype}
											placeholder="Select dtype"
											options={[
												{ value: 'fp32', label: 'fp32' },
												{ value: 'fp16', label: 'fp16' },
												{ value: 'q8', label: 'q8' },
												{ value: 'q4', label: 'q4' }
											]}
										/>
									</div>
								</div>
							{/if}

							<div class="py-0.5 flex w-full justify-between">
								<div class="self-center text-xs font-medium">
									{$i18n.t('Auto-playback response')}
								</div>

								<button
									class="p-1 px-3 text-xs flex rounded-sm transition"
									on:click={() => {
										toggleResponseAutoPlayback();
									}}
									type="button"
								>
									{#if responseAutoPlayback === true}
										<span class="ml-2 self-center">{$i18n.t('On')}</span>
									{:else}
										<span class="ml-2 self-center">{$i18n.t('Off')}</span>
									{/if}
								</button>
							</div>

							<div class="flex w-full items-center justify-between gap-3 py-1">
								<div class="min-w-0 pr-3 text-xs font-medium">{$i18n.t('Speech Playback Speed')}</div>

								<div class="relative flex shrink-0 items-center max-w-full">
									<HaloSelect
										className="w-fit text-xs text-right"
										bind:value={playbackRate}
										options={speedOptions.map((s) => ({ value: String(s), label: `${s}x` }))}
									/>
								</div>
							</div>
						</div>
					{/if}
				</div>

				<hr class="border-gray-100 dark:border-gray-850" />

				<div
					bind:this={sectionEl_voice}
					class={`scroll-mt-2 ${
						isDocumentCard
							? 'rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden'
							: ''
					}`}
				>
					{#if isDocumentCard}
						<button
							type="button"
							class="w-full flex items-center justify-between px-4 py-3 text-left"
							on:click={() => toggleSection('voice')}
						>
							<div class="mb-0 text-sm font-medium flex items-center gap-2">
								<span>{$i18n.t('Set Voice')}</span>
								{#if showScopeBadges && scopeLabel}
									<span class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
										>{scopeLabel}</span
									>
								{/if}
							</div>

							<div
								class="transform transition-transform duration-200 {expandedSections.voice
									? 'rotate-180'
									: ''}"
							>
								<ChevronDown className="size-4 text-gray-400" />
							</div>
						</button>
					{/if}

					{#if !isDocumentCard || expandedSections.voice}
						<div
							class={isDocumentCard ? 'px-4 pb-4 border-t border-gray-100 dark:border-gray-800' : ''}
							transition:slide={{ duration: 180, easing: quintOut }}
						>
							{#if TTSEngine === 'browser-kokoro' && kokoroConsentAccepted}
								{#if TTSModel}
									<div>
										{#if !isDocumentCard}
											<div class="mb-2.5 text-sm font-medium flex items-center gap-2">
												<span>{$i18n.t('Set Voice')}</span>
												{#if showScopeBadges && scopeLabel}
													<span
														class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
														>{scopeLabel}</span
													>
												{/if}
											</div>
										{/if}
										<div class="flex w-full">
											<div class="flex-1">
												<input
													list="voice-list"
													class="w-full text-sm bg-white dark:text-gray-300 dark:bg-gray-850 outline-hidden"
													bind:value={voice}
													placeholder="Select a voice"
												/>

												<datalist id="voice-list">
													{#each voices as voice}
														<option value={voice.id}>{voice.name}</option>
													{/each}
												</datalist>
											</div>
										</div>
									</div>
								{:else}
									<div>
										<div class="mb-2.5 text-sm font-medium flex gap-2 items-center">
											<Spinner className="size-4" />

											<div class="text-sm font-medium shimmer">
												{$i18n.t('Loading Kokoro.js...')}
												{TTSModelProgress && TTSModelProgress.status === 'progress'
													? `(${Math.round(TTSModelProgress.progress * 10) / 10}%)`
													: ''}
											</div>
										</div>

										<div class="text-xs text-gray-500">
											{$i18n.t('Please do not close the settings page while loading the model.')}
										</div>
									</div>
								{/if}
							{:else if $config.audio.tts.engine === ''}
								<div>
									{#if !isDocumentCard}
										<div class="mb-2.5 text-sm font-medium flex items-center gap-2">
											<span>{$i18n.t('Set Voice')}</span>
											{#if showScopeBadges && scopeLabel}
												<span
													class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
													>{scopeLabel}</span
												>
											{/if}
										</div>
									{/if}
									<div class="flex w-full">
										<div class="w-full max-w-[15rem]">
											<HaloSelect
												className="w-full"
												bind:value={voice}
												options={[
													{ value: '', label: $i18n.t('Default') },
													...voices
														.filter((v) => nonLocalVoices || v.localService === true)
														.map((v) => ({ value: v.name, label: v.name }))
												]}
											/>
										</div>
									</div>
									<div class="flex items-center justify-between my-1.5">
										<div class="text-xs">
											{$i18n.t('Allow non-local voices')}
										</div>

										<div class="mt-1">
											<Switch bind:state={nonLocalVoices} />
										</div>
									</div>
								</div>
							{:else if $config.audio.tts.engine !== ''}
								<div>
									{#if !isDocumentCard}
										<div class="mb-2.5 text-sm font-medium flex items-center gap-2">
											<span>{$i18n.t('Set Voice')}</span>
											{#if showScopeBadges && scopeLabel}
												<span
													class={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${scopeBadgeClass}`}
													>{scopeLabel}</span
												>
											{/if}
										</div>
									{/if}
									<div class="flex w-full">
										<div class="flex-1">
											<input
												list="voice-list"
												class="w-full text-sm bg-white dark:text-gray-300 dark:bg-gray-850 outline-hidden"
												bind:value={voice}
												placeholder="Select a voice"
											/>

											<datalist id="voice-list">
												{#each voices as voice}
													<option value={voice.id}>{voice.name}</option>
												{/each}
											</datalist>
										</div>
									</div>
								</div>
							{/if}
						</div>
					{/if}
				</div>

				{#if voices.length > 0 && $models.length > 0}
					<hr class="border-gray-100 dark:border-gray-850" />

					<div>
						<div class="mb-2 text-sm font-medium">{$i18n.t('Per-Model Voice Override')}</div>
						<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
							{$i18n.t(
								'Set a different voice for specific models. Leave empty to use the default voice.'
							)}
						</div>
						<div class="space-y-2 max-h-48 overflow-y-auto">
							{#each $models as model}
								<div class="flex items-center gap-2">
									<div class="text-xs font-medium w-1/3 truncate" title={model.name ?? model.id}>
										{model.name ?? model.id}
									</div>
									<div class="flex-1">
										<input
											list="model-voice-list-{model.id}"
											class="w-full text-xs bg-white dark:text-gray-300 dark:bg-gray-850 outline-hidden rounded px-2 py-1 border border-gray-200 dark:border-gray-700"
											value={modelVoices[model.id] ?? ''}
											placeholder={$i18n.t('Default')}
											on:change={(e) => {
												modelVoices[model.id] = e.target.value;
												modelVoices = modelVoices;
											}}
										/>
										<datalist id="model-voice-list-{model.id}">
											{#each voices as v}
												<option value={v.id ?? v.name}>{v.name ?? v.id}</option>
											{/each}
										</datalist>
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/if}
			{/if}
		</div>

		{#if showSubmit}
			<div class="flex justify-end text-sm font-medium">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
					type="submit"
				>
					{$i18n.t('Save')}
				</button>
			</div>
		{/if}
	</form>
{/if}
