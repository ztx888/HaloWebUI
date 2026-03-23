const PYODIDE_CONSENT_KEY = 'halowebui:pyodide-download-approved';
const KOKORO_CONSENT_KEY = 'halowebui:kokoro-download-approved';

const PYODIDE_BASE_DOWNLOAD_MB = 12;
const PYODIDE_PACKAGE_SIZES_MB: Record<string, number> = {
	requests: 0.2,
	beautifulsoup4: 0.3,
	numpy: 12.5,
	pandas: 23.8,
	'scikit-learn': 24,
	scipy: 48,
	regex: 1,
	seaborn: 0.8,
	sympy: 17,
	tiktoken: 2,
	matplotlib: 17,
	pytz: 1.1
};

export const KOKORO_MODEL_DOWNLOAD_MB = 90;

const hasWindow = () => typeof window !== 'undefined';

export const canUsePyodideRuntime = () => APP_ENABLE_PYODIDE || Boolean(APP_PYODIDE_INDEX_URL);
export const usesRemotePyodideRuntime = () => !APP_ENABLE_PYODIDE && Boolean(APP_PYODIDE_INDEX_URL);

export const getPyodidePackagesForCode = (code: string) =>
	[
		code.includes('requests') ? 'requests' : null,
		code.includes('bs4') ? 'beautifulsoup4' : null,
		code.includes('numpy') ? 'numpy' : null,
		code.includes('pandas') ? 'pandas' : null,
		code.includes('sklearn') ? 'scikit-learn' : null,
		code.includes('scipy') ? 'scipy' : null,
		code.includes('re') ? 'regex' : null,
		code.includes('seaborn') ? 'seaborn' : null,
		code.includes('sympy') ? 'sympy' : null,
		code.includes('tiktoken') ? 'tiktoken' : null,
		code.includes('matplotlib') ? 'matplotlib' : null,
		code.includes('pytz') ? 'pytz' : null
	].filter((pkg): pkg is string => Boolean(pkg));

export const getPyodideDownloadEstimateMb = (packages: string[]) =>
	PYODIDE_BASE_DOWNLOAD_MB +
	packages.reduce((sum, pkg) => sum + (PYODIDE_PACKAGE_SIZES_MB[pkg] ?? 0), 0);

export const getPyodideDownloadSummary = (packages: string[]) => {
	const rounded = Math.round(getPyodideDownloadEstimateMb(packages));
	if (packages.length === 0) {
		return `首次运行需要下载浏览器 Python 运行时，约 ${rounded} MB。`;
	}

	return `首次运行需要下载浏览器 Python 运行时，约 ${rounded} MB；其中包含基础运行时和 ${packages.join(', ')} 等附加包。`;
};

export const hasPyodideConsent = () =>
	hasWindow() && window.localStorage.getItem(PYODIDE_CONSENT_KEY) === 'true';

export const approvePyodideConsent = () => {
	if (hasWindow()) {
		window.localStorage.setItem(PYODIDE_CONSENT_KEY, 'true');
	}
};

export const hasKokoroConsent = () =>
	hasWindow() && window.localStorage.getItem(KOKORO_CONSENT_KEY) === 'true';

export const approveKokoroConsent = () => {
	if (hasWindow()) {
		window.localStorage.setItem(KOKORO_CONSENT_KEY, 'true');
	}
};
