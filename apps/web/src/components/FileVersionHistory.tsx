import React, { useState, useEffect, useCallback, useRef } from "react";
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
import { fileVersionApi } from "../lib/api";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { formatRelativeTime } from "../lib/dateUtils";
import { describeVersionSummary } from "../lib/versionSummary";
import type { FileVersion, VersionComparison } from "../types";
import { DiffViewer } from "./DiffViewer";
import { Modal } from "./ui/Modal";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { logger } from "../lib/logger";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";

const VERSION_PAGE_SIZE = 50;

interface FileVersionHistoryProps {
  fileId: string;
  fileTitle: string;
  onClose: () => void;
  onBeforeRollback?: () => void | Promise<void>;
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
  getChangeTypeLabel: (changeType: string) => string;
  getChangeTypeBadgeClass: (changeType: string, changeSource: string) => string;
}

export const FileVersionHistory: React.FC<FileVersionHistoryProps> = ({
  fileId,
  fileTitle,
  onClose,
  onBeforeRollback,
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
  // Only the server can tell whether the live text is already in history.
  // A missing field (older server) leaves every row unmarked.
  const [currentVersionNumber, setCurrentVersionNumber] = useState<number | null>(null);
  const rollbackInFlightRef = useRef(false);
  const [preview, setPreview] = useState<{ content: string; versionNumber: number } | null>(null);
  const listRequestGenerationRef = useRef(0);
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

  useEffect(() => {
    fileContextGenerationRef.current += 1;
    setVersions([]);
    setTotal(0);
    setCurrentVersionNumber(null);
    setPendingRollback(null);
    setSelectedVersions([]);
    setComparison(null);
    setIsComparing(false);
    setShowComparison(false);
    setIsRollingBack(false);
    setPreview(null);
    setLoadingMore(false);
    void loadVersions(0, false);
    return () => {
      fileContextGenerationRef.current += 1;
      listRequestGenerationRef.current += 1;
    };
  }, [loadVersions]);

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
            defaultValue: 'The content was restored, but this restore was not added to version history.',
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
        defaultValue: 'Could not load this version. Please try again.',
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

  const getChangeTypeLabel = (changeType: string) => {
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

  const getChangeTypeBadgeClass = (changeType: string, changeSource: string) => {
    if (changeSource === "ai") {
      return "badge-primary";
    }
    if (changeType === "restore") {
      return "badge-warning";
    }
    if (changeType === "create") {
      return "badge-success";
    }
    return "badge";
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
    getChangeTypeBadgeClass,
  }) => {
    const version = versions[index];
    const summaryText = describeVersionSummary(version.change_summary, t);

    return (
      <div
        tabIndex={0}
        className={`p-3 hover:bg-[hsl(var(--bg-tertiary))] cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${
          selectedVersions.includes(version.version_number)
            ? "bg-[hsl(var(--accent-primary)/0.1)] border-l-2 border-l-[hsl(var(--accent-primary))]"
            : ""
        }`}
        onClick={() => onSelectVersion(version.version_number)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onSelectVersion(version.version_number);
          }
        }}
      >
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            {getChangeTypeIcon(
              version.change_type,
              version.change_source
            )}
            <span className="font-medium text-[hsl(var(--text-primary))]">
              v{version.version_number}
            </span>
            <span className={getChangeTypeBadgeClass(version.change_type, version.change_source)}>
              {getChangeTypeLabel(version.change_type)}
            </span>
            {currentVersionNumber !== null && version.version_number === currentVersionNumber && (
              <span className="badge-success">
                {t('currentText')}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onViewContent(version.version_number);
              }}
              className="p-1 hover:bg-[hsl(var(--bg-secondary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
              title={t('viewContent')}
            >
              <FileText size={14} className="text-[hsl(var(--text-secondary))]" />
            </button>
            <button
                onClick={(e) => {
                  e.stopPropagation();
                  onRollback(version.version_number);
                }}
                className="p-1 hover:bg-[hsl(var(--bg-secondary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
                title={t('rollback')}
                disabled={isRollingBack}
              >
                <RotateCcw size={14} className="text-[hsl(var(--text-secondary))]" />
              </button>
          </div>
        </div>

        <div className="mt-1 flex items-center gap-3 text-xs text-[hsl(var(--text-secondary))]">
          <span className="flex items-center gap-1">
            <Clock size={12} />
            {formatRelativeTime(version.created_at)}
          </span>
          <span>{t('wordCount', { count: version.word_count })}</span>
          {(version.lines_added > 0 ||
            version.lines_removed > 0) && (
            <span className="flex items-center gap-1">
              {version.lines_added > 0 && (
                <span className="text-[hsl(var(--success))] flex items-center">
                  <Plus size={12} />
                  {t('linesAdded', { count: version.lines_added })}
                </span>
              )}
              {version.lines_removed > 0 && (
                <span className="text-[hsl(var(--error))] flex items-center">
                  <Minus size={12} />
                  {t('linesRemoved', { count: version.lines_removed })}
                </span>
              )}
            </span>
          )}
        </div>

        {summaryText && (
          <div className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
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
        size="full"
        className="max-w-[900px] max-h-[80vh] p-0 overflow-hidden"
        showCloseButton={false}
      >
      {/* Header */}
      <div className="px-4 py-3 border-b border-[hsl(var(--border-color))] flex items-center justify-between bg-[hsl(var(--bg-secondary))]">
        <div className="flex items-center gap-2">
          <History size={18} className="text-[hsl(var(--text-secondary))]" />
          <h2 className="font-medium text-[hsl(var(--text-primary))]">{t('title')}</h2>
          <span className="text-sm text-[hsl(var(--text-secondary))]">- {fileTitle}</span>
        </div>
        <button
          onClick={handleClose}
          disabled={isRollingBack}
          aria-label={t('common:close')}
          className="p-1 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
        >
          <X size={18} className="text-[hsl(var(--text-secondary))]" />
        </button>
      </div>

      {/* Toolbar */}
      <div className="px-4 py-2 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {t('totalVersions', { count: total })}
          </span>
          {selectedVersions.length > 0 && (
            <span className="text-sm text-[hsl(var(--accent-primary))]">
              {t('selectedVersions', { count: selectedVersions.length })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
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
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex">
        {/* Version List */}
        <div
          className={`${
            showComparison || preview ? "w-1/3 border-r border-[hsl(var(--border-color))]" : "w-full"
          } overflow-y-auto bg-[hsl(var(--bg-primary))]`}
        >
          {loading && (
            <div className="p-8 text-center text-[hsl(var(--text-secondary))]">{t('loading')}</div>
          )}

          {error && (
            <div className="p-8 text-center text-[hsl(var(--error))]">{error}</div>
          )}

          {!loading && !error && versions.length === 0 && (
            <div className="p-8 text-center text-[hsl(var(--text-secondary))]">
              {t('noVersions')}
            </div>
          )}

          {!loading && !error && versions.length > 0 && (
            <div className="divide-y divide-[hsl(var(--border-color))] overflow-y-auto" style={{ height: 600 }}>
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
                  getChangeTypeBadgeClass={getChangeTypeBadgeClass}
                />
              ))}
              {versions.length < total && (
                <div className="p-3 flex justify-center">
                  {pageError && <span role="alert" className="text-[hsl(var(--error))]">{pageError}</span>}
                  <button
                    type="button"
                    className="btn"
                    disabled={loadingMore}
                    onClick={() => void loadVersions(versions.length, true)}
                  >
                    {loadingMore
                      ? t('loading')
                      : t('loadMore', { defaultValue: 'Load more' })}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Comparison Panel */}
        {showComparison && comparison && (
          <div className="w-2/3 flex flex-col bg-[hsl(var(--bg-primary))]">
            <div className="px-4 py-2 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] flex items-center justify-between">
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('versionTo', { v1: comparison.version1.number, v2: comparison.version2.number })}
              </span>
              <button
                onClick={() => setShowComparison(false)}
                className="p-1 hover:bg-[hsl(var(--bg-secondary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
              >
                <X size={14} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>
            <div className="flex-1 overflow-auto">
              <DiffViewer comparison={comparison} />
            </div>
          </div>
        )}

        {preview && (
          <div className="w-2/3 flex flex-col bg-[hsl(var(--bg-primary))]">
            <div className="px-4 py-2 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] flex items-center justify-between">
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('versionPreview', {
                  version: preview.versionNumber,
                  defaultValue: `Version ${preview.versionNumber} preview`,
                })}
              </span>
              <button
                type="button"
                onClick={() => setPreview(null)}
                className="p-1 hover:bg-[hsl(var(--bg-secondary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
                title={t('closePreview', { defaultValue: 'Close preview' })}
              >
                <X size={14} className="text-[hsl(var(--text-secondary))]" />
              </button>
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

      <UpgradePromptModal
        open={showUpgradeModal}
        onClose={() => setShowUpgradeModal(false)}
        source={fileVersionUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t("quota.limitTitle")}
        description={t("quota.limitDescription")}
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
