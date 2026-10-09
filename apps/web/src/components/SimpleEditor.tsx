import { useState, useEffect, useRef, useCallback, useContext } from "react";
import { useTranslation } from "react-i18next";
import { QueryClientContext } from "@tanstack/react-query";
import { Save, Clock, Check, History, Sparkles, Loader2 } from "lucide-react";
import type { FileUpdateVersionIntent } from "../lib/api";
import { writingStatsApi } from "../lib/writingStatsApi";
import { FileVersionHistory } from "./FileVersionHistory";
import { DiffReviewSplitView } from "./DiffReviewSplitView";
import { DiffToolbar } from "./DiffToolbar";
import { SelectionToolbar } from "./SelectionToolbar";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { usePinchZoom } from "../hooks/useGestures";
import type { DiffReviewState } from "../types";
import { SavedAgoLabel } from "./SavedAgoLabel";
import { countWords } from "../lib/documentChunker";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";
import { preserveSelectionWhitespace } from "../lib/naturalPolish";
import { naturalPolishApi } from "../lib/naturalPolishApi";
import { subscriptionQueryKeys } from "../lib/subscriptionApi";
import type { QuotaResponse } from "../types/subscription";
import {
  BEFORE_CHUNK_RELOAD_EVENT,
  createEditorDraftSnapshot,
  writeEditorDraftSnapshot,
} from "../lib/editorDraftRecovery";
import { trackEditorSave } from "../lib/editorSaveTracker";

const isNearBottom = (el: HTMLElement, thresholdPx = 32) => {
  return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;
};

const SIMPLE_EDITOR_MIN_HEIGHT_PX = 200;

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
  userId?: string;
  fileId?: string;
  projectId?: string;
  fileType?: string;
  fileTitle?: string;
  baseUpdatedAt?: string;
  title: string;
  content: string;
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
  // Diff review props
  diffReviewState?: DiffReviewState | null;
  onEnterDiffReview?: (fileId: string, originalContent: string, newContent: string) => void;
  onAcceptEdit?: (editId: string) => void;
  onRejectEdit?: (editId: string) => void;
  onResetEdit?: (editId: string) => void;
  onAcceptAllEdits?: () => void;
  onRejectAllEdits?: () => void;
  onFinishReview?: () => void;
}

export const SimpleEditor = ({
  userId,
  fileId,
  projectId,
  fileType,
  fileTitle,
  baseUpdatedAt,
  title,
  content,
  onTitleChange,
  onContentChange,
  onSave,
  onHistoryRestore,
  onFlushReady,
  serverBaseline,
  getReviewLeaveDraft,
  readOnly = false,
  isStreaming = false,
  isAiEditing = false,
  recoveredDraft,
  // Diff review props
  diffReviewState,
  onEnterDiffReview,
  onAcceptEdit,
  onRejectEdit,
  onResetEdit,
  onAcceptAllEdits,
  onRejectAllEdits,
  onFinishReview,
}: SimpleEditorProps) => {
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
  // Selection toolbar state
  const [selectedText, setSelectedText] = useState("");
  const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const contentAreaRef = useRef<HTMLDivElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectionRangeRef = useRef<{ start: number; end: number } | null>(null);
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

  // Natural polish (de-AI tone) state
  const [isNaturalPolishRunning, setIsNaturalPolishRunning] = useState(false);
  const naturalPolishRunIdRef = useRef(0);
  const naturalPolishAbortRef = useRef<AbortController | null>(null);
  const naturalPolishBufferRef = useRef<string>("");
  const naturalPolishBaselineRef = useRef<{
    content: string;
    start: number;
    end: number;
    selection: string;
  } | null>(null);

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
    const isFocused = typeof document !== "undefined" && document.activeElement === textarea;

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
    const isFocused =
      typeof document !== "undefined" && document.activeElement === textareaRef.current;
    adjustTextareaHeight(!isFocused);
  }, [content, adjustTextareaHeight]);

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
    // clear selection and abort any in-flight natural polish requests.
    if (selectionTimeoutRef.current) {
      clearTimeout(selectionTimeoutRef.current);
      selectionTimeoutRef.current = null;
    }
    setSelectedText("");
    setSelectionPosition(null);
    selectionRangeRef.current = null;

    naturalPolishRunIdRef.current += 1;
    naturalPolishAbortRef.current?.abort();
    naturalPolishAbortRef.current = null;
    naturalPolishBaselineRef.current = null;
    naturalPolishBufferRef.current = "";
    setIsNaturalPolishRunning(false);
  }, [fileId, adjustTextareaHeight]);

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
      // If diffReviewState is cleared, we assume reviewed changes were saved.
      // Sync local save baseline so "unsaved" status doesn't get stuck.
      if (!diffReviewState) {
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
  }, [isReviewMode, diffReviewState, fileId, title, content, baseUpdatedAt, adjustTextareaHeight]);

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

  // Close selection toolbar
  const closeSelectionToolbar = useCallback(() => {
    setSelectionPosition(null);
  }, []);

  // Add quote from selection
  const handleAddQuote = useCallback(() => {
    if (!selectedText || !fileId) return;
    const displayTitle = fileTitle || title;
    addQuote(selectedText, fileId, displayTitle);
    closeSelectionToolbar();
  }, [selectedText, fileId, fileTitle, title, addQuote, closeSelectionToolbar]);

  const startNaturalPolish = useCallback(async () => {
    if (isNaturalPolishRunning) return;
    if (!projectId || !fileId) {
      toast.error(
        t("editor:naturalPolishMissingContext"),
      );
      return;
    }

    const textarea = textareaRef.current;
    if (!textarea) return;

    const range = selectionRangeRef.current;
    if (!range || range.start === range.end) {
      toast.info(t("editor:naturalPolishNoSelection"));
      return;
    }

    const { start, end } = range;
    const selection = textarea.value.substring(start, end);
    if (!selection.trim()) {
      toast.info(t("editor:naturalPolishNoSelection"));
      return;
    }

    const baselineContent = textarea.value;
    naturalPolishBaselineRef.current = { content: baselineContent, start, end, selection };
    naturalPolishBufferRef.current = "";
    const runId = naturalPolishRunIdRef.current + 1;
    naturalPolishRunIdRef.current = runId;

    // Cancel any previous in-flight request (defensive)
    naturalPolishAbortRef.current?.abort();

    setIsNaturalPolishRunning(true);
    const controller = new AbortController();
    naturalPolishAbortRef.current = controller;

    // 去AI味会预扣一条 AI 消息，没改动或失败时服务端再退还；
    // 无论结果如何，结束后都让额度胶囊重新拉一次，显示真实用量。
    const refreshAiQuota = () => {
      void queryClient?.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
      void queryClient?.invalidateQueries({ queryKey: subscriptionQueryKeys.quotaLite() });
    };

    try {
      const { text: rewrittenRaw, unchanged } = await naturalPolishApi.naturalPolish(
        {
          projectId,
          fileId,
          fileType,
          selectedText: selection,
        },
        { signal: controller.signal },
      );
      refreshAiQuota();

      if (naturalPolishRunIdRef.current !== runId) return;
      const baseline = naturalPolishBaselineRef.current;
      setIsNaturalPolishRunning(false);

      if (!baseline) {
        naturalPolishAbortRef.current = null;
        naturalPolishBaselineRef.current = null;
        naturalPolishBufferRef.current = "";
        return;
      }
      if (latestFileIdRef.current !== fileId) {
        // File switched while request was in flight; ignore this result.
        naturalPolishAbortRef.current = null;
        naturalPolishBaselineRef.current = null;
        naturalPolishBufferRef.current = "";
        return;
      }

      if (unchanged) {
        // 服务端判定没有实质改动并已退还额度：不进审阅，原文一个字节都不动。
        naturalPolishAbortRef.current = null;
        naturalPolishBaselineRef.current = null;
        naturalPolishBufferRef.current = "";
        toast.info(t("editor:naturalPolishNoChange"));
        return;
      }

      const rewrittenCore = rewrittenRaw.trim();
      if (!rewrittenCore) {
        naturalPolishAbortRef.current = null;
        naturalPolishBaselineRef.current = null;
        naturalPolishBufferRef.current = "";
        toast.error(t("editor:naturalPolishEmpty"));
        return;
      }

      const replacement = preserveSelectionWhitespace(baseline.selection, rewrittenCore);
      const modifiedContent =
        baseline.content.slice(0, baseline.start) +
        replacement +
        baseline.content.slice(baseline.end);

      naturalPolishAbortRef.current = null;
      naturalPolishBaselineRef.current = null;
      naturalPolishBufferRef.current = "";

      onEnterDiffReview?.(fileId, baseline.content, modifiedContent);
    } catch (error) {
      refreshAiQuota();
      // Abort is user-intentional (or file-switch cleanup); keep silent.
      if (controller.signal.aborted) {
        if (naturalPolishRunIdRef.current === runId) {
          setIsNaturalPolishRunning(false);
          naturalPolishAbortRef.current = null;
          naturalPolishBaselineRef.current = null;
          naturalPolishBufferRef.current = "";
        }
        return;
      }

      if (naturalPolishRunIdRef.current !== runId) return;
      setIsNaturalPolishRunning(false);
      naturalPolishAbortRef.current = null;
      naturalPolishBaselineRef.current = null;
      naturalPolishBufferRef.current = "";
      if (latestFileIdRef.current === fileId) {
        const errorMessage =
          error instanceof Error && error.message.trim()
            ? error.message
            : t("editor:naturalPolishFailed");
        toast.error(errorMessage);
      }
    }
  }, [
    isNaturalPolishRunning,
    projectId,
    fileId,
    fileType,
    t,
    onEnterDiffReview,
    queryClient,
  ]);

  // Cleanup selection timeout
  useEffect(() => {
    return () => {
      if (selectionTimeoutRef.current) {
        clearTimeout(selectionTimeoutRef.current);
      }
    };
  }, []);

  // Abort in-flight natural polish request when unmounting
  useEffect(() => {
    return () => {
      naturalPolishAbortRef.current?.abort();
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
  }, [content, isStreaming]);

  // Auto-save with debounce
  useEffect(() => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    if (!isDirty || showVersionHistory) return;
    if (isNaturalPolishRunning) return;
    if (suppressRecoveredAutoSaveRef.current) return;
    // AI 正在改这份文件：先不排自动保存。标记清除后本 effect 会重新跑，
    // 届时再按新的基线保存，用户的本地改动不会丢。
    if (isAiEditing) return;
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
  }, [isDirty, title, content, isNaturalPolishRunning, isAiEditing, showVersionHistory, isReviewMode, autosaveNudge]);

  // Handle save
  const handleSave = async (providedSubmission?: SaveSubmission): Promise<SaveOutcome> => {
    if ((!dirtyRef.current && !providedSubmission) || isNaturalPolishRunning || isAiEditing || showVersionHistory) {
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
        // indicator and pending writing stats. The parent may have opened a
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

      // Record daily writing stats for primary writing content.
      if (
        projectId &&
        (fileType === "draft" || fileType === "script") &&
        contentChanged
      ) {
        const previousWords = countWords(previousContent);
        const currentWords = countWords(submission.content);
        const wordsAdded = Math.max(currentWords - previousWords, 0);
        const wordsDeleted = Math.max(previousWords - currentWords, 0);

        try {
          await writingStatsApi.recordStats(projectId, {
            word_count: currentWords,
            words_added: wordsAdded,
            words_deleted: wordsDeleted,
          });
        } catch (statsError) {
          logger.error("Failed to record writing stats:", statsError);
        }
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
      return handleSaveRef.current();
    });
    return () => {
      onFlushReady(null);
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
      // 对比审阅打开时，草稿的令牌一定是旧的：不发注定 409 的请求。
      if (isReviewModeRef.current) {
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

    // Cmd/Ctrl + Shift + R: Natural polish selection
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'r' || e.key === 'R')) {
      e.preventDefault();
      startNaturalPolish();
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
    <div className="relative flex flex-col h-full bg-[hsl(var(--bg-primary))]" onKeyDown={handleKeyDown}>
      {/* Diff Review Toolbar - shown when in review mode */}
      {isReviewMode && diffReviewState && onAcceptAllEdits && onRejectAllEdits && onFinishReview && (
        <DiffToolbar
          pendingEdits={diffReviewState.pendingEdits}
          onAcceptAll={onAcceptAllEdits}
          onRejectAll={onRejectAllEdits}
          onFinish={onFinishReview}
        />
      )}

      {/* Streaming indicator overlay */}
      {isStreaming && !isReviewMode && (
        <div className="absolute top-0 left-0 right-0 z-10 bg-gradient-to-r from-[hsl(var(--accent-primary)/0.15)] to-[hsl(var(--accent-primary)/0.05)] px-6 py-3 flex items-center gap-3 border-b border-[hsl(var(--accent-primary)/0.2)] backdrop-blur-sm">
          <div className="relative">
            <Sparkles size={18} className="text-[hsl(var(--accent-primary))]" />
            <div className="absolute inset-0 animate-ping">
              <Sparkles size={18} className="text-[hsl(var(--accent-primary))] opacity-40" />
            </div>
          </div>
          <span className="text-sm text-[hsl(var(--accent-primary))] font-medium">{t('editor:aiWriting')}</span>
          <div className="flex gap-1">
            <span className="w-1.5 h-1.5 bg-[hsl(var(--accent-primary))] rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="w-1.5 h-1.5 bg-[hsl(var(--accent-primary))] rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="w-1.5 h-1.5 bg-[hsl(var(--accent-primary))] rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
        </div>
      )}
      
      {/* Title bar */}
      <div
        className={`shrink-0 px-6 ${
          isReviewMode
            ? 'bg-[hsl(var(--bg-primary))] pb-1.5 pt-2'
            : 'bg-[hsl(var(--bg-secondary)/0.3)] py-4'
        } ${isStreaming && !isReviewMode ? 'mt-12' : ''}`}
      >
        <input
          type="text"
          value={title}
          onChange={handleTitleChange}
          disabled={readOnly || isStreaming || isReviewMode || isNaturalPolishRunning}
          placeholder={t('editor:placeholder.titlePlaceholder')}
          className={`w-full bg-transparent font-bold text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] outline-none border-none ${
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
          <div className="px-6 py-4">
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={content}
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
                disabled={readOnly || isStreaming || isNaturalPolishRunning}
                placeholder={t('editor:placeholder.contentPlaceholder')}
                className={`w-full overflow-y-hidden bg-transparent text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] outline-none border-none resize-none leading-relaxed text-base ${isStreaming ? 'cursor-default' : ''}`}
                style={{ minHeight: `${SIMPLE_EDITOR_MIN_HEIGHT_PX}px`, fontSize: `${zoom}em` }}
              />
              {/* Mirror div for calculating selection position */}
              <div ref={mirrorRef} aria-hidden="true" />
              {/* Typing cursor for streaming */}
              {isStreaming && content && (
                <span className="inline-block w-0.5 h-5 bg-[hsl(var(--accent-primary))] ml-0.5 animate-pulse absolute" style={{ transform: 'translateY(-1px)' }} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Status bar - hide during review mode */}
      {!isReviewMode && (
        <div className="shrink-0 px-3 sm:px-6 py-2 flex flex-wrap items-center justify-between gap-2 bg-[hsl(var(--bg-secondary)/0.3)]">
          <div className="flex items-center gap-4 text-xs text-[hsl(var(--text-secondary))]">
            {/* Counts Chinese characters and English words only, so it reads lower than
                platforms that count punctuation; say so on hover and on tap. */}
            <button
              type="button"
              data-testid="editor-word-count"
              onClick={() => toast.info(t('editor:wordCountHint'))}
              title={t('editor:wordCountHint')}
              className="cursor-help text-[hsl(var(--text-secondary))] underline decoration-dotted decoration-[hsl(var(--text-tertiary))] underline-offset-4"
            >
              {t('editor:wordCount')} <strong className="text-[hsl(var(--text-primary))]">{countWords(content)}</strong>
            </button>
            <span>
              {t('editor:paragraphCount')} <strong className="text-[hsl(var(--text-primary))]">{content.split(/\n\n+/).filter(Boolean).length}</strong>
            </span>
            {/* Zoom indicator - only show when zoomed */}
            {zoom !== 1 && (
              <button
                onClick={resetZoom}
                className="text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
                title={t('editor:resetZoom')}
              >
                {Math.round(zoom * 100)}%
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-3">
            {/* Version history button */}
            {fileId && (
              <button
                onClick={() => setShowVersionHistory(true)}
                className="px-2 py-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-secondary))] rounded flex items-center gap-1 transition-colors"
                title={t('versions:title')}
              >
                <History size={14} />
                {t('editor:history')}
              </button>
            )}

            {/* Natural polish (selected text) */}
            {fileId && (
              <button
                onClick={startNaturalPolish}
                // No selection yet: stays pressable (dimmed) so a tap says why — the
                // hover title alone never reaches touch screens.
                disabled={
                  !projectId ||
                  readOnly ||
                  isStreaming ||
                  isNaturalPolishRunning
                }
                aria-disabled={!selectedText || undefined}
                className="px-2 py-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-secondary))] rounded flex items-center gap-1 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent aria-disabled:opacity-50"
                title={
                  !projectId
                    ? t("editor:naturalPolishMissingContext")
                    : !selectedText
                      ? t("editor:naturalPolishNoSelection")
                      : isLimitedAiQuota
                        ? t("editor:naturalPolishTooltipFree")
                        : t("editor:naturalPolishTooltip")
                }
              >
                {isNaturalPolishRunning ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Sparkles size={14} />
                )}
                {isNaturalPolishRunning
                  ? t("editor:naturalPolishWorking")
                  : t("editor:naturalPolish")}
              </button>
            )}

            {/* Save status */}
            {isSaving ? (
              <span className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1">
                <Clock size={12} className="animate-spin" />
                {t('editor:saving')}
              </span>
            ) : isDirty ? (
              <span className="text-xs text-[hsl(var(--warning))]">{t('editor:unsaved')}</span>
            ) : lastSaved ? (
              <span className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1">
                <Check size={12} className="text-[hsl(var(--success))]" />
                <SavedAgoLabel savedAt={lastSaved} />
              </span>
            ) : null}

            {/* Save button */}
            <button
              onClick={() => void handleSave()}
              disabled={isSaving || !isDirty || readOnly || isStreaming || isNaturalPolishRunning}
              className="px-3 py-1.5 text-xs bg-[hsl(var(--accent-primary))] text-white rounded hover:bg-[hsl(var(--accent-dark))] disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 transition-colors"
            >
              <Save size={14} />
              {t('editor:save')}
            </button>
          </div>
        </div>
      )}

      {/* Version History Modal */}
      {showVersionHistory && fileId && (
        <FileVersionHistory
          fileId={fileId}
          fileTitle={title}
          onClose={() => setShowVersionHistory(false)}
          onBeforeRollback={prepareHistoryRollback}
          onRollback={handleRollback}
        />
      )}

      {/* Selection Toolbar */}
      {selectedText && selectionPosition && fileId && (
        <SelectionToolbar
          text={selectedText}
          position={selectionPosition}
          onAdd={handleAddQuote}
          onClose={closeSelectionToolbar}
        />
      )}
    </div>
  );
};
