/**
 * @fileoverview VoiceInputButton component - Voice input control with cross-platform support.
 *
 * This component provides a voice input button with platform-specific interaction patterns:
 * - Mouse/keyboard (any width) and touch on wide screens: click to start/stop recording
 * - Touch on phone-width screens: press and hold to record, release to transcribe
 * - Visual waveform feedback during recording
 * - Toast notifications for errors and cancellation
 * - Browser support detection (graceful degradation)
 *
 * @module components/VoiceInputButton
 */

import React, { useRef, useEffect, useState } from 'react';
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
    <div data-testid="voice-waveform" className="flex items-center gap-0.5 h-4">
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
  // Children never receive pointer events: the press target must stay the
  // button while its icon is swapped for the waveform. touch-action/callout
  // keep a hold from scrolling the page or opening the system callout.
  const baseStyles = 'shrink-0 flex items-center justify-center rounded-lg transition-all duration-200 select-none touch-none [-webkit-touch-callout:none] [&_*]:pointer-events-none';
  const squareSize = isMobile ? 'w-11 h-11' : 'w-9 h-9';
  const recordingSize = isMobile ? 'min-w-[96px] h-11 px-3' : 'min-w-[80px] h-9 px-2.5';
  
  if (disabled) {
    return `${baseStyles} ${squareSize} bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] cursor-not-allowed opacity-50`;
  }
  
  switch (status) {
    case 'recording':
      return `${baseStyles} ${recordingSize} bg-[hsl(var(--error))] text-white ${isMobile ? '' : 'hover:bg-[hsl(var(--error)/0.9)]'}`;
    case 'requesting':
      return `${baseStyles} ${squareSize} bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]`;
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
 * **Click interaction (mouse, keyboard, and touch on wide screens):**
 * - Click (or Enter/Space) to start recording
 * - Click again to stop; right-click cancels
 * - Visual waveform shows recording progress
 *
 * **Hold-to-talk (touch on phone-width screens):**
 * - Press and hold (200ms threshold) starts recording; a shorter tap shows a hint
 * - Release to stop and transcribe; the pointer is captured, so the release is
 *   delivered even after the icon is replaced by the waveform
 * - Releasing while the microphone permission is still pending records nothing
 *   and asks the user to hold again once the microphone is ready
 * - A system-cancelled touch cancels an active recording; while the permission
 *   request is pending it is treated like a release
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
  /** Touch pointer of the current hold-to-talk press, once its hold started recording. */
  const holdPointerRef = useRef<number | null>(null);
  const isLongPressRef = useRef(false);
  /** The click that browsers synthesize after a hold-to-talk touch must not toggle. */
  const suppressClickRef = useRef(false);
  const [interaction, setInteraction] = useState<'hold' | 'toggle'>('toggle');
  
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
    releaseRecording,
    cancelPress,
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
    onReleasedBeforeStart: () => {
      toast.info(t('chat:voice.permissionReady'));
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
    holdPointerRef.current = null;
    if (status === 'requesting' || status === 'recording' || status === 'processing') cancelRecording();
  }, [disabled, status, cancelRecording]);

  // 不支持录音的浏览器不显示按钮
  if (!isSupported) {
    return null;
  }

  const holdToTalk = (e: React.PointerEvent) => isMobile && e.pointerType === 'touch';

  // Click/keyboard toggle; works at every width for mouse, pen and keyboard.
  const handleClick = async (e: React.MouseEvent) => {
    // A keyboard activation (detail 0) is never the tail of a touch press.
    if (suppressClickRef.current && e.detail !== 0) {
      suppressClickRef.current = false;
      return;
    }
    suppressClickRef.current = false;
    if (disabled) return;

    if (status === 'requesting') {
      cancelRecording();
      toast.info(t('chat:voice.cancelled'));
    } else if (isRecording) {
      stopRecording();
    } else if (!isProcessing) {
      setInteraction('toggle');
      await startRecording();
    }
  };

  // Phone-width touch: press and hold to talk.
  const handlePointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!holdToTalk(e)) {
      suppressClickRef.current = false;
      return;
    }
    suppressClickRef.current = true;
    if (!e.isPrimary || disabled || isProcessing) return;

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Capture is best effort; children ignore pointer events anyway.
    }
    if (longPressTimerRef.current !== null) clearTimeout(longPressTimerRef.current);
    isLongPressRef.current = false;
    holdPointerRef.current = e.pointerId;

    longPressTimerRef.current = window.setTimeout(() => {
      longPressTimerRef.current = null;
      isLongPressRef.current = true;
      setInteraction('hold');
      void startRecording();
    }, 200);
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!holdToTalk(e) || holdPointerRef.current !== e.pointerId) return;
    holdPointerRef.current = null;

    if (longPressTimerRef.current !== null) {
      // Released before the hold threshold: explain the gesture.
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
      if (!disabled) toast.info(t('chat:voice.mobile_hold'));
    } else if (isLongPressRef.current) {
      // Stops a recording, defers a pending permission request, and leaves an
      // auto-stopped recording's recognition alone.
      releaseRecording();
    }
    isLongPressRef.current = false;
  };

  const handlePointerCancel = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!holdToTalk(e) || holdPointerRef.current !== e.pointerId) return;
    holdPointerRef.current = null;
    if (longPressTimerRef.current !== null) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
    // Decided from the hook's refs: a permission prompt that steals the touch
    // behaves like a release, so the grant still ends in the "ready" hint.
    if (isLongPressRef.current && cancelPress()) {
      toast.info(t('chat:voice.cancelled'));
    }
    isLongPressRef.current = false;
  };

  // Right-click cancels a click-started recording (a touch long-press may also
  // raise contextmenu, which must not cancel a hold).
  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    if (interaction === 'toggle' && holdPointerRef.current === null && (isRecording || status === 'requesting')) {
      cancelRecording();
      toast.info(t('chat:voice.cancelled'));
    }
  };

  const buttonStyles = getButtonStyles(status, disabled, isMobile);
  
  // 根据状态渲染不同内容
  const renderContent = () => {
    if (isProcessing) {
      return (
        <div data-testid="voice-processing-indicator" className="flex items-center justify-center">
          <Loader2 size={16} className="animate-spin" />
          <span className="sr-only">{t('chat:voice.recognizing')}</span>
        </div>
      );
    }
    
    if (isRecording) {
      return (
        <div className="flex items-center gap-1.5">
          <VoiceWaveform volume={volume} isRecording={isRecording} />
          <span data-testid="voice-recording-duration" className="text-xs font-medium">{formatDuration(duration)}</span>
        </div>
      );
    }
    
    if (status === 'requesting') {
      return <Mic size={16} className="animate-pulse" />;
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
    if (status === 'requesting') return t('chat:voice.requesting');
    if (isRecording) {
      return interaction === 'hold' ? t('chat:voice.mobile_stop') : t('chat:voice.desktop_stop');
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
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
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
