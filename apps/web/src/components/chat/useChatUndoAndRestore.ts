/**
 * AI-edit undo confirmation, version rollback with stale-write/quota handling,
 * undo-blocking computation, and soft-deletion restoration for ChatPanel.
 *
 * @module components/chat/useChatUndoAndRestore
 */

import { useCallback, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import type { TFunction } from "i18next";
import type { FileEditUndoTarget } from "../../types";
import type { StreamRenderItem } from "../../hooks/streaming/types";
import type { Message } from "../MessageList";
import { fileApi, fileVersionApi } from "../../lib/api";
import { ApiError } from "../../lib/apiClient";
import { handleApiError } from "../../lib/errorHandler";
import { clearFileRemoved } from "../../lib/fileRemovalEvents";
import { logger } from "../../lib/logger";
import { toast } from "../../lib/toast";
import { getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { undoneEditKey } from "../../lib/undoneEdits";
import { collectUndoCardRefs, undoBlockedByLaterWrite } from "../../lib/aiWriteCards";

export interface UseChatUndoAndRestoreOptions {
  currentProjectId: string | null;
  messages: Message[];
  setMessages: Dispatch<SetStateAction<Message[]>>;
  streamRenderItems: StreamRenderItem[] | undefined;
  triggerFileTreeRefresh: () => void;
  triggerEditorRefresh: (fileId: string) => void;
  t: TFunction;
}

export function useChatUndoAndRestore({
  currentProjectId,
  messages,
  setMessages,
  streamRenderItems,
  triggerFileTreeRefresh,
  triggerEditorRefresh,
  t,
}: UseChatUndoAndRestoreOptions) {
  const fileVersionUpgradePrompt = getUpgradePromptDefinition("file_version_quota_blocked");

  const [pendingUndoTarget, setPendingUndoTarget] = useState<FileEditUndoTarget | null>(null);
  const [undoneEdits, setUndoneEdits] = useState<ReadonlySet<string>>(() => new Set());
  // Cards whose undo the server refused as stale in this session (the file no longer holds that
  // edit's text). They can never be undone, so they stop holding back an earlier card's undo.
  const [staleUndoEdits, setStaleUndoEdits] = useState<ReadonlySet<string>>(() => new Set());
  const [isUndoing, setIsUndoing] = useState(false);
  const [showFileVersionUpgradeModal, setShowFileVersionUpgradeModal] = useState(false);

  // AI write cards that must wait for a later AI write to the same file to be undone first.
  const undoBlocked = useMemo(
    () =>
      undoBlockedByLaterWrite(
        collectUndoCardRefs(
          [...messages, { displayItems: streamRenderItems ?? [] }],
          staleUndoEdits.size > 0 ? new Set([...undoneEdits, ...staleUndoEdits]) : undoneEdits,
        ),
      ),
    [messages, streamRenderItems, undoneEdits, staleUndoEdits],
  );

  const handleUndo = useCallback((target: FileEditUndoTarget) => {
    setPendingUndoTarget(target);
  }, []);

  const handleCloseUndoDialog = useCallback(() => {
    if (isUndoing) return;
    setPendingUndoTarget(null);
  }, [isUndoing]);

  const handleConfirmUndo = useCallback(async () => {
    const target = pendingUndoTarget;
    if (!target) return;
    setIsUndoing(true);
    try {
      const result = await fileVersionApi.rollback(
        target.fileId,
        target.beforeVersionNumber,
        target.expectedAfterUpdatedAt,
        {
          chatMessageId: target.chatMessageId,
          toolUseId: target.toolUseId,
          expectedAfterContentSha256: target.expectedAfterContentSha256,
        },
      );
      setUndoneEdits((previous) => new Set(previous).add(undoneEditKey(target)));
      if (!result.snapshot_created) {
        if (result.version_quota_exceeded) {
          toast.error(t("versions:quota.limitDescription"));
          if (fileVersionUpgradePrompt.surface === "modal") {
            setShowFileVersionUpgradeModal(true);
          }
        } else {
          toast.error(t("versions:rollbackHistoryNotSaved"));
        }
      }
      triggerFileTreeRefresh();
      triggerEditorRefresh(target.fileId);
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.errorCode === "ERR_RESOURCE_CONFLICT" &&
        err.details?.reason === "stale_write"
      ) {
        toast.error(t("versions:undoStaleWrite"));
        setStaleUndoEdits((previous) => new Set(previous).add(undoneEditKey(target)));
        return;
      }
      if (err instanceof ApiError && err.errorCode === "ERR_QUOTA_FILE_VERSIONS_EXCEEDED") {
        toast.error(handleApiError(err));
        if (fileVersionUpgradePrompt.surface === "modal") {
          setShowFileVersionUpgradeModal(true);
        }
        return;
      }
      logger.error("Failed to undo:", err);
      toast.error(
        err instanceof ApiError
          ? handleApiError(err)
          : t("editor:versionHistory.rollbackFailed"),
      );
    } finally {
      setIsUndoing(false);
      setPendingUndoTarget(null);
    }
  }, [
    pendingUndoTarget,
    fileVersionUpgradePrompt.surface,
    triggerFileTreeRefresh,
    triggerEditorRefresh,
    t,
  ]);

  const handleRestoreDeletion = useCallback(
    async (fileId: string, card?: { chatMessageId?: string; toolUseId?: string }) => {
      try {
        const restored = await fileApi.restore(fileId, card);
        if (currentProjectId) {
          clearFileRemoved(currentProjectId, fileId);
          for (const id of restored.file_ids ?? []) {
            clearFileRemoved(currentProjectId, id);
          }
        }
        if (card?.chatMessageId && card?.toolUseId) {
          const markToolRestored = <T extends { id?: string; result?: unknown }>(item: T): T => {
            if (item.id !== card.toolUseId) return item;
            const prevResult =
              item.result && typeof item.result === "object"
                ? (item.result as Record<string, unknown>)
                : { id: fileId };
            return { ...item, result: { ...prevResult, restored: true } };
          };
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.backendMessageId !== card.chatMessageId && msg.id !== card.chatMessageId) {
                return msg;
              }
              return {
                ...msg,
                displayItems: msg.displayItems?.map((item) =>
                  item.type === "tool_calls" && item.toolCalls
                    ? { ...item, toolCalls: item.toolCalls.map(markToolRestored) }
                    : item,
                ),
                toolCalls: msg.toolCalls?.map(markToolRestored),
                toolResults: msg.toolResults?.map(markToolRestored),
              };
            }),
          );
        }
        triggerFileTreeRefresh();
        toast.success(t("chat:tool.deletionRestored"));
      } catch (err) {
        logger.error("Failed to restore deleted file:", err);
        toast.error(err instanceof ApiError ? handleApiError(err) : t("common:error"));
        throw err;
      }
    },
    [currentProjectId, setMessages, triggerFileTreeRefresh, t],
  );

  return {
    pendingUndoTarget,
    undoneEdits,
    undoBlocked,
    isUndoing,
    showFileVersionUpgradeModal,
    setShowFileVersionUpgradeModal,
    handleUndo,
    handleCloseUndoDialog,
    handleConfirmUndo,
    handleRestoreDeletion,
  };
}
