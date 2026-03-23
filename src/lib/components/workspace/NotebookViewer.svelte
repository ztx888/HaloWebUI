<script lang="ts">
	import { onDestroy, getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		approvePyodideConsent,
		canUsePyodideRuntime,
		getPyodideDownloadSummary,
		getPyodidePackagesForCode,
		hasPyodideConsent,
		usesRemotePyodideRuntime
	} from '$lib/utils/browser-ai-assets';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher();

	// ── Types ──────────────────────────────────────────────
	interface NotebookOutput {
		output_type: string; // 'stream' | 'execute_result' | 'display_data' | 'error'
		name?: string; // 'stdout' | 'stderr'
		text?: string | string[];
		data?: Record<string, string | string[]>;
		ename?: string;
		evalue?: string;
		traceback?: string[];
	}

	interface NotebookCell {
		cell_type: 'code' | 'markdown' | 'raw';
		source: string | string[];
		outputs?: NotebookOutput[];
		execution_count?: number | null;
		metadata?: Record<string, any>;
	}

	interface Notebook {
		nbformat: number;
		nbformat_minor: number;
		metadata: Record<string, any>;
		cells: NotebookCell[];
	}

	type CellExecState = 'idle' | 'running' | 'done' | 'error';

	// ── Props ──────────────────────────────────────────────
	export let notebook: Notebook;
	export let filePath: string = '';

	// ── Kernel (lazy) ──────────────────────────────────────
	let kernel: any = null;
	let kernelLoading = false;
	const pyodideDisabledMessage = 'Python notebook execution is disabled in this build.';

	async function ensureKernel() {
		if (kernel) return kernel;
		if (!canUsePyodideRuntime()) {
			throw new Error(pyodideDisabledMessage);
		}
		kernelLoading = true;
		try {
			const mod = await import('$lib/pyodide/pyodideKernel');
			kernel = new mod.PyodideKernel();
			return kernel;
		} catch (e: any) {
			toast.error($i18n.t(e?.message || 'Failed to load Python kernel'));
			throw e;
		} finally {
			kernelLoading = false;
		}
	}

	// ── Execution state per cell index ─────────────────────
	let cellStates: Record<number, CellExecState> = {};
	let cellOutputs: Record<number, { stdout: string; stderr: string; result: any }> = {};
	let cellExecCounts: Record<number, number> = {};
	let globalExecCounter = 0;
	let runningAll = false;
	let showPyodideConsent = false;
	let pyodideConsentPackages: string[] = [];
	let pendingPyodideAction: { type: 'runAll' } | { type: 'runCell'; idx: number } | null = null;

	function requestPyodideConsent(action: { type: 'runAll' } | { type: 'runCell'; idx: number }, code: string) {
		if (!usesRemotePyodideRuntime() || hasPyodideConsent()) {
			return false;
		}

		pendingPyodideAction = action;
		pyodideConsentPackages = getPyodidePackagesForCode(code);
		showPyodideConsent = true;
		return true;
	}

	function resetCellOutput(idx: number) {
		cellOutputs[idx] = { stdout: '', stderr: '', result: null };
	}

	async function runCell(idx: number) {
		const cell = notebook.cells[idx];
		if (!cell || cell.cell_type !== 'code') return;

		const source = normalizeSource(cell.source);
		if (!source.trim()) return;
		if (requestPyodideConsent({ type: 'runCell', idx }, source)) return;

		cellStates[idx] = 'running';
		resetCellOutput(idx);
		cellStates = cellStates; // trigger reactivity

		try {
			const k = await ensureKernel();
			const cellId = `nb-${idx}-${Date.now()}`;

			// Stream handler: accumulate stdout/stderr during execution
			const streamHandler = (data: any) => {
				if (data.type === 'stdout') {
					cellOutputs[idx].stdout += data.message;
					cellOutputs = cellOutputs;
				} else if (data.type === 'stderr') {
					cellOutputs[idx].stderr += data.message;
					cellOutputs = cellOutputs;
				}
			};
			k.listeners.set(cellId + '-stream', streamHandler);

			const result = await k.execute(cellId, source);

			// Clean up stream listener
			k.listeners.delete(cellId + '-stream');

			globalExecCounter++;
			cellExecCounts[idx] = globalExecCounter;

			if (result.status === 'error') {
				cellStates[idx] = 'error';
				cellOutputs[idx].stderr = result.stderr || '';
			} else {
				cellStates[idx] = 'done';
				cellOutputs[idx].stdout = result.stdout || '';
				cellOutputs[idx].result = result.result;
			}
		} catch (e: any) {
			cellStates[idx] = 'error';
			cellOutputs[idx].stderr = e?.message || String(e);
		}

		cellStates = cellStates;
		cellOutputs = cellOutputs;
		cellExecCounts = cellExecCounts;
	}

	async function runAll() {
		if (!canUsePyodideRuntime()) {
			toast.error($i18n.t(pyodideDisabledMessage));
			return;
		}
		const fullSource = notebook.cells
			.filter((cell) => cell.cell_type === 'code')
			.map((cell) => normalizeSource(cell.source))
			.join('\n');
		if (requestPyodideConsent({ type: 'runAll' }, fullSource)) return;
		runningAll = true;
		for (let i = 0; i < notebook.cells.length; i++) {
			if (notebook.cells[i].cell_type === 'code') {
				await runCell(i);
				// Stop running all if a cell errors
				if (cellStates[i] === 'error') break;
			}
		}
		runningAll = false;
	}

	// ── Helpers ────────────────────────────────────────────
	function normalizeSource(src: string | string[]): string {
		return Array.isArray(src) ? src.join('') : src;
	}

	function joinTextOutput(text: string | string[] | undefined): string {
		if (!text) return '';
		return Array.isArray(text) ? text.join('') : text;
	}

	/** Simple markdown-to-HTML: headers, bold, italic, code, links, lists, paragraphs. */
	function renderMarkdown(md: string): string {
		let html = md
			// Code blocks (fenced)
			.replace(
				/```(\w*)\n([\s\S]*?)```/g,
				'<pre class="bg-gray-800 text-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs"><code>$2</code></pre>'
			)
			// Inline code
			.replace(
				/`([^`]+)`/g,
				'<code class="bg-gray-200 dark:bg-gray-700 px-1 py-0.5 rounded text-xs">$1</code>'
			)
			// Headers
			.replace(/^#### (.+)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1">$1</h4>')
			.replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold mt-3 mb-1">$1</h3>')
			.replace(/^## (.+)$/gm, '<h2 class="text-lg font-bold mt-4 mb-1">$1</h2>')
			.replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold mt-4 mb-2">$1</h1>')
			// Bold and italic
			.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
			.replace(/\*(.+?)\*/g, '<em>$1</em>')
			// Links
			.replace(
				/\[([^\]]+)\]\(([^)]+)\)/g,
				'<a href="$2" target="_blank" class="text-blue-500 hover:underline">$1</a>'
			)
			// Unordered lists
			.replace(/^[*-] (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
			// Ordered lists
			.replace(/^\d+\. (.+)$/gm, '<li class="ml-4 list-decimal">$1</li>')
			// Horizontal rules
			.replace(/^---$/gm, '<hr class="my-3 border-gray-300 dark:border-gray-600">')
			// Paragraphs (double newlines)
			.replace(/\n\n/g, '</p><p class="my-1">')
			// Single newlines to <br>
			.replace(/\n/g, '<br>');

		return `<p class="my-1">${html}</p>`;
	}

	/** Extract image data from cell output (base64 PNG/JPEG). */
	function getImageData(output: NotebookOutput): { mime: string; data: string } | null {
		if (!output.data) return null;
		for (const mime of ['image/png', 'image/jpeg', 'image/svg+xml']) {
			if (output.data[mime]) {
				const raw = joinTextOutput(output.data[mime]);
				if (mime === 'image/svg+xml') {
					return { mime, data: raw };
				}
				return { mime, data: `data:${mime};base64,${raw}` };
			}
		}
		return null;
	}

	function getLanguage(): string {
		const kernelInfo = notebook.metadata?.kernelspec;
		return kernelInfo?.language || kernelInfo?.name || 'python';
	}

	// ── Cleanup ────────────────────────────────────────────
	onDestroy(() => {
		if (kernel) {
			kernel.terminate();
			kernel = null;
		}
	});
</script>

<div class="flex flex-col h-full min-h-0">
	<!-- Toolbar -->
	<div
		class="flex items-center justify-between px-3 py-2 bg-gray-50 dark:bg-gray-800/50 border-b border-gray-200 dark:border-gray-700 flex-shrink-0"
	>
		<div class="flex items-center gap-2 min-w-0">
			<span class="text-base flex-shrink-0">&#x1F4D3;</span>
			<span class="text-sm font-mono truncate text-gray-600 dark:text-gray-300">
				{filePath || 'Notebook'}
			</span>
			<span class="text-[10px] text-gray-400 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700">
				{getLanguage()} &middot; {notebook.cells.length}
				{$i18n.t('cells')}
			</span>
		</div>
		<div class="flex items-center gap-1 flex-shrink-0">
			<button
				class="px-2.5 py-1 text-xs rounded-lg bg-green-600 text-white hover:bg-green-700 transition disabled:opacity-50 flex items-center gap-1"
				disabled={!canUsePyodideRuntime() || runningAll || kernelLoading}
				on:click={runAll}
			>
				{#if runningAll}
					<svg class="animate-spin h-3 w-3" fill="none" viewBox="0 0 24 24">
						<circle
							class="opacity-25"
							cx="12"
							cy="12"
							r="10"
							stroke="currentColor"
							stroke-width="4"
						/>
						<path
							class="opacity-75"
							fill="currentColor"
							d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
						/>
					</svg>
					{$i18n.t('Running...')}
				{:else}
					<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
						<path d="M8 5v14l11-7z" />
					</svg>
					{$i18n.t('Run All')}
				{/if}
			</button>
			<button
				class="px-2 py-1 text-xs rounded-lg text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
				on:click={() => dispatch('close')}
			>
				{$i18n.t('Close')}
			</button>
		</div>
	</div>
	{#if !canUsePyodideRuntime()}
		<div
			class="px-3 py-2 border-b border-amber-200 bg-amber-50 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200"
		>
			{$i18n.t(pyodideDisabledMessage)}
		</div>
	{:else if showPyodideConsent}
		<div
			class="px-3 py-3 border-b border-amber-200 bg-amber-50 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200"
		>
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
						const action = pendingPyodideAction;
						pendingPyodideAction = null;
						if (action?.type === 'runAll') {
							await runAll();
						} else if (action?.type === 'runCell') {
							await runCell(action.idx);
						}
					}}
					type="button"
				>
					下载并启用
				</button>
				<button
					class="rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-amber-700 transition hover:bg-amber-100 dark:bg-transparent dark:text-amber-200 dark:hover:bg-amber-900/30"
					on:click={() => {
						showPyodideConsent = false;
						pendingPyodideAction = null;
					}}
					type="button"
				>
					暂不
				</button>
			</div>
		</div>
	{/if}

	<!-- Cells -->
	<div class="flex-1 overflow-y-auto p-3 space-y-3">
		{#each notebook.cells as cell, idx}
			<div class="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
				{#if cell.cell_type === 'markdown'}
					<!-- Markdown cell -->
					<div
						class="px-4 py-3 prose prose-sm dark:prose-invert max-w-none text-sm text-gray-800 dark:text-gray-200"
					>
						{@html renderMarkdown(normalizeSource(cell.source))}
					</div>
				{:else if cell.cell_type === 'code'}
					<!-- Code cell -->
					<div class="flex flex-col">
						<!-- Code header with run button -->
						<div
							class="flex items-center gap-1 px-2 py-1 bg-gray-50 dark:bg-gray-800/70 border-b border-gray-200 dark:border-gray-700"
						>
							<!-- Execution count marker -->
							<span class="text-[10px] font-mono text-gray-400 w-10 text-right flex-shrink-0">
								{#if cellExecCounts[idx]}
									[{cellExecCounts[idx]}]
								{:else if cell.execution_count != null}
									[{cell.execution_count}]
								{:else}
									[ ]
								{/if}
							</span>

							<!-- Run button -->
							<button
								class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition disabled:opacity-50 flex-shrink-0"
								disabled={cellStates[idx] === 'running' || runningAll || !canUsePyodideRuntime()}
								title={$i18n.t('Run Cell')}
								on:click={() => runCell(idx)}
							>
								{#if cellStates[idx] === 'running'}
									<svg
										class="animate-spin h-3.5 w-3.5 text-blue-500"
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
										/>
										<path
											class="opacity-75"
											fill="currentColor"
											d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
										/>
									</svg>
								{:else}
									<svg
										class="w-3.5 h-3.5 {cellStates[idx] === 'done'
											? 'text-green-500'
											: cellStates[idx] === 'error'
												? 'text-red-500'
												: 'text-gray-400'}"
										fill="currentColor"
										viewBox="0 0 24 24"
									>
										<path d="M8 5v14l11-7z" />
									</svg>
								{/if}
							</button>

							<!-- Status indicator -->
							{#if cellStates[idx] === 'done'}
								<span class="text-[10px] text-green-500">&#10003;</span>
							{:else if cellStates[idx] === 'error'}
								<span class="text-[10px] text-red-500">&#10007;</span>
							{/if}

							<span class="flex-1"></span>
							<span class="text-[10px] text-gray-400">{getLanguage()}</span>
						</div>

						<!-- Source code -->
						<pre
							class="px-4 py-3 bg-gray-900 text-gray-100 text-xs font-mono overflow-x-auto whitespace-pre leading-relaxed">{normalizeSource(
								cell.source
							)}</pre>

						<!-- Original notebook outputs (before any execution) -->
						{#if !cellStates[idx] && cell.outputs && cell.outputs.length > 0}
							<div class="border-t border-gray-200 dark:border-gray-700">
								{#each cell.outputs as output}
									{@const imageData = getImageData(output)}
									{#if output.output_type === 'stream'}
										<pre
											class="px-4 py-2 text-xs font-mono whitespace-pre-wrap {output.name ===
											'stderr'
												? 'text-red-400 bg-red-950/20'
												: 'text-gray-300 bg-gray-900/50'}">{joinTextOutput(output.text)}</pre>
									{:else if output.output_type === 'error'}
										<pre
											class="px-4 py-2 text-xs font-mono text-red-400 bg-red-950/20 whitespace-pre-wrap">{output.traceback
												? output.traceback.join('\n').replace(/\x1b\[[0-9;]*m/g, '')
												: `${output.ename}: ${output.evalue}`}</pre>
									{:else if imageData}
										<div class="px-4 py-2 bg-white dark:bg-gray-900/50">
											{#if imageData.mime === 'image/svg+xml'}
												{@html imageData.data}
											{:else}
												<img src={imageData.data} alt="Cell output" class="max-w-full" />
											{/if}
										</div>
									{:else if output.output_type === 'execute_result' || output.output_type === 'display_data'}
										{@const textData = output.data?.['text/plain']}
										{@const htmlData = output.data?.['text/html']}
										{#if htmlData}
											<div class="px-4 py-2 bg-gray-900/50 text-xs overflow-x-auto">
												{@html joinTextOutput(htmlData)}
											</div>
										{:else if textData}
											<pre
												class="px-4 py-2 text-xs font-mono text-gray-300 bg-gray-900/50 whitespace-pre-wrap">{joinTextOutput(
													textData
												)}</pre>
										{/if}
									{/if}
								{/each}
							</div>
						{/if}

						<!-- Live execution outputs -->
						{#if cellStates[idx] && cellOutputs[idx]}
							{@const out = cellOutputs[idx]}
							{#if out.stdout || out.stderr || out.result != null}
								<div class="border-t border-gray-200 dark:border-gray-700">
									{#if out.stdout}
										<pre
											class="px-4 py-2 text-xs font-mono text-gray-300 bg-gray-900/50 whitespace-pre-wrap">{out.stdout}</pre>
									{/if}
									{#if out.stderr}
										<pre
											class="px-4 py-2 text-xs font-mono text-red-400 bg-red-950/20 whitespace-pre-wrap">{out.stderr}</pre>
									{/if}
									{#if out.result != null && out.result !== undefined && String(out.result) !== 'undefined'}
										<pre
											class="px-4 py-2 text-xs font-mono text-blue-300 bg-gray-900/50 whitespace-pre-wrap">{String(
												out.result
											)}</pre>
									{/if}
								</div>
							{/if}
						{/if}
					</div>
				{:else}
					<!-- Raw cell -->
					<pre
						class="px-4 py-3 text-xs font-mono text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-800/30 whitespace-pre-wrap">{normalizeSource(
							cell.source
						)}</pre>
				{/if}
			</div>
		{/each}

		{#if notebook.cells.length === 0}
			<div class="flex items-center justify-center py-12 text-gray-400 text-sm">
				{$i18n.t('This notebook has no cells')}
			</div>
		{/if}
	</div>
</div>
