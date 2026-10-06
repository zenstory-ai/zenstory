import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

const mockGetAccessToken = vi.fn()
const mockGetApiBase = vi.fn(() => 'http://localhost:8000')
const mockTryRefreshToken = vi.fn()
const mockResolveApiErrorMessage = vi.fn()

vi.mock('../apiClient', () => ({
  ApiError: class ApiError extends Error {
    status: number

    constructor(status: number, message: string) {
      super(message)
      this.status = status
      this.name = 'ApiError'
    }
  },
  getAccessToken: () => mockGetAccessToken(),
  getApiBase: () => mockGetApiBase(),
  resolveOwnedAuthSession: () => ({
    accessToken: mockGetAccessToken(), refreshToken: localStorage.getItem('refresh_token'),
  }),
  tryRefreshToken: () => mockTryRefreshToken(),
}))

vi.mock('../errorHandler', () => ({
  resolveApiErrorMessage: (...args: unknown[]) => mockResolveApiErrorMessage(...args),
}))

import { feedbackApi } from '../feedbackApi'

describe('feedbackApi.submit', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    mockGetAccessToken.mockReturnValue('test-access-token')
    localStorage.setItem('access_token', 'test-access-token')
    localStorage.setItem('refresh_token', 'test-refresh-token')
    mockGetApiBase.mockReturnValue('http://localhost:8000')
    mockTryRefreshToken.mockResolvedValue(false)
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
    mockTryRefreshToken.mockImplementation(async () => {
      mockGetAccessToken.mockReturnValue('rotated-access-token')
      localStorage.setItem('access_token', 'rotated-access-token')
      localStorage.setItem('refresh_token', 'rotated-refresh-token')
      return true
    })

    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
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
    expect(mockTryRefreshToken).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
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
      'ERR_INTERNAL_SERVER_ERROR'
    )
  })
})
