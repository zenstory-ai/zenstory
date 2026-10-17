import React from 'react';
import { useTranslation } from 'react-i18next';
import { MaterialReadingContent } from './materials/MaterialReadingContent';
import { BookOpen, Plus, MessageSquarePlus } from 'lucide-react';
import type { MaterialPreviewResponse } from '../lib/materialsApi';

interface MaterialPreviewProps {
  preview: MaterialPreviewResponse;
  isLoading: boolean;
  onAddToProject: () => void;
  onAttachToChat: () => void;
  onBack: () => void;
}

export const MaterialPreview: React.FC<MaterialPreviewProps> = ({
  preview,
  isLoading,
  onAddToProject,
  onAttachToChat,
  onBack,
}) => {
  const { t } = useTranslation(['editor', 'common']);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-[hsl(var(--text-secondary))]">
        <p className="text-sm">{t('common:loading')}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Top action bar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-[hsl(var(--border-primary))] bg-[hsl(var(--bg-secondary))]">
        <BookOpen size={16} className="text-[hsl(var(--accent-primary))] shrink-0" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
          {preview.title}
        </span>
        <span className="text-xs text-[hsl(var(--text-secondary))] shrink-0">
          {t('editor:fileTree.previewReadonly')}
        </span>
        <div className="flex-1" />
        <button
          onClick={onAttachToChat}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-[hsl(var(--border-primary))] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
        >
          <MessageSquarePlus size={14} />
          {t('editor:fileTree.attachToChat')}
        </button>
        <button
          onClick={onAddToProject}
          className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-[hsl(var(--accent-primary))] text-white hover:opacity-90 transition-opacity"
        >
          <Plus size={14} />
          {t('editor:fileTree.addToProject')}
        </button>
        <button
          onClick={onBack}
          className="text-xs text-[hsl(var(--accent-primary))] hover:underline cursor-pointer"
        >
          {t('editor:fileTree.backToEdit')}
        </button>
      </div>

      {/* Markdown content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="material-detail material-analysis mx-auto max-w-[44rem]">
          <MaterialReadingContent content={preview.markdown} />
        </div>
      </div>
    </div>
  );
};
