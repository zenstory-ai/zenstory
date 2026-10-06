import { http, HttpResponse } from 'msw'

/**
 * Voice Recognition Response Types
 * Matches backend API schemas from apps/server/api/voice.py
 */
interface VoiceRecognizeResponse {
  text: string
  success: boolean
  error?: string
  duration_ms?: number
}

interface VoiceStatusResponse {
  configured: boolean
  provider?: string
  service?: string
  max_duration_seconds?: number
  supported_formats?: string[]
  error?: string
}

/**
 * MSW handler for POST /api/v1/voice/recognize
 * Returns successful voice recognition result
 *
 * Simulates Tencent ASR response for short audio (<60s)
 */
export const mockVoiceRecognizeHandler = http.post(
  '/api/v1/voice/recognize',
  () => {
    const response: VoiceRecognizeResponse = {
      text: '这是一段测试语音识别结果',
      success: true,
      duration_ms: 3500, // 3.5 seconds audio duration
    }
    return HttpResponse.json(response)
  }
)

/**
 * MSW handler for GET /api/v1/voice/status
 * Returns configured voice service status
 */
export const mockVoiceStatusHandler = http.get('/api/v1/voice/status', () => {
  const response: VoiceStatusResponse = {
    configured: true,
    provider: 'tencent',
    service: '一句话识别',
    max_duration_seconds: 60,
    supported_formats: ['wav', 'pcm', 'ogg-opus', 'speex', 'silk', 'mp3', 'm4a', 'aac', 'amr'],
  }
  return HttpResponse.json(response)
})

/**
 * MSW handler for POST /api/v1/voice/recognize
 * Returns error scenario (recognition failed)
 */
export const mockVoiceRecognizeErrorHandler = http.post(
  '/api/v1/voice/recognize',
  () => {
    const response: VoiceRecognizeResponse = {
      text: '',
      success: false,
      error: 'InvalidParameter: 音频格式不支持',
    }
    return HttpResponse.json(response, { status: 400 })
  }
)

/**
 * MSW handler for GET /api/v1/voice/status
 * Returns not configured status
 */
export const mockVoiceNotConfiguredHandler = http.get(
  '/api/v1/voice/status',
  () => {
    const response: VoiceStatusResponse = {
      configured: false,
      error: 'Voice service not configured',
    }
    return HttpResponse.json(response)
  }
)

/**
 * MSW handler for POST /api/v1/voice/recognize
 * Returns audio decode failure
 */
export const mockVoiceDecodeFailedHandler = http.post(
  '/api/v1/voice/recognize',
  () => {
    const response: VoiceRecognizeResponse = {
      text: '',
      success: false,
      error: '语音识别失败: 音频数据 Base64 解码失败',
    }
    return HttpResponse.json(response, { status: 400 })
  }
)

/**
 * MSW handler for POST /api/v1/voice/recognize
 * Returns API request failure (upstream service error)
 */
export const mockVoiceApiFailedHandler = http.post(
  '/api/v1/voice/recognize',
  () => {
    const response: VoiceRecognizeResponse = {
      text: '',
      success: false,
      error: '腾讯云 API 请求失败: 502',
    }
    return HttpResponse.json(response, { status: 502 })
  }
)

/**
 * All voice handlers (success scenarios)
 */
export const voiceHandlers = [mockVoiceRecognizeHandler, mockVoiceStatusHandler]

/**
 * All voice handlers including error scenarios
 */
export const voiceHandlersWithError = [
  mockVoiceRecognizeHandler,
  mockVoiceStatusHandler,
  mockVoiceRecognizeErrorHandler,
  mockVoiceNotConfiguredHandler,
  mockVoiceDecodeFailedHandler,
  mockVoiceApiFailedHandler,
]
