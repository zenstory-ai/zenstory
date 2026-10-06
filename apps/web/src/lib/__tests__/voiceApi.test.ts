/**
 * Tests for Voice Recognition API Client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock apiClient - must use factory function to avoid hoisting issues
vi.mock('../apiClient', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

// Import after mocking
import { recognizeVoice, getVoiceStatus, blobToBase64 } from '../voiceApi'
import { api } from '../apiClient'

// Get the mocked api
const mockApi = api as { [key: string]: ReturnType<typeof vi.fn> }

describe('voiceApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('recognizeVoice', () => {
    describe('Speech-to-Text', () => {
      it('transcribes audio data successfully', async () => {
        const mockResponse = {
          text: 'Hello, this is a test transcription',
          success: true,
          duration_ms: 1500,
        }
        mockApi.post.mockResolvedValue(mockResponse)

        const audioData = 'base64encodedaudiodata'
        const result = await recognizeVoice(audioData)

        expect(result).toEqual(mockResponse)
        expect(mockApi.post).toHaveBeenCalledWith('/api/v1/voice/recognize', {
          audio_data: audioData,
          audio_format: 'wav',
          sample_rate: 16000,
          language: 'zh',
        })
      })

      it('handles large audio files', async () => {
        const mockResponse = {
          text: 'Long transcription result',
          success: true,
          duration_ms: 5000,
        }
        mockApi.post.mockResolvedValue(mockResponse)

        // Simulate large audio data (1MB base64 encoded)
        const largeAudioData = 'a'.repeat(1024 * 1024)
        const result = await recognizeVoice(largeAudioData, 'wav', 16000, 'zh')

        expect(result).toEqual(mockResponse)
        expect(result.success).toBe(true)
      })

      it('handles unsupported audio formats', async () => {
        const mockResponse = {
          text: '',
          success: false,
          error: 'Unsupported audio format: xyz',
        }
        mockApi.post.mockResolvedValue(mockResponse)

        // @ts-expect-error Runtime validation of unsupported formats belongs to the backend.
        const result = await recognizeVoice('audiodata', 'xyz', 16000, 'zh')

        expect(result.success).toBe(false)
        expect(result.error).toContain('Unsupported audio format')
      })

      it('processes transcription result with success flag', async () => {
        const mockResponse = {
          text: 'Processed transcription',
          success: true,
          duration_ms: 2000,
        }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await recognizeVoice('audio', 'mp3', 16000, 'en')

        expect(result.text).toBe('Processed transcription')
        expect(result.success).toBe(true)
        expect(result.duration_ms).toBe(2000)
      })

      it('uses default parameters when not specified', async () => {
        const mockResponse = { text: 'Default test', success: true }
        mockApi.post.mockResolvedValue(mockResponse)

        await recognizeVoice('audiodata')

        expect(mockApi.post).toHaveBeenCalledWith('/api/v1/voice/recognize', {
          audio_data: 'audiodata',
          audio_format: 'wav',
          sample_rate: 16000,
          language: 'zh',
        })
      })

      it('supports custom audio format', async () => {
        const mockResponse = { text: 'WAV transcription', success: true }
        mockApi.post.mockResolvedValue(mockResponse)

        await recognizeVoice('audiodata', 'wav')

        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/voice/recognize',
          expect.objectContaining({
            audio_format: 'wav',
          })
        )
      })

      it('forwards cancellation to the actual API fetch options', async () => {
        mockApi.post.mockResolvedValueOnce({ text: 'local result', success: true })
        const controller = new AbortController()
        await recognizeVoice('audio', 'wav', 16000, 'zh', controller.signal)
        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/voice/recognize',
          { audio_data: 'audio', audio_format: 'wav', sample_rate: 16000, language: 'zh' },
          { signal: controller.signal },
        )
      })

      it('supports custom sample rate', async () => {
        const mockResponse = { text: '8kHz transcription', success: true }
        mockApi.post.mockResolvedValue(mockResponse)

        await recognizeVoice('audiodata', 'wav', 8000)

        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/voice/recognize',
          expect.objectContaining({
            sample_rate: 8000,
          })
        )
      })

      it('supports custom language', async () => {
        const mockResponse = { text: 'English transcription', success: true }
        mockApi.post.mockResolvedValue(mockResponse)

        await recognizeVoice('audiodata', 'wav', 16000, 'en')

        expect(mockApi.post).toHaveBeenCalledWith(
          '/api/v1/voice/recognize',
          expect.objectContaining({
            language: 'en',
          })
        )
      })
    })

    describe('Error Handling', () => {
      it('handles ASR service unavailable', async () => {
        const mockResponse = {
          text: '',
          success: false,
          error: 'ASR service unavailable',
        }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await recognizeVoice('audio')

        expect(result.success).toBe(false)
        expect(result.error).toContain('ASR service unavailable')
      })

      it('handles network timeout', async () => {
        mockApi.post.mockRejectedValue(new Error('Network timeout'))

        await expect(recognizeVoice('audio')).rejects.toThrow('Network timeout')
      })

      it('handles invalid audio format error', async () => {
        const mockResponse = {
          text: '',
          success: false,
          error: 'Invalid audio format: corrupted data',
        }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await recognizeVoice('invalid-audio')

        expect(result.success).toBe(false)
        expect(result.error).toContain('Invalid audio format')
      })

      it('handles file size exceeded error', async () => {
        const mockResponse = {
          text: '',
          success: false,
          error: 'File size exceeds maximum limit',
        }
        mockApi.post.mockResolvedValue(mockResponse)

        const result = await recognizeVoice('large-audio')

        expect(result.success).toBe(false)
        expect(result.error).toContain('File size exceeds')
      })
    })
  })

  describe('getVoiceStatus', () => {
    it('returns voice service status', async () => {
      const mockResponse = {
        configured: true,
        provider: 'tencent',
        service: 'asr',
        max_duration_seconds: 60,
        supported_formats: ['wav', 'pcm', 'ogg-opus', 'speex', 'silk', 'mp3', 'm4a', 'aac', 'amr'],
      }
      mockApi.get.mockResolvedValue(mockResponse)

      const result = await getVoiceStatus()

      expect(result).toEqual(mockResponse)
      expect(result.configured).toBe(true)
      expect(result.provider).toBe('tencent')
      expect(result.supported_formats).toContain('ogg-opus')
      expect(mockApi.get).toHaveBeenCalledWith('/api/v1/voice/status')
    })

    it('returns unconfigured status', async () => {
      const mockResponse = {
        configured: false,
        provider: 'none',
        service: 'asr',
        max_duration_seconds: 0,
        supported_formats: [],
      }
      mockApi.get.mockResolvedValue(mockResponse)

      const result = await getVoiceStatus()

      expect(result.configured).toBe(false)
    })

    it('handles service unavailable', async () => {
      mockApi.get.mockRejectedValue(new Error('Service unavailable'))

      await expect(getVoiceStatus()).rejects.toThrow('Service unavailable')
    })
  })

  describe('blobToBase64', () => {
    it('converts Blob to Base64 string', async () => {
      const blob = new Blob(['test content'], { type: 'audio/webm' })
      const base64 = await blobToBase64(blob)

      expect(typeof base64).toBe('string')
      expect(base64.length).toBeGreaterThan(0)
      // Base64 should not contain data URL prefix
      expect(base64).not.toContain('data:')
    })

    it('handles different blob types', async () => {
      const wavBlob = new Blob(['wav content'], { type: 'audio/wav' })
      const base64 = await blobToBase64(wavBlob)

      expect(typeof base64).toBe('string')
      expect(base64.length).toBeGreaterThan(0)
    })

    it('handles empty blob', async () => {
      const emptyBlob = new Blob([], { type: 'audio/webm' })
      const base64 = await blobToBase64(emptyBlob)

      expect(typeof base64).toBe('string')
    })

    it('rejects on FileReader error', async () => {
      const blob = new Blob(['test'], { type: 'audio/webm' })

      // Mock FileReader to simulate error
      const originalFileReader = global.FileReader
      class MockFileReader {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        onerror: ((this: FileReader, ev: ProgressEvent<FileReader>) => any) | null = null
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        onloadend: ((this: FileReader, ev: ProgressEvent<FileReader>) => any) | null = null
        readAsDataURL() {
          setTimeout(() => {
            if (this.onerror) {
              this.onerror.call(this as unknown as FileReader, new ProgressEvent('error') as ProgressEvent<FileReader>)
            }
          }, 0)
        }
      }
      global.FileReader = MockFileReader as unknown as typeof FileReader

      await expect(blobToBase64(blob)).rejects.toBeDefined()

      global.FileReader = originalFileReader
    })

    it('removes data URL prefix from result', async () => {
      const blob = new Blob(['test'], { type: 'audio/webm' })
      const base64 = await blobToBase64(blob)

      // The result should be pure base64 without "data:audio/webm;base64," prefix
      expect(base64).not.toMatch(/^data:/)
      expect(base64).not.toContain(';base64,')
    })

    it('preserves base64 data integrity', async () => {
      // Create a blob with known content
      const testString = 'Hello, World!'
      const blob = new Blob([testString], { type: 'text/plain' })
      const base64 = await blobToBase64(blob)

      // Decode the base64 and verify
      const decoded = atob(base64)
      expect(decoded).toBe(testString)
    })
  })

  describe('Integration Scenarios', () => {
    it('handles complete voice recognition flow', async () => {
      // Step 1: Check service status
      const statusResponse = {
        configured: true,
        provider: 'tencent',
        service: 'asr',
        max_duration_seconds: 60,
        supported_formats: ['wav'],
      }
      mockApi.get.mockResolvedValue(statusResponse)

      const status = await getVoiceStatus()
      expect(status.configured).toBe(true)

      // Step 2: Convert blob to base64
      const audioBlob = new Blob(['audio data'], { type: 'audio/webm' })
      const base64Audio = await blobToBase64(audioBlob)
      expect(typeof base64Audio).toBe('string')

      // Step 3: Recognize voice
      const recognizeResponse = {
        text: 'Recognized text',
        success: true,
        duration_ms: 1500,
      }
      mockApi.post.mockResolvedValue(recognizeResponse)

      const result = await recognizeVoice(base64Audio)
      expect(result.success).toBe(true)
      expect(result.text).toBe('Recognized text')
    })

    it('handles recognition failure gracefully', async () => {
      // Service is configured but recognition fails
      mockApi.post.mockResolvedValue({
        text: '',
        success: false,
        error: 'Recognition failed: audio quality too poor',
      })

      const result = await recognizeVoice('poor-quality-audio')

      expect(result.success).toBe(false)
      expect(result.error).toContain('audio quality')
    })

    it('supports multiple recognition requests', async () => {
      const mockResponses = [
        { text: 'First transcription', success: true, duration_ms: 1000 },
        { text: 'Second transcription', success: true, duration_ms: 1200 },
        { text: 'Third transcription', success: true, duration_ms: 1100 },
      ]

      mockApi.post
        .mockResolvedValueOnce(mockResponses[0])
        .mockResolvedValueOnce(mockResponses[1])
        .mockResolvedValueOnce(mockResponses[2])

      const results = await Promise.all([
        recognizeVoice('audio1'),
        recognizeVoice('audio2'),
        recognizeVoice('audio3'),
      ])

      expect(results[0].text).toBe('First transcription')
      expect(results[1].text).toBe('Second transcription')
      expect(results[2].text).toBe('Third transcription')
    })
  })
})
