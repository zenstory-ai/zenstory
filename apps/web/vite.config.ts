import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import sitemapPlugin from 'vite-plugin-sitemap'
import { visualizer } from 'rollup-plugin-visualizer'
import fs from 'fs'
import path from 'path'
import { localeBundlePlugin } from './scripts/locale-bundle-plugin'

/**
 * Public docs routes for the sitemap, derived from the same markdown tree that
 * DocsPage loads via import.meta.glob. Leaf pages only: section paths such as
 * /docs/getting-started have no markdown file behind them and render empty.
 * The en/ subtree is the translation of the same slugs, not extra routes.
 */
function publicDocsRoutes(): string[] {
  const docsDir = path.resolve(__dirname, 'docs')
  const routes: string[] = []
  const walk = (dir: string, rel: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'en') continue
      const relPath = rel ? `${rel}/${entry.name}` : entry.name
      if (entry.isDirectory()) walk(path.join(dir, entry.name), relPath)
      else if (entry.name.endsWith('.md') && entry.name !== 'README.md') {
        routes.push(`/docs/${relPath.replace(/\.md$/, '')}`)
      }
    }
  }
  if (fs.existsSync(docsDir)) walk(docsDir, '')
  return routes.sort()
}

const docsRoutes = publicDocsRoutes()

/**
 * Static organization pages emitted after the build by scripts/build-org-pages.mjs
 * (see that file). Listed here so the sitemap includes them.
 */
export function orgPageRoutes(contentDir = path.resolve(__dirname, 'content')): string[] {
  const read = (f: string) => JSON.parse(fs.readFileSync(path.resolve(contentDir, f), 'utf8'))
  const projects = read('projects.json') as { slug: string }[]
  const glossary = read('glossary.json') as { slug: string }[]
  const guides = read('guides.json') as { owner: string; slug: string; topic?: string }[]
  const topics = read('guide-topics.json') as { slug: string }[]
  const articles = read('articles.json') as { owner?: unknown; slug?: unknown; langs?: unknown; topic?: string }[]
  for (const article of articles) {
    if (typeof article.slug !== 'string' || !/^[a-z0-9-]+$/.test(article.slug) || !projects.some((p) => p.slug === article.owner)) throw new Error('Invalid article identity')
    if (!Array.isArray(article.langs) || !article.langs.includes('zh') || article.langs.some((lang) => lang !== 'zh' && lang !== 'en')) throw new Error('Invalid article languages')
  }
  const comparisons = read('comparisons.json') as { slug?: unknown; options?: { project?: unknown }[] }[]
  const expectedProjects = ['oh-story', 'dsh', 'workbench']
  const comparisonRoutes = new Set<string>()
  for (const comparison of comparisons) {
    if (typeof comparison.slug !== 'string' || !/^[a-z0-9-]+$/.test(comparison.slug)) throw new Error('Invalid comparison identity')
    const route = `/compare/${comparison.slug}`
    if (comparisonRoutes.has(route)) throw new Error('Duplicate comparison route')
    comparisonRoutes.add(route)
    const options = comparison.options?.map((option) => option.project)
    if (!options || options.length !== expectedProjects.length || options.some((project, index) => project !== expectedProjects[index]) || options.some((project) => !projects.some((candidate) => candidate.slug === project))) throw new Error('Invalid comparison options')
  }
  // Match the producer's per-language topic pagination and legacy fallback.
  const topicPageSize = 24
  const fallbackTopics: Record<string, string> = { 'oh-story': 'plot-and-outline', 'drama-skills': 'short-drama', 'novel-to-game': 'interactive-games', 'video-recap': 'video-recaps', dsh: 'getting-started' }
  const topicRoutes = (lang: string) => {
    const items = [...guides, ...articles.filter(article => (article.langs as string[]).includes(lang))]
    return topics.flatMap(topic => {
      const count = items.filter(item => (item.topic ?? fallbackTopics[String(item.owner)]) === topic.slug).length
      return Array.from({ length: Math.max(1, Math.ceil(count / topicPageSize)) }, (_, index) =>
        `/guides/${topic.slug}${index > 0 ? `/page/${index + 1}` : ''}`)
    })
  }
  const english = ['/projects', ...projects.map((p) => `/${p.slug}`), ...guides.map((g) => `/${g.owner}/${g.slug}`), ...articles.filter((a) => (a.langs as string[]).includes('en')).map((a) => `/${a.owner}/${a.slug}`), '/guides', ...comparisonRoutes, '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)]
  // Every organization route also exists in Chinese under /zh (and /zh is the Chinese home);
  // Chinese-only craft articles exist only there.
  const chineseOnly = articles.filter((a) => !(a.langs as string[]).includes('en')).map((a) => `/zh/${a.owner}/${a.slug}`)
  return [...english, ...topicRoutes('en'), '/zh', ...english.map((route) => `/zh${route}`), ...topicRoutes('zh').map(route => `/zh${route}`), ...chineseOnly]
}

const orgRoutes = orgPageRoutes()

/**
 * Writes version.json naming the built entry script. The app polls it to notice
 * that a newer build was deployed while a tab stayed open (src/lib/appUpdate.ts).
 */
function appVersionManifest(): Plugin {
  let outDir = 'dist'
  return {
    name: 'zenstory-app-version-manifest',
    apply: 'build',
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir)
    },
    closeBundle() {
      const html = fs.readFileSync(path.join(outDir, 'index.html'), 'utf8')
      const entry = html.match(/<script type="module"[^>]*\ssrc="([^"]+)"/)?.[1]
      if (!entry) throw new Error('Built index.html has no module entry script for version.json')
      fs.writeFileSync(path.join(outDir, 'version.json'), `${JSON.stringify({ entry })}\n`)
    },
  }
}

export const DEFAULT_PRODUCTION_API_ORIGIN = 'https://api.zenstory.ai'

/**
 * Resolves the API origin to emit in `<link rel="preconnect">`, or `null` when
 * no external preconnect should be emitted (local dev, HTTP/E2E builds, or
 * isolated Vercel Preview builds that route same-origin `/api/*` to staging).
 */
export function resolveApiPreconnectOrigin(options: {
  command: 'serve' | 'build'
  apiBaseUrl?: string
  vercelEnv?: string
}): string | null {
  if (options.command === 'serve') return null

  const raw = options.apiBaseUrl?.trim() ?? ''
  if (raw) {
    try {
      const parsed = new URL(raw)
      return parsed.protocol === 'https:' ? parsed.origin : null
    } catch {
      return null
    }
  }

  if (options.vercelEnv?.trim() === 'preview') return null
  return DEFAULT_PRODUCTION_API_ORIGIN
}

/**
 * Injects `<link rel="preconnect" href="<origin>" crossorigin />` into the built
 * app shell only when building for an HTTPS API origin (production or explicit
 * HTTPS staging), preventing speculative connections to prod during local dev,
 * HTTP E2E runs, or isolated Vercel Preview builds.
 */
function apiPreconnectPlugin(): Plugin {
  let targetOrigin: string | null = null
  return {
    name: 'zenstory-api-preconnect',
    configResolved(config) {
      const env = loadEnv(config.mode, config.envDir || config.root, 'VITE_')
      targetOrigin = resolveApiPreconnectOrigin({
        command: config.command,
        apiBaseUrl: env.VITE_API_BASE_URL ?? process.env.VITE_API_BASE_URL,
        vercelEnv: process.env.VERCEL_ENV,
      })
    },
    transformIndexHtml(html) {
      if (!targetOrigin) return html
      const preconnectTag = `<link rel="preconnect" href="${targetOrigin}" crossorigin />`
      if (html.includes(preconnectTag)) return html
      const iconMarker = '<link rel="icon"'
      if (html.includes(iconMarker)) {
        return html.replace(iconMarker, `${preconnectTag}\n        ${iconMarker}`)
      }
      return html.replace('</head>', `        ${preconnectTag}\n    </head>`)
    },
  }
}

const REACT_VENDOR_PACKAGES = new Set([
  'react',
  'react-dom',
  'scheduler',
  'react-router',
  'react-router-dom',
])
const UI_VENDOR_PACKAGES = new Set(['lucide-react', 'clsx', 'tailwind-merge'])
const STATE_VENDOR_PACKAGES = new Set(['zustand', '@tanstack/react-query', '@tanstack/query-core'])
const I18N_VENDOR_PACKAGES = new Set(['i18next', 'react-i18next', 'i18next-browser-languagedetector'])
const UTILS_VENDOR_PACKAGES = new Set(['diff-match-patch', 'axios'])
const MARKDOWN_VENDOR_PACKAGES = new Set([
  'react-markdown',
  'remark-gfm',
  'remark-parse',
  'remark-rehype',
  'unified',
  'vfile',
  'vfile-message',
  'property-information',
  'markdown-table',
])

function npmPackageName(id: string): string | null {
  const normalized = id.replace(/\\/g, '/')
  const marker = '/node_modules/'
  const idx = normalized.lastIndexOf(marker)
  if (idx === -1) return null
  const rest = normalized.slice(idx + marker.length)
  if (!rest || rest.startsWith('.pnpm/')) return null
  const parts = rest.split('/')
  if (parts[0].startsWith('@') && parts.length >= 2) {
    return `${parts[0]}/${parts[1]}`
  }
  return parts[0]
}

function resolveManualChunk(id: string): string | undefined {
  const pkg = npmPackageName(id)
  if (!pkg) return undefined
  if (REACT_VENDOR_PACKAGES.has(pkg)) return 'react-vendor'
  if (UI_VENDOR_PACKAGES.has(pkg)) return 'ui-vendor'
  if (STATE_VENDOR_PACKAGES.has(pkg)) return 'state-vendor'
  if (I18N_VENDOR_PACKAGES.has(pkg)) return 'i18n-vendor'
  if (UTILS_VENDOR_PACKAGES.has(pkg)) return 'utils-vendor'
  if (pkg === 'posthog-js') return 'posthog-vendor'
  if (
    MARKDOWN_VENDOR_PACKAGES.has(pkg) ||
    pkg.startsWith('micromark') ||
    pkg.startsWith('mdast-') ||
    pkg.startsWith('hast-') ||
    pkg.startsWith('unist-')
  ) {
    return 'markdown-vendor'
  }
  return undefined
}

const apiProxyTarget = (process.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
const allowedHosts = (process.env.VITE_ALLOWED_HOSTS || 'localhost,127.0.0.1')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean)

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    localeBundlePlugin(path.resolve(__dirname, 'public/locales')),
    react(),
    apiPreconnectPlugin(),
    appVersionManifest(),
    sitemapPlugin({
      // 从环境变量读取 hostname,开发环境用 localhost
      hostname: process.env.VITE_BASE_URL || 'http://localhost:5173',
      // 添加 SPA 路由(不是实际 HTML 文件)
      dynamicRoutes: [
        ...orgRoutes,
        '/docs',
        ...docsRoutes,
        '/pricing',
        '/privacy-policy',
        '/terms-of-service',
      ],
      // 排除需要登录的页面
      exclude: [
        '/login',
        '/register',
        '/forgot-password',
        '/onboarding/',
        '/dashboard',
        '/profile',
        '/project/',
        '/materials/',
        '/admin',
        '/verify-email',
        '/auth',
      ],
      // 配置页面更新频率
      changefreq: 'weekly',
      // 配置页面优先级
      priority: {
        '/': 1.0,
        '/projects': 0.9,
        ...Object.fromEntries(orgRoutes.filter((r) => r !== '/projects').map((route) => [route, route.startsWith('/glossary') ? 0.7 : 0.9])),
        '/docs': 0.8,
        ...Object.fromEntries(docsRoutes.map((route) => [route, 0.6])),
        '/pricing': 0.7,
        '/privacy-policy': 0.3,
        '/terms-of-service': 0.3,
      },
      // robots.txt 由 public/robots.txt 单独管理
      generateRobotsTxt: false,
    }),
    visualizer({
      open: false,
      gzipSize: true,
      brotliSize: true,
    }),
  ],
  build: {
    // 代码分割配置
    rollupOptions: {
      output: {
        manualChunks: resolveManualChunk,
      },
    },

    // CSS 代码分割
    cssCodeSplit: true,

    // 调整 chunk 大小警告阈值
    chunkSizeWarningLimit: 1000,

    // 压缩配置 - 使用 esbuild (Vite 默认)
    minify: 'esbuild',
  },

  // 依赖预构建优化 - 移除 force: true
  optimizeDeps: {
    // 只预构建必要依赖
    include: [
      'react',
      'react-dom',
      'react-router-dom',
    ],
    // 排除不需要预构建的库
    exclude: [],
  },

  resolve: {
    dedupe: ['react', 'react-dom', 'react-router-dom'],
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  server: {
    allowedHosts,
    hmr: true,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
