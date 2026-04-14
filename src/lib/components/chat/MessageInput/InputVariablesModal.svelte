<script lang="ts">
	import { getContext, tick } from 'svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let variables = {};
	export let onSave = (_values) => {};

	let loading = true;
	let variableValues = {};

	const normalizeMultilineValues = (values: Record<string, any>) => {
		const normalized = {};
		for (const [key, value] of Object.entries(values)) {
			normalized[key] = typeof value === 'string' ? value.replace(/\r\n/g, '\n') : value;
		}
		return normalized;
	};

	const getDefaultValue = (variable) => {
		if (variable?.default !== undefined) {
			return variable.default;
		}

		if (variable?.type === 'checkbox') {
			return false;
		}

		return '';
	};

	const submitHandler = () => {
		onSave(normalizeMultilineValues(variableValues));
		show = false;
	};

	const init = async () => {
		loading = true;
		const nextValues = {};

		for (const key of Object.keys(variables ?? {})) {
			nextValues[key] = getDefaultValue(variables[key] ?? {});
		}

		variableValues = nextValues;
		loading = false;

		await tick();
		const firstInput = document.getElementById('input-variable-0');
		firstInput?.focus();
	};

	$: if (show) {
		init();
	}

	const getInputType = (variable) => {
		switch (variable?.type) {
			case 'color':
			case 'date':
			case 'datetime-local':
			case 'email':
			case 'month':
			case 'number':
			case 'range':
			case 'tel':
			case 'text':
			case 'time':
			case 'url':
			case 'week':
				return variable.type;
			default:
				return 'text';
		}
	};
</script>

{#if show}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="fixed inset-0 z-[999] flex items-center justify-center bg-black/50" on:click|self={() => (show = false)}>
		<div class="w-full max-w-lg mx-4 rounded-2xl bg-white shadow-xl dark:bg-gray-900 dark:text-gray-200">
			<div class="flex items-center justify-between px-5 pt-4 pb-2">
				<div class="text-lg font-medium">{$i18n.t('Input Variables')}</div>
				<button
					type="button"
					class="rounded-lg px-2 py-1 text-sm text-gray-500 hover:bg-black/5 dark:hover:bg-white/5"
					on:click={() => {
						show = false;
					}}
				>
					{$i18n.t('Close')}
				</button>
			</div>

			<form
				class="px-5 pb-4"
				on:submit|preventDefault={() => {
					submitHandler();
				}}
			>
				{#if !loading}
					<div class="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
						{#each Object.keys(variables) as variable, idx}
							{@const { type, ...inputAttributes } = variables[variable] ?? {}}
							<div class="space-y-1">
								<div class="text-xs font-medium">
									{variable}
									{#if variables[variable]?.required ?? false}
										<span class="text-gray-500">* {$i18n.t('required')}</span>
									{/if}
								</div>

								{#if variables[variable]?.type === 'select'}
									<select
										id="input-variable-{idx}"
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-hidden dark:border-gray-700"
										bind:value={variableValues[variable]}
										required={variables[variable]?.required ?? false}
									>
										{#if variables[variable]?.placeholder}
											<option value="" disabled>{variables[variable].placeholder}</option>
										{/if}
										{#each variables[variable]?.options ?? [] as option}
											<option value={option}>{option}</option>
										{/each}
									</select>
								{:else if variables[variable]?.type === 'checkbox'}
									<label class="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700">
										<input
											id="input-variable-{idx}"
											type="checkbox"
											bind:checked={variableValues[variable]}
											{...inputAttributes}
										/>
										<span>{variables[variable]?.label ?? variable}</span>
									</label>
								{:else if variables[variable]?.type === 'color'}
									<div class="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 dark:border-gray-700">
										<input
											id="input-variable-{idx}"
											type="color"
											class="size-8 rounded-md border border-gray-200 dark:border-gray-700"
											value={variableValues[variable]}
											on:input={(event) => {
												variableValues[variable] = event.currentTarget.value.toUpperCase();
											}}
											{...inputAttributes}
										/>
										<input
											type="text"
											class="min-w-0 flex-1 bg-transparent text-sm outline-hidden"
											bind:value={variableValues[variable]}
											placeholder={variables[variable]?.placeholder ?? '#FFFFFF'}
											required={variables[variable]?.required ?? false}
										/>
									</div>
								{:else if variables[variable]?.type === 'range'}
									<div class="flex items-center gap-3 rounded-lg border border-gray-200 px-3 py-2 dark:border-gray-700">
										<input
											id="input-variable-{idx}"
											type="range"
											class="min-w-0 flex-1"
											bind:value={variableValues[variable]}
											min={variables[variable]?.min}
											max={variables[variable]?.max}
											step={variables[variable]?.step}
											{...inputAttributes}
										/>
										<input
											type="text"
											class="w-16 bg-transparent text-right text-sm outline-hidden"
											bind:value={variableValues[variable]}
											required={variables[variable]?.required ?? false}
										/>
									</div>
								{:else if variables[variable]?.type === 'map'}
									<input
										id="input-variable-{idx}"
										type="text"
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-hidden dark:border-gray-700"
										bind:value={variableValues[variable]}
										placeholder={variables[variable]?.placeholder ?? $i18n.t('Enter coordinates (e.g. 51.505, -0.09)')}
										required={variables[variable]?.required ?? false}
									/>
								{:else if variables[variable]?.type === 'textarea'}
									<textarea
										id="input-variable-{idx}"
										class="min-h-[96px] w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-hidden dark:border-gray-700"
										bind:value={variableValues[variable]}
										placeholder={variables[variable]?.placeholder ?? ''}
										required={variables[variable]?.required ?? false}
									/>
								{:else}
									<input
										id="input-variable-{idx}"
										type={getInputType(variables[variable])}
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-hidden dark:border-gray-700"
										value={variableValues[variable]}
										placeholder={variables[variable]?.placeholder ?? ''}
										required={variables[variable]?.required ?? false}
										min={variables[variable]?.min}
										max={variables[variable]?.max}
										step={variables[variable]?.step}
										{...inputAttributes}
										on:input={(event) => {
											variableValues[variable] = event.currentTarget.value;
										}}
									/>
								{/if}
							</div>
						{/each}
					</div>
				{/if}

				<div class="mt-4 flex justify-end gap-2">
					<button
						type="button"
						class="rounded-lg px-4 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/5"
						on:click={() => {
							show = false;
						}}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="submit"
						class="rounded-lg bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700"
					>
						{$i18n.t('Apply')}
					</button>
				</div>
			</form>
		</div>
	</div>
{/if}
