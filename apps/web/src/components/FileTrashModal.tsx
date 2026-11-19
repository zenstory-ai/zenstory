import React from 'react';
import { useTranslation } from 'react-i18next';
import { History, RotateCcw } from 'lucide-react';
import type { ProjectTrashController } from '../hooks/useProjectTrash';
import { Button } from './ui/Button';
import { Modal } from './ui/Modal';

export const FileTrashTrigger: React.FC<{
  onClick: () => void;
  className?: string;
}> = ({ onClick, className = '' }) => {
  const { t } = useTranslation(['editor']);
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-xs text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] ${className}`}
    >
      <History size={14} />
      {t('editor:fileTree.trash')}
    </button>
  );
};

export const FileTrashModal: React.FC<{
  trash: ProjectTrashController;
}> = ({ trash }) => {
  const { t } = useTranslation(['editor', 'common', 'chat']);
  const {
    open,
    loading,
    error,
    batches,
    nextOffset,
    loadingMore,
    restoringBatchId,
    openTrash,
    closeTrash,
    loadMore,
    restoreBatch,
  } = trash;

  return (
    <Modal
      open={open}
      onClose={closeTrash}
      title={t('editor:fileTree.trash')}
      size="md"
      closeOnBackdropClick={!restoringBatchId}
      closeOnEscape={!restoringBatchId}
    >
      {loading ? (
        <p className="py-6 text-center text-sm text-[hsl(var(--text-secondary))]">{t('common:loading')}</p>
      ) : error ? (
        <div role="alert" className="py-6 text-center text-sm text-[hsl(var(--error-text))]">
          <p>{t('editor:fileTree.trashLoadFailed')}</p>
          <Button className="mt-3" variant="secondary" size="touch" onClick={() => { void openTrash(); }}>
            {t('common:retry')}
          </Button>
        </div>
      ) : batches.length === 0 ? (
        <p className="py-6 text-center text-sm text-[hsl(var(--text-secondary))]">{t('editor:fileTree.trashEmpty')}</p>
      ) : (
        <div className="max-h-[60vh] space-y-2 overflow-y-auto">
          {batches.map((batch) => (
            <div key={batch.batch_id} className="rounded-xl border border-[hsl(var(--border-color))] p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium text-[hsl(var(--text-primary))]">
                    {batch.titles?.slice(0, 3).join('、') || t('chat:tool.deletedCount', { count: batch.count })}
                  </p>
                  <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
                    {t('editor:fileTree.deletedAt', { date: new Date(batch.created_at).toLocaleString() })}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  size="touch"
                  leftIcon={<RotateCcw size={14} />}
                  disabled={restoringBatchId !== null}
                  isLoading={restoringBatchId === batch.batch_id}
                  onClick={() => { void restoreBatch(batch.batch_id); }}
                >
                  {t('editor:fileTree.restoreBatch')}
                </Button>
              </div>
            </div>
          ))}
          {nextOffset !== null && (
            <div className="flex justify-center pt-2">
              <Button
                type="button"
                variant="secondary"
                size="touch"
                isLoading={loadingMore}
                disabled={loadingMore}
                onClick={() => { void loadMore(); }}
              >
                {t('editor:fileTree.loadMoreTrash')}
              </Button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
};
