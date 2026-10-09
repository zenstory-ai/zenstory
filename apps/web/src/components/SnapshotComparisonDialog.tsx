import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { logger } from "../lib/logger";
import {
  GitCompare,
  Plus,
  Minus,
  Edit3,
  Loader2,
  AlertCircle,
  FileText,
  Folder,
  ArrowRight,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { fileVersionApi, versionApi } from '../lib/api';
import { formatFullDate } from '../lib/dateUtils';
import type { SnapshotComparison, VersionComparison } from '../types';
import { Modal } from './ui/Modal';
import { DiffViewer } from './DiffViewer';

type FileDiffState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'loaded'; comparison: VersionComparison };

interface SnapshotComparisonDialogProps {
  snapshotId1: string;
  snapshotId2: string;
  onClose: () => void;
}

export const SnapshotComparisonDialog: React.FC<SnapshotComparisonDialogProps> = ({
  snapshotId1,
  snapshotId2,
  onClose,
}) => {
  const { t } = useTranslation(['editor', 'common']);
  const [comparison, setComparison] = useState<SnapshotComparison | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-file text diffs load lazily the first time a writer opens them and
  // stay cached while the dialog is open, so collapsing is free.
  const [fileDiffs, setFileDiffs] = useState<Record<string, FileDiffState>>({});
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(() => new Set());
  const comparisonGeneration = useRef(0);

  useEffect(() => {
    let isCurrent = true;
    comparisonGeneration.current += 1;
    setFileDiffs({});
    setExpandedFiles(new Set());

    const loadComparison = async () => {
      setLoading(true);
      setError(null);

      try {
        const result = await versionApi.compare(snapshotId1, snapshotId2);
        if (isCurrent) {
          setComparison(result);
        }
      } catch (err) {
        if (isCurrent) {
          logger.error('Failed to load comparison:', err);
          setError(t('editor:versionHistory.loadFailed'));
        }
      } finally {
        if (isCurrent) {
          setLoading(false);
        }
      }
    };

    loadComparison();

    return () => {
      isCurrent = false;
    };
  }, [snapshotId1, snapshotId2, t]);

  const loadFileDiff = async (fileId: string, oldVersion: number, newVersion: number) => {
    const generation = comparisonGeneration.current;
    setFileDiffs((current) => ({ ...current, [fileId]: { status: 'loading' } }));
    try {
      const result = await fileVersionApi.compare(fileId, oldVersion, newVersion);
      if (generation !== comparisonGeneration.current) return;
      setFileDiffs((current) => ({ ...current, [fileId]: { status: 'loaded', comparison: result } }));
    } catch (err) {
      if (generation !== comparisonGeneration.current) return;
      logger.error('Failed to load file diff for snapshot comparison:', err);
      setFileDiffs((current) => ({ ...current, [fileId]: { status: 'error' } }));
    }
  };

  const toggleFileDiff = (fileId: string, oldVersion: number, newVersion: number) => {
    const isExpanded = expandedFiles.has(fileId);
    setExpandedFiles((current) => {
      const next = new Set(current);
      if (isExpanded) next.delete(fileId);
      else next.add(fileId);
      return next;
    });
    if (!isExpanded) {
      const state = fileDiffs[fileId];
      if (!state || state.status === 'error') void loadFileDiff(fileId, oldVersion, newVersion);
    }
  };

  const renderFileDiff = (fileId: string, oldVersion: number, newVersion: number) => {
    if (!expandedFiles.has(fileId)) return null;
    const state = fileDiffs[fileId];
    if (!state || state.status === 'loading') {
      return (
        <div className="flex items-center gap-2 py-3 text-xs text-[hsl(var(--text-secondary))]">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('editor:versionHistory.loadingChanges')}
        </div>
      );
    }
    if (state.status === 'error') {
      return (
        <div role="alert" className="flex items-center gap-2 py-3 text-xs text-[hsl(var(--error))]">
          <AlertCircle className="w-4 h-4" />
          <span>{t('editor:versionHistory.diffLoadFailed')}</span>
          <button
            type="button"
            onClick={() => void loadFileDiff(fileId, oldVersion, newVersion)}
            className="underline hover:no-underline"
          >
            {t('common:retry')}
          </button>
        </div>
      );
    }
    return (
      <div className="mt-2 max-h-[40vh] overflow-auto rounded border border-[hsl(var(--border-color))]">
        <DiffViewer comparison={state.comparison} defaultViewMode="inline" showLineNumbers={false} />
      </div>
    );
  };

  const getFileTypeIcon = (fileType?: string | null) => {
    return fileType === 'folder'
      ? <Folder className="w-4 h-4" />
      : <FileText className="w-4 h-4" />;
  };

  const getChangedText = (
    change: { old: unknown; new: unknown } | undefined,
    side: 'old' | 'new',
  ) => {
    const value = change?.[side];
    return typeof value === 'string' && value ? value : undefined;
  };

  const totalChanges =
    (comparison?.changes.added.length || 0) +
    (comparison?.changes.removed.length || 0) +
    (comparison?.changes.modified.length || 0);

  return (
    <Modal
      open={true}
      onClose={onClose}
      size="xl"
      title={
        <div className="flex items-center gap-2">
          <GitCompare className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
          <span>{t('editor:versionHistory.snapshotCompareTitle')}</span>
        </div>
      }
      footer={
        <button
          onClick={onClose}
          className="w-full px-4 py-2 bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-tertiary)/0.8)] text-[hsl(var(--text-primary))] rounded-lg transition-colors"
        >
          {t('common:close')}
        </button>
      }
    >
      <div className="max-h-[60vh] overflow-y-auto -mx-6 px-6">
        {loading && (
          <div className="flex flex-col items-center justify-center py-12 text-[hsl(var(--text-secondary))]">
            <Loader2 className="w-8 h-8 animate-spin mb-3" />
            <span>{t('editor:versionHistory.comparing')}</span>
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 text-[hsl(var(--error))] p-4 bg-[hsl(var(--error)/0.1)] rounded-lg">
            <AlertCircle className="w-5 h-5" />
            <span>{error}</span>
          </div>
        )}

        {!loading && !error && comparison && (
          <div className="space-y-6">
            {/* Snapshot Info */}
            <div className="flex items-center justify-between gap-4 p-4 bg-[hsl(var(--bg-tertiary))] rounded-lg">
              <div className="text-center flex-1">
                <div className="text-xs text-[hsl(var(--text-secondary))] mb-1">
                  {t('editor:versionHistory.oldVersion')}
                </div>
                <div className="text-sm text-[hsl(var(--text-primary))]">
                  {formatFullDate(comparison.snapshot1.created_at)}
                </div>
              </div>
              <ArrowRight className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
              <div className="text-center flex-1">
                <div className="text-xs text-[hsl(var(--text-secondary))] mb-1">
                  {t('editor:versionHistory.newVersion')}
                </div>
                <div className="text-sm text-[hsl(var(--text-primary))]">
                  {formatFullDate(comparison.snapshot2.created_at)}
                </div>
              </div>
            </div>

            {/* Summary */}
            <div className="flex gap-4">
              <div className="flex-1 p-3 bg-[hsl(var(--success)/0.1)] rounded-lg border border-[hsl(var(--success)/0.3)]">
                <div className="flex items-center gap-2 text-[hsl(var(--success))]">
                  <Plus className="w-4 h-4" />
                  <span className="font-medium">
                    {comparison.changes.added.length} {t('editor:versionHistory.added')}
                  </span>
                </div>
              </div>
              <div className="flex-1 p-3 bg-[hsl(var(--error)/0.1)] rounded-lg border border-[hsl(var(--error)/0.3)]">
                <div className="flex items-center gap-2 text-[hsl(var(--error))]">
                  <Minus className="w-4 h-4" />
                  <span className="font-medium">
                    {comparison.changes.removed.length} {t('editor:versionHistory.removed')}
                  </span>
                </div>
              </div>
              <div className="flex-1 p-3 bg-[hsl(var(--warning)/0.1)] rounded-lg border border-[hsl(var(--warning)/0.3)]">
                <div className="flex items-center gap-2 text-[hsl(var(--warning))]">
                  <Edit3 className="w-4 h-4" />
                  <span className="font-medium">
                    {comparison.changes.modified.length} {t('editor:versionHistory.modified')}
                  </span>
                </div>
              </div>
            </div>

            {/* No Changes */}
            {totalChanges === 0 && (
              <div className="text-center py-8 text-[hsl(var(--text-secondary))]">
                <GitCompare className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p>{t('editor:versionHistory.noDiff')}</p>
              </div>
            )}

            {/* Added Files */}
            {comparison.changes.added.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-[hsl(var(--success))] mb-2 flex items-center gap-2">
                  <Plus className="w-4 h-4" />
                  {t('editor:versionHistory.addedFiles')}
                </h3>
                <div className="space-y-2">
                  {comparison.changes.added.map((file) => (
                    <div
                      key={file.file_id}
                      className="flex items-center gap-3 p-3 bg-[hsl(var(--success)/0.05)] border border-[hsl(var(--success)/0.2)] rounded-lg"
                    >
                      <div className="text-[hsl(var(--success))]">
                        {getFileTypeIcon(file.file_type)}
                      </div>
                      <div className="flex-1">
                        <div className="text-sm text-[hsl(var(--text-primary))]">
                          {file.title || file.file_id}
                        </div>
                        {file.version_number != null && (
                          <div className="text-xs text-[hsl(var(--text-secondary))]">
                            {t('editor:versionHistory.versionPrefix')} {file.version_number}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Removed Files */}
            {comparison.changes.removed.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-[hsl(var(--error))] mb-2 flex items-center gap-2">
                  <Minus className="w-4 h-4" />
                  {t('editor:versionHistory.removedFiles')}
                </h3>
                <div className="space-y-2">
                  {comparison.changes.removed.map((file) => (
                    <div
                      key={file.file_id}
                      className="flex items-center gap-3 p-3 bg-[hsl(var(--error)/0.05)] border border-[hsl(var(--error)/0.2)] rounded-lg"
                    >
                      <div className="text-[hsl(var(--error))]">
                        {getFileTypeIcon(file.file_type)}
                      </div>
                      <div className="flex-1">
                        <div className="text-sm text-[hsl(var(--text-primary))] line-through opacity-70">
                          {file.title || file.file_id}
                        </div>
                        {file.version_number != null && (
                          <div className="text-xs text-[hsl(var(--text-secondary))]">
                            {t('editor:versionHistory.versionPrefix')} {file.version_number}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Modified Files */}
            {comparison.changes.modified.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-[hsl(var(--warning))] mb-2 flex items-center gap-2">
                  <Edit3 className="w-4 h-4" />
                  {t('editor:versionHistory.modifiedFiles')}
                </h3>
                <div className="space-y-2">
                  {comparison.changes.modified.map((file) => {
                    // A text diff needs two distinct recorded file versions.
                    const diffRange = file.old_version != null
                      && file.new_version != null
                      && file.old_version !== file.new_version
                      ? { from: file.old_version, to: file.new_version }
                      : null;
                    const isExpanded = expandedFiles.has(file.file_id);
                    return (
                    <div
                      key={file.file_id}
                      className="p-3 bg-[hsl(var(--warning)/0.05)] border border-[hsl(var(--warning)/0.2)] rounded-lg"
                    >
                      <div className="flex items-center gap-3">
                      <div className="text-[hsl(var(--warning))]">
                        {getFileTypeIcon(file.new_file_type || file.old_file_type)}
                      </div>
                      <div className="flex-1">
                        <div className="text-sm text-[hsl(var(--text-primary))]">
                          {(() => {
                            const oldTitle = file.old_title
                              || getChangedText(file.metadata_changes?.title, 'old')
                              || file.file_id;
                            const newTitle = file.new_title
                              || getChangedText(file.metadata_changes?.title, 'new')
                              || file.file_id;

                            return oldTitle === newTitle ? newTitle : (
                              <span className="inline-flex items-center gap-1">
                                <span>{oldTitle}</span>
                                <ArrowRight className="w-3 h-3" />
                                <span>{newTitle}</span>
                              </span>
                            );
                          })()}
                        </div>
                        {file.old_version != null && file.new_version != null ? (
                          <div className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1">
                            {t('editor:versionHistory.versionPrefix')} {file.old_version}
                            {file.old_version !== file.new_version && (
                              <>
                                <ArrowRight className="w-3 h-3" />
                                {t('editor:versionHistory.versionPrefix')} {file.new_version}
                              </>
                            )}
                          </div>
                        ) : (file.old_version ?? file.new_version) != null ? (
                          <div className="text-xs text-[hsl(var(--text-secondary))]">
                            {t('editor:versionHistory.versionPrefix')}{' '}
                            {file.old_version ?? file.new_version}
                          </div>
                        ) : null}
                      </div>
                      {diffRange && (
                        <button
                          type="button"
                          aria-expanded={isExpanded}
                          onClick={() => toggleFileDiff(file.file_id, diffRange.from, diffRange.to)}
                          className="flex items-center gap-1 px-2 py-1 text-xs rounded text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
                        >
                          {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                          {isExpanded ? t('editor:versionHistory.hideChanges') : t('editor:versionHistory.viewChanges')}
                        </button>
                      )}
                      </div>
                      {diffRange && renderFileDiff(file.file_id, diffRange.from, diffRange.to)}
                    </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
};

export default SnapshotComparisonDialog;
