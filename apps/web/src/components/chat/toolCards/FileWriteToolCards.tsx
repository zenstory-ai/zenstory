import React, { useState } from 'react';
import {
  CheckCircle2,
  AlertCircle,
  RotateCcw,
  Info,
  Edit3,
  ArrowRight,
  Plus,
  Minus,
} from 'lucide-react';
import type { FileEditUndoTarget } from '../../../types';
import { undoneEditKey } from '../../../lib/undoneEdits';
import { isPersistedUndoTarget, readFileEditUndoTarget, readWriteChapterOutcome } from '../../../lib/aiWriteCards';
import { countWords } from '../../../lib/documentChunker';
import { AnimatedDetails } from '../../ui/AnimatedCollapse';
import { CHAT_CARD_INLINE_ACTION, CHAT_CARD_SURFACE } from '../../chatCardStyles';
import {
  CardUndoControl,
  ToolCardFileRow,
  WRITE_CHAPTER_HEADLINES,
  WRITE_EPISODE_HEADLINES,
  formatMatchCountHeadline,
  formatWordCount,
  getFileTypeInfo,
  isEpisodeFile,
  isFileEditUndone,
  quote,
  readChapterNumber,
  readUserFacingText,
  readWordCount,
  resolveToolFailure,
  restoredAuthorDeletion,
  touchedAuthorEdits,
  type TranslateFn,
} from './toolCardHelpers';

interface SharedFileRowProps {
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  isPending?: boolean;
  interrupted?: boolean;
}

export const CreateFileCard: React.FC<{
  data: Record<string, unknown>;
  fileId: string;
  t: TranslateFn;
} & SharedFileRowProps> = ({ data, fileId, onOpenFile, isPending, interrupted, t }) => {
  const title = (data.title as string) || t('common:untitled');
  const fileType = (data.file_type as string) || '';
  const wordCount = readWordCount(data.new_word_count)
    ?? (typeof data.content === 'string' ? countWords(data.content) : 0);
  const typeInfo = getFileTypeInfo(fileType, t);

  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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
      </div>
      <div className="mt-1.5 pl-6">
        <ToolCardFileRow
          id={fileId}
          title={title}
          fileType={fileType}
          onOpenFile={onOpenFile}
          isPending={isPending}
          interrupted={interrupted}
        />
      </div>
    </div>
  );
};

export const WriteChapterCard: React.FC<{
  data: Record<string, unknown>;
  fileId: string;
  chatMessageId?: string;
  toolUseId?: string;
  undoneEdits: ReadonlySet<string>;
  undoBlocked: ReadonlySet<string>;
  onUndo?: (target: FileEditUndoTarget) => void;
  t: TranslateFn;
} & SharedFileRowProps> = ({
  data,
  fileId,
  chatMessageId,
  toolUseId,
  undoneEdits,
  undoBlocked,
  onUndo,
  onOpenFile,
  isPending,
  interrupted,
  t,
}) => {
  const title = (data.title as string) || t('common:untitled');
  const fileType = (data.file_type as string) || '';
  const chapter = readChapterNumber(data.chapter);
  const outcome = readWriteChapterOutcome(data);
  const previousWords = readWordCount(data.previous_word_count);
  const newWords = readWordCount(data.new_word_count)
    ?? (typeof data.content === 'string' ? countWords(data.content) : null);
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
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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
            onUndo={onUndo}
          />
        )}
      </div>
      <div className="mt-1.5 pl-6">
        <ToolCardFileRow
          id={fileId}
          title={title}
          fileType={fileType}
          onOpenFile={onOpenFile}
          isPending={isPending}
          interrupted={interrupted}
        />
      </div>
      {(outcome === 'replaced' || outcome === 'replacing') && previousWords !== null && previousWords > 0 && (
        <div className="mt-1 pl-6 text-xs text-[hsl(var(--text-secondary))]">
          {t(outcome === 'replaced' ? 'chat:tool.chapterReplacedPrevious' : 'chat:tool.chapterReplacingPrevious', {
            size: formatWordCount(previousWords, t),
          })}
        </div>
      )}
    </div>
  );
};

export const UpdateFileCard: React.FC<{
  data: Record<string, unknown>;
  fileId: string;
  t: TranslateFn;
} & SharedFileRowProps> = ({ data, fileId, onOpenFile, isPending, interrupted, t }) => {
  const title = (data.title as string) || t('chat:fileDefault');
  const fileType = (data.file_type as string) || '';
  const wordCount = typeof data.content === 'string' ? countWords(data.content) : 0;

  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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
      </div>
      <div className="mt-1.5 pl-6">
        <ToolCardFileRow
          id={fileId}
          title={title}
          fileType={fileType}
          onOpenFile={onOpenFile}
          isPending={isPending}
          interrupted={interrupted}
        />
      </div>
    </div>
  );
};

export const RenameFileCard: React.FC<{
  data: Record<string, unknown>;
  t: TranslateFn;
}> = ({ data, t }) => {
  const from = typeof data.previous_title === 'string' ? data.previous_title : '';
  const to = typeof data.title === 'string' ? data.title : '';
  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))] break-words">
          {t('chat:tool.renamed', { from, to })}
        </span>
      </div>
    </div>
  );
};

export const DeleteFileCard: React.FC<{
  data: Record<string, unknown>;
  chatMessageId?: string;
  toolUseId?: string;
  onRestoreDeletion?: (
    fileId: string,
    card?: { chatMessageId?: string; toolUseId?: string },
  ) => void | Promise<void>;
  t: TranslateFn;
}> = ({ data, chatMessageId, toolUseId, onRestoreDeletion, t }) => {
  const [restoringDeletion, setRestoringDeletion] = useState(false);
  const [deletionRestored, setDeletionRestored] = useState(false);

  const fileId = typeof data.id === 'string' ? data.id : '';
  const deletedTitle = typeof data.title === 'string' ? data.title.trim() : '';
  const isRestored = data.restored === true || deletionRestored;

  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex flex-wrap items-center gap-2">
        <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))] break-words">
          {deletedTitle ? `${t('chat:tool.deleted')} ${quote(deletedTitle)}` : t('chat:tool.deleted')}
        </span>
        {isRestored ? (
          <span className="ml-auto text-xs text-[hsl(var(--text-secondary))]">{t('chat:tool.deletionRestored')}</span>
        ) : data.mutation_applied !== false && fileId && onRestoreDeletion ? (
          <button
            type="button"
            className={`${CHAT_CARD_INLINE_ACTION} ml-auto`}
            disabled={restoringDeletion}
            onClick={() => {
              if (restoringDeletion) return;
              setRestoringDeletion(true);
              void Promise.resolve(onRestoreDeletion(fileId, { chatMessageId, toolUseId }))
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
};

export const EditFileCard: React.FC<{
  data: Record<string, unknown>;
  fileId: string;
  chatMessageId?: string;
  toolUseId?: string;
  undoneEdits: ReadonlySet<string>;
  undoBlocked: ReadonlySet<string>;
  onUndo?: (target: FileEditUndoTarget) => void;
  t: TranslateFn;
} & SharedFileRowProps> = ({
  data,
  fileId,
  chatMessageId,
  toolUseId,
  undoneEdits,
  undoBlocked,
  onUndo,
  onOpenFile,
  isPending,
  interrupted,
  t,
}) => {
  const undoTarget = readFileEditUndoTarget(data, chatMessageId, toolUseId);
  // The author undid this edit: here (live) or as recorded on the card by the server (after a refresh).
  const undone = undoTarget !== null && isFileEditUndone(data, undoTarget, undoneEdits);
  const details = (data.details as Array<{
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
  }>) || [];
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

  const editTitle = typeof data.title === 'string' && data.title.trim() ? data.title.trim() : '';
  const editFileType = typeof data.file_type === 'string' ? data.file_type : '';

  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
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
            onUndo={onUndo}
            className="ml-auto"
          />
        )}
      </div>

      {editTitle && (
        <div className="mt-1.5 pl-6">
          <ToolCardFileRow
            id={fileId}
            title={editTitle}
            fileType={editFileType}
            onOpenFile={onOpenFile}
            isPending={isPending}
            interrupted={interrupted}
          />
        </div>
      )}

      {details && details.length > 0 && (
        <AnimatedDetails
          className="group mt-1.5"
          defaultOpen={authorEditsTouched || authorDeletionRestored}
          summaryClassName="cursor-pointer select-none pl-6 min-h-11 md:min-h-0 -my-1.5 md:my-0 rounded text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 list-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] [&::-webkit-details-marker]:hidden"
          summaryStyle={{ listStyle: 'none' }}
          summary={(isDetailsOpen) => (
            <>
              <svg
                className="w-3 h-3 shrink-0 motion-chevron group-open:rotate-90"
                style={!isDetailsOpen ? { transform: 'rotate(0deg)' } : undefined}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              {t('chat:tool.view_edit_details', { count: details.length })}
            </>
          )}
        >
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
        </AnimatedDetails>
      )}
    </div>
  );
};

export const BatchReplaceFilesCard: React.FC<{
  data: Record<string, unknown>;
  chatMessageId?: string;
  toolUseId?: string;
  undoneEdits: ReadonlySet<string>;
  undoBlocked: ReadonlySet<string>;
  onUndo?: (target: FileEditUndoTarget) => void;
  t: TranslateFn;
} & SharedFileRowProps> = ({
  data,
  chatMessageId,
  toolUseId,
  undoneEdits,
  undoBlocked,
  onUndo,
  onOpenFile,
  isPending,
  interrupted,
  t,
}) => {
  const isDryRun = data.dry_run === true;
  const toObjectList = (value: unknown): Array<Record<string, unknown>> =>
    Array.isArray(value)
      ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
      : [];
  const affectedFiles = toObjectList(data.files).length > 0
    ? toObjectList(data.files)
    : toObjectList(data.modified_files).length > 0
      ? toObjectList(data.modified_files)
      : toObjectList(data.matched_files);
  const failedFiles = toObjectList(data.skipped_files).length > 0
    ? toObjectList(data.skipped_files)
    : toObjectList(data.failed_files);

  const filesMatched =
    typeof data.files_matched === 'number'
      ? data.files_matched
      : typeof data.matched_files === 'number'
        ? data.matched_files
        : affectedFiles.length;
  const filesModified =
    typeof data.files_modified === 'number'
      ? data.files_modified
      : typeof data.modified_files === 'number'
        ? data.modified_files
        : isDryRun
          ? 0
          : affectedFiles.length;
  const totalMatches =
    typeof data.total_replacements === 'number'
      ? data.total_replacements
      : typeof data.total_matches === 'number'
        ? data.total_matches
        : affectedFiles.reduce((sum, file) => {
            const count =
              typeof file.replacements === 'number'
                ? file.replacements
                : typeof file.match_count === 'number'
                  ? file.match_count
                  : 0;
            return sum + count;
          }, 0);
  const remainingFiles =
    typeof data.remaining_count === 'number' && data.remaining_count > 0
      ? data.remaining_count
      : typeof data.remaining_files === 'number' && data.remaining_files > 0
        ? data.remaining_files
        : Array.isArray(data.remaining_files)
          ? data.remaining_files.length
          : 0;

  const renderBatchFiles = () => (
    <div className="flex flex-col gap-1.5">
      {affectedFiles.map((file, index) => {
        const itemTitle = (file.title as string) || t('common:untitled');
        const itemFileType = (file.file_type as string) || '';
        const count =
          typeof file.replacements === 'number'
            ? file.replacements
            : typeof file.match_count === 'number'
              ? file.match_count
              : 0;
        const entryUndo = !isDryRun
          ? readFileEditUndoTarget(file, chatMessageId, toolUseId)
          : null;
        const entryUndone = entryUndo !== null && isFileEditUndone(file, entryUndo, undoneEdits);

        return (
          <div
            key={typeof file.id === 'string' && file.id ? file.id : index}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 max-w-full"
          >
            <ToolCardFileRow
              id={file.id}
              title={itemTitle}
              fileType={itemFileType}
              onOpenFile={onOpenFile}
              isPending={isPending}
              interrupted={interrupted}
            />
            {count > 0 && (
              <span className="text-xs text-[hsl(var(--text-secondary))] tabular-nums">
                {t(isDryRun ? 'chat:tool.search_file_match_count' : 'chat:tool.batch_replace_file_count', { count })}
              </span>
            )}
            {entryUndone ? (
              <span className="ml-auto shrink-0 text-xs text-[hsl(var(--text-secondary))]">
                {t('chat:tool.edit_undone')}
              </span>
            ) : onUndo && isPersistedUndoTarget(entryUndo) ? (
              <CardUndoControl
                target={entryUndo}
                blocked={undoBlocked.has(undoneEditKey(entryUndo))}
                onUndo={onUndo}
                className="ml-auto"
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );

  if (isDryRun) {
    const isDryRunTruncated =
      affectedFiles.length > 0 && filesMatched > affectedFiles.length;
    const previewFilesCount = isDryRunTruncated ? affectedFiles.length : filesMatched;
    const hasMatches = previewFilesCount > 0 && totalMatches > 0;
    const previewHeadline = !hasMatches
      ? t('chat:tool.batch_replace_preview_none')
      : isDryRunTruncated
        ? previewFilesCount === 1 && totalMatches === 1
          ? t('chat:tool.batch_replace_preview_truncated_one_in_one', { matched: filesMatched })
          : previewFilesCount === 1
            ? t('chat:tool.batch_replace_preview_truncated_many_in_one', {
                matches: totalMatches,
                matched: filesMatched,
              })
            : t('chat:tool.batch_replace_preview_truncated_many_in_many', {
                files: previewFilesCount,
                matches: totalMatches,
                matched: filesMatched,
              })
        : formatMatchCountHeadline('batch_replace_preview', filesMatched, totalMatches, t);
    return (
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Info className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
          <span className={`min-w-0 flex-1 text-sm ${hasMatches ? 'font-medium text-[hsl(var(--text-primary))]' : 'text-[hsl(var(--text-secondary))]'}`}>
            {previewHeadline}
          </span>
          <span className="shrink-0 rounded-md bg-[hsl(var(--bg-tertiary))] px-1.5 py-0.5 text-xs font-medium text-[hsl(var(--text-secondary))]">
            {t('chat:tool.batch_replace_preview_badge')}
          </span>
        </div>
        {remainingFiles > 0 && (
          <div className="mt-1 pl-6 text-xs text-[hsl(var(--text-secondary))]">
            {t('chat:tool.batch_replace_remaining_files', { count: remainingFiles })}
          </div>
        )}
        {affectedFiles.length > 0 && affectedFiles.length <= 5 && (
          <div className="mt-1.5 pl-6">{renderBatchFiles()}</div>
        )}
        {affectedFiles.length > 5 && (
          <AnimatedDetails
            className="group mt-1.5"
            summaryClassName="cursor-pointer select-none pl-6 min-h-11 md:min-h-0 -my-1.5 md:my-0 rounded text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 list-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] [&::-webkit-details-marker]:hidden"
            summaryStyle={{ listStyle: 'none' }}
            summary={(isDetailsOpen) => (
              <>
                <svg
                  className="w-3 h-3 shrink-0 motion-chevron group-open:rotate-90"
                  style={!isDetailsOpen ? { transform: 'rotate(0deg)' } : undefined}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                {t('chat:tool.batch_replace_view_files', { count: affectedFiles.length })}
              </>
            )}
          >
            <div className="mt-1.5 pl-6">{renderBatchFiles()}</div>
          </AnimatedDetails>
        )}
      </div>
    );
  }

  if (filesModified === 0 && failedFiles.length === 0) {
    return (
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
        <div className="flex items-center gap-2">
          <Info className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {t('chat:tool.batch_replace_no_changes')}
          </span>
        </div>
      </div>
    );
  }

  const hasFailures = failedFiles.length > 0;
  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {hasFailures ? (
          <AlertCircle className="w-4 h-4 shrink-0 text-[hsl(var(--warning-text))]" />
        ) : (
          <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
        )}
        <span className="min-w-0 flex-1 text-sm font-medium text-[hsl(var(--text-primary))]">
          {filesModified > 0
            ? formatMatchCountHeadline('batch_replace_done', filesModified, totalMatches, t)
            : t('chat:tool.batch_replace_failed_count', { count: failedFiles.length })}
        </span>
        {filesModified > 0 && hasFailures && (
          <span className="shrink-0 text-xs text-[hsl(var(--warning-text))]">
            {t('chat:tool.batch_replace_failed_count', { count: failedFiles.length })}
          </span>
        )}
      </div>
      {remainingFiles > 0 && (
        <div className="mt-1 pl-6 text-xs text-[hsl(var(--text-secondary))]">
          {t('chat:tool.batch_replace_remaining_files', { count: remainingFiles })}
        </div>
      )}
      {affectedFiles.length > 0 && affectedFiles.length <= 5 && (
        <div className="mt-1.5 pl-6">{renderBatchFiles()}</div>
      )}
      {affectedFiles.length > 5 && (
        <AnimatedDetails
          className="group mt-1.5"
          summaryClassName="cursor-pointer select-none pl-6 min-h-11 md:min-h-0 -my-1.5 md:my-0 rounded text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 list-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] [&::-webkit-details-marker]:hidden"
          summaryStyle={{ listStyle: 'none' }}
          summary={(isDetailsOpen) => (
            <>
              <svg
                className="w-3 h-3 shrink-0 motion-chevron group-open:rotate-90"
                style={!isDetailsOpen ? { transform: 'rotate(0deg)' } : undefined}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              {t('chat:tool.batch_replace_view_files', { count: affectedFiles.length })}
            </>
          )}
        >
          <div className="mt-1.5 pl-6">{renderBatchFiles()}</div>
        </AnimatedDetails>
      )}
      {failedFiles.length > 0 && (
        <div className="mt-1.5 pl-6 space-y-1">
          {failedFiles.map((file, index) => {
            const failedTitle = readUserFacingText(file.title) ?? t('common:untitled');
            const failureType = readUserFacingText(file.error_type) ?? readUserFacingText(file.reason);
            const reason = failureType || readUserFacingText(file.user_message)
              ? resolveToolFailure({ ...file, error_type: failureType }, t).message
              : t('chat:tool.taskNotCompleted');
            return (
              <div
                key={typeof file.id === 'string' && file.id ? file.id : index}
                className="text-xs text-[hsl(var(--text-secondary))] break-words"
              >
                <span className="font-medium text-[hsl(var(--text-primary))]">{failedTitle}</span>
                <span> · {reason}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
