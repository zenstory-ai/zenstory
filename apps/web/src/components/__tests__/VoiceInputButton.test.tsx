import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VoiceInputButton } from '../VoiceInputButton'

let isMobile = false
const startRecording = vi.fn()
const stopRecording = vi.fn()
const cancelRecording = vi.fn()
const releaseRecording = vi.fn()
const cancelPress = vi.fn(() => true)
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
  releaseRecording: typeof releaseRecording
  cancelPress: typeof cancelPress
  isSupported: boolean
}

let lastOptions:
  | {
      onResult: (text: string) => void
      onError: (message: string) => void
      onReleasedBeforeStart: () => void
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
          'chat:voice.requesting': 'Requesting microphone',
          'chat:voice.permissionReady': 'Microphone ready, hold again',
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
      releaseRecording,
      cancelPress,
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

  const touch = { pointerType: 'touch', pointerId: 7, isPrimary: true }

  async function holdPastThreshold(button: HTMLElement) {
    fireEvent.pointerDown(button, touch)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
  }

  it('records while a phone-width touch is held and stops on release', async () => {
    isMobile = true
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')

    await holdPastThreshold(button)
    expect(startRecording).toHaveBeenCalledTimes(1)

    voiceState = { ...voiceState, status: 'recording', isRecording: true, duration: 2 }
    rerender(<VoiceInputButton onResult={vi.fn()} />)
    expect(button).toHaveAttribute('aria-label', 'Release to stop')

    fireEvent.pointerUp(button, touch)
    expect(releaseRecording).toHaveBeenCalledTimes(1)
    expect(cancelRecording).not.toHaveBeenCalled()

    // The click browsers synthesize after the touch must not toggle again.
    fireEvent.click(button, { detail: 1 })
    expect(startRecording).toHaveBeenCalledTimes(1)
    expect(stopRecording).not.toHaveBeenCalled()
  })

  it('cancels a hold when the system cancels the touch', async () => {
    isMobile = true
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')
    await holdPastThreshold(button)
    voiceState = { ...voiceState, status: 'recording', isRecording: true, duration: 1 }
    rerender(<VoiceInputButton onResult={vi.fn()} />)

    fireEvent.pointerCancel(button, touch)

    expect(cancelPress).toHaveBeenCalledTimes(1)
    expect(toastInfo).toHaveBeenCalledWith('Recording cancelled')
  })

  it('does not announce a cancel when the permission prompt takes the touch', async () => {
    isMobile = true
    cancelPress.mockReturnValueOnce(false)
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')
    await holdPastThreshold(button)
    voiceState = { ...voiceState, status: 'requesting' }
    rerender(<VoiceInputButton onResult={vi.fn()} />)

    fireEvent.pointerCancel(button, touch)

    expect(cancelPress).toHaveBeenCalledTimes(1)
    expect(cancelRecording).not.toHaveBeenCalled()
    expect(toastInfo).not.toHaveBeenCalledWith('Recording cancelled')
  })

  it('explains the gesture on a short tap without opening the microphone', async () => {
    isMobile = true
    render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')

    fireEvent.pointerDown(button, touch)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    fireEvent.pointerUp(button, touch)
    fireEvent.click(button, { detail: 1 })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })

    expect(toastInfo).toHaveBeenCalledWith('Hold to record')
    expect(startRecording).not.toHaveBeenCalled()
  })

  it.each(['requesting', 'processing'] as const)(
    'hands a release during %s to the hook instead of cancelling',
    async (status) => {
      isMobile = true
      startRecording.mockReturnValueOnce(new Promise<void>(() => {}))
      const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
      const button = screen.getByTestId('voice-input-button')
      await holdPastThreshold(button)

      voiceState = { ...voiceState, status, isProcessing: status === 'processing' }
      rerender(<VoiceInputButton onResult={vi.fn()} />)
      fireEvent.pointerUp(button, touch)

      expect(releaseRecording).toHaveBeenCalledOnce()
      expect(cancelRecording).not.toHaveBeenCalled()
      expect(stopRecording).not.toHaveBeenCalled()
    },
  )

  it('shows the pending permission state and the ready hint', () => {
    voiceState = { ...voiceState, status: 'requesting' }
    render(<VoiceInputButton onResult={vi.fn()} />)
    expect(screen.getByTestId('voice-input-button')).toHaveAttribute('aria-label', 'Requesting microphone')

    act(() => {
      lastOptions?.onReleasedBeforeStart()
    })
    expect(toastInfo).toHaveBeenCalledWith('Microphone ready, hold again')
  })

  it.each([
    ['mouse click', { detail: 1 }],
    ['keyboard activation', { detail: 0 }],
  ] as const)('starts and stops with a %s at phone width', (_label, init) => {
    isMobile = true
    const { rerender } = render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')

    fireEvent.pointerDown(button, { pointerType: 'mouse', pointerId: 1, isPrimary: true })
    fireEvent.click(button, init)
    expect(startRecording).toHaveBeenCalledTimes(1)

    voiceState = { ...voiceState, status: 'recording', isRecording: true, duration: 3 }
    rerender(<VoiceInputButton onResult={vi.fn()} />)
    expect(button).toHaveAttribute('aria-label', 'Stop recording')
    fireEvent.click(button, init)
    expect(stopRecording).toHaveBeenCalledTimes(1)
  })

  it('keeps touch taps as click-to-toggle on wide screens', () => {
    render(<VoiceInputButton onResult={vi.fn()} />)
    const button = screen.getByTestId('voice-input-button')
    fireEvent.pointerDown(button, touch)
    fireEvent.pointerUp(button, touch)
    fireEvent.click(button, { detail: 1 })
    expect(startRecording).toHaveBeenCalledTimes(1)
    expect(toastInfo).not.toHaveBeenCalled()
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
    fireEvent.pointerDown(screen.getByTestId('voice-input-button'), touch)
    rerender(<VoiceInputButton onResult={vi.fn()} disabled />)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(startRecording).not.toHaveBeenCalled()
  })
})
