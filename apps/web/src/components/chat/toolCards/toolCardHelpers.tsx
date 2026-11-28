/* eslint-disable react-refresh/only-export-components */
import React, { useId } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../../lib/i18n';
import {
  RotateCcw,
  FileText,
  Edit3,
  FilePlus,
  FileEdit,
  Trash2,
  Search,
  User,
  BookOpen,
  ScrollText,
  Settings,
  Layers,
  ArrowRight,
  Plus,
  Minus,
} from 'lucide-react';
import type { FileEditUndoTarget } from '../../../types';
import { undoneEditKey } from '../../../lib/undoneEdits';
import type { WriteChapterOutcome } from '../../../lib/aiWriteCards';
import { CHAT_CARD_INLINE_ACTION } from '../../chatCardStyles';

export type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

export interface ToolResultCardProps {
  /**
   * The type of content to render.
   * - 'tool_call': Tool execution in progress (loading state)
   * - 'tool_result': Completed tool execution result
   */
  type: 'tool_call' | 'tool_result';
  /** Name of the tool being executed or completed. */
  toolName?: string;
  /** Tool execution result or arguments. */
  result?: Record<string, unknown>;
  /** Error message if tool execution failed. */
  error?: string;
  /** Whether the tool is currently executing. */
  isPending?: boolean;
  /** The round ended before this tool call returned a result. */
  interrupted?: boolean;
  /** Callback to undo a file edit operation. */
  onUndo?: (target: FileEditUndoTarget) => void;
  /** Restore a soft-deleted file from its chat card. */
  onRestoreDeletion?: (
    fileId: string,
    card?: { chatMessageId?: string; toolUseId?: string },
  ) => void | Promise<void>;
  /** The tool call of this card and its saved assistant message. */
  toolUseId?: string;
  chatMessageId?: string;
  /** Open the exact file from a create/update/edit result in the current project. */
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  /** Open the current project's AI Memory editor at Writing Conventions. */
  onOpenAIMemory?: () => void;
}

export const quote = (text: string) => {
  const isZh = i18n.language === 'zh';
  return `${isZh ? '「' : '"'}${text}${isZh ? '」' : '"'}`;
};

const PARALLEL_TASK_TYPES = new Set([
  'write_chapter',
  'edit_file',
  'batch_replace_files',
  'delete_file',
  'query_files',
  'read_files',
  'search_files',
  'hybrid_search',
]);

/** Localized name for a parallel sub-task type, or null for an unknown internal type. */
export const getParallelTaskLabel = (taskType: unknown, t: (key: string) => string): string | null => {
  if (typeof taskType !== 'string' || !PARALLEL_TASK_TYPES.has(taskType)) return null;
  return t(`chat:tool.${taskType}`);
};

export const getToolDisplayInfo = (toolName: string, t: (key: string) => string): { icon: React.ReactNode; label: string } => {
  switch (toolName) {
    case 'create_file':
      return {
        icon: <FilePlus className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.create_file'),
      };
    case 'update_file':
      return {
        icon: <FileEdit className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.update_file'),
      };
    case 'edit_file':
      return {
        icon: <Edit3 className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.edit_file'),
      };
    case 'batch_replace_files':
      return {
        icon: <Edit3 className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.batch_replace_files'),
      };
    case 'write_chapter':
      return {
        icon: <FileEdit className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.write_chapter'),
      };
    case 'delete_file':
      return {
        icon: <Trash2 className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.delete_file'),
      };
    case 'rename_file':
      return {
        icon: <FileEdit className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.rename_file'),
      };
    case 'query_files':
      return {
        icon: <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.query_files'),
      };
    case 'read_files':
      return {
        icon: <BookOpen className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.read_files'),
      };
    case 'search_files':
      return {
        icon: <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.search_files'),
      };
    case 'hybrid_search':
      return {
        icon: <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.hybrid_search'),
      };
    case 'update_project':
      return {
        icon: <Settings className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.update_project'),
      };
    case 'parallel_execute':
      return {
        icon: <Layers className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.parallel_execute'),
      };
    case 'load_skill':
      return {
        icon: <BookOpen className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.load_skill'),
      };
    case 'read_skill_resource':
      return {
        icon: <FileText className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.read_skill_resource'),
      };
    default:
      // 不认识的工具不把内部名字露给作者。
      return {
        icon: <FileText className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.generic'),
      };
  }
};

const authorEditsTouchedItems = (result: Record<string, unknown> | undefined): unknown[] => {
  const data = result?.data && typeof result.data === 'object' ? (result.data as Record<string, unknown>) : result;
  return Array.isArray(data?.author_edits_touched) ? data.author_edits_touched : [];
};

const isRestoredDeletion = (item: unknown): boolean =>
  Boolean(item && typeof item === 'object' && (item as { restored_deleted?: unknown }).restored_deleted === true);

/** An edit_file result rewrote paragraphs the author wrote after the AI (server-side, by position). */
export const touchedAuthorEdits = (result: Record<string, unknown> | undefined): boolean =>
  authorEditsTouchedItems(result).some((item) => !isRestoredDeletion(item));

/** An edit_file result put back, word for word, a paragraph the author had deleted (#268). */
export const restoredAuthorDeletion = (result: Record<string, unknown> | undefined): boolean =>
  authorEditsTouchedItems(result).some(isRestoredDeletion);

export const readUserFacingText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;

export type ToolFailureKind =
  | 'notFound'
  | 'ambiguous'
  | 'fileMissing'
  | 'repeatRead'
  | 'authorEdited'
  | 'protectionUnavailable'
  | 'authorChanged'
  | 'clarifyFirst'
  | 'noChange'
  | 'fileBusy'
  | 'historyUnavailable'
  | 'wholeBodyReplace'
  | 'duplicatedParagraph'
  | 'chapterReplacePending'
  | 'chapterTargetAmbiguous'
  | 'invalidEdit'
  | 'invalidParentId'
  | 'invalidReadRange'
  | 'skillNotFound'
  | 'skillResourceNotFound'
  | 'skillLimitExceeded'
  | 'permissionDenied'
  | 'generic';

/**
 * AI 有意没动文件（不是没做成）：作者手改过的文件没点名就不删、不整份重写（author_edit_protected），
 * 要求太笼统先问清楚（clarify_first）。卡片标题按这一类说，不写「失败」。
 */
export const INTENTIONAL_SKIP_KINDS: ReadonlySet<ToolFailureKind> = new Set([
  'authorEdited',
  'protectionUnavailable',
  'authorChanged',
  'clarifyFirst',
]);

/**
 * 按 error_type 归到作者能看懂的几类。error_type 由各工具自己定义，这里只认
 * 语义明确的形态；认不出来的一律归为 generic，绝不回退到原始错误文本。
 */
export const classifyToolFailure = (errorType: string | undefined): ToolFailureKind => {
  if (!errorType) return 'generic';
  const normalized = errorType.toLowerCase();
  if (normalized === 'repeated_read') return 'repeatRead';
  if (normalized === 'author_edit_protected') return 'authorEdited';
  if (normalized === 'author_edit_protection_unavailable') return 'protectionUnavailable';
  // The file changed after the AI read it (usually the author's own edit): not edited on the old text.
  if (normalized === 'stale_content') return 'authorChanged';
  if (normalized === 'clarify_first') return 'clarifyFirst';
  if (normalized === 'no_change') return 'noChange';
  if (normalized === 'file_busy') return 'fileBusy';
  if (normalized === 'pre_write_history_unavailable') return 'historyUnavailable';
  if (normalized === 'whole_body_replace') return 'wholeBodyReplace';
  if (normalized === 'duplicated_paragraph') return 'duplicatedParagraph';
  if (normalized === 'chapter_replace_pending') return 'chapterReplacePending';
  if (normalized === 'chapter_target_ambiguous') return 'chapterTargetAmbiguous';
  if (normalized === 'invalid_edit') return 'invalidEdit';
  if (normalized === 'invalid_parent_id') return 'invalidParentId';
  if (normalized === 'invalid_read_range') return 'invalidReadRange';
  if (normalized === 'skill_not_found') return 'skillNotFound';
  if (normalized === 'skill_resource_not_found') return 'skillResourceNotFound';
  if (normalized === 'skill_limit_exceeded') return 'skillLimitExceeded';
  if (normalized === 'permission_denied') return 'permissionDenied';
  if (/(^|_)files?_(not_found|missing|deleted)$/.test(normalized)) return 'fileMissing';
  if (/(ambiguous|multiple_match)/.test(normalized)) return 'ambiguous';
  if (/(anchor|old_text|text|pattern|match|passage)_not_found|no_match/.test(normalized)) return 'notFound';
  return 'generic';
};

export const isChineseUi = (): boolean => (i18n.language ?? '').startsWith('zh');

/**
 * Notes entries an update_project call appended because the author asked the AI to remember them
 * (`notes_added`, exact stored text). The server sets it only when the notes were written,
 * including on a partial (task board rejected) result.
 */
export const readNotesAdded = (
  data: Record<string, unknown> | undefined,
): string[] | null => {
  const entries = Array.isArray(data?.notes_added)
    ? [...new Set(data.notes_added
      .filter((entry): entry is string => typeof entry === 'string')
      .map((entry) => entry.trim())
      .filter(Boolean))]
    : [];
  return entries.length > 0 ? entries : null;
};

/**
 * 工具失败卡片给作者看的一句话：优先用工具给的 user_message，其次按 error_type
 * 给出通用说法。原始 error（给模型的指令、内部 id、英文异常）永远不显示。
 */
export const resolveToolFailure = (
  result: Record<string, unknown> | undefined,
  t: TranslateFn,
): { kind: ToolFailureKind; message: string } => {
  const nested = result?.data && typeof result.data === 'object'
    ? (result.data as Record<string, unknown>)
    : undefined;
  const errorType = readUserFacingText(result?.error_type) ?? readUserFacingText(nested?.error_type);
  const kind = classifyToolFailure(errorType);
  // user_message is written in Chinese by the backend; other UI languages use the error_type mapping.
  const userMessage = isChineseUi()
    ? readUserFacingText(result?.user_message) ?? readUserFacingText(nested?.user_message)
    : null;
  if (userMessage) return { kind, message: userMessage };

  const title = readUserFacingText(result?.title) ?? readUserFacingText(nested?.title);
  if (kind === 'repeatRead' || kind === 'authorEdited') {
    return {
      kind,
      message: title
        ? t(`chat:tool.failureHint.${kind}`, { title })
        : t(`chat:tool.failureHint.${kind}Untitled`),
    };
  }
  return { kind, message: t(`chat:tool.failureHint.${kind}`) };
};

const PARTIAL_FAILURE_HINT_KINDS: ReadonlySet<ToolFailureKind> = new Set([
  'notFound',
  'ambiguous',
  'noChange',
  'invalidEdit',
  'generic',
]);

/**
 * 部分成功编辑卡的失败原因：只说明未生效的剩余修改，绝不声称「文件未改动 / the file was not changed」。
 */
export const resolvePartialEditFailure = (
  failedEdit: Record<string, unknown> | undefined,
  t: TranslateFn,
): string => {
  const errorType = readUserFacingText(failedEdit?.error_type);
  const kind = classifyToolFailure(errorType);
  const hintKey = PARTIAL_FAILURE_HINT_KINDS.has(kind) ? kind : 'generic';
  return t(`chat:tool.partialFailureHint.${hintKey}`);
};

export const getEditOpIcon = (op: string): React.ReactNode => {
  switch (op) {
    case 'replace':
      return <ArrowRight className="w-3 h-3 shrink-0 text-[hsl(var(--text-secondary))]" />;
    case 'append':
    case 'prepend':
    case 'insert_after':
    case 'insert_before':
      return <Plus className="w-3 h-3 shrink-0 text-[hsl(var(--success-text))]" />;
    case 'delete':
      return <Minus className="w-3 h-3 shrink-0 text-[hsl(var(--error-text))]" />;
    default:
      return <Edit3 className="w-3 h-3 shrink-0 text-[hsl(var(--text-secondary))]" />;
  }
};

export const getEditOpLabel = (op: string, t: TranslateFn): string => {
  switch (op) {
    case 'replace': return t('chat:edit.replace');
    case 'append': return t('chat:edit.append');
    case 'prepend': return t('chat:edit.prepend');
    case 'insert_after': return t('chat:edit.insert_after');
    case 'insert_before': return t('chat:edit.insert_before');
    case 'delete': return t('chat:edit.label_delete');
    default: return op || t('chat:tool.edit_file');
  }
};

export const getFileTypeInfo = (fileType: string, t: (key: string) => string): { icon: React.ReactNode; label: string } => {
  switch (fileType) {
    case 'outline':
      return {
        icon: <ScrollText className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:fileType.outline'),
      };
    case 'draft':
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.draft'),
      };
    case 'character':
      return {
        icon: <User className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.character'),
      };
    case 'lore':
      return {
        icon: <BookOpen className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:fileType.lore'),
      };
    case 'snippet':
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.snippet'),
      };
    case 'script':
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.script'),
      };
    case 'document':
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.document'),
      };
    default:
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.default'),
      };
  }
};

/** A chapter number the server can address (a positive integer), or null. */
export const readChapterNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null;

/** A screenplay's write_chapter units are episodes (the server's `file_type`, not the title text). */
export const isEpisodeFile = (fileType: unknown): boolean => fileType === 'script';

/**
 * 「第3章「雨夜」」 (「第3集…」 for a script) for a write_chapter call or result; the title alone
 * when the number is missing.
 */
export const describeChapter = (
  chapter: unknown,
  title: string | undefined,
  t: TranslateFn,
  fileType?: unknown,
): string => {
  const number = readChapterNumber(chapter);
  const parts = [
    number !== null
      ? t(isEpisodeFile(fileType) ? 'chat:tool.episodeNumber' : 'chat:tool.chapterNumber', { chapter: number })
      : '',
    title ? quote(title) : '',
  ].filter(Boolean);
  return parts.join(' ');
};

export const WRITE_CHAPTER_HEADLINES: Record<WriteChapterOutcome, { numbered: string; unnumbered: string }> = {
  created: { numbered: 'chat:tool.chapterCreated', unnumbered: 'chat:tool.chapterCreatedUnnumbered' },
  exists: { numbered: 'chat:tool.chapterExists', unnumbered: 'chat:tool.chapterExistsUnnumbered' },
  replacing: { numbered: 'chat:tool.chapterReplacing', unnumbered: 'chat:tool.chapterReplacingUnnumbered' },
  replaced: { numbered: 'chat:tool.chapterReplaced', unnumbered: 'chat:tool.chapterReplacedUnnumbered' },
  unchanged: { numbered: 'chat:tool.chapterReplaceUnchanged', unnumbered: 'chat:tool.chapterReplaceUnchangedUnnumbered' },
  not_saved: { numbered: 'chat:tool.chapterReplaceNotSaved', unnumbered: 'chat:tool.chapterReplaceNotSavedUnnumbered' },
};

export const WRITE_EPISODE_HEADLINES: Record<WriteChapterOutcome, { numbered: string; unnumbered: string }> = {
  created: { numbered: 'chat:tool.episodeCreated', unnumbered: 'chat:tool.episodeCreatedUnnumbered' },
  exists: { numbered: 'chat:tool.episodeExists', unnumbered: 'chat:tool.episodeExistsUnnumbered' },
  replacing: { numbered: 'chat:tool.episodeReplacing', unnumbered: 'chat:tool.episodeReplacingUnnumbered' },
  replaced: { numbered: 'chat:tool.episodeReplaced', unnumbered: 'chat:tool.episodeReplacedUnnumbered' },
  unchanged: { numbered: 'chat:tool.episodeReplaceUnchanged', unnumbered: 'chat:tool.chapterReplaceUnchangedUnnumbered' },
  not_saved: { numbered: 'chat:tool.episodeReplaceNotSaved', unnumbered: 'chat:tool.episodeReplaceNotSavedUnnumbered' },
};

export const readWordCount = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

/** Undone here (live) or as the server recorded on the card (after a refresh). */
export const isFileEditUndone = (
  data: Record<string, unknown>,
  target: FileEditUndoTarget,
  undoneEdits: ReadonlySet<string>,
): boolean => {
  const undo = data.undo as Record<string, unknown> | undefined;
  return undo?.undone === true || undoneEdits.has(undoneEditKey(target));
};

export const formatWordCount = (words: number, t: TranslateFn): string => {
  if (words < 1000) return t('chat:size.characters', { length: words });
  return t('chat:size.kCharacters', { length: (words / 1000).toFixed(1) });
};

export const extractFileList = (data: unknown): Array<Record<string, unknown>> => {
  if (Array.isArray(data)) {
    return data.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'));
  }
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>;
    const nested = Array.isArray(record.files)
      ? record.files
      : Array.isArray(record.items)
        ? record.items
        : null;
    if (nested) {
      return nested.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'));
    }
  }
  return [];
};

export const formatMatchCountHeadline = (
  prefix: 'search_matches' | 'batch_replace_preview' | 'batch_replace_done',
  files: number,
  matches: number,
  t: TranslateFn,
): string => {
  if (files === 1 && matches === 1) {
    return t(`chat:tool.${prefix}_one_in_one`);
  }
  if (files === 1) {
    return t(`chat:tool.${prefix}_many_in_one`, { matches });
  }
  return t(`chat:tool.${prefix}_many_in_many`, { files, matches });
};

/**
 * Undo on an AI write card. While a later AI write to the same file is still in place (`blocked`),
 * undoing this one would be refused as stale: the button is disabled and says to undo that one first.
 */
export const CardUndoControl: React.FC<{
  target: FileEditUndoTarget;
  blocked: boolean;
  onUndo: (target: FileEditUndoTarget) => void;
  className?: string;
}> = ({ target, blocked, onUndo, className = '' }) => {
  const { t } = useTranslation(['chat', 'common']);
  const hintId = useId();
  if (blocked) {
    return (
      <>
        <button
          type="button"
          disabled
          aria-describedby={hintId}
          className={`inline-flex items-center gap-1 min-h-11 md:min-h-7 -my-2 md:my-0 shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-[hsl(var(--text-secondary))] cursor-not-allowed opacity-60 ${className}`}
        >
          <RotateCcw className="w-3 h-3" />
          {t('common:undo')}
        </button>
        <span id={hintId} className="basis-full min-w-0 pl-6 text-xs text-[hsl(var(--text-secondary))]">
          {t('chat:tool.undoLaterFirst')}
        </span>
      </>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onUndo(target)}
      className={`${CHAT_CARD_INLINE_ACTION} ${className}`}
      title={t('chat:tool.undo_edit')}
    >
      <RotateCcw className="w-3 h-3" />
      {t('common:undo')}
    </button>
  );
};

export const ToolCardFileRow: React.FC<{
  id?: unknown;
  title: string;
  fileType: string;
  label?: string;
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  isPending?: boolean;
  interrupted?: boolean;
}> = ({ id, title, fileType, label = title, onOpenFile, isPending, interrupted }) => {
  const { t } = useTranslation(['chat', 'common']);
  const typeInfo = getFileTypeInfo(fileType, t);
  const itemFileId = typeof id === 'string' ? id.trim() : '';
  const clickable = !!onOpenFile && !!itemFileId && !isPending && !interrupted;
  const content = (
    <>
      <span className="shrink-0">{typeInfo.icon}</span>
      <span className="min-w-0 break-words">{label}</span>
    </>
  );

  if (clickable) {
    return (
      <button
        type="button"
        onClick={() => onOpenFile(itemFileId, fileType, title)}
        className="inline-flex items-center gap-1.5 min-h-11 md:min-h-0 -my-2 md:my-0 py-1.5 min-w-0 max-w-full rounded-md text-left text-xs font-medium text-[hsl(var(--text-primary))] hover:text-[hsl(var(--accent-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
      >
        {content}
      </button>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 min-w-0 max-w-full text-xs font-medium text-[hsl(var(--text-primary))]">
      {content}
    </span>
  );
};
