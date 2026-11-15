import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import InspirationDetailPage from '../InspirationDetailPage'

const mockNavigate = vi.fn()
const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
const mockGetDetail = vi.fn()
const mockCopyInspiration = vi.fn()
const mockResetDetail = vi.fn()

let mockCurrentDetail: Record<string, unknown> | null = null
let mockIsDetailLoading = false
let mockIsCopying = false
let mockInspirationId = 'insp-1'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => ({ inspirationId: mockInspirationId }),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          copySuccess: 'Copied successfully',
          copyError: 'Copy failed',
          loadError: 'Failed to load inspiration',
          cancel: 'Cancel',
          retry: 'Retry',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

vi.mock('../../hooks/useInspirations', () => ({
  useInspirations: () => ({
    getDetail: mockGetDetail,
    currentDetail: mockCurrentDetail,
    isDetailLoading: mockIsDetailLoading,
    copyInspiration: mockCopyInspiration,
    isCopying: mockIsCopying,
    resetDetail: mockResetDetail,
  }),
}))

vi.mock('../../components/inspirations', () => ({
  InspirationDetailDialog: ({
    inspiration,
    onClose,
    onCopy,
  }: {
    inspiration: { id: string; title: string }
    onClose: () => void
    onCopy: (id: string, projectName?: string) => Promise<void>
  }) => (
    <div>
      <div>{inspiration.title}</div>
      <button onClick={onClose}>Close dialog</button>
      <button
        onClick={() => {
          void onCopy(inspiration.id, 'Project X').catch(() => undefined)
        }}
      >
        Copy inspiration
      </button>
    </div>
  ),
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
  },
}))

describe('InspirationDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockInspirationId = 'insp-1'
    mockCurrentDetail = { id: 'insp-1', title: 'Battle Scene' }
    mockIsDetailLoading = false
    mockIsCopying = false
    mockGetDetail.mockResolvedValue(mockCurrentDetail)
    mockCopyInspiration.mockResolvedValue({ success: true, project_id: 'project-1' })
  })

  it('shows loading and failed-load states', async () => {
    mockIsDetailLoading = true
    const { container, unmount } = render(<InspirationDetailPage />)
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()

    unmount()
    mockIsDetailLoading = false
    mockCurrentDetail = null
    mockGetDetail.mockResolvedValueOnce(null)
    mockInspirationId = 'insp-2'
    render(<InspirationDetailPage />)

    expect(await screen.findByText('Failed to load inspiration')).toBeInTheDocument()
  })

  it('copies inspiration and navigates to the target project', async () => {
    render(<InspirationDetailPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Copy inspiration' }))

    await waitFor(() => {
      expect(mockToastSuccess).toHaveBeenCalledWith('Copied successfully')
      expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')
    })
  })

  it('handles close and copy errors', async () => {
    mockCopyInspiration.mockRejectedValueOnce(new Error('copy failed'))
    render(<InspirationDetailPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }))
    expect(mockNavigate).toHaveBeenCalledWith(-1)

    fireEvent.click(screen.getByRole('button', { name: 'Copy inspiration' }))
    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Copy failed')
    })
  })

  it('ignores superseded retry null results when a newer retry succeeds', async () => {
    mockCurrentDetail = null
    mockGetDetail.mockResolvedValueOnce(null)
    const { rerender } = render(<InspirationDetailPage />)

    const retryBtn = await screen.findByRole('button', { name: 'Retry' })

    let resolveSlowRetry: ((val: Record<string, unknown> | null) => void) | undefined
    const recoveredDetail = { id: 'insp-2', title: 'Recovered Detail' }
    mockGetDetail
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSlowRetry = resolve
          })
      )
      .mockResolvedValueOnce(recoveredDetail)

    fireEvent.click(retryBtn)
    // Supersede on the same mounted page instance with a route param change before first retry settles
    mockInspirationId = 'insp-2'
    mockCurrentDetail = recoveredDetail
    rerender(<InspirationDetailPage />)

    await waitFor(() => {
      expect(screen.getByText('Recovered Detail')).toBeInTheDocument()
    })

    resolveSlowRetry!(null)
    await waitFor(() => {
      expect(mockGetDetail).toHaveBeenCalledTimes(3)
    })
    expect(screen.queryByText('Failed to load inspiration')).not.toBeInTheDocument()
    expect(screen.getByText('Recovered Detail')).toBeInTheDocument()
  })
})
