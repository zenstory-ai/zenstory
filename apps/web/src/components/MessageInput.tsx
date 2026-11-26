/**
 * @fileoverview MessageInput component - Chat input with suggestions, attachments, and selected skills.
 *
 * Orchestrates subcomponents under `components/chat/` for:
 * - Accessory chips, suggestions, and "/" skill quick-picker (`ChatInputAccessories`)
 * - Suggestion selection/dismissal state (`useChatSuggestions`)
 * - "/" skill command state & keyboard navigation (`useSlashSkillMenu`)
 * - Voice, send, stop (with arming delay), and steering controls (`ComposerActionControls`)
 *
 * @module components/MessageInput
 */
import React, { useState, useRef, useEffect, useCallback, useId } from "react";
import type { KeyboardEvent } from "react";
import { VoiceInputButton } from "./VoiceInputButton";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { useSkillTrigger, type SelectedSkill } from "../contexts/SkillTriggerContext";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { useTranslation } from "react-i18next";
import { logger } from "../lib/logger";
import { MAX_AGENT_MESSAGE_CHARS } from "../lib/agentLimits";
import { toast } from "../lib/toast";
import { ChatInputAccessories } from "./chat/ChatInputAccessories";
import { ComposerActionControls } from "./chat/ComposerActionControls";
import type { SuggestionDisplayState } from "./chat/suggestionHelpers";
import { useChatSuggestions } from "./chat/useChatSuggestions";
import { useSlashSkillMenu } from "./chat/useSlashSkillMenu";

/** How long the stop button ignores presses after it replaces the send button. */
export const STOP_ARM_DELAY_MS = 1500;
const TEXTAREA_MIN_HEIGHT_PX = 64;
const TEXTAREA_AUTO_MAX_HEIGHT_PX = 120;

interface MessageInputProps {
  /** Focus after an explicit cross-panel action without changing or sending the draft. */
  focusRequest?: number;
  /** Send a new turn. `extraMetadata.entry` is "suggestion" when sending a suggestion unchanged. */
  onSend: (message: string, selectedSkillIds: string[], extraMetadata?: Record<string, unknown>) => void;
  /** Disables both editing and sending. */
  disabled?: boolean;
  /** Disables sending while allowing draft typing; defaults to `disabled`. */
  sendDisabled?: boolean;
  /** Called instead of `onSend` when quota/sending is held back, preserving the draft. */
  onBlockedSend?: () => void;
  /** Server refused this message before starting a round; restores sent skills on new `seq`. */
  refusedSend?: { seq: number; message: string; skillIds: string[] } | null;
  /** Callback to cancel an ongoing operation (shows stop button when sendDisabled). */
  onCancel?: () => void;
  /** Stop already requested: disables stop button while waiting. */
  isStopping?: boolean;
  /** Dispatches a steering (follow-up) message while streaming when `canSteer` is true. */
  onSteer?: (message: string) => void | Promise<void>;
  /** Whether steering is currently available (agent streaming + active session). */
  canSteer?: boolean;
  /** Custom placeholder text (defaults to i18n "chat:input.placeholder"). */
  placeholder?: string;
  /** AI-generated context-aware suggestions to display. */
  aiSuggestions?: string[];
  /** Predicate to hide specific suggestion chips. */
  hideSuggestion?: (suggestion: string) => boolean;
  /** Suppress the entire suggestion chips row. */
  hideSuggestions?: boolean;
  /** Controls whether suggestion area is loading, ready with AI, or fallback static. */
  suggestionDisplayState?: SuggestionDisplayState;
  /** Callback to trigger refresh of AI suggestions. */
  onRefreshSuggestions?: () => void;
  /** Whether AI suggestions are currently being refreshed. */
  isRefreshingSuggestions?: boolean;
  /** Layout mode: "auto" grows up to max height; "fill" fills fixed-height parent. */
  layout?: "auto" | "fill";
  /** Skills that match the current context (displayed as chips). */
  matchedSkills?: Array<{ name: string; trigger: string }>;
  /** External draft value for controlled input state. */
  externalDraft?: string;
  /** Callback when draft content changes (for persistence). */
  onDraftChange?: (draft: string) => void;
}

export const MessageInput: React.FC<MessageInputProps> = ({
  onSend,
  focusRequest = 0,
  disabled = false,
  sendDisabled,
  onBlockedSend,
  refusedSend,
  onCancel,
  isStopping = false,
  onSteer,
  canSteer = false,
  placeholder,
  aiSuggestions = [],
  hideSuggestion,
  hideSuggestions = false,
  suggestionDisplayState = aiSuggestions.length > 0 ? "ready" : "fallback",
  onRefreshSuggestions,
  isRefreshingSuggestions = false,
  layout = "auto",
  matchedSkills = [],
  externalDraft,
  onDraftChange,
}) => {
  const { t } = useTranslation(["chat", "common"]);
  const inputLengthErrorId = useId();
  const { isMobile } = useMobileLayout();
  const textareaMinHeightPx = isMobile ? 44 : TEXTAREA_MIN_HEIGHT_PX;
  const effectiveSendDisabled = sendDisabled ?? disabled;
  const inputDisabled = disabled;

  // The stop button takes the send button's place the moment a message goes out,
  // so a double tap on send used to stop the round before anything was written.
  // Ignore it until STOP_ARM_DELAY_MS after it appears.
  const stopShown = Boolean(onCancel) && effectiveSendDisabled;
  const [stopArmed, setStopArmed] = useState(false);
  const [prevStopShown, setPrevStopShown] = useState(stopShown);
  if (prevStopShown !== stopShown) {
    setPrevStopShown(stopShown);
    setStopArmed(false);
  }
  useEffect(() => {
    if (!stopShown) return;
    const timer = setTimeout(() => setStopArmed(true), STOP_ARM_DELAY_MS);
    return () => clearTimeout(timer);
  }, [stopShown]);

  // Steering is offered while the agent is generating: the textarea stays
  // editable and submitting dispatches a follow-up instruction instead of a brand-new turn.
  const steerActive = canSteer && !!onSteer && !inputDisabled;

  const [input, setInputInternal] = useState(externalDraft ?? "");
  const inputRef = useRef(input);
  // 与后端 AgentRequest.message 的 max_length 一致：超限时禁用发送并提示。
  const inputTooLong = input.length > MAX_AGENT_MESSAGE_CHARS;

  const setInput = useCallback(
    (value: string | ((prev: string) => string)) => {
      const next = typeof value === "function" ? value(inputRef.current) : value;
      inputRef.current = next;
      setInputInternal(next);
      onDraftChange?.(next);
    },
    [onDraftChange]
  );

  // Sync from external draft when it changes (e.g. project switch)
  useEffect(() => {
    if (externalDraft !== undefined) {
      inputRef.current = externalDraft;
      setInputInternal(externalDraft);
    }
  }, [externalDraft]);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastFocusRequestRef = useRef(0);
  useEffect(() => {
    if (!focusRequest || focusRequest === lastFocusRequestRef.current || disabled) return;
    const frame = requestAnimationFrame(() => {
      textareaRef.current?.focus();
      lastFocusRequestRef.current = focusRequest;
    });
    return () => cancelAnimationFrame(frame);
  }, [focusRequest, disabled]);

  const isComposingRef = useRef(false);
  const { attachedMaterials, removeMaterial } = useMaterialAttachment();
  const { quotes, removeQuote } = useTextQuote();
  const { selectedSkills, selectSkill, removeSkill, clearSkills } = useSkillTrigger();

  // 追加指令发送中：用于禁用按钮/回车，避免重复提交同一条 steering。
  const [steerPending, setSteerPending] = useState(false);

  const skillMenu = useSlashSkillMenu({
    selectedSkills,
    selectSkill,
    setInput,
    layout,
    textareaRef,
  });

  const suggestions = useChatSuggestions({
    aiSuggestions,
    hideSuggestion,
    hideSuggestions,
    suggestionDisplayState,
    onRefreshSuggestions,
    input,
    inputDisabled,
    effectiveSendDisabled,
    layout,
    selectedSkillsCount: selectedSkills.length,
  });

  const adjustHeight = () => {
    if (layout !== "auto") return;
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, TEXTAREA_AUTO_MAX_HEIGHT_PX)}px`;
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setInput(value);
    adjustHeight();
    skillMenu.syncWithInputValue(value);
  };

  const acceptSuggestion = (suggestion: string) => {
    if (!input) {
      suggestions.noteSuggestionPicked(suggestion, "tab");
      setInput(suggestion);
      textareaRef.current?.focus();
      setTimeout(adjustHeight, 0);
    }
  };

  /**
   * 发送追加指令（steering）。
   * 必须等 onSteer 真正成功之后才清空输入，否则失败时用户敲的指令和草稿都会丢失。
   */
  const handleSteer = async () => {
    const trimmed = input.trim();
    if (!trimmed || !onSteer || steerPending || inputTooLong) return;

    setSteerPending(true);
    try {
      await onSteer(trimmed);
    } catch (error) {
      logger.error("Failed to send steering message", error);
      return;
    } finally {
      setSteerPending(false);
    }

    // 追加指令不携带 selected_skill_ids，所以已选技能 chip 保留给下一轮正式发送。
    setInput("");
    if (textareaRef.current && layout === "auto") {
      textareaRef.current.style.height = "auto";
    }
  };

  const lastSentRef = useRef<{ message: string; skills: SelectedSkill[] } | null>(null);
  const refusedSeq = refusedSend?.seq;
  const refusedSendRef = useRef(refusedSend);
  refusedSendRef.current = refusedSend;
  useEffect(() => {
    const refused = refusedSendRef.current;
    if (refusedSeq === undefined || !refused) return;
    const sent = lastSentRef.current;
    // The same words sent another way (a follow-up, a starter card) carried no skills from here.
    if (!sent || sent.message !== refused.message) return;
    if (sent.skills.map((skill) => skill.id).join() !== refused.skillIds.join()) return;
    lastSentRef.current = null;
    sent.skills.forEach((skill) => selectSkill(skill));
  }, [refusedSeq, selectSkill]);

  const handleSubmit = () => {
    const message = input.trim();
    if (message && onBlockedSend && !effectiveSendDisabled && !inputTooLong) {
      onBlockedSend();
      return;
    }
    if (message && !effectiveSendDisabled && !inputTooLong) {
      const fromSuggestion = suggestions.consumePickedSuggestionMatch(message);
      const skillIds = selectedSkills.map((skill) => skill.id);
      lastSentRef.current = { message, skills: selectedSkills };
      if (fromSuggestion) {
        onSend(message, skillIds, { entry: "suggestion" });
      } else {
        onSend(message, skillIds);
      }
      // 与输入框文本保持一致：发送即清空（onSend 不回报失败）。
      setInput("");
      clearSkills();
      if (textareaRef.current && layout === "auto") {
        textareaRef.current.style.height = "auto";
      }
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // IME 组字期间（拼音/日文输入法）按 Enter 只是确认候选词，不能触发发送等快捷键。
    // Safari 会在 compositionend 之后才派发确认键的 keydown（此时 isComposing 已为
    // false，但 keyCode 仍是 229），所以需要 composition 事件 + isComposing/keyCode 双重判断。
    if (
      isComposingRef.current ||
      e.nativeEvent.isComposing ||
      e.nativeEvent.keyCode === 229
    ) {
      return;
    }

    if (skillMenu.handleSkillMenuKeyDown(e)) {
      return;
    }

    if (e.key === "Tab" && !input && suggestions.displaySuggestions.length > 0) {
      e.preventDefault();
      acceptSuggestion(suggestions.displaySuggestions[0]);
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (steerActive) {
        void handleSteer();
      } else {
        handleSubmit();
      }
    }
  };

  const stopButtonLabel = isStopping
    ? t("chat:input.stopping")
    : stopArmed
      ? t("chat:input.stop")
      : t("chat:input.stopArming", { defaultValue: "正在开始，稍后可停止" });

  const handleCancel = () => {
    if (isStopping) return;
    if (!stopArmed) {
      // Every press gets an answer: the round is just starting, stop works in a moment.
      toast.info(t("chat:input.stopArming", { defaultValue: "正在开始，稍后可停止" }));
      return;
    }
    onCancel?.();
  };

  const handleSuggestionClick = (suggestion: string) => {
    suggestions.noteSuggestionPicked(suggestion, "click");
    setInput(suggestion);
    textareaRef.current?.focus();
  };

  const handleVoiceResult = (text: string) => {
    setInput((prev) => (prev ? `${prev} ${text}` : text));
    textareaRef.current?.focus();
    setTimeout(adjustHeight, 0);
  };

  const displayPlaceholder = steerActive
    ? t("chat:input.steerPlaceholder")
    : (placeholder ?? t("chat:input.placeholder"));
  const isFillLayout = layout === "fill";

  return (
    <div className={isFillLayout ? "flex flex-col gap-2.5 h-full min-h-0" : "flex flex-col gap-2.5"}>
      <ChatInputAccessories
        attachedMaterials={attachedMaterials}
        quotes={quotes}
        matchedSkills={matchedSkills}
        selectedSkills={selectedSkills}
        isFillLayout={isFillLayout}
        isMobile={isMobile}
        onRemoveMaterial={(id) => {
          removeMaterial(id);
          textareaRef.current?.focus();
        }}
        onRemoveQuote={(id) => {
          removeQuote(id);
          textareaRef.current?.focus();
        }}
        onRemoveSkill={(id) => {
          removeSkill(id);
          textareaRef.current?.focus();
        }}
        showSuggestionRow={suggestions.showSuggestionRow}
        isSuggestionLoading={suggestions.isSuggestionLoading}
        displaySuggestions={suggestions.displaySuggestions}
        onSelectSuggestion={handleSuggestionClick}
        onDismissSuggestion={suggestions.dismissSuggestion}
        onRefreshSuggestions={onRefreshSuggestions ? suggestions.refreshSuggestions : undefined}
        isRefreshingSuggestions={isRefreshingSuggestions}
        showSkillMenu={skillMenu.showSkillMenu}
        skillMenuRef={skillMenu.skillMenuRef}
        isLoadingSkills={skillMenu.isLoadingSkills}
        skillsLoadFailed={skillMenu.skillsLoadFailed}
        skillsCount={skillMenu.skillsCount}
        filteredSkills={skillMenu.filteredSkills}
        selectedSkillIndex={skillMenu.selectedSkillIndex}
        skillSelectionFull={skillMenu.skillSelectionFull}
        isSkillPickable={skillMenu.isSkillPickable}
        onPickSkill={skillMenu.pickSkillFromMenu}
        onRetrySkills={() => void skillMenu.loadSkills()}
      />

      {inputTooLong ? (
        <div
          id={inputLengthErrorId}
          className="shrink-0 px-1 text-xs tabular-nums text-[hsl(var(--error-text))] motion-error-shake"
          role="alert"
          data-testid="chat-input-too-long"
        >
          {t("chat:input.tooLong", {
            count: input.length,
            max: MAX_AGENT_MESSAGE_CHARS,
          })}
        </div>
      ) : null}

      {/* Unified capsule composer: compact single-row on phones (preserves keyboard viewport), airy two-row on desktop */}
      <div
        data-testid="chat-input-capsule"
        className={`flex rounded-2xl border bg-[hsl(var(--bg-secondary))] shadow-[0_1px_3px_hsl(0_0%_0%/0.05)] dark:shadow-[0_2px_10px_hsl(0_0%_0%/0.24)] transition-[border-color,box-shadow,background-color] duration-[var(--duration-fast)] ease-[var(--ease-standard)] ${
          isMobile ? "flex-row items-end gap-1 p-1" : "flex-col"
        } ${
          inputTooLong
            ? "border-[hsl(var(--error)/0.65)] ring-1 ring-inset ring-[hsl(var(--error)/0.2)]"
            : "border-[hsl(var(--border-color))] focus-within:border-[hsl(var(--accent-primary)/0.55)] focus-within:ring-1 focus-within:ring-inset focus-within:ring-[hsl(var(--accent-primary)/0.2)]"
        } ${isFillLayout ? "flex-1 min-h-0" : ""}`}
        style={isFillLayout ? { minHeight: "110px" } : undefined}
      >
        {isMobile && (
          <VoiceInputButton
            onResult={handleVoiceResult}
            disabled={inputDisabled}
            className="!border-0 !bg-transparent hover:!bg-[hsl(var(--bg-tertiary))] !rounded-full"
          />
        )}

        {/* data-testid: chat-input - Chat input textarea for chat interaction tests */}
        <textarea
          ref={textareaRef}
          aria-invalid={inputTooLong || undefined}
          aria-describedby={inputTooLong ? inputLengthErrorId : undefined}
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
          disabled={inputDisabled}
          className={`bg-transparent text-sm text-[hsl(var(--text-primary))] placeholder:text-sm placeholder:text-[hsl(var(--text-secondary))] outline-none resize-none overflow-y-auto disabled:opacity-50 ${
            isMobile ? "flex-1 min-w-0 px-2 py-2.5 text-base md:text-sm" : "w-full px-3.5 pt-2.5 pb-1"
          } ${isFillLayout ? "flex-1 min-h-0" : ""}`}
          style={
            isFillLayout
              ? { minHeight: `${textareaMinHeightPx}px` }
              : {
                  minHeight: `${textareaMinHeightPx}px`,
                  maxHeight: `${TEXTAREA_AUTO_MAX_HEIGHT_PX}px`,
                }
          }
          rows={1}
          data-testid="chat-input"
        />

        <ComposerActionControls
          isMobile={isMobile}
          input={input}
          inputDisabled={inputDisabled}
          effectiveSendDisabled={effectiveSendDisabled}
          inputTooLong={inputTooLong}
          steerActive={steerActive}
          steerPending={steerPending}
          hasCancelHandler={Boolean(onCancel)}
          isStopping={isStopping}
          stopArmed={stopArmed}
          stopButtonLabel={stopButtonLabel}
          onVoiceResult={handleVoiceResult}
          onSteer={() => {
            void handleSteer();
          }}
          onCancel={handleCancel}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  );
};
