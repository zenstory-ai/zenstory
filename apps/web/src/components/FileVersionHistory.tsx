import React, { useState, useEffect, useCallback } from "react";
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
import type { FileVersion, VersionComparison } from "../types";
import { DiffViewer } from "./DiffViewer";
import { Modal } from "./ui/Modal";
import { logger } from "../lib/logger";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";

interface FileVersionHistoryProps {
  fileId: string;
  fileTitle: string;
  onClose: () => void;
  onRollback?: (versionNumber: number) => void;
  onViewContent?: (content: string, versionNumber: number) => void;
}

interface VersionRowWrapperProps {
  index: number;
  versions: FileVersion[];
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
  onRollback,
  onViewContent,
}) => {
  const { t } = useTranslation('versions');
  const fileVersionUpgradePrompt = getUpgradePromptDefinition("file_version_quota_blocked");
  const [versions, setVersions] = useState<FileVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [selectedVersions, setSelectedVersions] = useState<number[]>([]);
  const [comparison, setComparison] = useState<VersionComparison | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [showComparison, setShowComparison] = useState(false);
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);

  const loadVersions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fileVersionApi.getVersions(fileId, { limit: 50 });
      setVersions(response.versions);
      setTotal(response.total);
    } catch (err) {
      setError(t('loadFailed'));
      logger.error("Failed to load versions:", err);
    } finally {
      setLoading(false);
    }
  }, [fileId, t]);

  useEffect(() => {
    loadVersions();
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

    setIsComparing(true);
    try {
      const [v1, v2] = selectedVersions.sort((a, b) => a - b);
      const result = await fileVersionApi.compare(fileId, v1, v2);
      setComparison(result);
      setShowComparison(true);
    } catch (err) {
      logger.error("Failed to compare versions:", err);
      toast.error(t('compareFailed'));
    } finally {
      setIsComparing(false);
    }
  };

  const handleRollback = async (versionNumber: number) => {
    if (
      !confirm(t('rollbackConfirm', { version: versionNumber }))
    ) {
      return;
    }

    try {
      await fileVersionApi.rollback(fileId, versionNumber);
      await loadVersions();
      onRollback?.(versionNumber);
    } catch (err) {
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
      toast.error(t('rollbackFailed'));
    }
  };

  const handleViewContent = async (versionNumber: number) => {
    try {
      const response = await fileVersionApi.getVersionContent(
        fileId,
        versionNumber
      );
      onViewContent?.(response.content, versionNumber);
    } catch (err) {
      logger.error("Failed to get version content:", err);
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
    const key = typeMap[changeType];
    return key ? t(key) : changeType;
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
            {index === 0 && (
              <span className="badge-success">
                {t('latestSaved')}
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

        {version.change_summary && (
          <div className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
            {version.change_summary}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <Modal
        open={true}
        onClose={onClose}
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
          onClick={onClose}
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
            showComparison ? "w-1/3 border-r border-[hsl(var(--border-color))]" : "w-full"
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
      </div>
      </Modal>

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
