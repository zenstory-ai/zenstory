/**
 * Voice Input Hook
 * 
 * 提供录音和语音识别功能
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { recognizeVoice, blobToBase64, MAX_VOICE_DATA_LENGTH } from '../lib/voiceApi';
import type { VoiceSampleRate } from '../lib/voiceApi';
import { normalizeRecording } from '../lib/audioRecording';
import { logger } from '../lib/logger';

export type VoiceInputStatus = 
  | 'idle'           // 空闲状态
  | 'requesting'     // 正在请求麦克风权限
  | 'recording'      // 录音中
  | 'processing'     // 识别中
  | 'error';         // 错误

export interface UseVoiceInputOptions {
  /** 识别结果回调 */
  onResult?: (text: string) => void;
  /** 错误回调 */
  onError?: (error: string) => void;
  /** 最大录音时长（秒），默认 55 秒（腾讯云限制 60 秒） */
  maxDuration?: number;
  /** 采样率，默认 16000 */
  sampleRate?: VoiceSampleRate;
}

export interface UseVoiceInputReturn {
  /** 当前状态 */
  status: VoiceInputStatus;
  /** 是否正在录音 */
  isRecording: boolean;
  /** 是否正在处理 */
  isProcessing: boolean;
  /** 当前录音时长（秒） */
  duration: number;
  /** 当前音量级别 (0-1) */
  volume: number;
  /** 错误信息 */
  error: string | null;
  /** 开始录音 */
  startRecording: () => Promise<void>;
  /** 停止录音并识别 */
  stopRecording: () => void;
  /** 取消录音 */
  cancelRecording: () => void;
  /** 浏览器是否支持录音 */
  isSupported: boolean;
}

/**
 * 检查浏览器是否支持录音
 */
function checkMediaRecorderSupport(): boolean {
  if (typeof window === 'undefined') return false;
  
  return !!(
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    typeof window.MediaRecorder !== 'undefined' &&
    typeof window.OfflineAudioContext !== 'undefined'
  );
}

/**
 * 获取支持的 MIME 类型
 */
function getSupportedMimeType(): string | undefined {
  const types = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/wav',
  ];
  
  for (const type of types) {
    if (MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  
  return undefined;
}

export function useVoiceInput(options: UseVoiceInputOptions = {}): UseVoiceInputReturn {
  const {
    onResult,
    onError,
    maxDuration = 55,
    sampleRate = 16000,
  } = options;

  const [status, setStatus] = useState<VoiceInputStatus>('idle');
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const { t, i18n } = useTranslation();
  const getAsrLanguage = useCallback((): 'zh' | 'en' => {
    const lang = (i18n.language || '').toLowerCase();
    if (lang.startsWith('en')) return 'en';
    if (lang.startsWith('zh')) return 'zh';
    return 'zh';
  }, [i18n.language]);
  
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(0);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const volumeTimerRef = useRef<number | null>(null);
  const errorResetTimerRef = useRef<number | null>(null);
  const recognitionRequestRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);

  const isSupported = checkMediaRecorderSupport();

  // 清理资源
  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    
    if (volumeTimerRef.current !== null) {
      clearInterval(volumeTimerRef.current);
      volumeTimerRef.current = null;
    }

    if (errorResetTimerRef.current !== null) {
      clearTimeout(errorResetTimerRef.current);
      errorResetTimerRef.current = null;
    }
    recognitionRequestRef.current?.abort();
    recognitionRequestRef.current = null;
    
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
      analyserRef.current = null;
    }
    
    if (mediaRecorderRef.current) {
      const recorder = mediaRecorderRef.current;
      mediaRecorderRef.current = null;
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.onerror = null;
      if (recorder.state !== 'inactive') recorder.stop();
    }
    
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(track => track.stop());
      streamRef.current = null;
    }
    
    setDuration(0);
    setVolume(0);
  }, []);

  const scheduleErrorReset = useCallback((requestGeneration: number) => {
    errorResetTimerRef.current = window.setTimeout(() => {
      errorResetTimerRef.current = null;
      if (requestGeneration !== requestGenerationRef.current) return;
      setStatus('idle');
      setError(null);
    }, 3000);
  }, []);

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      requestGenerationRef.current += 1;
      cleanup();
    };
  }, [cleanup]);

  // 处理录音数据
  const processRecording = useCallback(async (
    requestGeneration: number,
    audioBlob: Blob | null,
  ) => {
    if (requestGeneration !== requestGenerationRef.current) return;
    if (!audioBlob) {
      setError(t('chat:voice.noRecordingData'));
      setStatus('error');
      onError?.(t('chat:voice.noRecordingData'));
      return;
    }

    setStatus('processing');
    setError(null);
    const controller = new AbortController();
    recognitionRequestRef.current = controller;

    try {
      const wav = await normalizeRecording(audioBlob, sampleRate, controller.signal);
      if (requestGeneration !== requestGenerationRef.current) return;
      // Tencent limits Data after Base64, not the compressed browser recording.
      if (Math.ceil(wav.size / 3) * 4 > MAX_VOICE_DATA_LENGTH) {
        throw new Error(t('chat:voice.audioTooLarge'));
      }
      
      // 转换为 Base64
      const base64Data = await blobToBase64(wav);
      if (requestGeneration !== requestGenerationRef.current) return;
      if (base64Data.length > MAX_VOICE_DATA_LENGTH) {
        throw new Error(t('chat:voice.audioTooLarge'));
      }
      
      // 调用识别 API
      const response = await recognizeVoice(
        base64Data,
        'wav',
        sampleRate,
        getAsrLanguage(),
        controller.signal,
      );
      if (requestGeneration !== requestGenerationRef.current) return;
      
      if (response.success && response.text) {
        setStatus('idle');
        onResult?.(response.text);
      } else {
        throw new Error(response.error || t('chat:voice.recognitionFailed'));
      }
    } catch (err) {
      if (requestGeneration !== requestGenerationRef.current) return;
      const errorMessage = err instanceof Error ? err.message : t('chat:voice.recognitionError');
      setError(errorMessage);
      setStatus('error');
      onError?.(errorMessage);
      
      // 3 秒后恢复 idle 状态
      scheduleErrorReset(requestGeneration);
    } finally {
      if (recognitionRequestRef.current === controller) recognitionRequestRef.current = null;
    }
  }, [onResult, onError, sampleRate, getAsrLanguage, scheduleErrorReset, t]);

  // Stop recording and trigger recognition
  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      // Stop timer
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }

      // Stop recording (will trigger onstop)
      mediaRecorderRef.current.stop();

      // Stop media stream
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop());
        streamRef.current = null;
      }
    }
  }, []);

  // 开始录音
  const startRecording = useCallback(async () => {
    const requestGeneration = requestGenerationRef.current + 1;
    requestGenerationRef.current = requestGeneration;
    cleanup();
    if (!isSupported) {
      const msg = t('chat:voice.browserNotSupported');
      setError(msg);
      setStatus('error');
      onError?.(msg);
      return;
    }

    setStatus('requesting');
    setError(null);

    try {
      // 请求麦克风权限
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: sampleRate,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      if (requestGeneration !== requestGenerationRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      
      streamRef.current = stream;
      
      // 设置音量分析器
      try {
        const audioContext = new AudioContext();
        const analyser = audioContext.createAnalyser();
        const source = audioContext.createMediaStreamSource(stream);
        analyser.fftSize = 256;
        source.connect(analyser);
        audioContextRef.current = audioContext;
        analyserRef.current = analyser;
        
        // 定期更新音量
        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        volumeTimerRef.current = window.setInterval(() => {
          if (analyserRef.current) {
            analyserRef.current.getByteFrequencyData(dataArray);
            // 计算平均音量 (0-255) 并归一化到 (0-1)
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
            setVolume(Math.min(1, avg / 128));
          }
        }, 50);
      } catch {
        // AudioContext 不可用时忽略音量分析
        logger.warn('AudioContext not available for volume analysis');
      }
      
      // 获取支持的 MIME 类型
      const preferredMimeType = getSupportedMimeType();
      
      // 创建 MediaRecorder
      const mediaRecorder = preferredMimeType
        ? new MediaRecorder(stream, { mimeType: preferredMimeType })
        : new MediaRecorder(stream);
      
      mediaRecorderRef.current = mediaRecorder;
      const chunks: Blob[] = [];
      
      // 监听数据
      mediaRecorder.ondataavailable = (event) => {
        if (requestGeneration !== requestGenerationRef.current || mediaRecorder !== mediaRecorderRef.current) return;
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };
      
      // 监听停止
      mediaRecorder.onstop = () => {
        if (requestGeneration !== requestGenerationRef.current || mediaRecorder !== mediaRecorderRef.current) return;
        const mimeType = mediaRecorder.mimeType || chunks[0]?.type || preferredMimeType || '';
        const audioBlob = chunks.length > 0
          ? new Blob(chunks, { type: mimeType })
          : null;
        cleanup();
        void processRecording(requestGeneration, audioBlob);
      };
      
      // 监听错误
      mediaRecorder.onerror = (event) => {
        if (requestGeneration !== requestGenerationRef.current || mediaRecorder !== mediaRecorderRef.current) return;
        logger.error('MediaRecorder error:', event);
        cleanup();
        setError(t('chat:voice.recordingError'));
        setStatus('error');
        onError?.(t('chat:voice.recordingError'));
      };
      
      // 开始录音
      mediaRecorder.start(100); // 每 100ms 触发一次 dataavailable
      startTimeRef.current = Date.now();
      setStatus('recording');
      
      // 更新时长定时器
      timerRef.current = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000);
        setDuration(elapsed);
        
        // 超过最大时长自动停止
        if (elapsed >= maxDuration) {
          stopRecording();
        }
      }, 100);
      
    } catch (err) {
      if (requestGeneration !== requestGenerationRef.current) {
        return;
      }
      cleanup();
      
      let errorMessage = t('chat:voice.micAccessDenied');
      if (err instanceof Error) {
        if (err.name === 'NotAllowedError') {
          errorMessage = t('chat:voice.micPermissionDenied');
        } else if (err.name === 'NotFoundError') {
          errorMessage = t('chat:voice.micNotFound');
        } else {
          errorMessage = err.message;
        }
      }
      
      setError(errorMessage);
      setStatus('error');
      onError?.(errorMessage);
      
      // 3 秒后恢复 idle 状态
      scheduleErrorReset(requestGeneration);
    }
  }, [isSupported, cleanup, maxDuration, onError, processRecording, sampleRate, scheduleErrorReset, stopRecording, t]);


  // 取消录音
  const cancelRecording = useCallback(() => {
    requestGenerationRef.current += 1;
    cleanup();
    setStatus('idle');
    setError(null);
  }, [cleanup]);

  return {
    status,
    isRecording: status === 'recording',
    isProcessing: status === 'processing',
    duration,
    volume,
    error,
    startRecording,
    stopRecording,
    cancelRecording,
    isSupported,
  };
}
