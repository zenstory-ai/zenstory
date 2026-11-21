import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastContainer } from '../Toast'
import { toast } from '../../lib/toast'

describe('ToastContainer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('announces errors urgently and success/info politely without duplicate messages', () => {
    render(<ToastContainer />)
    act(() => {
      toast.error('Cannot save')
      toast.success('Saved')
      toast.info('Ready')
      toast.info('Ready')
    })
    expect(screen.getByRole('alert')).toHaveTextContent('Cannot save')
    expect(screen.getAllByRole('status')).toHaveLength(2)
    expect(screen.getByText('Ready')).toHaveAttribute('aria-atomic', 'true')
  })

  it('renders toast messages and auto-dismisses them', async () => {
    render(<ToastContainer />)

    act(() => {
      toast.success('Saved successfully')
    })

    expect(screen.getByText('Saved successfully')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })

    expect(screen.queryByText('Saved successfully')).not.toBeInTheDocument()
  })

  it('deduplicates identical toasts and keeps only the latest three', () => {
    render(<ToastContainer />)

    act(() => {
      toast.info('Duplicate')
      toast.info('Duplicate')
      toast.error('Second')
      toast.success('Third')
      toast.info('Fourth')
    })

    expect(screen.queryByText('Duplicate')).not.toBeInTheDocument()
    expect(screen.getByText('Second')).toBeInTheDocument()
    expect(screen.getByText('Third')).toBeInTheDocument()
    expect(screen.getByText('Fourth')).toBeInTheDocument()
  })

  it('uses AA contrast solid tokens and rounded-xl corners for all toast types', () => {
    render(<ToastContainer />)

    act(() => {
      toast.error('Error toast')
      toast.success('Success toast')
      toast.info('Info toast')
    })

    const errorEl = screen.getByText('Error toast')
    const successEl = screen.getByText('Success toast')
    const infoEl = screen.getByText('Info toast')

    for (const el of [errorEl, successEl, infoEl]) {
      expect(el.className).toContain('text-[hsl(var(--primary-foreground))]')
      expect(el.className).toContain('rounded-xl')
    }
    expect(errorEl.className).toContain('bg-[hsl(var(--danger-solid))]')
    expect(successEl.className).toContain('bg-[hsl(var(--success-solid))]')
    expect(infoEl.className).toContain('bg-[hsl(var(--primary))]')
  })
})
