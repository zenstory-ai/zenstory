import { Suspense } from 'react'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const i18nMock = vi.hoisted(() => ({
  isInitialized: false,
  language: 'zh',
  resolvedLanguage: '' as string | undefined,
  hasLoadedNamespace: vi.fn(() => false),
  t: vi.fn((key: string) => key),
}))

vi.mock('i18next', () => ({
  default: i18nMock,
}))

import { PageLoader } from '../PageLoader'

describe('PageLoader', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    document.documentElement.lang = 'zh-CN'
    i18nMock.isInitialized = false
    i18nMock.language = 'zh'
    i18nMock.resolvedLanguage = ''
    i18nMock.hasLoadedNamespace.mockReturnValue(false)
    i18nMock.t.mockImplementation((key: string) => key)
  })

  it('renders immediately inside Suspense without suspending when locale bundle is not loaded yet', () => {
    render(
      <Suspense fallback={<div data-testid="outer-fallback">Outer fallback</div>}>
        <PageLoader />
      </Suspense>,
    )

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('加载中…')).toBeInTheDocument()
    expect(screen.queryByTestId('outer-fallback')).not.toBeInTheDocument()
  })

  it('uses English fallback label before locale bundle finishes loading when language is en or unknown non-zh', () => {
    i18nMock.language = 'en'
    const first = render(<PageLoader />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    first.unmount()

    i18nMock.language = 'fr-FR'
    const second = render(<PageLoader />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    second.unmount()

    i18nMock.language = ''
    document.documentElement.lang = ''
    render(<PageLoader />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('uses loaded i18n common:loading translation once the namespace is ready', () => {
    i18nMock.isInitialized = true
    i18nMock.hasLoadedNamespace.mockReturnValue(true)
    i18nMock.t.mockReturnValue('Loading workspace…')

    render(<PageLoader />)

    expect(screen.getByText('Loading workspace…')).toBeInTheDocument()
  })
})
