import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// The server-rendered shell is Chinese; this reader has switched the app to English.
vi.hoisted(() => {
  document.documentElement.lang = 'zh-CN'
  document.title = 'ZenStory — AI 小说写作工作台'
  document.head.insertAdjacentHTML('beforeend', `<link rel="canonical" href="${window.location.origin}/" data-rh="true">`)
})

vi.mock('react-router-dom', () => ({ useLocation: () => ({ pathname: '/' }) }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ i18n: { language: 'en' } }) }))

import { SEOProvider, useSEO } from '../SEOProvider'

function Title() {
  return <div data-testid="title">{useSEO().seoConfig.title}</div>
}

describe('SEOProvider with a shell in another language', () => {
  it('uses the English page title instead of the Chinese shell title', () => {
    render(<SEOProvider><Title /></SEOProvider>)
    expect(screen.getByTestId('title').textContent).not.toMatch(/[一-鿿]/)
  })
})
