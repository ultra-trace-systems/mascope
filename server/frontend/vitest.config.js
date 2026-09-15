import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

import legal from './scripts/vite-plugin-legal.js'

/**
 * Unit / component test configuration (Vitest).
 * End-to-end tests live in tests/e2e and tests/instrument (Playwright,
 * see playwright.config.js) and are excluded here by the include glob.
 */
export default defineConfig({
  // legal resolves virtual:mascope-legal (NOTICE's copyright line) for src/lib/about.js
  plugins: [vue(), legal({ repoRoot: fileURLToPath(new URL('../..', import.meta.url)) })],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  envPrefix: 'MASCOPE_',
  test: {
    environment: 'happy-dom',
    setupFiles: ['tests/unit/setup/localStorage.js'],
    include: ['tests/unit/**/*.spec.js'],
    coverage: {
      provider: 'v8',
      include: ['src/**'],
      reporter: ['text', 'html']
    }
  }
})
