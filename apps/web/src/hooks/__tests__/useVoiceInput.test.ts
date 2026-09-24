import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useVoiceInput } from '../useVoiceInput'
import * as voiceApi from '@/lib/voiceApi'

// Mock voiceApi
vi.mock('@/lib/voiceApi', () => ({
  recognizeVoice: vi.fn(),
  blobToBase64: vi.fn(),
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
  })

  afterEach(() => {
    vi.restoreAllMocks()
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
