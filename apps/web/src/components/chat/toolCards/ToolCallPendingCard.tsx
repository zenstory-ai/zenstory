import React from 'react';
import { Loader2 } from 'lucide-react';
import { CHAT_CARD_SURFACE } from '../../chatCardStyles';
import {
  describeChapter,
  getFileTypeInfo,
  getToolDisplayInfo,
  isEpisodeFile,
  quote,
  type TranslateFn,
} from './toolCardHelpers';

export const ToolCallPendingCard: React.FC<{
  toolName: string;
  args: Record<string, unknown>;
  isPending?: boolean;
  interrupted?: boolean;
  t: TranslateFn;
}> = ({ toolName, args, isPending, interrupted, t }) => {
  const title = args.title as string | undefined;
  const fileType = args.file_type as string | undefined;
  const { icon, label: toolLabel } = getToolDisplayInfo(toolName, t);
  const label = toolName === 'write_chapter' && isEpisodeFile(fileType) ? t('chat:tool.write_episode') : toolLabel;

  let description = '';
  if (toolName === 'create_file' && title) {
    const typeInfo = fileType ? getFileTypeInfo(fileType, t) : null;
    description = typeInfo ? `${typeInfo.label}${quote(title)}` : quote(title);
  } else if (toolName === 'write_chapter') {
    description = describeChapter(args.chapter, title, t, fileType);
  } else if (toolName === 'query_files') {
    const queryFileType = args.file_type as string | undefined;
    if (queryFileType) {
      const typeInfo = getFileTypeInfo(queryFileType, t);
      description = t('chat:tool.query_description', { label: typeInfo.label });
    } else {
      description = t('chat:tool.query_files_default');
    }
  } else if (toolName === 'read_files') {
    const ids = Array.isArray(args.ids)
      ? args.ids.filter((id): id is string => typeof id === 'string' && id.trim().length > 0)
      : [];
    const section = typeof args.section === 'string' && args.section.trim() ? args.section.trim() : '';
    const parts = [
      ids.length > 0 ? t('chat:tool.read_files_count', { count: ids.length }) : '',
      section ? quote(section) : '',
    ].filter(Boolean);
    description = parts.join(' · ');
  } else if (toolName === 'search_files') {
    const query = typeof args.query === 'string' && args.query.trim() ? args.query.trim() : '';
    const rawTypes = Array.isArray(args.file_types)
      ? args.file_types.filter((ft): ft is string => typeof ft === 'string' && ft.trim().length > 0)
      : typeof args.file_types === 'string' && args.file_types.trim()
        ? [args.file_types.trim()]
        : typeof args.file_type === 'string' && args.file_type.trim()
          ? [args.file_type.trim()]
          : [];
    const typeLabels = rawTypes.map((ft) => getFileTypeInfo(ft, t).label).join(' / ');
    if (query && typeLabels) {
      description = `${typeLabels} · ${quote(query)}`;
    } else if (query) {
      description = quote(query);
    } else if (typeLabels) {
      description = t('chat:tool.query_description', { label: typeLabels });
    }
  } else if (toolName === 'batch_replace_files') {
    const fileIds = Array.isArray(args.file_ids)
      ? args.file_ids.filter((id): id is string => typeof id === 'string' && id.trim().length > 0)
      : [];
    const rawTypes = Array.isArray(args.file_types)
      ? args.file_types.filter((ft): ft is string => typeof ft === 'string' && ft.trim().length > 0)
      : typeof args.file_types === 'string' && args.file_types.trim()
        ? [args.file_types.trim()]
        : typeof args.file_type === 'string' && args.file_type.trim()
          ? [args.file_type.trim()]
          : [];
    const typeLabels = rawTypes.map((ft) => getFileTypeInfo(ft, t).label).join(' / ');
    const parts = [
      args.dry_run === true ? t('chat:tool.batch_replace_preview_pending') : '',
      fileIds.length > 0
        ? t('chat:tool.batch_replace_scope_files', { count: fileIds.length })
        : typeLabels,
    ].filter(Boolean);
    description = parts.join(' · ');
  } else if (toolName === 'update_project') {
    description = t('chat:tool.update_status_description');
  } else if (toolName === 'load_skill' && typeof args.name === 'string' && args.name) {
    description = quote(args.name);
  } else if (toolName === 'read_skill_resource' && typeof args.path === 'string' && args.path) {
    description = typeof args.name === 'string' && args.name
      ? `${quote(args.name)} · ${args.path}`
      : args.path;
  } else if (title) {
    description = quote(title);
  }

  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex items-center gap-2">
        {isPending ? (
          <Loader2 className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))] animate-spin" />
        ) : (
          icon
        )}
        <span className="min-w-0 text-sm text-[hsl(var(--text-primary))] break-words">
          {label}
          {description && <span className="text-[hsl(var(--text-secondary))]"> {description}</span>}
        </span>
        {isPending && (
          <span className="shrink-0 text-xs text-[hsl(var(--text-secondary))] animate-pulse ml-auto">
            {t('chat:tool.processing_ellipsis')}
          </span>
        )}
        {!isPending && interrupted && (
          <span className="shrink-0 text-xs text-[hsl(var(--text-secondary))] ml-auto">
            {t('chat:tool.interrupted')}
          </span>
        )}
      </div>
    </div>
  );
};
