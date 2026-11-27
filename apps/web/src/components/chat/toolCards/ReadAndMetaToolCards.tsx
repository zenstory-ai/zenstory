import React from 'react';
import {
  CheckCircle2,
  AlertCircle,
  XCircle,
  Info,
  Search,
  BookOpen,
  Layers,
} from 'lucide-react';
import { sanitizeAgentText } from '../../../lib/agentDisplayName';
import { RememberedNotes } from '../../RememberedNotes';
import { CHAT_CARD_SURFACE } from '../../chatCardStyles';
import {
  INTENTIONAL_SKIP_KINDS,
  ToolCardFileRow,
  extractFileList,
  formatMatchCountHeadline,
  getParallelTaskLabel,
  getToolDisplayInfo,
  isChineseUi,
  quote,
  readNotesAdded,
  readUserFacingText,
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

export const QueryOrSearchFilesCard: React.FC<{
  data: Record<string, unknown>;
  t: TranslateFn;
} & SharedFileRowProps> = ({ data, onOpenFile, isPending, interrupted, t }) => {
  const files = extractFileList(data);
  const totalMatches = !Array.isArray(data) && typeof data.total_matches === 'number'
    ? data.total_matches
    : null;
  const queryText = !Array.isArray(data) && typeof data.query === 'string' && data.query.trim()
    ? data.query.trim()
    : '';

  if (totalMatches !== null) {
    const matchedFilesCount =
      typeof data.matched_files === 'number'
        ? data.matched_files
        : typeof data.files_matched === 'number'
          ? data.files_matched
          : files.length;
    if (totalMatches === 0 || matchedFilesCount === 0) {
      return (
        <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Search className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
            <span className="text-sm text-[hsl(var(--text-secondary))]">
              {t('chat:tool.no_search_matches')}
            </span>
            {queryText && (
              <span className="text-xs text-[hsl(var(--text-secondary))] break-words">
                {quote(queryText)}
              </span>
            )}
          </div>
        </div>
      );
    }
    return (
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
          <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
            {formatMatchCountHeadline('search_matches', matchedFilesCount, totalMatches, t)}
          </span>
          {queryText && (
            <span className="text-xs text-[hsl(var(--text-secondary))] break-words">
              {quote(queryText)}
            </span>
          )}
        </div>
        {files.length > 0 && files.length <= 5 && (
          <div className="mt-1.5 pl-6 flex flex-col items-start gap-2 md:gap-1">
            {files.map((file, index) => {
              const itemTitle = (file.title as string) || t('common:untitled');
              const itemFileType = (file.file_type as string) || '';
              const matchCount = typeof file.match_count === 'number' ? file.match_count : 0;
              return (
                <div
                  key={typeof file.id === 'string' && file.id ? file.id : index}
                  className="flex flex-wrap items-center gap-x-2 gap-y-0.5 max-w-full"
                >
                  <ToolCardFileRow
                    id={file.id}
                    title={itemTitle}
                    fileType={itemFileType}
                    onOpenFile={onOpenFile}
                    isPending={isPending}
                    interrupted={interrupted}
                  />
                  {matchCount > 0 && (
                    <span className="text-xs text-[hsl(var(--text-secondary))] tabular-nums">
                      {t('chat:tool.search_file_match_count', { count: matchCount })}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  const count = files.length;

  if (count === 0) {
    return (
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {t('chat:tool.files_found', { count })}
        </span>
      </div>
      {count <= 5 && (
        <div className="mt-1.5 pl-6 flex flex-col items-start gap-2 md:gap-1">
          {files.map((file, index) => {
            const itemTitle = (file.title as string) || t('common:untitled');
            const itemFileType = (file.file_type as string) || '';
            return (
              <React.Fragment key={typeof file.id === 'string' && file.id ? file.id : index}>
                <ToolCardFileRow
                  id={file.id}
                  title={itemTitle}
                  fileType={itemFileType}
                  onOpenFile={onOpenFile}
                  isPending={isPending}
                  interrupted={interrupted}
                />
              </React.Fragment>
            );
          })}
        </div>
      )}
    </div>
  );
};

export const ReadFilesCard: React.FC<{
  data: Record<string, unknown>;
  t: TranslateFn;
} & SharedFileRowProps> = ({ data, onOpenFile, isPending, interrupted, t }) => {
  const list = extractFileList(data);
  const files = list.length > 0
    ? list
    : (!Array.isArray(data) && (typeof data.id === 'string' || typeof data.title === 'string') ? [data] : []);
  const count = files.length;

  if (count === 0) {
    return (
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 shrink-0 text-[hsl(var(--text-secondary))]" />
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {t('chat:tool.no_files_read')}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {t('chat:tool.files_read', { count })}
        </span>
      </div>
      {count <= 5 && (
        <div className="mt-1.5 pl-6 flex flex-col items-start gap-2 md:gap-1">
          {files.map((file, index) => {
            const itemTitle = (file.title as string) || t('common:untitled');
            const itemFileType = (file.file_type as string) || '';
            return (
              <React.Fragment key={typeof file.id === 'string' && file.id ? file.id : index}>
                <ToolCardFileRow
                  id={file.id}
                  title={itemTitle}
                  fileType={itemFileType}
                  onOpenFile={onOpenFile}
                  isPending={isPending}
                  interrupted={interrupted}
                />
              </React.Fragment>
            );
          })}
        </div>
      )}
    </div>
  );
};

export const UpdateProjectCard: React.FC<{
  data: Record<string, unknown>;
  isPending?: boolean;
  interrupted?: boolean;
  onOpenAIMemory?: () => void;
  t: TranslateFn;
}> = ({ data, isPending, interrupted, onOpenAIMemory, t }) => {
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
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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
          <RememberedNotes
            entries={notesAdded}
            onOpen={onOpenAIMemory}
            embedded
            showIcon={!(shownFields.length > 0 || otherChanges)}
          />
        </div>
      )}
    </div>
  );
};

export const ParallelExecuteCard: React.FC<{
  data: Record<string, unknown>;
  t: TranslateFn;
}> = ({ data, t }) => {
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
};

export const SkillToolCard: React.FC<{
  toolName: string;
  data: Record<string, unknown>;
  t: TranslateFn;
}> = ({ toolName, data, t }) => {
  const { label } = getToolDisplayInfo(toolName, t);
  const skillName = typeof data.skill_name === 'string' ? data.skill_name : '';
  const path = typeof data.path === 'string' ? data.path : '';
  const detail = [skillName ? quote(skillName) : '', path].filter(Boolean).join(' · ');
  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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
};

export const FallbackToolCard: React.FC<{
  toolName: string;
  t: TranslateFn;
}> = ({ toolName, t }) => {
  const { label } = getToolDisplayInfo(toolName, t);
  return (
    <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-text))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {label}{t('chat:tool.complete_suffix')}
        </span>
      </div>
    </div>
  );
};
