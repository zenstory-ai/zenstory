/**
 * Tests for fileApi.uploadDraft - Draft upload API client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock the api client from apiClient
const mockGetAccessToken = vi.fn<() => string | null>(() => 'test-token')
const mockTryRefreshToken = vi.fn(() => Promise.resolve(true))

vi.mock('../apiClient', async importOriginal => ({
  resolveOwnedAuthSession: (await importOriginal<typeof import('../apiClient')>()).resolveOwnedAuthSession,
  api: {},
  getApiBase: vi.fn(() => 'http://localhost:8000'),
  getAccessToken: () => mockGetAccessToken(),
  tryRefreshToken: () => mockTryRefreshToken(),
  ApiError: class ApiError extends Error {
    status: number
    constructor(
      status: number,
      message: string
    ) {
      super(message)
      this.status = status
      this.name = 'ApiError'
    }
  },
}))

// Mock fetch globally
global.fetch = vi.fn()

describe('fileApi.uploadDraft', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAccessToken.mockReturnValue('test-token')
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('refresh_token', 'test-refresh-token')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // Dynamically import to get fresh module after mock setup
  async function getUploadDraft() {
    const { fileApi } = await import('../api')
    return fileApi.uploadDraft
  }

  it('uploads a file and returns files, total, errors on success', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['chapter content'], 'novel.txt', { type: 'text/plain' })

    const mockResponse = {
      files: [
        {
          id: 'file-1',
          project_id: 'project-1',
          title: 'novel',
          content: 'chapter content',
          file_type: 'draft',
          parent_id: 'folder-1',
          order: 0,
          file_metadata: '{"source":"upload","word_count":2}',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        },
      ],
      total: 1,
      errors: [],
    }

    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => mockResponse,
    } as Response)

    const result = await uploadDraft('project-1', mockFile)

    expect(result.total).toBe(1)
    expect(result.files).toHaveLength(1)
    expect(result.files[0].title).toBe('novel')
    expect(result.files[0].file_type).toBe('draft')
    expect(result.errors).toHaveLength(0)
    expect(global.fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/projects/project-1/files/upload-drafts',
      expect.objectContaining({
        method: 'POST',
        headers: { Authorization: 'Bearer test-token' },
        body: expect.any(FormData),
      })
    )
  })

  it('sends FormData with files and optional parent_id', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ files: [], total: 0, errors: [] }),
    } as Response)

    await uploadDraft('project-1', mockFile, 'folder-123')

    const fetchCall = vi.mocked(global.fetch).mock.calls[0]
    const body = fetchCall![1]?.body as FormData

    expect(body.get('files')).toBe(mockFile)
    expect(body.get('parent_id')).toBe('folder-123')
  })

  it('does not send parent_id when omitted', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ files: [], total: 0, errors: [] }),
    } as Response)

    await uploadDraft('project-1', mockFile)

    const fetchCall = vi.mocked(global.fetch).mock.calls[0]
    const body = fetchCall![1]?.body as FormData

    expect(body.get('parent_id')).toBeNull()
  })

  it('handles 401 with token refresh and retries', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    const mockSuccessResponse = {
      files: [{ id: 'file-1', title: 'draft' }],
      total: 1,
      errors: [],
    }

    vi.mocked(global.fetch)
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Unauthorized' }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => mockSuccessResponse,
      } as Response)

    mockTryRefreshToken.mockResolvedValue(true)

    const result = await uploadDraft('project-1', mockFile)

    expect(result.total).toBe(1)
    expect(mockTryRefreshToken).toHaveBeenCalled()
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('throws ApiError on server error response', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    vi.mocked(global.fetch).mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'Invalid request' }),
    } as Response)

    await expect(uploadDraft('project-1', mockFile)).rejects.toThrow('Invalid request')
  })

  it('throws default error message when error response body is not parseable', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    vi.mocked(global.fetch).mockResolvedValue(new Response('Invalid JSON', { status: 500 }))

    await expect(uploadDraft('project-1', mockFile)).rejects.toThrow()
  })

  it('sends request without auth header when no access token', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    mockGetAccessToken.mockReturnValue(null)

    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ files: [], total: 0, errors: [] }),
    } as Response)

    await uploadDraft('project-1', mockFile)

    const fetchCall = vi.mocked(global.fetch).mock.calls[0]
    expect(fetchCall![1]?.headers).toBeUndefined()
  })

  it('returns errors list from partial success response', async () => {
    const uploadDraft = await getUploadDraft()
    const mockFile = new File(['content'], 'draft.txt', { type: 'text/plain' })

    const partialResponse = {
      files: [
        {
          id: 'file-1',
          project_id: 'project-1',
          title: 'valid_draft',
          content: 'content',
          file_type: 'draft',
          parent_id: 'folder-1',
          order: 0,
          file_metadata: '{}',
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        },
      ],
      total: 1,
      errors: ['invalid.pdf: ERR_FILE_TYPE_INVALID'],
    }

    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => partialResponse,
    } as Response)

    const result = await uploadDraft('project-1', mockFile)

    expect(result.total).toBe(1)
    expect(result.errors).toHaveLength(1)
    expect(result.errors[0]).toContain('ERR_FILE_TYPE_INVALID')
  })
})
