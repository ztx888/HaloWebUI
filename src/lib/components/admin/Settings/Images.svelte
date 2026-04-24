<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';
	import { config as backendConfig, user } from '$lib/stores';

	import { getBackendConfig } from '$lib/apis';
	import {
		getImageGenerationModels,
		getImageGenerationConfig,
		updateImageGenerationConfig,
		getConfig,
		updateConfig,
		verifyConfigUrl
	} from '$lib/apis/images';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import InlineDirtyActions from './InlineDirtyActions.svelte';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';
	import { translateWithDefault } from '$lib/i18n';
	import {
		GROK_IMAGE_ASPECT_RATIO_OPTIONS,
		GROK_IMAGE_RESOLUTION_OPTIONS
	} from '$lib/utils/image-generation';
	const dispatch = createEventDispatcher();

	const i18n = getContext('i18n');
	const tr = (key: string, defaultValue: string) =>
		translateWithDefault($i18n, key, defaultValue);

	let loading = false;
	let initialSnapshot = null;

	let config = null;
	let imageGenerationConfig = null;

	let models = null;
	$: imageModelOptions = (models ?? []).map((model) => ({
		value: `${model?.id ?? ''}`,
		label: `${model?.name || model?.id || ''}`
	}));

	let samplers = [
		'DPM++ 2M',
		'DPM++ SDE',
		'DPM++ 2M SDE',
		'DPM++ 2M SDE Heun',
		'DPM++ 2S a',
		'DPM++ 3M SDE',
		'Euler a',
		'Euler',
		'LMS',
		'Heun',
		'DPM2',
		'DPM2 a',
		'DPM fast',
		'DPM adaptive',
		'Restart',
		'DDIM',
		'DDIM CFG++',
		'PLMS',
		'UniPC'
	];

	let schedulers = [
		'Automatic',
		'Uniform',
		'Karras',
		'Exponential',
		'Polyexponential',
		'SGM Uniform',
		'KL Optimal',
		'Align Your Steps',
		'Simple',
		'Normal',
		'DDIM',
		'Beta'
	];

	let requiredWorkflowNodes = [
		{
			type: 'prompt',
			key: 'text',
			node_ids: ''
		},
		{
			type: 'model',
			key: 'ckpt_name',
			node_ids: ''
		},
		{
			type: 'width',
			key: 'width',
			node_ids: ''
		},
		{
			type: 'height',
			key: 'height',
			node_ids: ''
		},
		{
			type: 'steps',
			key: 'steps',
			node_ids: ''
		},
		{
			type: 'seed',
			key: 'seed',
			node_ids: ''
		}
	];

	const VERSION_LIKE_BASE_URL_RE = /\/(?:compatible-mode\/)?v\d+(?:[a-z]+\d*)?$/i;
	const GROK_ASPECT_RATIO_OPTIONS = GROK_IMAGE_ASPECT_RATIO_OPTIONS.map((option) => ({
		value: option.value,
		label: option.label
	}));
	const GROK_RESOLUTION_OPTIONS = GROK_IMAGE_RESOLUTION_OPTIONS.map((option) => ({
		value: option.value,
		label: option.label
	}));
	const IMAGE_SIZE_OPTIONS = [
		{
			value: 'auto',
			label: tr('自动', '自动'),
			description: tr('由上游模型或服务自动决定图片尺寸', 'Let the upstream model or service choose the image size'),
			badge: tr('推荐', 'Recommended')
		},
		{ value: '512x512', label: '512 x 512', description: tr('小尺寸，速度更快', 'Small square image, faster generation') },
		{ value: '1024x1024', label: '1024 x 1024', description: tr('常用正方形尺寸', 'Common square image size') },
		{ value: '1024x1536', label: '1024 x 1536', description: tr('竖图', 'Portrait image') },
		{ value: '1536x1024', label: '1536 x 1024', description: tr('横图', 'Landscape image') },
		{ value: '1792x1024', label: '1792 x 1024', description: tr('宽屏横图', 'Wide landscape image') },
		{ value: '1024x1792', label: '1024 x 1792', description: tr('长竖图', 'Tall portrait image') }
	];
	const isSharedKeyCapableEngine = (engine: string) => ['openai', 'gemini', 'grok'].includes(engine);

	const normalizeImageProviderUrlInput = (inputUrl: string, versionPath: string): string => {
		let normalized = `${inputUrl ?? ''}`.trim();
		if (!normalized) return '';

		const explicitForceMode = normalized.endsWith('#');
		if (explicitForceMode) {
			normalized = normalized.slice(0, -1);
		}

		normalized = normalized.replace(/\/+$/, '');
		if (!normalized) return '';

		if (explicitForceMode) {
			return `${normalized}#`;
		}

		normalized = normalized.replace(/\/chat\/completions$/i, '');
		normalized = normalized.replace(/\/models$/i, '');
		normalized = normalized.replace(/\/completions$/i, '');
		normalized = normalized.replace(/\/images\/generations$/i, '');
		normalized = normalized.replace(/\/images\/edits$/i, '');
		normalized = normalized.replace(/\/+$/, '');

		if (!normalized) return '';
		if (normalized.toLowerCase().endsWith(versionPath.toLowerCase())) return normalized;
		if (VERSION_LIKE_BASE_URL_RE.test(normalized)) return normalized;

		return `${normalized}${versionPath}`;
	};

	const splitImageProviderUrlInput = (inputUrl: string, versionPath: string) => {
		const normalizedInput = normalizeImageProviderUrlInput(inputUrl, versionPath);
		const forceMode = normalizedInput.endsWith('#');
		return {
			baseUrl: forceMode ? normalizedInput.slice(0, -1) : normalizedInput,
			forceMode
		};
	};

	const decorateImageProviderUrlInput = (
		baseUrl: string,
		forceMode: boolean,
		versionPath: string
	) => {
		const normalizedBaseUrl = `${baseUrl ?? ''}`.trim().replace(/\/+$/, '');
		if (!normalizedBaseUrl) return '';
		return forceMode
			? `${normalizedBaseUrl}#`
			: normalizeImageProviderUrlInput(normalizedBaseUrl, versionPath);
	};

	const hasConfiguredProviderBaseUrl = (inputUrl: string, versionPath: string) =>
		Boolean(splitImageProviderUrlInput(inputUrl, versionPath).baseUrl);

	const serializeConfigForSave = (draftConfig) => {
		const nextConfig = cloneSettingsSnapshot(draftConfig);
		const openai = splitImageProviderUrlInput(nextConfig?.openai?.OPENAI_API_BASE_URL, '/v1');
		const gemini = splitImageProviderUrlInput(nextConfig?.gemini?.GEMINI_API_BASE_URL, '/v1beta');
		const grok = splitImageProviderUrlInput(nextConfig?.grok?.GROK_API_BASE_URL, '/v1');

		nextConfig.openai = {
			...nextConfig.openai,
			OPENAI_API_BASE_URL: openai.baseUrl,
			OPENAI_API_FORCE_MODE: openai.forceMode
		};

		nextConfig.gemini = {
			...nextConfig.gemini,
			GEMINI_API_BASE_URL: gemini.baseUrl,
			GEMINI_API_FORCE_MODE: gemini.forceMode
		};

		nextConfig.grok = {
			...nextConfig.grok,
			GROK_API_BASE_URL: grok.baseUrl
		};

		return nextConfig;
	};

	const normalizeLoadedConfig = (value) => {
		if (!value) return value;

		const normalizedValue = cloneSettingsSnapshot(value);
		normalizedValue.enabled = Boolean(normalizedValue.enabled);
		normalizedValue.prompt_generation = Boolean(normalizedValue.prompt_generation);
		normalizedValue.shared_key_enabled = Boolean(normalizedValue.shared_key_enabled);
		normalizedValue.openai = {
			...(normalizedValue.openai ?? {}),
			OPENAI_API_FORCE_MODE: Boolean(normalizedValue.openai?.OPENAI_API_FORCE_MODE),
			OPENAI_API_BASE_URL: decorateImageProviderUrlInput(
				normalizedValue.openai?.OPENAI_API_BASE_URL,
				Boolean(normalizedValue.openai?.OPENAI_API_FORCE_MODE),
				'/v1'
			)
		};
		normalizedValue.gemini = {
			...(normalizedValue.gemini ?? {}),
			GEMINI_API_FORCE_MODE: Boolean(normalizedValue.gemini?.GEMINI_API_FORCE_MODE),
			GEMINI_API_BASE_URL: decorateImageProviderUrlInput(
				normalizedValue.gemini?.GEMINI_API_BASE_URL,
				Boolean(normalizedValue.gemini?.GEMINI_API_FORCE_MODE),
				'/v1beta'
			)
		};
		normalizedValue.grok = {
			...(normalizedValue.grok ?? {}),
			GROK_API_BASE_URL: decorateImageProviderUrlInput(
				normalizedValue.grok?.GROK_API_BASE_URL,
				false,
				'/v1'
			)
		};
		return normalizedValue;
	};

	const buildSnapshot = (currentConfig, currentImageGenerationConfig, currentRequiredWorkflowNodes) => ({
		config: currentConfig,
		imageGenerationConfig: currentImageGenerationConfig,
		requiredWorkflowNodes: currentRequiredWorkflowNodes
	});

	const syncBaseline = () => {
		initialSnapshot = cloneSettingsSnapshot(
			buildSnapshot(config, imageGenerationConfig, requiredWorkflowNodes)
		);
	};

	const syncDraftModels = () => {
		if (!config?.enabled) {
			models = null;
			return;
		}

		models = null;
	};

	const clearUnavailableDefaultModel = (nextModels, { silent = false }: { silent?: boolean } = {}) => {
		if (!imageGenerationConfig) {
			return;
		}

		const currentModel = `${imageGenerationConfig.MODEL ?? ''}`.trim();
		if (!currentModel) {
			return;
		}

		const normalizedModels = Array.isArray(nextModels) ? nextModels : [];
		if (normalizedModels.length === 0) {
			return;
		}

		const availableModelIds = new Set(
			normalizedModels
				.map((model) => `${model?.id ?? ''}`.trim())
				.filter(Boolean)
		);

		if (availableModelIds.has(currentModel)) {
			return;
		}

		imageGenerationConfig = {
			...imageGenerationConfig,
			MODEL: ''
		};

		if (!silent) {
			toast.info(
				$i18n.t(
					'The saved default model is not available for the current image engine and has been cleared.'
				)
			);
		}
	};

	const getErrorText = (error) => {
		if (typeof error === 'string') {
			return error;
		}

		if (error instanceof Error) {
			return error.message;
		}

		if (error && typeof error === 'object') {
			if ('detail' in error && typeof error.detail === 'string') {
				return error.detail;
			}

			if ('message' in error && typeof error.message === 'string') {
				return error.message;
			}
		}

		return `${error ?? ''}`;
	};

	const isImageEngineConnectionError = (message: string) =>
		[
			/HTTPConnectionPool/i,
			/Max retries exceeded/i,
			/Failed to establish a new connection/i,
			/Connection refused/i,
			/Connection reset/i,
			/Name or service not known/i,
			/Temporary failure in name resolution/i,
			/timed out/i
		].some((pattern) => pattern.test(message));

	const formatImageSettingsError = (error) => {
		const message = getErrorText(error).trim();

		if (!message) {
			return $i18n.t('Connection failed');
		}

		if (isImageEngineConnectionError(message)) {
			return $i18n.t(
				'Connection to the image generation service failed. Please check that the service is running and the URL is reachable.'
			);
		}

		return $i18n.t(message);
	};

	const getModels = async ({
		afterSave = false,
		silent = false
	}: { afterSave?: boolean; silent?: boolean } = {}) => {
		const nextModels = await getImageGenerationModels(localStorage.token, {
			context: 'settings'
		}).catch((error) => {
			if (!silent) {
				const message = afterSave
					? $i18n.t(
							'Settings were saved, but the model list could not be refreshed. Please check whether the image generation service is running and reachable.'
						)
					: formatImageSettingsError(error);

				if (afterSave) {
					toast.warning(message);
				} else {
					toast.error(message);
				}
			}

			return null;
		});

		models = Array.isArray(nextModels) ? nextModels : null;

		if (Array.isArray(nextModels)) {
			clearUnavailableDefaultModel(nextModels, { silent });
		}

		return nextModels;
	};

	const updateConfigHandler = async ({ refreshModels = true }: { refreshModels?: boolean } = {}) => {
		const res = await updateConfig(localStorage.token, serializeConfigForSave(config)).catch((error) => {
			toast.error(formatImageSettingsError(error));
			return null;
		});

		if (res) {
			config = normalizeLoadedConfig(res);
			await tick();
			if (initialSnapshot) {
				initialSnapshot = {
					...initialSnapshot,
					config: cloneSettingsSnapshot(config)
				};
			}
		}

		if (res) {
			backendConfig.set(await getBackendConfig());
			if (config.enabled) {
				if (refreshModels) {
					void getModels();
				}
			} else {
				models = null;
			}
		}
	};

	const validateJSON = (json) => {
		try {
			const obj = JSON.parse(json);

			if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
				return true;
			}
		} catch (e) {}
		return false;
	};

	const parseComfyUIWorkflow = (workflowJson) => {
		try {
			const workflow = JSON.parse(workflowJson);
			if (workflow && typeof workflow === 'object' && !Array.isArray(workflow)) {
				return workflow;
			}
		} catch (error) {}
		return null;
	};

	const inferComfyUIWorkflowNodes = (workflowJson) => {
		const workflow = parseComfyUIWorkflow(workflowJson);
		if (!workflow) {
			return null;
		}

		const nodes = Object.entries(workflow).map(([id, node]) => ({
			id: `${id}`,
			classType: `${node?.class_type ?? ''}`,
			title: `${node?._meta?.title ?? ''}`,
			inputs: node?.inputs && typeof node.inputs === 'object' ? node.inputs : {}
		}));

		const hasInput = (node, key) => Object.prototype.hasOwnProperty.call(node.inputs ?? {}, key);

		const pickPromptNode = () => {
			return (
				nodes.find((node) => /^CLIPTextEncodeLumina2$/i.test(node.classType) && hasInput(node, 'user_prompt')) ??
				nodes.find((node) => /^CLIPTextEncode/i.test(node.classType) && hasInput(node, 'text')) ??
				nodes.find((node) => /^CLIPTextEncode/i.test(node.classType) && hasInput(node, 'user_prompt')) ??
				nodes.find((node) => /^CLIPTextEncode/i.test(node.classType) && hasInput(node, 'text_g')) ??
				nodes.find((node) =>
					['user_prompt', 'text', 'prompt', 'text_g'].some((key) => hasInput(node, key))
				)
			);
		};

		const pickModelNode = () => {
			return (
				nodes.find((node) => /^UNETLoader$/i.test(node.classType) && hasInput(node, 'unet_name')) ??
				nodes.find((node) => /CheckpointLoader/i.test(node.classType) && hasInput(node, 'ckpt_name')) ??
				nodes.find((node) => hasInput(node, 'unet_name')) ??
				nodes.find((node) => hasInput(node, 'ckpt_name')) ??
				nodes.find((node) => hasInput(node, 'model_name')) ??
				nodes.find((node) => hasInput(node, 'dit_name'))
			);
		};

		const pickSizeNode = () => {
			return (
				nodes.find(
					(node) =>
						hasInput(node, 'width') &&
						hasInput(node, 'height') &&
						/Empty.*Latent|EmptyImage|Latent/i.test(node.classType)
				) ??
				nodes.find(
					(node) =>
						hasInput(node, 'width') &&
						hasInput(node, 'height') &&
						!/^CLIPTextEncode/i.test(node.classType)
				) ??
				nodes.find((node) => hasInput(node, 'width') && hasInput(node, 'height'))
			);
		};

		const pickSamplerNode = () => {
			return (
				nodes.find(
					(node) =>
						hasInput(node, 'steps') &&
						(hasInput(node, 'seed') || hasInput(node, 'noise_seed')) &&
						/KSampler|Sampler/i.test(node.classType)
				) ??
				nodes.find(
					(node) => hasInput(node, 'steps') && (hasInput(node, 'seed') || hasInput(node, 'noise_seed'))
				)
			);
		};

		const promptNode = pickPromptNode();
		const modelNode = pickModelNode();
		const sizeNode = pickSizeNode();
		const samplerNode = pickSamplerNode();

		const inferredByType = {
			prompt: promptNode
				? {
						key: hasInput(promptNode, 'user_prompt')
							? 'user_prompt'
							: hasInput(promptNode, 'text')
								? 'text'
								: hasInput(promptNode, 'prompt')
									? 'prompt'
									: hasInput(promptNode, 'text_g')
										? 'text_g'
										: 'text',
						node_ids: promptNode.id
					}
				: { key: 'text', node_ids: '' },
			model: modelNode
				? {
						key: hasInput(modelNode, 'unet_name')
							? 'unet_name'
							: hasInput(modelNode, 'ckpt_name')
								? 'ckpt_name'
								: hasInput(modelNode, 'model_name')
									? 'model_name'
									: hasInput(modelNode, 'dit_name')
										? 'dit_name'
										: 'unet_name',
						node_ids: modelNode.id
					}
				: { key: 'unet_name', node_ids: '' },
			width: sizeNode ? { key: 'width', node_ids: sizeNode.id } : { key: 'width', node_ids: '' },
			height: sizeNode ? { key: 'height', node_ids: sizeNode.id } : { key: 'height', node_ids: '' },
			steps: samplerNode ? { key: 'steps', node_ids: samplerNode.id } : { key: 'steps', node_ids: '' },
			seed: samplerNode
				? { key: hasInput(samplerNode, 'seed') ? 'seed' : 'noise_seed', node_ids: samplerNode.id }
				: { key: 'seed', node_ids: '' }
		};

		return requiredWorkflowNodes.map((node) => ({
			type: node.type,
			key: inferredByType[node.type]?.key ?? node.key,
			node_ids: inferredByType[node.type]?.node_ids ?? ''
		}));
	};

	const applyAutoDetectedComfyUIWorkflowNodes = (
		workflowJson,
		{ silent = false }: { silent?: boolean } = {}
	) => {
		const inferredNodes = inferComfyUIWorkflowNodes(workflowJson);
		if (!inferredNodes) {
			return false;
		}

		requiredWorkflowNodes = inferredNodes;

		if (!silent) {
			const resolvedCount = inferredNodes.filter((node) => `${node.node_ids ?? ''}`.trim() !== '').length;
			const unresolved = inferredNodes
				.filter((node) => `${node.node_ids ?? ''}`.trim() === '')
				.map((node) => node.type);

			if (resolvedCount > 0) {
				toast.success(
					unresolved.length === 0
						? $i18n.t('Auto-detected ComfyUI workflow node mapping.')
						: $i18n.t(
								'Auto-detected {{count}} ComfyUI workflow mappings. Remaining fields may need manual adjustment: {{types}}.',
								{ count: resolvedCount, types: unresolved.join(', ') }
							)
				);
			}
		}

		return true;
	};

	const handleComfyUIWorkflowUpload = (event: Event) => {
		const input = event.currentTarget as HTMLInputElement | null;
		const file = input?.files?.[0];
		if (!file) {
			return;
		}

		const reader = new FileReader();
		reader.onload = (loadEvent) => {
			config.comfyui.COMFYUI_WORKFLOW = `${loadEvent.target?.result ?? ''}`;
			if (validateJSON(config.comfyui.COMFYUI_WORKFLOW)) {
				applyAutoDetectedComfyUIWorkflowNodes(config.comfyui.COMFYUI_WORKFLOW);
			}
			if (input) {
				input.value = '';
			}
		};
		reader.readAsText(file);
	};

	const getMissingComfyUIWorkflowMappings = (workflowJson, workflowNodes) => {
		try {
			const workflow = parseComfyUIWorkflow(workflowJson);
			if (!workflow) {
				return [];
			}

			const workflowNodeIds = new Set(Object.keys(workflow));
			const missingMappings = [];

			for (const node of workflowNodes ?? []) {
				for (const rawNodeId of node?.node_ids ?? []) {
					const nodeId = `${rawNodeId ?? ''}`.trim();
					if (nodeId !== '' && !workflowNodeIds.has(nodeId)) {
						missingMappings.push(`${node.type}(${nodeId})`);
					}
				}
			}

			return missingMappings;
		} catch (error) {
			return [];
		}
	};

	const saveHandler = async () => {
		loading = true;

			if (config?.comfyui?.COMFYUI_WORKFLOW) {
				if (!validateJSON(config.comfyui.COMFYUI_WORKFLOW)) {
					toast.error($i18n.t('Invalid JSON format for ComfyUI Workflow.'));
					loading = false;
					return;
				}

				applyAutoDetectedComfyUIWorkflowNodes(config.comfyui.COMFYUI_WORKFLOW, {
					silent: true
				});
			}

		if (config?.comfyui?.COMFYUI_WORKFLOW) {
			const nextWorkflowNodes = requiredWorkflowNodes.map((node) => {
				return {
					type: node.type,
					key: node.key,
					node_ids:
						node.node_ids.trim() === '' ? [] : node.node_ids.split(',').map((id) => id.trim())
				};
			});

			const missingMappings = getMissingComfyUIWorkflowMappings(
				config.comfyui.COMFYUI_WORKFLOW,
				nextWorkflowNodes
			);

			if (missingMappings.length > 0) {
				toast.error(
					$i18n.t(
						'Configured ComfyUI workflow node IDs do not exist in the current workflow: {{mappings}}. Please update the workflow node mapping and try again.',
						{ mappings: missingMappings.join(', ') }
					)
				);
				loading = false;
				return;
			}

			config.comfyui.COMFYUI_WORKFLOW_NODES = nextWorkflowNodes;
		}

		const updatedConfig = await updateConfig(
			localStorage.token,
			serializeConfigForSave(config)
		).catch((error) => {
			toast.error(formatImageSettingsError(error));
			loading = false;
			return null;
		});

		const updatedImageGenerationConfig = await updateImageGenerationConfig(
			localStorage.token,
			imageGenerationConfig
		).catch((error) => {
			toast.error(formatImageSettingsError(error));
			loading = false;
			return null;
		});

		if (!updatedConfig || !updatedImageGenerationConfig) {
			loading = false;
			return;
		}

		config = normalizeLoadedConfig(updatedConfig);
		imageGenerationConfig = updatedImageGenerationConfig;

		backendConfig.set(await getBackendConfig());
		if (config.enabled) {
			await getModels({ afterSave: true });
		} else {
			models = null;
		}

		await tick();
		syncBaseline();
		dispatch('save');

		loading = false;
	};

	let snapshot = {
		config: null,
		imageGenerationConfig: null,
		requiredWorkflowNodes
	};
	$: snapshot = buildSnapshot(config, imageGenerationConfig, requiredWorkflowNodes);
	$: isDirty = !!(
		initialSnapshot &&
		config &&
		imageGenerationConfig &&
		!isSettingsSnapshotEqual(snapshot, initialSnapshot)
	);

	onMount(async () => {
		if ($user?.role === 'admin') {
			// 并行加载两个独立配置
			const [res, imageConfigRes] = await Promise.all([
				getConfig(localStorage.token).catch((error) => {
					toast.error(formatImageSettingsError(error));
					return null;
				}),
				getImageGenerationConfig(localStorage.token).catch((error) => {
					toast.error(formatImageSettingsError(error));
					return null;
				})
			]);

				if (res) {
					config = normalizeLoadedConfig(res);
				}

				if (config.comfyui.COMFYUI_WORKFLOW) {
					try {
						config.comfyui.COMFYUI_WORKFLOW = JSON.stringify(
						JSON.parse(config.comfyui.COMFYUI_WORKFLOW),
						null,
						2
					);
				} catch (e) {
					console.log(e);
				}
			}

				const storedWorkflowNodes = config.comfyui.COMFYUI_WORKFLOW_NODES ?? [];
				const storedMappingsMissing =
					config?.comfyui?.COMFYUI_WORKFLOW &&
					getMissingComfyUIWorkflowMappings(
						config.comfyui.COMFYUI_WORKFLOW,
						storedWorkflowNodes
					).length > 0;

				if (storedMappingsMissing) {
					applyAutoDetectedComfyUIWorkflowNodes(config.comfyui.COMFYUI_WORKFLOW, {
						silent: true
					});
				} else {
					requiredWorkflowNodes = requiredWorkflowNodes.map((node) => {
						const n = storedWorkflowNodes.find((n) => n.type === node.type) ?? node;

						return {
							type: n.type,
							key: n.key,
							node_ids: typeof n.node_ids === 'string' ? n.node_ids : n.node_ids.join(',')
						};
					});
				}

				if (imageConfigRes) {
					imageGenerationConfig = {
						IMAGE_ASPECT_RATIO: '1:1',
						IMAGE_RESOLUTION: '1k',
						...imageConfigRes
					};
				}

				if (config.enabled) {
					await getModels({ silent: true });
				}

				await tick();
				syncBaseline();
		}
	});

	const resetChanges = () => {
		if (!initialSnapshot) return;
		config = cloneSettingsSnapshot(initialSnapshot.config);
		imageGenerationConfig = cloneSettingsSnapshot(initialSnapshot.imageGenerationConfig);
		requiredWorkflowNodes = cloneSettingsSnapshot(initialSnapshot.requiredWorkflowNodes);
	};
</script>

<form
	class="flex h-full min-h-0 flex-col text-sm"
	on:submit|preventDefault={async () => {
		await saveHandler();
	}}
>
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		{#if config && imageGenerationConfig}
			<div class="max-w-6xl mx-auto space-y-6">
				<!-- Section A: 基本设置 -->
				<section
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {isDirty
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
						<div class="flex items-center gap-3">
							<div class="glass-icon-badge bg-pink-50 dark:bg-pink-950/30">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 24 24"
									fill="currentColor"
									class="size-[18px] text-pink-500 dark:text-pink-400"
								>
									<path
										fill-rule="evenodd"
										d="M1.5 6a2.25 2.25 0 012.25-2.25h16.5A2.25 2.25 0 0122.5 6v12a2.25 2.25 0 01-2.25 2.25H3.75A2.25 2.25 0 011.5 18V6zM3 16.06V18c0 .414.336.75.75.75h16.5A.75.75 0 0021 18v-1.94l-2.69-2.689a1.5 1.5 0 00-2.12 0l-.88.879.97.97a.75.75 0 11-1.06 1.06l-5.16-5.159a1.5 1.5 0 00-2.12 0L3 16.061zm10.125-7.81a1.125 1.125 0 112.25 0 1.125 1.125 0 01-2.25 0z"
										clip-rule="evenodd"
									/>
								</svg>
							</div>
							<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
								{$i18n.t('Image Settings')}
							</div>
						</div>
						<InlineDirtyActions dirty={isDirty} saving={loading} on:reset={resetChanges} />
					</div>

					<div class="space-y-3">
						<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
							{$i18n.t('Features')}
						</div>
						<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
							<div
								class="flex items-center justify-between glass-item px-4 py-3"
							>
								<div class="text-sm font-medium">
									{$i18n.t('Image Generation (Experimental)')}
								</div>
								<Switch
									bind:state={config.enabled}
									on:change={(e) => {
										const enabled = e.detail;
										if (enabled) {
											if (
												config.engine === 'automatic1111' &&
												config.automatic1111.AUTOMATIC1111_BASE_URL === ''
											) {
												toast.error($i18n.t('AUTOMATIC1111 Base URL is required.'));
												config.enabled = false;
											} else if (
												config.engine === 'comfyui' &&
												config.comfyui.COMFYUI_BASE_URL === ''
											) {
												toast.error($i18n.t('ComfyUI Base URL is required.'));
												config.enabled = false;
											} else if (
												config.engine === 'openai' &&
												config.shared_key_enabled &&
												(!hasConfiguredProviderBaseUrl(config.openai.OPENAI_API_BASE_URL, '/v1') ||
													config.openai.OPENAI_API_KEY === '')
											) {
												toast.error(
													$i18n.t(
														!hasConfiguredProviderBaseUrl(config.openai.OPENAI_API_BASE_URL, '/v1')
															? 'OpenAI API Base URL is required.'
															: 'OpenAI API Key is required.'
													)
												);
												config.enabled = false;
											} else if (
												config.engine === 'gemini' &&
												config.shared_key_enabled &&
												(!hasConfiguredProviderBaseUrl(config.gemini.GEMINI_API_BASE_URL, '/v1beta') ||
													config.gemini.GEMINI_API_KEY === '')
											) {
												toast.error(
													$i18n.t(
														!hasConfiguredProviderBaseUrl(config.gemini.GEMINI_API_BASE_URL, '/v1beta')
															? 'Gemini API Base URL is required.'
															: 'Gemini API Key is required.'
													)
												);
												config.enabled = false;
											} else if (
												config.engine === 'grok' &&
												config.shared_key_enabled &&
												(!hasConfiguredProviderBaseUrl(config.grok.GROK_API_BASE_URL, '/v1') ||
													config.grok.GROK_API_KEY === '')
											) {
												toast.error(
													$i18n.t(
														!hasConfiguredProviderBaseUrl(config.grok.GROK_API_BASE_URL, '/v1')
															? 'Grok API Base URL is required.'
															: 'Grok API Key is required.'
													)
												);
												config.enabled = false;
											}
										}
										syncDraftModels();
									}}
								/>
							</div>

							{#if config.enabled}
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">{$i18n.t('Image Prompt Generation')}</div>
									<Switch bind:state={config.prompt_generation} />
								</div>
							{/if}

							<div
								class="glass-item px-4 py-3"
							>
								<div class="flex items-center justify-between">
									<div class="text-sm font-medium">{$i18n.t('Image Generation Engine')}</div>
									<HaloSelect
										bind:value={config.engine}
										placeholder={$i18n.t('Select Engine')}
										options={[
											{ value: 'openai', label: $i18n.t('Default (Open AI)') },
											{ value: 'comfyui', label: $i18n.t('ComfyUI') },
											{ value: 'automatic1111', label: $i18n.t('Automatic1111') },
											{ value: 'gemini', label: $i18n.t('Gemini') },
											{ value: 'grok', label: $i18n.t('Grok') }
										]}
										className="w-fit"
										on:change={async () => {
											if (!isSharedKeyCapableEngine(config.engine)) {
												config.shared_key_enabled = false;
											}
											if (imageGenerationConfig?.MODEL) {
												imageGenerationConfig = {
													...imageGenerationConfig,
													MODEL: ''
												};
											}
											syncDraftModels();
										}}
									/>
								</div>
							</div>

							{#if config.enabled && isSharedKeyCapableEngine(config.engine)}
								<div
									class="flex items-center justify-between glass-item px-4 py-3"
								>
									<div class="text-sm font-medium">
										{$i18n.t('Allow users to use the workspace shared key')}
									</div>
									<Switch
										bind:state={config.shared_key_enabled}
										on:change={(e) => {
											const enabled = e.detail;
											if (enabled) {
												if (
													config.engine === 'openai' &&
													(!hasConfiguredProviderBaseUrl(config.openai.OPENAI_API_BASE_URL, '/v1') ||
														config.openai.OPENAI_API_KEY === '')
												) {
													toast.error(
														$i18n.t(
															!hasConfiguredProviderBaseUrl(config.openai.OPENAI_API_BASE_URL, '/v1')
																? 'OpenAI API Base URL is required.'
																: 'OpenAI API Key is required.'
														)
													);
													config.shared_key_enabled = false;
													return;
												}
												if (
													config.engine === 'gemini' &&
													(!hasConfiguredProviderBaseUrl(config.gemini.GEMINI_API_BASE_URL, '/v1beta') ||
														config.gemini.GEMINI_API_KEY === '')
												) {
													toast.error(
														$i18n.t(
															!hasConfiguredProviderBaseUrl(config.gemini.GEMINI_API_BASE_URL, '/v1beta')
																? 'Gemini API Base URL is required.'
																: 'Gemini API Key is required.'
														)
													);
													config.shared_key_enabled = false;
													return;
												}
												if (
													config.engine === 'grok' &&
													(!hasConfiguredProviderBaseUrl(config.grok.GROK_API_BASE_URL, '/v1') ||
														config.grok.GROK_API_KEY === '')
												) {
													toast.error(
														$i18n.t(
															!hasConfiguredProviderBaseUrl(config.grok.GROK_API_BASE_URL, '/v1')
																? 'Grok API Base URL is required.'
																: 'Grok API Key is required.'
														)
													);
													config.shared_key_enabled = false;
													return;
												}
											}
										}}
									/>
								</div>
							{/if}
						</div>

						{#if config.enabled && isSharedKeyCapableEngine(config.engine)}
							<div class="text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t(
									'When enabled, users without personal connections can fall back to the workspace shared key.'
								)}
							</div>
						{/if}
					</div>
				</section>

				<!-- Section B: 引擎配置 -->
				<section
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {isDirty
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex items-center gap-3">
						<div class="glass-icon-badge bg-purple-50 dark:bg-purple-950/30">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 24 24"
								fill="currentColor"
								class="size-[18px] text-purple-500 dark:text-purple-400"
							>
								<path
									fill-rule="evenodd"
									d="M11.078 2.25c-.917 0-1.699.663-1.85 1.567L9.05 4.889c-.02.12-.115.26-.297.348a7.493 7.493 0 00-.986.57c-.166.115-.334.126-.45.083L6.3 5.508a1.875 1.875 0 00-2.282.819l-.922 1.597a1.875 1.875 0 00.432 2.385l.84.692c.095.078.17.229.154.43a7.598 7.598 0 000 1.139c.015.2-.059.352-.153.43l-.841.692a1.875 1.875 0 00-.432 2.385l.922 1.597a1.875 1.875 0 002.282.818l1.019-.382c.115-.043.283-.031.45.082.312.214.641.405.985.57.182.088.277.228.297.35l.178 1.071c.151.904.933 1.567 1.85 1.567h1.844c.916 0 1.699-.663 1.85-1.567l.178-1.072c.02-.12.114-.26.297-.349.344-.165.673-.356.985-.57.167-.114.335-.125.45-.082l1.02.382a1.875 1.875 0 002.28-.819l.923-1.597a1.875 1.875 0 00-.432-2.385l-.84-.692c-.095-.078-.17-.229-.154-.43a7.614 7.614 0 000-1.139c-.016-.2.059-.352.153-.43l.84-.692c.708-.582.891-1.59.433-2.385l-.922-1.597a1.875 1.875 0 00-2.282-.818l-1.02.382c-.114.043-.282.031-.449-.083a7.49 7.49 0 00-.985-.57c-.183-.087-.277-.227-.297-.348l-.179-1.072a1.875 1.875 0 00-1.85-1.567h-1.843zM12 15.75a3.75 3.75 0 100-7.5 3.75 3.75 0 000 7.5z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
						<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
							{tr('引擎配置', 'Engine Configuration')}
						</div>
					</div>

					<div class="space-y-4">
						{#if (config?.engine ?? 'automatic1111') === 'automatic1111'}
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('AUTOMATIC1111')}
							</div>

							<!-- Base URL -->
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('AUTOMATIC1111 Base URL')}
								</div>
								<div class="flex gap-2">
									<input
										class="flex-1 w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('Enter URL (e.g. http://127.0.0.1:7860/)')}
										bind:value={config.automatic1111.AUTOMATIC1111_BASE_URL}
									/>
									<button
										class="px-3 glass-item hover:bg-gray-50 dark:hover:bg-gray-750 text-gray-700 dark:text-gray-300 transition"
										type="button"
										on:click={async () => {
											await updateConfigHandler({ refreshModels: false });
											const res = await verifyConfigUrl(localStorage.token).catch((error) => {
												toast.error(formatImageSettingsError(error));
												return null;
											});
											if (res) {
												toast.success($i18n.t('Server connection verified'));
											}
										}}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											class="w-4 h-4"
										>
											<path
												fill-rule="evenodd"
												d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
												clip-rule="evenodd"
											/>
										</svg>
									</button>
								</div>
								<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('Include `--api` flag when running stable-diffusion-webui')}
									<a
										class="text-gray-500 dark:text-gray-400 font-medium underline hover:text-blue-500"
										href="https://github.com/AUTOMATIC1111/stable-diffusion-webui/discussions/3734"
										target="_blank"
									>
										{$i18n.t('(e.g. `sh webui.sh --api`)')}
									</a>
								</div>
							</div>

							<!-- API Auth -->
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('AUTOMATIC1111 Api Auth String')}
								</div>
								<SensitiveInput
									placeholder={$i18n.t('Enter api auth string (e.g. username:password)')}
									bind:value={config.automatic1111.AUTOMATIC1111_API_AUTH}
									required={false}
								/>
								<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('Include `--api-auth` flag when running stable-diffusion-webui')}
									<a
										class="text-gray-500 dark:text-gray-400 font-medium underline hover:text-blue-500"
										href="https://github.com/AUTOMATIC1111/stable-diffusion-webui/discussions/13993"
										target="_blank"
									>
										{$i18n
											.t('(e.g. `sh webui.sh --api --api-auth username_password`)')
											.replace('_', ':')}
									</a>
								</div>
							</div>

							<!-- Sampler + Scheduler -->
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('Set Sampler')}
									</div>
									<Tooltip content={$i18n.t('Enter Sampler (e.g. Euler a)')} placement="top-start">
										<input
											list="sampler-list"
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											placeholder={$i18n.t('Enter Sampler (e.g. Euler a)')}
											bind:value={config.automatic1111.AUTOMATIC1111_SAMPLER}
										/>
										<datalist id="sampler-list">
											{#each samplers ?? [] as sampler}
												<option value={sampler}>{sampler}</option>
											{/each}
										</datalist>
									</Tooltip>
								</div>
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('Set Scheduler')}
									</div>
									<Tooltip content={$i18n.t('Enter Scheduler (e.g. Karras)')} placement="top-start">
										<input
											list="scheduler-list"
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											placeholder={$i18n.t('Enter Scheduler (e.g. Karras)')}
											bind:value={config.automatic1111.AUTOMATIC1111_SCHEDULER}
										/>
										<datalist id="scheduler-list">
											{#each schedulers ?? [] as scheduler}
												<option value={scheduler}>{scheduler}</option>
											{/each}
										</datalist>
									</Tooltip>
								</div>
							</div>

							<!-- CFG Scale -->
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('Set CFG Scale')}
								</div>
								<Tooltip content={$i18n.t('Enter CFG Scale (e.g. 7.0)')} placement="top-start">
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('Enter CFG Scale (e.g. 7.0)')}
										bind:value={config.automatic1111.AUTOMATIC1111_CFG_SCALE}
									/>
								</Tooltip>
							</div>

						{:else if config?.engine === 'comfyui'}
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('ComfyUI')}
							</div>

							<!-- Base URL -->
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('ComfyUI Base URL')}
								</div>
								<div class="flex gap-2">
									<input
										class="flex-1 w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('Enter URL (e.g. http://127.0.0.1:7860/)')}
										bind:value={config.comfyui.COMFYUI_BASE_URL}
									/>
									<button
										class="px-3 glass-item hover:bg-gray-50 dark:hover:bg-gray-750 text-gray-700 dark:text-gray-300 transition"
										type="button"
										on:click={async () => {
											await updateConfigHandler({ refreshModels: false });
											const res = await verifyConfigUrl(localStorage.token).catch((error) => {
												toast.error(formatImageSettingsError(error));
												return null;
											});
											if (res) {
												toast.success($i18n.t('Server connection verified'));
											}
										}}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											class="w-4 h-4"
										>
											<path
												fill-rule="evenodd"
												d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
												clip-rule="evenodd"
											/>
										</svg>
									</button>
								</div>
							</div>

							<!-- API Key -->
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('ComfyUI API Key')}
								</div>
								<SensitiveInput
									placeholder={$i18n.t('sk-1234')}
									bind:value={config.comfyui.COMFYUI_API_KEY}
									required={false}
								/>
							</div>

							<!-- Workflow -->
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('ComfyUI Workflow')}
								</div>

									{#if config.comfyui.COMFYUI_WORKFLOW}
										<textarea
											class="w-full mb-2 py-2 px-3 text-xs dark:text-gray-300 glass-input disabled:text-gray-600 resize-none"
											rows="10"
											bind:value={config.comfyui.COMFYUI_WORKFLOW}
											on:blur={() => {
												if (config?.comfyui?.COMFYUI_WORKFLOW && validateJSON(config.comfyui.COMFYUI_WORKFLOW)) {
													applyAutoDetectedComfyUIWorkflowNodes(
														config.comfyui.COMFYUI_WORKFLOW,
														{ silent: true }
													);
												}
											}}
											required
										/>
									{/if}

									<input
										id="upload-comfyui-workflow-input"
										hidden
										type="file"
										accept=".json"
										on:change={handleComfyUIWorkflowUpload}
									/>
								<button
									class="w-full text-sm font-medium py-2.5 glass-item border-dashed text-gray-600 dark:text-gray-400 text-center transition"
									type="button"
									on:click={() => {
										document.getElementById('upload-comfyui-workflow-input')?.click();
									}}
								>
									{$i18n.t('Click here to upload a workflow.json file.')}
								</button>
								<div class="mt-1.5 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t('Make sure to export a workflow.json file as API format from ComfyUI.')}
								</div>
							</div>

							<!-- Workflow Nodes -->
							{#if config.comfyui.COMFYUI_WORKFLOW}
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">
										{$i18n.t('ComfyUI Workflow Nodes')}
									</div>
									<div class="text-xs flex flex-col gap-1.5">
										{#each requiredWorkflowNodes as node}
											<div
												class="flex w-full items-center border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
											>
												<div class="shrink-0">
													<div
														class="capitalize line-clamp-1 font-medium px-3 py-1.5 w-20 text-center bg-green-500/10 text-green-700 dark:text-green-200"
													>
														{node.type}{node.type === 'prompt' ? '*' : ''}
													</div>
												</div>
												<div class="border-l border-gray-200 dark:border-gray-700">
													<Tooltip content="Input Key (e.g. text, unet_name, steps)">
														<input
															class="py-1.5 px-3 w-24 text-xs text-center bg-transparent outline-hidden"
															placeholder="Key"
															bind:value={node.key}
															required
														/>
													</Tooltip>
												</div>
												<div class="w-full border-l border-gray-200 dark:border-gray-700">
													<Tooltip
														content="Comma separated Node Ids (e.g. 1 or 1,2)"
														placement="top-start"
													>
														<input
															class="w-full py-1.5 px-4 text-xs bg-transparent outline-hidden"
															placeholder="Node Ids"
															bind:value={node.node_ids}
														/>
													</Tooltip>
												</div>
											</div>
										{/each}
									</div>
									<div class="mt-2 text-xs text-right text-gray-400 dark:text-gray-500">
										{$i18n.t('*Prompt node ID(s) are required for image generation')}
									</div>
								</div>
							{/if}

						{:else if config?.engine === 'openai'}
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('OpenAI API Config')}
							</div>
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('API Base URL')}
									</div>
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('API Base URL')}
										bind:value={config.openai.OPENAI_API_BASE_URL}
										on:blur={() => {
											config.openai.OPENAI_API_BASE_URL = normalizeImageProviderUrlInput(
												config.openai.OPENAI_API_BASE_URL,
												'/v1'
											);
										}}
										required
									/>
								</div>
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('API Key')}
									</div>
									<SensitiveInput
										placeholder={$i18n.t('API Key')}
										bind:value={config.openai.OPENAI_API_KEY}
									/>
								</div>
							</div>

						{:else if config?.engine === 'gemini'}
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{$i18n.t('Gemini API Config')}
							</div>
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('API Base URL')}
									</div>
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('API Base URL')}
										bind:value={config.gemini.GEMINI_API_BASE_URL}
										on:blur={() => {
											config.gemini.GEMINI_API_BASE_URL = normalizeImageProviderUrlInput(
												config.gemini.GEMINI_API_BASE_URL,
												'/v1beta'
											);
										}}
										required
									/>
								</div>
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('API Key')}
									</div>
									<SensitiveInput
										placeholder={$i18n.t('API Key')}
										bind:value={config.gemini.GEMINI_API_KEY}
									/>
								</div>
							</div>
						{:else if config?.engine === 'grok'}
							<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
								{tr('Grok 接口配置', 'Grok API Config')}
							</div>
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('API Base URL')}
									</div>
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('API Base URL')}
										bind:value={config.grok.GROK_API_BASE_URL}
										on:blur={() => {
											config.grok.GROK_API_BASE_URL = normalizeImageProviderUrlInput(
												config.grok.GROK_API_BASE_URL,
												'/v1'
											);
										}}
										required
									/>
								</div>
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('API Key')}
									</div>
									<SensitiveInput
										placeholder={$i18n.t('API Key')}
										bind:value={config.grok.GROK_API_KEY}
									/>
								</div>
							</div>
						{/if}
					</div>
				</section>

				<!-- Section C: 生成参数 -->
				<section
					class="scroll-mt-2 p-5 space-y-5 transition-all duration-300 {isDirty
						? 'glass-section glass-section-dirty'
						: 'glass-section'}"
				>
					<div class="flex items-center gap-3">
						<div class="glass-icon-badge bg-indigo-50 dark:bg-indigo-950/30">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 24 24"
								fill="currentColor"
								class="size-[18px] text-indigo-500 dark:text-indigo-400"
							>
								<path
									d="M18.75 12.75h1.5a.75.75 0 000-1.5h-1.5a.75.75 0 000 1.5zM12 6a.75.75 0 01.75-.75h7.5a.75.75 0 010 1.5h-7.5A.75.75 0 0112 6zM12 18a.75.75 0 01.75-.75h7.5a.75.75 0 010 1.5h-7.5A.75.75 0 0112 18zM3.75 6.75h1.5a.75.75 0 100-1.5h-1.5a.75.75 0 000 1.5zM5.25 18.75h-1.5a.75.75 0 010-1.5h1.5a.75.75 0 010 1.5zM3 12a.75.75 0 01.75-.75h7.5a.75.75 0 010 1.5h-7.5A.75.75 0 013 12zM9 3.75a2.25 2.25 0 100 4.5 2.25 2.25 0 000-4.5zM12.75 12a2.25 2.25 0 114.5 0 2.25 2.25 0 01-4.5 0zM9 15.75a2.25 2.25 0 100 4.5 2.25 2.25 0 000-4.5z"
								/>
							</svg>
						</div>
						<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
							{tr('生成参数', 'Generation Parameters')}
						</div>
					</div>

					<div class="space-y-4">
						<div class="text-sm font-medium text-gray-500 dark:text-gray-400 pl-1">
							{tr('默认设置', 'Default Settings')}
						</div>

						{#if config?.enabled}
							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('Set Default Model')}
									</div>
										<HaloSelect
											bind:value={imageGenerationConfig.MODEL}
											options={imageModelOptions}
											placeholder={$i18n.t('Select a model')}
											searchEnabled={true}
											searchPlaceholder={$i18n.t('Search a model')}
											noResultsText={$i18n.t('No results found')}
											allowCustomValue={true}
											customValueLabel={$i18n.t('Use custom model ID')}
											className="w-full"
										/>
										<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
											{$i18n.t(
												'If the model is not listed, search and use its exact model ID directly.'
											)}
										</div>
								</div>
								{#if config?.engine === 'grok'}
									<div class="glass-item p-4">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
											{tr('图片比例', 'Aspect Ratio')}
										</div>
										<HaloSelect
											bind:value={imageGenerationConfig.IMAGE_ASPECT_RATIO}
											options={GROK_ASPECT_RATIO_OPTIONS}
											className="w-full"
										/>
									</div>
								{:else}
									<div class="glass-item p-4">
										<div class="flex items-center justify-between gap-3 mb-1.5">
											<div class="text-xs font-medium text-gray-500 dark:text-gray-400">
												{$i18n.t('Set Image Size')}
											</div>
											<div class="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300">
												{imageGenerationConfig.IMAGE_SIZE === 'auto' ? tr('自动', '自动') : tr('固定尺寸', '固定尺寸')}
											</div>
										</div>
										<HaloSelect
											bind:value={imageGenerationConfig.IMAGE_SIZE}
											options={IMAGE_SIZE_OPTIONS}
											placeholder={tr('选择图片尺寸', 'Select image size')}
											searchEnabled={true}
											searchPlaceholder={tr('输入自定义尺寸', '输入自定义尺寸')}
											noResultsText={tr('没有匹配的尺寸', 'No matching size')}
											allowCustomValue={true}
											customValueLabel={tr('使用自定义尺寸', 'Use custom size')}
											className="w-full"
											contentClassName="!max-w-[24rem]"
										/>
										<div class="mt-2 text-xs leading-5 text-gray-400 dark:text-gray-500">
											{tr('选择自动时，图片尺寸由模型或上游服务决定。', '选择自动时，图片尺寸由模型或上游服务决定。')}
										</div>
									</div>
								{/if}
							</div>

							<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
								{#if config?.engine === 'grok'}
									<div class="glass-item p-4">
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
											{tr('清晰度', 'Resolution')}
										</div>
										<HaloSelect
											bind:value={imageGenerationConfig.IMAGE_RESOLUTION}
											options={GROK_RESOLUTION_OPTIONS}
											className="w-full"
										/>
									</div>
								{:else}
									<div
										class="glass-item p-4"
									>
										<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
											{$i18n.t('Set Steps')}
										</div>
										<Tooltip content={$i18n.t('Enter Number of Steps (e.g. 50)')} placement="top-start">
											<input
												class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
												placeholder={$i18n.t('Enter Number of Steps (e.g. 50)')}
												bind:value={imageGenerationConfig.IMAGE_STEPS}
												required
											/>
										</Tooltip>
									</div>
								{/if}
								<div
									class="glass-item p-4"
								>
									<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
										{$i18n.t('Model Filter Regex')}
									</div>
									<Tooltip
										content={$i18n.t('Regex pattern to filter image models (leave empty to show all)')}
										placement="top-start"
									>
										<input
											class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
											placeholder={$i18n.t('e.g. dall-e|gpt-image')}
											bind:value={imageGenerationConfig.IMAGE_MODEL_FILTER_REGEX}
										/>
									</Tooltip>
								</div>
							</div>
						{:else}
							<div
								class="glass-item p-4"
							>
								<div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">
									{$i18n.t('Model Filter Regex')}
								</div>
								<Tooltip
									content={$i18n.t('Regex pattern to filter image models (leave empty to show all)')}
									placement="top-start"
								>
									<input
										class="w-full py-2 px-3 text-sm dark:text-gray-300 glass-input"
										placeholder={$i18n.t('e.g. dall-e|gpt-image')}
										bind:value={imageGenerationConfig.IMAGE_MODEL_FILTER_REGEX}
									/>
								</Tooltip>
							</div>
						{/if}
					</div>
				</section>
			</div>
		{/if}
	</div>

</form>
