/**
 * @fileoverview MobileChatInput component - Mobile-optimized chat input with prominent voice input.
 *
 * This component provides a touch-friendly chat input interface optimized for mobile devices:
 * - Large, prominent voice input button (56px) for easy thumb access
 * - Long-press recording support for natural voice input interaction
 * - Auto-resizing textarea with touch-friendly sizing
 * - Material attachments and text quotes display
 * - Draft persistence with external state sync
 * - Simplified single-row layout for narrow viewports
 *
 * @module components/MobileChatInput
 */

import React, { useState, useRef, useEffect, useCallback } from "react";
import type { KeyboardEvent } from "react";
import { Send, X, Mic, MicOff, Loader2, FileText, Quote } from "lucide-react";
import { useVoiceInput } from "../hooks/useVoiceInput";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { useTranslation } from "react-i18next";
import type { VoiceInputStatus } from "../hooks/useVoiceInput";
import { logger } from "../lib/logger";

const TEXTAREA_MIN_HEIGHT_PX = 44; // Larger for mobile touch
const TEXTAREA_MAX_HEIGHT_PX = 120;
const VOICE_BUTTON_SIZE = 56; // Prominent voice button size

/**
 * Props for the MobileChatInput component.
 * Provides a mobile-optimized chat input interface with prominent voice input.
 */
interface MobileChatInputProps {
  /**
   * Callback invoked when the user submits a message.
   * @param message - The message text to send
   */
  onSend: (message: string) => void;
  /** Whether the input is disabled (e.g., during AI response generation) */
  disabled?: boolean;
  /** Callback to cancel an ongoing operation (shows cancel button when disabled) */
  onCancel?: () => void;
  /** Custom placeholder text (defaults to i18n "chat:input.placeholder") */
  placeholder?: string;
  /** External draft state synced from parent (used for cross-device persistence) */
  externalDraft?: string;
  /** Callback when draft content changes (for parent state sync) */
  onDraftChange?: (draft: string) => void;
}

/**
 * Format duration for display in voice recording UI.
 *
 * Converts seconds to MM:SS format with zero-padded seconds.
 *
 * @param seconds - Duration in seconds to format
 * @returns Formatted string in "MM:SS" format
 *
 * @example
 * formatDuration(65) // Returns "1:05"
 * formatDuration(0) // Returns "0:00"
 */
function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

/**
 * Props for the VoiceWaveform component.
 * Displays an animated waveform visualization during voice recording.
 */
interface VoiceWaveformProps {
  /** Current volume level (0-1) that affects bar heights */
  volume: number;
}

/**
 * Voice waveform animation component.
 *
 * Renders a 5-bar animated visualization that responds to audio volume levels.
 * Each bar's height is dynamically calculated based on the current volume,
 * creating a visual representation of the user's voice input.
 *
 * @param props - Component props
 * @param props.volume - Current volume level (0-1) that affects bar heights
 *
 * @example
 * <VoiceWaveform volume={0.75} />
 */
const VoiceWaveform: React.FC<VoiceWaveformProps> = ({ volume }) => {
  const bars = [0.6, 1, 0.7, 0.9, 0.5];

  return (
    <div className="flex items-center gap-1 h-5">
      {bars.map((baseHeight, i) => {
        const height = Math.max(0.3, baseHeight * (0.4 + volume * 0.6));
        return (
          <div
            key={i}
            className="w-1 bg-white rounded-full transition-all duration-75"
            style={{
              height: `${height * 100}%`,
            }}
          />
        );
      })}
    </div>
  );
};

/**
 * Get voice button styles based on recording status and disabled state.
 *
 * Returns a dynamically generated Tailwind CSS class string based on the
 * current voice input status. The button visually indicates:
 * - Disabled: Grayed out, non-interactive appearance
 * - Recording: Red background, scaled up with shadow
 * - Processing: Primary accent color with spinner
 * - Error: Red background with error icon
 * - Idle: Subtle accent background, touch feedback on press
 *
 * @param status - Current voice input status from useVoiceInput hook
 * @param disabled - Whether the button is disabled
 * @returns Tailwind CSS class string for the voice button
 *
 * @example
 * const styles = getVoiceButtonStyles("recording", false);
 * // Returns classes with red background and scale-110
 */
function getVoiceButtonStyles(status: VoiceInputStatus, disabled: boolean): string {
  const baseStyles = `shrink-0 flex items-center justify-center rounded-full transition-all duration-200 select-none touch-manipulation`;

  if (disabled) {
    return `${baseStyles} w-[${VOICE_BUTTON_SIZE}px] h-[${VOICE_BUTTON_SIZE}px] bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] cursor-not-allowed opacity-50`;
  }

  switch (status) {
    case "recording":
      return `${baseStyles} w-[${VOICE_BUTTON_SIZE}px] h-[${VOICE_BUTTON_SIZE}px] bg-[hsl(var(--error))] text-white scale-110 shadow-lg`;
    case "processing":
      return `${baseStyles} w-[${VOICE_BUTTON_SIZE}px] h-[${VOICE_BUTTON_SIZE}px] bg-[hsl(var(--accent-primary))] text-white`;
    case "error":
      return `${baseStyles} w-[${VOICE_BUTTON_SIZE}px] h-[${VOICE_BUTTON_SIZE}px] bg-[hsl(var(--error))] text-white`;
    default:
      return `${baseStyles} w-[${VOICE_BUTTON_SIZE}px] h-[${VOICE_BUTTON_SIZE}px] bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))] active:scale-95`;
  }
}

/**
 * Mobile-optimized chat input component with prominent voice input.
 *
 * A touch-friendly input interface designed specifically for mobile devices.
 * Features a large voice input button (56px) with long-press recording support,
 * auto-resizing textarea, and compact displays for attachments and quotes.
 *
 * Key features:
 * - Prominent voice button positioned at left for easy thumb access
 * - Long-press (200ms) to start recording, release to stop
 * - Auto-resizing textarea (44-120px height)
 * - Material attachment chips with compact display
 * - Text quote chips with truncation
 * - External draft sync for cross-device persistence
 *
 * @param props - Component props
 * @param props.onSend - Handler called when user submits a message
 * @param props.disabled - Whether the input is disabled
 * @param props.onCancel - Handler to cancel ongoing operation
 * @param props.placeholder - Custom placeholder text
 * @param props.externalDraft - External draft state for sync
 * @param props.onDraftChange - Handler when draft content changes
 *
 * @example
 * // Basic usage
 * <MobileChatInput
 *   onSend={(message) => sendMessage(message)}
 *   placeholder="Type a message..."
 * />
 *
 * @example
 * // With draft persistence and cancel support
 * <MobileChatInput
 *   onSend={handleSend}
 *   onCancel={handleCancel}
 *   disabled={isGenerating}
 *   externalDraft={draft}
 *   onDraftChange={setDraft}
 * />
 */
export const MobileChatInput: React.FC<MobileChatInputProps> = ({
  onSend,
  disabled = false,
  onCancel,
  placeholder,
  externalDraft,
  onDraftChange,
}) => {
  const { t } = useTranslation(["chat", "common"]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const longPressTimerRef = useRef<number | null>(null);
  const isLongPressRef = useRef(false);

  const { attachedMaterials, removeMaterial } = useMaterialAttachment();
  const { quotes, removeQuote } = useTextQuote();

  const [input, setInputInternal] = useState(externalDraft ?? "");

  // Wrap setInput to also notify parent of draft changes
  const setInput = useCallback(
    (value: string | ((prev: string) => string)) => {
      setInputInternal((prev) => {
        const next = typeof value === "function" ? value(prev) : value;
        onDraftChange?.(next);
        return next;
      });
    },
    [onDraftChange]
  );

  // Sync from external draft when it changes
  // This is a valid pattern for syncing controlled state from parent
  useEffect(() => {
    if (externalDraft !== undefined) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setInputInternal(externalDraft);
    }
  }, [externalDraft]);

  // Voice input hook
  const {
    status: voiceStatus,
    isRecording,
    isProcessing,
    duration,
    volume,
    error: voiceError,
    startRecording,
    stopRecording,
    cancelRecording,
    isSupported: isVoiceSupported,
  } = useVoiceInput({
    onResult: (text) => {
      setInput((prev) => (prev ? `${prev} ${text}` : text));
      textareaRef.current?.focus();
      setTimeout(adjustHeight, 0);
    },
    onError: (err) => {
      logger.error("Voice input error:", err);
    },
    maxDuration: 55,
  });

  // Cleanup long press timer on unmount
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
    if (voiceStatus === 'requesting' || voiceStatus === 'recording' || voiceStatus === 'processing') cancelRecording();
  }, [disabled, voiceStatus, cancelRecording]);

  // Auto-resize textarea
  const adjustHeight = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, TEXTAREA_MAX_HEIGHT_PX)}px`;
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    adjustHeight();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // IME 组字期间（拼音/日文输入法）按 Enter 只是确认候选词，不能触发发送。
    // Safari 会在 compositionend 之后才派发确认键的 keydown（此时 isComposing 已为
    // false，但 keyCode 仍是 229），所以需要 composition 事件 + isComposing/keyCode 双重判断。
    if (
      isComposingRef.current ||
      e.nativeEvent.isComposing ||
      e.nativeEvent.keyCode === 229
    ) {
      return;
    }

    // Enter sends message
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (trimmed && !disabled) {
      onSend(trimmed);
      setInput("");
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  };

  const handleCancel = () => {
    onCancel?.();
  };

  // Voice button touch handlers for long-press recording
  const handleVoiceTouchStart = async (e: React.TouchEvent) => {
    if (disabled || isProcessing || voiceStatus === 'requesting') return;

    e.preventDefault();
    isLongPressRef.current = false;

    // 200ms to determine long press
    longPressTimerRef.current = window.setTimeout(async () => {
      longPressTimerRef.current = null;
      isLongPressRef.current = true;
      await startRecording();
    }, 200);
  };

  const handleVoiceTouchEnd = (e: React.TouchEvent) => {
    if (disabled) return;

    e.preventDefault();

    // Clear long press timer
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }

    // Releasing while permission is pending must invalidate the recording too.
    if (isLongPressRef.current) {
      if (isRecording) stopRecording();
      else cancelRecording();
    }

    isLongPressRef.current = false;
  };

  const handleVoiceTouchCancel = () => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }

    if (isLongPressRef.current) {
      cancelRecording();
    }

    isLongPressRef.current = false;
  };

  // Render voice button content based on status
  const renderVoiceContent = () => {
    if (isProcessing) {
      return (
        <div className="flex items-center justify-center">
          <Loader2 size={24} className="animate-spin" />
          <span className="sr-only">{t("chat:voice.recognizing")}</span>
        </div>
      );
    }

    if (isRecording) {
      return (
        <div className="flex flex-col items-center gap-1">
          <VoiceWaveform volume={volume} />
          <span className="text-xs font-medium">{formatDuration(duration)}</span>
        </div>
      );
    }

    if (voiceStatus === "error") {
      return <MicOff size={24} />;
    }

    return <Mic size={24} />;
  };

  // Get voice button title
  const getVoiceTitle = () => {
    if (disabled) return t("chat:voice.unavailable");
    if (isProcessing) return t("chat:voice.recognizing");
    if (isRecording) return t("chat:voice.mobile_stop");
    if (voiceError) return voiceError;
    return t("chat:voice.mobile_hold");
  };

  const displayPlaceholder = placeholder ?? t("chat:input.placeholder");

  return (
    <div className="flex flex-col gap-2 px-3 py-2 bg-[hsl(var(--bg-primary))] border-t border-[hsl(var(--border-color))] pb-safe-or-2">
      {/* Attached materials display - compact for mobile */}
      {attachedMaterials.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t("chat:input.attachedMaterials")}
          </span>
          {attachedMaterials.map((material) => (
            <span
              key={material.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))] rounded text-xs"
            >
              <FileText size={12} />
              <span className="max-w-[80px] truncate">{material.title}</span>
              <button
                onClick={() => removeMaterial(material.id)}
                className="hover:text-[hsl(var(--error))] transition-colors p-0.5 -mr-0.5"
                title={t("chat:input.remove")}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Quoted text display - compact for mobile */}
      {quotes.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t("chat:input.quotedText")}
          </span>
          {quotes.map((quote) => (
            <span
              key={quote.id}
              className="inline-flex items-center gap-1 px-2 py-0.5 bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))] rounded text-xs"
              title={quote.text}
            >
              <Quote size={12} />
              <span className="max-w-[100px] truncate">
                {quote.text.length > 30 ? `${quote.text.slice(0, 30)}...` : quote.text}
              </span>
              <button
                onClick={() => removeQuote(quote.id)}
                className="hover:text-[hsl(var(--error))] transition-colors p-0.5 -mr-0.5"
                title={t("chat:input.remove")}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Main input row */}
      <div className="flex items-end gap-2">
        {/* Prominent Voice Input Button */}
        {isVoiceSupported && (
          <button
            type="button"
            onTouchStart={handleVoiceTouchStart}
            onTouchEnd={handleVoiceTouchEnd}
            onTouchCancel={handleVoiceTouchCancel}
            disabled={disabled || isProcessing}
            className={getVoiceButtonStyles(voiceStatus, disabled)}
            title={getVoiceTitle()}
            aria-label={getVoiceTitle()}
            data-testid="mobile-voice-input-button"
            style={{
              width: `${VOICE_BUTTON_SIZE}px`,
              height: `${VOICE_BUTTON_SIZE}px`,
            }}
          >
            {renderVoiceContent()}
            {isRecording && (
              <span data-testid="recording-indicator" className="sr-only">
                {t('chat:voice.recording')}
              </span>
            )}
          </button>
        )}

        {/* Text input area */}
        <div className="flex-1 min-w-0 flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => {
              isComposingRef.current = true;
            }}
            onCompositionEnd={() => {
              isComposingRef.current = false;
            }}
            placeholder={displayPlaceholder}
            disabled={disabled}
            className="flex-1 bg-[hsl(var(--bg-tertiary))] rounded-2xl px-4 py-3 text-base text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary)/0.5)] outline-none resize-none overflow-y-auto transition-all disabled:opacity-50 focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.3)]"
            style={{
              minHeight: `${TEXTAREA_MIN_HEIGHT_PX}px`,
              maxHeight: `${TEXTAREA_MAX_HEIGHT_PX}px`,
            }}
            rows={1}
            data-testid="mobile-chat-input"
          />

          {/* Send/Cancel button */}
          {onCancel && disabled ? (
            <button
              onClick={handleCancel}
              className="shrink-0 w-11 h-11 flex items-center justify-center bg-[hsl(var(--error))] active:bg-[hsl(var(--error)/0.9)] text-white rounded-full transition-colors touch-manipulation"
              title={t("common:cancel")}
            >
              <X size={18} />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || disabled}
              className="shrink-0 w-11 h-11 flex items-center justify-center bg-[hsl(var(--accent-primary))] active:bg-[hsl(var(--accent-dark))] disabled:bg-[hsl(var(--bg-tertiary))] disabled:cursor-not-allowed text-white rounded-full transition-colors touch-manipulation disabled:opacity-50"
              title={t("common:send")}
              data-testid="mobile-send-button"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>

      {/* Voice input hint - shown when idle */}
      {isVoiceSupported && !isRecording && !isProcessing && voiceStatus === "idle" && !input && (
        <p className="text-center text-xs text-[hsl(var(--text-secondary)/0.7)]">
          {t("chat:voice.mobile_hold")}
        </p>
      )}
    </div>
  );
};
