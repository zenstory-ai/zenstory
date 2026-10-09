import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'

const { listMock, createMock, apiBaseMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  createMock: vi.fn(),
  apiBaseMock: vi.fn(() => 'https://api.zenstory.ai'),
}))

const mediaState = vi.hoisted(() => ({ isMobile: false }))

vi.mock('../../../hooks/useMediaQuery', () => ({
  useIsMobile: () => mediaState.isMobile,
}))

vi.mock('../../../lib/apiClient', () => ({
  getApiBase: apiBaseMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : key),
    i18n: { language: 'zh' },
  }),
}))

vi.mock('../../../lib/api', () => ({
  agentApiKeysApi: {
    list: listMock,
    create: createMock,
    update: vi.fn(),
    delete: vi.fn(),
    regenerate: vi.fn(),
  },
}))

import { AgentApiKeysPanel } from '../AgentApiKeysPanel'

const renderPanel = (ui: ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const makeKey = () => ({
  id: 'key-1',
  name: 'My key',
  key_prefix: 'eg_abcd',
  scopes: ['read'],
  is_active: true,
  request_count: 0,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
})

describe('AgentApiKeysPanel', () => {
  beforeEach(() => {
    listMock.mockReset()
    createMock.mockReset()
    apiBaseMock.mockReset()
    apiBaseMock.mockReturnValue('https://api.zenstory.ai')
    mediaState.isMobile = false
  })

  it('does not submit when all permission scopes are deselected', async () => {
    listMock.mockResolvedValue({ keys: [] })
    renderPanel(<AgentApiKeysPanel />)
    fireEvent.click(await screen.findByText('apiKeys.create'))
    const name = screen.getByPlaceholderText('apiKeys.form.namePlaceholder')
    fireEvent.change(name, { target: { value: 'No permissions' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'settings:apiKeys.permissions.read' }))
    expect(screen.getByRole('button', { name: 'apiKeys.create' })).toBeDisabled()
    fireEvent.submit(name.closest('form')!)
    expect(createMock).not.toHaveBeenCalled()
  })

  it('uses the same 40px primary button for the form submit as for the button that opened it', async () => {
    listMock.mockResolvedValue({ keys: [] })
    renderPanel(<AgentApiKeysPanel />)
    const entry = await screen.findByRole('button', { name: 'apiKeys.create' })
    expect(entry.className).toContain('min-h-[40px]')
    fireEvent.click(entry)

    const submit = screen.getByRole('button', { name: 'apiKeys.create' })
    expect(submit).toHaveAttribute('type', 'submit')
    expect(submit.className).toContain('min-h-[40px]')
    expect(submit.className).toContain('bg-[hsl(var(--accent-primary))]')
    expect(submit.className).not.toContain('text-xs')

    const cancel = screen.getByRole('button', { name: 'common.cancel' })
    expect(cancel.className).toContain('min-h-[40px]')
    expect(cancel.className).not.toContain('bg-[hsl(var(--accent-primary))]')
  })

  it('grows the form buttons to the 44px touch size on phones', async () => {
    mediaState.isMobile = true
    listMock.mockResolvedValue({ keys: [] })
    renderPanel(<AgentApiKeysPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'apiKeys.create' }))
    expect(screen.getByRole('button', { name: 'apiKeys.create' }).className).toContain('min-h-[44px]')
    expect(screen.getByRole('button', { name: 'common.cancel' }).className).toContain('min-h-[44px]')
  })

  it('names every key action button for screen readers', async () => {
    listMock.mockResolvedValue({ keys: [makeKey()] })
    renderPanel(<AgentApiKeysPanel />)
    expect(await screen.findByRole('button', { name: 'apiKeys.disable' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'apiKeys.regenerate' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'apiKeys.delete' })).toBeInTheDocument()
  })

  it('shows list errors and supports retry instead of claiming there are no keys', async () => {
    listMock.mockRejectedValueOnce(new Error('Key list unavailable')).mockResolvedValue({ keys: [makeKey()] })
    renderPanel(<AgentApiKeysPanel />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Key list unavailable')
    expect(screen.queryByText('apiKeys.noKeys')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('My key')).toBeInTheDocument()
  })

  it('surfaces failed creation while preserving the entered form', async () => {
    listMock.mockResolvedValue({ keys: [] })
    createMock.mockRejectedValue(new Error('Key creation failed'))
    renderPanel(<AgentApiKeysPanel />)
    fireEvent.click(await screen.findByText('apiKeys.create'))
    const name = screen.getByPlaceholderText('apiKeys.form.namePlaceholder')
    fireEvent.change(name, { target: { value: 'Retryable form' } })
    fireEvent.submit(name.closest('form')!)
    expect(await screen.findByRole('alert')).toHaveTextContent('Key creation failed')
    expect(name).toHaveValue('Retryable form')
  })

  it('does not offer a misleading credential check for an expired stored key', async () => {
    listMock.mockResolvedValue({ keys: [{ ...makeKey(), expires_at: '2025-01-01T00:00:00Z' }] })
    renderPanel(<AgentApiKeysPanel />)
    await screen.findByText('My key')
    expect(screen.queryByTitle('apiKeys.testConnection')).not.toBeInTheDocument()
    expect(screen.queryByText('apiKeys.testSuccess')).not.toBeInTheDocument()
  })

  it('retains the one-time key through Escape and backdrop clicks until explicit Done', async () => {
    listMock.mockResolvedValue({keys:[]})
    createMock.mockResolvedValue({key:'once-only-key'})
    renderPanel(<AgentApiKeysPanel />)
    fireEvent.click(await screen.findByText('apiKeys.create'))
    const name=screen.getByPlaceholderText('apiKeys.form.namePlaceholder')
    fireEvent.change(name,{target:{value:'New key'}})
    fireEvent.submit(name.closest('form')!)
    const dialog=await screen.findByRole('dialog',{name:'apiKeys.createdTitle'})
    fireEvent.keyDown(document,{key:'Escape'})
    expect(dialog).toBeInTheDocument()
    fireEvent.click(dialog.parentElement!)
    expect(screen.getByText('once-only-key')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'apiKeys.done' }))
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
  })

  it('shows the CLI connect guide when the user has no keys' , async () => {
    listMock.mockResolvedValue({ keys: [] })
    renderPanel(<AgentApiKeysPanel />)

    expect(await screen.findByText('apiKeys.connectGuide.title')).toBeInTheDocument()
    expect(screen.getByText('npx zenstory login')).toBeInTheDocument()
    expect(screen.getByText('apiKeys.connectGuide.step2Hint')).toBeInTheDocument()
    expect(screen.getByText('npx zenstory skill install')).toBeInTheDocument()
    expect(screen.getByText('apiKeys.noKeys')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/--key|<[^>]*key[^>]*>/i)
    expect(screen.getByRole('link', { name: 'https://api.zenstory.ai/skill.md' })).toHaveAttribute(
      'href',
      'https://api.zenstory.ai/skill.md',
    )
  })

  it('points self-hosted deployments at their own API base', async () => {
    apiBaseMock.mockReturnValue('https://zenstory.example.com/')
    listMock.mockResolvedValue({ keys: [] })
    renderPanel(<AgentApiKeysPanel />)

    expect(
      await screen.findByText('npx zenstory login --api-base https://zenstory.example.com/api/v1'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://zenstory.example.com/skill.md' })).toHaveAttribute(
      'href',
      'https://zenstory.example.com/skill.md',
    )
  })

  it('announces copies to screen readers and hides decorative step numbers', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    listMock.mockResolvedValue({ keys: [makeKey()] })
    renderPanel(<AgentApiKeysPanel />)

    expect(await screen.findByRole('button', { name: 'apiKeys.copyPrefix' })).toBeInTheDocument()
    const guide = screen.getByRole('region', { name: 'apiKeys.connectGuide.title' })
    guide.querySelectorAll('ol > li > span:first-child').forEach((span) => {
      expect(span).toHaveAttribute('aria-hidden', 'true')
    })

    fireEvent.click(screen.getAllByRole('button', { name: 'apiKeys.copy' })[0])
    await waitFor(() => {
      expect(screen.getAllByRole('status').some((el) => el.textContent === 'apiKeys.copied')).toBe(true)
    })
    expect(writeText).toHaveBeenCalledWith('npx zenstory login')
  })

  it('gives every copy button a named 44px touch target on phones without changing desktop', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    listMock.mockResolvedValue({ keys: [makeKey()] })
    createMock.mockResolvedValue({ key: 'eg_secret_full_key' })
    renderPanel(<AgentApiKeysPanel />)

    const expectTouchTarget = (button: HTMLElement) => {
      // Same phone size as the row's IconButtons (h-11 w-11 below 768px); desktop keeps p-1.
      expect(button.className).toContain('max-md:h-11')
      expect(button.className).toContain('max-md:w-11')
      expect(button).toHaveAttribute('type', 'button')
    }

    const commandCopies = await screen.findAllByRole('button', { name: 'apiKeys.copy' })
    expect(commandCopies).toHaveLength(2)
    commandCopies.forEach(expectTouchTarget)
    expectTouchTarget(screen.getByRole('button', { name: 'apiKeys.copyPrefix' }))

    fireEvent.click(screen.getByRole('button', { name: 'apiKeys.create' }))
    fireEvent.change(screen.getByPlaceholderText('apiKeys.form.namePlaceholder'), { target: { value: 'CLI' } })
    fireEvent.submit(screen.getByPlaceholderText('apiKeys.form.namePlaceholder').closest('form')!)
    expectTouchTarget(await screen.findByRole('button', { name: 'apiKeys.copyKey' }))
  })

  it('keeps the connect guide above the key list', async () => {
    listMock.mockResolvedValue({ keys: [makeKey()] })
    renderPanel(<AgentApiKeysPanel />)

    expect(await screen.findByText('My key')).toBeInTheDocument()
    expect(screen.getByText('apiKeys.connectGuide.title')).toBeInTheDocument()
  })

  it('gives terminal commands instead of a chat prompt after creating a key', async () => {
    listMock.mockResolvedValueOnce({ keys: [] }).mockResolvedValue({ keys: [makeKey()] })
    createMock.mockResolvedValue({ key: 'eg_secret123', api_key: makeKey() })
    renderPanel(<AgentApiKeysPanel />)

    fireEvent.click(await screen.findByText('apiKeys.create'))
    fireEvent.change(screen.getByPlaceholderText('apiKeys.form.namePlaceholder'), {
      target: { value: 'CLI' },
    })
    fireEvent.submit(screen.getByPlaceholderText('apiKeys.form.namePlaceholder').closest('form')!)

    const dialog = await screen.findByRole('dialog', { name: 'apiKeys.createdTitle' })
    await waitFor(() => {
      expect(dialog).toHaveTextContent('npx zenstory login')
    })
    // The key is shown once (with its own copy button) but never embedded in a shell command.
    expect(dialog).toHaveTextContent('eg_secret123')
    expect(dialog).not.toHaveTextContent('--key')
    expect(dialog).toHaveTextContent('apiKeys.connectGuide.step2Hint')
    expect(dialog).toHaveTextContent('npx zenstory skill install')
    expect(dialog).not.toHaveTextContent('X-Agent-API-Key')
    expect(screen.getByRole('button', { name: 'apiKeys.copyKey' })).toBeInTheDocument()
  })
})
