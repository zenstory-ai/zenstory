import React, { useState, useEffect, useCallback, useLayoutEffect, useRef } from "react";
import {
  History,
  RotateCcw,
  GitCompare,
  Bot,
  User,
  Settings,
  Clock,
  X,
  Plus,
  Minus,
  FileText,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { fileEditOperationApi, fileVersionApi } from "../lib/api";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { formatRelativeTime } from "../lib/dateUtils";
import { describeVersionSummary } from "../lib/versionSummary";
import type {
  FileEditOperationSummary,
  FileEditOperationUndoResponse,
  FileVersion,
  PendingEdit,
  VersionComparison,
} from "../types";
import { DiffViewer } from "./DiffViewer";
import { Modal } from "./ui/Modal";
import { Button } from "./ui/Button";
import { Badge } from "./ui/Badge";
import { IconButton } from "./ui/IconButton";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { logger } from "../lib/logger";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { useIsMobile } from "../hooks/useMediaQuery";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";
import { rebaseLocalEdits } from "../lib/rebaseLocalEdits";
import { applyPendingEditsToDiffs, buildParagraphReviewData, type ReviewDiffTuple } from "../lib/diffReview";

const VERSION_PAGE_SIZE = 50;

interface FileVersionHistoryProps {
  fileId: string;
  fileTitle: string;
  onClose: () => void;
  onBeforeRollback?: () => void | Promise<void>;
  /** Persist the current draft before a one-click operation undo. */
  onBeforeOperationUndo?: () => void | Promise<void>;
  onRollback?: (versionNumber: number) => void | Promise<void>;
  onViewContent?: (content: string, versionNumber: number) => void;
}

interface VersionRowWrapperProps {
  index: number;
  versions: FileVersion[];
  currentVersionNumber: number | null;
  selectedVersions: number[];
  t: (key: string, params?: Record<string, unknown>) => string;
  onSelectVersion: (versionNumber: number) => void;
  onViewContent: (versionNumber: number) => void;
  onRollback: (versionNumber: number) => void;
  getChangeTypeIcon: (changeType: string, changeSource: string) => React.ReactNode;
  getChangeTypeLabel: (changeType: string, changeSource: string) => string;
}

export const FileVersionHistory: React.FC<FileVersionHistoryProps> = ({
  fileId,
  fileTitle,
  onClose,
  onBeforeRollback,
  onBeforeOperationUndo,
  onRollback,
  onViewContent,
}) => {
  const { t } = useTranslation('versions');
  const fileVersionUpgradePrompt = getUpgradePromptDefinition("file_version_quota_blocked");
  const [versions, setVersions] = useState<FileVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [selectedVersions, setSelectedVersions] = useState<number[]>([]);
  const [comparison, setComparison] = useState<VersionComparison | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [showComparison, setShowComparison] = useState(false);
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);
  const [pendingRollback, setPendingRollback] = useState<number | null>(null);
  const [operations, setOperations] = useState<FileEditOperationSummary[]>([]);
  const [operationTotal, setOperationTotal] = useState(0);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [operationsLoadingMore, setOperationsLoadingMore] = useState(false);
  const [undoingOperationId, setUndoingOperationId] = useState<string | null>(null);
  const [operationConflict, setOperationConflict] = useState<{
    operation: FileEditOperationSummary;
    response: FileEditOperationUndoResponse;
    diffs: ReviewDiffTuple[];
    edits: PendingEdit[];
    conflictIds: string[];
    restoreMetadata: boolean;
  } | null>(null);
  // Only the server can tell whether the live text is already in history.
  // A missing field (older server) leaves every row unmarked.
  const [currentVersionNumber, setCurrentVersionNumber] = useState<number | null>(null);
  const rollbackInFlightRef = useRef(false);
  const [preview, setPreview] = useState<{ content: string; versionNumber: number } | null>(null);
  // Phones (<768px) hide the list (display:none) while a preview or comparison
  // is open, which drops its scroll position; remember it so closing returns to
  // the row. From 768px up the list stays visible beside the preview and the
  // author may keep scrolling it, so it is never treated as hidden there.
  const isMobile = useIsMobile();
  const listRef = useRef<HTMLDivElement>(null);
  const listScrollRef = useRef(0);
  const listHidden = isMobile && (showComparison || preview !== null);
  const listRequestGenerationRef = useRef(0);
  const operationRequestGenerationRef = useRef(0);
  const fileContextGenerationRef = useRef(0);
  const translateRef = useRef(t);

  useEffect(() => {
    translateRef.current = t;
  }, [t]);

  const loadVersions = useCallback(async (offset = 0, append = false) => {
    const requestGeneration = ++listRequestGenerationRef.current;
    const fileContextGeneration = fileContextGenerationRef.current;
    if (append) {
      setLoadingMore(true);
      setPageError(null);
    } else {
      setLoading(true);
      setError(null);
      setPageError(null);
    }
    try {
      const response = await fileVersionApi.getVersions(fileId, {
        limit: VERSION_PAGE_SIZE,
        offset,
      });
      if (
        requestGeneration !== listRequestGenerationRef.current ||
        fileContextGeneration !== fileContextGenerationRef.current
      ) {
        return;
      }
      if (!append) setCurrentVersionNumber(response.current_version_number ?? null);
      setVersions((current) => {
        if (!append) return response.versions;
        const existing = new Set(current.map((version) => version.version_number));
        return [
          ...current,
          ...response.versions.filter((version) => !existing.has(version.version_number)),
        ];
      });
      setTotal(response.total);
    } catch (err) {
      if (
        requestGeneration !== listRequestGenerationRef.current ||
        fileContextGeneration !== fileContextGenerationRef.current
      ) {
        return;
      }
      if (append) setPageError(translateRef.current('loadFailed'));
      else setError(translateRef.current('loadFailed'));
      logger.error("Failed to load versions:", err);
    } finally {
      if (
        requestGeneration === listRequestGenerationRef.current &&
        fileContextGeneration === fileContextGenerationRef.current
      ) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [fileId]);

  const loadOperations = useCallback(async (offset = 0, append = false) => {
    if (!fileEditOperationApi?.list) return;
    const requestGeneration = ++operationRequestGenerationRef.current;
    const fileContextGeneration = fileContextGenerationRef.current;
    setOperationError(null);
    if (append) setOperationsLoadingMore(true);
    try {
      const response = await fileEditOperationApi.list(fileId, { limit: 50, offset });
      if (
        requestGeneration !== operationRequestGenerationRef.current ||
        fileContextGeneration !== fileContextGenerationRef.current
      ) return;
      setOperations((current) => append ? [...current, ...response.operations] : response.operations);
      setOperationTotal(response.total);
    } catch (err) {
      if (
        requestGeneration !== operationRequestGenerationRef.current ||
        fileContextGeneration !== fileContextGenerationRef.current
      ) return;
      setOperationError(translateRef.current('operationLoadFailed', { defaultValue: 'AI 操作记录暂时没能加载' }));
      logger.error('Failed to load AI edit operations:', err);
    } finally {
      if (
        requestGeneration === operationRequestGenerationRef.current &&
        fileContextGeneration === fileContextGenerationRef.current
      ) setOperationsLoadingMore(false);
    }
  }, [fileId]);

  useEffect(() => {
    fileContextGenerationRef.current += 1;
    setVersions([]);
    setTotal(0);
    setCurrentVersionNumber(null);
    setPendingRollback(null);
    setOperations([]);
    setOperationTotal(0);
    setOperationError(null);
    setOperationsLoadingMore(false);
    setOperationConflict(null);
    setUndoingOperationId(null);
    setSelectedVersions([]);
    setComparison(null);
    setIsComparing(false);
    setShowComparison(false);
    setIsRollingBack(false);
    setPreview(null);
    setLoadingMore(false);
    listScrollRef.current = 0;
    void loadVersions(0, false);
    void loadOperations();
    return () => {
      fileContextGenerationRef.current += 1;
      listRequestGenerationRef.current += 1;
      operationRequestGenerationRef.current += 1;
    };
  }, [loadOperations, loadVersions]);

  useLayoutEffect(() => {
    if (listHidden) return;
    if (listRef.current) listRef.current.scrollTop = listScrollRef.current;
  }, [listHidden]);

  const handleSelectVersion = (versionNumber: number) => {
    if (selectedVersions.includes(versionNumber)) {
      setSelectedVersions(selectedVersions.filter((v) => v !== versionNumber));
    } else if (selectedVersions.length < 2) {
      setSelectedVersions([...selectedVersions, versionNumber]);
    } else {
      // Replace the older selection
      setSelectedVersions([selectedVersions[1], versionNumber]);
    }
  };

  const handleCompare = async () => {
    if (selectedVersions.length !== 2) return;

    const fileContextGeneration = fileContextGenerationRef.current;
    setIsComparing(true);
    try {
      const [v1, v2] = [...selectedVersions].sort((a, b) => a - b);
      const result = await fileVersionApi.compare(fileId, v1, v2);
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      setComparison(result);
      setPreview(null);
      setShowComparison(true);
    } catch (err) {
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      logger.error("Failed to compare versions:", err);
      toast.error(translateRef.current('compareFailed'));
    } finally {
      if (fileContextGeneration === fileContextGenerationRef.current) {
        setIsComparing(false);
      }
    }
  };

  const handleRollback = (versionNumber: number) => {
    if (rollbackInFlightRef.current) return;
    setPendingRollback(versionNumber);
  };

  const handleConfirmRollback = () => {
    const versionNumber = pendingRollback;
    setPendingRollback(null);
    if (versionNumber !== null) void performRollback(versionNumber);
  };

  const performRollback = async (versionNumber: number) => {
    if (rollbackInFlightRef.current) return;
    const fileContextGeneration = fileContextGenerationRef.current;
    let submitted = false;
    try {
      await onBeforeRollback?.();
      if (fileContextGeneration !== fileContextGenerationRef.current || rollbackInFlightRef.current) return;
      rollbackInFlightRef.current = true;
      submitted = true;
      setIsRollingBack(true);
      const result = await fileVersionApi.rollback(fileId, versionNumber);
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      if (!result.snapshot_created) {
        if (result.version_quota_exceeded) {
          toast.error(translateRef.current('quota.limitDescription'));
          if (fileVersionUpgradePrompt.surface === "modal") {
            setShowUpgradeModal(true);
          }
        } else {
          toast.error(translateRef.current('rollbackHistoryNotSaved', {
            defaultValue: '正文已恢复，但本次恢复未能写入版本历史。',
          }));
        }
      }
      await loadVersions(0, false);
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      await onRollback?.(versionNumber);
    } catch (err) {
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      if (
        err instanceof ApiError &&
        err.errorCode === "ERR_QUOTA_FILE_VERSIONS_EXCEEDED"
      ) {
        toast.error(handleApiError(err));
        if (fileVersionUpgradePrompt.surface === "modal") {
          setShowUpgradeModal(true);
        }
        return;
      }
      logger.error("Failed to rollback:", err);
      toast.error(translateRef.current('rollbackFailed'));
    } finally {
      if (submitted) {
        rollbackInFlightRef.current = false;
        if (fileContextGeneration === fileContextGenerationRef.current) setIsRollingBack(false);
      }
    }
  };

  const finishOperationUndo = useCallback(async (operation: FileEditOperationSummary) => {
    await Promise.all([loadOperations(), loadVersions(0, false)]);
    await onRollback?.(operation.before_version_number);
  }, [loadOperations, loadVersions, onRollback]);

  const submitOperationResolution = useCallback(async (
    operation: FileEditOperationSummary,
    response: FileEditOperationUndoResponse,
    resolvedContent: string,
    metadataResolution: 'preserve' | 'restore',
  ) => {
    if (!response.current?.updated_at || !response.current.content_sha256) throw new Error('Missing current edit snapshot');
    const result = await fileEditOperationApi.undo(fileId, operation.id, {
      expected_updated_at: response.current.updated_at,
      expected_after_content_sha256: response.current.content_sha256,
      resolved_content: resolvedContent,
      metadata_resolution: metadataResolution,
    });
    if (result.status !== 'undone') throw new Error('Edit operation changed during undo');
    await finishOperationUndo(operation);
  }, [fileId, finishOperationUndo]);

  const handleOperationUndo = useCallback(async (operation: FileEditOperationSummary) => {
    if (undoingOperationId || operation.undone_at) return;
    const fileContextGeneration = fileContextGenerationRef.current;
    setUndoingOperationId(operation.id);
    try {
      await (onBeforeOperationUndo ?? onBeforeRollback)?.();
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      const response = await fileEditOperationApi.undo(fileId, operation.id);
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      if (response.status === 'undone') {
        await finishOperationUndo(operation);
        return;
      }
      if (!response.before || !response.after || !response.current) {
        throw new Error('Incomplete edit operation merge response');
      }
      const rebased = rebaseLocalEdits(
        response.after.content,
        response.current.content,
        response.before.content,
      );
      if (rebased.conflictEditIds.length === 0 && !response.metadata_conflict) {
        await submitOperationResolution(operation, response, rebased.merged, 'preserve');
        return;
      }
      const review = buildParagraphReviewData(response.current.content, rebased.merged);
      const conflicts = new Set(rebased.conflictEditIds);
      setOperationConflict({
        operation,
        response,
        diffs: review.diffs,
        edits: review.pendingEdits.map((edit) => ({
          ...edit,
          status: conflicts.has(edit.id) ? 'rejected' : 'accepted',
        })),
        conflictIds: rebased.conflictEditIds,
        restoreMetadata: false,
      });
    } catch (err) {
      logger.error('Failed to undo AI edit operation:', err);
      toast.error(err instanceof ApiError ? handleApiError(err) : translateRef.current('rollbackFailed'));
    } finally {
      if (fileContextGeneration === fileContextGenerationRef.current) setUndoingOperationId(null);
    }
  }, [fileId, finishOperationUndo, onBeforeOperationUndo, onBeforeRollback, submitOperationResolution, undoingOperationId]);

  const submitOperationConflict = useCallback(async () => {
    if (!operationConflict || undoingOperationId) return;
    setUndoingOperationId(operationConflict.operation.id);
    try {
      await submitOperationResolution(
        operationConflict.operation,
        operationConflict.response,
        applyPendingEditsToDiffs(operationConflict.diffs, operationConflict.edits),
        operationConflict.restoreMetadata ? 'restore' : 'preserve',
      );
      setOperationConflict(null);
    } catch (err) {
      logger.error('Failed to resolve AI edit operation undo:', err);
      toast.error(err instanceof ApiError ? handleApiError(err) : translateRef.current('rollbackFailed'));
    } finally {
      setUndoingOperationId(null);
    }
  }, [operationConflict, submitOperationResolution, undoingOperationId]);

  const handleClose = () => {
    // Before submission close still cancels via the generation guard. Once a
    // write starts, retain the dialog until the owning editor is reconciled.
    if (!rollbackInFlightRef.current) onClose();
  };

  const handleViewContent = async (versionNumber: number) => {
    const fileContextGeneration = fileContextGenerationRef.current;
    try {
      const response = await fileVersionApi.getVersionContent(
        fileId,
        versionNumber
      );
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      if (onViewContent) {
        onViewContent(response.content, versionNumber);
      } else {
        setComparison(null);
        setShowComparison(false);
        setPreview({ content: response.content, versionNumber });
      }
    } catch (err) {
      if (fileContextGeneration !== fileContextGenerationRef.current) return;
      logger.error("Failed to get version content:", err);
      toast.error(translateRef.current('viewContentFailed', {
        defaultValue: '加载该版本失败，请重试',
      }));
    }
  };


  const getChangeTypeIcon = (_changeType: string, changeSource: string) => {
    if (changeSource === "ai") {
      return <Bot size={14} className="text-[hsl(var(--text-secondary))]" />;
    }
    if (changeSource === "system") {
      return <Settings size={14} className="text-[hsl(var(--text-secondary))]" />;
    }
    return <User size={14} className="text-[hsl(var(--accent-primary))]" />;
  };

  const getChangeTypeLabel = (changeType: string, changeSource: string) => {
    // The author saved after reviewing AI changes (accept/reject per paragraph):
    // the text is theirs, so the badge must not read "AI 编辑".
    if (changeType === 'ai_edit' && changeSource === 'user') return t('types.reviewed');
    const typeMap: Record<string, string> = {
      create: 'types.created',
      edit: 'types.edited',
      ai_edit: 'types.aiEdited',
      restore: 'types.rolledBack',
      auto_save: 'types.autoSave',
    };
    // 未知类型不露出内部取值，按普通编辑显示。
    return t(typeMap[changeType] ?? 'types.edited');
  };

  // VersionRow component for rendering version items
  const VersionRowWrapper: React.FC<VersionRowWrapperProps> = ({
    index,
    versions,
    currentVersionNumber,
    selectedVersions,
    t,
    onSelectVersion,
    onViewContent,
    onRollback,
    getChangeTypeIcon,
    getChangeTypeLabel,
  }) => {
    const version = versions[index];
    const summaryText = describeVersionSummary(version.change_summary, t);

    return (
      <div
        tabIndex={0}
        className={`px-4 py-3 border-l-2 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary)/0.6)] ${
          selectedVersions.includes(version.version_number)
            ? "bg-[hsl(var(--accent-primary)/0.1)] border-l-[hsl(var(--accent-primary))]"
            : "border-l-transparent hover:bg-[hsl(var(--bg-tertiary))]"
        }`}
        onClick={() => onSelectVersion(version.version_number)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onSelectVersion(version.version_number);
          }
        }}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2 py-1">
            <span className="text-sm font-semibold text-[hsl(var(--text-primary))]">
              v{version.version_number}
            </span>
            <Badge icon={getChangeTypeIcon(version.change_type, version.change_source)} className="whitespace-nowrap">
              {getChangeTypeLabel(version.change_type, version.change_source)}
            </Badge>
            {currentVersionNumber !== null && version.version_number === currentVersionNumber && (
              <Badge variant="success" className="whitespace-nowrap">
                {t('currentText')}
              </Badge>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <IconButton
              onClick={(e) => {
                e.stopPropagation();
                onViewContent(version.version_number);
              }}
              label={t('viewContent')}
              icon={<FileText size={16} />}
            />
            <IconButton
              onClick={(e) => {
                e.stopPropagation();
                onRollback(version.version_number);
              }}
              label={t('rollback')}
              icon={<RotateCcw size={16} />}
              disabled={isRollingBack}
            />
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[hsl(var(--text-secondary))]">
          <span className="inline-flex items-center gap-1 whitespace-nowrap">
            <Clock size={12} className="shrink-0" />
            {formatRelativeTime(version.created_at)}
          </span>
          <span className="whitespace-nowrap">{t('wordCount', { count: version.word_count })}</span>
          {(version.lines_added > 0 ||
            version.lines_removed > 0) && (
            <span className="inline-flex flex-wrap items-center gap-2">
              {version.lines_added > 0 && (
                <span className="text-[hsl(var(--success-text))] inline-flex items-center whitespace-nowrap">
                  <Plus size={12} />
                  {t('linesAdded', { count: version.lines_added })}
                </span>
              )}
              {version.lines_removed > 0 && (
                <span className="text-[hsl(var(--error-text))] inline-flex items-center whitespace-nowrap">
                  <Minus size={12} />
                  {t('linesRemoved', { count: version.lines_removed })}
                </span>
              )}
            </span>
          )}
        </div>

        {summaryText && (
          <div className="mt-2 break-words text-xs text-[hsl(var(--text-secondary))]">
            {summaryText}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <Modal
        open={true}
        onClose={handleClose}
        size="xl"
        className={`${showComparison || preview ? 'h-[min(80vh,720px)]' : 'max-h-[min(80vh,720px)]'} p-0 overflow-hidden`}
        bodyClassName="p-0 flex flex-col overflow-hidden"
        ariaLabel={`${t('title')} - ${fileTitle}`}
        showCloseButton={false}
      >
      {/* Header */}
      <div className="px-4 py-4 shrink-0 border-b border-[hsl(var(--border-color))] flex items-start justify-between gap-3 bg-[hsl(var(--bg-secondary))]">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <History size={18} className="shrink-0 text-[hsl(var(--text-secondary))]" />
            <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t('title')}</h2>
          </div>
          <p className="mt-1 break-words text-sm text-[hsl(var(--text-secondary))]">{fileTitle}</p>
        </div>
        <IconButton
          onClick={handleClose}
          disabled={isRollingBack}
          label={t('common:close')}
          icon={<X size={18} />}
        />
      </div>

      {/* Selection actions */}
      {selectedVersions.length > 0 && (
      <div className="px-4 py-2 shrink-0 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-[hsl(var(--accent-primary))]">
          {t('selectedVersions', { count: selectedVersions.length })}
        </span>
        {selectedVersions.length === 2 && (
          <button
            onClick={handleCompare}
            disabled={isComparing}
            className="btn btn-primary"
          >
            <GitCompare size={14} />
            {isComparing ? t('comparing') : t('compare')}
          </button>
        )}
      </div>
      )}

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-hidden flex">
        {/* Version List */}
        <div
          ref={listRef}
          onScroll={(e) => {
            if (!listHidden) listScrollRef.current = e.currentTarget.scrollTop;
          }}
          data-testid="version-list"
          // Under 768px the list is too narrow beside a preview (one character per
          // line), so the preview takes the whole dialog and closing it returns here.
          className={`${
            showComparison || preview
              ? "hidden md:block md:w-1/3 md:border-r md:border-[hsl(var(--border-color))]"
              : "w-full"
          } min-h-0 overflow-y-auto bg-[hsl(var(--bg-primary))]`}
        >
          {loading && (
            <div className="p-8 text-center text-[hsl(var(--text-secondary))]">{t('loading')}</div>
          )}

          {error && (
            <div role="alert" className="p-8 flex flex-col items-center gap-3 text-center text-[hsl(var(--error-text))]">
              <span>{error}</span>
              <Button variant="secondary" size={isMobile ? 'touch' : 'sm'} onClick={() => void loadVersions(0, false)}>
                {t('common:retry')}
              </Button>
            </div>
          )}

          {!loading && !error && !operationError && versions.length === 0 && operations.length === 0 && (
            <div className="p-8 text-center text-[hsl(var(--text-secondary))]">
              {t('noVersions')}
            </div>
          )}

          {!loading && operationError && (
            <div role="alert" className="border-b border-[hsl(var(--border-color))] p-4 text-sm text-[hsl(var(--error-text))]">
              <p>{operationError}</p>
              <Button className="mt-2" variant="secondary" size={isMobile ? 'touch' : 'sm'} onClick={() => void loadOperations(0, false)}>{t('common:retry')}</Button>
            </div>
          )}

          {!loading && !error && operations.length > 0 && (
            <section className="border-b border-[hsl(var(--border-color))]" aria-label={t('aiOperations', { defaultValue: '最近 AI 操作' })}>
              <h3 className="px-4 pb-1 pt-3 text-xs font-semibold text-[hsl(var(--text-secondary))]">
                {t('aiOperations', { defaultValue: '最近 AI 操作' })}
              </h3>
              <div className="divide-y divide-[hsl(var(--border-color))]">
                {operations.map((operation) => (
                  <div key={operation.id} className="flex items-center justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-[hsl(var(--text-primary))]">
                        {operation.kind === 'rename_file'
                          ? `${operation.before_title} → ${operation.after_title}`
                          : t(`operationKinds.${operation.kind}`, { defaultValue: operation.kind === 'write_chapter' ? 'AI 写作' : 'AI 编辑' })}
                      </p>
                      <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">{formatRelativeTime(operation.created_at)}</p>
                    </div>
                    {operation.undone_at ? (
                      <Badge variant="success" className="whitespace-nowrap">{t('operationUndone', { defaultValue: '已撤销' })}</Badge>
                    ) : (
                      <Button
                        type="button"
                        variant="secondary"
                        size="touch"
                        leftIcon={<RotateCcw size={14} />}
                        disabled={undoingOperationId !== null || isRollingBack}
                        isLoading={undoingOperationId === operation.id}
                        onClick={() => { void handleOperationUndo(operation); }}
                      >
                        {t('undoOperation', { defaultValue: '撤销' })}
                      </Button>
                    )}
                  </div>
                ))}
              </div>
              {operations.length < operationTotal && (
                <div className="flex justify-center p-3">
                  <Button variant="secondary" size="touch" isLoading={operationsLoadingMore} disabled={operationsLoadingMore} onClick={() => void loadOperations(operations.length, true)}>
                    {t('loadMoreOperations', { defaultValue: '加载更早的 AI 操作' })}
                  </Button>
                </div>
              )}
            </section>
          )}

          {!loading && !error && versions.length > 0 && (
            <div
              data-testid="version-list-rows"
              className="divide-y divide-[hsl(var(--border-color))]"
            >
              {versions.map((version, index) => (
                <VersionRowWrapper
                  key={version.version_number}
                  index={index}
                  versions={versions}
                  currentVersionNumber={currentVersionNumber}
                  selectedVersions={selectedVersions}
                  t={t}
                  onSelectVersion={handleSelectVersion}
                  onViewContent={handleViewContent}
                  onRollback={handleRollback}
                  getChangeTypeIcon={getChangeTypeIcon}
                  getChangeTypeLabel={getChangeTypeLabel}
                />
              ))}
              {versions.length < total && (
                <div className="p-3 flex justify-center">
                  {pageError && <span role="alert" className="text-[hsl(var(--error-text))]">{pageError}</span>}
                  <button
                    type="button"
                    className="btn"
                    disabled={loadingMore}
                    onClick={() => void loadVersions(versions.length, true)}
                  >
                    {loadingMore
                      ? t('loading')
                      : t('loadMore', { defaultValue: '加载更多' })}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Comparison Panel */}
        {showComparison && comparison && (
          <div className="w-full md:w-2/3 min-w-0 flex flex-col bg-[hsl(var(--bg-primary))]">
            <div className="px-4 py-2 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] flex items-center justify-between">
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('versionTo', { v1: comparison.version1.number, v2: comparison.version2.number })}
              </span>
              <IconButton
                onClick={() => setShowComparison(false)}
                label={t('closePreview', { defaultValue: '关闭预览' })}
                icon={<X size={14} />}
              />
            </div>
            <div className="flex-1 overflow-auto">
              <DiffViewer comparison={comparison} />
            </div>
          </div>
        )}

        {preview && (
          <div data-testid="version-preview" className="w-full md:w-2/3 min-w-0 flex flex-col bg-[hsl(var(--bg-primary))]">
            <div className="px-4 py-2 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] flex items-center justify-between gap-2">
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('versionPreview', {
                  version: preview.versionNumber,
                  defaultValue: '版本 {{version}} 预览',
                })}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => handleRollback(preview.versionNumber)}
                  disabled={isRollingBack}
                  className="btn text-xs"
                >
                  <RotateCcw size={14} />
                  {t('rollback')}
                </button>
                <IconButton
                  onClick={() => setPreview(null)}
                  label={t('closePreview', { defaultValue: '关闭预览' })}
                  icon={<X size={14} />}
                />
              </div>
            </div>
            <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-sm text-[hsl(var(--text-primary))] font-sans">
              {preview.content}
            </pre>
          </div>
        )}
      </div>
      </Modal>

      <ConfirmDialog
        open={pendingRollback !== null}
        onClose={() => setPendingRollback(null)}
        onConfirm={handleConfirmRollback}
        title={t('rollback')}
        message={t('rollbackConfirm', { version: pendingRollback ?? '' })}
        confirmLabel={t('rollbackConfirmButton')}
        cancelLabel={t('common:cancel')}
        variant="warning"
      />

      <Modal
        open={operationConflict !== null}
        onClose={() => { if (!undoingOperationId) setOperationConflict(null); }}
        title={t('undoConflictTitle', { defaultValue: '这几处和你后写的内容重叠' })}
        description={t('undoConflictDescription', { defaultValue: '默认保留你现在的文字；只选择确实要恢复的片段。' })}
        size="md"
        closeOnBackdropClick={!undoingOperationId}
        closeOnEscape={!undoingOperationId}
      >
        {operationConflict && (
          <div className="space-y-4">
            <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
              {operationConflict.edits
                .filter((edit) => operationConflict.conflictIds.includes(edit.id))
                .map((edit) => (
                  <label key={edit.id} className="block rounded-xl border border-[hsl(var(--border-color))] p-3">
                    <span className="flex min-h-11 items-center gap-2 text-sm font-medium text-[hsl(var(--text-primary))]">
                      <input
                        type="checkbox"
                        checked={edit.status === 'accepted'}
                        disabled={undoingOperationId !== null}
                        onChange={(event) => setOperationConflict((current) => current && ({
                          ...current,
                          edits: current.edits.map((item) => item.id === edit.id
                            ? { ...item, status: event.target.checked ? 'accepted' : 'rejected' }
                            : item),
                        }))}
                      />
                      {t('restoreThisOverlap', { defaultValue: '恢复这处 AI 修改前的文字' })}
                    </span>
                    {edit.oldText && <div className="mt-1"><span className="text-[11px] font-medium text-[hsl(var(--text-secondary))]">{t('currentTextLabel', { defaultValue: '当前文字' })}</span><p className="mt-1 whitespace-pre-wrap break-words text-xs text-[hsl(var(--text-secondary))]">{edit.oldText}</p></div>}
                    {edit.newText && <div className="mt-2"><span className="text-[11px] font-medium text-[hsl(var(--text-secondary))]">{t('restoreTextLabel', { defaultValue: '恢复为' })}</span><p className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-[hsl(var(--bg-secondary))] p-2 text-xs text-[hsl(var(--text-primary))]">{edit.newText}</p></div>}
                  </label>
                ))}
            </div>
            {operationConflict.response.metadata_conflict && (
              <div className="rounded-xl border border-[hsl(var(--border-color))] p-3">
                <dl className="grid gap-2 text-xs">
                  <div><dt className="text-[hsl(var(--text-secondary))]">{t('currentTitleLabel', { defaultValue: '当前标题' })}</dt><dd className="mt-0.5 break-words text-[hsl(var(--text-primary))]">{operationConflict.response.current?.title}</dd></div>
                  <div><dt className="text-[hsl(var(--text-secondary))]">{t('restoreTitleLabel', { defaultValue: '恢复为' })}</dt><dd className="mt-0.5 break-words text-[hsl(var(--text-primary))]">{operationConflict.response.before?.title}</dd></div>
                  {typeof operationConflict.response.current?.order === 'number' && <div><dt className="text-[hsl(var(--text-secondary))]">{t('currentPositionLabel', { defaultValue: '当前排序位置' })}</dt><dd className="mt-0.5 text-[hsl(var(--text-primary))]">{operationConflict.response.current.order}</dd></div>}
                  {typeof operationConflict.response.before?.order === 'number' && <div><dt className="text-[hsl(var(--text-secondary))]">{t('restorePositionLabel', { defaultValue: '恢复排序位置' })}</dt><dd className="mt-0.5 text-[hsl(var(--text-primary))]">{operationConflict.response.before.order}</dd></div>}
                </dl>
                <label className="mt-2 flex min-h-11 items-center gap-2 text-sm text-[hsl(var(--text-primary))]">
                  <input type="checkbox" checked={operationConflict.restoreMetadata} disabled={undoingOperationId !== null} onChange={(event) => setOperationConflict((current) => current && ({ ...current, restoreMetadata: event.target.checked }))} />
                  {t('restoreEarlierTitle', { defaultValue: '同时恢复这次修改前的标题和位置' })}
                </label>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="touch" onClick={() => setOperationConflict(null)} disabled={undoingOperationId !== null}>
                {t('common:cancel')}
              </Button>
              <Button size="touch" onClick={() => { void submitOperationConflict(); }} disabled={undoingOperationId !== null} isLoading={undoingOperationId !== null}>
                {t('undoOperation', { defaultValue: '撤销' })}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <UpgradePromptModal
        open={showUpgradeModal}
        onClose={() => setShowUpgradeModal(false)}
        source={fileVersionUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t("quota.limitTitle")}
        description={t("quota.limitDescription")}
        paidDescription={t("quota.paidLimitDescription", {
          defaultValue: "正文照常保存，只是这个文件不再生成新版本。",
        })}
        primaryLabel={t("quota.upgradePrimary")}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(fileVersionUpgradePrompt.billingPath, fileVersionUpgradePrompt.source)
          );
        }}
        secondaryLabel={t("quota.upgradeSecondary")}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(fileVersionUpgradePrompt.pricingPath, fileVersionUpgradePrompt.source)
          );
        }}
      />
    </>
  );
};

export default FileVersionHistory;
