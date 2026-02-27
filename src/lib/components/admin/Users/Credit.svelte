<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { getCreditStats } from '$lib/apis/credit';
	import { toast } from 'svelte-sonner';
	import * as echarts from 'echarts';
	import type { EChartsType } from 'echarts';
	import { theme } from '$lib/stores';
	import flatpickr from 'flatpickr';
	import 'flatpickr/dist/flatpickr.css';
	import { Mandarin } from 'flatpickr/dist/l10n/zh.js';

	const maxDimensions = 20;
	// Use explicit colors for better visibility in different modes if needed
	const echartsTheme = $theme.includes('dark') ? 'dark' : 'light';

	let userPaymentLine: HTMLDivElement;
	let userPaymentLineOption = {};
	let userPaymentLineChart: EChartsType;

	let modelTokenPie: HTMLDivElement;
	let modelTokenPieOption = {};
	let modelTokenPieChart: EChartsType;

	let modelCostPie: HTMLDivElement;
	let modelCostPieOption = {};
	let modelCostPieChart: EChartsType;

	let userTokenPie: HTMLDivElement;
	let userTokenPieOption = {};
	let userTokenPieChart: EChartsType;

	let userCostPie: HTMLDivElement;
	let userCostPieOption = {};
	let userCostPieChart: EChartsType;

	const i18n = getContext('i18n');

	type ChartItem = {
		name: string;
		value: number;
	};
	type Data = {
		total_tokens: Number;
		total_credit: Number;
		model_cost_pie: Array<ChartItem>;
		model_token_pie: Array<ChartItem>;
		user_cost_pie: Array<ChartItem>;
		user_token_pie: Array<ChartItem>;
		total_payment: Number;
		user_payment_stats_x: Array<String>;
		user_payment_stats_y: Array<Number>;
	};

	let statsData: Data = {};

	let period = 7;

	let endTime = new Date();
	let startTime = new Date();
	startTime.setDate(endTime.getDate() - 7);

	const mergeData = (data: Array<ChartItem>) => {
		if (!data) return [];
		let sorted = data.sort((a, b) => b.value - a.value);
		let topItems = sorted.slice(0, maxDimensions);
		let rest = sorted.slice(maxDimensions);
		let restSum = rest.reduce((sum, item) => sum + item.value, 0);
		return [...topItems, ...(restSum > 0 ? [{ name: $i18n.t('Other'), value: restSum }] : [])];
	};

	let dateRangeInput;
	let fp;

	let query = '';
	// Simple debounce for search
	let searchTimeout;
	$: if (query !== undefined) {
		clearTimeout(searchTimeout);
		searchTimeout = setTimeout(() => {
			doQuery();
		}, 500);
	}

	const doQuery = async () => {
		const data = await getCreditStats(localStorage.token, {
			start_time: Math.round(startTime.getTime() / 1000),
			end_time: Math.round(endTime.getTime() / 1000),
			query: query
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (data) {
			statsData = data;
			// Resize charts after data update and a small delay for DOM rendering
			setTimeout(() => {
				drawChart(data);
				resizeCharts();
			}, 100);
		}
	};

	const drawChart = (data: Data) => {
		const commonTextStyle = {
			fontFamily: 'Inter, sans-serif'
		};
		
		const titleStyle = {
			...commonTextStyle,
			fontSize: 16,
			fontWeight: '500',
			color: $theme.includes('dark') ? '#e5e7eb' : '#374151'
		};

		if (!userPaymentLineChart) {
			userPaymentLineChart = echarts.init(userPaymentLine, echartsTheme);
		}
		userPaymentLineOption = {
			backgroundColor: 'transparent',
			title: {
				text: $i18n.t('User Payment Stats'),
				textStyle: titleStyle,
				left: 'center',
				top: 10
			},
			grid: { top: 60, right: 30, bottom: 30, left: 50, containLabel: true },
			tooltip: {
				show: true,
				trigger: 'axis',
				backgroundColor: $theme.includes('dark') ? '#374151' : '#ffffff',
				textStyle: { color: $theme.includes('dark') ? '#ffffff' : '#000000' }
			},
			xAxis: {
				type: 'category',
				data: data.user_payment_stats_x,
				axisLine: { show: false },
				axisTick: { show: false }
			},
			yAxis: {
				type: 'value',
				splitLine: { lineStyle: { type: 'dashed', color: $theme.includes('dark') ? '#4b5563' : '#e5e7eb' } }
			},
			series: [
				{
					data: data.user_payment_stats_y,
					type: 'line',
					smooth: true,
					showSymbol: false,
					areaStyle: { opacity: 0.2 },
					itemStyle: { color: '#3b82f6' }
				}
			]
		};
		userPaymentLineChart.setOption(userPaymentLineOption);

		if (!modelTokenPieChart) {
			modelTokenPieChart = echarts.init(modelTokenPie, echartsTheme);
		}
		modelTokenPieOption = {
			backgroundColor: 'transparent',
			title: {
				text: $i18n.t('Model Tokens Cost'),
				textStyle: titleStyle,
				left: 'center',
				top: 10
			},
			tooltip: {
				trigger: 'item',
				backgroundColor: $theme.includes('dark') ? '#374151' : '#ffffff',
				textStyle: { color: $theme.includes('dark') ? '#ffffff' : '#000000' }
			},
			legend: {
				type: 'scroll',
				bottom: 0,
				textStyle: { color: $theme.includes('dark') ? '#9ca3af' : '#6b7280' }
			},
			series: [
				{
					type: 'pie',
					radius: ['40%', '70%'],
					center: ['50%', '50%'],
					itemStyle: {
						borderRadius: 5,
						borderColor: $theme.includes('dark') ? '#1f2937' : '#fff',
						borderWidth: 2
					},
					data: mergeData(data.model_token_pie),
					label: { show: false }
				}
			]
		};
		modelTokenPieChart.setOption(modelTokenPieOption);

		if (!modelCostPieChart) {
			modelCostPieChart = echarts.init(modelCostPie, echartsTheme);
		}
		modelCostPieOption = {
			backgroundColor: 'transparent',
			title: {
				text: $i18n.t('Model Credit Cost'),
				textStyle: titleStyle,
				left: 'center',
				top: 10
			},
			tooltip: {
				trigger: 'item',
				backgroundColor: $theme.includes('dark') ? '#374151' : '#ffffff',
				textStyle: { color: $theme.includes('dark') ? '#ffffff' : '#000000' }
			},
			legend: {
				type: 'scroll',
				bottom: 0,
				textStyle: { color: $theme.includes('dark') ? '#9ca3af' : '#6b7280' }
			},
			series: [
				{
					type: 'pie',
					radius: ['40%', '70%'],
					center: ['50%', '50%'],
					itemStyle: {
						borderRadius: 5,
						borderColor: $theme.includes('dark') ? '#1f2937' : '#fff',
						borderWidth: 2
					},
					data: mergeData(data.model_cost_pie),
					label: { show: false }
				}
			]
		};
		modelCostPieChart.setOption(modelCostPieOption);

		if (!userTokenPieChart) {
			userTokenPieChart = echarts.init(userTokenPie, echartsTheme);
		}
		
		const _userTokenPieData = mergeData(data.user_token_pie);
		const userTokenX = _userTokenPieData.map((item) => item.name);
		const userTokenY = _userTokenPieData.map((item) => item.value);
		
		userTokenPieOption = {
			backgroundColor: 'transparent',
			title: {
				text: $i18n.t('User Tokens Cost'),
				textStyle: titleStyle,
				left: 'center',
				top: 10
			},
			grid: { top: 60, right: 30, bottom: 60, left: 50, containLabel: true },
			tooltip: {
				trigger: 'axis',
				axisPointer: { type: 'shadow' },
				backgroundColor: $theme.includes('dark') ? '#374151' : '#ffffff',
				textStyle: { color: $theme.includes('dark') ? '#ffffff' : '#000000' }
			},
			xAxis: {
				type: 'category',
				data: userTokenX,
				axisLabel: {
					interval: 0,
					rotate: 30,
					overflow: 'truncate',
					width: 80
				},
				axisLine: { show: false },
				axisTick: { show: false }
			},
			yAxis: {
				type: 'value',
				splitLine: { lineStyle: { type: 'dashed', color: $theme.includes('dark') ? '#4b5563' : '#e5e7eb' } }
			},
			series: [
				{
					type: 'bar',
					data: userTokenY,
					barMaxWidth: 30,
					itemStyle: {
						borderRadius: [4, 4, 0, 0],
						color: '#8b5cf6'
					}
				}
			]
		};
		userTokenPieChart.setOption(userTokenPieOption);

		if (!userCostPieChart) {
			userCostPieChart = echarts.init(userCostPie, echartsTheme);
		}
		
		const _userCostPieData = mergeData(data.user_cost_pie);
		const userCostX = _userCostPieData.map((item) => item.name);
		const userCostY = _userCostPieData.map((item) => item.value);
		
		userCostPieOption = {
			backgroundColor: 'transparent',
			title: {
				text: $i18n.t('User Credit Cost'),
				textStyle: titleStyle,
				left: 'center',
				top: 10
			},
			grid: { top: 60, right: 30, bottom: 60, left: 50, containLabel: true },
			tooltip: {
				trigger: 'axis',
				axisPointer: { type: 'shadow' },
				backgroundColor: $theme.includes('dark') ? '#374151' : '#ffffff',
				textStyle: { color: $theme.includes('dark') ? '#ffffff' : '#000000' }
			},
			xAxis: {
				type: 'category',
				data: userCostX,
				axisLabel: {
					interval: 0,
					rotate: 30,
					overflow: 'truncate',
					width: 80
				},
				axisLine: { show: false },
				axisTick: { show: false }
			},
			yAxis: {
				type: 'value',
				splitLine: { lineStyle: { type: 'dashed', color: $theme.includes('dark') ? '#4b5563' : '#e5e7eb' } }
			},
			series: [
				{
					type: 'bar',
					data: userCostY,
					barMaxWidth: 30,
					itemStyle: {
						borderRadius: [4, 4, 0, 0],
						color: '#10b981'
					}
				}
			]
		};
		userCostPieChart.setOption(userCostPieOption);
	};

	const resizeCharts = () => {
		userPaymentLineChart?.resize();
		modelTokenPieChart?.resize();
		modelCostPieChart?.resize();
		userTokenPieChart?.resize();
		userCostPieChart?.resize();
	};

	onMount(async () => {
		if (echartsTheme === 'dark') {
			await import('flatpickr/dist/themes/dark.css');
		}

		let locale = null;
		const lang = document.documentElement.getAttribute('lang');
		if (lang === 'zh-CN') {
			locale = Mandarin;
		}

		const minDays = new Date();
		minDays.setDate(endTime.getDate() - 180);
		const tomorrow = new Date();
		tomorrow.setDate(endTime.getDate() + 1);

		fp = flatpickr(dateRangeInput, {
			locale: locale,
			mode: 'range',
			dateFormat: 'Y-m-d H:i:S',
			enableTime: true,
			animate: true,
			allowInput: true,
			defaultDate: [startTime, endTime],
			defaultHour: 0,
			maxDate: tomorrow,
			minDate: minDays,
			position: 'auto center',
			showMonths: 2,
			time_24hr: true,
			onChange: async (selectedDates, _) => {
				if (selectedDates.length === 2) {
					startTime = selectedDates[0];
					endTime = selectedDates[1];
					await doQuery();
				}
			}
		});

		window.addEventListener('resize', resizeCharts);

		return () => {
			fp.destroy();
			window.removeEventListener('resize', resizeCharts);
			userPaymentLineChart?.dispose();
			modelTokenPieChart?.dispose();
			modelCostPieChart?.dispose();
			userTokenPieChart?.dispose();
			userCostPieChart?.dispose();
		};
	});
</script>

<div class="h-full flex flex-col space-y-4">
	<!-- Filter Bar -->
	<div class="flex flex-col md:flex-row justify-between items-center gap-4 bg-white dark:bg-gray-900 p-2 rounded-lg">
		<div class="text-xl font-semibold text-gray-800 dark:text-gray-100 flex items-center gap-2">
			<span>{$i18n.t('Credit Statistics')}</span>
		</div>
		<div class="flex flex-1 w-full md:w-auto gap-3 items-center">
			<!-- Date Picker -->
			<div class="flex-1 md:max-w-xs relative bg-gray-50 dark:bg-gray-850 rounded-lg border border-gray-100 dark:border-gray-800">
				<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
						<path fill-rule="evenodd" d="M5.75 2a.75.75 0 01.75.75V4h7V2.75a.75.75 0 011.5 0V4h.25A2.75 2.75 0 0118 6.75v8.5A2.75 2.75 0 0115.25 18H4.75A2.75 2.75 0 012 15.25v-8.5A2.75 2.75 0 014.75 4H5V2.75A.75.75 0 015.75 2zm-1 5.5c-.69 0-1.25.56-1.25 1.25v6.5c0 .69.56 1.25 1.25 1.25h10.5c.69 0 1.25-.56 1.25-1.25v-6.5c0-.69-.56-1.25-1.25-1.25H4.75z" clip-rule="evenodd" />
					</svg>
				</div>
				<input
					bind:this={dateRangeInput}
					type="text"
					class="w-full pl-10 pr-3 py-2 bg-transparent text-sm border-none outline-none focus:ring-0 text-gray-700 dark:text-gray-200"
					placeholder={$i18n.t('Select Date Range')}
					readonly
				/>
			</div>

			<!-- Search -->
			<div class="flex-1 md:max-w-xs relative bg-gray-50 dark:bg-gray-850 rounded-lg border border-gray-100 dark:border-gray-800">
				<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
						<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
					</svg>
				</div>
				<input
					class="w-full pl-10 pr-3 py-2 bg-transparent text-sm border-none outline-none focus:ring-0 text-gray-700 dark:text-gray-200"
					bind:value={query}
					placeholder={$i18n.t('Fuzzy Search Username')}
				/>
			</div>
		</div>
	</div>

	<!-- Stats Overview Cards -->
	<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
		<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800 flex flex-col items-center justify-center relative overflow-hidden group">
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1 z-10">{$i18n.t('Total Payment')}</div>
			<div class="text-3xl font-bold text-gray-800 dark:text-gray-100 z-10">{parseFloat(statsData.total_payment ?? 0).toFixed(2)}</div>
			<div class="absolute right-0 bottom-0 opacity-10 group-hover:scale-110 transition-transform duration-500">
				<svg class="w-24 h-24 text-blue-500" fill="currentColor" viewBox="0 0 20 20"><path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z"/></svg>
			</div>
			<div class="w-full h-1 bg-blue-500 absolute bottom-0 left-0"></div>
		</div>

		<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800 flex flex-col items-center justify-center relative overflow-hidden group">
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1 z-10">{$i18n.t('Total Credit Cost')}</div>
			<div class="text-3xl font-bold text-gray-800 dark:text-gray-100 z-10">{parseFloat(statsData.total_credit ?? 0).toFixed(4)}</div>
			<div class="w-full h-1 bg-green-500 absolute bottom-0 left-0"></div>
		</div>

		<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-5 border border-gray-100 dark:border-gray-800 flex flex-col items-center justify-center relative overflow-hidden group">
			<div class="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1 z-10">{$i18n.t('Total Token Cost')}</div>
			<div class="text-3xl font-bold text-gray-800 dark:text-gray-100 z-10">{parseInt(statsData.total_tokens ?? 0).toLocaleString()}</div>
			<div class="w-full h-1 bg-purple-500 absolute bottom-0 left-0"></div>
		</div>
	</div>

	<!-- Charts Grid -->
	<div class="space-y-4 pb-10">
		<!-- Payment Trend -->
		<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-4 border border-gray-100 dark:border-gray-800 h-80 shadow-sm">
			<div bind:this={userPaymentLine} class="w-full h-full"></div>
		</div>

		<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
			<!-- Pie Charts -->
			<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-4 border border-gray-100 dark:border-gray-800 h-80 shadow-sm">
				<div bind:this={modelTokenPie} class="w-full h-full"></div>
			</div>
			<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-4 border border-gray-100 dark:border-gray-800 h-80 shadow-sm">
				<div bind:this={modelCostPie} class="w-full h-full"></div>
			</div>

			<!-- Bar Charts -->
			<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-4 border border-gray-100 dark:border-gray-800 h-80 shadow-sm">
				<div bind:this={userTokenPie} class="w-full h-full"></div>
			</div>
			<div class="bg-gray-50 dark:bg-gray-850 rounded-lg p-4 border border-gray-100 dark:border-gray-800 h-80 shadow-sm">
				<div bind:this={userCostPie} class="w-full h-full"></div>
			</div>
		</div>
	</div>
</div>
