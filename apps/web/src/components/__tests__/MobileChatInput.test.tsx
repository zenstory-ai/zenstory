import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MobileChatInput } from '../MobileChatInput'

const startRecording = vi.fn()
const stopRecording = vi.fn()
const cancelRecording = vi.fn()
const removeMaterial = vi.fn()
const removeQuote = vi.fn()

let currentVoiceState: {
  status: 'idle' | 'requesting' | 'recording' | 'processing' | 'error'
  isRecording: boolean
  isProcessing: boolean
  duration: number
  volume: number
  error: string | null
  startRecording: typeof startRecording
  stopRecording: typeof stopRecording
  cancelRecording: typeof cancelRecording
  isSupported: boolean
} = {
  status: 'idle',
  isRecording: false,
  isProcessing: false,
  duration: 0,
  volume: 0.3,
  error: null,
  startRecording,
  stopRecording,
  cancelRecording,
  isSupported: true,
}

let lastVoiceOptions:
  | {
      onResult: (text: string) => void
      onError: (error: string) => void
      maxDuration: number
    }
  | undefined

const { mockUseMaterialAttachment, mockUseTextQuote } = vi.hoisted(() => ({
  mockUseMaterialAttachment: vi.fn(),
  mockUseTextQuote: vi.fn(),
}))

vi.mock('../../hooks/useVoiceInput', () => ({
  useVoiceInput: (options: typeof lastVoiceOptions) => {
    lastVoiceOptions = options
    return currentVoiceState
  },
}))

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: mockUseMaterialAttachment,
}))

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: mockUseTextQuote,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          'chat:input.placeholder': 'Type a message',
          'chat:input.attachedMaterials': 'Attached materials',
          'chat:input.quotedText': 'Quoted text',
          'chat:input.remove': 'Remove',
          'common:cancel': 'Cancel',
          'common:send': 'Send',
          'chat:voice.mobile_hold': 'Hold to talk',
          'chat:voice.mobile_stop': 'Release to stop',
          'chat:voice.unavailable': 'Voice unavailable',
          'chat:voice.recognizing': 'Recognizing',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

describe('MobileChatInput', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    lastVoiceOptions = undefined
    currentVoiceState = {
      status: 'idle',
      isRecording: false,
      isProcessing: false,
      duration: 0,
      volume: 0.3,
      error: null,
      startRecording,
      stopRecording,
      cancelRecording,
      isSupported: true,
    }
    mockUseMaterialAttachment.mockReturnValue({
      attachedMaterials: [],
      removeMaterial,
    })
    mockUseTextQuote.mockReturnValue({
      quotes: [],
      removeQuote,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('submits trimmed text and clears the draft', () => {
    const onSend = vi.fn()
    render(<MobileChatInput onSend={onSend} />)

    const textarea = screen.getByPlaceholderText('Type a message')
    fireEvent.change(textarea, { target: { value: '  hello mobile  ' } })
    fireEvent.click(screen.getByTestId('mobile-send-button'))

    expect(onSend).toHaveBeenCalledWith('hello mobile')
    expect(textarea).toHaveValue('')
  })

  it('appends recognized voice text into the textarea', () => {
    render(<MobileChatInput onSend={vi.fn()} />)

    act(() => {
      lastVoiceOptions?.onResult('voice result')
      vi.runOnlyPendingTimers()
    })

    expect(screen.getByTestId('mobile-chat-input')).toHaveValue('voice result')
  })

  it('starts and stops recording on long press', async () => {
    currentVoiceState = {
      ...currentVoiceState,
      status: 'recording',
      isRecording: true,
    }

    render(<MobileChatInput onSend={vi.fn()} />)

    const voiceButton = screen.getByTestId('mobile-voice-input-button')
    fireEvent.touchStart(voiceButton)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })

    fireEvent.touchEnd(voiceButton)

    expect(startRecording).toHaveBeenCalledTimes(1)
    expect(stopRecording).toHaveBeenCalledTimes(1)
  })

  it.each(['touchEnd', 'touchCancel'] as const)('cancels pending microphone permission on %s', async (event) => {
    const { rerender } = render(<MobileChatInput onSend={vi.fn()} />)
    const voiceButton = screen.getByTestId('mobile-voice-input-button')
    fireEvent.touchStart(voiceButton)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(startRecording).toHaveBeenCalledTimes(1)
    currentVoiceState = { ...currentVoiceState, status: 'requesting' }
    rerender(<MobileChatInput onSend={vi.fn()} />)
    fireEvent[event](voiceButton)
    expect(cancelRecording).toHaveBeenCalledTimes(1)
    expect(stopRecording).not.toHaveBeenCalled()
  })

  it.each(['requesting', 'recording', 'processing'] as const)('cancels active %s when disabled', (status) => {
    currentVoiceState = { ...currentVoiceState, status, isRecording: status === 'recording', isProcessing: status === 'processing' }
    const { rerender } = render(<MobileChatInput onSend={vi.fn()} />)
    rerender(<MobileChatInput onSend={vi.fn()} disabled />)
    expect(cancelRecording).toHaveBeenCalledOnce()
  })

  it('clears the hold timer when disabled before recording starts', async () => {
    const { rerender } = render(<MobileChatInput onSend={vi.fn()} />)
    fireEvent.touchStart(screen.getByTestId('mobile-voice-input-button'))
    rerender(<MobileChatInput onSend={vi.fn()} disabled />)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(startRecording).not.toHaveBeenCalled()
  })

  it('cancels recording on touch cancel and supports cancel action when disabled', async () => {
    currentVoiceState = {
      ...currentVoiceState,
      status: 'recording',
      isRecording: true,
    }
    const onCancel = vi.fn()

    const { rerender } = render(<MobileChatInput onSend={vi.fn()} />)
    const voiceButton = screen.getByTestId('mobile-voice-input-button')

    fireEvent.touchStart(voiceButton)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    fireEvent.touchCancel(voiceButton)

    expect(cancelRecording).toHaveBeenCalledTimes(1)

    rerender(<MobileChatInput onSend={vi.fn()} disabled={true} onCancel={onCancel} />)
    fireEvent.click(screen.getByTitle('Cancel'))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('renders attachments and quotes and removes them', () => {
    mockUseMaterialAttachment.mockReturnValue({
      attachedMaterials: [{ id: 'material-1', title: 'Scene notes' }],
      removeMaterial,
    })
    mockUseTextQuote.mockReturnValue({
      quotes: [{ id: 'quote-1', text: 'A dramatic quote that should be truncated in mobile view.' }],
      removeQuote,
    })

    render(<MobileChatInput onSend={vi.fn()} />)

    expect(screen.getByText('Attached materials')).toBeInTheDocument()
    expect(screen.getByText('Scene notes')).toBeInTheDocument()
    expect(screen.getByText('Quoted text')).toBeInTheDocument()

    const removeButtons = screen.getAllByTitle('Remove')
    fireEvent.click(removeButtons[0]!)
    fireEvent.click(removeButtons[1]!)

    expect(removeMaterial).toHaveBeenCalledWith('material-1')
    expect(removeQuote).toHaveBeenCalledWith('quote-1')
  })

  describe('IME composition (中文/日文输入法)', () => {
    it('does not send when Enter keydown carries isComposing (Chrome/Edge 确认候选词)', () => {
      const onSend = vi.fn()
      render(<MobileChatInput onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('Type a message')
      fireEvent.change(textarea, { target: { value: 'ni hao' } })
      fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })

      expect(onSend).not.toHaveBeenCalled()
      expect(textarea).toHaveValue('ni hao')
    })

    it('does not send on Enter between compositionstart and compositionend', () => {
      const onSend = vi.fn()
      render(<MobileChatInput onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('Type a message')
      fireEvent.compositionStart(textarea)
      fireEvent.change(textarea, { target: { value: 'nihao' } })
      fireEvent.keyDown(textarea, { key: 'Enter' })

      expect(onSend).not.toHaveBeenCalled()
    })

    it('does not send when the confirming Enter arrives after compositionend with keyCode 229 (Safari)', () => {
      const onSend = vi.fn()
      render(<MobileChatInput onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('Type a message')
      fireEvent.compositionStart(textarea)
      fireEvent.change(textarea, { target: { value: '你好' } })
      fireEvent.compositionEnd(textarea)
      fireEvent.keyDown(textarea, { key: 'Enter', keyCode: 229 })

      expect(onSend).not.toHaveBeenCalled()
    })

    it('sends normally on Enter after composition has ended', () => {
      const onSend = vi.fn()
      render(<MobileChatInput onSend={onSend} />)

      const textarea = screen.getByPlaceholderText('Type a message')
      fireEvent.compositionStart(textarea)
      fireEvent.change(textarea, { target: { value: '你好' } })
      fireEvent.compositionEnd(textarea)
      fireEvent.keyDown(textarea, { key: 'Enter' })

      expect(onSend).toHaveBeenCalledWith('你好')
    })
  })
})
