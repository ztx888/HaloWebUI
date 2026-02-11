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
	const chartPalette = ['#0ea5e9', '#14b8a6', '#22c55e', '#f59e0b', '#f97316', '#ef4444', '#6366f1', '#06b6d4'];
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

	const formatPayment = (value: number | string | undefined) => parseFloat(`${value ?? 0}`).toFixed(2);
	const formatCredit = (value: number | string | undefined) => parseFloat(`${value ?? 0}`).toFixed(4);
	const formatToken = (value: number | string | undefined) => parseInt(`${value ?? 0}`).toLocaleString();

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
			color: chartPalette,
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
			color: chartPalette,
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
			color: chartPalette,
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
			color: chartPalette,
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
			color: chartPalette,
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

<div class="credit-dashboard h-full flex flex-col gap-5 pb-8">
	<div class="hero-panel rounded-2xl p-4 md:p-5">
		<div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
			<div class="space-y-1">
				<div class="text-xl md:text-2xl font-semibold text-slate-900 dark:text-slate-100 tracking-tight font-['Avenir_Next','PingFang_SC','Microsoft_YaHei',sans-serif]">
					{$i18n.t('Credit Statistics')}
				</div>
				<div class="text-xs md:text-sm text-slate-600 dark:text-slate-300">
					{$i18n.t('Track payment, token, and credit usage trends in one place')}
				</div>
			</div>

			<div class="stats-inline flex items-center gap-2">
				<span class="dot dot-payment"></span>
				<span class="text-xs text-slate-700 dark:text-slate-300">{$i18n.t('Realtime')}</span>
			</div>
		</div>

		<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
			<div class="field-shell relative">
				<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
						<path fill-rule="evenodd" d="M5.75 2a.75.75 0 01.75.75V4h7V2.75a.75.75 0 011.5 0V4h.25A2.75 2.75 0 0118 6.75v8.5A2.75 2.75 0 0115.25 18H4.75A2.75 2.75 0 012 15.25v-8.5A2.75 2.75 0 014.75 4H5V2.75A.75.75 0 015.75 2zm-1 5.5c-.69 0-1.25.56-1.25 1.25v6.5c0 .69.56 1.25 1.25 1.25h10.5c.69 0 1.25-.56 1.25-1.25v-6.5c0-.69-.56-1.25-1.25-1.25H4.75z" clip-rule="evenodd" />
					</svg>
				</div>
				<input
					bind:this={dateRangeInput}
					type="text"
					class="w-full pl-10 pr-3 py-2.5 bg-transparent text-sm border-none outline-none focus:ring-0 text-slate-700 dark:text-slate-200"
					placeholder={$i18n.t('Select Date Range')}
					readonly
				/>
			</div>

			<div class="field-shell relative">
				<div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
						<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
					</svg>
				</div>
				<input
					class="w-full pl-10 pr-3 py-2.5 bg-transparent text-sm border-none outline-none focus:ring-0 text-slate-700 dark:text-slate-200"
					bind:value={query}
					placeholder={$i18n.t('Fuzzy Search Username')}
				/>
			</div>
		</div>
	</div>

	<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
		<div class="metric-card metric-payment">
			<div class="text-[11px] font-semibold tracking-[0.18em] uppercase text-slate-500 dark:text-slate-300">{$i18n.t('Total Payment')}</div>
			<div class="mt-2 text-3xl font-bold text-slate-900 dark:text-slate-100">{formatPayment(statsData.total_payment)}</div>
			<div class="metric-glow"></div>
		</div>

		<div class="metric-card metric-credit">
			<div class="text-[11px] font-semibold tracking-[0.18em] uppercase text-slate-500 dark:text-slate-300">{$i18n.t('Total Credit Cost')}</div>
			<div class="mt-2 text-3xl font-bold text-slate-900 dark:text-slate-100">{formatCredit(statsData.total_credit)}</div>
			<div class="metric-glow"></div>
		</div>

		<div class="metric-card metric-token">
			<div class="text-[11px] font-semibold tracking-[0.18em] uppercase text-slate-500 dark:text-slate-300">{$i18n.t('Total Token Cost')}</div>
			<div class="mt-2 text-3xl font-bold text-slate-900 dark:text-slate-100">{formatToken(statsData.total_tokens)}</div>
			<div class="metric-glow"></div>
		</div>
	</div>

	<div class="space-y-4 pb-10">
		<div class="chart-shell h-80">
			<div bind:this={userPaymentLine} class="w-full h-full"></div>
		</div>

		<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
			<div class="chart-shell h-80">
				<div bind:this={modelTokenPie} class="w-full h-full"></div>
			</div>
			<div class="chart-shell h-80">
				<div bind:this={modelCostPie} class="w-full h-full"></div>
			</div>

			<div class="chart-shell h-80">
				<div bind:this={userTokenPie} class="w-full h-full"></div>
			</div>
			<div class="chart-shell h-80">
				<div bind:this={userCostPie} class="w-full h-full"></div>
			</div>
		</div>
	</div>
</div>

<style>
	.credit-dashboard {
		--panel-bg: linear-gradient(140deg, rgba(255, 255, 255, 0.9), rgba(246, 249, 252, 0.82));
	}

	.hero-panel {
		border: 1px solid rgba(148, 163, 184, 0.24);
		background: var(--panel-bg);
		backdrop-filter: blur(10px);
		box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
	}

	.field-shell {
		border-radius: 0.9rem;
		border: 1px solid rgba(148, 163, 184, 0.25);
		background: rgba(248, 250, 252, 0.88);
		transition: all 0.2s ease;
	}

	.field-shell:focus-within {
		border-color: rgba(14, 165, 233, 0.5);
		box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.16);
		background: rgba(255, 255, 255, 0.95);
	}

	.stats-inline {
		border-radius: 9999px;
		padding: 0.45rem 0.75rem;
		border: 1px solid rgba(148, 163, 184, 0.24);
		background: rgba(241, 245, 249, 0.75);
	}

	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 9999px;
		display: inline-flex;
	}

	.dot-payment {
		background: #0ea5e9;
		box-shadow: 0 0 0 6px rgba(14, 165, 233, 0.16);
	}

	.metric-card {
		position: relative;
		overflow: hidden;
		border-radius: 1rem;
		padding: 1rem 1.1rem;
		border: 1px solid rgba(148, 163, 184, 0.25);
		background: rgba(255, 255, 255, 0.78);
		backdrop-filter: blur(8px);
		box-shadow: 0 10px 25px rgba(15, 23, 42, 0.08);
	}

	.metric-glow {
		position: absolute;
		right: -2rem;
		top: -2.5rem;
		width: 8rem;
		height: 8rem;
		border-radius: 9999px;
		filter: blur(30px);
		opacity: 0.34;
		pointer-events: none;
	}

	.metric-payment .metric-glow {
		background: #38bdf8;
	}

	.metric-credit .metric-glow {
		background: #34d399;
	}

	.metric-token .metric-glow {
		background: #f59e0b;
	}

	.chart-shell {
		border-radius: 1rem;
		padding: 0.75rem;
		border: 1px solid rgba(148, 163, 184, 0.24);
		background: rgba(255, 255, 255, 0.82);
		box-shadow: 0 10px 28px rgba(15, 23, 42, 0.07);
	}

	:global(.dark) .credit-dashboard {
		--panel-bg: linear-gradient(135deg, rgba(15, 23, 42, 0.82), rgba(30, 41, 59, 0.7));
	}

	:global(.dark) .hero-panel,
	:global(.dark) .metric-card,
	:global(.dark) .chart-shell {
		border-color: rgba(71, 85, 105, 0.52);
		background: rgba(15, 23, 42, 0.64);
		box-shadow: 0 12px 28px rgba(2, 6, 23, 0.45);
	}

	:global(.dark) .field-shell {
		border-color: rgba(71, 85, 105, 0.55);
		background: rgba(15, 23, 42, 0.65);
	}

	:global(.dark) .field-shell:focus-within {
		border-color: rgba(34, 211, 238, 0.6);
		box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.16);
	}

	:global(.dark) .stats-inline {
		background: rgba(30, 41, 59, 0.75);
		border-color: rgba(71, 85, 105, 0.6);
	}
</style>
