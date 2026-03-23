<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher();

	export let steps: { value: any; label: string }[] = [];
	export let value: any = steps[0]?.value;
	// 每个 step 对应的颜色 class（bg-xxx 格式），长度应与 steps 一致
	// 不传则全部用默认蓝色
	export let stepColors: string[] = [];

	let trackEl: HTMLDivElement;
	let dragging = false;

	$: currentIndex = steps.findIndex((s) => s.value === value);
	$: if (currentIndex === -1) currentIndex = 0;
	$: thumbPercent = steps.length > 1 ? (currentIndex / (steps.length - 1)) * 100 : 0;

	// 当前 step 的颜色，fallback 到蓝色
	const defaultBg = 'bg-blue-500 dark:bg-blue-400';
	const defaultText = 'text-blue-600 dark:text-blue-400';
	$: currentBg = stepColors[currentIndex] || defaultBg;
	$: currentText = stepColors[currentIndex]
		? stepColors[currentIndex].replace(/bg-/g, 'text-')
		: defaultText;

	function select(index: number) {
		if (index < 0 || index >= steps.length) return;
		value = steps[index].value;
		dispatch('change', { value: steps[index].value, index });
	}

	function getIndexFromX(clientX: number) {
		if (!trackEl) return 0;
		const rect = trackEl.getBoundingClientRect();
		const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
		return Math.round(ratio * (steps.length - 1));
	}

	function handleTrackDown(e: MouseEvent | TouchEvent) {
		e.preventDefault();
		dragging = true;
		const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX;
		select(getIndexFromX(clientX));
		window.addEventListener('mousemove', handleMove);
		window.addEventListener('mouseup', handleUp);
		window.addEventListener('touchmove', handleMove);
		window.addEventListener('touchend', handleUp);
	}

	function handleMove(e: MouseEvent | TouchEvent) {
		if (!dragging) return;
		const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX;
		select(getIndexFromX(clientX));
	}

	function handleUp() {
		dragging = false;
		window.removeEventListener('mousemove', handleMove);
		window.removeEventListener('mouseup', handleUp);
		window.removeEventListener('touchmove', handleMove);
		window.removeEventListener('touchend', handleUp);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
			e.preventDefault();
			select(Math.min(currentIndex + 1, steps.length - 1));
		} else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
			e.preventDefault();
			select(Math.max(currentIndex - 1, 0));
		} else if (e.key === 'Home') {
			e.preventDefault();
			select(0);
		} else if (e.key === 'End') {
			e.preventDefault();
			select(steps.length - 1);
		}
	}
</script>

<div class="stepped-slider select-none">
	<!-- Track area -->
	<!-- svelte-ignore a11y-no-noninteractive-tabindex -->
	<div
		class="relative h-10 flex items-center cursor-pointer"
		bind:this={trackEl}
		on:mousedown={handleTrackDown}
		on:touchstart={handleTrackDown}
		on:keydown={handleKeydown}
		tabindex="0"
		role="slider"
		aria-valuemin={0}
		aria-valuemax={steps.length - 1}
		aria-valuenow={currentIndex}
		aria-valuetext={steps[currentIndex]?.label ?? ''}
	>
		<!-- Background track -->
		<div class="absolute left-0 right-0 h-1 rounded-full bg-gray-300 dark:bg-gray-700" />

		<!-- Filled track -->
		<div
			class="absolute left-0 h-1 rounded-full transition-all duration-150 {currentBg}"
			style="width: {thumbPercent}%"
		/>

		<!-- Tick dots -->
		{#each steps as _, i}
			{@const tickPct = steps.length > 1 ? (i / (steps.length - 1)) * 100 : 0}
			{@const tickBg = i <= currentIndex ? currentBg : 'bg-gray-400 dark:bg-gray-500'}
			<div
				class="absolute w-1.5 h-1.5 rounded-full -translate-x-1/2 transition-colors duration-150
					{tickBg}"
				style="left: {tickPct}%"
			/>
		{/each}

		<!-- Thumb -->
		<div
			class="absolute w-4 h-4 rounded-full -translate-x-1/2 transition-all shadow-sm
				{currentBg} border-2 border-white dark:border-gray-900
				{dragging ? 'scale-110 shadow-md' : 'hover:scale-105'}"
			style="left: {thumbPercent}%"
		/>
	</div>

	<!-- Labels -->
	<div class="relative h-4 -mt-1">
		{#each steps as step, i}
			{@const labelPct = steps.length > 1 ? (i / (steps.length - 1)) * 100 : 0}
			{@const labelColor =
				i === currentIndex
					? currentText
					: 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}
			<button
				type="button"
				class="absolute text-[10px] leading-tight text-center transition-all duration-150 cursor-pointer
					-translate-x-1/2 whitespace-nowrap
					{labelColor}
					{i === currentIndex ? 'font-medium' : ''}"
				style="left: {labelPct}%"
				on:click|stopPropagation={() => select(i)}
			>
				{step.label}
			</button>
		{/each}
	</div>
</div>
