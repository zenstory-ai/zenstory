import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ProjectProvider, useProject } from '../ProjectContext'
import type { ReactNode } from 'react'
import type { Project, SelectedItem } from '../../types'

// Mock dependencies
vi.mock('../../lib/api', () => ({
  projectApi: {
    getAll: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
    getTemplates: vi.fn(),
  },
}))

vi.mock('../AuthContext', () => ({
  useAuth: vi.fn(),
}))

vi.mock('diff-match-patch', () => ({
  diff_match_patch: vi.fn().mockImplementation(function () {
    return {
      diff_main: vi.fn().mockReturnValue([
        [0, 'same'],
        [1, 'added'],
      ]),
      diff_cleanupSemantic: vi.fn(),
    }
  }),
  DIFF_DELETE: -1,
  DIFF_INSERT: 1,
  DIFF_EQUAL: 0,
}))

import { projectApi } from '../../lib/api'
import { useAuth } from '../AuthContext'

// Helper to create wrapper
function createWrapper(user = null) {
  vi.mocked(useAuth).mockReturnValue({
    user,
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refreshToken: vi.fn(),
    handleOAuthCallback: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    googleLogin: vi.fn(),
    appleLogin: vi.fn(),
  })

  return function Wrapper({ children }: { children: ReactNode }) {
    return <ProjectProvider>{children}</ProjectProvider>
  }
}

// Mock console methods to reduce noise
vi.spyOn(console, 'log').mockImplementation(() => {})
vi.spyOn(console, 'warn').mockImplementation(() => {})
vi.spyOn(console, 'error').mockImplementation(() => {})

describe('ProjectContext', () => {
  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    email_verified: true,
    is_active: true,
    is_superuser: false,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }

  const mockProjects: Project[] = [
    {
      id: 'project-1',
      name: 'Project 1',
      description: 'First project',
      project_type: 'novel',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-02T00:00:00Z',
    },
    {
      id: 'project-2',
      name: 'Project 2',
      description: 'Second project',
      project_type: 'short',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-03T00:00:00Z',
    },
  ]

  let localStorageSpy: {
    getItem: ReturnType<typeof vi.spyOn>
    setItem: ReturnType<typeof vi.spyOn>
    removeItem: ReturnType<typeof vi.spyOn>
    clear: ReturnType<typeof vi.spyOn>
  }

  beforeEach(() => {
    vi.clearAllMocks()
    // Clear localStorage
    localStorage.clear()
    // Set up spies on real localStorage
    localStorageSpy = {
      getItem: vi.spyOn(Storage.prototype, 'getItem'),
      setItem: vi.spyOn(Storage.prototype, 'setItem'),
      removeItem: vi.spyOn(Storage.prototype, 'removeItem'),
      clear: vi.spyOn(Storage.prototype, 'clear'),
    }
  })

  afterEach(() => {
    vi.clearAllTimers()
    localStorageSpy.getItem.mockRestore()
    localStorageSpy.setItem.mockRestore()
    localStorageSpy.removeItem.mockRestore()
    localStorageSpy.clear.mockRestore()
  })

  // ========================================
  // 1. Project Selection Tests
  // ========================================
  describe('Project Selection', () => {
    it('should initialize with null currentProject when no user', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue([])
      const wrapper = createWrapper(null)

      const { result } = renderHook(() => useProject(), { wrapper })

      expect(result.current.projects).toEqual([])
      expect(result.current.currentProject).toBe(null)
      expect(result.current.currentProjectId).toBe(null)
      expect(result.current.loading).toBe(false)
    })

    it('should load projects when user is authenticated', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      expect(result.current.projects).toEqual(mockProjects)
    })

    it('should select most recently updated project on load', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.currentProjectId).toBe('project-2')
      })

      // project-2 has more recent updated_at
      expect(result.current.currentProjectId).toBe('project-2')
    })

    it('should restore project from localStorage if available', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      // Set localStorage before creating the wrapper
      localStorage.setItem('zenstory_current_project_id:user-123', 'project-1')
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.currentProjectId).toBe('project-1')
      })
    })

    it('should clear project state when user logs out', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result, rerender } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      // Simulate logout
      vi.mocked(useAuth).mockReturnValue({
        user: null,
        loading: false,
        login: vi.fn(),
        register: vi.fn(),
        logout: vi.fn(),
        refreshToken: vi.fn(),
        handleOAuthCallback: vi.fn(),
        verifyEmail: vi.fn(),
        resendVerification: vi.fn(),
        googleLogin: vi.fn(),
        appleLogin: vi.fn(),
      })

      rerender()

      expect(result.current.projects).toEqual([])
      expect(result.current.currentProjectId).toBe(null)
    })

    it('should handle non-existent project in localStorage', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      localStorage.setItem('zenstory_current_project_id:user-123', 'non-existent-id')
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        // Should fall back to most recently updated
        expect(result.current.currentProjectId).toBe('project-2')
      })
    })

    it('should handle empty projects list', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue([])
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects).toEqual([])
      })

      expect(result.current.currentProjectId).toBe(null)
    })
  })

  // ========================================
  // 2. Project CRUD Operations
  // ========================================
  describe('Project CRUD Operations', () => {
    it('should switch project using switchProject', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.switchProject('project-1')
      })

      expect(result.current.currentProjectId).toBe('project-1')
      expect(result.current.selectedItem).toBe(null)
    })

    it('should not switch to non-existent project', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const initialProjectId = result.current.currentProjectId

      act(() => {
        result.current.switchProject('non-existent')
      })

      expect(result.current.currentProjectId).toBe(initialProjectId)
    })

    it('should create a new project and add to projects list', async () => {
      const newProject: Project = {
        id: 'project-new',
        name: 'New Project',
        description: 'A new project',
        project_type: 'novel',
        created_at: '2024-01-04T00:00:00Z',
        updated_at: '2024-01-04T00:00:00Z',
      }

      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.create).mockResolvedValue(newProject)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      let createdProject: Project | undefined

      await act(async () => {
        createdProject = await result.current.createProject('New Project', 'A new project', 'novel')
      })

      // Verify the API was called correctly
      expect(projectApi.create).toHaveBeenCalledWith({
        name: 'New Project',
        description: 'A new project',
        project_type: 'novel',
      })
      // Verify the returned project is the new one
      expect(createdProject).toEqual(newProject)
    })

    it('should throw error when create returns invalid data', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.create).mockResolvedValue({} as Project)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      await expect(
        result.current.createProject('Test', 'desc')
      ).rejects.toThrow('Invalid project data returned from server')
    })

    it('should update an existing project', async () => {
      const updatedProject: Project = {
        ...mockProjects[0],
        name: 'Updated Project 1',
        description: 'Updated description',
      }

      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.update).mockResolvedValue(updatedProject)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      let returned: Project | undefined

      await act(async () => {
        returned = await result.current.updateProject('project-1', {
          name: 'Updated Project 1',
          description: 'Updated description',
        })
      })

      expect(projectApi.update).toHaveBeenCalledWith('project-1', {
        name: 'Updated Project 1',
        description: 'Updated description',
      })
      expect(returned).toEqual(updatedProject)
      expect(result.current.projects.find(p => p.id === 'project-1')?.name).toBe('Updated Project 1')
    })

    it('should delete a project and call API correctly', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.delete).mockResolvedValue(undefined)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      // Select project-1 first
      act(() => {
        result.current.switchProject('project-1')
      })

      expect(result.current.currentProjectId).toBe('project-1')

      await act(async () => {
        await result.current.deleteProject('project-1')
      })

      // Verify the API was called correctly
      expect(projectApi.delete).toHaveBeenCalledWith('project-1')
    })

    it('should call delete API when deleting last project', async () => {
      const singleProject = [mockProjects[0]]
      vi.mocked(projectApi.getAll).mockResolvedValue(singleProject)
      vi.mocked(projectApi.delete).mockResolvedValue(undefined)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(1)
      })

      await act(async () => {
        await result.current.deleteProject('project-1')
      })

      // Verify the API was called correctly
      expect(projectApi.delete).toHaveBeenCalledWith('project-1')
    })

    it('should refresh projects', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const initialCallCount = vi.mocked(projectApi.getAll).mock.calls.length

      await act(async () => {
        await result.current.refreshProjects()
      })

      expect(vi.mocked(projectApi.getAll).mock.calls.length).toBe(initialCallCount + 1)
    })
  })

  // ========================================
  // 3. Selected Item State
  // ========================================
  describe('Selected Item State', () => {
    it('should set selected item', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const item: SelectedItem = {
        id: 'file-1',
        type: 'draft',
        title: 'Chapter 1',
      }

      act(() => {
        result.current.setSelectedItem(item)
      })

      expect(result.current.selectedItem).toEqual(item)
    })

    it('should clear selected item', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const item: SelectedItem = {
        id: 'file-1',
        type: 'draft',
        title: 'Chapter 1',
      }

      act(() => {
        result.current.setSelectedItem(item)
      })

      expect(result.current.selectedItem).toEqual(item)

      act(() => {
        result.current.setSelectedItem(null)
      })

      expect(result.current.selectedItem).toBe(null)
    })

    it('should clear selected item when switching project', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const item: SelectedItem = {
        id: 'file-1',
        type: 'draft',
        title: 'Chapter 1',
      }

      act(() => {
        result.current.setSelectedItem(item)
      })

      expect(result.current.selectedItem).toEqual(item)

      act(() => {
        result.current.switchProject('project-1')
      })

      expect(result.current.selectedItem).toBe(null)
    })
  })

  // ========================================
  // 4. File Tree State
  // ========================================
  describe('File Tree State', () => {
    it('should trigger file tree refresh', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const initialVersion = result.current.fileTreeVersion

      act(() => {
        result.current.triggerFileTreeRefresh()
      })

      expect(result.current.fileTreeVersion).toBe(initialVersion + 1)
    })

    it('should increment file tree version on multiple refreshes', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.triggerFileTreeRefresh()
        result.current.triggerFileTreeRefresh()
        result.current.triggerFileTreeRefresh()
      })

      expect(result.current.fileTreeVersion).toBe(3)
    })
  })

  // ========================================
  // 5. Editor Refresh State
  // ========================================
  describe('Editor Refresh State', () => {
    it('should trigger editor refresh', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const initialVersion = result.current.editorRefreshVersion
      expect(result.current.lastEditedFileId).toBe(null)

      act(() => {
        result.current.triggerEditorRefresh('file-123')
      })

      expect(result.current.editorRefreshVersion).toBe(initialVersion + 1)
      expect(result.current.lastEditedFileId).toBe('file-123')
    })

    it('should track multiple editor refreshes', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.triggerEditorRefresh('file-1')
      })

      expect(result.current.lastEditedFileId).toBe('file-1')
      expect(result.current.editorRefreshVersion).toBe(1)

      act(() => {
        result.current.triggerEditorRefresh('file-2')
      })

      expect(result.current.lastEditedFileId).toBe('file-2')
      expect(result.current.editorRefreshVersion).toBe(2)
    })
  })

  // ========================================
  // 6. File Streaming State
  // ========================================
  describe('File Streaming State', () => {
    it('should start file streaming', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      expect(result.current.streamingFileId).toBe(null)
      expect(result.current.streamingContent).toBe('')

      act(() => {
        result.current.startFileStreaming('file-1')
      })

      expect(result.current.streamingFileId).toBe('file-1')
      expect(result.current.streamingContent).toBe('')
    })

    it('should not reset streaming if already active for same file', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.startFileStreaming('file-1')
      })

      // Add some content
      act(() => {
        result.current.appendFileContent('file-1', 'Hello')
      })

      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Hello')
      })

      // Try to start again for same file
      act(() => {
        result.current.startFileStreaming('file-1')
      })

      // Content should still be there
      expect(result.current.streamingContent).toBe('Hello')
    })

    it('should append file content', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.startFileStreaming('file-1')
      })

      act(() => {
        result.current.appendFileContent('file-1', 'Hello ')
      })

      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Hello ')
      })

      act(() => {
        result.current.appendFileContent('file-1', 'World')
      })

      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Hello World')
      })
    })

    it('should auto-start streaming when content arrives first', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      // Send content without starting stream
      act(() => {
        result.current.appendFileContent('file-1', 'Auto content')
      })

      expect(result.current.streamingFileId).toBe('file-1')
      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Auto content')
      })
    })

    it('should ignore content for different file during streaming', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.startFileStreaming('file-1')
      })

      act(() => {
        result.current.appendFileContent('file-1', 'Correct')
        result.current.appendFileContent('file-2', 'Ignored')
      })

      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Correct')
      })
    })

    it('should finish file streaming', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.startFileStreaming('file-1')
        result.current.appendFileContent('file-1', 'Content')
      })

      expect(result.current.streamingFileId).toBe('file-1')
      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Content')
      })

      act(() => {
        result.current.finishFileStreaming('file-1')
      })

      expect(result.current.streamingFileId).toBe(null)
      expect(result.current.streamingContent).toBe('')
    })

    it('should ignore finish for different file', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.startFileStreaming('file-1')
        result.current.appendFileContent('file-1', 'Content')
      })

      act(() => {
        result.current.finishFileStreaming('file-2')
      })

      // Should still be streaming file-1
      expect(result.current.streamingFileId).toBe('file-1')
      await waitFor(() => {
        expect(result.current.streamingContent).toBe('Content')
      })
    })
  })

  // ========================================
  // 7. Diff Review State
  // ========================================
  describe('Diff Review State', () => {
    it('should enter diff review mode', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      expect(result.current.diffReviewState).toBe(null)

      act(() => {
        result.current.enterDiffReview('file-1', 'original content', 'modified content')
      })

      expect(result.current.diffReviewState).not.toBe(null)
      expect(result.current.diffReviewState?.isReviewing).toBe(true)
      expect(result.current.diffReviewState?.fileId).toBe('file-1')
      expect(result.current.diffReviewState?.originalContent).toBe('original content')
      expect(result.current.diffReviewState?.modifiedContent).toBe('modified content')
    })

    it('should accept a single edit', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'old', 'new')
      })

      const editId = result.current.diffReviewState?.pendingEdits[0]?.id

      act(() => {
        result.current.acceptEdit(editId!)
      })

      const edit = result.current.diffReviewState?.pendingEdits.find(e => e.id === editId)
      expect(edit?.status).toBe('accepted')
    })

    it('should reject a single edit', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'old', 'new')
      })

      const editId = result.current.diffReviewState?.pendingEdits[0]?.id

      act(() => {
        result.current.rejectEdit(editId!)
      })

      const edit = result.current.diffReviewState?.pendingEdits.find(e => e.id === editId)
      expect(edit?.status).toBe('rejected')
    })

    it('should reset a single edit back to pending', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'old', 'new')
      })

      const editId = result.current.diffReviewState?.pendingEdits[0]?.id

      act(() => {
        result.current.acceptEdit(editId!)
      })

      act(() => {
        result.current.resetEdit(editId!)
      })

      const edit = result.current.diffReviewState?.pendingEdits.find(e => e.id === editId)
      expect(edit?.status).toBe('pending')
    })

    it('should accept all edits', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'old', 'new')
      })

      act(() => {
        result.current.acceptAllEdits()
      })

      result.current.diffReviewState?.pendingEdits.forEach(edit => {
        expect(edit.status).toBe('accepted')
      })
    })

    it('should reject all edits', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'old', 'new')
      })

      act(() => {
        result.current.rejectAllEdits()
      })

      result.current.diffReviewState?.pendingEdits.forEach(edit => {
        expect(edit.status).toBe('rejected')
      })
    })

    it('should exit diff review mode', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'old', 'new')
      })

      expect(result.current.diffReviewState).not.toBe(null)

      act(() => {
        result.current.exitDiffReview()
      })

      expect(result.current.diffReviewState).toBe(null)
    })

    it('should apply diff review changes', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      act(() => {
        result.current.enterDiffReview('file-1', 'original content', 'modified content')
      })

      const finalContent = result.current.applyDiffReviewChanges()

      // Pending edits default to "accepted" when applying reviewed changes.
      // (diff-match-patch is mocked above: equal "same" + insert "added")
      expect(finalContent).toBe('sameadded')
    })

    it('should return empty string when no diff review state', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      const finalContent = result.current.applyDiffReviewChanges()

      expect(finalContent).toBe('')
    })
  })

  // ========================================
  // 8. Error Handling
  // ========================================
  describe('Error Handling', () => {
    it('should handle API error on load', async () => {
      const error = new Error('Network error')
      vi.mocked(projectApi.getAll).mockRejectedValue(error)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.error).toBe('Network error')
      })

      expect(result.current.loading).toBe(false)
    })

    it('should handle non-Error thrown value', async () => {
      vi.mocked(projectApi.getAll).mockRejectedValue('string error')
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.error).toBe('Failed to load projects')
      })
    })

    it('should handle project creation error', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.create).mockRejectedValue(new Error('Create failed'))
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      await expect(
        result.current.createProject('Test', 'desc')
      ).rejects.toThrow('Create failed')
    })

    it('should handle project update error', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.update).mockRejectedValue(new Error('Update failed'))
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      await expect(
        result.current.updateProject('project-1', { name: 'New' })
      ).rejects.toThrow('Update failed')
    })

    it('should handle project delete error', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      vi.mocked(projectApi.delete).mockRejectedValue(new Error('Delete failed'))
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      await expect(
        result.current.deleteProject('project-1')
      ).rejects.toThrow('Delete failed')
    })

    it('should filter out invalid projects from API response', async () => {
      const invalidProjects = [
        null,
        undefined,
        { id: '', name: 'Empty ID' },
        { id: 'valid-1', name: 'Valid Project' },
        { id: 'valid-2', name: 'Another Valid' },
        { id: 123, name: 'Number ID' }, // Invalid type
      ] as unknown as Project[]

      vi.mocked(projectApi.getAll).mockResolvedValue(invalidProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      expect(result.current.projects[0].id).toBe('valid-1')
      expect(result.current.projects[1].id).toBe('valid-2')
    })
  })

  // ========================================
  // 9. setCurrentProjectId Direct Usage
  // ========================================
  describe('setCurrentProjectId', () => {
    it('should set current project id and clear selected item', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      // Set a selected item first
      act(() => {
        result.current.setSelectedItem({ id: 'file-1', type: 'draft', title: 'Test' })
      })

      expect(result.current.selectedItem).not.toBe(null)

      act(() => {
        result.current.setCurrentProjectId('project-1')
      })

      expect(result.current.currentProjectId).toBe('project-1')
      expect(result.current.selectedItem).toBe(null)
    })

    it('should preserve the selected item when re-selecting the current project', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.currentProjectId).toBe('project-2')
      })

      const selectedItem: SelectedItem = { id: 'file-1', type: 'draft', title: 'Test' }
      act(() => {
        result.current.setSelectedItem(selectedItem)
        result.current.setCurrentProjectId('project-2')
      })

      expect(result.current.selectedItem).toEqual(selectedItem)
    })

    it('should save project id to localStorage', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      // Clear spy calls that happened during initialization
      localStorageSpy.setItem.mockClear()

      act(() => {
        result.current.setCurrentProjectId('project-1')
      })

      // Check that localStorage was updated
      expect(localStorage.getItem('zenstory_current_project_id:user-123')).toBe('project-1')
    })

    it('should remove from localStorage when setting to null', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.projects.length).toBe(2)
      })

      // First set a project
      act(() => {
        result.current.setCurrentProjectId('project-1')
      })

      expect(localStorage.getItem('zenstory_current_project_id:user-123')).toBe('project-1')

      // Then clear it
      act(() => {
        result.current.setCurrentProjectId(null)
      })

      // Check that localStorage was cleared
      expect(localStorage.getItem('zenstory_current_project_id:user-123')).toBe(null)
    })
  })

  // ========================================
  // 10. useProject Hook Error
  // ========================================
  describe('useProject hook', () => {
    it('should throw error when used outside provider', () => {
      // Suppress the error output
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

      expect(() => {
        renderHook(() => useProject())
      }).toThrow('useProject must be used within a ProjectProvider')

      consoleSpy.mockRestore()
    })
  })

  // ========================================
  // 11. Loading State
  // ========================================
  describe('Loading State', () => {
    it('should start with loading=true when user exists', () => {
      vi.mocked(projectApi.getAll).mockImplementation(() => new Promise(() => {})) // Never resolves
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      // Loading starts as true because user exists
      expect(result.current.loading).toBe(true)
    })

    it('should set loading to false after load completes', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })
    })

    it('should set loading to false on error', async () => {
      vi.mocked(projectApi.getAll).mockRejectedValue(new Error('Failed'))
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })
    })
  })

  // ========================================
  // 12. Current Project Derivation
  // ========================================
  describe('currentProject derivation', () => {
    it('should derive currentProject from projects list', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.currentProject).not.toBe(null)
      })

      // project-2 should be selected (most recent)
      expect(result.current.currentProject?.id).toBe('project-2')
      expect(result.current.currentProject?.name).toBe('Project 2')
    })

    it('should return null when projectId not found in list', async () => {
      vi.mocked(projectApi.getAll).mockResolvedValue(mockProjects)
      localStorage.setItem('zenstory_current_project_id:user-123', 'project-2')
      const wrapper = createWrapper(mockUser)

      const { result } = renderHook(() => useProject(), { wrapper })

      await waitFor(() => {
        expect(result.current.currentProjectId).toBe('project-2')
      })

      // Manually set to non-existent
      act(() => {
        result.current.setCurrentProjectId('non-existent')
      })

      expect(result.current.currentProject).toBe(null)
    })
  })
})
