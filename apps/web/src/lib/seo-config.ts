/**
 * SEO configuration for all application pages.
 *
 * This module provides page-specific SEO metadata including titles, descriptions,
 * keywords, and structured data (JSON-LD schema.org). Supports bilingual content
 * (Chinese and English) for international SEO optimization.
 *
 * Architecture:
 * - Page-specific configs in `pageSEOConfig` keyed by route path
 * - Each page has 'zh' (Chinese) and 'en' (English) variants
 * - `noindex` controls whether search engines should index the page
 * - Structured data (schema) added to public pages for rich search results
 * - Fallback to `defaultSEOConfig` for pages without specific config
 *
 * SEO Strategy:
 * - Public pages (/, /privacy-policy, /terms-of-service): Indexable with schema
 * - Auth pages (/login, /register, /auth/callback): Noindex to avoid auth in search
 * - Dashboard (/dashboard): Noindex, user content not for public indexing
 *
 * Usage:
 * ```ts
 * import { pageSEOConfig, defaultSEOConfig } from '../lib/seo-config';
 *
 * // Get SEO config for a specific page and language
 * const config = pageSEOConfig['/']?.[language] ?? defaultSEOConfig;
 *
 * // Apply to document
 * document.title = config.title;
 * document.querySelector('meta[name="description"]')?.setAttribute('content', config.description);
 *
 * // Check if page should be indexed
 * if (!config.noindex) {
 *   // Add JSON-LD structured data
 *   const script = document.createElement('script');
 *   script.type = 'application/ld+json';
 *   script.textContent = JSON.stringify(config.schema);
 *   document.head.appendChild(script);
 * }
 * ```
 *
 * @module lib/seo-config
 */

import type { SEOPageConfig, SEOConfig } from '../types/seo';
import { generateSoftwareApplicationSchema } from './structured-data';
import { getBaseUrl } from './utils';

/**
 * Generates Schema.org structured data for the home page with correct dynamic URL.
 *
 * Creates SoftwareApplication schema objects for both Chinese and English variants,
 * ensuring the URL is always current (important for deployments across environments).
 *
 * @returns Object with 'zh' and 'en' keys containing SoftwareApplication schema
 *
 * @example
 * ```ts
 * const schema = getHomePageSchema();
 * // Returns:
 * // {
 * //   zh: { '@context': 'https://schema.org', '@type': 'SoftwareApplication', ... },
 * //   en: { '@context': 'https://schema.org', '@type': 'SoftwareApplication', ... }
 * // }
 * ```
 */
function getHomePageSchema(): { zh: SEOConfig["schema"]; en: SEOConfig["schema"] } {
  const baseUrl = getBaseUrl();

  return {
    zh: generateSoftwareApplicationSchema(
      baseUrl,
      'ZenStory',
      '专业的AI小说写作助手，提供智能大纲生成、角色管理、世界观构建等功能'
    ) as SEOConfig["schema"],
    en: generateSoftwareApplicationSchema(
      baseUrl,
      'ZenStory',
      'Professional AI novel writing assistant featuring intelligent outline generation, character management, and world-building'
    ) as SEOConfig["schema"],
  };
}

/**
 * Pre-computed home page schema for static use.
 * Generated at module load time to ensure correct URL.
 */
const homePageSchema = getHomePageSchema();

/**
 * Page-specific SEO configuration indexed by route path.
 *
 * Each route maps to an object with 'zh' (Chinese) and 'en' (English) variants
 * containing localized SEO metadata. Missing fields fall back to defaultSEOConfig.
 *
 * Configuration Structure:
 * - `title`: Page title (shown in browser tab and search results)
 * - `description`: Meta description for search result snippets
 * - `keywords`: Array of SEO keywords (optional, used for meta keywords tag)
 * - `noindex`: If true, adds robots noindex meta tag to prevent search indexing
 * - `schema`: JSON-LD structured data object for rich search results (optional)
 *
 * @example
 * ```ts
 * // Access login page SEO config
 * const loginConfig = pageSEOConfig['/login'];
 *
 * // Get Chinese variant
 * const zhLogin = loginConfig.zh;
 * console.log(zhLogin.title); // "登录 - ZenStory"
 * console.log(zhLogin.noindex); // true (auth pages not indexed)
 *
 * // Get English home page with schema
 * const enHome = pageSEOConfig['/'].en;
 * console.log(enHome.schema); // SoftwareApplication JSON-LD object
 * ```
 */
export const pageSEOConfig: Record<string, SEOPageConfig> = {
  '/': {
    zh: {
      title: 'ZenStory - 创作让人难忘的故事',
      description: 'ZenStory 是一款 AI 小说写作助手，提供大纲生成、角色管理、世界观构建等功能',
      keywords: ['AI写作', '小说创作', '写作助手', '智能创作'],
      noindex: false,  // 公开页面，允许索引
      schema: homePageSchema.zh,
    },
    en: {
      title: 'ZenStory - Create Unforgettable Stories',
      description: 'ZenStory is a professional AI novel writing assistant featuring intelligent outline generation, character management, and world-building',
      keywords: ['AI writing', 'novel writing', 'writing assistant', 'creative writing'],
      noindex: false,
      schema: homePageSchema.en,
    }
  },
  '/login': {
    zh: {
      title: '登录 - ZenStory',
      description: '登录你的 ZenStory 账号',
      noindex: true,  // 需要登录，不允许索引
    },
    en: {
      title: 'Login - ZenStory',
      description: 'Sign in to your ZenStory account',
      noindex: true,
    }
  },
  '/register': {
    zh: {
      title: '注册 - ZenStory',
      description: '创建 ZenStory 账号',
      noindex: true,  // 需要登录，不允许索引
    },
    en: {
      title: 'Register - ZenStory',
      description: 'Create a ZenStory account',
      noindex: true,
    }
  },
  '/dashboard': {
    zh: {
      title: '工作台 - ZenStory',
      description: '管理你的写作项目',
      noindex: true,  // 需要登录，不允许索引
    },
    en: {
      title: 'Dashboard - ZenStory',
      description: 'Manage your writing projects',
      noindex: true,
    }
  },
  // Signed-in pages: the tab names the page in the interface language instead
  // of the generic site title.
  '/dashboard/projects': {
    zh: { title: '我的项目 - ZenStory', description: '管理你的写作项目', noindex: true },
    en: { title: 'My Projects - ZenStory', description: 'Manage your writing projects', noindex: true },
  },
  '/dashboard/materials': {
    zh: { title: '素材库 - ZenStory', description: '上传参考小说并拆解', noindex: true },
    en: { title: 'Materials - ZenStory', description: 'Upload and break down reference novels', noindex: true },
  },
  '/dashboard/skills': {
    zh: { title: '技能 - ZenStory', description: '管理写作技能', noindex: true },
    en: { title: 'Skills - ZenStory', description: 'Manage writing skills', noindex: true },
  },
  '/dashboard/billing': {
    zh: { title: '订阅权益 - ZenStory', description: '套餐与用量', noindex: true },
    en: { title: 'Plan & Usage - ZenStory', description: 'Plan and usage', noindex: true },
  },
  '/onboarding/persona': {
    zh: { title: '告诉我们你怎么写作 - ZenStory', description: '选择你的写作方向', noindex: true },
    en: { title: 'How You Write - ZenStory', description: 'Choose how you write', noindex: true },
  },
  '/verify-email': {
    zh: {
      title: '验证邮箱 - ZenStory',
      description: '验证你的邮箱地址',
      noindex: true,
    },
    en: {
      title: 'Verify Email - ZenStory',
      description: 'Verify your email address',
      noindex: true,
    }
  },
  '/docs': {
    zh: {
      title: '文档 - ZenStory',
      description: 'ZenStory 使用文档：快速入门、用户指南、进阶技巧、参考资料与故障排除',
      keywords: ['ZenStory 文档', 'AI小说写作教程', '写作工作台'],
      noindex: false,  // 公开文档，允许索引
    },
    en: {
      title: 'Documentation - ZenStory',
      description: 'ZenStory documentation: getting started, user guide, advanced workflows, reference and troubleshooting',
      keywords: ['ZenStory docs', 'AI novel writing guide', 'writing workbench'],
      noindex: false,
    }
  },
  '/pricing': {
    zh: {
      title: '定价 - ZenStory',
      description: 'ZenStory 订阅方案与权益对比',
      noindex: false,  // 公开页面
    },
    en: {
      title: 'Pricing - ZenStory',
      description: 'ZenStory subscription plans and benefits',
      noindex: false,
    }
  },
  '/privacy-policy': {
    zh: {
      title: '隐私政策 - ZenStory',
      description: 'ZenStory 的隐私政策',
      noindex: false,  // 公开页面
    },
    en: {
      title: 'Privacy Policy - ZenStory',
      description: 'Privacy Policy of ZenStory',
      noindex: false,
    }
  },
  '/terms-of-service': {
    zh: {
      title: '服务条款 - ZenStory',
      description: 'ZenStory 的服务条款',
      noindex: false,  // 公开页面
    },
    en: {
      title: 'Terms of Service - ZenStory',
      description: 'Terms of Service of ZenStory',
      noindex: false,
    }
  },
  '/auth/callback': {
    zh: {
      title: '授权回调 - ZenStory',
      description: 'OAuth 授权回调处理',
      noindex: true,  // 回调页面，不允许索引
    },
    en: {
      title: 'Auth Callback - ZenStory',
      description: 'OAuth authorization callback handler',
      noindex: true,
    }
  }
};

/**
 * Default SEO configuration used as fallback for pages without specific config.
 *
 * Applied when a route is not found in pageSEOConfig or when accessing
 * a language variant that doesn't exist for a page.
 *
 * @example
 * ```ts
 * // Fallback pattern
 * const route = '/unknown-page';
 * const language = 'en';
 * const config = pageSEOConfig[route]?.[language] ?? defaultSEOConfig;
 *
 * console.log(config.title); // "ZenStory - 创作让人难忘的故事"
 * console.log(config.noindex); // false (index by default)
 * ```
 */
export const defaultSEOConfig: SEOPageConfig = {
  zh: {
    title: 'ZenStory - 创作让人难忘的故事',
    description: '专业的AI小说写作助手，提供智能大纲生成、角色管理、世界观构建等功能',
    noindex: false,
  },
  en: {
    title: 'ZenStory - Create Unforgettable Stories',
    description: 'Professional AI novel writing assistant featuring intelligent outline generation, character management, and world-building',
    noindex: false,
  }
};
