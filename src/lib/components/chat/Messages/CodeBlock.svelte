<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';

	import { getContext, onMount, tick, onDestroy } from 'svelte';
	import { copyToClipboard } from '$lib/utils';

	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import SvgPanZoom from '$lib/components/common/SVGPanZoom.svelte';
	import { artifactPreviewTarget, config, settings, showArtifacts, showControls } from '$lib/stores';
	import { executeCode } from '$lib/apis/utils';
	import { toast } from 'svelte-sonner';
	import ChevronUpDown from '$lib/components/icons/ChevronUpDown.svelte';
	import {
		approvePyodideConsent,
		canUsePyodideRuntime,
		getPyodideDownloadSummary,
		getPyodidePackagesForCode,
		hasPyodideConsent,
		usesRemotePyodideRuntime
	} from '$lib/utils/browser-ai-assets';
	import { getLanguageIcon } from '$lib/utils/language-icons';
	import {
		DEFAULT_MERMAID_THEME,
		normalizeMermaidTheme,
		renderMermaidSvg
	} from '$lib/utils/lobehub-chat-appearance';

	const i18n = getContext('i18n');

	export let id = '';

	export let onSave = (e) => {};
	export let onCode = (e) => {};

	export let save = false;
	export let run = true;
	export let collapsed = false;

	export let token;
	export let lang = '';
	export let code = '';
	export let messageId = '';

	$: langIcon = getLanguageIcon(lang);
	export let attributes = {};

	export let className = 'my-2';
	export let editorClassName = '';

	let pyodideWorker = null;

	let _code = '';
	$: if (code) {
		updateCode();
	}

	const updateCode = () => {
		_code = code;
	};

	let _token = null;
	let mermaidThemeId = DEFAULT_MERMAID_THEME;

	let mermaidHtml = null;
	let executing = false;

	let stdout = null;
	let stderr = null;
	let result = null;
	let files = null;

	let copied = false;
	let saved = false;
	let mermaidThemeObserver: MutationObserver | null = null;
	const PYODIDE_DISABLED_MESSAGE = 'Pyodide is disabled in this build.';
	let showPyodideConsent = false;
	let pendingPyodideCode = '';
	let pyodideConsentPackages: string[] = [];

	const collapseCodeBlock = () => {
		collapsed = !collapsed;
	};

	const isSvgPreviewable = (lang: string, code: string) => {
		const normalizedLang = String(lang ?? '').toLowerCase();
		return normalizedLang === 'svg' || (normalizedLang === 'xml' && code.includes('<svg'));
	};

	const previewSvg = () => {
		const previewContent = (_code || code || '').trim();
		if (!previewContent) return;

		artifactPreviewTarget.set({
			messageId,
			type: 'svg',
			content: previewContent
		});
		showArtifacts.set(true);
		showControls.set(true);
	};

	const saveCode = () => {
		saved = true;

		code = _code;
		onSave(code);

		setTimeout(() => {
			saved = false;
		}, 1000);
	};

	const copyCode = async () => {
		copied = true;
		await copyToClipboard(code);

		setTimeout(() => {
			copied = false;
		}, 1000);
	};

	const checkPythonCode = (str) => {
		// Check if the string contains typical Python syntax characters
		const pythonSyntax = [
			'def ',
			'else:',
			'elif ',
			'try:',
			'except:',
			'finally:',
			'yield ',
			'lambda ',
			'assert ',
			'nonlocal ',
			'del ',
			'True',
			'False',
			'None',
			' and ',
			' or ',
			' not ',
			' in ',
			' is ',
			' with '
		];

		for (let syntax of pythonSyntax) {
			if (str.includes(syntax)) {
				return true;
			}
		}

		// If none of the above conditions met, it's probably not Python code
		return false;
	};

	const executePython = async (code) => {
		result = null;
		stdout = null;
		stderr = null;

		executing = true;

		if ($config?.code?.engine === 'jupyter') {
			const output = await executeCode(localStorage.token, code).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (output) {
				if (output['stdout']) {
					stdout = output['stdout'];
					const stdoutLines = stdout.split('\n');

					for (const [idx, line] of stdoutLines.entries()) {
						if (line.startsWith('data:image/png;base64')) {
							if (files) {
								files.push({
									type: 'image/png',
									data: line
								});
							} else {
								files = [
									{
										type: 'image/png',
										data: line
									}
								];
							}

							if (stdout.startsWith(`${line}\n`)) {
								stdout = stdout.replace(`${line}\n`, ``);
							} else if (stdout.startsWith(`${line}`)) {
								stdout = stdout.replace(`${line}`, ``);
							}
						}
					}
				}

				if (output['result']) {
					result = output['result'];
					const resultLines = result.split('\n');

					for (const [idx, line] of resultLines.entries()) {
						if (line.startsWith('data:image/png;base64')) {
							if (files) {
								files.push({
									type: 'image/png',
									data: line
								});
							} else {
								files = [
									{
										type: 'image/png',
										data: line
									}
								];
							}

							if (result.startsWith(`${line}\n`)) {
								result = result.replace(`${line}\n`, ``);
							} else if (result.startsWith(`${line}`)) {
								result = result.replace(`${line}`, ``);
							}
						}
					}
				}

				output['stderr'] && (stderr = output['stderr']);
			}

			executing = false;
		} else {
			executePythonAsWorker(code);
		}
	};

	const executePythonAsWorker = async (code) => {
		if (!canUsePyodideRuntime()) {
			stderr = PYODIDE_DISABLED_MESSAGE;
			executing = false;
			return;
		}

		const packages = getPyodidePackagesForCode(code);

		if (usesRemotePyodideRuntime() && !hasPyodideConsent()) {
			pendingPyodideCode = code;
			pyodideConsentPackages = packages;
			showPyodideConsent = true;
			executing = false;
			return;
		}

		console.log(packages);

		const { default: PyodideWorker } = await import('$lib/workers/pyodide.worker?worker');
		pyodideWorker = new PyodideWorker();

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
			}
		}, 60000);

		pyodideWorker.onmessage = (event) => {
			console.log('pyodideWorker.onmessage', event);
			const { id, ...data } = event.data;

			console.log(id, data);

			if (data['stdout']) {
				stdout = data['stdout'];
				const stdoutLines = stdout.split('\n');

				for (const [idx, line] of stdoutLines.entries()) {
					if (line.startsWith('data:image/png;base64')) {
						if (files) {
							files.push({
								type: 'image/png',
								data: line
							});
						} else {
							files = [
								{
									type: 'image/png',
									data: line
								}
							];
						}

						if (stdout.startsWith(`${line}\n`)) {
							stdout = stdout.replace(`${line}\n`, ``);
						} else if (stdout.startsWith(`${line}`)) {
							stdout = stdout.replace(`${line}`, ``);
						}
					}
				}
			}

			if (data['result']) {
				result = data['result'];
				const resultLines = result.split('\n');

				for (const [idx, line] of resultLines.entries()) {
					if (line.startsWith('data:image/png;base64')) {
						if (files) {
							files.push({
								type: 'image/png',
								data: line
							});
						} else {
							files = [
								{
									type: 'image/png',
									data: line
								}
							];
						}

						if (result.startsWith(`${line}\n`)) {
							result = result.replace(`${line}\n`, ``);
						} else if (result.startsWith(`${line}`)) {
							result = result.replace(`${line}`, ``);
						}
					}
				}
			}

			data['stderr'] && (stderr = data['stderr']);
			data['result'] && (result = data['result']);

			executing = false;
		};

		pyodideWorker.onerror = (event) => {
			console.log('pyodideWorker.onerror', event);
			executing = false;
		};
	};

	const drawMermaidDiagram = async () => {
		if (typeof document === 'undefined') return;

		try {
			mermaidHtml = await renderMermaidSvg({
				code,
				id: `mermaid-${uuidv4()}`,
				isDark: document.documentElement.classList.contains('dark'),
				themeId: mermaidThemeId
			});
		} catch (error) {
			console.log('Error:', error);
			mermaidHtml = null;
		}
	};

	const render = async () => {
		if (lang === 'mermaid' && (token?.raw ?? '').slice(-4).includes('```')) {
			(async () => {
				await drawMermaidDiagram();
			})();
		}
	};

	$: if (token) {
		if (JSON.stringify(token) !== JSON.stringify(_token)) {
			_token = token;
		}
	}

	$: if (_token) {
		mermaidThemeId = normalizeMermaidTheme($settings?.mermaidTheme ?? DEFAULT_MERMAID_THEME);
		render();
	}

	$: onCode({ lang, code });

	$: if (attributes) {
		onAttributesUpdate();
	}

	const onAttributesUpdate = () => {
		if (attributes?.output) {
			// Create a helper function to unescape HTML entities
			const unescapeHtml = (html) => {
				const textArea = document.createElement('textarea');
				textArea.innerHTML = html;
				return textArea.value;
			};

			try {
				// Unescape the HTML-encoded string
				const unescapedOutput = unescapeHtml(attributes.output);

				// Parse the unescaped string into JSON
				const output = JSON.parse(unescapedOutput);

				// Assign the parsed values to variables
				stdout = output.stdout;
				stderr = output.stderr;
				result = output.result;
			} catch (error) {
				console.error('Error:', error);
			}
		}
	};

	onMount(async () => {
		console.log('codeblock', lang, code);

		if (lang) {
			onCode({ lang, code });
		}

		mermaidThemeObserver = new MutationObserver(() => {
			if (lang === 'mermaid') {
				render();
			}
		});

		mermaidThemeObserver.observe(document.documentElement, {
			attributeFilter: ['class'],
			attributes: true
		});
	});

	onDestroy(() => {
		mermaidThemeObserver?.disconnect();
		if (pyodideWorker) {
			pyodideWorker.terminate();
		}
	});
</script>

<div>
	<div
		class="relative {className} flex flex-col rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 my-2 overflow-hidden"
		dir="ltr"
	>
		{#if lang === 'mermaid'}
			{#if mermaidHtml}
				<SvgPanZoom
					className=" border border-gray-100 dark:border-gray-850 rounded-lg max-h-fit overflow-hidden"
					svg={mermaidHtml}
					content={_token.text}
				/>
			{:else}
				<pre class="mermaid">{code}</pre>
			{/if}
		{:else}
			<div
				class="group sticky top-0 left-0 right-0 z-10 flex items-center justify-between px-3 py-1.5 min-h-[36px] bg-white dark:bg-gray-900 text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-800 cursor-pointer"
				on:click={collapseCodeBlock}
			>
				<div class="flex items-center gap-2">
					<span class="size-4 flex-shrink-0" aria-hidden="true">
						{@html langIcon.svg}
					</span>
					<span class="text-[13px] font-medium text-gray-600 dark:text-gray-400">
						{langIcon.label}
					</span>
				</div>
				<div
					class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
					on:click|stopPropagation
				>
					{#if ($config?.features?.enable_code_execution ?? true) && (lang.toLowerCase() === 'python' || lang.toLowerCase() === 'py' || (lang === '' && checkPythonCode(code)))}
						{#if executing}
							<div
								class="inline-flex items-center text-gray-400 dark:text-gray-500 p-1.5 rounded-md"
								title={$i18n.t('Running')}
							>
								<svg
									class="animate-spin size-4"
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
								>
									<circle
										class="opacity-25"
										cx="12"
										cy="12"
										r="10"
										stroke="currentColor"
										stroke-width="4"
									></circle>
									<path
										class="opacity-75"
										fill="currentColor"
										d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
									></path>
								</svg>
							</div>
						{:else if run}
							<button
								class="inline-flex items-center text-gray-400 dark:text-gray-500 hover:text-green-600 dark:hover:text-green-400 p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition"
								on:click={async () => {
									code = _code;
									await tick();
									executePython(code);
								}}
								title={$i18n.t('Run')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-4"
								>
									<path
										d="M6.3 2.84A1.5 1.5 0 0 0 4 4.11v11.78a1.5 1.5 0 0 0 2.3 1.27l9.344-5.891a1.5 1.5 0 0 0 0-2.538L6.3 2.841Z"
									/>
								</svg>
							</button>
						{/if}
					{/if}

					{#if isSvgPreviewable(lang, code)}
						<button
							class="inline-flex items-center text-gray-400 dark:text-gray-500 hover:text-sky-600 dark:hover:text-sky-400 p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition"
							on:click={previewSvg}
							title={`${$i18n.t('Preview')} SVG`}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="size-4"
							>
								<path d="M10 4c4.478 0 8.268 2.943 9.543 7-.33 1.05-.84 2.026-1.498 2.889C16.37 16.083 13.35 18 10 18s-6.37-1.917-8.045-5.111A9.963 9.963 0 0 1 .457 11C1.732 6.943 5.522 4 10 4Zm0 2c-3.182 0-5.92 2.07-7.05 5 1.13 2.93 3.868 5 7.05 5s5.92-2.07 7.05-5c-1.13-2.93-3.868-5-7.05-5Zm0 1.75a3.25 3.25 0 1 1 0 6.5 3.25 3.25 0 0 1 0-6.5Z" />
							</svg>
						</button>
					{/if}

					{#if save}
						<button
							class="inline-flex items-center text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-white p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition"
							on:click={saveCode}
							title={$i18n.t('Save')}
						>
							{#if saved}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-4 text-green-500"
								>
									<path
										fill-rule="evenodd"
										d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
										clip-rule="evenodd"
									/>
								</svg>
							{:else}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-4"
								>
									<path
										d="M10.75 2.75a.75.75 0 0 0-1.5 0v8.614L6.295 8.235a.75.75 0 1 0-1.09 1.03l4.25 4.5a.75.75 0 0 0 1.09 0l4.25-4.5a.75.75 0 0 0-1.09-1.03l-2.955 3.129V2.75Z"
									/>
									<path
										d="M3.5 12.75a.75.75 0 0 0-1.5 0v2.5A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25v-2.5a.75.75 0 0 0-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5Z"
									/>
								</svg>
							{/if}
						</button>
					{/if}

					<button
						class="inline-flex items-center text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-white p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={copyCode}
						title={$i18n.t('Copy')}
					>
						{#if copied}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="size-4 text-green-500"
							>
								<path
									fill-rule="evenodd"
									d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
									clip-rule="evenodd"
								/>
							</svg>
						{:else}
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="size-4"
							>
								<path
									d="M7 3.5A1.5 1.5 0 0 1 8.5 2h3.879a1.5 1.5 0 0 1 1.06.44l3.122 3.12A1.5 1.5 0 0 1 17 6.622V12.5a1.5 1.5 0 0 1-1.5 1.5h-1v-3.379a3 3 0 0 0-.879-2.121L10.5 5.379A3 3 0 0 0 8.379 4.5H7v-1Z"
								/>
								<path
									d="M4.5 6A1.5 1.5 0 0 0 3 7.5v9A1.5 1.5 0 0 0 4.5 18h7a1.5 1.5 0 0 0 1.5-1.5v-5.879a1.5 1.5 0 0 0-.44-1.06L9.44 6.439A1.5 1.5 0 0 0 8.378 6H4.5Z"
								/>
							</svg>
						{/if}
					</button>

					<button
						class="inline-flex items-center text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-white p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						on:click={collapseCodeBlock}
						title={collapsed ? $i18n.t('Expand') : $i18n.t('Collapse')}
					>
						<ChevronUpDown className="size-4" />
					</button>
				</div>
			</div>

			{#if showPyodideConsent}
				<div class="border-b border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
					<div class="font-medium">浏览器 Python 运行时未准备就绪</div>
					<div class="mt-1 text-xs leading-relaxed">
						{getPyodideDownloadSummary(pyodideConsentPackages)}
					</div>
					<div class="mt-3 flex gap-2">
						<button
							class="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-amber-700"
							on:click={async () => {
								approvePyodideConsent();
								showPyodideConsent = false;
								const nextCode = pendingPyodideCode || code;
								pendingPyodideCode = '';
								pyodideConsentPackages = [];
								executing = true;
								await executePythonAsWorker(nextCode);
							}}
							type="button"
						>
							下载并启用
						</button>
						<button
							class="rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 dark:bg-transparent dark:text-amber-200 dark:hover:bg-amber-900/30"
							on:click={() => {
								showPyodideConsent = false;
								pendingPyodideCode = '';
								pyodideConsentPackages = [];
								stderr = '已取消下载浏览器 Python 运行时。';
							}}
							type="button"
						>
							暂不
						</button>
					</div>
				</div>
			{/if}

			<div
				class="language-{lang} {editorClassName
					? editorClassName
					: executing || stdout || stderr || result
						? ''
						: ''} overflow-hidden font-mono"
			>
				{#if !collapsed}
					<CodeEditor
						value={code}
						{id}
						{lang}
						onSave={() => {
							saveCode();
						}}
						onChange={(value) => {
							_code = value;
						}}
					/>
				{:else}
					<div
						class="bg-gray-50 dark:bg-gray-950 text-gray-400 dark:text-gray-500 py-2 px-4 flex flex-col gap-2 text-sm text-center border-t border-gray-100 dark:border-gray-900"
					>
						<span class="italic">
							{$i18n.t('{{COUNT}} hidden lines', {
								COUNT: code.split('\n').length
							})}
						</span>
					</div>
				{/if}
			</div>

			{#if !collapsed}
				<div
					id="plt-canvas-{id}"
					class="bg-gray-50 dark:bg-gray-900 text-gray-800 dark:text-gray-200 max-w-full overflow-x-auto scrollbar-hidden"
				></div>

				{#if executing || stdout || stderr || result || files}
					<div
						class="bg-gray-50 dark:bg-gray-900/50 text-gray-800 dark:text-gray-200 border-t border-gray-100 dark:border-gray-800/50 py-3 px-4 flex flex-col gap-3"
					>
						{#if executing}
							<div>
								<div
									class="inline-flex items-center gap-2 rounded-lg bg-gray-100 dark:bg-gray-800/50 px-2.5 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 mb-2"
								>
									{$i18n.t('Output')}
								</div>
								<div class="text-sm flex items-center gap-2 text-gray-500">
									<svg
										class="animate-spin size-3.5"
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
									>
										<circle
											class="opacity-25"
											cx="12"
											cy="12"
											r="10"
											stroke="currentColor"
											stroke-width="4"
										></circle>
										<path
											class="opacity-75"
											fill="currentColor"
											d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
										></path>
									</svg>
									{$i18n.t('Running...')}
								</div>
							</div>
						{:else}
							{#if stdout || stderr}
								<div>
									<div
										class="inline-flex items-center gap-2 rounded-lg bg-gray-100 dark:bg-gray-800/50 px-2.5 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 mb-2"
									>
										{$i18n.t('Output')}
									</div>
									<div
										class="text-sm leading-6 font-mono whitespace-pre-wrap bg-white dark:bg-gray-950 rounded-lg p-3 border border-gray-100 dark:border-gray-800 {stdout?.split(
											'\n'
										)?.length > 100
											? `max-h-96`
											: ''} overflow-y-auto"
										style="font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;"
									>
										{stdout || stderr}
									</div>
								</div>
							{/if}
							{#if result || files}
								<div>
									<div
										class="inline-flex items-center gap-2 rounded-lg bg-gray-100 dark:bg-gray-800/50 px-2.5 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 mb-2"
									>
										{$i18n.t('Result')}
									</div>
									{#if result}
										<div
											class="text-sm leading-6 font-mono bg-white dark:bg-gray-950 rounded-lg p-3 border border-gray-100 dark:border-gray-800"
											style="font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;"
										>
											{`${JSON.stringify(result)}`}
										</div>
									{/if}
									{#if files}
										<div class="flex flex-col gap-2 mt-2">
											{#each files as file}
												{#if file.type.startsWith('image')}
													<img
														src={file.data}
														alt="Output"
														class="w-full max-w-[36rem] rounded-lg border border-gray-100 dark:border-gray-800"
													/>
												{/if}
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						{/if}
					</div>
				{/if}
			{/if}
		{/if}
	</div>
</div>
