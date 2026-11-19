/**
 * @fileoverview Tool Result Card Component
 * @module components/ToolResultCard
 *
 * A versatile component for displaying AI agent tool execution results.
 * Renders different UI based on the tool type (create_file, update_file,
 * write_chapter, edit_file, delete_file, query_files, update_project) and result status
 * (pending, success, error).
 *
 * Features:
 * - Tool call loading state with animated spinner
 * - Success/error states with appropriate icons
 * - Diff-style display for file edits (replace, insert, delete)
 * - Response card with action buttons (apply, retry, copy)
 * - Used snippets display with view links
 * - Internationalization support via react-i18next
 */

import React, { useCallback, useContext, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../lib/i18n';
import {
  CheckCircle2,
  AlertCircle,
  XCircle,
  RotateCcw,
  FileText,
  Info,
  Edit3,
  ArrowRight,
  Plus,
  Minus,
  Loader2,
  FilePlus,
  FileEdit,
  Trash2,
  Search,
  User,
  BookOpen,
  ScrollText,
  Settings,
  Layers,
} from 'lucide-react';
import type { FileDeletionPreview, FileDeletionResult, FileEditUndoTarget } from '../types';
import { DeletionTargetList } from './DeletionTargetList';
import { UndoBlockedContext, UndoneEditsContext, undoneEditKey } from '../lib/undoneEdits';
import { isPersistedUndoTarget, readFileEditUndoTarget, readWriteChapterOutcome, type WriteChapterOutcome } from '../lib/aiWriteCards';
import { countWords } from '../lib/documentChunker';
import { sanitizeAgentText } from '../lib/agentDisplayName';
import { RememberedNotes } from './RememberedNotes';
import { CHAT_CARD_INLINE_ACTION, CHAT_CARD_SURFACE } from './chatCardStyles';

const quote = (text: string) => {
  const isZh = i18n.language === 'zh';
  return `${isZh ? '「' : '"'}${text}${isZh ? '」' : '"'}`;
};

/**
 * Props for the ToolResultCard component.
 *
 * @interface ToolResultCardProps
 */
interface ToolResultCardProps {
  /**
   * The type of content to render.
   * - 'tool_call': Tool execution in progress (loading state)
   * - 'tool_result': Completed tool execution result
   */
  type: 'tool_call' | 'tool_result';

  /**
   * Name of the tool being executed or completed.
   * Used to determine icon and display label.
   * @example 'create_file', 'update_file', 'query_files'
   */
  toolName?: string;

  /**
   * Tool execution result or arguments.
   * For tool_call: contains arguments (title, file_type, etc.)
   * For tool_result: contains result data (id, content, details, etc.)
   */
  result?: Record<string, unknown>;

  /**
   * Error message if tool execution failed.
   * Displayed with error styling when present.
   */
  error?: string;

  /**
   * Whether the tool is currently executing.
   * Shows loading spinner and "Processing..." text when true.
   * @default false
   */
  isPending?: boolean;

  /**
   * The round ended (stopped / dropped) before this tool call returned a result.
   * Shown as a neutral 「未完成」 instead of a spinner (the round may have ended by stop, error or disconnect).
   * @default false
   */
  interrupted?: boolean;

  /**
   * Callback to undo a file edit operation.
   * Called with immutable edit provenance when undo button is clicked.
   * Only shown for successful edit_file results.
   */
  onUndo?: (target: FileEditUndoTarget) => void;
  /** Restore one recoverable deletion batch. */
  onRestoreDeletion?: (batchId: string) => void | Promise<void>;
  /** Confirm the exact server-previewed deletion target set. */
  onConfirmDeletion?: (
    preview: FileDeletionPreview,
    card?: { chatMessageId?: string; toolUseId?: string },
  ) => Promise<FileDeletionResult>;

  /** The tool call of this card and its saved assistant message: an undo names them so the server marks this card undone. */
  toolUseId?: string;
  chatMessageId?: string;

  /** Open the exact file from a successful create/update result in the current project. */
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;

  /** Open the current project's AI Memory editor at Writing Conventions. */
  onOpenAIMemory?: () => void;
}

/**
 * Returns display icon and localized label for a tool name.
 *
 * @param toolName - Internal tool name (e.g., 'create_file', 'update_file')
 * @param t - Translation function from react-i18next
 * @returns Object with icon React node and localized label string
 *
 * @example
 * const { icon, label } = getToolDisplayInfo('create_file', t);
 * // icon: <FilePlus /> with success color
 * // label: t('chat:tool.create_file')
 */
const PARALLEL_TASK_TYPES = new Set([
  'write_chapter',
  'edit_file',
  'delete_file',
  'query_files',
  'hybrid_search',
]);

/** Localized name for a parallel sub-task type, or null for an unknown internal type. */
const getParallelTaskLabel = (taskType: unknown, t: (key: string) => string): string | null => {
  if (typeof taskType !== 'string' || !PARALLEL_TASK_TYPES.has(taskType)) return null;
  return t(`chat:tool.${taskType}`);
};

const getToolDisplayInfo = (toolName: string, t: (key: string) => string): { icon: React.ReactNode; label: string } => {
  switch (toolName) {
    case 'create_file':
      return { 
        icon: <FilePlus className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.create_file')
      };
    case 'update_file':
      return { 
        icon: <FileEdit className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />, 
        label: t('chat:tool.update_file')
      };
    case 'edit_file':
      return { 
        icon: <Edit3 className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.edit_file')
      };
    case 'write_chapter':
      return {
        icon: <FileEdit className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.write_chapter')
      };
    case 'delete_file':
      return { 
        icon: <Trash2 className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.delete_file')
      };
    case 'rename_file':
      return {
        icon: <FileEdit className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.rename_file')
      };
    case 'query_files':
      return { 
        icon: <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />, 
        label: t('chat:tool.query_files')
      };
    case 'hybrid_search':
      return {
        icon: <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.hybrid_search')
      };
    case 'update_project':
      return {
        icon: <Settings className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.update_project')
      };
    case 'parallel_execute':
      return {
        icon: <Layers className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.parallel_execute')
      };
    case 'load_skill':
      return {
        icon: <BookOpen className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.load_skill')
      };
    case 'read_skill_resource':
      return {
        icon: <FileText className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.read_skill_resource')
      };
    default:
      // 不认识的工具不把内部名字露给作者。
      return {
        icon: <FileText className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:tool.generic')
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
const touchedAuthorEdits = (result: Record<string, unknown> | undefined): boolean =>
  authorEditsTouchedItems(result).some((item) => !isRestoredDeletion(item));

/** An edit_file result put back, word for word, a paragraph the author had deleted (#268). */
const restoredAuthorDeletion = (result: Record<string, unknown> | undefined): boolean =>
  authorEditsTouchedItems(result).some(isRestoredDeletion);

const readUserFacingText = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value.trim() : undefined;

type ToolFailureKind =
  | 'notFound'
  | 'ambiguous'
  | 'fileMissing'
  | 'repeatRead'
  | 'authorEdited'
  | 'protectionUnavailable'
  | 'authorChanged'
  | 'clarifyFirst'
  | 'generic';

/**
 * AI 有意没动文件（不是没做成）：作者手改过的文件没点名就不删、不整份重写（author_edit_protected），
 * 要求太笼统先问清楚（clarify_first）。卡片标题按这一类说，不写「失败」。
 */
const INTENTIONAL_SKIP_KINDS: ReadonlySet<ToolFailureKind> = new Set(['authorEdited', 'protectionUnavailable', 'authorChanged', 'clarifyFirst']);

/**
 * 按 error_type 归到作者能看懂的几类。error_type 由各工具自己定义，这里只认
 * 语义明确的形态；认不出来的一律归为 generic，绝不回退到原始错误文本。
 */
const classifyToolFailure = (errorType: string | undefined): ToolFailureKind => {
  if (!errorType) return 'generic';
  const normalized = errorType.toLowerCase();
  if (normalized === 'repeated_read') return 'repeatRead';
  if (normalized === 'author_edit_protected') return 'authorEdited';
  if (normalized === 'author_edit_protection_unavailable') return 'protectionUnavailable';
  // The file changed after the AI read it (usually the author's own edit): not edited on the old text.
  if (normalized === 'stale_content') return 'authorChanged';
  if (normalized === 'clarify_first') return 'clarifyFirst';
  if (/(^|_)file_(not_found|missing|deleted)$/.test(normalized)) return 'fileMissing';
  if (/(ambiguous|multiple_match)/.test(normalized)) return 'ambiguous';
  if (/(anchor|old_text|text|pattern|match|passage)_not_found|no_match/.test(normalized)) return 'notFound';
  return 'generic';
};

const isChineseUi = (): boolean => (i18n.language ?? '').startsWith('zh');

/**
 * Notes entries an update_project call appended because the author asked the AI to remember them
 * (`notes_added`, exact stored text). The server sets it only when the notes were written,
 * including on a partial (task board rejected) result. Older history need not carry project_id
 * because editing opens the current project's single AI Memory dialog.
 */
const readNotesAdded = (
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
const resolveToolFailure = (
  result: Record<string, unknown> | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
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

/**
 * Returns display icon and localized label for a file type.
 *
 * @param fileType - File type (outline, draft, character, lore, snippet)
 * @param t - Translation function from react-i18next
 * @returns Object with icon React node and localized label string
 *
 * @example
 * const { icon, label } = getFileTypeInfo('outline', t);
 * // icon: <ScrollText /> with accent color
 * // label: t('chat:fileType.outline')
 */
const getFileTypeInfo = (fileType: string, t: (key: string) => string): { icon: React.ReactNode; label: string } => {
  switch (fileType) {
    case 'outline':
      return { 
        icon: <ScrollText className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />, 
        label: t('chat:fileType.outline')
      };
    case 'draft':
      return { 
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.draft')
      };
    case 'character':
      return { 
        icon: <User className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.character')
      };
    case 'lore':
      return { 
        icon: <BookOpen className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />, 
        label: t('chat:fileType.lore')
      };
    case 'snippet':
      return { 
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />, 
        label: t('chat:fileType.snippet')
      };
    case 'script':
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.script')
      };
    case 'document':
      return {
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />,
        label: t('chat:fileType.document')
      };
    default:
      return { 
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />, 
        label: t('chat:fileType.default')
      };
  }
};

/**
 * Formats a word count (the editor's `countWords` definition: one per Chinese
 * character, one per English word) for display, switching to 千字 / k above 1000.
 *
 * @param words - Word count from `countWords` or the server's `count_words`
 * @param t - Translation function with interpolation support
 * @returns Formatted string (e.g., "150 字" or "2.5 千字")
 */
/** A chapter number the server can address (a positive integer), or null. */
const readChapterNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null;

/** A screenplay's write_chapter units are episodes (the server's `file_type`, not the title text). */
const isEpisodeFile = (fileType: unknown): boolean => fileType === 'script';

/**
 * 「第3章「雨夜」」 (「第3集…」 for a script) for a write_chapter call or result; the title alone
 * when the number is missing.
 */
const describeChapter = (
  chapter: unknown,
  title: string | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
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

const WRITE_CHAPTER_HEADLINES: Record<WriteChapterOutcome, { numbered: string; unnumbered: string }> = {
  created: { numbered: 'chat:tool.chapterCreated', unnumbered: 'chat:tool.chapterCreatedUnnumbered' },
  exists: { numbered: 'chat:tool.chapterExists', unnumbered: 'chat:tool.chapterExistsUnnumbered' },
  replacing: { numbered: 'chat:tool.chapterReplacing', unnumbered: 'chat:tool.chapterReplacingUnnumbered' },
  replaced: { numbered: 'chat:tool.chapterReplaced', unnumbered: 'chat:tool.chapterReplacedUnnumbered' },
  unchanged: { numbered: 'chat:tool.chapterReplaceUnchanged', unnumbered: 'chat:tool.chapterReplaceUnchangedUnnumbered' },
  not_saved: { numbered: 'chat:tool.chapterReplaceNotSaved', unnumbered: 'chat:tool.chapterReplaceNotSavedUnnumbered' },
};

const WRITE_EPISODE_HEADLINES: Record<WriteChapterOutcome, { numbered: string; unnumbered: string }> = {
  created: { numbered: 'chat:tool.episodeCreated', unnumbered: 'chat:tool.episodeCreatedUnnumbered' },
  exists: { numbered: 'chat:tool.episodeExists', unnumbered: 'chat:tool.episodeExistsUnnumbered' },
  replacing: { numbered: 'chat:tool.episodeReplacing', unnumbered: 'chat:tool.episodeReplacingUnnumbered' },
  replaced: { numbered: 'chat:tool.episodeReplaced', unnumbered: 'chat:tool.episodeReplacedUnnumbered' },
  unchanged: { numbered: 'chat:tool.episodeReplaceUnchanged', unnumbered: 'chat:tool.chapterReplaceUnchangedUnnumbered' },
  not_saved: { numbered: 'chat:tool.episodeReplaceNotSaved', unnumbered: 'chat:tool.episodeReplaceNotSavedUnnumbered' },
};

const readWordCount = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

/** Undone here (live) or as the server recorded on the card (after a refresh). */
const isFileEditUndone = (
  data: Record<string, unknown>,
  target: FileEditUndoTarget,
  undoneEdits: ReadonlySet<string>,
): boolean => {
  const undo = data.undo as Record<string, unknown> | undefined;
  return undo?.undone === true || undoneEdits.has(undoneEditKey(target));
};

const formatWordCount = (words: number, t: (key: string, options?: Record<string, unknown>) => string): string => {
  if (words < 1000) return t('chat:size.characters', { length: words });
  return t('chat:size.kCharacters', { length: (words / 1000).toFixed(1) });
};

/**
 * Undo on an AI write card. While a later AI write to the same file is still in place (`blocked`),
 * undoing this one would be refused as stale: the button is disabled and says to undo that one first.
 */
const CardUndoControl: React.FC<{
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
          className={`inline-flex items-center gap-1 min-h-11 md:min-h-7 shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-[hsl(var(--text-secondary))] cursor-not-allowed opacity-60 ${className}`}
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

const ToolResultCardComponent: React.FC<ToolResultCardProps> = ({
  type,
  toolName,
  result,
  error,
  isPending,
  interrupted,
  onUndo,
  onRestoreDeletion,
  onConfirmDeletion,
  toolUseId,
  chatMessageId,
  onOpenFile,
  onOpenAIMemory,
}) => {
  const { t } = useTranslation(['chat', 'common']);
  const undoneEdits = useContext(UndoneEditsContext);
  const undoBlocked = useContext(UndoBlockedContext);
  const [confirmedDeletion, setConfirmedDeletion] = useState<FileDeletionResult | null>(null);
  const [confirmingDeletion, setConfirmingDeletion] = useState(false);
  const [restoringDeletion, setRestoringDeletion] = useState(false);
  const [deletionRestored, setDeletionRestored] = useState(false);

  // Memoize event handlers to prevent unnecessary re-renders
  const handleUndo = useCallback((target: FileEditUndoTarget) => {
    if (onUndo) {
      onUndo(target);
    }
  }, [onUndo]);

  // Render Tool Call (loading state)
  if (type === 'tool_call' && toolName) {
    // Extract user-friendly info from arguments
    const args = result || {};
    const title = args.title as string | undefined;
    const fileType = args.file_type as string | undefined;
    const { icon, label: toolLabel } = getToolDisplayInfo(toolName, t);
    const label = toolName === 'write_chapter' && isEpisodeFile(fileType) ? t('chat:tool.write_episode') : toolLabel;
    
    // Build description based on tool type
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
      <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
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
  }

  // Render Tool Result
  if (type === 'tool_result' && toolName) {
    // Handle error case
    if (error) {
      // 中途某一步没做成是常态（AI 会换个方式继续），用中性样式，不用红色报错。
      const { label } = getToolDisplayInfo(toolName, t);
      const { kind, message: userMessage } = resolveToolFailure(result, t);
      const intentionalSkip = INTENTIONAL_SKIP_KINDS.has(kind);
      // update_project with a rejected task board still saved its notes (partial result).
      const partialNotes = toolName === 'update_project'
        ? readNotesAdded((result?.data || result) as Record<string, unknown> | undefined)
        : null;
      return (
        <div className="space-y-2">
          <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
            <div className="flex items-center gap-2">
              {intentionalSkip ? (
                <Info className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
              ) : (
                <XCircle className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
              )}
              <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
                {intentionalSkip ? t(`chat:tool.skippedTitle.${kind}`) : t('chat:tool.failed', { label })}
              </span>
            </div>
            <p className="text-xs leading-5 text-[hsl(var(--text-secondary))] mt-1 ml-6">
              {userMessage}
            </p>
          </div>
          {partialNotes && !isPending && !interrupted && (
            <RememberedNotes entries={partialNotes} onOpen={onOpenAIMemory} />
          )}
        </div>
      );
    }

    // Handle success cases by tool type
    const data = (result?.data || result) as Record<string, unknown>;
    const fileId = typeof data?.id === 'string' ? data.id.trim() : '';
    const canOpenFile = !!onOpenFile && !!fileId && !isPending && !interrupted;
    const fileTitle = (title: string, fileType: string, label = title) => canOpenFile ? (
      <button
        type="button"
        onClick={() => onOpenFile?.(fileId, fileType, title)}
        className="min-h-11 md:min-h-0 min-w-0 max-w-full break-words rounded text-left underline decoration-[hsl(var(--border-color))] underline-offset-2 hover:text-[hsl(var(--accent-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
      >
        {label}
      </button>
    ) : <span className="min-w-0 break-words">{label}</span>;
    
    // create_file success
    if (toolName === 'create_file' && data) {
      const title = data.title as string || t('common:untitled');
      const fileType = data.file_type as string || '';
      const wordCount = readWordCount(data.new_word_count)
        ?? (typeof data.content === 'string' ? countWords(data.content) : 0);
      const typeInfo = getFileTypeInfo(fileType, t);
      
      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="min-w-0 flex-1 text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('chat:tool.created', { type: typeInfo.label })}
            </span>
            {wordCount > 0 && (
              <span className="shrink-0 text-xs text-[hsl(var(--text-secondary))] ml-auto">
                {formatWordCount(wordCount, t)}
              </span>
            )}
            <span className="flex basis-full min-w-0 items-start gap-1 pl-6 text-sm text-[hsl(var(--text-primary))]">
              <span className="shrink-0 mt-0.5">{typeInfo.icon}</span>
              {fileTitle(title, fileType)}
            </span>
          </div>
        </div>
      );
    }
    
    // write_chapter: the server's `action` says what happened to exactly that chapter. A replacement
    // arrives twice under one tool_use_id: armed (awaiting its text), then saved or refused.
    if (toolName === 'write_chapter' && data) {
      const title = data.title as string || t('common:untitled');
      const fileType = data.file_type as string || '';
      const chapter = readChapterNumber(data.chapter);
      const outcome = readWriteChapterOutcome(data);
      const previousWords = readWordCount(data.previous_word_count);
      const newWords = readWordCount(data.new_word_count)
        ?? (typeof data.content === 'string' ? countWords(data.content) : null);
      const typeInfo = getFileTypeInfo(fileType, t);
      const headlines = isEpisodeFile(fileType) ? WRITE_EPISODE_HEADLINES : WRITE_CHAPTER_HEADLINES;
      const headline = outcome === null
        ? t(isEpisodeFile(fileType) ? 'chat:tool.write_episode' : 'chat:tool.write_chapter')
        : chapter === null
          ? t(headlines[outcome].unnumbered)
          : t(headlines[outcome].numbered, { chapter });
      const succeeded = outcome === 'created' || outcome === 'replaced';
      const undoTarget = outcome === 'replaced'
        ? readFileEditUndoTarget(data, chatMessageId, toolUseId)
        : null;
      const undone = undoTarget !== null && isFileEditUndone(data, undoTarget, undoneEdits);

      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {succeeded ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            ) : (
              <Info className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
            )}
            <span className={`min-w-0 flex-1 text-sm ${succeeded ? 'font-medium text-[hsl(var(--text-primary))]' : 'text-[hsl(var(--text-secondary))]'}`}>
              {headline}
            </span>
            {succeeded && newWords !== null && newWords > 0 && (
              <span className="shrink-0 text-xs text-[hsl(var(--text-secondary))] ml-auto">
                {formatWordCount(newWords, t)}
              </span>
            )}
            {undone ? (
              <span className="shrink-0 text-xs text-[hsl(var(--text-secondary))]">
                {t('chat:tool.edit_undone')}
              </span>
            ) : onUndo && isPersistedUndoTarget(undoTarget) && (
              <CardUndoControl
                target={undoTarget}
                blocked={undoBlocked.has(undoneEditKey(undoTarget))}
                onUndo={handleUndo}
              />
            )}
            <span className="flex basis-full min-w-0 items-start gap-1 pl-6 text-sm text-[hsl(var(--text-primary))]">
              <span className="shrink-0 mt-0.5">{typeInfo.icon}</span>
              {fileTitle(title, fileType)}
            </span>
            {(outcome === 'replaced' || outcome === 'replacing') && previousWords !== null && previousWords > 0 && (
              <span className="basis-full min-w-0 pl-6 text-xs text-[hsl(var(--text-secondary))]">
                {t(outcome === 'replaced' ? 'chat:tool.chapterReplacedPrevious' : 'chat:tool.chapterReplacingPrevious', {
                  size: formatWordCount(previousWords, t),
                })}
              </span>
            )}
          </div>
        </div>
      );
    }

    // update_file success
    if (toolName === 'update_file' && data) {
      const title = data.title as string || t('chat:fileDefault');
      const fileType = data.file_type as string || '';
      const wordCount = typeof data.content === 'string' ? countWords(data.content) : 0;
      
      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="min-w-0 flex-1 text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('chat:tool.updated')}
            </span>
            {wordCount > 0 && (
              <span className="shrink-0 text-xs text-[hsl(var(--text-secondary))] ml-auto">
                {formatWordCount(wordCount, t)}
              </span>
            )}
            <span className="basis-full min-w-0 pl-6 text-sm text-[hsl(var(--text-primary))]">
              {fileTitle(title, fileType, quote(title))}
            </span>
          </div>
        </div>
      );
    }
    
    // rename_file success: only the title changed (#273)
    if (toolName === 'rename_file' && data && typeof data === 'object' && !Array.isArray(data)) {
      const renamed = data as Record<string, unknown>;
      const from = typeof renamed.previous_title === 'string' ? renamed.previous_title : '';
      const to = typeof renamed.title === 'string' ? renamed.title : '';
      const undoTarget = readFileEditUndoTarget(renamed, chatMessageId, toolUseId);
      const undone = undoTarget !== null && isFileEditUndone(renamed, undoTarget, undoneEdits);
      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="text-sm font-medium text-[hsl(var(--text-primary))] break-words">
              {t('chat:tool.renamed', { from, to })}
            </span>
            {undone ? (
              <span className="ml-auto text-xs text-[hsl(var(--text-secondary))]">{t('chat:tool.edit_undone')}</span>
            ) : onUndo && isPersistedUndoTarget(undoTarget) ? (
              <CardUndoControl
                target={undoTarget}
                blocked={undoBlocked.has(undoneEditKey(undoTarget))}
                onUndo={handleUndo}
                className="ml-auto"
              />
            ) : null}
          </div>
        </div>
      );
    }

    // delete_file success
    if (toolName === 'delete_file') {
      const deleted = (confirmedDeletion ?? (data && typeof data === 'object' ? data : {})) as Record<string, unknown>;
      const batchId = typeof deleted.batch_id === 'string' ? deleted.batch_id : '';
      const restored = deleted.restored === true;
      const isRestored = restored || deletionRestored;
      const count = typeof deleted.count === 'number' ? deleted.count : null;
      const preview = deleted.preview && typeof deleted.preview === 'object'
        ? deleted.preview as Record<string, unknown>
        : null;
      if (deleted.confirmation_required === true && preview) {
        const deletionPreview = preview as unknown as FileDeletionPreview;
        return (
          <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
            <div className="flex flex-wrap items-center gap-2">
              <Info className="h-4 w-4 shrink-0 text-[hsl(var(--text-secondary))]" />
              <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
                {t('chat:tool.deleteNeedsConfirmation', {
                  count: typeof preview.count === 'number' ? preview.count : 0,
                })}
              </span>
              {onConfirmDeletion && (
                <button
                  type="button"
                  className={`${CHAT_CARD_INLINE_ACTION} ml-auto min-h-11`}
                  disabled={confirmingDeletion}
                  onClick={() => {
                    setConfirmingDeletion(true);
                    void onConfirmDeletion(deletionPreview, { chatMessageId, toolUseId })
                      .then(setConfirmedDeletion)
                      .catch(() => undefined)
                      .finally(() => setConfirmingDeletion(false));
                  }}
                >
                  {confirmingDeletion ? t('common:saving') : t('common:delete')}
                </button>
              )}
            </div>
            <div className="mt-2">
              <DeletionTargetList targets={deletionPreview.targets} />
            </div>
          </div>
        );
      }
      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {count !== null ? t('chat:tool.deletedCount', { count }) : t('chat:tool.deleted')}
            </span>
            {isRestored ? (
              <span className="ml-auto text-xs text-[hsl(var(--text-secondary))]">{t('chat:tool.deletionRestored')}</span>
            ) : batchId && onRestoreDeletion ? (
              <button
                type="button"
                className={`${CHAT_CARD_INLINE_ACTION} ml-auto`}
                disabled={restoringDeletion}
                onClick={() => {
                  setRestoringDeletion(true);
                  void Promise.resolve(onRestoreDeletion(batchId))
                    .then(() => setDeletionRestored(true))
                    .catch(() => undefined)
                    .finally(() => setRestoringDeletion(false));
                }}
              >
                <RotateCcw className="h-3 w-3" />
                {restoringDeletion ? t('common:saving') : t('chat:tool.restoreDeleted')}
              </button>
            ) : null}
          </div>
        </div>
      );
    }
    
    // query_files success
    if (toolName === 'query_files' && data) {
      const files = Array.isArray(data) ? data : [];
      const count = files.length;
      
      if (count === 0) {
        return (
          <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
            <div className="flex items-center gap-2">
              <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('chat:tool.no_files_found')}
              </span>
            </div>
          </div>
        );
      }
      
      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t('chat:tool.files_found', { count })}
            </span>
          </div>
          {count <= 5 && (
            <div className="mt-1.5 ml-6 space-y-1">
              {files.map((file: Record<string, unknown>, index: number) => {
                const fileTitle = file.title as string || t('common:untitled');
                const fileType = file.file_type as string || '';
                const typeInfo = getFileTypeInfo(fileType, t);
                return (
                  <div key={index} className="flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]">
                    {typeInfo.icon}
                    <span>{fileTitle}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      );
    }
    
    // update_project success
    if (toolName === 'update_project' && data) {
      const projectStatus = data.project_status as Record<string, unknown> | undefined;
      const rawUpdatedFields = data.updated_fields ?? projectStatus?.updated_fields;
      const updatedFields = Array.isArray(rawUpdatedFields)
        ? rawUpdatedFields.filter((field): field is string => typeof field === 'string')
        : [];
      const fieldLabels: Record<string, string> = {
        summary: t('chat:project.field.summary'),
        current_phase: t('chat:project.field.current_phase'),
        writing_style: t('chat:project.field.writing_style'),
        notes: t('chat:project.field.notes'),
      };

      const notesAdded = readNotesAdded(data);
      // notes_added has its own receipt below, so do not repeat it in the generic field list.
      const shownFields = notesAdded ? updatedFields.filter((f) => f !== 'notes') : updatedFields;
      const updatedLabels = shownFields
        .map((f) => fieldLabels[f] || f)
        .join(t('common:separator'));
      const otherChanges = data.project_name_updated === true
        || projectStatus?.project_name_updated === true
        || (data.plan !== undefined && data.plan !== null);
      const changed = updatedFields.length > 0 || otherChanges || notesAdded !== null;

      // Nothing visible changed (e.g. notes copied back unchanged): no green success.
      if (!changed) {
        return (
          <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
            <div className="flex items-center gap-2">
              <Info className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {typeof data.notes_note === 'string' ? t('chat:notesAdded.full') : t('chat:tool.status_unchanged')}
              </span>
            </div>
          </div>
        );
      }

      return (
        <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
          {(shownFields.length > 0 || otherChanges) && (
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-[hsl(var(--success-text))]" />
              <span className="text-sm font-medium text-[hsl(var(--text-primary))]">{t('chat:tool.status_updated')}</span>
              {updatedLabels && <span className="text-xs text-[hsl(var(--text-secondary))]">({updatedLabels})</span>}
            </div>
          )}
          {typeof data.notes_note === 'string' && (
            <p className="text-xs text-[hsl(var(--text-secondary))] mt-1 ml-6">{t('chat:notesAdded.full')}</p>
          )}
          {notesAdded && !isPending && !interrupted && (
            <div className={shownFields.length > 0 || otherChanges ? 'mt-2 border-t border-[hsl(var(--separator-color))]' : ''}>
              <RememberedNotes entries={notesAdded} onOpen={onOpenAIMemory} embedded showIcon={!(shownFields.length > 0 || otherChanges)} />
            </div>
          )}
        </div>
      );
    }
    
    // edit_file success - special detailed rendering
    if (toolName === 'edit_file' && data) {
      const undoTarget = readFileEditUndoTarget(data, chatMessageId, toolUseId);
      // The author undid this edit: here (live) or as recorded on the card by the server (after a refresh).
      const undone = undoTarget !== null && isFileEditUndone(data, undoTarget, undoneEdits);
      const details = data.details as Array<{
        op: string;
        old_preview?: string;
        new_preview?: string;
        anchor_preview?: string;
        text_preview?: string;
        text_len?: number;
        /** Word count of the inserted text (server `count_words`); absent on older servers. */
        text_words?: number;
        deleted_preview?: string;
        /** Text actually replaced (fuzzy/approximate matches); `old_preview` is the model's quote. */
        matched_original?: string;
        count?: number;
      }> || [];
      // The edit rewrote paragraphs the author wrote after the AI (server-side, by position).
      const authorEditsTouched = touchedAuthorEdits(data);
      const authorDeletionRestored = restoredAuthorDeletion(data);
      
      const getOpIcon = (op: string) => {
        switch (op) {
          case 'replace':
            return <ArrowRight className="w-3 h-3 text-[hsl(var(--text-secondary))]" />;
          case 'append':
          case 'prepend':
          case 'insert_after':
          case 'insert_before':
            return <Plus className="w-3 h-3 text-[hsl(var(--success-text))]" />;
          case 'delete':
            return <Minus className="w-3 h-3 text-[hsl(var(--error-text))]" />;
          default:
            return <Edit3 className="w-3 h-3 text-[hsl(var(--text-secondary))]" />;
        }
      };
      
      const getOpLabel = (op: string) => {
        switch (op) {
          case 'replace': return t('chat:edit.replace');
          case 'append': return t('chat:edit.append');
          case 'prepend': return t('chat:edit.prepend');
          case 'insert_after': return t('chat:edit.insert_after');
          case 'insert_before': return t('chat:edit.insert_before');
          case 'delete': return t('chat:edit.label_delete');
          default: return op;
        }
      };
      
      return (
        <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="text-sm text-[hsl(var(--text-primary))] font-medium">
              {t('chat:tool.edit_success')}
            </span>
            {authorEditsTouched && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                {t('chat:tool.edit_touched_author_edits')}
              </span>
            )}
            {authorDeletionRestored && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                {t('chat:tool.edit_restored_author_deletion')}
              </span>
            )}
            {undone ? (
              <span className="ml-auto shrink-0 text-xs text-[hsl(var(--text-secondary))]">
                {t('chat:tool.edit_undone')}
              </span>
            ) : onUndo && isPersistedUndoTarget(undoTarget) && (
              <CardUndoControl
                target={undoTarget}
                blocked={undoBlocked.has(undoneEditKey(undoTarget))}
                onUndo={handleUndo}
                className="ml-auto"
              />
            )}
          </div>
          
          {details && details.length > 0 && (
            <details className="mt-2" open={authorEditsTouched || authorDeletionRestored || undefined}>
              <summary className="cursor-pointer select-none text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 list-none"
                style={{ listStyle: 'none' }}
              >
                <svg
                  className="w-3 h-3 transition-transform duration-200"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                {t('chat:tool.view_edit_details', { count: details.length })}
              </summary>
              <div className="space-y-2 mt-2">
                {details.map((detail, index) => (
                  <div key={index} className="rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.45)] p-2.5 text-xs">
                    <div className="flex items-center gap-1 mb-1">
                      {getOpIcon(detail.op)}
                      <span className="text-[hsl(var(--text-primary))] font-medium">
                        {getOpLabel(detail.op)}
                      </span>
                      {detail.count && detail.count > 1 && (
                        <span className="text-[hsl(var(--text-secondary))]">
                          × {detail.count}
                        </span>
                      )}
                    </div>

                    {detail.op === 'replace' && (
                      <div className="space-y-2 mt-1.5">
                        <div className="bg-[hsl(var(--diff-remove-bg))] p-2 rounded-lg border-l-2 border-[hsl(var(--diff-remove-text))]">
                          <div className="flex items-center gap-1 mb-1">
                            <Minus className="w-3 h-3 text-[hsl(var(--diff-remove-text))]" />
                            <span className="text-xs text-[hsl(var(--diff-remove-text))]">{t('chat:edit.label_delete')}</span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                            {detail.matched_original || detail.old_preview}
                          </div>
                        </div>
                        <div className="bg-[hsl(var(--diff-add-bg))] p-2 rounded-lg border-l-2 border-[hsl(var(--diff-add-text))]">
                          <div className="flex items-center gap-1 mb-1">
                            <Plus className="w-3 h-3 text-[hsl(var(--diff-add-text))]" />
                            <span className="text-xs text-[hsl(var(--diff-add-text))]">{t('chat:edit.label_add')}</span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                            {detail.new_preview}
                          </div>
                        </div>
                      </div>
                    )}

                    {(detail.op === 'append' || detail.op === 'prepend') && (
                      <div className="bg-[hsl(var(--diff-add-bg))] p-2 rounded-lg border-l-2 border-[hsl(var(--diff-add-text))] mt-1.5">
                        <div className="flex items-center gap-1 mb-1">
                          <Plus className="w-3 h-3 text-[hsl(var(--diff-add-text))]" />
                          <span className="text-xs text-[hsl(var(--diff-add-text))]">
                            {detail.op === 'append' ? t('chat:edit.append_content') : t('chat:edit.prepend_content')}
                            {detail.text_len && detail.text_len > 200 && typeof detail.text_words === 'number' && (
                              <span className="ml-1 text-[hsl(var(--text-secondary))]">
                                ({formatWordCount(detail.text_words, t)})
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                          {detail.text_preview}
                        </div>
                      </div>
                    )}

                    {(detail.op === 'insert_after' || detail.op === 'insert_before') && (
                      <div className="space-y-2 mt-1.5">
                        <div className="bg-[hsl(var(--bg-secondary))] p-2 rounded-lg border-l-2 border-[hsl(var(--border-color))]">
                          <div className="flex items-center gap-1 mb-1">
                            <span className="text-xs text-[hsl(var(--text-secondary))]">
                              {detail.op === 'insert_after' ? t('chat:edit.insert_after_label') : t('chat:edit.insert_before_label')}
                            </span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-secondary))] whitespace-pre-wrap break-words font-normal max-h-40 overflow-auto">
                            {detail.anchor_preview}
                          </div>
                        </div>
                        <div className="bg-[hsl(var(--diff-add-bg))] p-2 rounded-lg border-l-2 border-[hsl(var(--diff-add-text))]">
                          <div className="flex items-center gap-1 mb-1">
                            <Plus className="w-3 h-3 text-[hsl(var(--diff-add-text))]" />
                            <span className="text-xs text-[hsl(var(--diff-add-text))]">
                              {t('chat:edit.insert_content')}
                              {detail.text_len && detail.text_len > 200 && typeof detail.text_words === 'number' && (
                                <span className="ml-1 text-[hsl(var(--text-secondary))]">
                                  ({formatWordCount(detail.text_words, t)})
                                </span>
                              )}
                            </span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                            {detail.text_preview}
                          </div>
                        </div>
                      </div>
                    )}

                    {detail.op === 'delete' && (
                      <div className="bg-[hsl(var(--diff-remove-bg))] p-2 rounded-lg border-l-2 border-[hsl(var(--diff-remove-text))] mt-1.5">
                        <div className="flex items-center gap-1 mb-1">
                          <Minus className="w-3 h-3 text-[hsl(var(--diff-remove-text))]" />
                          <span className="text-xs text-[hsl(var(--diff-remove-text))]">{t('chat:edit.delete_content')}</span>
                        </div>
                        <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                          {detail.deleted_preview}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      );
    }
    
    // parallel_execute success - show per-subtask status so partial failures
    // are visible (the aggregate envelope stays "success" to preserve data).
    if (toolName === 'parallel_execute' && data) {
      const tasks = Array.isArray(data.tasks)
        ? (data.tasks as Array<Record<string, unknown>>)
        : [];
      const total = (data.total_tasks as number) ?? tasks.length;
      const completed =
        (data.completed as number) ??
        tasks.filter((task) => task.status === 'completed').length;
      const failed =
        (data.failed as number) ??
        tasks.filter((task) => task.status === 'failed').length;
      // partial: a continue_on_error edit applied only some of its edits (older payloads lack the total).
      const partial =
        typeof data.partial === 'number'
          ? data.partial
          : tasks.filter((task) => task.status === 'partial').length;
      const taskResultOf = (task: Record<string, unknown>) =>
        task.result && typeof task.result === 'object'
          ? (task.result as Record<string, unknown>)
          : undefined;
      // 有意没动的子任务（作者手改过、读过之后作者又改了）不算没做成，不标警告色。
      const isIntentionalSkip = (task: Record<string, unknown>) =>
        task.status === 'failed' &&
        INTENTIONAL_SKIP_KINDS.has(resolveToolFailure(taskResultOf(task), t).kind);
      // 标题里的「失败」数不含有意没动的子任务（它们在下面逐条说明）。
      const skippedCount = tasks.filter(isIntentionalSkip).length;
      const shownFailed = Math.max(0, failed - skippedCount);
      const anyFailed = shownFailed > 0 || partial > 0;
      const iconClass = anyFailed
        ? 'text-[hsl(var(--warning-text))]'
        : 'text-[hsl(var(--accent-primary))]';
      const titleClass = anyFailed
        ? 'text-[hsl(var(--warning-text))] font-medium'
        : 'text-[hsl(var(--text-primary))] font-medium';

      return (
        <div
          className={`rounded-xl border bg-[hsl(var(--bg-secondary))] px-3 py-2.5 ${
            anyFailed
              ? 'border-[hsl(var(--warning))]'
              : 'border-[hsl(var(--border-color))]'
          }`}
        >
          <div className="flex items-center gap-2">
            <Layers className={`w-4 h-4 shrink-0 ${iconClass}`} />
            <span className={`text-sm ${titleClass}`}>
              {partial > 0
                ? skippedCount > 0
                  ? t('chat:tool.parallel_summary_with_partial_and_skipped', { total, completed, partial, skipped: skippedCount, failed: shownFailed })
                  : t('chat:tool.parallel_summary_with_partial', { total, completed, partial, failed })
                : skippedCount > 0
                  ? t('chat:tool.parallel_summary_with_skipped', { total, completed, skipped: skippedCount, failed: shownFailed })
                  : t('chat:tool.parallel_summary', { total, completed, failed })}
            </span>
          </div>
          {tasks.length > 0 && (
            <div className="mt-2 space-y-1">
              {tasks.map((task, index) => {
                const isFailed = task.status === 'failed';
                const isPartial = task.status === 'partial';
                // Statuses this client does not know are neither success nor failure.
                const isUnknown = !isFailed && !isPartial && task.status !== 'completed';
                const taskResult = taskResultOf(task);
                const skipped = isIntentionalSkip(task);
                // Localized task kind; unknown internal types are hidden, never shown raw.
                const taskLabel = getParallelTaskLabel(task.type, t);
                const desc =
                  sanitizeAgentText(task.description as string, t) ||
                  taskLabel ||
                  `#${index + 1}`;
                const showTaskLabel = Boolean(taskLabel) && taskLabel !== desc;
                return (
                  <div
                    key={(task.id as string) || index}
                    className="flex items-start gap-2 text-xs"
                  >
                    {skipped ? (
                      <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--text-secondary))]" />
                    ) : isFailed ? (
                      <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--text-secondary))]" />
                    ) : isPartial ? (
                      <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--warning-text))]" />
                    ) : isUnknown ? (
                      <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--text-secondary))]" />
                    ) : (
                      <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--success-text))]" />
                    )}
                    <div className="min-w-0">
                      {showTaskLabel ? (
                        <span className="text-[hsl(var(--text-secondary))]">
                          {t('chat:tool.parallelTaskLabel', { label: taskLabel })}
                        </span>
                      ) : null}
                      <span className="text-[hsl(var(--text-primary))] break-words">
                        {desc}
                      </span>
                      {skipped ? (
                        <div className="text-[hsl(var(--text-secondary))] break-words">
                          {resolveToolFailure(taskResult, t).message}
                        </div>
                      ) : isFailed ? (
                        <div className="text-[hsl(var(--text-secondary))] break-words">
                          {(isChineseUi() ? readUserFacingText(taskResult?.user_message) : null) ?? t('chat:tool.taskNotCompleted')}
                        </div>
                      ) : isPartial ? (
                        <div className="text-[hsl(var(--text-secondary))] break-words">
                          {t('chat:tool.taskPartiallyCompleted')}
                        </div>
                      ) : isUnknown ? (
                        <div className="text-[hsl(var(--text-secondary))] break-words">
                          {t('chat:tool.taskOutcomeUnknown')}
                        </div>
                      ) : touchedAuthorEdits(taskResult) ? (
                        <div className="text-[hsl(var(--text-secondary))] break-words">
                          {t('chat:tool.edit_touched_author_edits')}
                        </div>
                      ) : restoredAuthorDeletion(taskResult) ? (
                        <div className="text-[hsl(var(--text-secondary))] break-words">
                          {t('chat:tool.edit_restored_author_deletion')}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      );
    }

    // load_skill / read_skill_resource success: the server only ships the skill
    // name, resource path and sizes (full instructions/content go to the model only)
    if ((toolName === 'load_skill' || toolName === 'read_skill_resource') && data) {
      const { label } = getToolDisplayInfo(toolName, t);
      const skillName = typeof data.skill_name === 'string' ? data.skill_name : '';
      const path = typeof data.path === 'string' ? data.path : '';
      const detail = [skillName ? quote(skillName) : '', path].filter(Boolean).join(' · ');
      return (
        <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
            <span className="shrink-0 text-sm font-medium text-[hsl(var(--text-primary))]">
              {label}{t('chat:tool.complete_suffix')}
            </span>
            {detail && (
              <span className="min-w-0 text-xs text-[hsl(var(--text-secondary))] truncate">{detail}</span>
            )}
          </div>
        </div>
      );
    }

    // Default fallback - show minimal success message without technical details
    const { label } = getToolDisplayInfo(toolName, t);
    return (
      <div className={`px-3 py-2 ${CHAT_CARD_SURFACE}`}>
        <div className="flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
          <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
            {label}{t('chat:tool.complete_suffix')}
          </span>
        </div>
      </div>
    );
  }

  return null;
};

export const ToolResultCard = React.memo(ToolResultCardComponent);

export default ToolResultCard;
