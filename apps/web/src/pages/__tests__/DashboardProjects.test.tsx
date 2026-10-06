import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardProjects from '../DashboardProjects'
import { ApiError } from '../../lib/apiClient'

const mockNavigate = vi.fn()
const mockDeleteProject = vi.fn()
const mockToastError = vi.fn()
const mockRefreshProjects = vi.fn()
let isMobile = false
let isTablet = false
let mockLoading = false
let mockError: string | null = null

let mockProjects = [
  {
    id: 'project-1',
    name: 'Novel Alpha',
    description: 'Primary draft',
    project_type: 'novel',
    updated_at: '2026-04-07T00:00:00Z',
  },
  {
    id: 'project-2',
    name: 'Script Beta',
    description: 'Screenplay draft',
    project_type: 'screenplay',
    updated_at: '2026-04-06T00:00:00Z',
  },
]

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      (
        {
          'projects.all': 'All Projects',
          'projects.new': 'New Project',
          'projects.searchPlaceholder': 'Search projects',
          'projects.count': `${options?.count ?? 0} projects`,
          'projects.noMatch': 'No matching projects',
          'projects.tryDifferent': 'Try a different filter',
          'projects.empty': 'No projects yet',
          'projects.emptyHint': 'Create a project to begin',
          'projects.deleteProject': 'Delete project',
          'projects.confirmDeleteTitle': 'Delete project',
          'projects.confirmDeleteMessage': 'Delete this project?',
          'projectType.novel.name': 'Novel',
          'projectType.short.name': 'Short',
          'projectType.screenplay.name': 'Screenplay',
          'common:delete': 'Delete',
          'common:cancel': 'Cancel',
          'projects.filterAll': 'All',
          'common.loading': 'Loading...',
          'common.retry': 'Retry',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: mockProjects,
    loading: mockLoading,
    error: mockError,
    refreshProjects: mockRefreshProjects,
    deleteProject: mockDeleteProject,
  }),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => isMobile,
  useIsTablet: () => isTablet,
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    error: (...args: unknown[]) => mockToastError(...args),
  },
}))

vi.mock('../../lib/errorHandler', () => ({
  handleApiError: (error: unknown) => (error instanceof Error ? error.message : 'Unknown error'),
  toUserErrorMessage: (message: string) => message,
}))

vi.mock('../../components/dashboard/DashboardPageHeader', () => ({
  DashboardPageHeader: ({ title, action }: { title: string; action: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {action}
    </div>
  ),
}))

vi.mock('../../components/dashboard/DashboardSearchBar', () => ({
  DashboardSearchBar: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string
    onChange: (value: string) => void
    placeholder: string
  }) => (
    <input
      aria-label="Project search"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
    />
  ),
}))

vi.mock('../../components/dashboard/DashboardFilterPills', () => ({
  DashboardFilterPills: ({
    options,
    value,
    onChange,
  }: {
    options: Array<{ value: string; label: string }>
    value: string
    onChange: (value: string) => void
  }) => (
    <div>
      {options.map((option) => (
        <button
          key={option.value}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}))

vi.mock('../../components/ui/ConfirmDialog', () => ({
  ConfirmDialog: ({
    open,
    onConfirm,
    onClose,
  }: {
    open: boolean
    onConfirm: () => void
    onClose: () => void
  }) =>
    open ? (
      <div>
        <button onClick={onConfirm}>Delete</button>
        <button onClick={onClose}>Cancel</button>
      </div>
    ) : null,
}))

describe('DashboardProjects', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isMobile = false
    isTablet = false
    mockLoading = false
    mockError = null
    mockProjects = [
      {
        id: 'project-1',
        name: 'Novel Alpha',
        description: 'Primary draft',
        project_type: 'novel',
        updated_at: '2026-04-07T00:00:00Z',
      },
      {
        id: 'project-2',
        name: 'Script Beta',
        description: 'Screenplay draft',
        project_type: 'screenplay',
        updated_at: '2026-04-06T00:00:00Z',
      },
    ]
    mockDeleteProject.mockResolvedValue(undefined)
    mockRefreshProjects.mockResolvedValue(undefined)
  })

  it.each(['desktop', 'tablet', 'mobile'])('anchors project metadata to the card bottom on %s', (viewport) => {
    isMobile = viewport === 'mobile'
    isTablet = viewport === 'tablet'
    mockProjects = ['', 'Short description', 'A longer description that wraps across multiple lines. '.repeat(8)].map(
      (description, index) => ({
        id: `layout-${index}`,
        name: `Layout project ${index}`,
        description,
        project_type: 'novel',
        updated_at: '2026-04-07T00:00:00Z',
      }),
    )

    render(<DashboardProjects />)

    for (const project of mockProjects) {
      const card = screen.getByRole('button', { name: `Open project ${project.name}` })
      const content = card.querySelector(':scope > .relative')
      const footer = content?.lastElementChild

      expect(card).toHaveClass('flex', 'flex-col')
      expect(content).toHaveClass('flex', 'flex-1', 'flex-col')
      expect(footer).toHaveClass('mt-auto', 'flex', 'items-center', 'justify-between')
      expect(footer?.children).toHaveLength(2)
      expect(footer?.firstElementChild).toHaveTextContent('Novel')
      expect(footer?.lastElementChild).not.toBeEmptyDOMElement()
    }
  })

  it('renders loading, retryable error, and successful empty as distinct states', async () => {
    mockProjects = []
    mockLoading = true
    const { rerender } = render(<DashboardProjects />)

    expect(screen.getByTestId('projects-loading')).toBeInTheDocument()
    expect(screen.queryByText('No projects yet')).not.toBeInTheDocument()

    mockLoading = false
    mockError = 'Could not load projects'
    rerender(<DashboardProjects />)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load projects')
    expect(screen.queryByText('No projects yet')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(mockRefreshProjects).toHaveBeenCalledTimes(1))

    mockError = null
    rerender(<DashboardProjects />)
    expect(screen.getByText('No projects yet')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('filters projects by search and type and navigates to the selected project', () => {
    render(<DashboardProjects />)

    fireEvent.change(screen.getByLabelText('Project search'), { target: { value: 'script' } })
    expect(screen.getByText('Script Beta')).toBeInTheDocument()
    expect(screen.queryByText('Novel Alpha')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Novel' }))
    expect(screen.getByText('No matching projects')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Project search'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    fireEvent.click(screen.getByRole('button', { name: /Open project Novel Alpha/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')
  })

  it('deletes a project and surfaces non-auth delete errors', async () => {
    mockDeleteProject.mockRejectedValueOnce(new ApiError(500, 'delete failed'))

    render(<DashboardProjects />)

    fireEvent.click(screen.getAllByTitle('Delete project')[0]!)
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      expect(mockDeleteProject).toHaveBeenCalledWith('project-1')
      expect(mockToastError).toHaveBeenCalledWith('delete failed')
    })
  })

  it('supports empty-state rendering, keyboard navigation, and successful deletion', async () => {
    mockProjects = []
    const { rerender } = render(<DashboardProjects />)

    expect(screen.getByText('No projects yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'New Project' }))
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard')

    mockProjects = [
      {
        id: 'project-3',
        name: 'Keyboard Novel',
        description: 'Keyboard open',
        project_type: 'novel',
        updated_at: '2026-04-05T00:00:00Z',
      },
    ]
    rerender(<DashboardProjects />)

    fireEvent.keyDown(screen.getByRole('button', { name: /Open project Keyboard Novel/ }), { key: 'Enter' })
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-3')

    fireEvent.click(screen.getByTitle('Delete project'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      expect(mockDeleteProject).toHaveBeenCalledWith('project-3')
    })
  })

  it('renders mobile delete actions and falls back unknown project types to the novel label', () => {
    isMobile = true
    mockProjects = [
      {
        id: 'project-4',
        name: 'Mystery Project',
        description: 'Unknown type project',
        project_type: 'mystery' as never,
        updated_at: '2026-04-04T00:00:00Z',
      },
    ]

    render(<DashboardProjects />)

    expect(screen.getAllByText('Novel').length).toBeGreaterThan(0)
    expect(screen.getByTitle('Delete project')).toBeInTheDocument()
  })
})
