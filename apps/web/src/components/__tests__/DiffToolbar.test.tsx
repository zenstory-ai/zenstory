import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { PendingEdit } from '../../types'

const viewport = vi.hoisted(() => ({ isMobile: false }))

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: viewport.isMobile }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

import { DiffToolbar } from '../DiffToolbar'

const edits = [
  { id: 'e1', status: 'pending' },
  { id: 'e2', status: 'pending' },
] as unknown as PendingEdit[]

const renderToolbar = () =>
  render(<DiffToolbar pendingEdits={edits} onAcceptAll={vi.fn()} onRejectAll={vi.fn()} onFinish={vi.fn()} />)

describe('DiffToolbar review hint', () => {
  beforeEach(() => {
    viewport.isMobile = false
  })

  it('points to the queue on the right with shortcuts on wide screens', () => {
    renderToolbar()
    expect(screen.getByText('editor:reviewModeHint')).toBeInTheDocument()
  })

  it('points below and drops the shortcut mention on phones', () => {
    viewport.isMobile = true
    renderToolbar()
    expect(screen.getByText('editor:reviewModeHintMobile')).toBeInTheDocument()
    expect(screen.queryByText('editor:reviewModeHint')).not.toBeInTheDocument()
  })
})
