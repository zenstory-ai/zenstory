/**
 * SkillsPage Unit Tests
 *
 * These tests verify that the SkillsPage component mounts correctly
 * and that all useEffect hooks reference functions that are defined
 * before they are used (temporal dead zone prevention).
 *
 * IMPORTANT: This catches issues like the "Cannot access 'w' before initialization"
 * error that only manifests in production builds with minification.
 *
 * Background: A production bug was found where useEffect dependency arrays
 * referenced functions that were defined later in the component. In development
 * mode, JavaScript's hoisting behavior masked this issue, but minification
 * in production exposed the temporal dead zone error.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SkillsPage from '../SkillsPage'
import { ApiError } from '../../lib/apiClient'
import type { Skill, AddedSkill, PublicSkill, SkillCategory, MySkillsResponse, PublicSkillListResponse } from '../../types'

// Mock dependencies
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'title': 'Skills',
        'description': 'Manage your AI assistant skills',
        'discoverTab': 'Discover',
        'mySkillsTab': 'My Skills',
        'create': 'Create',
        'searchPlaceholder': 'Search skills...',
        'searchMySkillsPlaceholder': 'Search my skills...',
        'allCategories': 'All',
        'noSkillsFound': 'No skills found',
        'addToMine': 'Add',
        'alreadyAdded': 'Added',
        'addCount': 'users',
        'expand': 'Expand',
        'collapse': 'Collapse',
        'userSkills': 'My Skills',
        'addedSkills': 'Added Skills',
        'noUserSkills': 'No skills yet',
        'noUserSkillsHint': 'Save writing rules you reuse as skills',
        'noAddedSkills': 'No added skills',
        'createFirst': 'Create your first skill',
        'discoverMore': 'Discover more',
        'browsePublic': 'Browse public skills',
        'noSearchResults': 'No results found',
        'skills:readonly': 'Read-only',
        'skills:added': 'Added',
        'skills:share.title': 'Share',
        'skills:remove': 'Remove',
        'skills:editSkill': 'Edit Skill',
        'skills:createSkill': 'Create Skill',
        'skills:deleteConfirm.title': 'Delete Skill',
        'skills:deleteConfirm.message': 'Are you sure you want to delete this skill?',
        'skills:batch.deleteConfirmTitle': 'Delete Skills',
        'skills:batch.deleteConfirmMessage': `Delete ${1} skills?`,
        'skills:batch.selectAll': 'Select All',
        'skills:batch.selected': `${1} selected`,
        'skills:batch.clearSelection': 'Clear',
        'skills:batch.delete': 'Delete',
        'skills:form.name': 'Name',
        'skills:form.namePlaceholder': 'Skill name',
        'skills:form.description': 'Description',
        'skills:form.descriptionPlaceholder': 'Brief description',
        'skills:form.triggers': 'Triggers',
        'skills:form.triggersPlaceholder': 'trigger1, trigger2',
        'skills:form.triggersHint': 'Comma-separated keywords',
        'skills:form.instructions': 'Instructions',
        'skills:form.instructionsPlaceholder': 'Detailed instructions',
        'skills:form.instructionsHint': 'Markdown supported',
        'skills:collapse': 'Collapse',
        'skills:expand': 'Expand',
        'skills:official': 'Official',
        'stats.title': 'Statistics',
        'common:cancel': 'Cancel',
        'common:save': 'Save',
        'common:delete': 'Delete',
      }
      return translations[key] || key
    },
    i18n: {
      changeLanguage: vi.fn(),
    },
  }),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => false,
  useIsTablet: () => false,
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProject: { id: 'project-1', name: 'Test Project' },
    currentProjectId: 'project-1',
  }),
}))

vi.mock('../../lib/api', () => ({
  skillsApi: {
    mySkills: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    batchUpdate: vi.fn(),
    importSkill: vi.fn(),
    exportSkill: vi.fn(),
    listResources: vi.fn(),
    getResourceContent: vi.fn(),
    upsertResource: vi.fn(),
    deleteResource: vi.fn(),
  },
  publicSkillsApi: {
    list: vi.fn(),
    get: vi.fn(),
    getCategories: vi.fn(),
    add: vi.fn(),
    remove: vi.fn(),
  },
}))

vi.mock('../../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

// Mock react-markdown to avoid complexity
vi.mock('react-markdown', () => ({
  default: ({ children }: { children: string }) => <div>{children}</div>,
}))

// Mock dialog components
vi.mock('../../components/SkillStatsDialog', () => ({
  SkillStatsDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="stats-dialog">Stats Dialog</div> : null,
}))

vi.mock('../../components/skills/ShareSkillModal', () => ({
  ShareSkillModal: ({ skill }: { skill: Skill | null }) =>
    skill ? <div data-testid="share-modal">Share: {skill.name}</div> : null,
}))

vi.mock('../../components/subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open, title }: { open: boolean; title: string }) =>
    open ? <div data-testid="upgrade-modal">{title}</div> : null,
}))

// Suppress console noise
vi.spyOn(console, 'error').mockImplementation(() => {})
vi.spyOn(console, 'log').mockImplementation(() => {})

import { skillsApi, publicSkillsApi } from '../../lib/api'
import { toast } from '../../lib/toast'

// Test data fixtures
const mockUserSkills: Skill[] = [
  {
    id: 'skill-1',
    name: 'Writing Assistant',
    description: 'Helps with creative writing',
    triggers: ['write', 'help'],
    instructions: 'You are a writing assistant.',
    source: 'user',
    is_active: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
  },
  {
    id: 'skill-2',
    name: 'Character Builder',
    description: 'Creates character profiles',
    triggers: ['character', 'profile'],
    instructions: 'Help create detailed character profiles.',
    source: 'user',
    is_active: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-03T00:00:00Z',
  },
]

const mockAddedSkills: AddedSkill[] = [
  {
    id: 'added-1',
    public_skill_id: 'public-1',
    name: 'Plot Twist Generator',
    description: 'Generates plot twists',
    instructions: 'Generate unexpected plot twists.',
    category: 'Writing',
    source: 'added',
    is_active: true,
    added_at: '2024-01-01T00:00:00Z',
  },
]

const mockPublicSkills: PublicSkill[] = [
  {
    id: 'public-1',
    name: 'Dialogue Expert',
    description: 'Expert at writing dialogue',
    instructions: 'Write natural dialogue.',
    category: 'Writing',
    tags: ['dialogue'],
    source: 'official',
    author_id: null,
    status: 'approved',
    add_count: 100,
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 'public-2',
    name: 'World Builder',
    description: 'Builds immersive worlds',
    instructions: 'Create detailed world settings.',
    category: 'Worldbuilding',
    tags: ['world'],
    source: 'community',
    author_id: 'user-1',
    author_name: 'Community User',
    status: 'approved',
    add_count: 50,
    created_at: '2024-01-01T00:00:00Z',
  },
]

const mockCategories: SkillCategory[] = [
  { name: 'Writing', count: 10 },
  { name: 'Worldbuilding', count: 5 },
  { name: 'Characters', count: 8 },
]

describe('SkillsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // Set up default mock implementations
    vi.mocked(publicSkillsApi.list).mockImplementation(async () => ({
      skills: mockPublicSkills,
      total: 2,
      page: 1,
      page_size: 20,
    } as PublicSkillListResponse))

    vi.mocked(publicSkillsApi.getCategories).mockImplementation(async () => ({
      categories: mockCategories,
    }))

    vi.mocked(skillsApi.mySkills).mockImplementation(async () => ({
      user_skills: mockUserSkills,
      added_skills: mockAddedSkills,
      total: 3,
    } as MySkillsResponse))
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // ========================================
  // 1. Component Mount Tests (TDZ Prevention)
  // ========================================
  describe('Component Mount (Temporal Dead Zone Prevention)', () => {
    it('should mount without throwing initialization errors', async () => {
      // This test verifies that all functions used in useEffect hooks
      // are defined BEFORE the useEffect hooks that reference them.
      // In development mode, hoisting masks temporal dead zone issues.
      // In production minification, these errors are exposed.
      render(<SkillsPage />)

      // Wait for the component to finish rendering
      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      // Verify loadDiscoverData was called on mount (discover is default tab)
      expect(publicSkillsApi.list).toHaveBeenCalled()
      expect(publicSkillsApi.getCategories).toHaveBeenCalled()
      expect(publicSkillsApi.list).toHaveBeenCalledTimes(1)
    })

    it('ignores a stale public-skill response after a newer category request resolves', async () => {
      let resolveInitial!: (value: PublicSkillListResponse) => void
      vi.mocked(publicSkillsApi.list)
        .mockImplementationOnce(() => new Promise((resolve) => { resolveInitial = resolve }))
        .mockResolvedValueOnce({
          skills: [{ ...mockPublicSkills[1], name: 'Newest category result' }],
          total: 1,
          page: 1,
          page_size: 20,
        })
      const user = userEvent.setup()
      render(<SkillsPage />)

      const search = screen.getByPlaceholderText('Search skills...')
      await user.type(search, 'w')
      expect(await screen.findByText('Newest category result')).toBeInTheDocument()

      resolveInitial({
        skills: [{ ...mockPublicSkills[0], name: 'Stale initial result' }],
        total: 1,
        page: 1,
        page_size: 20,
      })
      await waitFor(() => expect(screen.queryByText('Stale initial result')).not.toBeInTheDocument())
      expect(screen.getByText('Newest category result')).toBeInTheDocument()
    })

    it('should not throw when useEffect callbacks reference useCallback functions', async () => {
      // This specifically tests the pattern where useEffect depends on
      // functions wrapped in useCallback - those functions must be defined
      // before the useEffect hook
      const { unmount } = render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      // Unmounting should also not throw
      expect(() => unmount()).not.toThrow()
    })
  })

  // ========================================
  // 2. Initial Load Tests
  // ========================================
  describe('Initial Load', () => {
    it('should load discover data on mount', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalled()
        expect(publicSkillsApi.getCategories).toHaveBeenCalled()
      })
    })

    it('should display discover tab by default', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Discover')).toBeInTheDocument()
        // The discover tab should be active (has gradient styling)
        const discoverButton = screen.getByRole('button', { name: /discover/i })
        expect(discoverButton).toBeInTheDocument()
      })
    })

    it('should show loading state initially', async () => {
      // Delay the API response
      vi.mocked(publicSkillsApi.list).mockImplementation(() =>
        new Promise(resolve => setTimeout(() => resolve({
          skills: [],
          total: 0,
          page: 1,
          page_size: 20,
        } as PublicSkillListResponse), 100))
      )

      render(<SkillsPage />)

      // Should show loading spinner
      expect(document.querySelector('.animate-spin')).toBeInTheDocument()
    })
  })

  // ========================================
  // 3. Tab Switching Tests
  // ========================================
  describe('Tab Switching', () => {
    it('should switch between tabs correctly', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      // Click on "My Skills" tab
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      // Should load my skills data
      await waitFor(() => {
        expect(skillsApi.mySkills).toHaveBeenCalled()
      })
    })

    it('should load my skills only when tab becomes active', async () => {
      render(<SkillsPage />)

      // Discover tab is active by default, mySkills should not be called yet
      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalled()
      })

      // Clear the mock to track new calls
      vi.mocked(skillsApi.mySkills).mockClear()

      // Switch to my skills tab
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      // Now mySkills should be called
      await waitFor(() => {
        expect(skillsApi.mySkills).toHaveBeenCalled()
      })
    })
  })

  // =================================-------
  // 4. Discover Tab Tests
  // ========================================
  describe('Discover Tab', () => {
    it('should display public skills', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Dialogue Expert')).toBeInTheDocument()
        expect(screen.getByText('World Builder')).toBeInTheDocument()
      })
    })

    it('should display categories', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Writing')).toBeInTheDocument()
        expect(screen.getByText('Worldbuilding')).toBeInTheDocument()
      })
    })

    it('should filter by category', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      // Click on a category. Use findByRole so the query waits for the category
      // buttons to render — they can appear after the "Skills" heading, which made
      // the synchronous getByRole flake on slower (CI) runs.
      const writingCategory = await screen.findByRole('button', { name: 'Writing' })
      await userEvent.click(writingCategory)

      // API should be called with category filter
      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalledWith(
          expect.objectContaining({ category: 'Writing' })
        )
      })
    })

    it('should search skills', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      const searchInput = screen.getByPlaceholderText('Search skills...')
      await userEvent.type(searchInput, 'dialogue')

      // Wait for debounce and API call
      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalledWith(
          expect.objectContaining({ search: 'dialogue' })
        )
      }, { timeout: 1000 })
    })

    it('should add public skill to my skills', async () => {
      vi.mocked(publicSkillsApi.add).mockResolvedValue(undefined)

      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Dialogue Expert')).toBeInTheDocument()
      })

      // Find and click the add button
      const addButtons = screen.getAllByRole('button', { name: /add$/i })
      await userEvent.click(addButtons[0])

      await waitFor(() => {
        expect(publicSkillsApi.add).toHaveBeenCalled()
      })
    })
  })

  // ========================================
  // 5. My Skills Tab Tests
  // ========================================
  describe('My Skills Tab', () => {
    it('should display user skills', async () => {
      render(<SkillsPage />)

      // Switch to my skills tab
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      await waitFor(() => {
        expect(screen.getByText('Writing Assistant')).toBeInTheDocument()
        expect(screen.getByText('Character Builder')).toBeInTheDocument()
      })
    })

    it('should display added skills', async () => {
      render(<SkillsPage />)

      // Switch to my skills tab
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      await waitFor(() => {
        expect(screen.getByText('Plot Twist Generator')).toBeInTheDocument()
      })
    })

    it('should open create skill modal', async () => {
      render(<SkillsPage />)

      // Switch to my skills tab
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      await waitFor(() => {
        expect(screen.getByText('Writing Assistant')).toBeInTheDocument()
      })

      // Click create button
      const createButton = screen.getByRole('button', { name: /create$/i })
      await userEvent.click(createButton)

      // Modal should appear
      await waitFor(() => {
        expect(screen.getByText('Create Skill')).toBeInTheDocument()
      })
    })

    it('should keep typing focus inside create skill modal inputs', async () => {
      render(<SkillsPage />)

      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      await waitFor(() => {
        expect(screen.getByText('Writing Assistant')).toBeInTheDocument()
      })

      const createButton = screen.getByRole('button', { name: /create$/i })
      await userEvent.click(createButton)

      await waitFor(() => {
        expect(screen.getByText('Create Skill')).toBeInTheDocument()
      })

      const nameInput = screen.getByPlaceholderText('Skill name')
      await userEvent.type(nameInput, 'Skill 123')

      expect(nameInput).toHaveValue('Skill 123')
      expect(nameInput).toHaveFocus()
    })

    it('should search my skills', async () => {
      render(<SkillsPage />)

      // Switch to my skills tab
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      vi.mocked(skillsApi.mySkills).mockClear()

      const searchInput = screen.getByPlaceholderText('Search my skills...')
      await userEvent.type(searchInput, 'writing')

      // Wait for debounce and API call
      await waitFor(() => {
        expect(skillsApi.mySkills).toHaveBeenCalledWith(
          expect.objectContaining({ search: 'writing' })
        )
      }, { timeout: 1000 })
    })

    it('shows upgrade modal when creating skill hits quota limit', async () => {
      vi.mocked(skillsApi.create).mockRejectedValueOnce(new ApiError(402, 'ERR_QUOTA_EXCEEDED'))

      render(<SkillsPage />)

      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      await waitFor(() => {
        expect(screen.getByText('Writing Assistant')).toBeInTheDocument()
      })

      const createButton = screen.getByRole('button', { name: /create$/i })
      await userEvent.click(createButton)

      await waitFor(() => {
        expect(screen.getByText('Create Skill')).toBeInTheDocument()
      })

      await userEvent.type(screen.getByPlaceholderText('Skill name'), 'Quota Skill')
      await userEvent.type(screen.getByPlaceholderText('Detailed instructions'), 'do something')
      await userEvent.click(screen.getByRole('button', { name: /save/i }))

      await waitFor(() => {
        expect(skillsApi.create).toHaveBeenCalled()
        expect(screen.getByTestId('upgrade-modal')).toBeInTheDocument()
      })
    })
  })

  // ========================================
  // 5b. Import / Export / Resources
  // ========================================
  describe('Import, export and resources', () => {
    const openMySkills = async () => {
      render(<SkillsPage />)
      await userEvent.click(screen.getByRole('button', { name: /my skills/i }))
      await waitFor(() => {
        expect(screen.getByText('Writing Assistant')).toBeInTheDocument()
      })
    }

    it('imports a skill package and shows returned warnings', async () => {
      vi.mocked(skillsApi.importSkill).mockResolvedValueOnce({
        skill: { ...mockUserSkills[0], id: 'imported-1', name: 'Imported Skill' },
        warnings: ['scripts/run.py: scripts are not supported', 'assets/logo.png: unsupported file type'],
      })

      render(<SkillsPage />)

      const file = new File(['zip-bytes'], 'my-skill.zip', { type: 'application/zip' })
      await userEvent.upload(screen.getByTestId('skill-import-input'), file)

      await waitFor(() => {
        expect(skillsApi.importSkill).toHaveBeenCalledWith(file)
      })
      const result = await screen.findByTestId('skill-import-result')
      expect(result).toHaveTextContent('scripts/run.py: scripts are not supported')
      expect(result).toHaveTextContent('assets/logo.png: unsupported file type')
      // Imported skill list is refreshed
      await waitFor(() => {
        expect(skillsApi.mySkills).toHaveBeenCalled()
      })
    })

    it('rejects unsupported import file types before calling the API', async () => {
      render(<SkillsPage />)

      const file = new File(['x'], 'tool.exe', { type: 'application/octet-stream' })
      await userEvent.upload(screen.getByTestId('skill-import-input'), file, { applyAccept: false })

      expect(skillsApi.importSkill).not.toHaveBeenCalled()
      expect(toast.error).toHaveBeenCalledWith('skills:import.invalidType')
    })

    it('exports a skill as zip', async () => {
      vi.mocked(skillsApi.exportSkill).mockResolvedValueOnce(undefined)
      await openMySkills()

      const exportButtons = screen.getAllByRole('button', { name: 'skills:export.button' })
      await userEvent.click(exportButtons[0])

      expect(skillsApi.exportSkill).toHaveBeenCalledWith('skill-1', 'Writing Assistant')
    })

    it('shows resources read-only for added skills', async () => {
      vi.mocked(skillsApi.listResources).mockResolvedValue({
        resources: [{ path: 'references/guide.md', size: 120, updated_at: '2024-01-01T00:00:00Z' }],
      })
      vi.mocked(skillsApi.getResourceContent).mockResolvedValue({
        path: 'references/guide.md',
        content: '# Guide',
      })
      await openMySkills()

      const addedCard = screen.getByText('Plot Twist Generator').closest('.group') as HTMLElement
      await userEvent.click(within(addedCard).getByTitle('Expand'))

      const section = await within(addedCard).findByTestId('skill-resources-section')
      expect(skillsApi.listResources).toHaveBeenCalledWith('added-1')
      expect(within(section).queryByText('resources.add')).not.toBeInTheDocument()
      expect(within(section).queryByRole('button', { name: 'resources.delete' })).not.toBeInTheDocument()

      await userEvent.click(within(section).getByText('references/guide.md'))
      const textarea = await within(section).findByRole('textbox', { name: 'resources.content' })
      expect(textarea).toHaveValue('# Guide')
      expect(textarea).toHaveAttribute('readonly')
      expect(within(section).queryByText('resources.saveFile')).not.toBeInTheDocument()
    })

    it('hides the resources section for added skills without resources', async () => {
      vi.mocked(skillsApi.listResources).mockResolvedValue({ resources: [] })
      await openMySkills()

      const addedCard = screen.getByText('Plot Twist Generator').closest('.group') as HTMLElement
      await userEvent.click(within(addedCard).getByTitle('Expand'))

      await waitFor(() => {
        expect(skillsApi.listResources).toHaveBeenCalledWith('added-1')
      })
      expect(within(addedCard).queryByTestId('skill-resources-section')).not.toBeInTheDocument()
    })

    it('validates resource path prefix and extension before saving own skill resources', async () => {
      vi.mocked(skillsApi.listResources).mockResolvedValue({ resources: [] })
      vi.mocked(skillsApi.upsertResource).mockResolvedValue({})
      await openMySkills()

      await userEvent.click(screen.getAllByRole('button', { name: 'Edit Skill' })[0])
      const section = await screen.findByTestId('skill-resources-section')
      await userEvent.click(within(section).getByText('resources.add'))

      const pathInput = within(section).getByRole('textbox', { name: 'resources.path' })
      const contentInput = within(section).getByRole('textbox', { name: 'resources.content' })
      await userEvent.type(contentInput, 'notes')

      await userEvent.clear(pathInput)
      await userEvent.type(pathInput, 'notes/a.md')
      await userEvent.click(within(section).getByText('resources.saveFile'))
      expect(within(section).getByRole('alert')).toHaveTextContent('resources.errors.prefix')

      await userEvent.clear(pathInput)
      await userEvent.type(pathInput, 'references/run.py')
      await userEvent.click(within(section).getByText('resources.saveFile'))
      expect(within(section).getByRole('alert')).toHaveTextContent('resources.errors.extension')
      expect(skillsApi.upsertResource).not.toHaveBeenCalled()

      await userEvent.clear(pathInput)
      await userEvent.type(pathInput, 'references/notes.md')
      await userEvent.click(within(section).getByText('resources.saveFile'))
      await waitFor(() => {
        expect(skillsApi.upsertResource).toHaveBeenCalledWith('skill-1', 'references/notes.md', 'notes')
      })
    })
  })

  // ========================================
  // 6. Error Handling Tests
  // ========================================
  describe('Error Handling', () => {
    it('should handle API errors gracefully', async () => {
      vi.mocked(publicSkillsApi.list).mockRejectedValue(new Error('Network error'))

      render(<SkillsPage />)

      // Component should still render without crashing
      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })
    })

    it('should handle empty skills list', async () => {
      vi.mocked(publicSkillsApi.list).mockResolvedValue({
        skills: [],
        total: 0,
        page: 1,
        page_size: 20,
      } as PublicSkillListResponse)

      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('noDiscoverableSkills')).toBeInTheDocument()
      })
    })
  })

  // ========================================
  // 7. useCallback Function Order Tests
  // ========================================
  describe('useCallback Function Order', () => {
    it('should have loadDiscoverData defined before useEffect that uses it', async () => {
      // This is a structural test - if loadDiscoverData was defined after
      // the useEffect that depends on it, this test would fail in minified builds
      render(<SkillsPage />)

      // The component should mount successfully
      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      // And the function should have been called
      expect(publicSkillsApi.list).toHaveBeenCalled()
    })

    it('should have loadSkills defined before useEffect that uses it', async () => {
      render(<SkillsPage />)

      // Switch to my-skills tab which triggers the loadSkills useEffect
      const mySkillsTab = screen.getByRole('button', { name: /my skills/i })
      await userEvent.click(mySkillsTab)

      // Should load without errors
      await waitFor(() => {
        expect(skillsApi.mySkills).toHaveBeenCalled()
      })
    })

    it('should have loadPublicSkills defined before useEffect that uses it', async () => {
      render(<SkillsPage />)

      await waitFor(() => {
        expect(screen.getByText('Skills')).toBeInTheDocument()
      })

      // Change search query which triggers loadPublicSkills
      const searchInput = screen.getByPlaceholderText('Search skills...')
      await userEvent.type(searchInput, 'test')

      // Should not throw TDZ error
      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalled()
      })
    })
  })

  // ========================================
  // Launch hardening: error feedback, limits, discover paging/search
  // ========================================
  describe('Launch hardening', () => {
    const openMySkills = async () => {
      render(<SkillsPage />)
      await userEvent.click(screen.getByRole('button', { name: /my skills/i }))
      await waitFor(() => {
        expect(screen.getByText('Writing Assistant')).toBeInTheDocument()
      })
    }

    it('shows the backend error in a toast when saving a skill fails', async () => {
      vi.mocked(skillsApi.create).mockRejectedValueOnce(
        new ApiError(422, 'ERR_SKILL_PACKAGE_INVALID', undefined, '技能名称不能超过 100 个字符'),
      )
      await openMySkills()
      await userEvent.click(screen.getByRole('button', { name: /create$/i }))
      await userEvent.type(screen.getByPlaceholderText('Skill name'), 'Too long')
      await userEvent.type(screen.getByPlaceholderText('Detailed instructions'), 'do something')
      await userEvent.click(screen.getByRole('button', { name: /save/i }))

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('技能名称不能超过 100 个字符'))
      })
      // 弹窗保持打开，用户的输入还在
      expect(screen.getByPlaceholderText('Skill name')).toHaveValue('Too long')
    })

    it('falls back to a localized message for non-API failures', async () => {
      vi.mocked(skillsApi.delete).mockRejectedValueOnce('boom')
      await openMySkills()
      const card = screen.getByText('Writing Assistant').closest('.group') as HTMLElement
      const buttons = within(card).getAllByRole('button')
      // 卡片按钮顺序：勾选、编辑、删除……
      await userEvent.click(buttons[2])
      await userEvent.click(await screen.findByRole('button', { name: 'Delete' }))

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith('skills:errors.deleteFailed')
      })
    })

    it('limits form fields to the backend lengths and shows counters', async () => {
      await openMySkills()
      await userEvent.click(screen.getByRole('button', { name: /create$/i }))

      expect(screen.getByPlaceholderText('Skill name')).toHaveAttribute('maxLength', '100')
      expect(screen.getByPlaceholderText('Brief description')).toHaveAttribute('maxLength', '1024')
      expect(screen.getByPlaceholderText('Detailed instructions')).toHaveAttribute('maxLength', '50000')
      await userEvent.type(screen.getByPlaceholderText('Skill name'), 'abc')
      expect(screen.getByText('3 / 100')).toBeInTheDocument()
    })

    it('sends an empty description when the user clears it while editing', async () => {
      vi.mocked(skillsApi.update).mockResolvedValueOnce(mockUserSkills[0])
      await openMySkills()
      await userEvent.click(screen.getAllByRole('button', { name: 'Edit Skill' })[0])
      const description = screen.getByPlaceholderText('Brief description')
      await userEvent.clear(description)
      await userEvent.click(screen.getByRole('button', { name: /save/i }))

      await waitFor(() => {
        expect(skillsApi.update).toHaveBeenCalledWith(
          'skill-1',
          expect.objectContaining({ description: '' }),
        )
      })
    })

    it('explains what a custom skill is when the author has none yet', async () => {
      vi.mocked(skillsApi.mySkills).mockResolvedValue({
        user_skills: [],
        added_skills: [],
        total: 0,
      } as MySkillsResponse)
      render(<SkillsPage />)
      await userEvent.click(screen.getByRole('button', { name: /my skills/i }))

      expect(await screen.findByText('No skills yet')).toBeInTheDocument()
      expect(screen.getByText('Save writing rules you reuse as skills')).toBeInTheDocument()
    })

    it('shows the review status of shared skills', async () => {
      vi.mocked(skillsApi.mySkills).mockResolvedValue({
        user_skills: [{ ...mockUserSkills[0], share_status: 'unpublished' }],
        added_skills: [],
        total: 1,
      } as MySkillsResponse)
      await openMySkills()

      expect(screen.getByTestId('skill-share-status')).toHaveTextContent('skills:shareStatus.unpublished')
    })

    it('shows a toast when adding a public skill fails', async () => {
      vi.mocked(publicSkillsApi.add).mockRejectedValueOnce(new Error('服务器开小差了'))
      render(<SkillsPage />)
      await screen.findByText('Dialogue Expert')
      await userEvent.click(screen.getAllByRole('button', { name: /add$/i })[0])

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith('服务器开小差了')
      })
    })

    it('loads the next page of public skills with "load more"', async () => {
      const page2Skill = { ...mockPublicSkills[1], id: 'public-21', name: 'Twenty First Skill' }
      vi.mocked(publicSkillsApi.list)
        .mockResolvedValueOnce({ skills: mockPublicSkills, total: 3, page: 1, page_size: 20 })
        .mockResolvedValueOnce({ skills: [page2Skill], total: 3, page: 2, page_size: 20 })
      render(<SkillsPage />)
      await screen.findByText('Dialogue Expert')

      await userEvent.click(screen.getByRole('button', { name: 'loadMore' }))

      expect(await screen.findByText('Twenty First Skill')).toBeInTheDocument()
      expect(publicSkillsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2, page_size: 20 }),
      )
      // 前一页的内容保留，已经全部加载后不再显示按钮
      expect(screen.getByText('Dialogue Expert')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'loadMore' })).not.toBeInTheDocument()
    })

    it('shows an empty-library state with a create entry instead of a failed-search message', async () => {
      vi.mocked(publicSkillsApi.list).mockResolvedValue({
        skills: [],
        total: 0,
        page: 1,
        page_size: 20,
      } as PublicSkillListResponse)
      render(<SkillsPage />)

      expect(await screen.findByText('noDiscoverableSkills')).toBeInTheDocument()
      expect(screen.getByText('noDiscoverableSkillsHint')).toBeInTheDocument()
      expect(screen.queryByText('No skills found')).not.toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: 'createSkill' }))
      expect(await screen.findByText('Create Skill')).toBeInTheDocument()
    })

    it('keeps the no-match message for a search that finds nothing', async () => {
      vi.mocked(publicSkillsApi.list).mockResolvedValue({
        skills: [],
        total: 0,
        page: 1,
        page_size: 20,
      } as PublicSkillListResponse)
      render(<SkillsPage />)
      await screen.findByText('noDiscoverableSkills')

      fireEvent.change(screen.getByTestId('public-skill-search'), { target: { value: '不存在' } })

      expect(await screen.findByText('No skills found')).toBeInTheDocument()
      expect(screen.queryByText('noDiscoverableSkills')).not.toBeInTheDocument()
    })

    it('debounces the discover search and waits for IME composition to end', async () => {
      render(<SkillsPage />)
      await screen.findByText('Dialogue Expert')
      vi.mocked(publicSkillsApi.list).mockClear()

      const search = screen.getByTestId('public-skill-search')
      fireEvent.compositionStart(search)
      fireEvent.change(search, { target: { value: 'ren' } })
      fireEvent.change(search, { target: { value: 'renwu' } })
      await new Promise((resolve) => setTimeout(resolve, 400))
      expect(publicSkillsApi.list).not.toHaveBeenCalled()

      fireEvent.change(search, { target: { value: '人物' } })
      fireEvent.compositionEnd(search, { target: { value: '人物' } })
      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalledTimes(1)
      })
      expect(publicSkillsApi.list).toHaveBeenCalledWith(expect.objectContaining({ search: '人物' }))

      // 连续输入只在停顿后查询一次
      vi.mocked(publicSkillsApi.list).mockClear()
      fireEvent.change(search, { target: { value: '人物a' } })
      fireEvent.change(search, { target: { value: '人物ab' } })
      fireEvent.change(search, { target: { value: '人物abc' } })
      await waitFor(() => {
        expect(publicSkillsApi.list).toHaveBeenCalledTimes(1)
      })
      expect(publicSkillsApi.list).toHaveBeenCalledWith(expect.objectContaining({ search: '人物abc' }))
    })

    it('fetches full instructions when expanding a truncated public skill', async () => {
      vi.mocked(publicSkillsApi.list).mockResolvedValue({
        skills: [{ ...mockPublicSkills[0], instructions: 'Preview only', instructions_truncated: true }],
        total: 1,
        page: 1,
        page_size: 20,
      })
      vi.mocked(publicSkillsApi.get).mockResolvedValue({
        ...mockPublicSkills[0],
        instructions: 'The complete instructions',
      })
      render(<SkillsPage />)
      await screen.findByText('Dialogue Expert')

      await userEvent.click(screen.getByRole('button', { name: /expand/i }))

      expect(await screen.findByText('The complete instructions')).toBeInTheDocument()
      expect(publicSkillsApi.get).toHaveBeenCalledWith('public-1')
    })
  })
})
