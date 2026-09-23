import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import sitemapPlugin from 'vite-plugin-sitemap'
import { visualizer } from 'rollup-plugin-visualizer'
import fs from 'fs'
import path from 'path'

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
  const guides = read('guides.json') as { owner: string; slug: string }[]
  const articles = read('articles.json') as { owner?: unknown; slug?: unknown; langs?: unknown }[]
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
  const english = ['/projects', ...projects.map((p) => `/${p.slug}`), ...guides.map((g) => `/${g.owner}/${g.slug}`), ...articles.filter((a) => (a.langs as string[]).includes('en')).map((a) => `/${a.owner}/${a.slug}`), '/guides', ...comparisonRoutes, '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)]
  // Every organization route also exists in Chinese under /zh (and /zh is the Chinese home);
  // Chinese-only craft articles exist only there.
  const chineseOnly = articles.filter((a) => !(a.langs as string[]).includes('en')).map((a) => `/zh/${a.owner}/${a.slug}`)
  return [...english, '/zh', ...english.map((route) => `/zh${route}`), ...chineseOnly]
}

const orgRoutes = orgPageRoutes()

const apiProxyTarget = (process.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
const allowedHosts = (process.env.VITE_ALLOWED_HOSTS || 'localhost,127.0.0.1')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean)

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
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
      open: !process.env.CI,
      gzipSize: true,
      brotliSize: true,
    }),
  ],
  build: {
    // 代码分割配置
    rollupOptions: {
      output: {
        manualChunks: {
          // React 核心库分离 - 缓存利用率高
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],

          // UI 库分离 - 首页不需要的库
          'ui-vendor': ['lucide-react', 'clsx', 'tailwind-merge'],

          // 状态管理分离 - Dashboard 需要但首页不需要
          'state-vendor': ['zustand', '@tanstack/react-query'],

          // Editor 库分离 - 首页不需要
          'editor-vendor': ['@tiptap/react', '@tiptap/starter-kit'],

          // i18n 库分离
          'i18n-vendor': ['i18next', 'react-i18next'],

          // Markdown 渲染库分离
          'markdown-vendor': ['react-markdown', 'remark-gfm'],

          // 工具库分离
          'utils-vendor': ['diff-match-patch', 'axios'],
        },
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
