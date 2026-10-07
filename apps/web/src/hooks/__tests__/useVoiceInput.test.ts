import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useVoiceInput } from '../useVoiceInput'
import * as voiceApi from '@/lib/voiceApi'
import { normalizeRecording } from '@/lib/audioRecording'

vi.mock('@/lib/audioRecording', () => ({ normalizeRecording: vi.fn() }))

// Mock voiceApi
vi.mock('@/lib/voiceApi', () => ({
  recognizeVoice: vi.fn(),
  blobToBase64: vi.fn(),
  MAX_VOICE_DATA_LENGTH: 3_000_000,
}))

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: {
      language: 'zh',
    },
  }),
}))

describe('useVoiceInput', () => {
  // Store callbacks and mock functions at module scope
  let mockStart: ReturnType<typeof vi.fn>
  let mockStop: ReturnType<typeof vi.fn>
  let mockGetTracks: ReturnType<typeof vi.fn>
  let ondataavailableCallback: ((event: { data: Blob }) => void) | null
  let onstopCallback: (() => void) | null
  let recorderState: string

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})

    mockStart = vi.fn(() => {
      recorderState = 'recording'
    })
    mockStop = vi.fn(() => {
      recorderState = 'inactive'
    })
    mockGetTracks = vi.fn(() => [{ stop: vi.fn() }])
    ondataavailableCallback = null
    onstopCallback = null
    recorderState = 'inactive'

    // Mock MediaRecorder class (constructed with `new`, so not an arrow function)
    const MockMediaRecorder = vi.fn().mockImplementation(function () {
      return {
        start: mockStart,
        stop: mockStop,
        get ondataavailable() { return ondataavailableCallback },
        set ondataavailable(cb: typeof ondataavailableCallback) { ondataavailableCallback = cb },
        get onstop() { return onstopCallback },
        set onstop(cb: typeof onstopCallback) { onstopCallback = cb },
        get state() { return recorderState },
      }
    })
    ;(MockMediaRecorder as unknown as { isTypeSupported: typeof vi.fn }).isTypeSupported = vi.fn(() => true)
    vi.stubGlobal('MediaRecorder', MockMediaRecorder)
    vi.stubGlobal('OfflineAudioContext', vi.fn())

    // Mock navigator.mediaDevices
    const mockStream = {
      getTracks: mockGetTracks,
    }
    vi.stubGlobal('navigator', {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue(mockStream),
      },
    })

    vi.mocked(voiceApi.blobToBase64).mockResolvedValue('base64-audio-data')
    vi.mocked(normalizeRecording).mockResolvedValue(new Blob(['wav-data'], { type: 'audio/wav' }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  describe('initial state', () => {
    it('initializes with correct default state', () => {
      const { result } = renderHook(() => useVoiceInput())

      expect(result.current.status).toBe('idle')
      expect(result.current.isRecording).toBe(false)
      expect(result.current.isProcessing).toBe(false)
      expect(result.current.duration).toBe(0)
      expect(result.current.volume).toBe(0)
      expect(result.current.error).toBe(null)
    })

    it('reports isSupported as true when MediaRecorder available', () => {
      const { result } = renderHook(() => useVoiceInput())
      expect(result.current.isSupported).toBe(true)
    })

    it('reports isSupported as false when MediaRecorder not available', () => {
      vi.stubGlobal('MediaRecorder', undefined)
      vi.stubGlobal('navigator', {
        mediaDevices: undefined,
      })

      const { result } = renderHook(() => useVoiceInput())
      expect(result.current.isSupported).toBe(false)
    })
  })

  describe('startRecording', () => {
    it('sets status to requesting when starting', () => {
      const { result } = renderHook(() => useVoiceInput())

      act(() => {
        result.current.startRecording()
      })

      expect(result.current.status).toBe('requesting')
    })

    it('requests microphone permission', async () => {
      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
    })

    it('sets status to recording after permission granted', async () => {
      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.status).toBe('recording')
      expect(result.current.isRecording).toBe(true)
    })

    it('creates and starts MediaRecorder', async () => {
      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      expect(mockStart).toHaveBeenCalledWith(100)
    })

    it('handles permission denied error', async () => {
      const error = new Error('Permission denied')
      error.name = 'NotAllowedError'
      vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(error)

      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onError }))

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.status).toBe('error')
      expect(result.current.error).toContain('chat:voice.micPermissionDenied')
      expect(onError).toHaveBeenCalled()
    })

    it('handles device not found error', async () => {
      const error = new Error('Device not found')
      error.name = 'NotFoundError'
      vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(error)

      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onError }))

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.status).toBe('error')
      expect(result.current.error).toContain('chat:voice.micNotFound')
    })

    it('clears previous error on new recording', async () => {
      const { result } = renderHook(() => useVoiceInput())

      // First attempt fails
      vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new Error('First error'))

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.error).toBeTruthy()

      // Second attempt succeeds
      const mockStream = { getTracks: mockGetTracks }
      vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce(mockStream)

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.error).toBe(null)
    })

    it('uses custom sample rate from options', async () => {
      const { result } = renderHook(() =>
        useVoiceInput({ sampleRate: 8000 })
      )

      await act(async () => {
        await result.current.startRecording()
      })

      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith(
        expect.objectContaining({
          audio: expect.objectContaining({
            sampleRate: 8000,
          }),
        })
      )
    })
  })

  describe('stopRecording', () => {
    it('stops MediaRecorder when called', async () => {
      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      // Verify recording started
      expect(result.current.isRecording).toBe(true)

      act(() => {
        result.current.stopRecording()
      })

      expect(mockStop).toHaveBeenCalled()
    })

    it('stops media stream tracks', async () => {
      const mockTrack = { stop: vi.fn() }
      mockGetTracks.mockReturnValueOnce([mockTrack])

      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      act(() => {
        result.current.stopRecording()
      })

      expect(mockTrack.stop).toHaveBeenCalled()
    })
  })

  describe('cancelRecording', () => {
    it('discards a stream that resolves after cancellation while permission is pending', async () => {
      let resolveStream: ((stream: { getTracks: typeof mockGetTracks }) => void) | undefined
      const pendingStream = new Promise<{ getTracks: typeof mockGetTracks }>((resolve) => {
        resolveStream = resolve
      })
      vi.mocked(navigator.mediaDevices.getUserMedia).mockReturnValueOnce(
        pendingStream as Promise<MediaStream>
      )
      const track = { stop: vi.fn() }
      mockGetTracks.mockReturnValue([track])

      const { result } = renderHook(() => useVoiceInput())
      let startPromise: Promise<void>
      act(() => {
        startPromise = result.current.startRecording()
      })
      act(() => result.current.cancelRecording())

      await act(async () => {
        resolveStream?.({ getTracks: mockGetTracks })
        await startPromise!
      })

      expect(track.stop).toHaveBeenCalled()
      expect(mockStart).not.toHaveBeenCalled()
      expect(result.current.status).toBe('idle')
    })

    it('cancels recording without processing', async () => {
      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.isRecording).toBe(true)

      act(() => {
        result.current.cancelRecording()
      })

      expect(result.current.status).toBe('idle')
      expect(result.current.duration).toBe(0)
      expect(result.current.volume).toBe(0)
    })

    it('clears error on cancel', async () => {
      vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new Error('Some error'))

      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      expect(result.current.error).toBeTruthy()

      act(() => {
        result.current.cancelRecording()
      })

      expect(result.current.error).toBe(null)
    })
  })

  describe('voice recognition', () => {
    it('calls recognizeVoice after recording', async () => {
      vi.mocked(voiceApi.recognizeVoice).mockResolvedValueOnce({
        success: true,
        text: 'Hello world',
      })

      const { result } = renderHook(() => useVoiceInput())

      await act(async () => {
        await result.current.startRecording()
      })

      // Simulate recording data
      act(() => {
        if (ondataavailableCallback) {
          ondataavailableCallback({ data: new Blob(['audio-data']) })
        }
      })

      // Stop recording triggers processing
      act(() => {
        result.current.stopRecording()
      })

      // Trigger onstop callback
      await act(async () => {
        if (onstopCallback) {
          onstopCallback()
        }
      })

      expect(voiceApi.recognizeVoice).toHaveBeenCalled()
    })

    it('calls onResult callback with recognized text', async () => {
      vi.mocked(voiceApi.recognizeVoice).mockResolvedValueOnce({
        success: true,
        text: 'Hello world',
      })

      const onResult = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onResult }))

      await act(async () => {
        await result.current.startRecording()
      })

      act(() => {
        if (ondataavailableCallback) {
          ondataavailableCallback({ data: new Blob(['audio-data']) })
        }
      })

      act(() => {
        result.current.stopRecording()
      })

      await act(async () => {
        if (onstopCallback) {
          onstopCallback()
        }
      })

      expect(onResult).toHaveBeenCalledWith('Hello world')
    })

    it('handles recognition failure', async () => {
      vi.mocked(voiceApi.recognizeVoice).mockResolvedValueOnce({
        success: false,
        text: '',
        error: 'Recognition failed',
      })

      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onError }))

      await act(async () => {
        await result.current.startRecording()
      })

      act(() => {
        if (ondataavailableCallback) {
          ondataavailableCallback({ data: new Blob(['audio-data']) })
        }
      })

      act(() => {
        result.current.stopRecording()
      })

      await act(async () => {
        if (onstopCallback) {
          onstopCallback()
        }
      })

      expect(result.current.status).toBe('error')
      expect(onError).toHaveBeenCalled()
    })

    it('handles recognition exception', async () => {
      vi.mocked(voiceApi.recognizeVoice).mockRejectedValueOnce(
        new Error('Network error')
      )

      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onError }))

      await act(async () => {
        await result.current.startRecording()
      })

      act(() => {
        if (ondataavailableCallback) {
          ondataavailableCallback({ data: new Blob(['audio-data']) })
        }
      })

      act(() => {
        result.current.stopRecording()
      })

      await act(async () => {
        if (onstopCallback) {
          onstopCallback()
        }
      })

      expect(result.current.status).toBe('error')
      expect(result.current.error).toContain('Network error')
    })
  })

  describe('recording lifecycle', () => {
    const deferred = <T,>() => {
      let resolve!: (value: T) => void
      let reject!: (error: Error) => void
      const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
      return { promise, resolve, reject }
    }

    async function stopWithData(result: { current: ReturnType<typeof useVoiceInput> }) {
      act(() => {
        ondataavailableCallback?.({ data: new Blob(['audio']) })
        result.current.stopRecording()
      })
      await act(async () => { onstopCallback?.() })
    }

    it.each(['resolve', 'reject'] as const)('ignores a late recognition %s after cancellation', async (outcome) => {
      const request = deferred<voiceApi.VoiceRecognizeResponse>()
      vi.mocked(voiceApi.recognizeVoice).mockReturnValueOnce(request.promise)
      const onResult = vi.fn()
      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onResult, onError }))
      await act(async () => { await result.current.startRecording() })
      await stopWithData(result)
      expect(result.current.status).toBe('processing')
      act(() => result.current.cancelRecording())
      expect(vi.mocked(voiceApi.recognizeVoice).mock.calls[0][4]?.aborted).toBe(true)
      await act(async () => {
        if (outcome === 'resolve') request.resolve({ success: true, text: 'stale text' })
        else request.reject(new Error('stale error'))
      })
      expect(onResult).not.toHaveBeenCalled()
      expect(onError).not.toHaveBeenCalled()
      expect(result.current.status).toBe('idle')
    })

    it('does not issue recognition when conversion finishes after cancellation', async () => {
      const conversion = deferred<string>()
      vi.mocked(voiceApi.blobToBase64).mockReturnValueOnce(conversion.promise)
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      await stopWithData(result)
      act(() => result.current.cancelRecording())
      await act(async () => { conversion.resolve('stale-audio') })
      expect(voiceApi.recognizeVoice).not.toHaveBeenCalled()
      expect(result.current.status).toBe('idle')
    })

    it('does not encode or upload when normalization finishes after cancellation', async () => {
      const conversion = deferred<Blob>()
      vi.mocked(normalizeRecording).mockReturnValueOnce(conversion.promise)
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      await stopWithData(result)
      act(() => result.current.cancelRecording())
      expect(vi.mocked(normalizeRecording).mock.calls[0][2]?.aborted).toBe(true)
      await act(async () => { conversion.resolve(new Blob(['late-wav'])) })
      expect(voiceApi.blobToBase64).not.toHaveBeenCalled()
      expect(voiceApi.recognizeVoice).not.toHaveBeenCalled()
      expect(result.current.status).toBe('idle')
    })

    it('ignores recognition completion after unmount', async () => {
      const request = deferred<voiceApi.VoiceRecognizeResponse>()
      vi.mocked(voiceApi.recognizeVoice).mockReturnValueOnce(request.promise)
      const onResult = vi.fn()
      const { result, unmount } = renderHook(() => useVoiceInput({ onResult }))
      await act(async () => { await result.current.startRecording() })
      await stopWithData(result)
      unmount()
      await act(async () => { request.resolve({ success: true, text: 'unmounted text' }) })
      expect(onResult).not.toHaveBeenCalled()
    })

    it('ignores final data and stop events already queued before cancellation', async () => {
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      const queuedData = ondataavailableCallback
      const queuedStop = onstopCallback
      act(() => result.current.cancelRecording())
      await act(async () => {
        queuedData?.({ data: new Blob(['cancelled-audio']) })
        queuedStop?.()
      })
      expect(voiceApi.blobToBase64).not.toHaveBeenCalled()
      expect(voiceApi.recognizeVoice).not.toHaveBeenCalled()
      expect(result.current.status).toBe('idle')
    })

    it('closes the AudioContext and removes timers after a normal stop', async () => {
      vi.useFakeTimers()
      const close = vi.fn().mockResolvedValue(undefined)
      vi.stubGlobal('AudioContext', vi.fn().mockImplementation(function () {
        return {
          close,
          createAnalyser: () => ({ frequencyBinCount: 128, getByteFrequencyData: vi.fn() }),
          createMediaStreamSource: () => ({ connect: vi.fn() }),
        }
      }))
      vi.mocked(voiceApi.recognizeVoice).mockResolvedValueOnce({ success: true, text: 'done' })
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      expect(vi.getTimerCount()).toBe(2)
      await stopWithData(result)
      expect(close).toHaveBeenCalledTimes(1)
      expect(vi.getTimerCount()).toBe(0)
      expect(result.current.status).toBe('idle')
    })

    it('reports an empty final recording instead of remaining recording', async () => {
      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onError }))
      await act(async () => { await result.current.startRecording() })
      act(() => result.current.stopRecording())
      await act(async () => { onstopCallback?.() })
      expect(result.current.status).toBe('error')
      expect(onError).toHaveBeenCalledWith('chat:voice.noRecordingData')
      expect(voiceApi.recognizeVoice).not.toHaveBeenCalled()
    })

    it('does not let the previous error-reset timer change a new recording', async () => {
      vi.useFakeTimers()
      vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new Error('first failure'))
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      expect(result.current.status).toBe('error')
      await act(async () => { await result.current.startRecording() })
      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
      expect(result.current.status).toBe('recording')
    })
  })

  describe('provider audio format', () => {
    it.each(['audio/webm;codecs=opus', 'audio/mp4'])('normalizes %s to actual WAV before upload', async (mimeType) => {
      vi.mocked(MediaRecorder.isTypeSupported).mockImplementation((type) => type === mimeType)
      vi.mocked(voiceApi.recognizeVoice).mockResolvedValueOnce({ success: true, text: 'normalized' })
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      act(() => {
        ondataavailableCallback?.({ data: new Blob(['native-audio'], { type: mimeType }) })
        result.current.stopRecording()
      })
      await act(async () => { onstopCallback?.() })
      expect(normalizeRecording).toHaveBeenCalledWith(
        expect.objectContaining({ type: mimeType }), 16000, expect.any(AbortSignal),
      )
      expect(voiceApi.blobToBase64).toHaveBeenCalledWith(expect.objectContaining({ type: 'audio/wav' }))
      expect(voiceApi.recognizeVoice).toHaveBeenCalledWith(
        'base64-audio-data', 'wav', 16000, 'zh', expect.any(AbortSignal),
      )
    })

    it('lets the browser choose a container when no preferred MIME is supported', async () => {
      vi.mocked(MediaRecorder.isTypeSupported).mockReturnValue(false)
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      expect(MediaRecorder).toHaveBeenCalledWith(expect.anything())
    })

    it('rejects oversized normalized WAV before base64 or provider requests', async () => {
      vi.mocked(normalizeRecording).mockResolvedValueOnce(new Blob([new Uint8Array(3 * 1024 * 1024)]))
      const { result } = renderHook(() => useVoiceInput())
      await act(async () => { await result.current.startRecording() })
      act(() => {
        ondataavailableCallback?.({ data: new Blob(['small-compressed-audio']) })
        result.current.stopRecording()
      })
      await act(async () => { onstopCallback?.() })
      expect(result.current.error).toBe('chat:voice.audioTooLarge')
      expect(voiceApi.blobToBase64).not.toHaveBeenCalled()
      expect(voiceApi.recognizeVoice).not.toHaveBeenCalled()
    })
  })

  describe('options', () => {
    it('accepts maxDuration option', () => {
      const { result } = renderHook(() =>
        useVoiceInput({ maxDuration: 30 })
      )

      expect(result.current).toBeDefined()
    })

    it('accepts sampleRate option', () => {
      const { result } = renderHook(() =>
        useVoiceInput({ sampleRate: 8000 })
      )

      expect(result.current).toBeDefined()
    })

    it('accepts onResult callback', () => {
      const onResult = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onResult }))

      expect(result.current).toBeDefined()
    })

    it('accepts onError callback', () => {
      const onError = vi.fn()
      const { result } = renderHook(() => useVoiceInput({ onError }))

      expect(result.current).toBeDefined()
    })
  })

  describe('return values', () => {
    it('returns all expected properties', () => {
      const { result } = renderHook(() => useVoiceInput())

      expect(result.current).toHaveProperty('status')
      expect(result.current).toHaveProperty('isRecording')
      expect(result.current).toHaveProperty('isProcessing')
      expect(result.current).toHaveProperty('duration')
      expect(result.current).toHaveProperty('volume')
      expect(result.current).toHaveProperty('error')
      expect(result.current).toHaveProperty('startRecording')
      expect(result.current).toHaveProperty('stopRecording')
      expect(result.current).toHaveProperty('cancelRecording')
      expect(result.current).toHaveProperty('isSupported')
    })
  })
})
