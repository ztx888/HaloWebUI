<script lang="ts">
	import { onMount, onDestroy, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { models } from '$lib/stores';
	import { convertMathOCR } from '$lib/apis/math-ocr';
	import { getTaskConfig } from '$lib/apis';

	const i18n = getContext('i18n');

	let loading = false;
	let dragActive = false;
	let imageDataUrl = '';
	let latex = '';
	let configuredModelId = '';
	let customPrompt =
		'请从图片中提取数学公式，并仅返回可直接使用的 LaTeX 代码，不要附加解释。';
	let fileInputElement: HTMLInputElement;

	const getConfiguredModelName = () => {
		if (!configuredModelId) return $i18n.t('管理员默认模型');
		const model = $models.find((item) => item.id === configuredModelId);
		return model?.name ?? configuredModelId;
	};

	const readFileAsDataUrl = async (file: File): Promise<string> => {
		return await new Promise((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(String(reader.result || ''));
			reader.onerror = () => reject(new Error('读取图片失败'));
			reader.readAsDataURL(file);
		});
	};

	const handleImageFile = async (file?: File | null) => {
		if (!file) return;
		if (!file.type.startsWith('image/')) {
			toast.error($i18n.t('请上传图片文件'));
			return;
		}
		imageDataUrl = await readFileAsDataUrl(file);
		latex = '';
	};

	const onPaste = async (event: ClipboardEvent) => {
		const items = event.clipboardData?.items ?? [];
		for (const item of items) {
			if (item.type.startsWith('image/')) {
				event.preventDefault();
				const file = item.getAsFile();
				await handleImageFile(file);
				return;
			}
		}
	};

	const runConversion = async () => {
		if (!imageDataUrl) {
			toast.error($i18n.t('请先上传、拖拽或粘贴一张公式图片'));
			return;
		}

		loading = true;
		try {
			const result = await convertMathOCR(localStorage.token, {
				image_base64: imageDataUrl,
				prompt: customPrompt
			});
			latex = result?.latex ?? '';
			if (!latex) {
				toast.warning($i18n.t('未识别到有效公式'));
			}
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	const copyLatex = async () => {
		if (!latex) return;
		try {
			await navigator.clipboard.writeText(latex);
			toast.success($i18n.t('LaTeX 已复制到剪贴板'));
		} catch (error) {
			toast.error($i18n.t('复制失败，请手动复制'));
		}
	};

	onMount(async () => {
		window.addEventListener('paste', onPaste);
		const taskConfig = await getTaskConfig(localStorage.token).catch(() => null);
		if (taskConfig?.MATH_OCR_MODEL_ID) {
			configuredModelId = taskConfig.MATH_OCR_MODEL_ID;
		}
	});

	onDestroy(() => {
		window.removeEventListener('paste', onPaste);
	});
</script>

<svelte:head>
	<title>{$i18n.t('公式转换')}</title>
</svelte:head>

<div class="formula-shell w-full min-h-full px-1 py-3 md:py-5">
	<div
		class="mx-auto max-w-[1280px] rounded-[28px] border border-gray-200/80 dark:border-gray-800/80 bg-white/85 dark:bg-gray-950/70 shadow-[0_28px_80px_-44px_rgba(15,23,42,0.6)] overflow-hidden"
	>
		<div class="relative px-5 md:px-8 py-6 md:py-7 border-b border-gray-200/70 dark:border-gray-800/80">
			<div class="hero-glow hero-glow-left" aria-hidden="true"></div>
			<div class="hero-glow hero-glow-right" aria-hidden="true"></div>

			<div class="relative z-[1] flex items-start justify-between gap-4">
				<div class="min-w-0">
					<div class="flex items-center gap-3">
						<div
							class="w-10 h-10 rounded-xl bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900 flex items-center justify-center text-lg font-semibold"
						>
							∑
						</div>
						<div class="text-2xl md:text-[30px] leading-tight font-semibold tracking-tight text-gray-900 dark:text-gray-100">
							{$i18n.t('数学公式图片转 LaTeX')}
						</div>
					</div>
					<div class="mt-2 text-sm md:text-[15px] text-gray-600 dark:text-gray-300">
						{$i18n.t('粘贴截图、上传照片或拖拽图片，快速提取可直接使用的 LaTeX 公式代码。')}
					</div>
				</div>

				<div
					class="shrink-0 hidden md:inline-flex px-3 py-1.5 rounded-full text-xs bg-white/90 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-200 backdrop-blur"
				>
					{$i18n.t('当前模型')}: {getConfiguredModelName()}
				</div>
			</div>

			<div class="relative z-[1] mt-4 flex flex-wrap gap-2 text-xs text-gray-600 dark:text-gray-300">
				<span class="px-2.5 py-1 rounded-full bg-gray-100 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
					Ctrl/Cmd + V {$i18n.t('直接粘贴')}
				</span>
				<span class="px-2.5 py-1 rounded-full bg-gray-100 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
					{$i18n.t('自动识别并输出纯 LaTeX')}
				</span>
				<span class="px-2.5 py-1 rounded-full bg-gray-100 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
					{$i18n.t('适配 Word / WPS / PowerPoint 公式编辑器')}
				</span>
			</div>
		</div>

		<div class="p-4 md:p-7 space-y-4 md:space-y-5">
			<div class="grid grid-cols-1 xl:grid-cols-12 gap-4 md:gap-5">
				<div class="xl:col-span-7 rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/90 overflow-hidden">
					<div class="px-4 py-3.5 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-3">
						<div class="text-sm font-semibold text-gray-800 dark:text-gray-200">{$i18n.t('公式图片')}</div>
						<div class="flex items-center gap-2">
							<button
								class="px-3 py-1.5 rounded-lg text-sm bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 transition"
								on:click={() => fileInputElement?.click()}
							>
								{$i18n.t('上传图片')}
							</button>
							<button
								class="px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-900 text-white hover:bg-gray-800 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-200 transition disabled:opacity-60"
								on:click={runConversion}
								disabled={loading}
							>
								{loading ? $i18n.t('识别中...') : $i18n.t('开始转换')}
							</button>
						</div>
					</div>

					<input
						bind:this={fileInputElement}
						type="file"
						accept="image/*"
						class="hidden"
						on:change={async (event) => {
							const target = event.target as HTMLInputElement;
							await handleImageFile(target?.files?.[0]);
						}}
					/>

					<div class="p-4">
						<div
							class="rounded-2xl border-2 border-dashed transition h-[22rem] md:h-[29rem] {dragActive
								? 'border-cyan-400 bg-cyan-50/60 dark:bg-cyan-950/20'
								: 'border-gray-200 dark:border-gray-700 bg-gradient-to-br from-gray-50 to-gray-100/60 dark:from-gray-900/40 dark:to-gray-850/20'} flex items-center justify-center overflow-hidden"
							on:dragover|preventDefault={() => {
								dragActive = true;
							}}
							on:dragleave|preventDefault={() => {
								dragActive = false;
							}}
							on:drop|preventDefault={async (event) => {
								dragActive = false;
								const files = event.dataTransfer?.files;
								await handleImageFile(files?.[0]);
							}}
						>
							{#if imageDataUrl}
								<img
									src={imageDataUrl}
									alt="公式图片预览"
									class="max-h-full max-w-full object-contain rounded-lg shadow-sm"
								/>
							{:else}
								<div class="text-center px-6 text-gray-500 dark:text-gray-400">
									<div class="text-lg font-semibold text-gray-700 dark:text-gray-200 mb-2">
										{$i18n.t('拖拽图片到这里')}
									</div>
									<div class="text-sm leading-6">
										{$i18n.t('支持 Ctrl/Cmd + V 粘贴截图，也可点击上传')}
									</div>
									<div class="mt-2 text-xs text-gray-400">
										PNG / JPG / JPEG / GIF / BMP / WebP
									</div>
								</div>
							{/if}
						</div>

						<div class="mt-3 flex items-center gap-2">
							<button
								class="px-3 py-1.5 rounded-lg text-sm bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 transition"
								on:click={() => {
									imageDataUrl = '';
									latex = '';
								}}
							>
								{$i18n.t('清空')}
							</button>
						</div>
					</div>
				</div>

				<div class="xl:col-span-5 rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/90 overflow-hidden">
					<div class="px-4 py-3.5 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between">
						<div class="text-sm font-semibold text-gray-800 dark:text-gray-200">{$i18n.t('LaTeX 结果')}</div>
						<button
							class="px-3 py-1.5 rounded-lg text-sm bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 transition disabled:opacity-50"
							on:click={copyLatex}
							disabled={!latex}
						>
							{$i18n.t('复制')}
						</button>
					</div>

					<div class="p-4">
						<textarea
							class="w-full h-[22rem] md:h-[29rem] rounded-2xl px-4 py-3 text-sm leading-6 font-mono bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-700 outline-none resize-none focus:border-cyan-400/70 dark:focus:border-cyan-500/70 transition"
							bind:value={latex}
							placeholder={$i18n.t('转换结果会显示在这里，可直接复制到公式编辑器')}
						/>
					</div>
				</div>
			</div>

			<div class="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900/90 p-4 md:p-5">
				<div class="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">{$i18n.t('使用说明')}</div>
				<div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm leading-7 text-gray-600 dark:text-gray-300">
					<div>
						1. {$i18n.t('上传或粘贴公式截图，建议裁剪到只保留公式区域。')}<br />
						2. {$i18n.t('点击“开始转换”，等待识别完成后复制 LaTeX。')}<br />
						3. {$i18n.t('如识别结果不理想，可尝试更清晰截图或更高分辨率图片。')}
					</div>
					<div>
						1. {$i18n.t('在 Word / PowerPoint / WPS 中按 Alt + = 打开公式输入框。')}<br />
						2. {$i18n.t('粘贴 LaTeX 并回车，即可渲染为公式。')}<br />
						3. {$i18n.t('若部分命令不兼容，先简化表达式再粘贴。')}
					</div>
				</div>
			</div>
		</div>
	</div>
</div>

<style>
	.formula-shell {
		background:
			radial-gradient(1200px 500px at 2% 0%, rgba(34, 211, 238, 0.09), transparent 58%),
			radial-gradient(900px 420px at 100% 0%, rgba(59, 130, 246, 0.08), transparent 58%);
	}

	.hero-glow {
		position: absolute;
		width: 200px;
		height: 200px;
		filter: blur(44px);
		opacity: 0.25;
		pointer-events: none;
	}

	.hero-glow-left {
		left: -80px;
		top: -90px;
		background: linear-gradient(135deg, #22d3ee, #3b82f6);
	}

	.hero-glow-right {
		right: -90px;
		top: -70px;
		background: linear-gradient(135deg, #2563eb, #0ea5e9);
	}
</style>
