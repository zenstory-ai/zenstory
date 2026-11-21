import React, { useId, useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useProject } from '../contexts/ProjectContext';
import { fileApi } from '../lib/api';
import { materialsApi } from '../lib/materialsApi';
import Modal from './ui/Modal';
import { Button } from './ui/Button';
import type { MaterialPreviewResponse, MaterialEntityType } from '../lib/materialsApi';
import type { FileTreeNode } from '../types';
import { logger } from "../lib/logger";

interface ImportMaterialDialogProps {
  isOpen: boolean;
  onClose: () => void;
  preview: MaterialPreviewResponse;
  novelId: number;
  entityType: MaterialEntityType;
  entityId: number;
  onSuccess: (fileId: string, folderName: string) => void;
}

export const ImportMaterialDialog: React.FC<ImportMaterialDialogProps> = ({
  isOpen,
  onClose,
  preview,
  novelId,
  entityType,
  entityId,
  onSuccess,
}) => {
  const { t } = useTranslation(['editor', 'common']);
  const fileNameId = useId();
  const targetFolderIdInput = useId();
  const { currentProjectId } = useProject();
  const [fileName, setFileName] = useState(preview.suggested_file_name);
  const [targetFolderId, setTargetFolderId] = useState<string | null>(null);
  const [folders, setFolders] = useState<{ id: string; title: string }[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [folderError, setFolderError] = useState(false);
  const folderRequestRef = useRef(0);
  const importRequestRef = useRef(0);
  const mountedRef = useRef(true);
  const currentProjectIdRef = useRef(currentProjectId);
  const isOpenRef = useRef(isOpen);
  currentProjectIdRef.current = currentProjectId;
  isOpenRef.current = isOpen;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      folderRequestRef.current += 1;
      importRequestRef.current += 1;
    };
  }, []);

  // Load project folders
  const loadFolders = useCallback(async () => {
      if (!isOpen || !currentProjectId) return;
      const request = ++folderRequestRef.current;
      const requestProjectId = currentProjectId;
      setFolderError(false);
      try {
        const response = await fileApi.getTree(requestProjectId);
        if (!mountedRef.current || folderRequestRef.current !== request || currentProjectId !== requestProjectId) return;
        const folderList = response.tree
          .filter((n: FileTreeNode) => n.file_type === 'folder')
          .map((n: FileTreeNode) => ({ id: n.id, title: n.title }));
        setFolders(folderList);
        setTargetFolderId((prev) =>
          prev && folderList.some((f) => f.id === prev && f.title !== preview.suggested_folder_name)
            ? prev
            : null
        );
      } catch (err) {
        if (!mountedRef.current || folderRequestRef.current !== request) return;
        logger.error('Failed to load folders:', err);
        setFolders([]);
        setTargetFolderId(null);
        setFolderError(true);
      }
  }, [currentProjectId, isOpen, preview.suggested_folder_name]);

  useEffect(() => {
    void loadFolders();
    return () => { folderRequestRef.current += 1; };
  }, [loadFolders]);

  // Reset state when dialog opens
  useEffect(() => {
    if (isOpen) {
      setFileName(preview.suggested_file_name);
      setTargetFolderId(null);
      setError(null);
      setFolderError(false);
    }
  }, [isOpen, preview.suggested_file_name]);

  const handleSubmit = async () => {
    if (!currentProjectId || isSubmitting) return;

    setIsSubmitting(true);
    setError(null);
    const request = ++importRequestRef.current;
    const requestProjectId = currentProjectId;
    try {
      const result = await materialsApi.importToProject({
        project_id: requestProjectId,
        novel_id: novelId,
        entity_type: entityType,
        entity_id: entityId,
        file_name: fileName || undefined,
        target_folder_id: targetFolderId || undefined,
      });

      if (
        mountedRef.current
        && importRequestRef.current === request
        && currentProjectIdRef.current === requestProjectId
        && isOpenRef.current
      ) {
        onSuccess(result.file_id, result.folder_name);
        onClose();
      }
    } catch (err) {
      if (!mountedRef.current || importRequestRef.current !== request) return;
      logger.error('Failed to import material:', err);
      setError(t('editor:fileTree.importDialog.importFailed'));
    } finally {
      if (mountedRef.current && importRequestRef.current === request) setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open={isOpen}
      onClose={() => { if (!isSubmitting) onClose(); }}
      title={t('editor:fileTree.importDialog.title')}
      size="sm"
      showCloseButton={!isSubmitting}
      closeOnBackdropClick={!isSubmitting}
      closeOnEscape={!isSubmitting}
      footer={
        <>
          <Button
            type="button"
            variant="secondary"
            size="touch"
            onClick={() => { if (!isSubmitting) onClose(); }}
            disabled={isSubmitting}
          >
            {t('editor:fileTree.importDialog.cancel')}
          </Button>
          <Button
            type="button"
            size="touch"
            onClick={handleSubmit}
            disabled={isSubmitting || !fileName.trim()}
            isLoading={isSubmitting}
            loadingText={t('common:loading')}
          >
            {t('editor:fileTree.importDialog.confirm')}
          </Button>
        </>
      }
    >
      <Modal.Body className="space-y-4">
        {/* Error message */}
        {error && (
          <div role="alert" className="p-3 text-sm text-[hsl(var(--error-text))] bg-[hsl(var(--error)/0.1)] rounded-xl border border-[hsl(var(--error)/0.3)]">
            {error}
          </div>
        )}

        {/* File name input */}
        <div>
          <label htmlFor={fileNameId} className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
            {t('editor:fileTree.importDialog.fileName')}
          </label>
          <input
            id={fileNameId}
            type="text"
            value={fileName}
            onChange={(e) => setFileName(e.target.value)}
            className="input w-full"
          />
        </div>

        {/* Target folder select */}
        <div>
          <label htmlFor={targetFolderIdInput} className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
            {t('editor:fileTree.importDialog.targetFolder')}
          </label>
          <select
            id={targetFolderIdInput}
            value={targetFolderId || ''}
            onChange={(e) => setTargetFolderId(e.target.value || null)}
            className="input w-full"
          >
            <option value="">
              {preview.suggested_folder_name} ({t('editor:fileTree.importDialog.recommended')})
            </option>
            {folders
              .filter((f) => f.title !== preview.suggested_folder_name)
              .map((f) => (
                <option key={f.id} value={f.id}>
                  {f.title}
                </option>
              ))}
          </select>
          {folderError && (
            <div role="alert" className="mt-2 text-xs text-[hsl(var(--error-text))]">
              <span>{t('editor:fileTree.importDialog.folderLoadFailed', { defaultValue: '文件夹加载失败。' })}</span>{' '}
              <button type="button" className="underline" onClick={() => void loadFolders()}>
                {t('common:retry', { defaultValue: '重试' })}
              </button>
            </div>
          )}
        </div>
      </Modal.Body>
    </Modal>
  );
};
