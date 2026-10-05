/**
 * Tests for CRUD API operations
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const analyticsMocks = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
  captureExceptionMock: vi.fn(),
}))

// Mock apiClient - must use factory function to avoid hoisting issues
vi.mock('../apiClient', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  getAccessToken: vi.fn(() => 'test-token'),
  getApiBase: vi.fn(() => ''),
  tryRefreshToken: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, message: string) {
      super(message)
      this.status = status
      this.name = 'ApiError'
    }
  },
  apiErrorFromPayload: vi.fn((status: number, payload: unknown) => {
    const error = new Error(JSON.stringify(payload)) as Error & { status: number }
    error.status = status
    return error
  }),
}))

vi.mock('../analytics', () => ({
  trackEvent: analyticsMocks.trackEventMock,
  captureException: analyticsMocks.captureExceptionMock,
}))

// Import after mocking
import {
  authApi,
  projectApi,
  fileApi,
  versionApi,
  fileVersionApi,
  exportApi,
  skillsApi,
  publicSkillsApi,
} from '../api'
import { ApiError, api, apiErrorFromPayload } from '../apiClient'

// Get the mocked api
const mockApi = api as { [key: string]: ReturnType<typeof vi.fn> }

describe('api', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('authApi', () => {
    describe('register', () => {
      it('registers new user successfully', async () => {
        const mockResponse = {
          access_token: 'token',
          refresh_token: 'refresh',
          user: { id: '1', username: 'test' },
        }

        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          json: async () => mockResponse,
        })

        const result = await authApi.register({
          username: 'testuser',
          email: 'test@example.com',
          password: 'password123',
        })

        expect(result).toEqual(mockResponse)
      })

      it('throws ApiError on registration failure', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: false,
          status: 400,
          json: async () => ({ detail: 'Email already exists' }),
        })

        await expect(
          authApi.register({
            username: 'testuser',
            email: 'test@example.com',
            password: 'password123',
          })
        ).rejects.toThrow('Email already exists')
      })
    })

    describe('login', () => {
      it('logs in user successfully', async () => {
        const mockResponse = {
          access_token: 'token',
          refresh_token: 'refresh',
          user: { id: '1', username: 'test' },
        }

        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          json: async () => mockResponse,
        })

        const result = await authApi.login('testuser', 'password123')

        expect(result).toEqual(mockResponse)
      })

      it('sends FormData with username and password', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          json: async () => ({ access_token: 'token' }),
        })

        await authApi.login('testuser', 'password123')

        const callArgs = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
        expect(callArgs[1].body).toBeInstanceOf(FormData)
      })

      it('throws ApiError on invalid credentials', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: false,
          status: 401,
          json: async () => ({ detail: 'Invalid credentials' }),
        })

        await expect(
          authApi.login('testuser', 'wrongpassword')
        ).rejects.toThrow('Invalid credentials')
      })
    })

    describe('refreshToken', () => {
      it('refreshes token successfully', async () => {
        const mockResponse = {
          access_token: 'new-token',
          refresh_token: 'new-refresh',
          user: { id: '1' },
        }

        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          json: async () => mockResponse,
        })

        const result = await authApi.refreshToken('old-refresh-token')

        expect(result).toEqual(mockResponse)
      })

      it('throws ApiError when refresh token is invalid', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: false,
          status: 401,
          json: async () => ({ detail: 'Invalid refresh token' }),
        })

        await expect(
          authApi.refreshToken('invalid-token')
        ).rejects.toThrow('Invalid refresh token')
      })
    })

    describe('verifyEmail', () => {
      it('verifies email successfully', async () => {
        const mockResponse = { message: 'Email verified' }

        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          json: async () => mockResponse,
        })

        const result = await authApi.verifyEmail('test@example.com', '123456')

        expect(result).toEqual(mockResponse)
      })

      it('throws ApiError on invalid code', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: false,
          status: 400,
          json: async () => ({ detail: 'Invalid verification code' }),
        })

        await expect(
          authApi.verifyEmail('test@example.com', 'wrong')
        ).rejects.toThrow('Invalid verification code')
      })
    })

    describe('googleLogin', () => {
      it('redirects to Google OAuth URL', () => {
        const originalLocation = window.location
        // @ts-expect-error - mock
        delete window.location
        window.location = { href: '' } as Location

        authApi.googleLogin()

        expect(window.location.href).toContain('/api/auth/google')

        window.location = originalLocation
      })
    })
  })

  describe('projectApi', () => {
    describe('getAll', () => {
      it('fetches all projects', async () => {
        const mockProjects = [
          { id: '1', name: 'Project 1' },
          { id: '2', name: 'Project 2' },
        ]
        mockApi.get.mockResolvedValue(mockProjects)

        const result = await projectApi.getAll()

        expect(result).toEqual(mockProjects)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/projects')
      })
    })

    describe('get', () => {
      it('fetches single project by ID', async () => {
        const mockProject = { id: '1', name: 'Project 1' }
        mockApi.get.mockResolvedValue(mockProject)

        const result = await projectApi.get('1')

        expect(result).toEqual(mockProject)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/projects/1')
      })
    })

    describe('create', () => {
      it('creates new project', async () => {
        const newProject = { name: 'New Project', description: 'Test' }
        const mockResponse = { id: '1', ...newProject }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await projectApi.create(newProject)

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith('/api/v1/projects', newProject)
      })
    })

    describe('update', () => {
      it('updates existing project', async () => {
        const updates = { name: 'Updated Name' }
        const mockResponse = { id: '1', name: 'Updated Name' }
        mockApi.put.mockResolvedValue(mockResponse)

        const result = await projectApi.update('1', updates)

        expect(result).toEqual(mockResponse)
        expect(mockApi.put).toHaveBeenCalledWith('/api/v1/projects/1', updates)
      })
    })

    describe('delete', () => {
      it('deletes project', async () => {
        mockApi.delete.mockResolvedValue(undefined)

        await projectApi.delete('1')

        expect(mockApi.delete).toHaveBeenCalledWith('/api/v1/projects/1')
      })
    })

    describe('patch', () => {
      it('patches project status fields', async () => {
        const patchData = {
          summary: 'Updated summary',
          current_phase: 'Drafting',
        }
        const mockResponse = { id: '1', ...patchData }
        mockApi.patch.mockResolvedValue(mockResponse)

        const result = await projectApi.patch('1', patchData)

        expect(result).toEqual(mockResponse)
        expect(mockApi.patch).toHaveBeenCalledWith('/api/v1/projects/1', patchData)
      })
    })

    describe('getTemplates', () => {
      it('fetches project templates', async () => {
        const mockTemplates = {
          novel: { name: 'Novel', description: 'Novel template' },
          short: { name: 'Short Story', description: 'Short story template' },
        }
        mockApi.get.mockResolvedValue(mockTemplates)

        const result = await projectApi.getTemplates()

        expect(result).toEqual(mockTemplates)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/project-templates')
      })
    })
  })

  describe('fileApi', () => {
    describe('getAll', () => {
      it('fetches all files for a project', async () => {
        const mockFiles = [
          { id: '1', title: 'File 1' },
          { id: '2', title: 'File 2' },
        ]
        mockApi.get.mockResolvedValue(mockFiles)

        const result = await fileApi.getAll('project-1')

        expect(result).toEqual(mockFiles)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/projects/project-1/files')
      })

      it('filters by file type', async () => {
        mockApi.get.mockResolvedValue([])

        await fileApi.getAll('project-1', { fileType: 'draft' })

        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/projects/project-1/files?file_type=draft'
        )
      })

      it('filters by parent_id', async () => {
        mockApi.get.mockResolvedValue([])

        await fileApi.getAll('project-1', { parentId: 'parent-1' })

        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/projects/project-1/files?parent_id=parent-1'
        )
      })
    })

    describe('get', () => {
      it('fetches single file by ID', async () => {
        const mockFile = { id: '1', title: 'File 1', content: 'content' }
        mockApi.get.mockResolvedValue(mockFile)

        const result = await fileApi.get('1')

        expect(result).toEqual(mockFile)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/files/1')
      })
    })

    describe('create', () => {
      it('creates new file', async () => {
        const newFile = {
          title: 'New File',
          file_type: 'draft',
          content: 'content',
        }
        const mockResponse = { id: '1', ...newFile }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await fileApi.create('project-1', newFile)

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/projects/project-1/files',
          newFile
        )
        expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
          'file_created',
          expect.objectContaining({
            project_id: 'project-1',
            file_id: '1',
            file_type: 'draft',
          })
        )
      })
    })

    describe('update', () => {
      it('updates existing file', async () => {
        const updates = { title: 'Updated Title', content: 'new content' }
        const mockResponse = { id: '1', ...updates }
        mockApi.put.mockResolvedValue(mockResponse)

        const result = await fileApi.update('1', updates)

        expect(result).toEqual(mockResponse)
        expect(mockApi.put).toHaveBeenCalledWith('/api/v1/files/1', updates)
        expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
          'file_saved',
          expect.objectContaining({
            file_id: '1',
            project_id: mockResponse.project_id,
            change_type: 'edit',
          })
        )
      })

      it('sends file_saved at most once per file every 5 minutes', async () => {
        vi.useFakeTimers()
        try {
          vi.setSystemTime(new Date('2026-10-05T00:00:00Z'))
          mockApi.put.mockResolvedValue({ id: 'throttled-file', project_id: 'p1' })
          analyticsMocks.trackEventMock.mockClear()
          const fileSavedCalls = () =>
            analyticsMocks.trackEventMock.mock.calls.filter(([name, props]) =>
              name === 'file_saved' && (props as { file_id?: string }).file_id === 'throttled-file')

          await fileApi.update('throttled-file', { content: 'a' })
          await fileApi.update('throttled-file', { content: 'ab' })
          vi.setSystemTime(new Date('2026-10-05T00:04:59Z'))
          await fileApi.update('throttled-file', { content: 'abc' })
          expect(fileSavedCalls()).toHaveLength(1)

          // Another file is tracked independently.
          await fileApi.update('other-file', { content: 'x' })
          expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
            'file_saved',
            expect.objectContaining({ file_id: 'other-file' }),
          )

          vi.setSystemTime(new Date('2026-10-05T00:05:00Z'))
          await fileApi.update('throttled-file', { content: 'abcd' })
          expect(fileSavedCalls()).toHaveLength(2)
        } finally {
          vi.useRealTimers()
        }
      })
    })

    describe('delete', () => {
      it('deletes file', async () => {
        mockApi.delete.mockResolvedValue(undefined)

        await fileApi.delete('1')

        expect(mockApi.delete).toHaveBeenCalledWith('/api/v1/files/1')
      })

      it('deletes file recursively', async () => {
        mockApi.delete.mockResolvedValue(undefined)

        await fileApi.delete('1', true)

        expect(mockApi.delete).toHaveBeenCalledWith('/api/v1/files/1?recursive=true')
      })
    })

    describe('getTree', () => {
      it('fetches file tree', async () => {
        const mockTree = {
          tree: [
            { id: '1', title: 'Root', children: [] },
          ],
        }
        mockApi.get.mockResolvedValue(mockTree)

        const result = await fileApi.getTree('project-1')

        expect(result).toEqual(mockTree)
        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/projects/project-1/file-tree'
        )
      })
    })

    describe('upload', () => {
      it('uploads txt file successfully', async () => {
        const mockResponse = {
          id: '1',
          title: 'uploaded.txt',
          file_type: 'material',
        }

        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          json: async () => mockResponse,
        })

        const file = new File(['test content'], 'test.txt', { type: 'text/plain' })
        const result = await fileApi.upload('project-1', file)

        expect(result).toEqual(mockResponse)
        expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
          'material_uploaded',
          expect.objectContaining({
            project_id: 'project-1',
            file_id: '1',
            file_size_bytes: file.size,
          })
        )
      })

      it('rejects non-txt file before request is sent', async () => {
        global.fetch = vi.fn()
        const file = new File(['test content'], 'test.md', { type: 'text/markdown' })

        await expect(fileApi.upload('project-1', file)).rejects.toThrow('ERR_FILE_TYPE_INVALID')
        expect(global.fetch).not.toHaveBeenCalled()
        expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
          'material_upload_failed',
          expect.objectContaining({
            project_id: 'project-1',
            reason: 'invalid_file_type',
          })
        )
      })

      it('rejects oversized txt file before request is sent', async () => {
        global.fetch = vi.fn()
        const oversized = new File([new Uint8Array(2_000_001)], 'big.txt', { type: 'text/plain' })

        await expect(fileApi.upload('project-1', oversized)).rejects.toThrow('ERR_FILE_TOO_LARGE')
        expect(global.fetch).not.toHaveBeenCalled()
        expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
          'material_upload_failed',
          expect.objectContaining({
            project_id: 'project-1',
            reason: 'file_too_large',
          })
        )
      })

      it('throws ApiError on upload failure', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: false,
          status: 400,
          json: async () => ({ detail: 'Invalid file type' }),
        })

        const file = new File(['test'], 'test.txt', { type: 'text/plain' })

        await expect(fileApi.upload('project-1', file)).rejects.toThrow(
          'Invalid file type'
        )
        expect(analyticsMocks.trackEventMock).toHaveBeenCalledWith(
          'material_upload_failed',
          expect.objectContaining({
            project_id: 'project-1',
            reason: 'request_failed',
            status: 400,
          })
        )
        expect(analyticsMocks.captureExceptionMock).toHaveBeenCalledTimes(1)
      })
    })
  })

  describe('versionApi', () => {
    describe('getSnapshots', () => {
      it('fetches snapshots for project', async () => {
        const mockSnapshots = [{ id: '1', project_id: 'p1' }]
        mockApi.get.mockResolvedValue(mockSnapshots)

        const result = await versionApi.getSnapshots('p1')

        expect(result).toEqual(mockSnapshots)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/projects/p1/snapshots')
      })

      it('filters by file_id', async () => {
        mockApi.get.mockResolvedValue([])

        await versionApi.getSnapshots('p1', { fileId: 'f1' })

        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/projects/p1/snapshots?file_id=f1'
        )
      })

      it('filters by limit', async () => {
        mockApi.get.mockResolvedValue([])

        await versionApi.getSnapshots('p1', { limit: 10 })

        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/projects/p1/snapshots?limit=10'
        )
      })
    })

    describe('createSnapshot', () => {
      it('creates snapshot', async () => {
        const mockResponse = { id: '1', project_id: 'p1' }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await versionApi.createSnapshot('p1', {
          description: 'Test snapshot',
        })

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/projects/p1/snapshots',
          expect.objectContaining({ description: 'Test snapshot' })
        )
      })
    })

    describe('compare', () => {
      it('compares two snapshots', async () => {
        const mockComparison = {
          snapshot1: { id: 's1' },
          snapshot2: { id: 's2' },
          changes: { added: [], removed: [], modified: [] },
        }
        mockApi.get.mockResolvedValue(mockComparison)

        const result = await versionApi.compare('s1', 's2')

        expect(result).toEqual(mockComparison)
        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/snapshots/s1/compare/s2'
        )
      })
    })

    describe('rollback', () => {
      it('rolls back to snapshot', async () => {
        const mockResponse = { message: 'Rollback successful' }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await versionApi.rollback('s1')

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/snapshots/s1/rollback'
        )
      })
    })
  })

  describe('fileVersionApi', () => {
    describe('getVersions', () => {
      it('fetches version history for file', async () => {
        const mockResponse = {
          versions: [{ id: 'v1', version_number: 1 }],
          total: 1,
          file_id: 'f1',
          file_title: 'Test File',
        }
        mockApi.get.mockResolvedValue(mockResponse)

        const result = await fileVersionApi.getVersions('f1')

        expect(result).toEqual(mockResponse)
        expect(mockApi.get).toHaveBeenCalledWith('/api/v1/files/f1/versions')
      })

      it('includes query parameters', async () => {
        mockApi.get.mockResolvedValue({ versions: [], total: 0 })

        await fileVersionApi.getVersions('f1', {
          limit: 10,
          offset: 5,
          includeAutoSave: true,
        })

        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/files/f1/versions?limit=10&offset=5&include_auto_save=true'
        )
      })
    })

    describe('createVersion', () => {
      it('creates new version', async () => {
        const mockResponse = { id: 'v1', version_number: 2 }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await fileVersionApi.createVersion('f1', 'new content', {
          changeType: 'edit',
          changeSource: 'user',
          changeSummary: 'Updated text',
        })

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/files/f1/versions',
          expect.objectContaining({
            content: 'new content',
            change_type: 'edit',
            change_source: 'user',
            change_summary: 'Updated text',
          })
        )
      })
    })

    describe('compare', () => {
      it('compares two versions', async () => {
        const mockComparison = {
          file_id: 'f1',
          version1: { number: 1 },
          version2: { number: 2 },
          unified_diff: '---\n+++',
          html_diff: [],
          stats: { lines_added: 1, lines_removed: 0, word_diff: 5 },
        }
        mockApi.get.mockResolvedValue(mockComparison)

        const result = await fileVersionApi.compare('f1', 1, 2)

        expect(result).toEqual(mockComparison)
        expect(mockApi.get).toHaveBeenCalledWith(
          '/api/v1/files/f1/versions/compare?v1=1&v2=2'
        )
      })
    })

    describe('rollback', () => {
      it('rolls back to specific version', async () => {
        const mockResponse = {
          message: 'Rollback successful',
          new_version_number: 3,
        }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await fileVersionApi.rollback('f1', 1)

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/files/f1/versions/1/rollback'
        )
      })
    })
  })

  describe('exportApi', () => {
    describe('exportDrafts', () => {
      it('exports drafts successfully', async () => {
        const mockBlob = new Blob(['draft content'], { type: 'text/plain' })
        global.fetch = vi.fn().mockResolvedValue({
          ok: true,
          blob: async () => mockBlob,
          headers: {
            get: (name: string) => {
              if (name === 'Content-Disposition') {
                return 'attachment; filename="drafts.txt"'
              }
              return null
            },
          },
        })

        // Mock DOM methods and URL API
        const mockLink = {
          href: '',
          download: '',
          click: vi.fn(),
        }
        vi.spyOn(document, 'createElement').mockReturnValue(mockLink as HTMLElement)
        vi.spyOn(document.body, 'appendChild').mockImplementation(() => mockLink as HTMLElement)
        vi.spyOn(document.body, 'removeChild').mockImplementation(() => mockLink as HTMLElement)

        // Mock URL API
        const mockUrl = 'blob:mock-url'
        global.URL.createObjectURL = vi.fn(() => mockUrl)
        global.URL.revokeObjectURL = vi.fn()

        await exportApi.exportDrafts('project-1')

        expect(mockLink.click).toHaveBeenCalled()
        expect(mockLink.download).toBe('drafts.txt')
      })

      it('throws ApiError when no drafts', async () => {
        global.fetch = vi.fn().mockResolvedValue({
          ok: false,
          status: 404,
          json: async () => ({ detail: 'ERR_EXPORT_NO_DRAFTS' }),
        })

        await expect(exportApi.exportDrafts('project-1')).rejects.toThrow(ApiError)
      })
    })
  })

  describe('skillsApi', () => {
    it('lists skills', async () => {
      const mockResponse = { skills: [], total: 0 }
      mockApi.get.mockResolvedValue(mockResponse)

      const result = await skillsApi.list()

      expect(result).toEqual(mockResponse)
      expect(mockApi.get).toHaveBeenCalledWith('/api/v1/skills')
    })

    it('creates skill', async () => {
      const newSkill = { name: 'Test Skill', prompt: 'Test prompt' }
      const mockResponse = { id: '1', ...newSkill }
      mockApi.post.mockResolvedValue(mockResponse)

      const result = await skillsApi.create(newSkill as { name: string; prompt: string })

      expect(result).toEqual(mockResponse)
    })

    it('updates skill', async () => {
      const updates = { name: 'Updated' }
      const mockResponse = { id: '1', name: 'Updated' }
      mockApi.put.mockResolvedValue(mockResponse)

      const result = await skillsApi.update('1', updates as { name: string })

      expect(result).toEqual(mockResponse)
    })

    it('deletes skill', async () => {
      const mockResponse = { success: true, message: 'Deleted' }
      mockApi.delete.mockResolvedValue(mockResponse)

      const result = await skillsApi.delete('1')

      expect(result).toEqual(mockResponse)
    })

    it('gets skill stats', async () => {
      const mockStats = { total_calls: 10, unique_skills_used: 5 }
      mockApi.get.mockResolvedValue(mockStats)

      const result = await skillsApi.getStats('project-1', 30)

      expect(result).toEqual(mockStats)
      expect(mockApi.get).toHaveBeenCalledWith(
        '/api/v1/skills/stats/project-1?days=30'
      )
    })

    it('batch updates skills', async () => {
      const mockResponse = { success: true, updated_count: 3 }
      mockApi.post.mockResolvedValue(mockResponse)

      const result = await skillsApi.batchUpdate(['1', '2', '3'], 'enable')

      expect(result).toEqual(mockResponse)
      expect(mockApi.post).toHaveBeenCalledWith('/api/v1/skills/batch-update', {
        skill_ids: ['1', '2', '3'],
        action: 'enable',
      })
    })

    it('imports a skill package as multipart form data with auth', async () => {
      const mockResponse = { skill: { id: 's1', name: 'Imported' }, warnings: ['scripts/run.py dropped'] }
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockResponse),
      })
      const file = new File(['x'], 'skill.zip', { type: 'application/zip' })

      const result = await skillsApi.importSkill(file)

      expect(result).toEqual(mockResponse)
      const [url, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
      expect(url).toBe('/api/v1/skills/import')
      expect(init.method).toBe('POST')
      expect(init.headers.Authorization).toBe('Bearer test-token')
      expect((init.body as FormData).get('file')).toBe(file)
    })

    it('builds the import error from the full payload so the server reason is kept', async () => {
      const payload = {
        detail: 'ERR_SKILL_PACKAGE_INVALID',
        error_code: 'ERR_SKILL_PACKAGE_INVALID',
        error_detail: '技能包中包含重复的资源路径：references/a.md',
      }
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: () => Promise.resolve(payload),
      })

      await expect(
        skillsApi.importSkill(new File(['x'], 'skill.zip', { type: 'application/zip' }))
      ).rejects.toMatchObject({ status: 400 })
      expect(apiErrorFromPayload).toHaveBeenCalledWith(400, payload)
    })

    it('exports a skill zip using the Content-Disposition filename', async () => {
      const createObjectURL = vi.fn(() => 'blob:skill')
      const revokeObjectURL = vi.fn()
      Object.assign(URL, { createObjectURL, revokeObjectURL })
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: new Headers({
          'Content-Disposition': "attachment; filename=\"skill.zip\"; filename*=UTF-8''%E8%8A%82%E5%A5%8F.zip",
        }),
        blob: () => Promise.resolve(new Blob(['zip'])),
      })

      await skillsApi.exportSkill('s1', 'fallback')

      expect((global.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe('/api/v1/skills/s1/export')
      const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
      expect(anchor.download).toBe('节奏.zip')
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:skill')
    })

    it('falls back to <name>.zip when Content-Disposition is missing', async () => {
      Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: new Headers(),
        blob: () => Promise.resolve(new Blob(['zip'])),
      })

      await skillsApi.exportSkill('s1', '我的技能')

      const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
      expect(anchor.download).toBe('我的技能.zip')
    })

    it('manages skill resources with encoded paths', async () => {
      mockApi.get.mockResolvedValue({ resources: [] })
      mockApi.put.mockResolvedValue({})
      mockApi.delete.mockResolvedValue({})

      await skillsApi.listResources('s1')
      await skillsApi.getResourceContent('s1', 'references/a b.md')
      await skillsApi.upsertResource('s1', 'references/a.md', 'hello')
      await skillsApi.deleteResource('s1', 'references/a b.md')

      expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/v1/skills/s1/resources')
      expect(mockApi.get).toHaveBeenNthCalledWith(
        2,
        '/api/v1/skills/s1/resources/content?path=references%2Fa%20b.md'
      )
      expect(mockApi.put).toHaveBeenCalledWith('/api/v1/skills/s1/resources', {
        path: 'references/a.md',
        content: 'hello',
      })
      expect(mockApi.delete).toHaveBeenCalledWith(
        '/api/v1/skills/s1/resources?path=references%2Fa%20b.md'
      )
    })
  })

  describe('publicSkillsApi', () => {
    it('lists public skills with filters', async () => {
      const mockResponse = { skills: [], total: 0 }
      mockApi.get.mockResolvedValue(mockResponse)

      const result = await publicSkillsApi.list({
        category: 'writing',
        search: 'test',
        page: 1,
        page_size: 10,
      })

      expect(result).toEqual(mockResponse)
      expect(mockApi.get).toHaveBeenCalledWith(
        '/api/v1/public-skills?category=writing&search=test&page=1&page_size=10'
      )
    })

    it('gets single public skill', async () => {
      const mockSkill = { id: '1', name: 'Public Skill' }
      mockApi.get.mockResolvedValue(mockSkill)

      const result = await publicSkillsApi.get('1')

      expect(result).toEqual(mockSkill)
    })

    it('gets categories', async () => {
      const mockCategories = { categories: [{ id: '1', name: 'Writing' }] }
      mockApi.get.mockResolvedValue(mockCategories)

      const result = await publicSkillsApi.getCategories()

      expect(result).toEqual(mockCategories)
    })

    it('adds public skill to user', async () => {
      const mockResponse = { success: true, added_skill_id: '123' }
      mockApi.post.mockResolvedValue(mockResponse)

      const result = await publicSkillsApi.add('1')

      expect(result).toEqual(mockResponse)
      expect(mockApi.post).toHaveBeenCalledWith('/api/v1/public-skills/1/add')
    })

    it('removes public skill from user', async () => {
      const mockResponse = { success: true }
      mockApi.delete.mockResolvedValue(mockResponse)

      const result = await publicSkillsApi.remove('1')

      expect(result).toEqual(mockResponse)
    })
  })
})
