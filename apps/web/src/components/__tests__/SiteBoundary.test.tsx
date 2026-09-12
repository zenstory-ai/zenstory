import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { SiteBoundary } from '../SiteBoundary'

function renderBoundary(href: string, replace: ReturnType<typeof vi.fn>, children: React.ReactNode) {
  const url = new URL(href)
  return render(
    <MemoryRouter initialEntries={[`${url.pathname}${url.search}${url.hash}`]}>
      <SiteBoundary location={{ href, replace }}>{children}</SiteBoundary>
    </MemoryRouter>,
  )
}

describe('SiteBoundary', () => {
  it('replaces cross-host destinations and does not render app routes while redirecting', async () => {
    const replace = vi.fn()

    renderBoundary(
      'https://zenstory.ai/login?next=%2Fdashboard#form',
      replace,
      <div>application routes</div>,
    )

    expect(screen.queryByText('application routes')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(replace).toHaveBeenCalledOnce()
      expect(replace).toHaveBeenCalledWith('https://app.zenstory.ai/login?next=%2Fdashboard#form')
    })
  })

  it('replaces a React-mounted site root once and never renders the app home there', async () => {
    const replace = vi.fn()

    renderBoundary('https://zenstory.ai/', replace, <div>old app home</div>)

    expect(screen.queryByText('old app home')).not.toBeInTheDocument()
    await waitFor(() => expect(replace).toHaveBeenCalledOnce())
    expect(replace).toHaveBeenCalledWith('https://zenstory.ai/')
  })

  it('renders normally on the app home and unrecognized hosts', () => {
    const replace = vi.fn()
    const { unmount } = renderBoundary('https://app.zenstory.ai/', replace, <div>app home</div>)

    expect(screen.getByText('app home')).toBeInTheDocument()
    expect(replace).not.toHaveBeenCalled()

    unmount()
    renderBoundary('http://localhost:5173/docs', replace, <div>local docs</div>)
    expect(screen.getByText('local docs')).toBeInTheDocument()
    expect(replace).not.toHaveBeenCalled()
  })

  it('intercepts a hydrated apex Link to the static root before the SPA router handles it', () => {
    const replace = vi.fn()
    renderBoundary(
      'https://zenstory.ai/docs/guide',
      replace,
      <a href="https://zenstory.ai/">organization home</a>,
    )

    fireEvent.click(screen.getByRole('link', { name: 'organization home' }))
    expect(replace).toHaveBeenCalledOnce()
    expect(replace).toHaveBeenCalledWith('https://zenstory.ai/')
    expect(screen.queryByRole('link', { name: 'organization home' })).not.toBeInTheDocument()
  })

  it('observes programmatic SPA navigation and blocks the wrong-host route', async () => {
    const replace = vi.fn()
    function ProgrammaticNavigation() {
      const navigate = useNavigate()
      return <button type="button" onClick={() => navigate('/')}>go home</button>
    }

    renderBoundary(
      'https://zenstory.ai/docs/guide',
      replace,
      <ProgrammaticNavigation />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'go home' }))
    await waitFor(() => expect(replace).toHaveBeenCalledWith('https://zenstory.ai/'))
    expect(screen.queryByRole('button', { name: 'go home' })).not.toBeInTheDocument()
  })

  it.each([
    ['middle click', { button: 1 }],
    ['right click', { button: 2 }],
    ['ctrl click', { ctrlKey: true }],
    ['meta click', { metaKey: true }],
    ['shift click', { shiftKey: true }],
    ['alt click', { altKey: true }],
  ])('does not intercept a cross-host %s', (_name, init) => {
    const replace = vi.fn()
    renderBoundary(
      'https://zenstory.ai/docs/guide',
      replace,
      <a href="https://zenstory.ai/login">sign in</a>,
    )
    const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init })

    fireEvent(screen.getByRole('link', { name: 'sign in' }), event)

    expect(event.defaultPrevented).toBe(false)
    expect(replace).not.toHaveBeenCalled()
  })

  it.each([
    ['targeted', <a href="https://zenstory.ai/login" target="_blank">sign in</a>],
    ['download', <a href="https://zenstory.ai/login" download>sign in</a>],
  ])('does not intercept a %s anchor', (_name, anchor) => {
    const replace = vi.fn()
    renderBoundary('https://zenstory.ai/docs/guide', replace, anchor)
    const event = new MouseEvent('click', { bubbles: true, cancelable: true })

    fireEvent(screen.getByRole('link', { name: 'sign in' }), event)

    expect(event.defaultPrevented).toBe(false)
    expect(replace).not.toHaveBeenCalled()
  })

  it('leaves an ordinary same-host non-boundary click untouched', () => {
    const replace = vi.fn()
    renderBoundary(
      'https://zenstory.ai/docs/guide',
      replace,
      <a href="https://zenstory.ai/docs/another-guide">next guide</a>,
    )
    const event = new MouseEvent('click', { bubbles: true, cancelable: true })

    fireEvent(screen.getByRole('link', { name: 'next guide' }), event)

    expect(event.defaultPrevented).toBe(false)
    expect(replace).not.toHaveBeenCalled()
  })
})
