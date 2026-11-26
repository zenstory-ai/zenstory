import { useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MutableRefObject, RefObject } from "react";
import { QueryClientContext } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { flushOpenEditor } from "../../lib/editorSaveTracker";
import { subscriptionQueryKeys } from "../../lib/subscriptionApi";
import { toast } from "../../lib/toast";
import type { QuotaResponse } from "../../types/subscription";
import type { NaturalPolishRequestParams, SaveOutcome, SaveSubmission } from "./types";

interface UseNaturalPolishOptions {
  projectId?: string;
  fileId?: string;
  fileType?: string;
  fileTitle?: string;
  displayContent: string;
  readOnly: boolean;
  isStreaming: boolean;
  isChatStreaming: boolean;
  isAiEditing: boolean;
  isReviewMode: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  selectionRangeRef: MutableRefObject<{ start: number; end: number } | null>;
  latestContentRef: MutableRefObject<string>;
  latestTitleRef: MutableRefObject<string>;
  latestFileIdRef: MutableRefObject<string | undefined>;
  dirtyRef: MutableRefObject<boolean>;
  suppressRecoveredAutoSaveRef: MutableRefObject<boolean>;
  isMountedRef: MutableRefObject<boolean>;
  handleSaveRef: MutableRefObject<(submission?: SaveSubmission) => Promise<SaveOutcome>>;
  dropSelection: () => void;
  switchToChat: () => void;
  onNaturalPolish?: (params: NaturalPolishRequestParams) => void;
}

export function useNaturalPolish({
  projectId,
  fileId,
  fileType,
  fileTitle,
  displayContent,
  readOnly,
  isStreaming,
  isChatStreaming,
  isAiEditing,
  isReviewMode,
  textareaRef,
  selectionRangeRef,
  latestContentRef,
  latestTitleRef,
  latestFileIdRef,
  dirtyRef,
  suppressRecoveredAutoSaveRef,
  isMountedRef,
  handleSaveRef,
  dropSelection,
  switchToChat,
  onNaturalPolish,
}: UseNaturalPolishOptions) {
  const { t } = useTranslation(["editor"]);
  const isReviewModeRef = useRef(isReviewMode);
  useLayoutEffect(() => {
    isReviewModeRef.current = isReviewMode;
  }, [isReviewMode]);

  // 只读 QuotaBadge 已经拉好的额度缓存，不为一个按钮提示另发请求；
  // 没有 QueryClientProvider（嵌入场景、单测）时按「额度未知」处理。
  const queryClient = useContext(QueryClientContext);
  const readCachedQuota = useCallback(
    () => queryClient?.getQueryData<QuotaResponse>(subscriptionQueryKeys.quota()),
    [queryClient]
  );
  const [cachedQuota, setCachedQuota] = useState(readCachedQuota);
  useEffect(() => {
    if (!queryClient) return;
    setCachedQuota(readCachedQuota());
    return queryClient.getQueryCache().subscribe(() => setCachedQuota(readCachedQuota()));
  }, [queryClient, readCachedQuota]);
  const isLimitedAiQuota = cachedQuota != null && cachedQuota.ai_conversations.limit !== -1;

  // Re-entrancy guard while flushing unsaved edits before dispatching ui_action="natural_polish".
  const [isFlushingNaturalPolish, setIsFlushingNaturalPolish] = useState(false);
  const flushingNaturalPolishRef = useRef(false);

  const isNaturalPolishBusy =
    isStreaming || isChatStreaming || isAiEditing || isFlushingNaturalPolish;
  const hasPolishableContent = Boolean(displayContent.trim());

  const startNaturalPolish = useCallback(async () => {
    if (
      flushingNaturalPolishRef.current ||
      isNaturalPolishBusy ||
      readOnly ||
      isReviewModeRef.current
    ) {
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
    textareaRef,
    latestContentRef,
    selectionRangeRef,
    dirtyRef,
    suppressRecoveredAutoSaveRef,
    handleSaveRef,
    latestFileIdRef,
    latestTitleRef,
    isMountedRef,
    dropSelection,
    onNaturalPolish,
    switchToChat,
  ]);

  return {
    isLimitedAiQuota,
    isFlushingNaturalPolish,
    isNaturalPolishBusy,
    hasPolishableContent,
    startNaturalPolish,
  };
}
