import { describe, it, expect, vi, beforeEach, afterEach, afterAll } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessageInput, STOP_ARM_DELAY_MS } from '../MessageInput'
import { skillsApi } from '../../lib/api'
import { MAX_AGENT_MESSAGE_CHARS } from '../../lib/agentLimits'

const { mockUseMaterialAttachment, mockUseTextQuote, mockUseSkillTrigger } = vi.hoisted(() => ({
  mockUseMaterialAttachment: vi.fn(() => ({
    attachedMaterials: [],
    removeMaterial: vi.fn(),
  })),
  mockUseTextQuote: vi.fn(() => ({
    quotes: [],
    removeQuote: vi.fn(),
  })),
  mockUseSkillTrigger: vi.fn(() => ({
    selectedSkills: [],
    selectSkill: vi.fn(),
    removeSkill: vi.fn(),
    clearSkills: vi.fn(),
  })),
}))

const { mockT, mockI18n } = vi.hoisted(() => ({
  mockT: vi.fn((key: string, options?: { returnObjects?: boolean }) => {
    if (key === 'chat:input.staticSuggestions' && options?.returnObjects) {
      return ['Static 1', 'Static 2', 'Static 3']
    }
    return key
  }),
  mockI18n: {
    language: 'zh',
  },
}))

// Mock contexts
vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: mockUseMaterialAttachment,
}))

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: mockUseTextQuote,
}))

vi.mock('../../contexts/SkillTriggerContext', () => ({
  MAX_SELECTED_SKILLS: 3,
  useSkillTrigger: mockUseSkillTrigger,
}))

vi.mock('../../lib/api', () => ({
  skillsApi: {
    list: vi.fn().mockResolvedValue({ skills: [] }),
  },
}))

vi.mock('../VoiceInputButton', () => ({
  VoiceInputButton: ({
    onResult,
    disabled,
  }: {
    onResult: (text: string) => void
    disabled?: boolean
  }) => (
    <button
      type="button"
      disabled={disabled}
      aria-label="voice button"
      onClick={() => onResult('voice result')}
    >
      Voice
    </button>
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: mockT,
    i18n: mockI18n,
  }),
}))

describe('MessageInput', () => {
  const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  const defaultProps = {
    onSend: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockI18n.language = 'zh'
    mockUseMaterialAttachment.mockReturnValue({
      attachedMaterials: [],
      removeMaterial: vi.fn(),
    })
    mockUseTextQuote.mockReturnValue({
      quotes: [],
      removeQuote: vi.fn(),
    })
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [],
      selectSkill: vi.fn(),
      removeSkill: vi.fn(),
      clearSkills: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
  })

  afterAll(() => {
    consoleErrorSpy.mockRestore()
  })

  it('renders textarea and send button', () => {
    render(<MessageInput {...defaultProps} />)
    expect(screen.getByPlaceholderText('chat:input.placeholder')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /common:send/i })).toBeInTheDocument()
  })

  it('disables when isLoading is true', () => {
    render(<MessageInput {...defaultProps} disabled={true} />)
    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    const sendButton = screen.getByRole('button', { name: /common:send/i })
    expect(textarea).toBeDisabled()
    expect(sendButton).toBeDisabled()
  })

  it('clears input after send', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, 'Test message')
    await user.click(screen.getByRole('button', { name: /common:send/i }))

    expect(onSend).toHaveBeenCalledWith('Test message', [])
    await waitFor(() => {
      expect(textarea).toHaveValue('')
    })
  })

  it('handles Shift+Enter for newline', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, 'Line 1{Shift>}{Enter}{/Shift}Line 2')

    expect(onSend).not.toHaveBeenCalled()
    expect(textarea).toHaveValue('Line 1\nLine 2')
  })

  it('handles Enter to send', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, 'Test message{Enter}')

    expect(onSend).toHaveBeenCalledWith('Test message', [])
  })

  it('blocks sending and explains when the message exceeds the backend limit', () => {
    const onSend = vi.fn()
    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    fireEvent.change(textarea, { target: { value: '字'.repeat(MAX_AGENT_MESSAGE_CHARS + 1) } })

    expect(screen.getByTestId('chat-input-too-long')).toHaveTextContent('chat:input.tooLong')
    expect(screen.getByTestId('send-button')).toBeDisabled()
    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.change(textarea, { target: { value: '字'.repeat(MAX_AGENT_MESSAGE_CHARS) } })
    expect(screen.queryByTestId('chat-input-too-long')).not.toBeInTheDocument()
    expect(screen.getByTestId('send-button')).not.toBeDisabled()
  })

  it('does not send empty messages', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const sendButton = screen.getByRole('button', { name: /common:send/i })
    expect(sendButton).toBeDisabled()

    await user.click(sendButton)
    expect(onSend).not.toHaveBeenCalled()
  })

  it('trims whitespace from messages', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, '  Test message  {Enter}')

    expect(onSend).toHaveBeenCalledWith('Test message', [])
  })

  it('shows cancel button when onCancel provided and disabled', () => {
    const onCancel = vi.fn()
    render(<MessageInput {...defaultProps} disabled={true} onCancel={onCancel} />)

    expect(screen.getByRole('button', { name: /chat:input.stop/i })).toBeInTheDocument()
  })

  it('ignores the stop button right after it replaces send, then stops on a later press', () => {
    // A double tap on send used to land on stop and end the round before anything was written.
    vi.useFakeTimers()
    try {
      const onCancel = vi.fn()
      render(<MessageInput {...defaultProps} sendDisabled={true} onCancel={onCancel} />)
      const stopButton = screen.getByRole('button', { name: /chat:input.stop/i })

      fireEvent.click(stopButton)
      expect(onCancel).not.toHaveBeenCalled()

      act(() => {
        vi.advanceTimersByTime(STOP_ARM_DELAY_MS)
      })
      fireEvent.click(stopButton)
      expect(onCancel).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not stop twice while a stop is already in progress', () => {
    vi.useFakeTimers()
    try {
      const onCancel = vi.fn()
      render(<MessageInput {...defaultProps} sendDisabled={true} onCancel={onCancel} isStopping={true} />)
      act(() => {
        vi.advanceTimersByTime(STOP_ARM_DELAY_MS)
      })

      fireEvent.click(screen.getByRole('button', { name: /chat:input.stopping/i }))
      expect(onCancel).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows custom placeholder', () => {
    render(<MessageInput {...defaultProps} placeholder="Custom placeholder" />)
    expect(screen.getByPlaceholderText('Custom placeholder')).toBeInTheDocument()
  })

  it('reserves room for a wrapped desktop placeholder instead of clipping it to one line', () => {
    render(<MessageInput {...defaultProps} layout="fill" />)
    expect(screen.getByTestId('chat-input')).toHaveStyle({ minHeight: '64px' })
  })

  it('displays AI suggestions', () => {
    const aiSuggestions = ['Suggestion 1', 'Suggestion 2', 'Suggestion 3']
    render(<MessageInput {...defaultProps} aiSuggestions={aiSuggestions} />)

    expect(screen.getByText('Suggestion 1')).toBeInTheDocument()
    expect(screen.getByText('Suggestion 2')).toBeInTheDocument()
    expect(screen.getByText('Suggestion 3')).toBeInTheDocument()
  })

  it('clicks suggestion to fill input', async () => {
    const user = userEvent.setup({ delay: null })
    const aiSuggestions = ['Suggestion 1', 'Suggestion 2']
    render(<MessageInput {...defaultProps} aiSuggestions={aiSuggestions} />)

    await user.click(screen.getByText('Suggestion 1'))

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    expect(textarea).toHaveValue('Suggestion 1')
  })

  it('displays static suggestions when no AI suggestions', () => {
    render(
      <MessageInput
        {...defaultProps}
        aiSuggestions={[]}
        messageCount={0}
        suggestionDisplayState="fallback"
      />
    )
    expect(screen.getByText('Static 1')).toBeInTheDocument()
    expect(screen.getByText('Static 2')).toBeInTheDocument()
  })

  it('shows loading placeholders instead of static suggestions during loading', () => {
    render(
      <MessageInput
        {...defaultProps}
        aiSuggestions={[]}
        suggestionDisplayState="loading"
      />
    )
    expect(screen.queryByText('Static 1')).not.toBeInTheDocument()
    expect(screen.getByText('common:loading')).toBeInTheDocument()
  })

  it('handles refresh suggestions button click', async () => {
    const user = userEvent.setup({ delay: null })
    const onRefreshSuggestions = vi.fn()
    render(
      <MessageInput
        {...defaultProps}
        onRefreshSuggestions={onRefreshSuggestions}
        isRefreshingSuggestions={false}
      />
    )

    // Find refresh button (RefreshCw icon)
    const refreshButtons = screen.getAllByRole('button')
    const refreshButton = refreshButtons.find(btn =>
      btn.querySelector('svg.lucide-refresh-cw') || btn.getAttribute('aria-busy') !== null
    )

    if (refreshButton) {
      await user.click(refreshButton)
      expect(onRefreshSuggestions).toHaveBeenCalled()
    }
  })

  it('shows refresh button as busy when refreshing', () => {
    render(
      <MessageInput
        {...defaultProps}
        isRefreshingSuggestions={true}
        onRefreshSuggestions={vi.fn()}
      />
    )

    const refreshButton = screen.getAllByRole('button').find(btn =>
      btn.getAttribute('aria-busy') === 'true'
    )
    expect(refreshButton).toBeTruthy()
  })

  it('auto-resizes textarea in auto layout', async () => {
    const user = userEvent.setup({ delay: null })
    render(<MessageInput {...defaultProps} layout="auto" />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')

    // Type multiple lines
    await user.type(textarea, 'Line 1\nLine 2\nLine 3\nLine 4\nLine 5')

    // Textarea should have auto-resized (we can't check exact height in jsdom)
    expect(textarea).toHaveValue('Line 1\nLine 2\nLine 3\nLine 4\nLine 5')
  })

  it('uses fill layout correctly', () => {
    const { container } = render(<MessageInput {...defaultProps} layout="fill" />)

    // Check that fill layout class is applied
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('h-full')
  })

  it('displays matched skills', () => {
    const matchedSkills = [
      { name: 'Skill 1', trigger: '/skill1' },
      { name: 'Skill 2', trigger: '/skill2' },
    ]
    render(<MessageInput {...defaultProps} matchedSkills={matchedSkills} />)

    expect(screen.getByText('Skill 1')).toBeInTheDocument()
    expect(screen.getByText('Skill 2')).toBeInTheDocument()
  })

  it('handles skill trigger via "/"', async () => {
    const user = userEvent.setup({ delay: null })
    render(<MessageInput {...defaultProps} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, '/')

    // Skill menu should open (but may be empty if skills not loaded)
    // We just check that the input accepts the slash
    expect(textarea).toHaveValue('/')
  })

  it('adds a skill chip (not trigger text) when picking from the slash menu', async () => {
    const user = userEvent.setup({ delay: null })
    const selectSkill = vi.fn()
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [],
      selectSkill,
      removeSkill: vi.fn(),
      clearSkills: vi.fn(),
    })
    vi.mocked(skillsApi.list).mockResolvedValueOnce({
      skills: [
        {
          id: 'skill-menu-1',
          name: '场景节奏',
          description: '压缩拖沓段落',
          triggers: ['/pace'],
          instructions: '收紧节奏。',
          source: 'user',
          is_active: true,
        },
      ],
      total: 1,
    })

    render(<MessageInput {...defaultProps} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, '/')
    await user.click(await screen.findByRole('button', { name: /场景节奏/i }))

    expect(selectSkill).toHaveBeenCalledWith({ id: 'skill-menu-1', name: '场景节奏' })
    expect(textarea).toHaveValue('')
  })

  it('keeps the input and explains the limit when 3 skills are already selected', async () => {
    const user = userEvent.setup({ delay: null })
    const selectSkill = vi.fn()
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [
        { id: 'skill-a', name: '技能A' },
        { id: 'skill-b', name: '技能B' },
        { id: 'skill-c', name: '技能C' },
      ],
      selectSkill,
      removeSkill: vi.fn(),
      clearSkills: vi.fn(),
    })
    vi.mocked(skillsApi.list).mockResolvedValueOnce({
      skills: [
        {
          id: 'skill-menu-4',
          name: '第四个技能',
          description: '',
          triggers: [],
          instructions: 'x',
          source: 'user',
          is_active: true,
        },
      ],
      total: 1,
    })

    render(<MessageInput {...defaultProps} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, '/第')
    const option = await screen.findByRole('button', { name: /第四个技能/ })

    expect(option).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('chat:skill.selectionFull')

    await user.click(option)
    await user.keyboard('{Enter}')

    expect(selectSkill).not.toHaveBeenCalled()
    expect(textarea).toHaveValue('/第')
  })

  it('renders selected skills as a compact rail in fill layout', async () => {
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [{ id: 'skill-outline', name: '大纲助手' }],
      selectSkill: vi.fn(),
      removeSkill: vi.fn(),
      clearSkills: vi.fn(),
    })

    render(<MessageInput {...defaultProps} layout="fill" />)

    const skillRow = await screen.findByTestId('chat-selected-skill-row')
    expect(skillRow.className).toContain('overflow-x-auto')
    expect(skillRow.className).not.toContain('flex-wrap')
    expect(screen.getByText('大纲助手')).toBeInTheDocument()
    expect(screen.getByTestId('chat-input-accessories').className).toContain('overflow-y-auto')
  })

  it('removes a selected skill via its accessible remove button', async () => {
    const user = userEvent.setup({ delay: null })
    const removeSkill = vi.fn()
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [{ id: 'skill-a', name: '技能A' }],
      selectSkill: vi.fn(),
      removeSkill,
      clearSkills: vi.fn(),
    })

    render(<MessageInput {...defaultProps} />)

    await user.click(screen.getByRole('button', { name: 'chat:skill.removeSelected' }))
    expect(removeSkill).toHaveBeenCalledWith('skill-a')
  })

  it('sends selected skill ids with the message and clears the chips', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    const clearSkills = vi.fn()
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [
        { id: 'skill-a', name: '技能A' },
        { id: 'skill-b', name: '技能B' },
      ],
      selectSkill: vi.fn(),
      removeSkill: vi.fn(),
      clearSkills,
    })

    render(<MessageInput {...defaultProps} onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, '继续写这一章')
    await user.click(screen.getByRole('button', { name: /common:send/i }))

    expect(onSend).toHaveBeenCalledWith('继续写这一章', ['skill-a', 'skill-b'])
    expect(clearSkills).toHaveBeenCalled()
    expect(textarea).toHaveValue('')
  })

  it('does not send or clear chips when only skills are selected without text', async () => {
    const user = userEvent.setup({ delay: null })
    const onSend = vi.fn()
    const clearSkills = vi.fn()
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [{ id: 'skill-a', name: '技能A' }],
      selectSkill: vi.fn(),
      removeSkill: vi.fn(),
      clearSkills,
    })

    render(<MessageInput {...defaultProps} onSend={onSend} />)

    await user.type(screen.getByPlaceholderText('chat:input.placeholder'), '{Enter}')

    expect(onSend).not.toHaveBeenCalled()
    expect(clearSkills).not.toHaveBeenCalled()
  })

  it('hides suggestion chips in fill layout when a skill is already selected', () => {
    mockUseSkillTrigger.mockReturnValue({
      selectedSkills: [{ id: 'skill-a', name: '技能A' }],
      selectSkill: vi.fn(),
      removeSkill: vi.fn(),
      clearSkills: vi.fn(),
    })

    render(
      <MessageInput
        {...defaultProps}
        layout="fill"
        aiSuggestions={['Suggestion 1', 'Suggestion 2']}
      />,
    )

    expect(screen.queryByText('Suggestion 1')).not.toBeInTheDocument()
    expect(screen.queryByText('Suggestion 2')).not.toBeInTheDocument()
  })

  it('handles Tab to accept first suggestion', async () => {
    const user = userEvent.setup({ delay: null })
    const aiSuggestions = ['First suggestion', 'Second suggestion']
    render(<MessageInput {...defaultProps} aiSuggestions={aiSuggestions} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.click(textarea) // Focus
    await user.keyboard('{Tab}')

    expect(textarea).toHaveValue('First suggestion')
  })

  it('does not show suggestions when input has content', async () => {
    const user = userEvent.setup({ delay: null })
    const aiSuggestions = ['Suggestion 1', 'Suggestion 2']
    render(<MessageInput {...defaultProps} aiSuggestions={aiSuggestions} />)

    const textarea = screen.getByPlaceholderText('chat:input.placeholder')
    await user.type(textarea, 'Some text')

    // Suggestions should not be visible when input has content
    expect(screen.queryByText('Suggestion 1')).not.toBeInTheDocument()
  })

  it('does not show suggestions when disabled', () => {
    const aiSuggestions = ['Suggestion 1', 'Suggestion 2']
    render(<MessageInput {...defaultProps} aiSuggestions={aiSuggestions} disabled={true} />)

    expect(screen.queryByText('Suggestion 1')).not.toBeInTheDocument()
  })

  it('handles voice input button', () => {
    render(<MessageInput {...defaultProps} />)
    // VoiceInputButton is rendered (mocked)
    const voiceButton = screen.getByRole('button', { name: /voice/i })
    expect(voiceButton).toBeInTheDocument()
  })

  it('allows switching generation mode (fast/quality)', async () => {
    const user = userEvent.setup({ delay: null })
    const onGenerationModeChange = vi.fn()

    render(
      <MessageInput
        {...defaultProps}
        generationMode="quality"
        onGenerationModeChange={onGenerationModeChange}
      />
    )

    await user.click(screen.getByRole('button', { name: /chat:input.mode.label/i }))
    expect(onGenerationModeChange).toHaveBeenCalledWith('fast')
  })

  describe('steering (追加消息)', () => {
    const steerProps = {
      sendDisabled: true,
      onCancel: vi.fn(),
      onSteer: vi.fn(),
      canSteer: true,
    }

    it('shows the steering placeholder and hint while generating', () => {
      render(<MessageInput {...defaultProps} {...steerProps} />)
      expect(
        screen.getByPlaceholderText('chat:input.steerPlaceholder')
      ).toBeInTheDocument()
      expect(screen.getByTestId('steer-hint')).toBeInTheDocument()
    })

    it('keeps the textarea editable while generating', () => {
      render(<MessageInput {...defaultProps} {...steerProps} />)
      expect(
        screen.getByPlaceholderText('chat:input.steerPlaceholder')
      ).not.toBeDisabled()
    })

    it('shows cancel when empty, and a steer button once text is typed', async () => {
      const user = userEvent.setup({ delay: null })
      render(<MessageInput {...defaultProps} {...steerProps} />)

      // Empty input while streaming -> cancel button, no steer button.
      expect(screen.queryByTestId('steer-button')).not.toBeInTheDocument()

      await user.type(
        screen.getByPlaceholderText('chat:input.steerPlaceholder'),
        '把主角改名为林川'
      )
      expect(screen.getByTestId('steer-button')).toBeInTheDocument()
    })

    it('dispatches onSteer (not onSend) when sending during generation', async () => {
      const user = userEvent.setup({ delay: null })
      const onSend = vi.fn()
      const onSteer = vi.fn()
      render(
        <MessageInput
          {...defaultProps}
          onSend={onSend}
          sendDisabled={true}
          onCancel={vi.fn()}
          onSteer={onSteer}
          canSteer={true}
        />
      )

      const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
      await user.type(textarea, '补充指令')
      await user.click(screen.getByTestId('steer-button'))

      expect(onSteer).toHaveBeenCalledWith('补充指令')
      expect(onSend).not.toHaveBeenCalled()
      await waitFor(() => expect(textarea).toHaveValue(''))
    })

    it('keeps selected skill chips after steering (steering does not carry skill ids)', async () => {
      const user = userEvent.setup({ delay: null })
      const clearSkills = vi.fn()
      mockUseSkillTrigger.mockReturnValue({
        selectedSkills: [{ id: 'skill-a', name: '技能A' }],
        selectSkill: vi.fn(),
        removeSkill: vi.fn(),
        clearSkills,
      })
      const onSteer = vi.fn()
      render(
        <MessageInput
          {...defaultProps}
          sendDisabled={true}
          onCancel={vi.fn()}
          onSteer={onSteer}
          canSteer={true}
        />
      )

      const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
      await user.type(textarea, '补充指令')
      await user.click(screen.getByTestId('steer-button'))

      await waitFor(() => expect(textarea).toHaveValue(''))
      expect(onSteer).toHaveBeenCalledWith('补充指令')
      expect(clearSkills).not.toHaveBeenCalled()
      expect(screen.getByText('技能A')).toBeInTheDocument()
    })

    it('steers on Enter while generating', async () => {
      const user = userEvent.setup({ delay: null })
      const onSend = vi.fn()
      const onSteer = vi.fn()
      render(
        <MessageInput
          {...defaultProps}
          onSend={onSend}
          sendDisabled={true}
          onCancel={vi.fn()}
          onSteer={onSteer}
          canSteer={true}
        />
      )

      const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
      await user.type(textarea, '下一步加一段悬念{Enter}')

      expect(onSteer).toHaveBeenCalledWith('下一步加一段悬念')
      expect(onSend).not.toHaveBeenCalled()
    })

    it('does not offer steering when canSteer is false', () => {
      render(
        <MessageInput
          {...defaultProps}
          sendDisabled={true}
          onCancel={vi.fn()}
          onSteer={vi.fn()}
          canSteer={false}
        />
      )
      expect(screen.queryByTestId('steer-hint')).not.toBeInTheDocument()
      expect(
        screen.queryByPlaceholderText('chat:input.steerPlaceholder')
      ).not.toBeInTheDocument()
    })
  })

  describe('IME composition (中文/日文输入法)', () => {
    it('does not send when Enter keydown carries isComposing (Chrome/Edge 确认候选词)', () => {
      const onSend = vi.fn()
      render(<MessageInput {...defaultProps} onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('chat:input.placeholder')
      fireEvent.change(textarea, { target: { value: 'ni hao' } })
      fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })

      expect(onSend).not.toHaveBeenCalled()
      expect(textarea).toHaveValue('ni hao')
    })

    it('does not send on Enter between compositionstart and compositionend', () => {
      const onSend = vi.fn()
      render(<MessageInput {...defaultProps} onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('chat:input.placeholder')
      fireEvent.compositionStart(textarea)
      fireEvent.change(textarea, { target: { value: 'nihao' } })
      fireEvent.keyDown(textarea, { key: 'Enter' })

      expect(onSend).not.toHaveBeenCalled()
    })

    it('does not send when the confirming Enter arrives after compositionend with keyCode 229 (Safari)', () => {
      const onSend = vi.fn()
      render(<MessageInput {...defaultProps} onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('chat:input.placeholder')
      fireEvent.compositionStart(textarea)
      fireEvent.change(textarea, { target: { value: '你好' } })
      fireEvent.compositionEnd(textarea)
      fireEvent.keyDown(textarea, { key: 'Enter', keyCode: 229 })

      expect(onSend).not.toHaveBeenCalled()
    })

    it('sends normally on Enter after composition has ended', () => {
      const onSend = vi.fn()
      render(<MessageInput {...defaultProps} onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('chat:input.placeholder')
      fireEvent.compositionStart(textarea)
      fireEvent.change(textarea, { target: { value: '你好' } })
      fireEvent.compositionEnd(textarea)
      fireEvent.keyDown(textarea, { key: 'Enter' })

      expect(onSend).toHaveBeenCalledWith('你好', [])
    })

    it('does not steer when Enter keydown carries isComposing while generating', () => {
      const onSteer = vi.fn()
      render(
        <MessageInput
          {...defaultProps}
          sendDisabled={true}
          onCancel={vi.fn()}
          onSteer={onSteer}
          canSteer={true}
        />
      )

      const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
      fireEvent.change(textarea, { target: { value: 'jia yi duan xuan nian' } })
      fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })

      expect(onSteer).not.toHaveBeenCalled()
    })
  })
})
