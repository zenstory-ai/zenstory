import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

const mockResolveApiErrorMessage = vi.fn()

vi.mock('../errorHandler', () => ({
  resolveApiErrorMessage: (...args: unknown[]) => mockResolveApiErrorMessage(...args),
  toUserErrorMessage: (message: string) => message,
}))

import { feedbackApi } from '../feedbackApi'

describe('feedbackApi.submit', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.clear()
    localStorage.setItem('access_token', 'test-access-token')
    localStorage.setItem('refresh_token', 'test-refresh-token')
    mockResolveApiErrorMessage.mockImplementation(
      (_errorData: unknown, fallbackMessage: string) => fallbackMessage
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('submits multipart payload with auth and language headers', async () => {
    localStorage.setItem('zenstory-language', 'en')
    const screenshot = new File(['image-bytes'], 'bug.png', { type: 'image/png' })
    const mockResponse = {
      id: 'feedback-1',
      message: 'Feedback submitted successfully.',
      created_at: '2026-03-07T12:00:00Z',
    }

    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue(mockResponse),
    } as unknown as Response)

    const result = await feedbackApi.submit({
      issueText: 'Editor toolbar overlaps',
      sourcePage: 'editor',
      sourceRoute: '/project/abc',
      screenshot,
    })

    expect(result).toEqual(mockResponse)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('http://localhost:8000/api/v1/feedback')
    expect(options.method).toBe('POST')
    expect(options.headers).toEqual({
      Authorization: 'Bearer test-access-token',
      'Accept-Language': 'en',
    })

    const formData = options.body as FormData
    expect(formData.get('issue_text')).toBe('Editor toolbar overlaps')
    expect(formData.get('source_page')).toBe('editor')
    expect(formData.get('source_route')).toBe('/project/abc')
    expect(formData.get('screenshot')).toBe(screenshot)
  })

  it('retries once after 401 when token refresh succeeds', async () => {
    const mockResponse = {
      id: 'feedback-2',
      message: 'Feedback submitted successfully.',
      created_at: '2026-03-07T12:00:00Z',
    }

    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
      } as unknown as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue({
          access_token: 'rotated-access-token',
          refresh_token: 'rotated-refresh-token',
          user: { id: 'user-1' },
        }),
      } as unknown as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue(mockResponse),
      } as unknown as Response)

    const result = await feedbackApi.submit({
      issueText: 'Need retry flow',
      sourcePage: 'dashboard',
    })

    expect(result).toEqual(mockResponse)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[1]?.[0]).toBe('http://localhost:8000/api/auth/refresh')
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      headers: expect.objectContaining({ Authorization: 'Bearer rotated-access-token' }),
    })
  })

  it('throws ApiError with resolved backend message', async () => {
    mockResolveApiErrorMessage.mockReturnValue('ERR_FILE_TOO_LARGE')

    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: vi.fn().mockResolvedValue({ error_code: 'ERR_FILE_TOO_LARGE' }),
    } as unknown as Response)

    await expect(
      feedbackApi.submit({
        issueText: 'Oversized file upload',
        sourcePage: 'dashboard',
      })
    ).rejects.toMatchObject({
      name: 'ApiError',
      status: 400,
      message: 'ERR_FILE_TOO_LARGE',
    })

    expect(mockResolveApiErrorMessage).toHaveBeenCalledWith(
      { error_code: 'ERR_FILE_TOO_LARGE' },
      'API error: 400'
    )
  })

  it('uses status-based ApiError instead of ERR_INTERNAL_SERVER_ERROR on non-JSON 413/429 responses', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 413,
      json: vi.fn().mockRejectedValue(new SyntaxError('Unexpected token <')),
    } as unknown as Response)

    await expect(
      feedbackApi.submit({
        issueText: 'Oversized proxy upload',
        sourcePage: 'dashboard',
      })
    ).rejects.toMatchObject({
      name: 'ApiError',
      status: 413,
      message: 'API error: 413',
    })
  })

  it('clears expired auth storage when replayed request after refresh still returns 401', async () => {
    const logoutReasons: string[] = []
    const onLogout = (event: Event) => {
      logoutReasons.push((event as CustomEvent<{ reason: string }>).detail.reason)
    }
    window.addEventListener('auth:logout', onLogout)

    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: vi.fn().mockResolvedValue({ detail: 'Unauthorized' }),
      } as unknown as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue({
          access_token: 'rotated-access-token',
          refresh_token: 'rotated-refresh-token',
          user: { id: 'user-1' },
        }),
      } as unknown as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: vi.fn().mockResolvedValue({ detail: 'Unauthorized' }),
      } as unknown as Response)

    try {
      await expect(
        feedbackApi.submit({
          issueText: 'Session expired',
          sourcePage: 'dashboard',
        })
      ).rejects.toMatchObject({
        name: 'ApiError',
        status: 401,
      })

      expect(logoutReasons).toEqual(['retry_still_unauthorized'])
      expect(localStorage.getItem('access_token')).toBeNull()
    } finally {
      window.removeEventListener('auth:logout', onLogout)
    }
  })
})
