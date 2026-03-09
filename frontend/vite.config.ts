import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

import { defineConfig, searchForWorkspaceRoot } from 'vite';
import devtoolsJson from 'vite-plugin-devtools-json';
import { storybookTest } from '@storybook/addon-vitest/vitest-plugin';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * the maximum size of an embedded asset in bytes,
 * e.g. maximum size of embedded font (see node_modules/katex/dist/fonts/*.woff2)
 */
const MAX_ASSET_SIZE = 32000;

const ENABLE_JS_MINIFICATION = true;

// Dev server target — route through mitmweb reverse proxy at :8080 so all
// model API calls are visible in the mitmweb UI (http://localhost:8081).
// mitmweb forwards to llama-router at :11434. Override with LLAMA_SERVER env var.
const LLAMA_SERVER = process.env.LLAMA_SERVER ?? 'http://localhost:8080';

export default defineConfig({
	resolve: {
		alias: {
			'katex-fonts': resolve('node_modules/katex/dist/fonts')
		}
	},
	build: {
		assetsInlineLimit: MAX_ASSET_SIZE,
		chunkSizeWarningLimit: 3072,
		minify: ENABLE_JS_MINIFICATION
	},
	css: {
		preprocessorOptions: {
			scss: {
				additionalData: `
					$use-woff2: true;
					$use-woff: false;
					$use-ttf: false;
				`
			}
		}
	},
	plugins: [tailwindcss(), sveltekit(), devtoolsJson()],
	test: {
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'client',
					environment: 'browser',
					browser: {
						enabled: true,
						provider: 'playwright',
						instances: [{ browser: 'chromium' }]
					},
					include: ['tests/client/**/*.svelte.{test,spec}.{js,ts}'],
					setupFiles: ['./vitest-setup-client.ts']
				}
			},
			{
				extends: './vite.config.ts',
				test: {
					name: 'unit',
					environment: 'node',
					include: ['tests/unit/**/*.{test,spec}.{js,ts}']
				}
			},
			{
				extends: './vite.config.ts',
				test: {
					name: 'ui',
					environment: 'browser',
					browser: {
						enabled: true,
						provider: 'playwright',
						instances: [{ browser: 'chromium', headless: true }]
					},
					include: ['tests/stories/**/*.stories.{js,ts,svelte}'],
					setupFiles: ['./.storybook/vitest.setup.ts']
				},
				plugins: [
					storybookTest({
						storybookScript: 'pnpm run storybook --no-open'
					})
				]
			}
		]
	},

	server: {
		proxy: {
			// Proxy llama-server API calls during dev
			'/v1': LLAMA_SERVER,
			'/props': LLAMA_SERVER,
			'/models': LLAMA_SERVER,
			'/cors-proxy': LLAMA_SERVER,
			// Proxy counsel API calls to FastAPI during dev (npm run dev -- with backend running)
			'/api': 'http://localhost:5000'
		},
		headers: {
			'Cross-Origin-Embedder-Policy': 'require-corp',
			'Cross-Origin-Opener-Policy': 'same-origin'
		},
		fs: {
			allow: [searchForWorkspaceRoot(process.cwd()), resolve(__dirname, 'tests')]
		}
	}
});
