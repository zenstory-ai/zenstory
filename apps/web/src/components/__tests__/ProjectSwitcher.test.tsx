import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ProjectSwitcher } from '../ProjectSwitcher'

const mockNavigate = vi.fn()
const mockUseProject = vi.fn()
const toastErrorMock = vi.fn()
const toastInfoMock = vi.fn()
let mockIsMobile = false

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      (
        {
          'editor:projectSwitcher.loading': 'Loading projects',
          'editor:projectSwitcher.searchPlaceholder': 'Search projects',
          'editor:projectSwitcher.noResults': 'No results',
          'editor:projectSwitcher.noProjects': 'No projects',
          'editor:projectSwitcher.editName': 'Edit project',
          'editor:projectSwitcher.deleteProject': 'Delete project',
          'editor:projectSwitcher.projectNamePlaceholder': 'Project name',
          'editor:projectSwitcher.create': 'Create',
          'editor:projectSwitcher.createProject': 'Create project',
          'editor:projectSwitcher.confirmDelete': 'Delete it?',
          'editor:projectSwitcher.cannotDeleteLast': 'Cannot delete last project',
          'common:delete': 'Delete',
          'common:cancel': 'Cancel',
          'dashboard:billing.ctaUpgradePro': 'Upgrade',
          'home:pricingTeaser.viewPricing': 'View pricing',
        } as Record<string, string>
      )[key] ?? options?.defaultValue ?? key,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => mockUseProject(),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => mockIsMobile,
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorMock(...args),
    info: (...args: unknown[]) => toastInfoMock(...args),
  },
}))

vi.mock('../../config/upgradeExperience', () => ({
  getUpgradePromptDefinition: () => ({
    source: 'project_quota_blocked',
    surface: 'modal',
    billingPath: '/billing',
    pricingPath: '/pricing',
  }),
  buildUpgradeUrl: (path: string) => `https://zenstory.local${path}`,
}))

vi.mock('../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open, title }: { open: boolean; title: string }) =>
    open ? <div data-testid="upgrade-modal">{title}</div> : null,
}))

describe('ProjectSwitcher', () => {
  const switchProject = vi.fn()
  const createProject = vi.fn()
  const updateProject = vi.fn()
  const deleteProject = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    mockIsMobile = false
    mockUseProject.mockReturnValue({
      projects: [
        { id: 'project-1', name: 'Alpha', description: 'First project' },
        { id: 'project-2', name: 'Beta', description: 'Second project' },
      ],
      currentProject: { id: 'project-1', name: 'Alpha' },
      currentProjectId: 'project-1',
      loading: false,
      switchProject,
      createProject,
      updateProject,
      deleteProject,
    })
  })

  it('opens the dropdown, filters projects, and switches project', async () => {
    render(<ProjectSwitcher />)

    fireEvent.click(screen.getByRole('button', { name: /alpha/i }))
    expect(screen.getByPlaceholderText('Search projects')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Search projects'), {
      target: { value: 'Beta' },
    })
    expect(screen.getAllByText('Beta').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByText('Beta'))
    expect(switchProject).toHaveBeenCalledWith('project-2')
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-2')
  })

  it('renames a project inline and saves on Enter', async () => {
    updateProject.mockResolvedValue(undefined)

    render(<ProjectSwitcher />)
    fireEvent.click(screen.getByRole('button', { name: /alpha/i }))

    fireEvent.click(screen.getAllByTitle('Edit project')[0]!)
    const editInput = screen.getByDisplayValue('Alpha')
    fireEvent.change(editInput, { target: { value: 'Renamed project' } })
    fireEvent.keyDown(editInput, { key: 'Enter' })

    await waitFor(() => {
      expect(updateProject).toHaveBeenCalledWith('project-1', { name: 'Renamed project' })
    })
  })

  it('creates and deletes projects from the dropdown footer', async () => {
    createProject.mockResolvedValue({ id: 'project-3' })
    deleteProject.mockResolvedValue(undefined)

    const nativeConfirm = vi.fn(() => true)
    vi.stubGlobal('confirm', nativeConfirm)

    render(<ProjectSwitcher />)
    fireEvent.click(screen.getByRole('button', { name: /alpha/i }))

    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))
    fireEvent.change(screen.getByPlaceholderText('Project name'), {
      target: { value: 'Gamma' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => {
      expect(createProject).toHaveBeenCalledWith('Gamma')
      expect(mockNavigate).toHaveBeenCalledWith('/project/project-3')
    })

    fireEvent.click(screen.getByRole('button', { name: /alpha/i }))
    fireEvent.click(screen.getAllByTitle('Delete project')[1]!)

    const dialog = await screen.findByRole('dialog')
    expect(nativeConfirm).not.toHaveBeenCalled()
    expect(within(dialog).getByText('Delete it?')).toBeInTheDocument()
    expect(deleteProject).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      expect(deleteProject).toHaveBeenCalledWith('project-2')
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('cancelling the delete dialog keeps the project', async () => {
    render(<ProjectSwitcher />)
    fireEvent.click(screen.getByRole('button', { name: /alpha/i }))
    fireEvent.click(screen.getAllByTitle('Delete project')[1]!)

    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(deleteProject).not.toHaveBeenCalled()
  })

  it('explains in a toast, not a native alert, that the last project cannot be deleted', () => {
    const nativeAlert = vi.fn()
    vi.stubGlobal('alert', nativeAlert)
    mockUseProject.mockReturnValue({
      ...mockUseProject(),
      projects: [{ id: 'project-1', name: 'Alpha' }],
    })

    render(<ProjectSwitcher />)
    fireEvent.click(screen.getByRole('button', { name: /alpha/i }))
    fireEvent.click(screen.getByTitle('Delete project'))

    expect(nativeAlert).not.toHaveBeenCalled()
    expect(toastInfoMock).toHaveBeenCalledWith('Cannot delete last project')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(deleteProject).not.toHaveBeenCalled()
  })

  it('lets the trigger shrink and pins the mobile dropdown inside the viewport', () => {
    mockIsMobile = true
    const { container } = render(<ProjectSwitcher />)

    const root = container.firstElementChild as HTMLElement
    expect(root).toHaveClass('min-w-0', 'max-w-full')
    const trigger = screen.getByRole('button', { name: /alpha/i })
    expect(trigger).toHaveClass('max-w-full')

    fireEvent.click(trigger)
    const dropdown = screen.getByPlaceholderText('Search projects').closest('div.rounded-xl') as HTMLElement
    expect(dropdown).toHaveClass('fixed', 'left-4', 'right-4', 'top-12')
    expect(dropdown.className).not.toMatch(/translate/)
  })
})
