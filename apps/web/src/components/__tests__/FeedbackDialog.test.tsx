import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockSubmit = vi.fn()
const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
const mockHandleApiError = vi.fn((error: unknown) =>
  error instanceof Error ? error.message : 'Unknown error'
)

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (
      key: string,
      fallbackOrOptions?: string | Record<string, unknown>,
      maybeOptions?: Record<string, unknown>
    ) => {
      const fallback = typeof fallbackOrOptions === 'string' ? fallbackOrOptions : key
      const options = (
        typeof fallbackOrOptions === 'object' && fallbackOrOptions !== null
          ? fallbackOrOptions
          : maybeOptions
      ) ?? {}

      return Object.keys(options).reduce((result, optionKey) => {
        return result.replace(new RegExp(`{{\\s*${optionKey}\\s*}}`, 'g'), String(options[optionKey]))
      }, fallback)
    },
  }),
}))

vi.mock('../../lib/feedbackApi', () => ({
  feedbackApi: {
    submit: (...args: unknown[]) => mockSubmit(...args),
  },
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
    info: vi.fn(),
  },
}))

vi.mock('../../lib/errorHandler', () => ({
  handleApiError: (...args: unknown[]) => mockHandleApiError(...args),
}))

import { FeedbackDialog } from '../feedback/FeedbackDialog'

describe('FeedbackDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  it('submits trimmed issue text and closes on success', async () => {
    const onClose = vi.fn()
    mockSubmit.mockResolvedValue({
      id: 'feedback-1',
      message: 'ok',
      created_at: '2026-03-07T12:00:00Z',
    })

    render(
      <FeedbackDialog open onClose={onClose} sourcePage="editor" sourceRoute="/project/test-id" />
    )

    const submitBtn = screen.getByRole('button', { name: '提交反馈' })
    expect(submitBtn).toBeDisabled()
    expect(submitBtn).toHaveAttribute('title', '请先填写问题描述')

    fireEvent.change(screen.getByLabelText(/问题描述/), {
      target: { value: '   Header button style looks broken.   ' },
    })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockSubmit).toHaveBeenCalledWith({
        issueText: 'Header button style looks broken.',
        sourcePage: 'editor',
        sourceRoute: '/project/test-id',
        screenshot: null,
      })
    })

    expect(mockToastSuccess).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows validation error for unsupported screenshot type', () => {
    render(<FeedbackDialog open onClose={vi.fn()} sourcePage="dashboard" sourceRoute="/dashboard" />)

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const invalidFile = new File(['bad'], 'bad.gif', { type: 'image/gif' })
    fireEvent.change(fileInput, { target: { files: [invalidFile] } })

    expect(screen.getByText('请上传 PNG/JPG/WEBP 图片')).toBeInTheDocument()
    expect(mockSubmit).not.toHaveBeenCalled()
  })

  it('shows toast error when submit fails', async () => {
    const onClose = vi.fn()
    mockSubmit.mockRejectedValue(new Error('submit failed'))
    mockHandleApiError.mockReturnValue('提交失败')

    render(<FeedbackDialog open onClose={onClose} sourcePage="dashboard" />)

    fireEvent.change(screen.getByLabelText(/问题描述/), {
      target: { value: 'Submission should show error toast' },
    })
    fireEvent.click(screen.getByRole('button', { name: '提交反馈' }))

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('提交失败')
    })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('attaches debug context when available', async () => {
    const onClose = vi.fn()
    mockSubmit.mockResolvedValue({
      id: 'feedback-2',
      message: 'ok',
      created_at: '2026-03-07T12:00:00Z',
    })

    sessionStorage.setItem(
      'zenstory_debug_context_v1',
      JSON.stringify({
        trace_id: 'trace-123',
        request_id: 'req-abc',
        agent_run_id: 'run-xyz',
        project_id: 'project-1',
        agent_session_id: 'session-1',
      }),
    )

    render(<FeedbackDialog open onClose={onClose} sourcePage="editor" />)

    fireEvent.change(screen.getByLabelText(/问题描述/), {
      target: { value: 'Need debug ids attached' },
    })
    fireEvent.click(screen.getByRole('button', { name: '提交反馈' }))

    await waitFor(() => {
      expect(mockSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          issueText: 'Need debug ids attached',
          sourcePage: 'editor',
          screenshot: null,
          debugContext: {
            trace_id: 'trace-123',
            request_id: 'req-abc',
            agent_run_id: 'run-xyz',
            project_id: 'project-1',
            agent_session_id: 'session-1',
          },
        }),
      )
    })
  })
})
