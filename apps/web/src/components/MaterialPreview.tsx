import React from 'react';
import { useTranslation } from 'react-i18next';
import { MaterialReadingContent } from './materials/MaterialReadingContent';
import { BookOpen, Plus, MessageSquarePlus } from 'lucide-react';
import type { MaterialPreviewResponse } from '../lib/materialsApi';

interface MaterialPreviewProps {
  preview: MaterialPreviewResponse | null;
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

  if (isLoading || !preview) {
    return (
      <div className="flex items-center justify-center h-full text-[hsl(var(--text-secondary))]">
        <p className="text-sm">{t('common:loading')}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[hsl(var(--bg-canvas))]">
      {/* Top action bar */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-canvas))]">
        <BookOpen size={16} className="text-[hsl(var(--accent-primary))] shrink-0" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
          {preview.title}
        </span>
        <span className="text-xs text-[hsl(var(--text-secondary))] shrink-0">
          {t('editor:fileTree.previewReadonly')}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onAttachToChat}
          className="inline-flex items-center gap-1 px-2.5 py-1 max-md:min-h-11 text-xs rounded-lg border border-[hsl(var(--border-color))] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
        >
          <MessageSquarePlus size={14} />
          {t('editor:fileTree.attachToChat')}
        </button>
        <button
          type="button"
          onClick={onAddToProject}
          className="inline-flex items-center gap-1 px-2.5 py-1 max-md:min-h-11 text-xs font-medium rounded-lg bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] hover:bg-[hsl(var(--primary-hover))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-light))]"
        >
          <Plus size={14} />
          {t('editor:fileTree.addToProject')}
        </button>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center px-2 py-1 max-md:min-h-11 text-xs text-[hsl(var(--accent-primary))] hover:underline cursor-pointer rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
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
