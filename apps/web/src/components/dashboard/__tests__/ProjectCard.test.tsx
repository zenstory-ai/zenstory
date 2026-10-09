import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProjectCard } from '../ProjectCard'
import type { Project, ProjectProgress } from '../../../types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      (
        {
          'dashboard:projects.openProject': `Open project ${String(options?.name ?? '')}`,
          'projects.deleteProject': 'Delete project',
          'projectType.novel.name': 'Novel',
          'dashboard:projectProgress.chapters': 'chapters written',
        } as Record<string, string>
      )[key] ?? key,
    i18n: { language: 'zh' },
  }),
}))

const project = (overrides: Partial<Project> = {}): Project =>
  ({
    id: 'p1',
    name: 'Rain Bookshop',
    description: '',
    project_type: 'novel',
    updated_at: '2026-10-09T00:00:00Z',
    ...overrides,
  }) as Project

const writing: ProjectProgress = { project_id: 'p1', written_units: 3, word_count: 9000, framework_ready: false }

function renderCard(overrides: Partial<Project> = {}, progress: ProjectProgress | undefined = writing) {
  const onOpen = vi.fn()
  const onDelete = vi.fn()
  render(
    <ProjectCard
      project={project(overrides)}
      progress={progress}
      onOpen={onOpen}
      onDelete={onDelete}
      alwaysShowDelete
    />,
  )
  return { onOpen, onDelete, card: screen.getByRole('button', { name: /Open project/ }) }
}

describe('ProjectCard', () => {
  it.each([
    ['without a description', ''],
    ['with a description', 'A long synopsis that takes up two lines of the card.'],
  ])('keeps the progress line in the bottom-anchored footer %s', (_label, description) => {
    const { card } = renderCard({ description })

    const footer = within(card).getByTestId('project-card-footer')
    // The footer is pushed to the bottom, so progress lines align across a grid row
    // whether or not a card has a description above them.
    expect(footer).toHaveClass('mt-auto')
    expect(within(footer).getByTestId('project-progress')).toHaveTextContent('chapters written')
  })

  it('names the delete button and does not open the project when it is activated by keyboard', () => {
    const { onOpen, onDelete } = renderCard()

    const remove = screen.getByRole('button', { name: 'Delete project' })
    fireEvent.keyDown(remove, { key: 'Enter' })
    fireEvent.click(remove)

    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('opens the project with Enter on the card itself', () => {
    const { onOpen, card } = renderCard()

    fireEvent.keyDown(card, { key: 'Enter' })

    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
