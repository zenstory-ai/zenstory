import { fireEvent, render, renderHook, screen } from '@testing-library/react'
import zhCommon from '../../../public/locales/zh/common.json'
import enCommon from '../../../public/locales/en/common.json'

const { mockPathname, mockLanguage } = vi.hoisted(() => ({
  mockPathname: { value: '/' },
  mockLanguage: { value: 'zh-CN' },
}))

function lookupCommonKey(locale: 'zh' | 'en', key: string): string | undefined {
  const path = key.replace(/^common:/, '').split('.')
  let node: unknown = locale === 'zh' ? zhCommon : enCommon
  for (const part of path) {
    if (node && typeof node === 'object' && part in node) {
      node = (node as Record<string, unknown>)[part]
    } else {
      return undefined
    }
  }
  return typeof node === 'string' ? node : undefined
}

vi.mock('react-router-dom', () => ({
  useLocation: () => ({
    pathname: mockPathname.value,
  }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => {
    const langKey: 'zh' | 'en' = mockLanguage.value.startsWith('zh') ? 'zh' : 'en'
    return {
      t: (key: string, options?: { defaultValue?: string }) =>
        lookupCommonKey(langKey, key) ?? options?.defaultValue ?? key,
      i18n: {
        language: mockLanguage.value,
      },
    }
  },
}))

import { SEOProvider, useSEO } from '../SEOProvider'

function SEOConsumer() {
  const { seoConfig, updateSEO } = useSEO()

  return (
    <div>
      <div data-testid="seo-title">{seoConfig.title}</div>
      <div data-testid="seo-description">{seoConfig.description}</div>
      <div data-testid="seo-noindex">{String(Boolean(seoConfig.noindex))}</div>
      <button type="button" onClick={() => updateSEO({ title: 'Overridden title' })}>
        update
      </button>
    </div>
  )
}

describe('SEOProvider', () => {
  beforeEach(() => {
    mockPathname.value = '/'
    mockLanguage.value = 'zh-CN'
  })

  it('provides route-specific SEO config for the active language', () => {
    mockPathname.value = '/login'
    mockLanguage.value = 'en'

    render(
      <SEOProvider>
        <SEOConsumer />
      </SEOProvider>,
    )

    expect(screen.getByTestId('seo-title')).toHaveTextContent('Login - ZenStory')
    expect(screen.getByTestId('seo-description')).toHaveTextContent('Sign in to your ZenStory account')
    expect(screen.getByTestId('seo-noindex')).toHaveTextContent('true')
  })

  it('falls back to dynamic project SEO for project routes in zh and non-zh locales via i18n keys', () => {
    mockPathname.value = '/project/project-1'
    mockLanguage.value = 'zh-CN'

    const zhRender = render(
      <SEOProvider>
        <SEOConsumer />
      </SEOProvider>,
    )

    expect(screen.getByTestId('seo-title')).toHaveTextContent('项目 - ZenStory')
    expect(screen.getByTestId('seo-description')).toHaveTextContent('AI辅助的小说创作项目')
    expect(screen.getByTestId('seo-noindex')).toHaveTextContent('true')
    zhRender.unmount()

    mockLanguage.value = 'en'
    const enRender = render(
      <SEOProvider>
        <SEOConsumer />
      </SEOProvider>,
    )
    expect(screen.getByTestId('seo-title')).toHaveTextContent('Project - ZenStory')
    expect(screen.getByTestId('seo-description')).toHaveTextContent('AI-assisted novel writing project')
    expect(screen.getByTestId('seo-noindex')).toHaveTextContent('true')
    enRender.unmount()

    mockLanguage.value = 'fr-FR'
    render(
      <SEOProvider>
        <SEOConsumer />
      </SEOProvider>,
    )
    expect(screen.getByTestId('seo-title')).toHaveTextContent('Project - ZenStory')
    expect(screen.getByTestId('seo-description')).toHaveTextContent('AI-assisted novel writing project')
  })

  it('merges manual SEO overrides with the active route config', () => {
    mockPathname.value = '/dashboard'
    mockLanguage.value = 'zh-CN'

    render(
      <SEOProvider>
        <SEOConsumer />
      </SEOProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'update' }))

    expect(screen.getByTestId('seo-title')).toHaveTextContent('Overridden title')
    expect(screen.getByTestId('seo-description')).toHaveTextContent('管理你的写作项目')
  })

  it('throws when useSEO is called outside the provider', () => {
    expect(() => renderHook(() => useSEO())).toThrow('useSEO must be used within SEOProvider')
  })
})
