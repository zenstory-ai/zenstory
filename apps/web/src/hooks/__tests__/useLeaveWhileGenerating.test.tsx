import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Link } from 'react-router-dom'
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
          <button onClick={guard.confirmLeave}>leave</button>
          <button onClick={guard.cancelLeave}>stay</button>
        </>
      )}
    </>
  )
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
})
