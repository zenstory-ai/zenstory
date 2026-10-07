/**
 * Voice Recognition API Client
 *
 * Provides speech-to-text functionality via Tencent Cloud ASR (Automatic Speech Recognition).
 * Used by the voice input feature in the AI chat interface for hands-free text input.
 *
 * Features:
 * - Supports Tencent SentenceRecognition's actual audio formats
 * - Chinese and English language recognition
 * - Configurable sample rates (8000Hz or 16000Hz)
 */

import { api } from './apiClient';

export type VoiceAudioFormat = 'wav' | 'pcm' | 'ogg-opus' | 'speex' | 'silk' | 'mp3' | 'm4a' | 'aac' | 'amr';
export type VoiceSampleRate = 8000 | 16000;
// SentenceRecognition limits Data after Base64 encoding; use decimal MB.
export const MAX_VOICE_DATA_LENGTH = 3_000_000;

/**
 * Request payload for voice recognition API.
 */
export interface VoiceRecognizeRequest {
  /** Base64 encoded audio data */
  audio_data: string;
  /** Actual provider container/codec, never a relabeled browser recording */
  audio_format: VoiceAudioFormat;
  /** Sample rate in Hz: 8000 or 16000 */
  sample_rate: VoiceSampleRate;
  /** Recognition language: 'zh' or 'en' (also accepts zh-CN/en-US) */
  language: string;
}

/**
 * Response from voice recognition API containing transcribed text.
 */
export interface VoiceRecognizeResponse {
  /** Transcribed text from the audio */
  text: string;
  /** Whether recognition was successful */
  success: boolean;
  /** Error message if recognition failed */
  error?: string | null;
  /** Processing duration in milliseconds */
  duration_ms?: number | null;
}

/**
 * Voice recognition service status and configuration info.
 */
export interface VoiceStatusResponse {
  /** Whether the voice recognition service is configured and available */
  configured: boolean;
  /** Service provider name (e.g., 'tencent') */
  provider: string;
  /** Service name (e.g., 'asr') */
  service: string;
  /** Maximum allowed audio duration in seconds */
  max_duration_seconds: number;
  /** List of supported audio formats */
  supported_formats: VoiceAudioFormat[];
}

/**
 * Send audio data to the voice recognition API for transcription.
 *
 * Converts speech to text using Tencent Cloud ASR service.
 * Audio must be provided as base64-encoded data.
 *
 * @param audioData - Base64 encoded audio data
 * @param audioFormat - Audio format (default: 'wav')
 * @param sampleRate - Sample rate in Hz (default: 16000)
 * @param language - Recognition language (default: 'zh')
 * @returns Promise resolving to recognition result with transcribed text
 */
export async function recognizeVoice(
  audioData: string,
  audioFormat: VoiceAudioFormat = 'wav',
  sampleRate: VoiceSampleRate = 16000,
  language: string = 'zh',
  signal?: AbortSignal,
): Promise<VoiceRecognizeResponse> {
  const request: VoiceRecognizeRequest = {
    audio_data: audioData,
    audio_format: audioFormat,
    sample_rate: sampleRate,
    language,
  };

  return signal
    ? api.post<VoiceRecognizeResponse>('/api/v1/voice/recognize', request, { signal })
    : api.post<VoiceRecognizeResponse>('/api/v1/voice/recognize', request);
}

/**
 * Check the voice recognition service status and configuration.
 *
 * Returns information about whether the service is configured,
 * the provider, and supported audio formats.
 *
 * @returns Promise resolving to service status information
 */
export async function getVoiceStatus(): Promise<VoiceStatusResponse> {
  return api.get<VoiceStatusResponse>('/api/v1/voice/status');
}

/**
 * Convert a Blob to base64-encoded string for API transmission.
 *
 * Uses FileReader to read the blob as a data URL, then extracts
 * the base64 portion. This ensures compatibility with Tencent Cloud API
 * which expects pure base64 strings without data URI prefix.
 *
 * @param blob - Audio Blob to convert
 * @returns Promise resolving to base64-encoded string (without data URI prefix)
 */
export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result as string;
      // Remove the data:audio/xxx;base64, prefix, keeping only the pure base64 string
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
