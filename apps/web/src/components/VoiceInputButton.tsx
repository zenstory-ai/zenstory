/**
 * @fileoverview VoiceInputButton component - Voice input control with cross-platform support.
 *
 * This component provides a voice input button with platform-specific interaction patterns:
 * - Desktop: Click to start/stop recording
 * - Mobile: Long-press to record, release to transcribe
 * - Visual waveform feedback during recording
 * - Toast notifications for errors and cancellation
 * - Browser support detection (graceful degradation)
 *
 * @module components/VoiceInputButton
 */

import React, { useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Mic, MicOff, Loader2 } from 'lucide-react';
import { useVoiceInput } from '../hooks/useVoiceInput';
import { useIsMobile } from '../hooks/useMediaQuery';
import type { VoiceInputStatus } from '../hooks/useVoiceInput';
import { toast } from '../lib/toast';
import { logger } from "../lib/logger";

/**
 * Props for the VoiceInputButton component.
 * Provides voice recording and speech-to-text functionality.
 */
interface VoiceInputButtonProps {
  /**
   * Callback invoked when speech recognition completes successfully.
   * @param text - The transcribed text from speech recognition
   */
  onResult: (text: string) => void;
  /** Whether the button is disabled (e.g., during AI response generation) */
  disabled?: boolean;
  /** Additional CSS classes to apply to the button */
  className?: string;
}

/**
 * Format recording duration for display.
 *
 * Converts seconds to M:SS format with zero-padded seconds.
 *
 * @param seconds - Duration in seconds to format
 * @returns Formatted string in "M:SS" format
 *
 * @example
 * formatDuration(65) // Returns "1:05"
 * formatDuration(0) // Returns "0:00"
 */
function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Props for the VoiceWaveform visualization component.
 */
interface VoiceWaveformProps {
  /** Current audio volume level (0-1) that affects bar heights */
  volume: number;
  /** Whether recording is active (component returns null when false) */
  isRecording: boolean;
}

/**
 * Voice waveform animation component.
 *
 * Renders a 5-bar animated visualization that responds to audio volume levels.
 * Only visible during active recording. Each bar has a base height pattern
 * that is dynamically scaled based on the current volume.
 *
 * @param props - Waveform properties
 * @returns Waveform visualization or null when not recording
 */
const VoiceWaveform: React.FC<VoiceWaveformProps> = ({ volume, isRecording }) => {
  if (!isRecording) return null;
  
  // 生成 5 个波形条
  const bars = [0.6, 1, 0.7, 0.9, 0.5];
  
  return (
    <div className="flex items-center gap-0.5 h-4">
      {bars.map((baseHeight, i) => {
        // 根据音量动态调整高度
        const height = Math.max(0.2, baseHeight * (0.3 + volume * 0.7));
        const delay = i * 0.1;
        
        return (
          <div
            key={i}
            className="w-0.5 bg-[hsl(var(--bg-primary))] rounded-full transition-all duration-75"
            style={{
              height: `${height * 100}%`,
              animationDelay: `${delay}s`,
            }}
          />
        );
      })}
    </div>
  );
};

/**
 * Compute button styles based on current state.
 *
 * Returns appropriate Tailwind CSS classes for the button based on:
 * - Disabled state: Grayed out with reduced opacity
 * - Recording state: Red background with expanded width for duration display
 * - Processing state: Accent color with loading spinner
 * - Error state: Red background with error icon
 * - Default: Tertiary background with hover effects
 *
 * @param status - Current voice input status ('idle' | 'recording' | 'processing' | 'error')
 * @param disabled - Whether the button is disabled
 * @param isMobile - Whether running on mobile device (affects hover behavior)
 * @returns Tailwind CSS class string for the button
 */
function getButtonStyles(status: VoiceInputStatus, disabled: boolean, isMobile: boolean): string {
  const baseStyles = 'shrink-0 flex items-center justify-center rounded-lg transition-all duration-200 select-none';
  const squareSize = isMobile ? 'w-11 h-11' : 'w-9 h-9';
  const recordingSize = isMobile ? 'min-w-[96px] h-11 px-3' : 'min-w-[80px] h-9 px-2.5';
  
  if (disabled) {
    return `${baseStyles} ${squareSize} bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] cursor-not-allowed opacity-50`;
  }
  
  switch (status) {
    case 'recording':
      return `${baseStyles} ${recordingSize} bg-[hsl(var(--error))] text-white ${isMobile ? '' : 'hover:bg-[hsl(var(--error)/0.9)]'}`;
    case 'processing':
      return `${baseStyles} ${squareSize} bg-[hsl(var(--accent-primary))] text-white`;
    case 'error':
      return `${baseStyles} ${squareSize} bg-[hsl(var(--error))] text-white`;
    default:
      return `${baseStyles} ${squareSize} bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary)/0.8)] hover:text-[hsl(var(--text-primary))] ${isMobile ? 'active:scale-95' : ''}`;
  }
}

/**
 * Voice input button component with platform-specific interaction patterns.
 *
 * Provides voice recording and speech-to-text functionality with different
 * behaviors for desktop and mobile platforms:
 *
 * **Desktop (click interaction):**
 * - Click to start recording
 * - Click again or right-click to stop/cancel
 * - Visual waveform shows recording progress
 *
 * **Mobile (long-press interaction):**
 * - Long-press (200ms threshold) starts recording
 * - Release to stop and transcribe
 * - Move finger away to cancel
 * - Touch-optimized button sizing
 *
 * **Features:**
 * - Auto-dismisses after 55 seconds max duration
 * - Browser support detection (graceful degradation)
 * - Toast notifications for errors and cancellation
 * - Visual volume waveform during recording
 * - Duration timer display
 * - Loading spinner during transcription
 *
 * @param props - Component properties
 * @returns Voice input button element or null if speech recognition not supported
 *
 * @example
 * ```tsx
 * // Basic usage in chat input
 * <VoiceInputButton
 *   onResult={(text) => setMessageInput(text)}
 *   disabled={isGenerating}
 * />
 * ```
 *
 * @example
 * ```tsx
 * // With custom styling
 * <VoiceInputButton
 *   onResult={handleVoiceResult}
 *   className="shadow-lg"
 * />
 * ```
 */
export const VoiceInputButton: React.FC<VoiceInputButtonProps> = ({
  onResult,
  disabled = false,
  className = '',
}) => {
  const isMobile = useIsMobile();
  const { t } = useTranslation(['chat', 'common']);
  const longPressTimerRef = useRef<number | null>(null);
  const isLongPressRef = useRef(false);
  
  const {
    status,
    isRecording,
    isProcessing,
    duration,
    volume,
    error,
    startRecording,
    stopRecording,
    cancelRecording,
    isSupported,
  } = useVoiceInput({
    onResult: (text) => {
      // 识别成功后直接回调，不显示 toast
      onResult(text);
    },
    onError: (err) => {
      logger.error('Voice input error:', err);
      toast.error(err);
    },
    maxDuration: 55,
  });

  // 清理长按计时器
  useEffect(() => {
    return () => {
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!disabled) return;
    if (longPressTimerRef.current !== null) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
    isLongPressRef.current = false;
    if (status === 'requesting' || status === 'recording' || status === 'processing') cancelRecording();
  }, [disabled, status, cancelRecording]);

  // 不支持录音的浏览器不显示按钮
  if (!isSupported) {
    return null;
  }

  // PC 端点击处理
  const handleClick = async () => {
    if (disabled || isMobile) return;
    
    if (status === 'requesting') {
      cancelRecording();
    } else if (isRecording) {
      stopRecording();
    } else if (!isProcessing) {
      await startRecording();
    }
  };

  // 移动端长按开始
  const handleTouchStart = async (e: React.TouchEvent) => {
    if (disabled || !isMobile || isProcessing || status === 'requesting') return;
    
    e.preventDefault();
    isLongPressRef.current = false;
    
    // 200ms 后判定为长按
    longPressTimerRef.current = window.setTimeout(async () => {
      longPressTimerRef.current = null;
      isLongPressRef.current = true;
      await startRecording();
    }, 200);
  };

  // 移动端长按结束
  const handleTouchEnd = (e: React.TouchEvent) => {
    if (disabled || !isMobile) return;
    
    e.preventDefault();
    
    // 清除长按计时器
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
    
    // Permission may still be pending after the finger is released.
    if (isLongPressRef.current) {
      if (isRecording) {
        stopRecording();
      } else {
        cancelRecording();
      }
    }
    
    isLongPressRef.current = false;
  };

  // 移动端取消（手指移开）
  const handleTouchCancel = () => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
    
    if (isLongPressRef.current) {
      cancelRecording();
      toast.info(t('chat:voice.cancelled'));
    }
    
    isLongPressRef.current = false;
  };

  // PC 端右键取消
  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    if ((isRecording || status === 'requesting') && !isMobile) {
      cancelRecording();
      toast.info(t('chat:voice.cancelled'));
    }
  };

  const buttonStyles = getButtonStyles(status, disabled, isMobile);
  
  // 根据状态渲染不同内容
  const renderContent = () => {
    if (isProcessing) {
      return (
        <div className="flex items-center justify-center">
          <Loader2 size={16} className="animate-spin" />
          <span className="sr-only">{t('chat:voice.recognizing')}</span>
        </div>
      );
    }
    
    if (isRecording) {
      return (
        <div className="flex items-center gap-1.5">
          <VoiceWaveform volume={volume} isRecording={isRecording} />
          <span className="text-xs font-medium">{formatDuration(duration)}</span>
        </div>
      );
    }
    
    if (status === 'error') {
      return <MicOff size={16} />;
    }
    
    return <Mic size={16} />;
  };

  // 获取提示文字
  const getTitle = () => {
    if (disabled) return t('chat:voice.unavailable');
    if (isProcessing) return t('chat:voice.recognizing');
    if (isRecording) {
      return isMobile ? t('chat:voice.mobile_stop') : t('chat:voice.desktop_stop');
    }
    if (error) return error;
    return isMobile ? t('chat:voice.mobile_hold') : t('chat:voice.input');
  };

  return (
    <>
      {/* data-testid: voice-input-button - Voice input trigger for voice input tests */}
      <button
        type="button"
        onClick={handleClick}
        onContextMenu={handleContextMenu}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
        onTouchCancel={handleTouchCancel}
        disabled={disabled || isProcessing}
        className={`${buttonStyles} ${className}`}
        title={getTitle()}
        aria-label={getTitle()}
        data-testid="voice-input-button"
      >
        {renderContent()}
        {/* data-testid: recording-indicator - Recording state indicator shown during active recording */}
        {isRecording && <span data-testid="recording-indicator" className="sr-only">{t('chat:voice.recording')}</span>}
      </button>
    </>
  );
};
