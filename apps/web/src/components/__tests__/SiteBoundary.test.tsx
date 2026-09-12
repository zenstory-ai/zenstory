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
})
