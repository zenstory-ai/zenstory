import { useState, useEffect, useRef, useCallback, useContext } from "react";
import { useTranslation } from "react-i18next";
import { QueryClientContext } from "@tanstack/react-query";
import { Save, Clock, Check, History, Sparkles, Loader2 } from "lucide-react";
import type { FileUpdateVersionIntent } from "../lib/api";
import { FileVersionHistory } from "./FileVersionHistory";
import { DiffReviewSplitView } from "./DiffReviewSplitView";
import { DiffToolbar } from "./DiffToolbar";
import { EditorStreamingBanner, EditorStreamingCanvasWaiting } from "./EditorStreamingWaiting";
import { SelectionToolbar } from "./SelectionToolbar";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { usePinchZoom } from "../hooks/useGestures";
import { useAnimatedPresence } from "../hooks/useAnimatedPresence";
import type { DiffReviewState } from "../types";
import { SavedAgoLabel } from "./SavedAgoLabel";
import { countWords } from "../lib/documentChunker";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";
import { subscriptionQueryKeys } from "../lib/subscriptionApi";
import type { QuotaResponse } from "../types/subscription";
import {
  BEFORE_CHUNK_RELOAD_EVENT,
  createEditorDraftSnapshot,
  writeEditorDraftSnapshot,
} from "../lib/editorDraftRecovery";
import { flushOpenEditor, trackEditorSave } from "../lib/editorSaveTracker";
import { isFileRemoved } from "../lib/fileRemovalEvents";

const isNearBottom = (el: HTMLElement, thresholdPx = 32) => {
  return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;
};

const SIMPLE_EDITOR_MIN_HEIGHT_PX = 200;

const isAppleSaveShortcutPlatform = (): boolean => {
  if (typeof navigator === "undefined") return false;
  const uaDataPlatform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform;
  return /mac|iphone|ipad|ipod/i.test(uaDataPlatform || navigator.platform || "");
};

export type SaveOutcome = "saved" | "conflict" | "failed";
export type SaveResult =
  | { outcome: "saved"; updatedAt: string }
  | { outcome: "conflict" | "failed" };

export interface SaveSubmission {
  fileId?: string;
  title: string;
  content: string;
  baseUpdatedAt?: string;
  previousTitle: string;
  versionIntent?: FileUpdateVersionIntent;
}

const restoreContainerScrollTop = (container: HTMLElement | null, prevScrollTop: number | null) => {
  if (!container || prevScrollTop === null) return;

  const restore = () => {
    const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
    container.scrollTop = Math.min(prevScrollTop, maxTop);
  };

  restore();
  requestAnimationFrame(restore);
};

interface SimpleEditorProps {
  /** Static UI only: never persisted as manuscript content. */
  emptyHint?: (focusEditor: () => void) => React.ReactNode;
  userId?: string;
  fileId?: string;
  projectId?: string;
  fileType?: string;
  fileTitle?: string;
  baseUpdatedAt?: string;
  /** The body of the server copy `baseUpdatedAt` belongs to (what is saved). */
  savedContent?: string;
  title: string;
  /** The author's editable body; streaming previews must never become a saved draft. */
  content: string;
  /** Display only while streaming; excluded from draft, dirty and save-token state. */
  streamingContent?: string;
  onTitleChange: (title: string) => void;
  onContentChange: (content: string) => void;
  onSave: (submission: SaveSubmission) => Promise<SaveResult>;
  onHistoryRestore?: () => Promise<void>;
  onFlushReady?: (flush: (() => Promise<SaveOutcome>) | null) => void;
  /**
   * The parent switched the editor to a newer server copy (for example after
   * an AI write). Each new `version` resets the draft, save token, dirty flag
   * and word-count baseline to this copy; the current `title`/`content` props
   * become the draft (an unsaved title rename stays dirty).
   */
  serverBaseline?: {
    version: number;
    fileId: string;
    title: string;
    content: string;
    updatedAt?: string;
  };
  /**
   * While a comparison is open, the draft to keep locally if the editor goes
   * away (leaving the project, closing the page) instead of the raw draft;
   * null when this comparison has no such draft.
   */
  getReviewLeaveDraft?: () => { title: string; content: string; baseUpdatedAt?: string } | null;
  readOnly?: boolean;
  isStreaming?: boolean;
  /** Whether the chat agent is currently running a turn (disables editor-triggered AI actions). */
  isChatStreaming?: boolean;
  /**
   * AI 正在编辑这份文件（file_edit_start ~ file_edit_end 之间）。
   * 期间必须挂起防抖自动保存：编辑前拍下的整篇快照若在 AI 写完之后才发出，
   * 会把 AI 的改动整篇覆盖掉（服务端的乐观并发校验会挡下来并返回 409，
   * 但让用户吃一次冲突提示不如根本不发这次请求）。
   */
  isAiEditing?: boolean;
  recoveredDraft?: {
    capturedAt: string;
    serverTitle: string;
    serverContent: string;
  };
  /** Dispatch a structured ui_action="natural_polish" turn to the chat agent (after flushing dirty edits). */
  onNaturalPolish?: (params: {
    projectId: string;
    fileId: string;
    fileType?: string;
    fileTitle?: string;
    selectedText?: string;
  }) => void;
  /** Focus and select the title field once on mount/open for freshly created files (desktop only). */
  autoFocusTitle?: boolean;
  onAutoFocusConsumed?: () => void;
  // Diff review props
  diffReviewState?: DiffReviewState | null;
  onAcceptEdit?: (editId: string) => void;
  onRejectEdit?: (editId: string) => void;
  onResetEdit?: (editId: string) => void;
  onAcceptAllEdits?: () => void;
  onRejectAllEdits?: () => void;
  onFinishReview?: () => void;
}

export const SimpleEditor = ({
  emptyHint,
  userId,
  fileId,
  projectId,
  fileType,
  fileTitle,
  baseUpdatedAt,
  savedContent,
  title,
  content,
  streamingContent,
  onTitleChange,
  onContentChange,
  onSave,
  onHistoryRestore,
  onFlushReady,
  serverBaseline,
  getReviewLeaveDraft,
  readOnly = false,
  isStreaming = false,
  isChatStreaming = false,
  isAiEditing = false,
  recoveredDraft,
  onNaturalPolish,
  autoFocusTitle = false,
  onAutoFocusConsumed,
  // Diff review props
  diffReviewState,
  onAcceptEdit,
  onRejectEdit,
  onResetEdit,
  onAcceptAllEdits,
  onRejectAllEdits,
  onFinishReview,
}: SimpleEditorProps) => {
  const displayContent = isStreaming ? streamingContent ?? content : content;
  const { t } = useTranslation(['editor', 'versions']);
  const { addQuote } = useTextQuote();
  // 只读 QuotaBadge 已经拉好的额度缓存，不为一个按钮提示另发请求；
  // 没有 QueryClientProvider（嵌入场景、单测）时按「额度未知」处理。
  const queryClient = useContext(QueryClientContext);
  const readCachedQuota = useCallback(
    () => queryClient?.getQueryData<QuotaResponse>(subscriptionQueryKeys.quota()),
    [queryClient],
  );
  const [cachedQuota, setCachedQuota] = useState(readCachedQuota);
  useEffect(() => {
    if (!queryClient) return;
    setCachedQuota(readCachedQuota());
    return queryClient.getQueryCache().subscribe(() => setCachedQuota(readCachedQuota()));
  }, [queryClient, readCachedQuota]);
  const isLimitedAiQuota = cachedQuota != null && cachedQuota.ai_conversations.limit !== -1;
  const [isSaving, setIsSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  // Bumped when a new server baseline leaves the draft unsaved, so the
  // autosave is scheduled again on the new token (see the baseline effect).
  const [autosaveNudge, setAutosaveNudge] = useState(0);
  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const isVersionHistoryOpen = showVersionHistory && Boolean(fileId);
  const { isMounted: isVersionHistoryMounted } = useAnimatedPresence(isVersionHistoryOpen, 150);
  // Selection toolbar state
  const [selectedText, setSelectedText] = useState("");
  const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const isSelectionToolbarOpen = Boolean(selectedText && selectionPosition && fileId);
  const { isMounted: isSelectionToolbarMounted } = useAnimatedPresence(isSelectionToolbarOpen, 120);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const contentAreaRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectionRangeRef = useRef<{ start: number; end: number } | null>(null);
  /** Forget the selection the toolbar and 去AI味 would act on. */
  const clearSelectionState = useCallback(() => {
    if (selectionTimeoutRef.current) {
      clearTimeout(selectionTimeoutRef.current);
      selectionTimeoutRef.current = null;
    }
    setSelectedText("");
    setSelectionPosition(null);
    selectionRangeRef.current = null;
  }, []);
  /**
   * The selection is done with (quoted to chat, or the mobile editor panel was left): collapse
   * it in the textarea too, so a later 去AI味 never acts on text the author no longer sees selected.
   */
  const dropSelection = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea && textarea.selectionStart !== textarea.selectionEnd) {
      textarea.setSelectionRange(textarea.selectionEnd, textarea.selectionEnd);
    }
    clearSelectionState();
  }, [clearSelectionState]);
  const lastSavedContentRef = useRef<string>(content);
  const lastSavedTitleRef = useRef<string>(title);
  const latestContentRef = useRef(content);
  const latestTitleRef = useRef(title);
  const previousFileIdRef = useRef(fileId);
  const pendingBaselineSyncRef = useRef(false);
  const pendingHistoryRestoreTokenRef = useRef<string | undefined | null>(null);
  const shouldAutoScrollRef = useRef(true);
  const isComposingRef = useRef(false);
  const handleSaveRef = useRef<(submission?: SaveSubmission) => Promise<SaveOutcome>>(
    async () => "failed",
  );
  const latestFileIdRef = useRef<string | undefined>(fileId);
  const latestBaseUpdatedAtRef = useRef(baseUpdatedAt);
  const dirtyRef = useRef(false);
  const pendingSaveCountRef = useRef(0);
  const saveChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const activeSaveRef = useRef<{ key: string; promise: Promise<SaveOutcome> } | null>(null);
  const draftRef = useRef({ fileId, title, content, baseUpdatedAt });
  // Only clean boundaries or this queue's confirmed PUT advance the draft token.
  const persistedBaseRef = useRef({ fileId, updatedAt: baseUpdatedAt });
  const isMountedRef = useRef(true);
  const suppressRecoveredAutoSaveRef = useRef(false);
  const appliedRecoveryRef = useRef<string | null>(null);

  // Re-entrancy guard while flushing unsaved edits before dispatching ui_action="natural_polish".
  const [isFlushingNaturalPolish, setIsFlushingNaturalPolish] = useState(false);
  const flushingNaturalPolishRef = useRef(false);

  latestContentRef.current = content;
  latestTitleRef.current = title;
  const draftScopeRef = useRef({ userId, projectId });
  draftScopeRef.current = { userId, projectId };
  latestFileIdRef.current = fileId;
  latestBaseUpdatedAtRef.current = baseUpdatedAt;
  const reviewLeaveDraftRef = useRef(getReviewLeaveDraft);
  reviewLeaveDraftRef.current = getReviewLeaveDraft;

  useEffect(() => {
    if (!dirtyRef.current && draftRef.current.fileId === fileId) {
      draftRef.current = { fileId, title, content, baseUpdatedAt };
      persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
    }
  }, [fileId, title, content, baseUpdatedAt]);

  const appliedServerBaselineRef = useRef(serverBaseline?.version ?? 0);
  useEffect(() => {
    if (!serverBaseline || serverBaseline.version === appliedServerBaselineRef.current) return;
    appliedServerBaselineRef.current = serverBaseline.version;
    if (serverBaseline.fileId !== fileId) return;
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    // 编辑器换成了服务端的新版本：草稿、保存令牌、字数基线都要跟着换，
    // 否则下一次保存会带着旧令牌和旧草稿发出（撞 409 后把旧稿当成提案），
    // 或把 AI 写的字算成作者新增的字数。
    lastSavedContentRef.current = serverBaseline.content;
    lastSavedTitleRef.current = serverBaseline.title;
    persistedBaseRef.current = { fileId, updatedAt: serverBaseline.updatedAt };
    draftRef.current = {
      fileId,
      title: latestTitleRef.current,
      content: latestContentRef.current,
      baseUpdatedAt: serverBaseline.updatedAt,
    };
    pendingBaselineSyncRef.current = false;
    suppressRecoveredAutoSaveRef.current = false;
    const dirty =
      latestTitleRef.current !== serverBaseline.title ||
      latestContentRef.current !== serverBaseline.content;
    dirtyRef.current = dirty;
    setIsDirty(dirty);
    // Text the author has not saved yet (kept on top of the newer copy):
    // schedule its save again, the pending one was cleared above.
    if (dirty) setAutosaveNudge((n) => n + 1);
  }, [serverBaseline, fileId]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!userId || !projectId || !fileId) return;
    let lastCapturedDraft: typeof draftRef.current | null = null;
    const captureDirtyDraft = (reason: "chunk-reload" | "page-exit") => {
      // With a comparison open, keep what finishing it would save (see getReviewLeaveDraft).
      const reviewDraft = isReviewModeRef.current ? reviewLeaveDraftRef.current?.() ?? null : null;
      if (!reviewDraft) {
        if (!dirtyRef.current || draftRef.current.fileId !== fileId) return true;
        if (lastCapturedDraft === draftRef.current) return true;
      }
      const currentDraft = reviewDraft ?? draftRef.current;
      const captured = writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
        userId,
        projectId,
        fileId,
        title: currentDraft.title,
        content: currentDraft.content,
        baseUpdatedAt: currentDraft.baseUpdatedAt,
        reason,
      }));
      if (captured && !reviewDraft) lastCapturedDraft = draftRef.current;
      return captured;
    };
    const captureBeforeChunkReload = (event: Event) => {
      if (!captureDirtyDraft("chunk-reload")) event.preventDefault();
    };
    const captureBeforeUnload = (event: BeforeUnloadEvent) => {
      if (captureDirtyDraft("page-exit")) return;
      event.preventDefault();
      event.returnValue = "";
    };
    const capturePageHide = () => {
      captureDirtyDraft("page-exit");
    };
    window.addEventListener(BEFORE_CHUNK_RELOAD_EVENT, captureBeforeChunkReload);
    window.addEventListener("beforeunload", captureBeforeUnload);
    window.addEventListener("pagehide", capturePageHide);
    return () => {
      window.removeEventListener(BEFORE_CHUNK_RELOAD_EVENT, captureBeforeChunkReload);
      window.removeEventListener("beforeunload", captureBeforeUnload);
      window.removeEventListener("pagehide", capturePageHide);
    };
  }, [userId, projectId, fileId]);

  // Pinch-to-zoom gesture support
  const { zoom, bind: bindPinchZoom, resetZoom } = usePinchZoom(1, 0.5, 2.5);

  // Reset zoom when file changes
  useEffect(() => {
    resetZoom();
  }, [fileId, resetZoom]);

  // Auto-resize textarea
  // NOTE: Avoid a full `height="auto"` reflow on every keystroke.
  // That reintroduces the "jump back to editor top" bug for hardware keyboard input.
  // While the textarea is focused we only grow; when focus leaves (or on file switches)
  // we do a full recalculation so the editor can shrink safely.
  const adjustTextareaHeight = useCallback((force = false) => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const container = contentAreaRef.current;
    const prevScrollTop = container ? container.scrollTop : null;
    const currentHeightPx =
      Number.parseFloat(textarea.style.height || "0") || SIMPLE_EDITOR_MIN_HEIGHT_PX;
    const isFocused = document.activeElement === textarea;

    let nextHeightPx = Math.max(textarea.scrollHeight, SIMPLE_EDITOR_MIN_HEIGHT_PX);

    if (force || !isFocused) {
      const previousHeight = textarea.style.height;
      const previousMinHeight = textarea.style.minHeight;
      textarea.style.height = "0px";
      textarea.style.minHeight = "0";
      nextHeightPx = Math.max(textarea.scrollHeight, SIMPLE_EDITOR_MIN_HEIGHT_PX);
      textarea.style.minHeight = previousMinHeight;
      textarea.style.height = previousHeight;
    } else if (nextHeightPx < currentHeightPx) {
      nextHeightPx = currentHeightPx;
    }

    if (force || Math.abs(nextHeightPx - currentHeightPx) > 1) {
      textarea.style.height = `${nextHeightPx}px`;
    }

    restoreContainerScrollTop(container, prevScrollTop);
  }, []);

  // Check if in diff review mode
  const isReviewMode = diffReviewState?.isReviewing && diffReviewState.fileId === fileId;
  const isReviewModeRef = useRef(false);
  isReviewModeRef.current = Boolean(isReviewMode);

  useEffect(() => {
    const isFocused = document.activeElement === textareaRef.current;
    adjustTextareaHeight(!isFocused);
  }, [displayContent, adjustTextareaHeight]);

  // When switching files, force a full recalculation so short files don't leave huge height.
  useEffect(() => {
    const didFileChange = previousFileIdRef.current !== fileId;
    previousFileIdRef.current = fileId;

    if (didFileChange && dirtyRef.current) {
      const previousDraft = draftRef.current;
      void handleSaveRef.current({
        ...previousDraft,
        previousTitle: lastSavedTitleRef.current,
      });
    }

    draftRef.current = {
      fileId,
      title: latestTitleRef.current,
      content: latestContentRef.current,
      baseUpdatedAt: latestBaseUpdatedAtRef.current,
    };

    persistedBaseRef.current = { fileId, updatedAt: latestBaseUpdatedAtRef.current };
    adjustTextareaHeight(true);
    // When opening a new file, default to "follow bottom" during streaming
    shouldAutoScrollRef.current = true;
    // Reset save/dirty state for the new file and sync baseline on first content load.
    setIsDirty(false);
    dirtyRef.current = false;
    setLastSaved(null);
    lastSavedContentRef.current = latestContentRef.current;
    lastSavedTitleRef.current = latestTitleRef.current;
    pendingBaselineSyncRef.current = true;
    pendingHistoryRestoreTokenRef.current = null;
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }

    if (!didFileChange) {
      return;
    }

    // This editor instance is often reused across file switches, so we must
    // clear selection state when switching files.
    clearSelectionState();
  }, [fileId, adjustTextareaHeight, clearSelectionState]);

  useEffect(() => {
    if (!recoveredDraft || !fileId) {
      if (appliedRecoveryRef.current === null) return;
      appliedRecoveryRef.current = null;
      lastSavedTitleRef.current = title;
      lastSavedContentRef.current = content;
      draftRef.current = { fileId, title, content, baseUpdatedAt };
      persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
      suppressRecoveredAutoSaveRef.current = false;
      dirtyRef.current = false;
      setIsDirty(false);
      return;
    }
    const recoveryKey = `${fileId}:${recoveredDraft.capturedAt}`;
    if (appliedRecoveryRef.current === recoveryKey) return;
    appliedRecoveryRef.current = recoveryKey;
    lastSavedTitleRef.current = recoveredDraft.serverTitle;
    lastSavedContentRef.current = recoveredDraft.serverContent;
    draftRef.current = { fileId, title, content, baseUpdatedAt };
    persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
    pendingBaselineSyncRef.current = false;
    suppressRecoveredAutoSaveRef.current = true;
    dirtyRef.current = true;
    setIsDirty(true);
  }, [recoveredDraft, fileId, title, content, baseUpdatedAt]);

  // Some file switches load content asynchronously after fileId changes.
  // Sync baseline once when the new content arrives so dirty/version diff is correct.
  useEffect(() => {
    if (!pendingBaselineSyncRef.current) return;
    if (isDirty) return;
    if (pendingHistoryRestoreTokenRef.current !== null) return;
    lastSavedContentRef.current = content;
    pendingBaselineSyncRef.current = false;
  }, [content, isDirty]);

  // A history restore has a separate server-token boundary. Don't consume the
  // old draft while its refresh is pending or alter async file-switch syncing.
  useEffect(() => {
    if (pendingHistoryRestoreTokenRef.current === null || isDirty) return;
    if (baseUpdatedAt === pendingHistoryRestoreTokenRef.current) return;
    lastSavedContentRef.current = content;
    lastSavedTitleRef.current = title;
    pendingBaselineSyncRef.current = false;
    pendingHistoryRestoreTokenRef.current = null;
    persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
  }, [content, title, fileId, isDirty, baseUpdatedAt]);

  // Re-adjust textarea height when exiting review mode
  const prevReviewModeRef = useRef(isReviewMode);
  useEffect(() => {
    const wasInReviewMode = prevReviewModeRef.current;
    prevReviewModeRef.current = isReviewMode;
    
    // If we just exited review mode, schedule a height adjustment
    if (wasInReviewMode && !isReviewMode) {
      // The review closed. When it saved the editor's text (it now matches the saved copy),
      // sync the local save baseline so "unsaved" status doesn't get stuck. A review that
      // closed without saving it (the author's unsaved text was kept) leaves it unsaved:
      // autosave, the leave flush and the page-exit draft still cover it.
      if (!diffReviewState && savedContent !== undefined && content !== savedContent) {
        dirtyRef.current = true;
        setIsDirty(true);
        setAutosaveNudge((n) => n + 1);
      } else if (!diffReviewState) {
        lastSavedContentRef.current = content;
        lastSavedTitleRef.current = title;
        dirtyRef.current = false;
        persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
        draftRef.current = { fileId, title, content, baseUpdatedAt };
        setIsDirty(false);
        setLastSaved(new Date());
      }

      // Use setTimeout to wait for the textarea to be rendered
      setTimeout(() => {
        adjustTextareaHeight(true);
      }, 50);
    }
  }, [isReviewMode, diffReviewState, fileId, title, content, baseUpdatedAt, savedContent, adjustTextareaHeight]);

  const handleContentScroll = useCallback(() => {
    const el = contentAreaRef.current;
    if (!el) return;
    shouldAutoScrollRef.current = isNearBottom(el);
  }, []);

  // Calculate selection position using mirror div technique
  const getSelectionPosition = useCallback(() => {
    const textarea = textareaRef.current;
    const mirror = mirrorRef.current;
    if (!textarea || !mirror) return null;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    if (start === end) return null;

    // Copy textarea styles to mirror
    const styles = window.getComputedStyle(textarea);
    mirror.style.cssText = `
      position: absolute;
      visibility: hidden;
      white-space: pre-wrap;
      word-wrap: break-word;
      overflow-wrap: break-word;
      width: ${styles.width};
      font: ${styles.font};
      padding: ${styles.padding};
      border: ${styles.border};
      line-height: ${styles.lineHeight};
      letter-spacing: ${styles.letterSpacing};
    `;

    // Create span for measuring position
    const textBefore = textarea.value.substring(0, start);
    const selectedText = textarea.value.substring(start, end);

    mirror.innerHTML = '';
    mirror.appendChild(document.createTextNode(textBefore));
    const span = document.createElement('span');
    span.textContent = selectedText;
    mirror.appendChild(span);

    const textareaRect = textarea.getBoundingClientRect();
    const spanRect = span.getBoundingClientRect();
    const mirrorRect = mirror.getBoundingClientRect();

    // Calculate position relative to viewport
    const x = textareaRect.left + (spanRect.left - mirrorRect.left) + spanRect.width / 2;
    const y = textareaRect.top + (spanRect.top - mirrorRect.top) - textarea.scrollTop - 8;

    return { x, y };
  }, []);

  // Handle text selection
  const handleSelection = useCallback((target?: HTMLTextAreaElement | null) => {
    if (selectionTimeoutRef.current) {
      clearTimeout(selectionTimeoutRef.current);
    }

    const textarea = target ?? textareaRef.current;
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const text = textarea.value.substring(start, end).trim();

    if (!text) {
      setSelectedText("");
      setSelectionPosition(null);
      selectionRangeRef.current = null;
      return;
    }

    // Keep a "selection exists" signal immediate so action buttons can respond quickly.
    setSelectedText(text);
    selectionRangeRef.current = { start, end };

    // Delay showing toolbar by 300ms
    selectionTimeoutRef.current = setTimeout(() => {
      const position = getSelectionPosition();
      if (position) {
        setSelectionPosition(position);
      }
    }, 300);
  }, [getSelectionPosition]);

  // Focus & select the default title of a freshly created file on desktop so the author can rename & press Enter to write immediately
  useEffect(() => {
    if (!autoFocusTitle || readOnly || isStreaming || isReviewMode) return;
    const input = titleInputRef.current;
    if (!input) return;
    const frame = requestAnimationFrame(() => {
      input.focus({ preventScroll: true });
      input.select();
      onAutoFocusConsumed?.();
    });
    return () => cancelAnimationFrame(frame);
  }, [autoFocusTitle, fileId, readOnly, isStreaming, isReviewMode, onAutoFocusConsumed]);

  // Close selection toolbar without disturbing scroll or selection
  const closeSelectionToolbar = useCallback((restoreFocus?: boolean) => {
    setSelectionPosition(null);
    if (restoreFocus) {
      textareaRef.current?.focus({ preventScroll: true });
    }
  }, []);

  // Add quote from selection
  const handleAddQuote = useCallback(() => {
    if (!selectedText || !fileId) return;
    const displayTitle = fileTitle || title;
    addQuote(selectedText, fileId, displayTitle);
    // The quoted text is used; it is no longer the target of 去AI味.
    dropSelection();
  }, [selectedText, fileId, fileTitle, title, addQuote, dropSelection]);

  // Mobile keeps every panel mounted (hidden with inert), so a selection made before
  // switching to 文件 / AI would otherwise survive unseen and be what 去AI味 polishes.
  const { isMobile, activePanel, switchToChat } = useMobileLayout();
  useEffect(() => {
    if (isMobile && activePanel !== "editor") dropSelection();
  }, [isMobile, activePanel, dropSelection]);

  const isNaturalPolishBusy = isStreaming || isChatStreaming || isAiEditing || isFlushingNaturalPolish;
  const hasPolishableContent = Boolean(displayContent.trim());

  const startNaturalPolish = useCallback(async () => {
    if (flushingNaturalPolishRef.current || isNaturalPolishBusy || readOnly || isReviewModeRef.current) {
      return;
    }
    if (!projectId || !fileId) {
      toast.error(t("editor:naturalPolishMissingContext"));
      return;
    }

    const textarea = textareaRef.current;
    const currentText = textarea?.value ?? latestContentRef.current;
    if (!currentText.trim()) {
      return;
    }

    const range = selectionRangeRef.current;
    let selection: string | undefined;
    if (range && range.start !== range.end && textarea) {
      const rawSelection = textarea.value.substring(range.start, range.end);
      if (rawSelection.trim()) {
        selection = rawSelection;
      }
    }

    flushingNaturalPolishRef.current = true;
    setIsFlushingNaturalPolish(true);
    try {
      // Mandatory dirty-draft flush BEFORE starting /api/v1/agent/stream so the
      // agent never edits stale DB content or triggers aiEditedWhileDirty.
      const pendingFlush = flushOpenEditor(5000);
      if (pendingFlush) {
        const result = await pendingFlush;
        if (!result.saved) {
          if (result.outcome !== "conflict") {
            toast.error(t("editor:naturalPolishSaveFailed"));
          }
          return;
        }
      } else if (dirtyRef.current || suppressRecoveredAutoSaveRef.current) {
        const outcome: SaveOutcome = suppressRecoveredAutoSaveRef.current
          ? "failed"
          : await handleSaveRef.current();
        if (outcome !== "saved") {
          if (outcome !== "conflict") {
            toast.error(t("editor:naturalPolishSaveFailed"));
          }
          return;
        }
      }

      if (latestFileIdRef.current !== fileId) {
        return;
      }

      dropSelection();
      onNaturalPolish?.({
        projectId,
        fileId,
        fileType,
        fileTitle: fileTitle || latestTitleRef.current,
        selectedText: selection,
      });
      switchToChat();
    } finally {
      flushingNaturalPolishRef.current = false;
      if (isMountedRef.current) {
        setIsFlushingNaturalPolish(false);
      }
    }
  }, [
    isNaturalPolishBusy,
    readOnly,
    projectId,
    fileId,
    fileType,
    fileTitle,
    t,
    dropSelection,
    onNaturalPolish,
    switchToChat,
  ]);

  // Cleanup selection timeout
  useEffect(() => {
    return () => {
      if (selectionTimeoutRef.current) {
        clearTimeout(selectionTimeoutRef.current);
      }
    };
  }, []);

  // Auto-scroll to bottom when streaming (only if user is already near bottom)
  useEffect(() => {
    const el = contentAreaRef.current;
    if (!el) return;

    // When streaming starts, default to following the output
    if (isStreaming) {
      // If the user hasn't scrolled away, keep following.
      if (shouldAutoScrollRef.current || isNearBottom(el)) {
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight;
        });
      }
    }
  }, [displayContent, isStreaming]);

  // Auto-save with debounce
  useEffect(() => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    if (!isDirty || showVersionHistory) return;
    if (suppressRecoveredAutoSaveRef.current) return;
    // AI 正在改这份文件：先不排自动保存。标记清除后本 effect 会重新跑，
    // 届时再按新的基线保存，用户的本地改动不会丢。
    if (isAiEditing) return;
    // AI 正在把正文流式写进这份文件（<file> 整章替换 / 新正文）：同样先不排自动保存。
    // 这时保存的是流式开始前的旧稿，会推进版本、让 AI 写完的整章按版本冲突作废。
    // 流式结束后本 effect 重新跑：服务端没变就照常保存作者的字；AI 的新稿落库了，
    // Editor 的回读（syncOpenFileFromServer）先打开对比审阅，两边的字都不丢。
    // 离开 / 切换文件时的最后一次保存（onFlushReady）不受影响。
    if (isStreaming) return;
    // 对比审阅进行中：由作者在审阅里决定保留哪一份，审阅完成时整体写回。
    // 此时再按旧令牌自动保存只会撞 409 并把审阅重置成初始状态。
    if (isReviewMode) return;

    saveTimeoutRef.current = setTimeout(async () => {
      await handleSaveRef.current();
    }, 3000);

    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
    };
  }, [isDirty, title, content, isAiEditing, isStreaming, showVersionHistory, isReviewMode, autosaveNudge]);

  // Handle save
  const handleSave = async (providedSubmission?: SaveSubmission): Promise<SaveOutcome> => {
    if (isFileRemoved(draftScopeRef.current.projectId, providedSubmission?.fileId ?? draftRef.current.fileId)) return "failed";
    if ((!dirtyRef.current && !providedSubmission) || isAiEditing || showVersionHistory) {
      return "failed";
    }

    const draft: SaveSubmission = providedSubmission ?? {
      ...draftRef.current,
      previousTitle: lastSavedTitleRef.current,
    };
    // Retain the old-file baseline when a switch flushes its immutable draft.
    const queuedBaselineContent = lastSavedContentRef.current;
    const queuedBaselineTitle = lastSavedTitleRef.current;
    const queuedBaseUpdatedAt = persistedBaseRef.current.fileId === draft.fileId
      ? persistedBaseRef.current.updatedAt : draft.baseUpdatedAt;
    const submissionKey = JSON.stringify([
      draft.fileId, draft.title, draft.content, draft.baseUpdatedAt,
    ]);
    if (activeSaveRef.current?.key === submissionKey) {
      return activeSaveRef.current.promise;
    }

    pendingSaveCountRef.current += 1;
    if (isMountedRef.current) setIsSaving(true);
    const execute = async (): Promise<SaveOutcome> => {
      if (isFileRemoved(draftScopeRef.current.projectId, draft.fileId)) return "failed";
      // A preceding successful save advances this baseline. Resolve it only
      // when this immutable draft reaches the front of the serialized queue.
      const isCurrentFile = latestFileIdRef.current === draft.fileId;
      const previousContent = isCurrentFile ? lastSavedContentRef.current : queuedBaselineContent;
      const previousTitle = isCurrentFile ? lastSavedTitleRef.current : queuedBaselineTitle;
      const contentChanged = draft.content !== previousContent;
      let versionIntent = draft.versionIntent;
      if (!versionIntent && contentChanged) {
        const wordCount = countWords(draft.content);
        versionIntent = Math.abs(draft.content.length - previousContent.length) > 10
          ? { change_type: "edit", change_source: "user", word_count: wordCount }
          : { skip_version: true, word_count: wordCount };
      }
      const submission: SaveSubmission = {
        ...draft, previousTitle, versionIntent,
        baseUpdatedAt: persistedBaseRef.current.fileId === draft.fileId
          ? persistedBaseRef.current.updatedAt : queuedBaseUpdatedAt,
      };
      const result = await onSave(submission);
      if (result.outcome !== "saved") {
        // Conflict/failure paths deliberately keep the old baseline, dirty
        // indicator and pending local edits. The parent may have opened a
        // diff review, but no save has completed yet.
        return result.outcome;
      }

      if (persistedBaseRef.current.fileId === submission.fileId) {
        persistedBaseRef.current = { fileId: submission.fileId, updatedAt: result.updatedAt };
      }
      if (latestFileIdRef.current === submission.fileId) {
        lastSavedContentRef.current = submission.content;
        lastSavedTitleRef.current = submission.title;
      }

      if (
        latestFileIdRef.current === submission.fileId &&
        latestTitleRef.current === submission.title &&
        latestContentRef.current === submission.content
      ) {
        dirtyRef.current = false;
        if (isMountedRef.current) {
          setIsDirty(false);
          setLastSaved(new Date());
        }
      } else if (latestFileIdRef.current === submission.fileId) {
        dirtyRef.current = true;
        if (isMountedRef.current) setIsDirty(true);
      }
      return "saved";
    };

    const queued = saveChainRef.current.then(execute, execute);
    saveChainRef.current = queued.catch(() => undefined);
    const result = (async (): Promise<SaveOutcome> => {
      try {
        return await queued;
      } catch (error) {
        logger.error("Failed to save:", error);
        return "failed";
      } finally {
        if (activeSaveRef.current?.key === submissionKey) activeSaveRef.current = null;
        pendingSaveCountRef.current -= 1;
        if (pendingSaveCountRef.current === 0 && isMountedRef.current) setIsSaving(false);
      }
    })();
    activeSaveRef.current = { key: submissionKey, promise: result };
    // 离开编辑器时的最后一次保存在卸载时才发出；工作台据此先等它落库再拉进度。
    trackEditorSave(result);
    return result;
  };

  handleSaveRef.current = handleSave;

  useEffect(() => {
    if (!onFlushReady) return;
    onFlushReady(async () => {
      if (!dirtyRef.current) return "saved";
      if (suppressRecoveredAutoSaveRef.current) return "failed";
      return handleSaveRef.current();
    });
    return () => {
      onFlushReady(null);
      // Editor has already synchronously retained the deleted file's draft.
      // Never PUT it, or overwrite that recovery snapshot during unmount.
      if (isFileRemoved(draftScopeRef.current.projectId, draftRef.current.fileId)) return;
      // 对比开着时，留下的应是「现在完成对比会保存的内容」（基于服务端新版本和它的令牌），
      // 而不是作者在旧版本上的整篇草稿：恢复那份草稿会把 AI 的新版本改回去。
      const reviewDraft = isReviewModeRef.current ? reviewLeaveDraftRef.current?.() ?? null : null;
      if (!dirtyRef.current && !reviewDraft) return;
      // 离开编辑器时最后一次保存。没保存成功（例如对比还开着、令牌已过期而撞 409）
      // 就把草稿写进本地快照，下次打开这份文件时会提示恢复，作者的字不会丢。
      const draft = reviewDraft ? { ...draftRef.current, ...reviewDraft } : draftRef.current;
      const { userId: ownerId, projectId: draftProjectId } = draftScopeRef.current;
      const keepDraftLocally = () => {
        if (!ownerId || !draftProjectId || !draft.fileId) return;
        writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
          userId: ownerId,
          projectId: draftProjectId,
          fileId: draft.fileId,
          title: draft.title,
          content: draft.content,
          baseUpdatedAt: draft.baseUpdatedAt,
          reason: "page-exit",
        }));
      };
      // 对比审阅或恢复后尚未编辑/保存的草稿只留本地，不在退出时自动写回。
      if (isReviewModeRef.current || suppressRecoveredAutoSaveRef.current) {
        keepDraftLocally();
        return;
      }
      void handleSaveRef.current().then((outcome) => {
        if (outcome !== "saved") keepDraftLocally();
      });
    };
  }, [onFlushReady]);

  // Handle keyboard shortcuts
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Normal editor shortcuts
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      handleSave();
      return;
    }

    // Diff review mode shortcuts
    // In review mode, we should not start new natural polish requests.
    if (isReviewMode) {
      // Shift+Y: Accept all
      if (e.shiftKey && (e.key === 'y' || e.key === 'Y')) {
        e.preventDefault();
        onAcceptAllEdits?.();
        return;
      }
      // Shift+N: Reject all
      if (e.shiftKey && (e.key === 'n' || e.key === 'N')) {
        e.preventDefault();
        onRejectAllEdits?.();
        return;
      }
      // Enter: Finish review
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        onFinishReview?.();
        return;
      }
      // Escape: Cancel review (reject all and exit)
      if (e.key === 'Escape') {
        e.preventDefault();
        onRejectAllEdits?.();
        // Small delay then finish
        setTimeout(() => onFinishReview?.(), 50);
        return;
      }
      return;
    }

    // Cmd/Ctrl + Shift + Q: Add selection to quote
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'q' || e.key === 'Q')) {
      e.preventDefault();
      const textarea = textareaRef.current;
      if (textarea && fileId) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value.substring(start, end).trim();
        if (text) {
          const displayTitle = fileTitle || title;
          addQuote(text, fileId, displayTitle);
        }
      }
      return;
    }

    // Cmd/Ctrl + Shift + R: Natural polish selection or whole open chapter via Agent
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'r' || e.key === 'R')) {
      e.preventDefault();
      void startNaturalPolish();
      return;
    }
  };

  // Handle title change
  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    suppressRecoveredAutoSaveRef.current = false;
    draftRef.current = { ...draftRef.current, title: e.target.value };
    onTitleChange(e.target.value);
    dirtyRef.current = true;
    setIsDirty(true);
  };

  // Handle content change
  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    suppressRecoveredAutoSaveRef.current = false;
    draftRef.current = { ...draftRef.current, content: e.target.value };
    onContentChange(e.target.value);
    dirtyRef.current = true;
    setIsDirty(true);

    // During IME composition (e.g. Chinese Pinyin), avoid resizing on each intermediate update.
    // We'll resize on `compositionend` / effect instead.
    if (!isComposingRef.current) {
      adjustTextareaHeight();
    }
  };

  const handleContentBlur = () => {
    adjustTextareaHeight(true);
  };

  // Handle rollback from version history
  const prepareHistoryRollback = async () => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    // Finish writes already queued before the dialog opened; don't flush an
    // unscheduled dirty draft that the restore confirmation will discard.
    await saveChainRef.current;
  };

  const handleRollback = async () => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    dirtyRef.current = false;
    setIsDirty(false);
    pendingBaselineSyncRef.current = true;
    pendingHistoryRestoreTokenRef.current = latestBaseUpdatedAtRef.current;
    // Refresh through the existing file owner without unmounting history's
    // quota prompt or clearing the omission toast with a page reload.
    await onHistoryRestore?.();
  };


  return (
    <div className="relative flex flex-col h-full bg-[hsl(var(--bg-canvas))]" onKeyDown={handleKeyDown}>
      {/* Diff Review Toolbar - shown when in review mode */}
      {isReviewMode && diffReviewState && onAcceptAllEdits && onRejectAllEdits && onFinishReview && (
        <DiffToolbar
          pendingEdits={diffReviewState.pendingEdits}
          onAcceptAll={onAcceptAllEdits}
          onRejectAll={onRejectAllEdits}
          onFinish={onFinishReview}
        />
      )}

      {/* Streaming indicator banner (kept mounted for symmetric exit) */}
      <EditorStreamingBanner
        open={isStreaming && !isReviewMode}
        isWaitingForFirstToken={!displayContent}
        title={title}
      />
      
      {/* Title bar */}
      <div
        className={`shrink-0 px-4 sm:px-6 ${
          isReviewMode
            ? 'bg-[hsl(var(--bg-canvas))] pb-1.5 pt-2'
            : 'bg-transparent pt-5 pb-3'
        }`}
      >
        <input
          ref={titleInputRef}
          type="text"
          value={title}
          onChange={handleTitleChange}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) {
              e.preventDefault();
              textareaRef.current?.focus();
            }
          }}
          disabled={readOnly || isStreaming || isReviewMode}
          placeholder={t('editor:placeholder.titlePlaceholder')}
          className={`w-full bg-transparent font-[family-name:var(--font-display)] font-bold text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] outline-none border-none ${
            isReviewMode ? 'text-[1.7rem] leading-tight' : 'text-xl'
          }`}
        />
      </div>

      {/* Content area - switch between normal editor and diff review */}
      {isReviewMode && diffReviewState && onAcceptEdit && onRejectEdit && onResetEdit ? (
        <DiffReviewSplitView
          originalContent={diffReviewState.originalContent}
          modifiedContent={diffReviewState.modifiedContent}
          pendingEdits={diffReviewState.pendingEdits}
          onAcceptEdit={onAcceptEdit}
          onRejectEdit={onRejectEdit}
          onResetEdit={onResetEdit}
        />
      ) : (
        <div
          ref={contentAreaRef}
          data-editor-scroll-container="true"
          className="flex-1 overflow-auto"
          onScroll={handleContentScroll}
          style={{ overflowAnchor: "none" }}
          {...bindPinchZoom()}
        >
          <div className="px-4 sm:px-6 py-4">
            {!content.trim() && !readOnly && !isStreaming && emptyHint?.(() => textareaRef.current?.focus())}
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={displayContent}
                onChange={handleContentChange}
                onSelect={(event) => handleSelection(event.currentTarget)}
                onMouseUp={(event) => handleSelection(event.currentTarget)}
                onBlur={handleContentBlur}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  isComposingRef.current = false;
                  // Resize after IME commits the final text.
                  requestAnimationFrame(() => adjustTextareaHeight());
                }}
                disabled={readOnly || isStreaming}
                aria-label={t('editor:placeholder.contentPlaceholder')}
                placeholder={isStreaming && !displayContent ? '' : t('editor:placeholder.contentPlaceholder')}
                className={`editor-content font-[family-name:var(--font-body)] w-full overflow-y-hidden bg-transparent text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] outline-none border-none resize-none leading-relaxed text-base ${isStreaming ? 'cursor-default' : ''}`}
                style={{ minHeight: `${SIMPLE_EDITOR_MIN_HEIGHT_PX}px`, fontSize: `${zoom}em` }}
              />
              {/* Calm editorial waiting state before the first streamed character arrives */}
              {isStreaming && !displayContent && (
                <EditorStreamingCanvasWaiting />
              )}
              {/* Mirror div for calculating selection position */}
              <div ref={mirrorRef} aria-hidden="true" />
              {/* Typing cursor for streaming */}
              {isStreaming && displayContent && (
                <span className="inline-block w-0.5 h-5 bg-[hsl(var(--accent-primary))] ml-0.5 animate-pulse absolute" style={{ transform: 'translateY(-1px)' }} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Status bar - hide during review mode */}
      {!isReviewMode && (
        <div className="shrink-0 px-3 sm:px-4 py-2 flex flex-wrap items-center justify-between gap-2 border-t border-[hsl(var(--separator-color)/0.7)] bg-[hsl(var(--bg-canvas))]">
          <div className="flex items-center gap-2.5 text-xs text-[hsl(var(--text-secondary))] whitespace-nowrap">
            <span
              data-testid="editor-word-count"
              title={t('editor:wordCountHint')}
              className="tabular-nums text-[hsl(var(--text-secondary))]"
            >
              {t('editor:wordCount')} <strong className="tabular-nums text-[hsl(var(--text-primary))]">{countWords(displayContent)}</strong>
            </span>
            {/* One paragraph per non-empty line: manuscripts indent each paragraph instead of leaving blank lines. */}
            <span data-testid="editor-paragraph-count" className="tabular-nums">
              {t('editor:paragraphCount')} <strong className="tabular-nums text-[hsl(var(--text-primary))]">{displayContent.split("\n").filter((line) => line.trim()).length}</strong>
            </span>
            {/* Zoom indicator - only show when zoomed */}
            {zoom !== 1 && (
              <button
                type="button"
                onClick={resetZoom}
                className="max-md:min-h-11 px-1.5 py-0.5 rounded-lg tabular-nums text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
                title={t('editor:resetZoom')}
              >
                {Math.round(zoom * 100)}%
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {/* Version history button */}
            {fileId && (
              <button
                type="button"
                onClick={() => setShowVersionHistory(true)}
                className="max-md:min-h-11 px-1.5 py-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-lg flex items-center gap-1 whitespace-nowrap transition-colors"
                title={t('versions:title')}
              >
                <History size={14} />
                {t('editor:history')}
              </button>
            )}

            {/* Natural polish (selection when highlighted, otherwise full open chapter via Agent) */}
            {fileId && (
              <button
                type="button"
                onClick={() => void startNaturalPolish()}
                disabled={
                  !projectId ||
                  readOnly ||
                  isNaturalPolishBusy ||
                  !hasPolishableContent
                }
                className="max-md:min-h-11 px-1.5 py-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-lg flex items-center gap-1 whitespace-nowrap transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                title={
                  !projectId
                    ? t("editor:naturalPolishMissingContext")
                    : !hasPolishableContent
                      ? t("editor:naturalPolishEmptyContent")
                      : selectedText
                        ? isLimitedAiQuota
                          ? t("editor:naturalPolishTooltipSelectionFree")
                          : t("editor:naturalPolishTooltipSelection")
                        : isLimitedAiQuota
                          ? t("editor:naturalPolishTooltipFree")
                          : t("editor:naturalPolishTooltip")
                }
              >
                {isFlushingNaturalPolish ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Sparkles size={14} />
                )}
                {isFlushingNaturalPolish
                  ? t("editor:naturalPolishWorking")
                  : selectedText
                    ? t("editor:naturalPolishSelection")
                    : t("editor:naturalPolish")}
              </button>
            )}

            {/* Save status */}
            {isSaving ? (
              <span className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1 whitespace-nowrap tabular-nums">
                <Clock size={12} className="animate-spin" />
                {t('editor:saving')}
              </span>
            ) : isDirty ? (
              <span className="text-xs text-[hsl(var(--warning-text))] whitespace-nowrap motion-banner-enter">{t('editor:unsaved')}</span>
            ) : lastSaved ? (
              <span className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1 whitespace-nowrap tabular-nums">
                <Check size={12} className="text-[hsl(var(--success-text))] motion-icon-pop" />
                <SavedAgoLabel savedAt={lastSaved} />
              </span>
            ) : null}

            {/* Save button */}
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={isSaving || !isDirty || readOnly || isStreaming}
              title={
                isStreaming
                  ? t('editor:saveUnavailableStreaming')
                  : isSaving
                    ? t('editor:saving')
                    : isDirty
                      ? t('editor:saveWithShortcut', { shortcut: isAppleSaveShortcutPlatform() ? '⌘S' : 'Ctrl+S' })
                      : t('editor:alreadySaved')
              }
              className="max-md:min-h-11 px-2.5 py-1.5 text-xs bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] rounded-lg hover:bg-[hsl(var(--primary-hover))] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 flex items-center gap-1.5 whitespace-nowrap transition-[transform,background-color,opacity] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.975] motion-reduce:active:scale-100"
            >
              <Save size={14} />
              {t('editor:save')}
            </button>
          </div>
        </div>
      )}

      {/* Version History Modal */}
      {isVersionHistoryMounted && fileId && (
        <FileVersionHistory
          open={isVersionHistoryOpen}
          fileId={fileId}
          fileTitle={title}
          hasUnsavedChanges={isDirty}
          onClose={() => setShowVersionHistory(false)}
          onBeforeRollback={prepareHistoryRollback}
          onRollback={handleRollback}
        />
      )}

      {/* Selection Toolbar (mounted during symmetric exit in browsers, unmounted immediately in tests) */}
      {isSelectionToolbarMounted && (
        <SelectionToolbar
          open={isSelectionToolbarOpen}
          text={selectedText}
          position={selectionPosition}
          onAdd={handleAddQuote}
          onClose={closeSelectionToolbar}
        />
      )}
    </div>
  );
};
