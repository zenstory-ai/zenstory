/**
 * @fileoverview MessageInput component - Chat input with suggestions, attachments, and selected skills.
 *
 * This component provides a rich input interface for AI chat, handling:
 * - Auto-resizing textarea with keyboard shortcuts
 * - Project-aware suggestion display with fallback behavior
 * - Swipe-to-dismiss suggestion chips on mobile
 * - Material attachments and text quotes
 * - Explicitly selected skills shown as removable chips (sent as selected_skill_ids)
 * - Skill quick picker menu ("/" prefix) that adds a skill chip
 * - Voice input integration
 * - Draft persistence with external state sync
 *
 * @module components/MessageInput
 */
import React, { useState, useRef, useEffect, useMemo, useCallback } from "react";
import type { KeyboardEvent } from "react";
import { Send, X, Square, RefreshCw, FileText, Quote, Zap, Sparkles } from "lucide-react";
import { VoiceInputButton } from "./VoiceInputButton";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { MAX_SELECTED_SKILLS, useSkillTrigger } from "../contexts/SkillTriggerContext";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { useTranslation } from "react-i18next";
import { skillsApi } from "../lib/api";
import type { Skill } from "../types";
import { useSwipeGestures } from "../hooks/useGestures";
import { logger } from "../lib/logger";
import { MAX_AGENT_MESSAGE_CHARS } from "../lib/agentLimits";
import { trackEvent } from "../lib/analytics";
import { toast } from "../lib/toast";

/**
 * Randomly selects a specified number of distinct suggestions from a pool.
 * Uses Fisher-Yates shuffle for unbiased random selection.
 *
 * @param pool - Array of suggestion strings to choose from
 * @param count - Maximum number of suggestions to return
 * @returns Array of randomly selected suggestions (may be fewer than count if pool is small)
 *
 * @example
 * const suggestions = getRandomSuggestions(['Help me write', 'Continue story', 'Fix grammar'], 2);
 * // Returns 2 random suggestions from the pool
 */
function getRandomSuggestions(pool: string[], count: number): string[] {
  if (!pool.length) return [];
  const shuffled = [...pool].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, Math.min(count, shuffled.length));
}

type SuggestionDisplayState = "loading" | "ready" | "fallback";

function getSuggestionsToDisplay(
  staticSuggestions: string[],
  aiSuggestions: string[],
  suggestionDisplayState: SuggestionDisplayState
): string[] {
  if (suggestionDisplayState === "ready" && aiSuggestions.length > 0) {
    return aiSuggestions.slice(0, 3);
  }
  if (suggestionDisplayState === "fallback") {
    return staticSuggestions.slice(0, 3);
  }
  return [];
}

/** How long the stop button ignores presses after it replaces the send button. */
export const STOP_ARM_DELAY_MS = 1500;
/** Minimum textarea height in pixels */
const TEXTAREA_MIN_HEIGHT_PX = 64;
/** Maximum textarea height in auto-layout mode before scrolling */
const TEXTAREA_AUTO_MAX_HEIGHT_PX = 120;

/**
 * Props for the SwipeableSuggestionChip component.
 * Displays a suggestion that can be tapped to use or swiped to dismiss.
 */
interface SwipeableSuggestionChipProps {
  /** The suggestion text to display */
  suggestion: string;
  /** Callback when the chip is clicked/tapped */
  onClick: () => void;
  /** Callback when the chip is dismissed via swipe gesture */
  onDismiss: () => void;
  /** Whether to use mobile-optimized styling */
  isMobile: boolean;
}

/**
 * A suggestion chip component with swipe-to-dismiss support for mobile.
 *
 * Renders as a pill-shaped button that can be:
 * - Tapped to insert the suggestion into the input
 * - Swiped left or right to dismiss (mobile only)
 *
 * Features smooth animations for both dismiss and tap interactions.
 *
 * @param props - Component props
 * @param props.suggestion - The suggestion text to display
 * @param props.onClick - Handler for click/tap events
 * @param props.onDismiss - Handler called after swipe-dismiss animation completes
 * @param props.isMobile - Whether to enable swipe gestures
 *
 * @example
 * <SwipeableSuggestionChip
 *   suggestion="Help me write"
 *   onClick={() => setInput("Help me write")}
 *   onDismiss={() => dismissSuggestion("Help me write")}
 *   isMobile={true}
 * />
 */
const SwipeableSuggestionChip: React.FC<SwipeableSuggestionChipProps> = ({
  suggestion,
  onClick,
  onDismiss,
  isMobile,
}) => {
  /** Tracks whether the chip is in dismiss animation state */
  const [isDismissing, setIsDismissing] = useState(false);
  /** Horizontal translation for swipe animation */
  const [translateX, setTranslateX] = useState(0);

  const { bind } = useSwipeGestures(
    (e) => {
      // Dismiss on left or right swipe
      if (e.direction === "left" || e.direction === "right") {
        setIsDismissing(true);
        // Animate out in swipe direction
        setTranslateX(e.direction === "left" ? -150 : 150);
        // Remove after animation
        setTimeout(() => {
          onDismiss();
        }, 200);
      }
    },
    {
      swipeThreshold: 40,
      swipeTimeout: 250,
      swipeVelocity: 0.2,
      enableVerticalSwipe: false,
      preventDefaultTouch: false, // Allow scroll to work
    }
  );

  return (
    <button
      {...bind()}
      onClick={onClick}
      aria-label={suggestion}
      className={`shrink-0 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-tertiary)/0.8)] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] rounded-full text-xs whitespace-nowrap transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${isMobile ? 'px-2 py-0.5' : 'px-2.5 py-1'} ${isDismissing ? 'opacity-0 h-0 py-0 my-0 overflow-hidden' : ''}`}
      style={{
        transform: `translateX(${translateX}px)`,
        transition: isDismissing ? 'transform 0.2s ease-out, opacity 0.2s ease-out' : 'none',
      }}
    >
      {suggestion}
    </button>
  );
};

/**
 * Props for the MessageInput component.
 * Provides a rich chat input interface with suggestions, attachments, and selected skills.
 */
interface MessageInputProps {
  /**
   * Callback invoked when the user submits a message.
   * @param message - The message text
   * @param selectedSkillIds - Ids of the skills the user explicitly selected (chips), max 3
   */
  /**
   * Send a new turn. `extraMetadata.entry` is "suggestion" when the author sends a
   * suggestion chip unchanged.
   */
  onSend: (message: string, selectedSkillIds: string[], extraMetadata?: Record<string, unknown>) => void;
  /**
   * Whether the input is disabled (e.g., when the panel is unavailable).
   * This disables both editing and sending.
   */
  disabled?: boolean;
  /**
   * Whether sending is disabled (e.g., during AI response generation),
   * while still allowing the user to type a draft message.
   *
   * When omitted, defaults to `disabled`.
   */
  sendDisabled?: boolean;
  /** Callback to cancel an ongoing operation (shows cancel button when disabled) */
  onCancel?: () => void;
  /** Stop already requested: the stop button shows it is waiting and ignores presses */
  isStopping?: boolean;
  /**
   * Callback to send a steering (follow-up) message while the agent is
   * streaming. When provided together with `canSteer`, typing during
   * generation and sending dispatches a steering message instead of a new turn.
   */
  onSteer?: (message: string) => void | Promise<void>;
  /** Whether steering is currently available (agent streaming + active session). */
  canSteer?: boolean;
  /** Custom placeholder text (defaults to i18n "chat:input.placeholder") */
  placeholder?: string;
  /** AI-generated context-aware suggestions to display */
  aiSuggestions?: string[];
  /** Current conversation message count (reserved for compatibility/analytics) */
  messageCount?: number;
  /** Controls whether suggestion area is loading, ready with AI, or fallback static */
  suggestionDisplayState?: SuggestionDisplayState;
  /** Callback to trigger refresh of AI suggestions */
  onRefreshSuggestions?: () => void;
  /** Whether AI suggestions are currently being refreshed */
  isRefreshingSuggestions?: boolean;
  /**
   * Layout mode for the textarea:
   * - "auto": Textarea auto-grows up to max height (default)
   * - "fill": Textarea fills available space in fixed-height parent
   */
  layout?: "auto" | "fill";
  /** Skills that match the current context (displayed as chips) */
  matchedSkills?: Array<{ name: string; trigger: string }>;
  /** External draft value for controlled input state */
  externalDraft?: string;
  /** Callback when draft content changes (for persistence) */
  onDraftChange?: (draft: string) => void;

  /**
   * Agent generation mode.
   *
   * - fast: prioritize speed
   * - quality: prioritize output quality (may take longer)
   */
  generationMode?: "fast" | "quality";

  /** Callback invoked when the user changes generation mode. */
  onGenerationModeChange?: (mode: "fast" | "quality") => void;
}

/**
 * Rich chat input component with suggestions, attachments, and selected skills.
 *
 * Features:
 * - Auto-resizing textarea with Enter-to-send and Shift+Enter for newline
 * - Project-aware suggestion system (loading → AI suggestions → static fallback)
 * - Swipe-to-dismiss suggestions on mobile devices
 * - Material attachment display from MaterialAttachmentContext
 * - Text quote display from TextQuoteContext
 * - Selected skill chips from SkillTriggerContext (removable, cleared on send)
 * - Skill quick picker menu (type "/" to search and select skills as chips)
 * - Voice input integration via VoiceInputButton
 * - Draft persistence with external state synchronization
 *
 * Keyboard Shortcuts:
 * - Enter: Send message
 * - Shift+Enter: Newline
 * - Tab: Accept first suggestion (when input is empty)
 * - "/" + type: Open skill search menu
 * - Arrow Up/Down: Navigate skill menu
 * - Escape: Close skill menu
 *
 * @param props - Component props (see MessageInputProps)
 * @returns The rendered message input component
 *
 * @example
 * // Basic usage
 * <MessageInput
 *   onSend={(msg) => sendMessage(msg)}
 *   placeholder="Ask anything..."
 * />
 *
 * @example
 * // With AI suggestions and draft persistence
 * <MessageInput
 *   onSend={handleSend}
 *   aiSuggestions={['Continue story', 'Add dialogue']}
 *   messageCount={5}
 *   onRefreshSuggestions={refreshAI}
 *   externalDraft={savedDraft}
 *   onDraftChange={saveDraft}
 * />
 *
 * @example
 * // During AI response (disabled with cancel)
 * <MessageInput
 *   onSend={handleSend}
 *   disabled={true}
 *   onCancel={cancelGeneration}
 * />
 */
export const MessageInput: React.FC<MessageInputProps> = ({
  onSend,
  disabled = false,
  sendDisabled,
  onCancel,
  isStopping = false,
  onSteer,
  canSteer = false,
  placeholder,
  aiSuggestions = [],
  suggestionDisplayState = aiSuggestions.length > 0 ? "ready" : "fallback",
  onRefreshSuggestions,
  isRefreshingSuggestions = false,
  layout = "auto",
  matchedSkills = [],
  externalDraft,
  onDraftChange,
  generationMode = "quality",
  onGenerationModeChange,
}) => {
  const { t } = useTranslation(["chat", "common"]);
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
  // The suggestion the author last put into the box; sent unchanged → entry "suggestion".
  const pickedSuggestionRef = useRef<string | null>(null);
  const noteSuggestionPicked = (suggestion: string, via: "click" | "tab") => {
    pickedSuggestionRef.current = suggestion;
    trackEvent("ai_suggestion_picked", {
      via,
      index: aiSuggestions.indexOf(suggestion),
      source: suggestionDisplayState,
    });
  };
  // Steering is offered while the agent is generating: the textarea stays
  // editable and submitting dispatches a follow-up instruction instead of a
  // brand-new turn.
  const steerActive = canSteer && !!onSteer && !inputDisabled;

  const allStaticSuggestions = useMemo(() => {
    const raw = t("chat:input.staticSuggestions", { returnObjects: true }) as unknown;
    if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
    return [];
  }, [t]);

  const [input, setInputInternal] = useState(externalDraft ?? "");
  // 与后端 AgentRequest.message 的 max_length 一致：超限时禁用发送并提示。
  const inputTooLong = input.length > MAX_AGENT_MESSAGE_CHARS;
  const [staticSuggestions, setStaticSuggestions] = useState<string[]>(() =>
    getRandomSuggestions(allStaticSuggestions, 3)
  );

  // Track dismissed suggestions for swipe-to-dismiss
  const [dismissedSuggestions, setDismissedSuggestions] = useState<Set<string>>(new Set());

  // Wrap setInput to also notify parent of draft changes
  const setInput = useCallback((value: string | ((prev: string) => string)) => {
    setInputInternal((prev) => {
      const next = typeof value === 'function' ? value(prev) : value;
      onDraftChange?.(next);
      return next;
    });
  }, [onDraftChange]);

  // Sync from external draft when it changes (e.g. project switch)
  useEffect(() => {
    if (externalDraft !== undefined) {
      setInputInternal(externalDraft);
    }
  }, [externalDraft]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const { attachedMaterials, removeMaterial } = useMaterialAttachment();
  const { quotes, removeQuote } = useTextQuote();
  const { selectedSkills, selectSkill, removeSkill, clearSkills } = useSkillTrigger();

  // 追加指令发送中：用于禁用按钮/回车，避免重复提交同一条 steering。
  const [steerPending, setSteerPending] = useState(false);

  // Skill quick trigger state
  const [showSkillMenu, setShowSkillMenu] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillSearchQuery, setSkillSearchQuery] = useState("");
  const [selectedSkillIndex, setSelectedSkillIndex] = useState(0);
  const [isLoadingSkills, setIsLoadingSkills] = useState(false);
  const skillMenuRef = useRef<HTMLDivElement>(null);

  // Reset static suggestions when language changes
  useEffect(() => {
    setStaticSuggestions(getRandomSuggestions(allStaticSuggestions, 3));
  }, [allStaticSuggestions]);

  const baseDisplaySuggestions = getSuggestionsToDisplay(
    staticSuggestions,
    aiSuggestions,
    suggestionDisplayState,
  );

  // Filter out dismissed suggestions
  const displaySuggestions = useMemo(
    () => baseDisplaySuggestions.filter(s => !dismissedSuggestions.has(s)),
    [baseDisplaySuggestions, dismissedSuggestions]
  );
  const isSuggestionLoading = suggestionDisplayState === "loading";
  const showSuggestionRow =
    !input &&
    !inputDisabled &&
    !effectiveSendDisabled &&
    !(layout === "fill" && selectedSkills.length > 0) &&
    (isSuggestionLoading || suggestionDisplayState === "fallback" || displaySuggestions.length > 0);

  // Load skills when menu is shown
  const loadSkills = useCallback(async () => {
    if (skills.length > 0) return; // Already loaded
    setIsLoadingSkills(true);
    try {
      const response = await skillsApi.list();
      setSkills(response.skills.filter(s => s.is_active));
    } catch (error) {
      logger.error("Failed to load skills:", error);
    } finally {
      setIsLoadingSkills(false);
    }
  }, [skills.length]);

  // Filter skills based on search query
  const filteredSkills = useMemo(() => {
    if (!skillSearchQuery) return skills;
    const query = skillSearchQuery.toLowerCase();
    return skills.filter(skill =>
      skill.name.toLowerCase().includes(query) ||
      skill.triggers.some(t => t.toLowerCase().includes(query)) ||
      (skill.description?.toLowerCase().includes(query))
    );
  }, [skills, skillSearchQuery]);

  // 已选满 MAX_SELECTED_SKILLS 个时，菜单里未选中的技能不可再选（否则 selectSkill 静默忽略、
  // 输入却被清空，用户看不出发生了什么）；已选中的仍可点，等价于保持原样。
  const skillSelectionFull = selectedSkills.length >= MAX_SELECTED_SKILLS;
  const isSkillPickable = useCallback(
    (skill: Skill) => !skillSelectionFull || selectedSkills.some((selected) => selected.id === skill.id),
    [selectedSkills, skillSelectionFull],
  );

  // Handle skill selection from the "/" menu: add a chip and drop the "/query" text
  const pickSkillFromMenu = useCallback((skill: Skill) => {
    if (!isSkillPickable(skill)) {
      return;
    }
    selectSkill({ id: skill.id, name: skill.name });
    setInput("");
    setShowSkillMenu(false);
    setSkillSearchQuery("");
    setSelectedSkillIndex(0);
    textareaRef.current?.focus();
    if (layout === "auto" && textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [isSkillPickable, layout, selectSkill, setInput]);

  // Close skill menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (skillMenuRef.current && !skillMenuRef.current.contains(e.target as Node)) {
        setShowSkillMenu(false);
        setSkillSearchQuery("");
        setSelectedSkillIndex(0);
      }
    };
    if (showSkillMenu) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showSkillMenu]);

  // Dismiss a suggestion (swipe-to-dismiss)
  const dismissSuggestion = useCallback((suggestion: string) => {
    setDismissedSuggestions(prev => {
      const next = new Set(prev);
      next.add(suggestion);
      return next;
    });
  }, []);

  // 刷新提示
  const refreshSuggestions = () => {
    setDismissedSuggestions(new Set());
    onRefreshSuggestions?.();
  };

  // Auto-resize textarea (only in auto layout)
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

    // Check for skill trigger: input starts with "/"
    if (value.startsWith("/")) {
      const query = value.slice(1); // Remove the "/" prefix
      setSkillSearchQuery(query);
      setSelectedSkillIndex(0);
      if (!showSkillMenu) {
        setShowSkillMenu(true);
        loadSkills();
      }
    } else {
      if (showSkillMenu) {
        setShowSkillMenu(false);
        setSkillSearchQuery("");
        setSelectedSkillIndex(0);
      }
    }
  };

  // 采纳建议（第一条 AI 建议或静态建议）
  const acceptSuggestion = (suggestion: string) => {
    if (!input) {
      noteSuggestionPicked(suggestion, "tab");
      setInput(suggestion);
      textareaRef.current?.focus();
      setTimeout(adjustHeight, 0);
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

    // Handle skill menu navigation
    if (showSkillMenu && filteredSkills.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedSkillIndex(prev =>
          prev < filteredSkills.length - 1 ? prev + 1 : 0
        );
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedSkillIndex(prev =>
          prev > 0 ? prev - 1 : filteredSkills.length - 1
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pickSkillFromMenu(filteredSkills[selectedSkillIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setShowSkillMenu(false);
        setSkillSearchQuery("");
        setSelectedSkillIndex(0);
        setInput("");
        return;
      }
    }

    // Tab 键：技能菜单选择或采纳建议
    if (e.key === "Tab") {
      if (showSkillMenu && filteredSkills.length > 0) {
        // 技能菜单已打开 - Tab 用于导航（已在上面处理）
        return;
      }
      if (!input && displaySuggestions.length > 0) {
        // 无输入且有建议 - Tab 采纳第一条建议
        e.preventDefault();
        acceptSuggestion(displaySuggestions[0]);
        return;
      }
    }
    // Enter 发送（生成中且可追加时，发送补充指令）
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (steerActive) {
        void handleSteer();
      } else {
        handleSubmit();
      }
    }
  };

  /**
   * 发送追加指令（steering）。
   *
   * 必须等 onSteer 真正成功之后才清空输入：setInput 会同步把草稿
   * （localStorage 里的 drafts[projectId]）覆写成空串，一旦失败，
   * 用户敲的这段指令在 textarea 和持久化草稿里都不复存在，只能凭记忆重打。
   */
  const handleSteer = async () => {
    const trimmed = input.trim();
    if (!trimmed || !onSteer || steerPending || inputTooLong) return;

    setSteerPending(true);
    try {
      await onSteer(trimmed);
    } catch (error) {
      // 失败时保留输入，用户可直接重试（提示由上层 toast 负责）。
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

  const handleSubmit = () => {
    const message = input.trim();
    if (message && !effectiveSendDisabled && !inputTooLong) {
      const fromSuggestion = pickedSuggestionRef.current?.trim() === message;
      pickedSuggestionRef.current = null;
      const skillIds = selectedSkills.map((skill) => skill.id);
      if (fromSuggestion) {
        onSend(message, skillIds, { entry: "suggestion" });
      } else {
        onSend(message, skillIds);
      }
      // 与输入框文本保持一致：发送即清空（onSend 不回报失败）。
      setInput("");
      clearSkills();
      // Reset height (auto layout only)
      if (textareaRef.current && layout === "auto") {
        textareaRef.current.style.height = "auto";
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

  // 点击提示填充到输入框
  const handleSuggestionClick = (suggestion: string) => {
    noteSuggestionPicked(suggestion, "click");
    setInput(suggestion);
    textareaRef.current?.focus();
  };

  // 语音识别结果回调
  const handleVoiceResult = (text: string) => {
    // 将识别结果追加到输入框（如果已有内容则追加）
    setInput((prev) => {
      const newText = prev ? `${prev} ${text}` : text;
      return newText;
    });
    textareaRef.current?.focus();
    // 调整高度
    setTimeout(adjustHeight, 0);
  };

  const displayPlaceholder = steerActive
    ? t("chat:input.steerPlaceholder")
    : (placeholder ?? t("chat:input.placeholder"));
  const generationModeLabel =
    generationMode === "fast" ? t("chat:input.mode.fast") : t("chat:input.mode.quality");
  const generationModeHint =
    generationMode === "fast" ? t("chat:input.mode.fastHint") : t("chat:input.mode.qualityHint");
  const generationModeTitle = `${t("chat:input.mode.label")}: ${generationModeLabel} · ${generationModeHint}`;
  const isFillLayout = layout === "fill";
  const compactAccessoryRowClass = isFillLayout
    ? "shrink-0 flex min-w-0 items-center gap-1.5 overflow-x-auto pb-0.5 scrollbar-none"
    : "shrink-0 flex flex-wrap items-center gap-1.5";
  const accessoryLabelClass = `text-xs text-[hsl(var(--text-secondary))] ${isFillLayout ? "shrink-0" : ""}`;
  const hasAccessoryRows =
    attachedMaterials.length > 0 ||
    quotes.length > 0 ||
    matchedSkills.length > 0 ||
    selectedSkills.length > 0 ||
    showSuggestionRow ||
    showSkillMenu;

  const accessoryRows = (
    <>
      {/* Attached materials display */}
      {attachedMaterials.length > 0 && (
        <div className={compactAccessoryRowClass}>
          <span className={accessoryLabelClass}>{t("chat:input.attachedMaterials")}</span>
          {attachedMaterials.map((material) => (
            <span
              key={material.id}
              className={`inline-flex min-w-0 items-center gap-1 px-2 py-0.5 bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))] rounded text-xs ${isFillLayout ? "shrink-0" : ""}`}
            >
              <FileText size={12} className="shrink-0" />
              <span className="max-w-[100px] truncate">{material.title}</span>
              <button
                onClick={() => removeMaterial(material.id)}
                className="shrink-0 hover:text-[hsl(var(--error))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--accent-primary)/0.15)] rounded-sm"
                title={t("chat:input.remove")}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Quoted text display */}
      {quotes.length > 0 && (
        <div className={compactAccessoryRowClass}>
          <span className={accessoryLabelClass}>{t("chat:input.quotedText")}</span>
          {quotes.map((quote) => (
            <span
              key={quote.id}
              className={`inline-flex min-w-0 items-center gap-1 px-2 py-0.5 bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))] rounded text-xs ${isFillLayout ? "shrink-0" : ""}`}
              title={quote.text}
            >
              <Quote size={12} className="shrink-0" />
              <span className="max-w-[150px] truncate">
                {quote.text.length > 50 ? `${quote.text.slice(0, 50)}...` : quote.text}
              </span>
              <button
                onClick={() => removeQuote(quote.id)}
                className="shrink-0 hover:text-[hsl(var(--error))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--warning)/0.15)] rounded-sm"
                title={t("chat:input.remove")}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Matched skills indicator */}
      {matchedSkills.length > 0 && (
        <div className={compactAccessoryRowClass}>
          {matchedSkills.map((skill, index) => (
            <span
              key={index}
              className={`inline-flex min-w-0 items-center gap-1 px-2 py-0.5 bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))] rounded text-xs ${isFillLayout ? "shrink-0" : ""}`}
            >
              <Zap size={10} className="shrink-0" />
              <span className="max-w-[180px] truncate">{skill.name}</span>
            </span>
          ))}
        </div>
      )}

      {/* Explicitly selected skills (sent as selected_skill_ids) */}
      {selectedSkills.length > 0 && (
        <div className={compactAccessoryRowClass} data-testid="chat-selected-skill-row">
          <span className={accessoryLabelClass}>
            {t("chat:skill.selectedLabel", { count: selectedSkills.length, max: MAX_SELECTED_SKILLS })}
          </span>
          {selectedSkills.map((skill) => (
            <span
              key={skill.id}
              className={`inline-flex min-w-0 items-center gap-1 px-2 py-0.5 bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))] rounded text-xs ${isFillLayout ? "shrink-0" : ""}`}
              data-testid="chat-selected-skill-chip"
            >
              <Zap size={10} className="shrink-0" />
              <span className="max-w-[200px] truncate">{skill.name}</span>
              <button
                type="button"
                onClick={() => removeSkill(skill.id)}
                className={`shrink-0 hover:text-[hsl(var(--error))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--success)/0.15)] rounded-sm ${isMobile ? "p-1 -m-1" : ""}`}
                title={t("chat:input.remove")}
                aria-label={t("chat:skill.removeSelected", { name: skill.name })}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Suggestion row: project suggestions first, static only as explicit fallback */}
      {showSuggestionRow && (
        <div className={`shrink-0 flex min-w-0 items-center overflow-x-auto pb-0.5 scrollbar-none ${isMobile ? 'gap-1' : 'gap-1.5'}`}>
          {isSuggestionLoading ? (
            <>
              <span className="sr-only">{t("common:loading")}</span>
              {[0, 1, 2].map((i) => (
                <span
                  key={`suggestion-loading-${i}`}
                  className={`shrink-0 rounded-full bg-[hsl(var(--bg-tertiary))] animate-pulse ${isMobile ? 'w-20 h-6' : 'w-24 h-6'}`}
                />
              ))}
            </>
          ) : (
            displaySuggestions.map((suggestion, index) => (
              <SwipeableSuggestionChip
                key={`${suggestion}-${index}`}
                suggestion={suggestion}
                onClick={() => handleSuggestionClick(suggestion)}
                onDismiss={() => dismissSuggestion(suggestion)}
                isMobile={isMobile}
              />
            ))
          )}
          <button
            onClick={refreshSuggestions}
            disabled={isRefreshingSuggestions}
            className="shrink-0 p-1 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-full transition-colors disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
            title={t("chat:input.refresh")}
            aria-busy={isRefreshingSuggestions}
          >
            <RefreshCw size={12} className={isRefreshingSuggestions ? "animate-spin" : ""} />
          </button>
        </div>
      )}

      {/* Skill quick trigger menu */}
      {showSkillMenu && (
        <div
          ref={skillMenuRef}
          className="shrink-0 relative bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg shadow-lg max-h-64 overflow-y-auto"
        >
          {isLoadingSkills ? (
            <div className="px-3 py-4 text-center text-sm text-[hsl(var(--text-secondary))]">
              {t("chat:skill.loading")}
            </div>
          ) : filteredSkills.length === 0 ? (
            <div className="px-3 py-4 text-center text-sm text-[hsl(var(--text-secondary))]">
              {t("chat:skill.noResults")}
            </div>
          ) : (
            <div className="py-1">
              {skillSelectionFull && (
                <div
                  role="status"
                  className="px-3 py-2 text-xs text-[hsl(var(--warning))]"
                >
                  {t("chat:skill.selectionFull", { max: MAX_SELECTED_SKILLS })}
                </div>
              )}
              {filteredSkills.map((skill, index) => (
                <button
                  key={skill.id}
                  type="button"
                  onClick={() => pickSkillFromMenu(skill)}
                  disabled={!isSkillPickable(skill)}
                  className={`w-full px-3 py-2 text-left hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:bg-[hsl(var(--bg-tertiary))] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent ${
                    index === selectedSkillIndex
                      ? "bg-[hsl(var(--accent-primary)/0.1)] border-l-2 border-l-[hsl(var(--accent-primary))]"
                      : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Zap size={14} className="text-[hsl(var(--accent-primary))] shrink-0" />
                    <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
                      {skill.name}
                    </span>
                    <span className="text-xs text-[hsl(var(--text-secondary))] px-1.5 py-0.5 bg-[hsl(var(--bg-tertiary))] rounded">
                      {skill.source === "builtin" ? t("chat:skill.builtin") : t("chat:skill.user")}
                    </span>
                  </div>
                  {skill.description && (
                    <p className="mt-1 text-xs text-[hsl(var(--text-secondary))] line-clamp-1 ml-5">
                      {skill.description}
                    </p>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1 ml-5">
                    {skill.triggers.slice(0, 3).map((trigger, i) => (
                      <span
                        key={i}
                        className="text-xs text-[hsl(var(--accent-primary)/0.8)] bg-[hsl(var(--accent-primary)/0.1)] px-1.5 py-0.5 rounded"
                      >
                        {trigger}
                      </span>
                    ))}
                    {skill.triggers.length > 3 && (
                      <span className="text-xs text-[hsl(var(--text-secondary))]">
                        +{skill.triggers.length - 3}
                      </span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
          <div className="px-3 py-2 border-t border-[hsl(var(--border-color))] text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1.5">
            <span>{t("chat:skill.hint")}</span>
          </div>
        </div>
      )}
    </>
  );

  return (
    <div
      className={
        isFillLayout
          ? "flex flex-col gap-2.5 h-full min-h-0"
          : "flex flex-col gap-2.5"
      }
    >
      {isFillLayout && hasAccessoryRows ? (
        <div className="min-h-0 overflow-y-auto pr-1" data-testid="chat-input-accessories">
          <div className="flex flex-col gap-2.5 pb-1">{accessoryRows}</div>
        </div>
      ) : (
        accessoryRows
      )}

      {inputTooLong ? (
        <div
          className="text-xs text-[hsl(var(--error))] px-1"
          role="alert"
          data-testid="chat-input-too-long"
        >
          {t("chat:input.tooLong", {
            count: input.length,
            max: MAX_AGENT_MESSAGE_CHARS,
          })}
        </div>
      ) : null}

      {/* Steering hint: shown while the agent is generating and steering is available */}
      {steerActive ? (
        <div
          className="text-xs text-[hsl(var(--text-secondary))] px-1"
          data-testid="steer-hint"
        >
          {t("chat:input.steerHint")}
        </div>
      ) : null}

      {/* Input row */}
      <div
        className={
          isFillLayout
            ? "flex flex-1 min-h-0 items-end gap-1.5"
            : `flex items-end ${isMobile ? 'gap-1.5' : 'gap-1.5'}`
        }
        style={isFillLayout ? { minHeight: `${textareaMinHeightPx}px` } : undefined}
      >
        {/* 语音输入按钮 */}
        <VoiceInputButton
          onResult={handleVoiceResult}
          disabled={inputDisabled}
        />

        {/* Generation mode toggle (placed next to voice button to avoid vertical overlap) */}
        {onGenerationModeChange ? (
          <button
            type="button"
            disabled={inputDisabled}
            aria-pressed={generationMode === "fast"}
            onClick={() =>
              onGenerationModeChange(generationMode === "fast" ? "quality" : "fast")
            }
            className={`shrink-0 flex items-center justify-center bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-tertiary)/0.8)] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:opacity-50 disabled:cursor-not-allowed ${
              isMobile ? "w-11 h-11 active:scale-95" : "w-9 h-9"
            }`}
            title={generationModeTitle}
            aria-label={generationModeTitle}
          >
            {generationMode === "fast" ? <Zap size={16} /> : <Sparkles size={16} />}
          </button>
        ) : null}

        {/* data-testid: chat-input - Chat input textarea for chat interaction tests */}
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
          disabled={inputDisabled}
          className={`flex-1 bg-[hsl(var(--bg-tertiary))] border border-transparent rounded-lg text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary)/0.5)] outline-none resize-none overflow-y-auto transition-all disabled:opacity-50 focus:border-[hsl(var(--accent-primary)/0.5)] focus:shadow-[inset_0_1px_2px_hsl(0_0%_0%_/_0.1),_0_0_0_2px_hsl(var(--accent-primary)/0.15)] ${
            isMobile ? 'px-3 py-2.5' : 'px-3 py-2'
          } ${
            isFillLayout ? "h-full min-h-0" : ""
          }`}
          style={
            isFillLayout
              ? {
                  minHeight: `${textareaMinHeightPx}px`,
                  height: "100%",
                }
              : {
                  minHeight: `${textareaMinHeightPx}px`,
                  maxHeight: `${TEXTAREA_AUTO_MAX_HEIGHT_PX}px`,
                }
          }
          rows={1}
          data-testid="chat-input"
        />

        {steerActive && input.trim() ? (
          <button
            onClick={() => { void handleSteer(); }}
            disabled={!input.trim() || steerPending || inputTooLong}
            className={`shrink-0 flex items-center justify-center bg-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-dark))] disabled:bg-[hsl(var(--bg-tertiary))] disabled:cursor-not-allowed text-white rounded-lg transition-colors focus-visible:outline-none focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-primary)),_0_0_0_4px_hsl(var(--accent-primary))] ${isMobile ? 'w-11 h-11' : 'w-9 h-9'}`}
            title={t("chat:input.steerButton")}
            aria-label={t("chat:input.steerButton")}
            data-testid="steer-button"
          >
            <Send size={16} />
          </button>
        ) : onCancel && effectiveSendDisabled ? (
          <button
            onClick={handleCancel}
            // While arming the button stays clickable (aria-disabled) so a press gets an answer.
            disabled={isStopping}
            aria-disabled={!stopArmed || isStopping}
            className={`shrink-0 flex items-center justify-center text-white rounded-lg transition-colors focus-visible:outline-none ${
              !stopArmed || isStopping
                // Same look as the disabled send button in this spot.
                ? 'bg-[hsl(var(--bg-tertiary))] cursor-not-allowed focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-primary)),_0_0_0_4px_hsl(var(--bg-tertiary))]'
                : 'bg-[hsl(var(--error))] hover:bg-[hsl(var(--error))] focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-primary)),_0_0_0_4px_hsl(var(--error))]'
            } ${isMobile ? 'w-11 h-11' : 'w-9 h-9'}`}
            title={stopButtonLabel}
            aria-label={stopButtonLabel}
            data-testid="stop-button"
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!input.trim() || effectiveSendDisabled || inputTooLong}
            className={`shrink-0 flex items-center justify-center bg-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-dark))] disabled:bg-[hsl(var(--bg-tertiary))] disabled:cursor-not-allowed text-white rounded-lg transition-colors focus-visible:outline-none focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-primary)),_0_0_0_4px_hsl(var(--accent-primary))] disabled:focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-primary)),_0_0_0_4px_hsl(var(--bg-tertiary))] ${isMobile ? 'w-11 h-11' : 'w-9 h-9'}`}
            title={t("common:send")}
            data-testid="send-button"
          >
            <Send size={16} />
          </button>
        )}
      </div>
    </div>
  );
};
