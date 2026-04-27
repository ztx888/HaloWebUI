<script lang="ts">
	import { onMount, getContext, tick } from 'svelte';
	import {
		models,
		tools,
		functions,
		skills as skillsStore,
		knowledge as knowledgeCollections,
		user
	} from '$lib/stores';

	import AdvancedParams from '$lib/components/chat/Settings/Advanced/AdvancedParams.svelte';
	import Tags from '$lib/components/common/Tags.svelte';
	import Knowledge from '$lib/components/workspace/Models/Knowledge.svelte';
	import ToolsSelector from '$lib/components/workspace/Models/ToolsSelector.svelte';
	import SkillsSelector from '$lib/components/workspace/Models/SkillsSelector.svelte';
	import FiltersSelector from '$lib/components/workspace/Models/FiltersSelector.svelte';
	import ActionsSelector from '$lib/components/workspace/Models/ActionsSelector.svelte';
	import Capabilities from '$lib/components/workspace/Models/Capabilities.svelte';
	import BuiltinToolConfig from '$lib/components/workspace/Models/BuiltinToolConfig.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import InlineDirtyActions from '$lib/components/admin/Settings/InlineDirtyActions.svelte';
	import { DEFAULT_MODEL_ICON, resolveModelIcon } from '$lib/utils/model-icons';
	import { getModelBaseName, getModelChatDisplayName } from '$lib/utils/model-display';
	import {
		findModelByIdentity,
		findModelByRef,
		getModelRef,
		getModelSelectionId,
		resolveModelSelectionId
	} from '$lib/utils/model-identity';
	import { cloneSettingsSnapshot, isSettingsSnapshotEqual } from '$lib/utils/settings-dirty';
	import { getTools } from '$lib/apis/tools';
	import { getFunctions } from '$lib/apis/functions';
	import { getKnowledgeBases } from '$lib/apis/knowledge';
	import { getSkills } from '$lib/apis/skills';
	import AccessControl from '../common/AccessControl.svelte';
	import { toast } from 'svelte-sonner';
	import HaloSelect from '$lib/components/common/HaloSelect.svelte';
	import { translateWithDefault } from '$lib/i18n';

	const i18n = getContext('i18n');
	const tr = (key: string, defaultValue: string) =>
		translateWithDefault($i18n, key, defaultValue);

	export let onSubmit: Function;
	export let onBack: null | Function = null;

	export let model = null;
	export let edit = false;

	export let preset = true;

	let loading = false;
	let success = false;
	let saving = false;

	let filesInputElement;
	let inputFiles;

	let showAdvanced = false;
	let showPreview = false;

	let loaded = false;

	// Tab state
	type ModelEditorTab = 'profile' | 'behavior' | 'integrations' | 'capabilities';
	let selectedTab: ModelEditorTab = 'profile';

	const tabMeta: Array<{
		key: ModelEditorTab;
		titleKey: string;
		titleDefault: string;
		descKey: string;
		descDefault: string;
		badgeColor: string;
		iconColor: string;
		iconPaths: string[];
	}> = [
		{
			key: 'profile',
			titleKey: '模型信息',
			titleDefault: 'Model Info',
			descKey: '头像、名称、基础模型、描述、标签、访问控制',
			descDefault: 'Avatar, name, base model, description, tags, and access control',
			badgeColor: 'bg-amber-50 dark:bg-amber-950/30',
			iconColor: 'text-amber-500 dark:text-amber-400',
			iconPaths: [
				'M18.685 19.097A9.723 9.723 0 0 0 21.75 12c0-5.385-4.365-9.75-9.75-9.75S2.25 6.615 2.25 12a9.723 9.723 0 0 0 3.065 7.097A9.716 9.716 0 0 0 12 21.75a9.716 9.716 0 0 0 6.685-2.653Zm-12.54-1.285A7.486 7.486 0 0 1 12 15a7.486 7.486 0 0 1 5.855 2.812A8.224 8.224 0 0 1 12 20.25a8.224 8.224 0 0 1-5.855-2.438ZM15.75 9a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z'
			]
		},
		{
			key: 'behavior',
			titleKey: '行为设置',
			titleDefault: 'Behavior Settings',
			descKey: '系统提示词、高级参数、提示建议',
			descDefault: 'System prompt, advanced parameters, and suggestion prompts',
			badgeColor: 'bg-indigo-50 dark:bg-indigo-950/30',
			iconColor: 'text-indigo-500 dark:text-indigo-400',
			iconPaths: [
				'M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z',
				'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'
			]
		},
		{
			key: 'integrations',
			titleKey: '集成配置',
			titleDefault: 'Integrations',
			descKey: '知识库、工具、技能、过滤器、动作、内置工具',
			descDefault: 'Knowledge bases, tools, skills, filters, actions, and built-in tools',
			badgeColor: 'bg-cyan-50 dark:bg-cyan-950/30',
			iconColor: 'text-cyan-500 dark:text-cyan-400',
			iconPaths: [
				'M14.25 6.087c0-.355.186-.676.401-.959.221-.29.349-.634.349-1.003 0-1.036-1.007-1.875-2.25-1.875s-2.25.84-2.25 1.875c0 .369.128.713.349 1.003.215.283.401.604.401.959v0a.64.64 0 0 1-.657.643 48.39 48.39 0 0 1-4.163-.3c.186 1.613.293 3.25.315 4.907a.656.656 0 0 1-.658.663v0c-.355 0-.676-.186-.959-.401a1.647 1.647 0 0 0-1.003-.349c-1.036 0-1.875 1.007-1.875 2.25s.84 2.25 1.875 2.25c.369 0 .713-.128 1.003-.349.283-.215.604-.401.959-.401v0c.31 0 .555.26.532.57a48.039 48.039 0 0 1-.642 5.056c1.518.19 3.058.309 4.616.354a.64.64 0 0 0 .657-.643v0c0-.355-.186-.676-.401-.959a1.647 1.647 0 0 1-.349-1.003c0-1.035 1.008-1.875 2.25-1.875 1.243 0 2.25.84 2.25 1.875 0 .369-.128.713-.349 1.003-.215.283-.4.604-.4.959v0c0 .333.277.599.61.58a48.1 48.1 0 0 0 5.427-.63 48.05 48.05 0 0 0 .582-4.717.532.532 0 0 0-.533-.57v0c-.355 0-.676.186-.959.401-.29.221-.634.349-1.003.349-1.035 0-1.875-1.007-1.875-2.25s.84-2.25 1.875-2.25c.37 0 .713.128 1.003.349.283.215.604.401.96.401v0a.656.656 0 0 0 .658-.663 48.422 48.422 0 0 0-.37-5.36c-1.886.342-3.81.574-5.766.689a.578.578 0 0 1-.61-.58v0Z'
			]
		},
		{
			key: 'capabilities',
			titleKey: '能力与预览',
			titleDefault: 'Capabilities & Preview',
			descKey: '模型能力开关、JSON 配置预览',
			descDefault: 'Model capability toggles and JSON config preview',
			badgeColor: 'bg-emerald-50 dark:bg-emerald-950/30',
			iconColor: 'text-emerald-500 dark:text-emerald-400',
			iconPaths: [
				'M11.48 3.499a.562.562 0 0 1 1.04 0l2.125 5.111a.563.563 0 0 0 .475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 0 0-.182.557l1.285 5.385a.562.562 0 0 1-.84.61l-4.725-2.885a.562.562 0 0 0-.586 0L6.982 20.54a.562.562 0 0 1-.84-.61l1.285-5.386a.562.562 0 0 0-.182-.557l-4.204-3.602a.562.562 0 0 1 .321-.988l5.518-.442a.563.563 0 0 0 .475-.345L11.48 3.5Z'
			]
		}
	];

	$: allTabs = tabMeta.map((t) => ({
		...t,
		title: tr(t.titleKey, t.titleDefault),
		description: tr(t.descKey, t.descDefault)
	}));

	$: activeTab = allTabs.find((t) => t.key === selectedTab) ?? allTabs[0];

	// ///////////
	// model
	// ///////////

	let id = '';
	let name = '';
	let defaultBaseName: string | null = null;

	let enableDescription = true;

	$: if (!edit) {
		if (name) {
			id = name
				.replace(/\s+/g, '-')
				.replace(/[^a-zA-Z0-9-]/g, '')
				.toLowerCase();
		}
	}

	let info = {
		id: '',
		base_model_id: null,
		name: '',
		meta: {
			profile_image_url: DEFAULT_MODEL_ICON,
			description: '',
			suggestion_prompts: null,
			tags: []
		},
		params: {
			system: ''
		}
	};

	const resetProfileImageToMatchedIcon = () => {
		const resolved = getMatchedProfileImage(info?.base_model_id ?? null);

		info.meta.profile_image_url = resolved ?? DEFAULT_MODEL_ICON;
	};

	const getMatchedProfileImage = (baseModelId: string | null) => {
		const baseModel = baseModelId ? findModelByIdentity($models, baseModelId) : null;

		const resolved = baseModel
			? resolveModelIcon(baseModel as any)
			: resolveModelIcon({
					id,
					name,
					base_model_id: baseModelId,
					owned_by: (model as any)?.owned_by
				} as any);

		return resolved ?? DEFAULT_MODEL_ICON;
	};

	const isUserCustomProfileImage = (icon?: string | null) =>
		!!(
			icon &&
			icon !== DEFAULT_MODEL_ICON &&
			(icon.startsWith('data:') ||
				icon.startsWith('blob:') ||
				icon.startsWith('/cache/') ||
				icon.startsWith('/api/v1/files/'))
		);

	let trackedBaseModelId: string | null = null;

	const syncProfileImageWithBaseModel = (nextBaseModelId: string | null) => {
		const currentIcon = info?.meta?.profile_image_url ?? DEFAULT_MODEL_ICON;
		const previousAutoIcon = getMatchedProfileImage(trackedBaseModelId);

		if (isUserCustomProfileImage(currentIcon)) {
			trackedBaseModelId = nextBaseModelId;
			return;
		}

		if (currentIcon === DEFAULT_MODEL_ICON || currentIcon === previousAutoIcon) {
			info.meta.profile_image_url = getMatchedProfileImage(nextBaseModelId);
		}

		trackedBaseModelId = nextBaseModelId;
	};

	let params = {
		system: ''
	};
	let capabilities = {
		vision: true,
		usage: undefined,
		citations: true
	};

	let knowledge = [];
	let toolIds = [];
	let skillIds = [];
	let filterIds = [];
	let actionIds = [];

	const normalizeAccessControl = (value) =>
		value === null
			? null
			: {
					read: {
						group_ids: value?.read?.group_ids ?? [],
						user_ids: value?.read?.user_ids ?? []
					},
					write: {
						group_ids: value?.write?.group_ids ?? [],
						user_ids: value?.write?.user_ids ?? []
					}
				};

	let accessControl = normalizeAccessControl({});
	let builtinToolConfig: Record<string, boolean> = {};
	$: canManageAcl = !edit || $user?.role === 'admin' || model?.user_id === $user?.id;

	const DEFAULT_REASONING_EFFORT_VALUE = '__default__';

	const normalizeReasoningEffortValue = (value: unknown): string | null => {
		if (value === null || value === undefined) {
			return null;
		}

		const normalized = String(value).trim().toLowerCase();
		return normalized === '' ? null : normalized;
	};

	const normalizeEditorParams = (value: Record<string, any> | null | undefined = {}) => {
		const next = {
			system: '',
			...cloneSettingsSnapshot(value ?? {})
		};

		next.stop = next?.stop
			? (typeof next.stop === 'string' ? next.stop.split(',') : (next?.stop ?? [])).join(',')
			: null;

		return next;
	};

	$: reasoningEffortSelectOptions = [
		{ value: DEFAULT_REASONING_EFFORT_VALUE, label: $i18n.t('Default') },
		{ value: 'none', label: $i18n.t('Off') },
		{ value: 'low', label: 'Low' },
		{ value: 'medium', label: 'Medium' },
		{ value: 'high', label: 'High' },
		{ value: 'xhigh', label: 'XHigh' },
		{ value: 'max', label: 'Max' }
	];

	$: reasoningEffortSelectValue =
		normalizeReasoningEffortValue(params?.reasoning_effort) ?? DEFAULT_REASONING_EFFORT_VALUE;

	const updateReasoningEffort = (nextValue: string | null | undefined) => {
		const normalized = normalizeReasoningEffortValue(nextValue);
		params = {
			...params,
			reasoning_effort:
				normalized === null || normalized === DEFAULT_REASONING_EFFORT_VALUE ? null : normalized
		};
	};

	const buildDraftModelInfo = () => ({
		...cloneSettingsSnapshot(info),
		id,
		name,
		access_control: cloneSettingsSnapshot(accessControl),
		meta: {
			...cloneSettingsSnapshot(info?.meta ?? {}),
			capabilities: cloneSettingsSnapshot(capabilities)
		},
		params: normalizeEditorParams(params)
	});

	const buildCapabilitiesSnapshot = () =>
		Object.keys(capabilities ?? {}).reduce<Record<string, boolean>>((acc, key) => {
			acc[key] = Boolean(capabilities[key]);
			return acc;
		}, {});

	// Dirty tracking
	const buildSnapshot = () => ({
		name,
		id,
		info: buildDraftModelInfo(),
		params: normalizeEditorParams(params),
		capabilities: buildCapabilitiesSnapshot(),
		knowledge: cloneSettingsSnapshot(knowledge),
		toolIds: cloneSettingsSnapshot(toolIds),
		skillIds: cloneSettingsSnapshot(skillIds),
		filterIds: cloneSettingsSnapshot(filterIds),
		actionIds: cloneSettingsSnapshot(actionIds),
		accessControl: cloneSettingsSnapshot(accessControl),
		builtinToolConfig: cloneSettingsSnapshot(builtinToolConfig),
		enableDescription
	});

	let initialSnapshot: ReturnType<typeof buildSnapshot> | null = null;
	let snapshot: ReturnType<typeof buildSnapshot> | null = null;
	let dirty = false;

	$: {
		name;
		id;
		info;
		params;
		capabilities;
		knowledge;
		toolIds;
		skillIds;
		filterIds;
		actionIds;
		accessControl;
		builtinToolConfig;
		enableDescription;
		snapshot = buildSnapshot();
	}
	$: dirty =
		loaded &&
		!!snapshot &&
		!!initialSnapshot &&
		!isSettingsSnapshotEqual(snapshot, initialSnapshot);

	function handleReset() {
		if (!initialSnapshot) return;
		const snap = cloneSettingsSnapshot(initialSnapshot);
		name = snap.name;
		id = snap.id;
		info = snap.info;
		params = snap.params;
		capabilities = snap.capabilities;
		knowledge = snap.knowledge;
		toolIds = snap.toolIds;
		skillIds = snap.skillIds;
		filterIds = snap.filterIds;
		actionIds = snap.actionIds;
		accessControl = snap.accessControl;
		builtinToolConfig = snap.builtinToolConfig;
		enableDescription = snap.enableDescription;
	}

	const deriveDefaultBaseName = (m: any): string | null => {
		const explicit =
			(m?.default_name ?? m?.defaultName ?? m?.source_name ?? m?.sourceName ?? '')
				?.toString?.()
				.trim?.() ?? '';
		if (explicit) return explicit;

		const openaiId = m?.openai?.id ?? m?.openai?.model ?? m?.openai?.name;
		if (openaiId) return openaiId.toString();

		const ollamaId = m?.ollama?.model ?? m?.ollama?.name;
		if (ollamaId) return ollamaId.toString();

		const geminiId = m?.gemini?.name ?? m?.gemini?.id ?? m?.gemini?.model;
		if (geminiId) return geminiId.toString().replace(/^models\//, '');

		const originalId = m?.originalId ?? m?.original_id;
		if (originalId) return originalId.toString();

		const base = getModelBaseName(m);
		return base ? base.toString() : null;
	};

	const isBaseModelOption = (candidate: any) =>
		(candidate?.info?.base_model_id ?? candidate?.base_model_id ?? null) == null;

	const findBaseModelOption = (baseModelId: string | null | undefined, sourceModel: any = null) => {
		const candidates = ($models ?? []).filter((m) => isBaseModelOption(m));
		const ids = [
			baseModelId,
			baseModelId ? `${baseModelId}:latest` : '',
			resolveModelSelectionId($models, baseModelId ?? ''),
			sourceModel?.meta?.base_selection_id
		].filter((id) => typeof id === 'string' && id.trim() !== '');

		const direct = candidates.find(
			(m) => ids.includes(getModelSelectionId(m)) || ids.includes(m.id)
		);
		if (direct) return direct;

		const baseModelRef = sourceModel?.meta?.base_model_ref ?? sourceModel?.meta?.model_ref ?? null;
		return findModelByRef(candidates, baseModelRef, sourceModel?.meta?.base_selection_id ?? baseModelId);
	};

	const addUsage = (base_model_id) => {
		const baseModel = findModelByIdentity($models, base_model_id);

		if (baseModel) {
			if (baseModel.owned_by === 'openai') {
				capabilities.usage = baseModel?.meta?.capabilities?.usage ?? false;
			} else {
				delete capabilities.usage;
			}
			capabilities = capabilities;
		}
	};

	const submitHandler = async () => {
		loading = true;
		saving = true;

		const modelInfo = buildDraftModelInfo();

		if (id.trim() === '') {
			toast.error('Model ID is required.');
			loading = false;
			saving = false;
			return;
		}

		if (name.trim() === '') {
			toast.error('Model Name is required.');
			loading = false;
			saving = false;
			return;
		}

		if (preset && !modelInfo.base_model_id) {
			toast.error($i18n.t('Please select a base model before saving this assistant.'));
			selectedTab = 'profile';
			loading = false;
			saving = false;
			return;
		}
		if (preset && modelInfo.base_model_id) {
			const baseModel = findBaseModelOption(modelInfo.base_model_id, modelInfo);
			const baseModelRef = getModelRef(baseModel);
			modelInfo.base_model_id = baseModel
				? getModelSelectionId(baseModel)
				: modelInfo.base_model_id;
			if (baseModelRef) {
				modelInfo.meta.base_model_ref = baseModelRef;
				modelInfo.meta.base_selection_id = getModelSelectionId(baseModel);
			} else if (!modelInfo.meta.base_model_ref && !modelInfo.meta.base_selection_id) {
				delete modelInfo.meta.base_model_ref;
				delete modelInfo.meta.base_selection_id;
			}
		}

		if (Object.keys(builtinToolConfig).length > 0) {
			modelInfo.meta.builtin_tool_config = builtinToolConfig;
		} else {
			delete modelInfo.meta.builtin_tool_config;
		}

		if (enableDescription) {
			modelInfo.meta.description =
				(modelInfo.meta.description ?? '').trim() === '' ? null : modelInfo.meta.description;
		} else {
			modelInfo.meta.description = null;
		}

		if (knowledge.length > 0) {
			modelInfo.meta.knowledge = knowledge;
		} else {
			if (modelInfo.meta.knowledge) {
				delete modelInfo.meta.knowledge;
			}
		}

		if (toolIds.length > 0) {
			modelInfo.meta.toolIds = toolIds;
		} else {
			if (modelInfo.meta.toolIds) {
				delete modelInfo.meta.toolIds;
			}
		}

		if (skillIds.length > 0) {
			modelInfo.meta.skillIds = skillIds;
		} else {
			if (modelInfo.meta.skillIds) {
				delete modelInfo.meta.skillIds;
			}
		}

		if (filterIds.length > 0) {
			modelInfo.meta.filterIds = filterIds;
		} else {
			if (modelInfo.meta.filterIds) {
				delete modelInfo.meta.filterIds;
			}
		}

		if (actionIds.length > 0) {
			modelInfo.meta.actionIds = actionIds;
		} else {
			if (modelInfo.meta.actionIds) {
				delete modelInfo.meta.actionIds;
			}
		}

		modelInfo.params.stop = modelInfo.params.stop
			? modelInfo.params.stop.split(',').filter((s) => s.trim())
			: null;
		Object.keys(modelInfo.params).forEach((key) => {
			if (modelInfo.params[key] === '' || modelInfo.params[key] === null) {
				delete modelInfo.params[key];
			}
		});

		const saved = await onSubmit(modelInfo);
		if (!saved) {
			loading = false;
			saving = false;
			success = false;
			return;
		}

		// Update snapshot after successful save
		initialSnapshot = cloneSettingsSnapshot(buildSnapshot());

		loading = false;
		saving = false;
		success = false;
	};

	onMount(async () => {
		await tools.set(await getTools(localStorage.token));
		await functions.set(await getFunctions(localStorage.token));
		await knowledgeCollections.set(await getKnowledgeBases(localStorage.token));
		await skillsStore.set((await getSkills(localStorage.token)) ?? []);

		// Scroll to top 'workspace-container' element
		const workspaceContainer = document.getElementById('workspace-container');
		if (workspaceContainer) {
			workspaceContainer.scrollTop = 0;
		}

		if (model) {
			defaultBaseName = deriveDefaultBaseName(model);

			name = getModelBaseName(model);
			await tick();

			id = model.id;

			enableDescription = model?.meta?.description !== null;

			if (model.base_model_id) {
				const base_model = findBaseModelOption(model.base_model_id, model);

				console.log('base_model', base_model);

				if (base_model) {
					model.base_model_id = getModelSelectionId(base_model);
				}
			}

			params = normalizeEditorParams(model?.params);

			toolIds = model?.meta?.toolIds ?? [];
			skillIds = model?.meta?.skillIds ?? [];
			filterIds = model?.meta?.filterIds ?? [];
			actionIds = model?.meta?.actionIds ?? [];
			knowledge = (model?.meta?.knowledge ?? []).map((item) => {
				if (item?.collection_name) {
					return {
						id: item.collection_name,
						name: item.name,
						legacy: true
					};
				} else if (item?.collection_names) {
					return {
						name: item.name,
						type: 'collection',
						collection_names: item.collection_names,
						legacy: true
					};
				} else {
					return item;
				}
			});
			capabilities = { ...capabilities, ...(model?.meta?.capabilities ?? {}) };
			builtinToolConfig = { ...(model?.meta?.builtin_tool_config ?? {}) };

			if ('access_control' in model) {
				accessControl = normalizeAccessControl(model.access_control);
			} else {
				accessControl = normalizeAccessControl({});
			}

			trackedBaseModelId = model.base_model_id ?? null;

			info = {
				...info,
				...JSON.parse(
					JSON.stringify(
						model
							? model
							: {
									id: model.id,
									name: model.name
								}
					)
				)
			};
		}

		loaded = true;
		await tick();
		trackedBaseModelId = info?.base_model_id ?? null;
		initialSnapshot = cloneSettingsSnapshot(buildSnapshot());
	});
</script>

{#if loaded}
	<input
		bind:this={filesInputElement}
		bind:files={inputFiles}
		type="file"
		hidden
		accept="image/*"
		on:change={() => {
			let reader = new FileReader();
			reader.onload = (event) => {
				let originalImageUrl = `${event.target.result}`;

				const img = new Image();
				img.src = originalImageUrl;

				img.onload = function () {
					const canvas = document.createElement('canvas');
					const ctx = canvas.getContext('2d');

					const aspectRatio = img.width / img.height;

					let newWidth, newHeight;
					if (aspectRatio > 1) {
						newWidth = 250 * aspectRatio;
						newHeight = 250;
					} else {
						newWidth = 250;
						newHeight = 250 / aspectRatio;
					}

					canvas.width = 250;
					canvas.height = 250;

					const offsetX = (250 - newWidth) / 2;
					const offsetY = (250 - newHeight) / 2;

					ctx.drawImage(img, offsetX, offsetY, newWidth, newHeight);

					const compressedSrc = canvas.toDataURL();
					info.meta.profile_image_url = compressedSrc;

					inputFiles = null;
					filesInputElement.value = '';
				};
			};

			if (
				inputFiles &&
				inputFiles.length > 0 &&
				['image/gif', 'image/webp', 'image/jpeg', 'image/png', 'image/svg+xml'].includes(
					inputFiles[0]['type']
				)
			) {
				reader.readAsDataURL(inputFiles[0]);
			} else {
				console.log(`Unsupported File Type '${inputFiles[0]['type']}'.`);
				inputFiles = null;
			}
		}}
	/>

	{#if !edit || (edit && model)}
		<form
			class="flex flex-col h-full min-h-0 text-sm"
			on:submit|preventDefault={() => {
				submitHandler();
			}}
		>
			<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
				<div class="max-w-6xl mx-auto space-y-6">

					<!-- ==================== Hero Section ==================== -->
					<section class="glass-section p-5 space-y-5">
						<div class="@container flex flex-col gap-5">
							<div class="flex flex-col gap-4 @[64rem]:flex-row @[64rem]:items-start @[64rem]:justify-between">
								<div class="min-w-0 @[64rem]:flex-1">
									<!-- Breadcrumb -->
									<div class="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-full border border-gray-200/80 bg-white/80 px-3.5 text-xs font-medium leading-none text-gray-600 dark:border-gray-700/80 dark:bg-gray-900/70 dark:text-gray-300">
										{#if onBack}
											<button type="button" class="leading-none text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors" on:click={() => onBack()}>
												<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
													<path fill-rule="evenodd" d="M17 10a.75.75 0 01-.75.75H5.612l4.158 3.96a.75.75 0 11-1.04 1.08l-5.5-5.25a.75.75 0 010-1.08l5.5-5.25a.75.75 0 111.04 1.08L5.612 9.25H16.25A.75.75 0 0117 10z" clip-rule="evenodd" />
												</svg>
											</button>
										{/if}
										<a href="/workspace/models" class="leading-none text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">{$i18n.t('Models')}</a>
										<span class="leading-none text-gray-300 dark:text-gray-600">/</span>
										<span class="leading-none text-gray-900 dark:text-white">{edit ? $i18n.t('Edit') : $i18n.t('Create')}</span>
									</div>

									<!-- Icon badge + title -->
									{#if activeTab}
										<div class="mt-3 flex items-start gap-3">
											<div class="glass-icon-badge {activeTab.badgeColor}">
												<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-[18px] {activeTab.iconColor}">
													{#each activeTab.iconPaths as pathD}
														<path fill-rule="evenodd" d={pathD} clip-rule="evenodd" />
													{/each}
												</svg>
											</div>
											<div class="min-w-0">
												<div class="flex items-center gap-3">
													<div class="text-base font-semibold text-gray-800 dark:text-gray-100">
														{activeTab.title}
													</div>
													<InlineDirtyActions dirty={dirty} {saving} saveAsSubmit={true} on:reset={handleReset} />
												</div>
												<p class="mt-1 text-xs text-gray-400 dark:text-gray-500">{activeTab.description}</p>
											</div>
										</div>
									{/if}
								</div>

								<!-- Tab bar -->
								<div class="inline-flex max-w-full flex-wrap items-center gap-2 self-start rounded-2xl bg-gray-100 p-1 dark:bg-gray-850 @[64rem]:ml-auto @[64rem]:mt-11 @[64rem]:flex-nowrap @[64rem]:justify-end @[64rem]:shrink-0">
									{#each allTabs as tab (tab.key)}
										<button
											type="button"
											class="flex shrink-0 items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all {selectedTab === tab.key
												? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white'
												: 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'}"
											on:click={() => {
												selectedTab = tab.key;
											}}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-4">
												{#each tab.iconPaths as pathD}
													<path fill-rule="evenodd" d={pathD} clip-rule="evenodd" />
												{/each}
											</svg>
											<span>{tab.title}</span>
										</button>
									{/each}
								</div>
							</div>
						</div>
					</section>

					<!-- ==================== Content Section ==================== -->
					<section class="glass-section p-5 space-y-3 transition-all duration-300 {dirty ? 'glass-section-dirty' : ''}">

						<!-- ===== Profile Tab ===== -->
						{#if selectedTab === 'profile'}
							<!-- Profile Image -->
							<div class="glass-item p-5">
								<div class="flex flex-col sm:flex-row items-center gap-5">
									<button
										type="button"
										class="relative group shrink-0 rounded-2xl overflow-hidden"
										on:click={() => {
											filesInputElement.click();
										}}
									>
										{#if info.meta.profile_image_url}
											<img
												src={info.meta.profile_image_url}
												alt="model profile"
												class="size-28 object-cover"
											/>
										{:else}
											<img
												src={DEFAULT_MODEL_ICON}
												alt="model profile"
												class="size-28 object-cover"
											/>
										{/if}
										<div class="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all flex items-center justify-center">
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-6 text-white opacity-0 group-hover:opacity-100 transition-opacity">
												<path d="M12 9a3.75 3.75 0 1 0 0 7.5A3.75 3.75 0 0 0 12 9Z" />
												<path fill-rule="evenodd" d="M9.344 3.071a49.52 49.52 0 0 1 5.312 0c.967.052 1.83.585 2.332 1.39l.821 1.317c.2.32.58.532.996.532h.176c1.594 0 2.769 1.388 2.769 2.94v7.5c0 1.552-1.175 2.94-2.769 2.94H5.019c-1.594 0-2.769-1.388-2.769-2.94v-7.5c0-1.552 1.175-2.94 2.769-2.94h.176c.416 0 .796-.213.996-.532l.82-1.317a2.636 2.636 0 0 1 2.333-1.39ZM12 7.5a5.25 5.25 0 1 0 0 10.5 5.25 5.25 0 0 0 0-10.5Z" clip-rule="evenodd" />
											</svg>
										</div>
									</button>
									<div class="flex flex-col gap-2 text-center sm:text-left">
										<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
											{$i18n.t('Profile Image')}
										</div>
										<div class="text-xs text-gray-400 dark:text-gray-500">
											{$i18n.t('Click to upload a custom image')}
										</div>
										<button
											type="button"
											class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors w-fit"
											on:click={() => {
												resetProfileImageToMatchedIcon();
											}}
										>
											{$i18n.t('Reset Image')}
										</button>
									</div>
								</div>
							</div>

							<!-- Model Name & ID -->
							<div class="glass-item p-4 space-y-3">
								<div>
									<div class="text-sm font-medium mb-1.5">{$i18n.t('Model Name')}</div>
									<div class="flex items-center gap-2">
										<input
											class="glass-input w-full px-3.5 py-2 text-sm"
											placeholder={$i18n.t('Model Name')}
											bind:value={name}
											required
										/>
										{#if edit && defaultBaseName && name !== defaultBaseName}
											<button
												type="button"
												class="shrink-0 text-xs whitespace-nowrap px-2.5 py-1.5 rounded-lg border border-gray-200/60 dark:border-gray-700/40 bg-gray-100/70 dark:bg-gray-800/60 hover:bg-gray-200/70 dark:hover:bg-gray-700/60 transition-colors"
												on:click={() => {
													name = defaultBaseName;
												}}
											>
												{$i18n.t('Restore Default')}
											</button>
										{/if}
									</div>
								</div>
								<div>
									<div class="text-sm font-medium mb-1.5">{$i18n.t('Model ID')}</div>
									<input
										class="glass-input w-full px-3.5 py-2 text-xs text-gray-500"
										placeholder={$i18n.t('Model ID')}
										bind:value={id}
										disabled={edit}
										required
									/>
								</div>
							</div>

							<!-- Base Model -->
							{#if preset}
								<div class="glass-item p-4">
									<div class="text-sm font-medium mb-2">{$i18n.t('Base Model (From)')}</div>
									<HaloSelect
										bind:value={info.base_model_id}
										options={[
											{ value: null, label: $i18n.t('Select a base model') },
											...$models
												.filter((m) => (model ? m.id !== model.id : true) && isBaseModelOption(m))
												.map((m) => ({
													value: getModelSelectionId(m),
													label: getModelChatDisplayName(m)
												}))
										]}
										placeholder={$i18n.t('Select a base model (e.g. llama3, gpt-4o)')}
										className="w-full"
										on:change={(e) => {
											addUsage(e.detail.value);
											syncProfileImageWithBaseModel(e.detail.value ?? null);
										}}
									/>
								</div>
							{/if}

							<!-- Description -->
							<div class="glass-item p-4">
								<div class="flex items-center justify-between mb-2.5">
									<div class="text-sm font-medium">{$i18n.t('Description')}</div>
									<div class="flex items-center gap-2">
										<span class="text-xs text-gray-400 dark:text-gray-500">
											{enableDescription ? $i18n.t('Custom') : $i18n.t('Default')}
										</span>
										<Switch bind:state={enableDescription} />
									</div>
								</div>

								{#if enableDescription}
									<Textarea
										className="glass-input w-full px-3.5 py-2 text-sm resize-none overflow-y-hidden"
										placeholder={$i18n.t('Add a short description about what this model does')}
										bind:value={info.meta.description}
									/>
								{/if}
							</div>

							<!-- Tags -->
							<div class="glass-item p-4">
								<div class="text-sm font-medium mb-2">{$i18n.t('Tags')}</div>
								<Tags
									tags={info?.meta?.tags ?? []}
									on:delete={(e) => {
										const tagName = e.detail;
										info.meta.tags = info.meta.tags.filter((tag) => tag.name !== tagName);
									}}
									on:add={(e) => {
										const tagName = e.detail;
										if (!(info?.meta?.tags ?? null)) {
											info.meta.tags = [{ name: tagName }];
										} else {
											info.meta.tags = [...info.meta.tags, { name: tagName }];
										}
									}}
								/>
							</div>

							<!-- Access Control -->
							<div class="glass-item p-4">
									<AccessControl
										bind:accessControl
										accessRoles={['read', 'write']}
										allowPublic={$user?.permissions?.sharing?.public_models || $user?.role === 'admin'}
										allowUserSelection={$user?.role === 'admin'}
										readOnly={!canManageAcl}
									/>
								</div>
						{/if}

						<!-- ===== Behavior Tab ===== -->
						{#if selectedTab === 'behavior'}
							<!-- System Prompt -->
							<div class="glass-item p-4">
								<div class="text-sm font-medium mb-2">{$i18n.t('System Prompt')}</div>
								<Textarea
									className="glass-input w-full px-3.5 py-2 text-sm resize-none overflow-y-hidden"
									placeholder={$i18n.t(
										"Enter the model's default system prompt here\ne.g.) You are a professional AI assistant. Provide clear, accurate, and concise responses based on the user's needs."
									)}
									rows={4}
									bind:value={params.system}
								/>
							</div>

							<div class="glass-item p-4">
								<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
									<div class="min-w-0 flex-1">
										<div class="text-sm font-medium mb-1.5">{$i18n.t('Thinking intensity')}</div>
										<div class="max-w-2xl text-xs leading-5 text-gray-500 dark:text-gray-400">
											{$i18n.t(
												'Constrains effort on reasoning for reasoning models. Only applicable to reasoning models from specific providers that support reasoning effort.'
											)}
										</div>
									</div>

									<div class="shrink-0 sm:pt-0.5">
										<HaloSelect
											value={reasoningEffortSelectValue}
											options={reasoningEffortSelectOptions}
											placeholder={$i18n.t('Enter reasoning effort')}
											className="w-full sm:w-[10.5rem]"
											contentClassName="min-w-[8.5rem]"
											contentAlign="end"
											matchTriggerMinWidth={false}
											on:change={(e) => {
												updateReasoningEffort(e.detail.value);
											}}
										/>
									</div>
								</div>
							</div>

							<!-- Advanced Params -->
							<div class="glass-item p-4">
								<button
									type="button"
									class="flex w-full items-center justify-between"
									on:click={() => {
										showAdvanced = !showAdvanced;
									}}
								>
									<div class="text-sm font-medium">{$i18n.t('Advanced Params')}</div>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 20 20"
										fill="currentColor"
										class="size-4 text-gray-400 transition-transform duration-200 {showAdvanced ? 'rotate-180' : ''}"
									>
										<path fill-rule="evenodd" d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z" clip-rule="evenodd" />
									</svg>
								</button>

								{#if showAdvanced}
										<div class="mt-3 pt-3 border-t border-gray-200/40 dark:border-gray-700/30">
											<AdvancedParams
												admin={true}
												bind:params
											/>
										</div>
									{/if}
							</div>

							<!-- Prompt Suggestions -->
							<div class="glass-item p-4">
								<div class="flex items-center justify-between mb-2.5">
									<div class="text-sm font-medium">{$i18n.t('Prompt suggestions')}</div>
									<div class="flex items-center gap-2">
										<span class="text-xs text-gray-400 dark:text-gray-500">
											{(info?.meta?.suggestion_prompts ?? null) !== null ? $i18n.t('Custom') : $i18n.t('Default')}
										</span>
										<Switch
											state={(info?.meta?.suggestion_prompts ?? null) !== null}
											on:change={(e) => {
												if (e.detail) {
													info.meta.suggestion_prompts = [{ content: '' }];
												} else {
													info.meta.suggestion_prompts = null;
												}
											}}
										/>
									</div>
								</div>

								{#if info?.meta?.suggestion_prompts}
									<div class="space-y-2">
										{#if info.meta.suggestion_prompts.length > 0}
											{#each info.meta.suggestion_prompts as prompt, promptIdx}
												<div class="flex items-center gap-2">
													<input
														class="glass-input flex-1 px-3.5 py-2 text-sm"
														placeholder={$i18n.t('Write a prompt suggestion (e.g. Who are you?)')}
														bind:value={prompt.content}
													/>
													<button
														type="button"
														class="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
														on:click={() => {
															info.meta.suggestion_prompts.splice(promptIdx, 1);
															info.meta.suggestion_prompts = info.meta.suggestion_prompts;
														}}
													>
														<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-4">
															<path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
														</svg>
													</button>
												</div>
											{/each}
										{:else}
											<div class="text-xs text-center text-gray-400 dark:text-gray-500 py-2">
												{$i18n.t('No suggestion prompts')}
											</div>
										{/if}

										<button
											type="button"
											class="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
											on:click={() => {
												if (
													info.meta.suggestion_prompts.length === 0 ||
													info.meta.suggestion_prompts.at(-1).content !== ''
												) {
													info.meta.suggestion_prompts = [
														...info.meta.suggestion_prompts,
														{ content: '' }
													];
												}
											}}
										>
											<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
												<path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z" />
											</svg>
											{$i18n.t('Add suggestion')}
										</button>
									</div>
								{/if}
							</div>
						{/if}

						<!-- ===== Integrations Tab ===== -->
						{#if selectedTab === 'integrations'}
							<div class="glass-item p-4">
								<Knowledge bind:selectedKnowledge={knowledge} collections={$knowledgeCollections} />
							</div>

							<div class="glass-item p-4">
								<ToolsSelector bind:selectedToolIds={toolIds} tools={$tools} />
							</div>

							<div class="glass-item p-4">
								<SkillsSelector bind:selectedSkillIds={skillIds} skills={$skillsStore} />
							</div>

							<div class="glass-item p-4">
								<FiltersSelector
									bind:selectedFilterIds={filterIds}
									filters={$functions.filter((func) => func.type === 'filter')}
								/>
							</div>

							<div class="glass-item p-4">
								<ActionsSelector
									bind:selectedActionIds={actionIds}
									actions={$functions.filter((func) => func.type === 'action')}
								/>
							</div>

							<div class="glass-item p-4">
								<BuiltinToolConfig bind:config={builtinToolConfig} />
							</div>
						{/if}

						<!-- ===== Capabilities Tab ===== -->
						{#if selectedTab === 'capabilities'}
							<div class="glass-item p-4">
								<Capabilities bind:capabilities />
							</div>

							<!-- JSON Preview -->
							<div class="glass-item p-4">
								<button
									type="button"
									class="flex w-full items-center justify-between"
									on:click={() => {
										showPreview = !showPreview;
									}}
								>
									<div class="text-sm font-medium">{$i18n.t('JSON Preview')}</div>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 20 20"
										fill="currentColor"
										class="size-4 text-gray-400 transition-transform duration-200 {showPreview ? 'rotate-180' : ''}"
									>
										<path fill-rule="evenodd" d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z" clip-rule="evenodd" />
									</svg>
								</button>

								{#if showPreview}
									<div class="mt-3 pt-3 border-t border-gray-200/40 dark:border-gray-700/30">
										<textarea
											class="glass-input w-full px-3.5 py-2 text-xs font-mono resize-none"
											rows="10"
											value={JSON.stringify(buildDraftModelInfo(), null, 2)}
											disabled
											readonly
										/>
									</div>
								{/if}
							</div>
						{/if}

					</section>

				</div>
			</div>
		</form>
	{/if}
{/if}
