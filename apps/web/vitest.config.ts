import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    // Configure module resolution to handle case-sensitivity issues
    deps: {
      // Force vitest to use the actual filetree directory, not FileTree.tsx
      // when resolving '../filetree' imports
      moduleDirectories: ['node_modules', 'src'],
    },
    server: {
      deps: {
        // Inline react-router and react-router-dom to avoid ESM/CJS interop issues
        inline: ['react-router', 'react-router-dom'],
      },
    },
    environment: 'happy-dom',
    environmentOptions: {
      happyDOM: {
        settings: {
          disableJavaScriptEvaluation: false,
          disableCSSFileLoading: true,
          disableIframePageLoading: true,
        },
      },
    },
    setupFiles: ['./src/test/setup.ts'],
    exclude: [
      'scripts/__tests__/*.test.mjs', // node:test contracts run separately via test:site
      '**/node_modules/**',
      '**/dist/**',
      '**/e2e/**',
      '**/playwright.config.ts',
    ],
    // Run each test file in its own forked process with isolation. The previous
    // shared single-thread pool with top-level `isolate: false` let global state
    // leak across files (module mocks like react-i18next, the i18n singleton),
    // which caused hundreds of order-dependent failures — files passed alone but
    // failed in the full suite. Per-file process isolation removes that coupling
    // (and is faster here, since files run in parallel).
    pool: 'forks',
    poolOptions: {
      forks: {
        isolate: true,
      },
    },
    testTimeout: 10000,
    hookTimeout: 10000,
    teardownTimeout: 20000, // Increase teardown timeout
    isolate: true,
    benchmark: {
      include: ['**/*'],
      exclude: ['node_modules', 'dist', '.idea', '.git', '.cache'],
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov', 'json-summary'],
      exclude: [
        'node_modules/**',
        'dist/**',
        'e2e/**',
        'playwright/**',
        'playwright.config.ts',
        'src/test/**',
        'src/lib/i18n.ts', // i18n configuration
        'src/lib/seo-config.ts', // SEO configuration
        'src/lib/structured-data.ts', // SEO structured data
        '**/*.d.ts',
        '**/*.config.*',
      ],
      thresholds: {
        lines: 74,
        functions: 61,
        branches: 71,
        statements: 74,
      },
    },
    globals: true,
    css: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      // Explicit alias for filetree module to avoid case-sensitivity issues on macOS
      // where 'filetree' and 'FileTree' are treated as the same path
      '@/components/filetree': path.resolve(__dirname, './src/components/filetree/index.ts'),
    },
  },
})
