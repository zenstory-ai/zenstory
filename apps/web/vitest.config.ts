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
    // (and is faster here, since files run in parallel). Vitest 4 removed
    // `poolOptions`; isolation is configured by the top-level `isolate` below.
    pool: 'forks',
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
      // Vitest 4 removed `coverage.all` and only reports files loaded during the
      // run unless `include` is set. List the source roots so untested files
      // still count toward the thresholds, as they did under Vitest 3.
      include: ['src/**/*.{ts,tsx}', 'scripts/**/*.{mjs,ts}', 'api/**/*.js'],
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
      // Re-baselined for Vitest 4's AST-based V8 remapping, which counts real
      // statements/branches instead of physical lines (same files, same tests:
      // v3 76.1/75.8/67.7/76.1 -> v4 70.8/60.5/64.8/68.2 lines/branches/functions/
      // statements). Lines, branches and statements keep the headroom they had
      // under Vitest 3; functions still passes at its old value.
      thresholds: {
        lines: 68,
        functions: 61,
        branches: 55,
        statements: 66,
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
