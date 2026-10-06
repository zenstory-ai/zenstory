import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VoiceInputButton } from '../VoiceInputButton'

let isMobile = false
const startRecording = vi.fn()
const stopRecording = vi.fn()
const cancelRecording = vi.fn()
const toastInfo = vi.fn()
const toastError = vi.fn()
const loggerError = vi.fn()

let voiceState: {
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
}

let lastOptions:
  | {
      onResult: (text: string) => void
      onError: (message: string) => void
      maxDuration: number
    }
  | undefined

vi.mock('../../hooks/useVoiceInput', () => ({
  useVoiceInput: (options: typeof lastOptions) => {
    lastOptions = options
    return voiceState
  },
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => isMobile,
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    info: (message: string) => toastInfo(message),
    error: (message: string) => toastError(message),
  },
}))

vi.mock('../../lib/logger', () => ({
  logger: {
    error: (...args: unknown[]) => loggerError(...args),
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          'chat:voice.cancelled': 'Recording cancelled',
          'chat:voice.recognizing': 'Recognizing',
          'chat:voice.desktop_stop': 'Stop recording',
          'chat:voice.mobile_stop': 'Release to stop',
          'chat:voice.mobile_hold': 'Hold to record',
          'chat:voice.input': 'Voice input',
          'chat:voice.unavailable': 'Voice unavailable',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

describe('VoiceInputButton', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    lastOptions = undefined
    isMobile = false
    voiceState = {
      status: 'idle',
      isRecording: false,
      isProcessing: false,
      duration: 0,
      volume: 0.4,
      error: null,
      startRecording,
      stopRecording,
      cancelRecording,
      isSupported: true,
    }
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not render when voice input is unsupported', () => {
    voiceState = { ...voiceState, isSupported: false }

    const { container } = render(<VoiceInputButton onResult={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('starts and stops recording on desktop interactions', async () => {
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)

    fireEvent.click(screen.getByTestId('voice-input-button'))
    expect(startRecording).toHaveBeenCalledTimes(1)

    voiceState = { ...voiceState, status: 'recording', isRecording: true, duration: 7 }
    rerender(<VoiceInputButton onResult={vi.fn()} />)

    fireEvent.click(screen.getByTestId('voice-input-button'))
    expect(stopRecording).toHaveBeenCalledTimes(1)

    fireEvent.contextMenu(screen.getByTestId('voice-input-button'))
    expect(cancelRecording).toHaveBeenCalledTimes(1)
    expect(toastInfo).toHaveBeenCalledWith('Recording cancelled')
  })

  it('handles mobile long press and touch cancel flows', async () => {
    isMobile = true
    voiceState = { ...voiceState, status: 'recording', isRecording: true, duration: 5 }

    render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')

    fireEvent.touchStart(button)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    fireEvent.touchEnd(button)

    expect(startRecording).toHaveBeenCalledTimes(1)
    expect(stopRecording).toHaveBeenCalledTimes(1)

    fireEvent.touchStart(button)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    fireEvent.touchCancel(button)

    expect(cancelRecording).toHaveBeenCalledTimes(1)
    expect(toastInfo).toHaveBeenCalledWith('Recording cancelled')
  })

  it('cancels pending mobile permission when touch ends after the hold threshold', async () => {
    isMobile = true
    startRecording.mockReturnValueOnce(new Promise<void>(() => {}))

    render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')
    fireEvent.touchStart(button)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    fireEvent.touchEnd(button)

    expect(startRecording).toHaveBeenCalledOnce()
    expect(cancelRecording).toHaveBeenCalledOnce()
    expect(stopRecording).not.toHaveBeenCalled()
  })

  it('surfaces hook errors through logger and toast', () => {
    render(<VoiceInputButton onResult={vi.fn()} />)

    act(() => {
      lastOptions?.onError('Microphone blocked')
    })

    expect(loggerError).toHaveBeenCalledWith('Voice input error:', 'Microphone blocked')
    expect(toastError).toHaveBeenCalledWith('Microphone blocked')
  })

  it.each(['click', 'contextMenu'] as const)('cancels pending desktop permission on %s rather than requesting again', (event) => {
    voiceState = { ...voiceState, status: 'requesting' }
    render(<VoiceInputButton onResult={vi.fn()} />)
    fireEvent[event](screen.getByTestId('voice-input-button'))
    expect(cancelRecording).toHaveBeenCalledOnce()
    expect(startRecording).not.toHaveBeenCalled()
  })

  it.each(['requesting', 'recording', 'processing'] as const)('cancels active %s when disabled', (status) => {
    voiceState = { ...voiceState, status, isRecording: status === 'recording', isProcessing: status === 'processing' }
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
    rerender(<VoiceInputButton onResult={vi.fn()} disabled />)
    expect(cancelRecording).toHaveBeenCalledOnce()
  })

  it('clears the mobile hold timer when disabled before recording starts', async () => {
    isMobile = true
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
    fireEvent.touchStart(screen.getByTestId('voice-input-button'))
    rerender(<VoiceInputButton onResult={vi.fn()} disabled />)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(startRecording).not.toHaveBeenCalled()
  })
})
