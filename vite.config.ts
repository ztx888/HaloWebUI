import { sveltekit } from '@sveltejs/kit/vite';
import { rm } from 'node:fs/promises';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';

const enablePyodide = process.env.ENABLE_PYODIDE === 'true';
const enableSourceMap = process.env.VITE_SOURCEMAP === 'true';
const pyodideRemoteIndexUrl =
	process.env.PYODIDE_INDEX_URL ?? 'https://cdn.jsdelivr.net/pyodide/v0.27.3/full/';
const pyodideIndexUrl = enablePyodide ? '/pyodide/' : pyodideRemoteIndexUrl;

const stripPyodideAssets = () => ({
	name: 'strip-pyodide-assets',
	closeBundle: async () => {
		if (enablePyodide) {
			return;
		}

		await rm(resolve('build/pyodide'), { recursive: true, force: true });
	}
});

// /** @type {import('vite').Plugin} */
// const viteServerConfig = {
// 	name: 'log-request-middleware',
// 	configureServer(server) {
// 		server.middlewares.use((req, res, next) => {
// 			res.setHeader('Access-Control-Allow-Origin', '*');
// 			res.setHeader('Access-Control-Allow-Methods', 'GET');
// 			res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
// 			res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
// 			next();
// 		});
// 	}
// };

export default defineConfig({
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		}),
		stripPyodideAssets()
	],
	server: {
		// In dev, keep frontend same-origin and proxy backend routes to `:8080`.
		// This allows LAN clients (e.g., phone) to only access `:5173`.
		proxy: {
			'/api': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true,
				ws: true
			},
			'/ollama': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true
			},
			'/openai': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true
			},
			'/gemini': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true
			},
			'/anthropic': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true
			},
			'/ws': {
				target: 'http://127.0.0.1:8080',
				changeOrigin: true,
				ws: true
			}
		}
	},
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build'),
		APP_ENABLE_PYODIDE: JSON.stringify(enablePyodide),
		APP_PYODIDE_INDEX_URL: JSON.stringify(pyodideIndexUrl)
	},
	build: {
		sourcemap: enableSourceMap
	},
	worker: {
		format: 'es'
	}
});
