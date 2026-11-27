import React from 'react';
import { Info, XCircle } from 'lucide-react';
import { RememberedNotes } from '../../RememberedNotes';
import { CHAT_CARD_SURFACE } from '../../chatCardStyles';
import {
  getToolDisplayInfo,
  INTENTIONAL_SKIP_KINDS,
  readNotesAdded,
  resolveToolFailure,
  type TranslateFn,
} from './toolCardHelpers';

export const ToolFailureCard: React.FC<{
  toolName: string;
  result?: Record<string, unknown>;
  isPending?: boolean;
  interrupted?: boolean;
  onOpenAIMemory?: () => void;
  t: TranslateFn;
}> = ({ toolName, result, isPending, interrupted, onOpenAIMemory, t }) => {
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
