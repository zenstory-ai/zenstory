import React from 'react';
import { Info, XCircle } from 'lucide-react';
import { RememberedNotes } from '../../RememberedNotes';
import { AnimatedDetails } from '../../ui/AnimatedCollapse';
import { CHAT_CARD_SURFACE } from '../../chatCardStyles';
import { FailedEditsSection, RetryEditFailureAction } from './FailedEditsSection';
import {
  ToolCardFileRow,
  extractFailedEdits,
  getToolDisplayInfo,
  INTENTIONAL_SKIP_KINDS,
  readNotesAdded,
  readUserFacingText,
  resolveToolFailure,
  type EditFailureRetryContext,
  type TranslateFn,
} from './toolCardHelpers';

const RETRYABLE_FILE_EDIT_TOOLS: ReadonlySet<string> = new Set([
  'edit_file',
  'batch_replace_files',
  'write_chapter',
  'update_file',
]);

export const ToolFailureCard: React.FC<{
  toolName: string;
  result?: Record<string, unknown>;
  toolArguments?: Record<string, unknown>;
  isPending?: boolean;
  interrupted?: boolean;
  isStreaming?: boolean;
  nextStepsBlocked?: boolean;
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  onOpenAIMemory?: () => void;
  onRetryEditFailure?: (context: EditFailureRetryContext) => void;
  t: TranslateFn;
}> = ({
  toolName,
  result,
  toolArguments,
  isPending,
  interrupted,
  isStreaming = false,
  nextStepsBlocked = false,
  onOpenFile,
  onOpenAIMemory,
  onRetryEditFailure,
  t,
}) => {
  // 中途某一步没做成是常态，用中性样式，不用刺眼红色报错。
  const { label } = getToolDisplayInfo(toolName, t);
  const { kind, message: userMessage } = resolveToolFailure(result, t, { isStreaming });
  const intentionalSkip = INTENTIONAL_SKIP_KINDS.has(kind);
  const data = (result?.data && typeof result.data === 'object'
    ? result.data
    : result) as Record<string, unknown> | undefined;

  // update_project with a rejected task board still saved its notes (partial result).
  const partialNotes = toolName === 'update_project'
    ? readNotesAdded(data)
    : null;

  const targetFileId =
    readUserFacingText(data?.id)
    ?? readUserFacingText(result?.id)
    ?? readUserFacingText(toolArguments?.id)
    ?? '';
  const targetTitle =
    readUserFacingText(data?.title)
    ?? readUserFacingText(result?.title)
    ?? readUserFacingText(toolArguments?.title)
    ?? '';
  const targetFileType =
    readUserFacingText(data?.file_type)
    ?? readUserFacingText(result?.file_type)
    ?? readUserFacingText(toolArguments?.file_type)
    ?? '';
  const canOpenTargetFile = Boolean(
    onOpenFile
    && toolName !== 'create_file'
    && targetFileId
    && kind !== 'fileMissing'
    && !isPending
    && !interrupted,
  );
  const showFileRow =
    toolName !== 'create_file'
    && (Boolean(targetTitle) || canOpenTargetFile);

  const topErrorType =
    readUserFacingText(result?.error_type)
    ?? readUserFacingText(data?.error_type);
  const failedEdits = toolName === 'edit_file'
    ? extractFailedEdits(data, toolArguments, topErrorType)
    : [];
  const skippedFiles = toolName === 'batch_replace_files'
    && Array.isArray(data?.skipped_files)
    ? data.skipped_files.filter(
        (item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'),
      )
    : [];

  const showRetryAction = Boolean(
    RETRYABLE_FILE_EDIT_TOOLS.has(toolName)
    && !intentionalSkip
    && kind !== 'fileMissing'
    && kind !== 'repeatRead'
    && kind !== 'noChange'
    && onRetryEditFailure
    && !isStreaming
    && !nextStepsBlocked
    && !isPending
    && !interrupted,
  );

  return (
    <div className="space-y-2">
      <div className={`px-3 py-2.5 ${CHAT_CARD_SURFACE}`}>
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

        {showFileRow && (
          <div className="mt-1.5 pl-6">
            <ToolCardFileRow
              id={canOpenTargetFile ? targetFileId : undefined}
              title={targetTitle || t('chat:tool.open_target_file')}
              fileType={targetFileType}
              onOpenFile={canOpenTargetFile ? onOpenFile : undefined}
              isPending={isPending}
              interrupted={interrupted}
            />
          </div>
        )}

        <p className="text-xs leading-5 text-[hsl(var(--text-secondary))] mt-1 ml-6">
          {userMessage}
        </p>

        {failedEdits.length > 0 && (
          <FailedEditsSection
            failedEdits={failedEdits}
            defaultOpen={true}
            isStreaming={isStreaming}
            t={t}
          />
        )}

        {skippedFiles.length > 0 && (
          <AnimatedDetails
            className="group mt-1.5"
            defaultOpen={true}
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
                {t('chat:tool.skipped_files_summary', { count: skippedFiles.length })}
              </>
            )}
          >
            <div className="mt-1.5 pl-6 space-y-1.5">
              {skippedFiles.map((file, index) => {
                const failedTitle = readUserFacingText(file.title) ?? t('common:untitled');
                const failedFileType = readUserFacingText(file.file_type) ?? '';
                const failureType = readUserFacingText(file.error_type) ?? readUserFacingText(file.reason);
                const reason = failureType || readUserFacingText(file.user_message)
                  ? resolveToolFailure({ ...file, error_type: failureType }, t, { isStreaming }).message
                  : t('chat:tool.taskNotCompleted');
                const canOpenSkipped = Boolean(
                  onOpenFile
                  && typeof file.id === 'string'
                  && file.id.trim()
                  && failureType !== 'file_not_found'
                  && !isPending
                  && !interrupted,
                );
                return (
                  <div
                    key={typeof file.id === 'string' && file.id ? file.id : index}
                    className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-[hsl(var(--text-secondary))] break-words"
                  >
                    <ToolCardFileRow
                      id={canOpenSkipped ? file.id : undefined}
                      title={failedTitle}
                      fileType={failedFileType}
                      onOpenFile={canOpenSkipped ? onOpenFile : undefined}
                      isPending={isPending}
                      interrupted={interrupted}
                    />
                    <span>· {reason}</span>
                  </div>
                );
              })}
            </div>
          </AnimatedDetails>
        )}

        {showRetryAction && onRetryEditFailure && (
          <RetryEditFailureAction
            context={{
              toolName,
              fileId: targetFileId || undefined,
              title: targetTitle || undefined,
              fileType: targetFileType || undefined,
              failedCount: failedEdits.length || skippedFiles.length || undefined,
            }}
            onRetry={onRetryEditFailure}
            t={t}
          />
        )}
      </div>
      {partialNotes && !isPending && !interrupted && (
        <RememberedNotes entries={partialNotes} onOpen={onOpenAIMemory} />
      )}
    </div>
  );
};
