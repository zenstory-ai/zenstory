import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SkillResourcesSection } from '../SkillResourcesSection'
import { skillsApi } from '../../../lib/api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../../../lib/api', () => ({
  skillsApi: {
    listResources: vi.fn(),
    getResourceContent: vi.fn(),
    upsertResource: vi.fn(),
    deleteResource: vi.fn(),
  },
}))

describe('SkillResourcesSection request freshness', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(skillsApi.listResources).mockResolvedValue({
      resources: [
        { path: 'references/a.md', size: 1 },
        { path: 'references/b.md', size: 1 },
      ],
    })
  })

  it('ignores stale resource content when a newer selection resolves first', async () => {
    let resolveA!: (value: { path: string; content: string }) => void
    let resolveB!: (value: { path: string; content: string }) => void
    vi.mocked(skillsApi.getResourceContent).mockImplementation((_skillId, path) =>
      new Promise((resolve) => {
        if (path === 'references/a.md') resolveA = resolve
        else resolveB = resolve
      }),
    )
    const user = userEvent.setup()
    render(<SkillResourcesSection skillId="skill-1" />)

    await user.click(await screen.findByText('references/a.md'))
    await user.click(screen.getByText('references/b.md'))
    resolveB({ path: 'references/b.md', content: 'newest B content' })
    expect(await screen.findByDisplayValue('newest B content')).toBeInTheDocument()

    resolveA({ path: 'references/a.md', content: 'stale A content' })
    await waitFor(() => expect(screen.queryByDisplayValue('stale A content')).not.toBeInTheDocument())
    expect(screen.getByDisplayValue('newest B content')).toBeInTheDocument()
  })

  it('shows load error with retry instead of claiming there are no resource files', async () => {
    vi.mocked(skillsApi.listResources)
      .mockRejectedValueOnce(new Error('Gateway timeout'))
      .mockResolvedValueOnce({ resources: [{ path: 'references/recovered.md', size: 12 }] })

    const user = userEvent.setup()
    render(<SkillResourcesSection skillId="skill-1" readOnly />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Gateway timeout')
    expect(screen.queryByText('resources.empty')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'common:retry' }))
    expect(await screen.findByText('references/recovered.md')).toBeInTheDocument()
  })

  it.each(['examples/opening.md', 'templates/chapter.txt'])(
    'allows creating resources under %s',
    async (resourcePath) => {
      vi.mocked(skillsApi.upsertResource).mockResolvedValue({
        path: resourcePath,
        size: 7,
        updated_at: 'now',
      })
      const user = userEvent.setup()
      render(<SkillResourcesSection skillId="skill-1" />)

      await user.click(await screen.findByRole('button', { name: 'resources.add' }))
      const pathInput = screen.getByRole('textbox', { name: 'resources.path' })
      await user.clear(pathInput)
      await user.type(pathInput, resourcePath)
      await user.type(screen.getByRole('textbox', { name: 'resources.content' }), 'content')
      await user.click(screen.getByRole('button', { name: 'resources.saveFile' }))

      await waitFor(() =>
        expect(skillsApi.upsertResource).toHaveBeenCalledWith('skill-1', resourcePath, 'content'),
      )
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    },
  )

  it('rejects disallowed resource prefixes such as scripts/', async () => {
    const user = userEvent.setup()
    render(<SkillResourcesSection skillId="skill-1" />)

    await user.click(await screen.findByRole('button', { name: 'resources.add' }))
    const pathInput = screen.getByRole('textbox', { name: 'resources.path' })
    await user.clear(pathInput)
    await user.type(pathInput, 'scripts/run.md')
    await user.type(screen.getByRole('textbox', { name: 'resources.content' }), 'nope')
    await user.click(screen.getByRole('button', { name: 'resources.saveFile' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('resources.errors.prefix')
    expect(skillsApi.upsertResource).not.toHaveBeenCalled()
  })
})
