import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act, waitFor } from '@testing-library/react'
import { BrowserRouter, MemoryRouter, Routes, Route, Link } from 'react-router-dom'
import { useLeaveWhileGenerating } from '../useLeaveWhileGenerating'

function Workbench({ active }: { active: boolean }) {
  const guard = useLeaveWhileGenerating(active)
  return (
    <>
      <p>workbench</p>
      <Link to="/dashboard">dashboard</Link>
      <Link to="/project/a?file=2">same page</Link>
      {guard.leavePending && (
        <>
          {guard.roundEnded && <p>round ended</p>}
          <button onClick={guard.confirmLeave}>leave</button>
          <button onClick={guard.cancelLeave}>stay</button>
        </>
      )}
    </>
  )
}

function renderInBrowserHistory(active: boolean) {
  window.history.replaceState(null, '', '/dashboard')
  window.history.pushState(null, '', '/project/a')
  const ui = (isActive: boolean) => (
    <BrowserRouter>
      <Routes>
        <Route path="/project/a" element={<Workbench active={isActive} />} />
        <Route path="/dashboard" element={<p>dashboard page</p>} />
      </Routes>
    </BrowserRouter>
  )
  const view = render(ui(active))
  return { ...view, setActive: (isActive: boolean) => view.rerender(ui(isActive)) }
}

const pressBack = async () => {
  await act(async () => {
    window.history.back()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

function renderAt(active: boolean) {
  return render(
    <MemoryRouter initialEntries={['/project/a']}>
      <Routes>
        <Route path="/project/a" element={<Workbench active={active} />} />
        <Route path="/dashboard" element={<p>dashboard page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('useLeaveWhileGenerating', () => {
  afterEach(() => cleanup())

  it('asks before leaving the page mid-round and leaves only when confirmed', () => {
    renderAt(true)

    fireEvent.click(screen.getByText('dashboard'))
    expect(screen.getByText('workbench')).toBeInTheDocument()

    fireEvent.click(screen.getByText('stay'))
    expect(screen.queryByText('leave')).not.toBeInTheDocument()
    expect(screen.getByText('workbench')).toBeInTheDocument()

    fireEvent.click(screen.getByText('dashboard'))
    fireEvent.click(screen.getByText('leave'))
    expect(screen.getByText('dashboard page')).toBeInTheDocument()
  })

  it('never blocks staying on the same page or leaving when nothing is generating', () => {
    const { unmount } = renderAt(true)
    fireEvent.click(screen.getByText('same page'))
    expect(screen.queryByText('leave')).not.toBeInTheDocument()
    unmount()

    renderAt(false)
    fireEvent.click(screen.getByText('dashboard'))
    expect(screen.getByText('dashboard page')).toBeInTheDocument()
  })

  it('asks before the browser Back button / system back leaves mid-round', async () => {
    renderInBrowserHistory(true)

    await pressBack()
    expect(screen.getByText('leave')).toBeInTheDocument()
    expect(screen.getByText('workbench')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/project/a')

    // Staying keeps guarding: the next Back asks again.
    fireEvent.click(screen.getByText('stay'))
    await pressBack()
    expect(screen.getByText('leave')).toBeInTheDocument()

    fireEvent.click(screen.getByText('leave'))
    await waitFor(() => expect(screen.getByText('dashboard page')).toBeInTheDocument())
    expect(window.location.pathname).toBe('/dashboard')
  })

  it('keeps the author\'s choice when the round ends while the dialog is open', async () => {
    const { setActive } = renderInBrowserHistory(true)
    fireEvent.click(screen.getByText('dashboard'))
    expect(screen.getByText('leave')).toBeInTheDocument()

    setActive(false)
    // The dialog stays (now saying nothing will be cut off) and 离开 still leaves.
    expect(screen.getByText('round ended')).toBeInTheDocument()
    fireEvent.click(screen.getByText('leave'))
    await waitFor(() => expect(screen.getByText('dashboard page')).toBeInTheDocument())
  })

  it('leaves no dead Back press behind after a same-page link during the round', async () => {
    const { setActive } = renderInBrowserHistory(true)
    fireEvent.click(screen.getByText('same page'))
    expect(window.location.search).toBe('?file=2')
    expect(screen.queryByText('leave')).not.toBeInTheDocument()

    // Back is still guarded after the same-page link.
    await pressBack()
    expect(screen.getByText('leave')).toBeInTheDocument()
    fireEvent.click(screen.getByText('stay'))

    setActive(false)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(window.location.search).toBe('?file=2')

    // Same as without the guard: Back returns to the page before the link, then to the dashboard.
    await pressBack()
    expect(window.location.pathname).toBe('/project/a')
    expect(window.location.search).toBe('')
    await pressBack()
    await waitFor(() => expect(screen.getByText('dashboard page')).toBeInTheDocument())
  })

  it('removes its Back guard once the round is over, so Back works normally', async () => {
    const { setActive } = renderInBrowserHistory(true)
    setActive(false)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })

    await pressBack()
    expect(screen.queryByText('leave')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('dashboard page')).toBeInTheDocument())
  })
})
