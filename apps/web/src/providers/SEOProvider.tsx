import React, { createContext, useContext, useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { pageSEOConfig, defaultSEOConfig } from '../lib/seo-config';
import type { SEOConfig } from '../types/seo';

interface SEOContextType {
  seoConfig: SEOConfig;
  updateSEO: (config: Partial<SEOConfig>) => void;
}

const SEOContext = createContext<SEOContextType | undefined>(undefined);

export function SEOProvider({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const { i18n } = useTranslation();
  const [seoConfig, setSeoConfig] = useState<SEOConfig>(defaultSEOConfig.en);

  useEffect(() => {
    const pathname = location.pathname;
    const lang = i18n.language;
    const langKey = lang.startsWith('zh') ? 'zh' : 'en';

    // 获取当前路由的SEO配置
    let config = pageSEOConfig[pathname]?.[langKey];

    // 文档子页面 /docs/*：沿用 /docs 的配置（公开、可索引）
    if (!config && pathname.startsWith('/docs/')) {
      config = pageSEOConfig['/docs']?.[langKey];
    }

    // 处理动态路由：/project/:projectId
    if (!config && pathname.startsWith('/project/')) {
      config = {
        zh: {
          title: '项目 - zenstory',
          description: 'AI辅助的小说创作项目',
          noindex: true,  // 需要登录，不允许索引
        },
        en: {
          title: 'Project - zenstory',
          description: 'AI-assisted novel writing project',
          noindex: true,
        }
      }[langKey];
    }

    // 如果没有配置，使用默认配置
    if (!config) {
      config = defaultSEOConfig[langKey] ?? defaultSEOConfig.en;
    }

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSeoConfig(config);
  }, [location.pathname, i18n.language]);

  const updateSEO = (updates: Partial<SEOConfig>) => {
    setSeoConfig(prev => ({ ...prev, ...updates }));
  };

  return (
    <SEOContext.Provider value={{ seoConfig, updateSEO }}>
      {children}
    </SEOContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSEO() {
  const context = useContext(SEOContext);
  if (!context) {
    throw new Error('useSEO must be used within SEOProvider');
  }
  return context;
}
