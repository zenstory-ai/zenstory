import React from 'react';
import { Info, XCircle } from 'lucide-react';
import { RememberedNotes } from '../../RememberedNotes';
import { CHAT_CARD_SURFACE } from '../../chatCardStyles';
import {
  ToolCardFileRow,
  getToolDisplayInfo,
  INTENTIONAL_SKIP_KINDS,
  readNotesAdded,
  readUserFacingText,
  resolveToolFailure,
  type TranslateFn,
} from './toolCardHelpers';

export const ToolFailureCard: React.FC<{
  toolName: string;
  result?: Record<string, unknown>;
  isPending?: boolean;
  interrupted?: boolean;
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  onOpenAIMemory?: () => void;
  t: TranslateFn;
}> = ({
  toolName,
  result,
  isPending,
  interrupted,
  onOpenFile,
  onOpenAIMemory,
  t,
}) => {
  // 中途某一步没做成是常态，用中性样式，不用刺眼红色报错。
  const { label } = getToolDisplayInfo(toolName, t);
  const { kind, message: userMessage } = resolveToolFailure(result, t);
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
    ?? '';
  const targetTitle =
    readUserFacingText(data?.title)
    ?? readUserFacingText(result?.title)
    ?? '';
  const targetFileType =
    readUserFacingText(data?.file_type)
    ?? readUserFacingText(result?.file_type)
    ?? '';
  const showFileRow = Boolean(
    targetTitle
    && toolName !== 'create_file'
    && kind !== 'fileMissing'
    && kind !== 'permissionDenied',
  );
  const canOpenTargetFile = Boolean(
    showFileRow
    && onOpenFile
    && targetFileId
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
              title={targetTitle}
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
      </div>
      {partialNotes && !isPending && !interrupted && (
        <RememberedNotes entries={partialNotes} onOpen={onOpenAIMemory} />
      )}
    </div>
  );
};
