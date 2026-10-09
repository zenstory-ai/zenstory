/**
 * @fileoverview VersionHistoryPanel component - Version history viewer for project files.
 *
 * This component provides a modal panel for viewing and managing version snapshots,
 * handling:
 * - Snapshot history display with file/folder counts
 * - Snapshot description editing
 * - Version rollback with an in-app confirmation dialog
 * - Side-by-side version comparison
 * - Accessible modal shell (Esc to close, focus trap and focus return)
 * - Real-time snapshot loading with error handling
 * - Mobile-responsive layout
 *
 * @module components/VersionHistoryPanel
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Clock,
  RotateCcw,
  GitCompare,
  Edit2,
  X,
  Check,
  AlertCircle,
} from 'lucide-react';
import { logger } from '../lib/logger';
import { versionApi } from '../lib/api';
import { formatRelativeTimeWithYear } from '../lib/dateUtils';
import { toast } from '../lib/toast';
import { formatVersionSummary } from '../lib/versionSummary';
import type { Snapshot } from '../types';
import { SnapshotComparisonDialog } from './SnapshotComparisonDialog';
import { Modal } from './ui/Modal';
import { ConfirmDialog } from './ui/ConfirmDialog';

/**
 * Extended snapshot type with computed summary information.
 * Contains file and folder counts parsed from the snapshot data.
 */
interface SnapshotWithSummary extends Snapshot {
  /** Computed summary containing parsed file and folder counts */
  summary: {
    /** Number of file versions in this snapshot */
    file_count: number;
    /** Number of folders in this snapshot */
    folder_count: number;
  };
}

/**
 * Props for the VersionHistoryPanel component.
 */
interface VersionHistoryPanelProps {
  /** ID of the project to load snapshots for */
  projectId: string;
  /** Optional file ID to filter snapshots by specific file */
  outlineId?: string;
  /** Callback invoked when the panel is closed */
  onClose: () => void;
  /** Callback invoked after a successful rollback operation */
  onRollback?: (snapshotId: string) => void | Promise<void>;
  /** Callback invoked when comparing two snapshots */
  onCompare?: (snapshot1Id: string, snapshot2Id: string) => void;
}

export const VersionHistoryPanel: React.FC<VersionHistoryPanelProps> = ({
  projectId,
  outlineId,
  onClose,
  onRollback,
  onCompare,
}) => {
  const { t } = useTranslation(['editor', 'common']);
  const [snapshots, setSnapshots] = useState<SnapshotWithSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDescription, setEditDescription] = useState('');
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([]);
  const [showComparison, setShowComparison] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [nextOffset, setNextOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingRollbackId, setPendingRollbackId] = useState<string | null>(null);
  const contextGeneration = useRef(0);
  const requestGeneration = useRef(0);
  const listInFlight = useRef<number | null>(null);
  const actionInFlight = useRef<symbol | null>(null);
  const translate = useRef(t);

  useEffect(() => {
    translate.current = t;
  }, [t]);

  const loadSnapshots = useCallback(async (offset = 0, append = false) => {
    if (append && (listInFlight.current !== null || actionInFlight.current !== null)) return;
    const context = contextGeneration.current;
    const request = ++requestGeneration.current;
    listInFlight.current = request;
    if (append) setLoadingMore(true);
    else {
      setLoading(true);
      setError(null);
    }
    setPageError(null);

    try {
      const response = await versionApi.getSnapshots(projectId, {
        fileId: outlineId,
        limit: 50,
        ...(offset > 0 ? { offset } : {}),
      });
      if (context !== contextGeneration.current || request !== requestGeneration.current) return;

      // Parse data field to compute summary for each snapshot
      const snapshotsWithSummary: SnapshotWithSummary[] = response.map((snapshot) => {
        let fileCount = 0;
        let folderCount = 0;

        try {
          if (snapshot.data) {
            const data = JSON.parse(snapshot.data);
            fileCount = data.file_versions?.length || 0;
            folderCount = data.files_metadata?.filter(
              (f: { file_type?: string }) => f.file_type === 'folder'
            )?.length || 0;
          }
        } catch (e) {
          logger.warn('Failed to parse snapshot data:', e);
        }

        return {
          ...snapshot,
          summary: {
            file_count: fileCount,
            folder_count: folderCount,
          },
        };
      });

      setSnapshots((current) => {
        if (!append) return snapshotsWithSummary;
        const seen = new Set(current.map((snapshot) => snapshot.id));
        return [...current, ...snapshotsWithSummary.filter((snapshot) => {
          if (snapshot.id && seen.has(snapshot.id)) return false;
          seen.add(snapshot.id);
          return true;
        })];
      });
      setNextOffset(offset + response.length);
      setHasMore(response.length === 50);
    } catch (err) {
      if (context !== contextGeneration.current || request !== requestGeneration.current) return;
      if (append) setPageError(translate.current('editor:versionHistory.loadFailed'));
      else setError(translate.current('editor:versionHistory.loadFailed'));
      logger.error('Failed to load snapshots:', err);
    } finally {
      if (context === contextGeneration.current && request === requestGeneration.current) {
        listInFlight.current = null;
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [projectId, outlineId]);

  useEffect(() => {
    contextGeneration.current += 1;
    listInFlight.current = null;
    actionInFlight.current = null;
    setSnapshots([]);
    setNextOffset(0);
    setHasMore(false);
    setLoadingMore(false);
    setEditingId(null);
    setEditDescription('');
    setSelectedForCompare([]);
    setShowComparison(false);
    setActionBusy(false);
    setActionError(null);
    setPendingRollbackId(null);
    void loadSnapshots();
    return () => {
      contextGeneration.current += 1;
      requestGeneration.current += 1;
      actionInFlight.current = null;
    };
  }, [loadSnapshots]);

  const handleSaveDescription = async (snapshotId: string) => {
    if (actionInFlight.current !== null) return;
    const context = contextGeneration.current;
    const action = Symbol('description');
    actionInFlight.current = action;
    setActionBusy(true);
    setActionError(null);
    try {
      const updated = await versionApi.updateSnapshot(snapshotId, { description: editDescription });
      if (context !== contextGeneration.current) return;
      setSnapshots((current) => current.map((snapshot) => snapshot.id === snapshotId
        ? { ...snapshot, ...updated, description: updated?.description ?? editDescription }
        : snapshot));
      setEditingId(null);
      setEditDescription('');
    } catch (err) {
      if (context !== contextGeneration.current) return;
      setActionError(translate.current('editor:versionHistory.updateFailed'));
      logger.error('Failed to update description:', err);
    } finally {
      if (actionInFlight.current === action) {
        actionInFlight.current = null;
        setActionBusy(false);
      }
    }
  };

  const handleRollback = (snapshotId: string) => {
    if (actionInFlight.current !== null) return;
    setPendingRollbackId(snapshotId);
  };

  const handleConfirmRollback = () => {
    const snapshotId = pendingRollbackId;
    setPendingRollbackId(null);
    if (snapshotId) void performRollback(snapshotId);
  };

  const performRollback = async (snapshotId: string) => {
    if (actionInFlight.current !== null) return;
    const context = contextGeneration.current;
    const action = Symbol('rollback');
    actionInFlight.current = action;
    setActionBusy(true);
    setActionError(null);
    try {
      await versionApi.rollback(snapshotId);
      if (context !== contextGeneration.current) return;
      if (onRollback) {
        // The workbench callback reconciles and closes this panel. Do not issue
        // a throwaway history request before that required reconciliation.
        await onRollback(snapshotId);
      } else await loadSnapshots();
    } catch (err) {
      if (context !== contextGeneration.current) return;
      logger.error('Rollback failed:', err);
      toast.error(translate.current('editor:versionHistory.rollbackFailed'));
    } finally {
      if (actionInFlight.current === action) {
        actionInFlight.current = null;
        setActionBusy(false);
      }
    }
  };

  const handleSelectForCompare = (snapshotId: string) => {
    if (selectedForCompare.includes(snapshotId)) {
      setSelectedForCompare(selectedForCompare.filter((id) => id !== snapshotId));
    } else if (selectedForCompare.length < 2) {
      setSelectedForCompare([...selectedForCompare, snapshotId]);
    } else {
      // Replace the older selection
      setSelectedForCompare([selectedForCompare[1], snapshotId]);
    }
  };

  const handleCompare = () => {
    if (selectedForCompare.length === 2) {
      setShowComparison(true);
      if (onCompare) {
        onCompare(selectedForCompare[0], selectedForCompare[1]);
      }
    }
  };


  const getTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      auto: t('editor:versionHistory.auto'),
      manual: t('editor:versionHistory.manual'),
      pre_ai_edit: t('editor:versionHistory.beforeAI'),
      pre_rollback: t('editor:versionHistory.beforeRollback'),
    };
    return labels[type] || type;
  };

  // While a restore is reconciling, every close path (button, Esc, backdrop)
  // stays inert so the workbench can finish swapping the project contents.
  const handleClose = () => {
    if (actionInFlight.current === null) onClose();
  };

  return (
    <>
      <Modal
        open={true}
        onClose={handleClose}
        size="xl"
        showCloseButton={false}
        closeOnEscape={!actionBusy}
        closeOnBackdropClick={!actionBusy}
        className="relative max-h-[80vh]"
        title={
          <span className="flex items-center gap-2 pr-32">
            <Clock className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            <span>{t('editor:versionHistory.title')}</span>
          </span>
        }
        description={t('editor:versionHistory.intro')}
      >
        {/* Header actions sit in the dialog's top-right corner. */}
        <div className="absolute right-4 top-5 flex items-center gap-2">

            {selectedForCompare.length === 2 && (
              <button
                onClick={handleCompare}
                className="flex items-center gap-1 px-3 py-1.5 bg-[hsl(var(--accent-primary))] text-white rounded-md text-sm hover:bg-[hsl(var(--accent-primary-hover))]"
              >
                <GitCompare className="w-4 h-4" />
                {t('editor:versionHistory.compare')}
              </button>
            )}

            <button
              type="button"
              onClick={handleClose}
              disabled={actionBusy}
              aria-label={t('common:close')}
              className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded-md text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
            >
              <X className="w-5 h-5" />
            </button>
        </div>

        {/* Content */}
        <div>
          {loading && (
            <div className="text-center text-[hsl(var(--text-secondary))] py-8">{t('common:loading')}</div>
          )}

          {error && (
            <div className="flex items-center gap-2 text-[hsl(var(--error))] p-4 bg-[hsl(var(--error)/0.1)] rounded-lg">
              <AlertCircle className="w-5 h-5" />
              <span>{error}</span>
              <button onClick={() => void loadSnapshots()}>{t('common:retry')}</button>
            </div>
          )}

          {actionError && <div role="alert" className="text-[hsl(var(--error))] p-2">{actionError}</div>}

          {!loading && !error && snapshots.length === 0 && (
            <div className="text-center text-[hsl(var(--text-secondary))] py-8">{t('editor:versionHistory.empty')}</div>
          )}

          {!loading && !error && snapshots.length > 0 && (
            <div className="space-y-3">
              {snapshots.map((snapshot, index) => {
                const snapshotId = snapshot.id;
                const isSelectable = typeof snapshotId === 'string' && snapshotId.length > 0;
                const isSelected = isSelectable ? selectedForCompare.includes(snapshotId) : false;

                return (
                <div
                  key={isSelectable ? snapshotId : `snapshot-${index}`}
                  className={`border rounded-lg p-4 transition-colors ${
                    isSelected
                      ? 'border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.1)]'
                      : 'border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] hover:bg-[hsl(var(--bg-hover))]'
                  }`}
                >
                  {/* Header Row */}
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1">
                      {index === 0 && (
                        <span className="inline-block px-2 py-0.5 text-xs bg-[hsl(var(--success))] text-white rounded mr-2">
                          {t('editor:versionHistory.latestSaved')}
                        </span>
                      )}
                      <span className="text-xs text-[hsl(var(--text-secondary))]">
                        {getTypeLabel(snapshot.snapshot_type || 'auto')}
                      </span>
                      <div className="text-sm text-[hsl(var(--text-primary))] mt-1">
                        {snapshot.created_at ? formatRelativeTimeWithYear(snapshot.created_at) : '-'}
                      </div>
                    </div>

                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => {
                          if (snapshotId) handleSelectForCompare(snapshotId);
                        }}
                        disabled={!isSelectable || actionBusy}
                        className={`p-1.5 rounded ${
                          isSelected
                            ? 'bg-[hsl(var(--accent-primary))] text-white'
                            : 'hover:bg-[hsl(var(--bg-hover))] text-[hsl(var(--text-primary))]'
                        } ${!isSelectable ? 'opacity-50 cursor-not-allowed' : ''}`}
                        title={t('editor:versionHistory.selectCompare')}
                      >
                        <GitCompare className="w-4 h-4" />
                      </button>

                      <button
                          onClick={() => {
                            if (snapshotId) handleRollback(snapshotId);
                          }}
                          disabled={!isSelectable || actionBusy}
                          className={`p-1.5 hover:bg-[hsl(var(--bg-hover))] rounded text-[hsl(var(--text-primary))] ${!isSelectable ? 'opacity-50 cursor-not-allowed' : ''}`}
                          title={t('editor:versionHistory.rollbackTo')}
                        >
                          <RotateCcw className="w-4 h-4" />
                        </button>
                    </div>
                  </div>

                  {/* Description */}
                  {editingId === snapshot.id ? (
                    <div className="flex items-center gap-2 mb-2">
                      <input
                        type="text"
                        value={editDescription}
                        onChange={(e) => setEditDescription(e.target.value)}
                        className="flex-1 px-2 py-1 bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] rounded text-sm text-[hsl(var(--text-primary))]"
                        placeholder={t('editor:versionHistory.addDescription')}
                        autoFocus
                        disabled={actionBusy}
                      />
                      <button
                        onClick={() => {
                          if (snapshotId) handleSaveDescription(snapshotId);
                        }}
                        disabled={!isSelectable || actionBusy}
                        className={`p-1.5 bg-[hsl(var(--success))] text-white rounded hover:bg-[hsl(var(--success-dark))] ${!isSelectable ? 'opacity-50 cursor-not-allowed' : ''}`}
                      >
                        <Check className="w-4 h-4" />
                      </button>
                      <button
                        disabled={actionBusy}
                        onClick={() => {
                          setEditingId(null);
                          setEditDescription('');
                        }}
                        className="p-1.5 bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] rounded hover:bg-[hsl(var(--bg-hover))]"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-start gap-2 mb-2">
                      <p className="flex-1 text-sm text-[hsl(var(--text-secondary))]">
                        {formatVersionSummary(snapshot.description, t) || (
                          <span className="text-[hsl(var(--text-secondary))]">{t('editor:versionHistory.noDescription')}</span>
                        )}
                      </p>
                      <button
                        onClick={() => {
                          if (!snapshotId) return;
                          setEditingId(snapshotId);
                          setEditDescription(formatVersionSummary(snapshot.description, t));
                        }}
                        disabled={!isSelectable || actionBusy}
                        className={`p-1 hover:bg-[hsl(var(--bg-hover))] rounded text-[hsl(var(--text-secondary))] ${!isSelectable ? 'opacity-50 cursor-not-allowed' : ''}`}
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}

                  {/* Summary */}
                  <div className="flex items-center gap-4 text-xs text-[hsl(var(--text-secondary))]">
                    <span>{snapshot.summary.file_count} {t('editor:versionHistory.files')}</span>
                    <span>{snapshot.summary.folder_count} {t('editor:versionHistory.folders')}</span>
                  </div>
                </div>
                );
              })}
              {pageError && <div role="alert" className="text-[hsl(var(--error))]">{pageError}</div>}
              {hasMore && (
                <button
                  onClick={() => void loadSnapshots(nextOffset, true)}
                  disabled={loadingMore || actionBusy}
                  className="w-full py-2 rounded-md text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-hover))] disabled:opacity-50"
                >
                  {loadingMore ? t('common:loading') : pageError ? t('common:retry') : t('editor:versionHistory.loadMore')}
                </button>
              )}
            </div>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={pendingRollbackId !== null}
        onClose={() => setPendingRollbackId(null)}
        onConfirm={handleConfirmRollback}
        title={t('editor:versionHistory.confirmRollbackTitle')}
        message={t('editor:versionHistory.confirmRollback')}
        confirmLabel={t('editor:versionHistory.confirmRollbackButton')}
        cancelLabel={t('common:cancel')}
        variant="warning"
      />

      {/* Comparison Dialog */}
      {showComparison && selectedForCompare.length === 2 && (
        <SnapshotComparisonDialog
          snapshotId1={selectedForCompare[0]}
          snapshotId2={selectedForCompare[1]}
          onClose={() => {
            setShowComparison(false);
            setSelectedForCompare([]);
          }}
        />
      )}
    </>
  );
};

export default VersionHistoryPanel;
