import React, { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle } from 'lucide-react';
import { Button } from './ui/Button';
import { useDialogInteractions } from './ui/dialogFocus';

export interface LeaveWhileGeneratingDialogProps {
  open: boolean;
  /** A deleted file has unsaved text that browser storage could not protect. */
  unprotectedDraft?: boolean;
  /**
   * What the author is about to do mid-round: leave the workbench (the default), or start a
   * new chat in the panel, which stops this round through the server first.
   */
  intent?: 'leave' | 'newChat';
  /** The round finished while the dialog was open: leaving no longer cuts anything off. */
  roundEnded: boolean;
  /** This round already wrote something (prose or a file): leaving keeps what was already saved. */
  producedOutput: boolean;
  /** Free plan: mention whether the round counts toward today's AI messages (Pro: never). */
  showDailyCount: boolean;
  onStay: () => void;
  onLeave: () => void;
}

/**
 * Asked before leaving the workbench (in-app link, Back, system back) or starting a new chat
 * mid-round. Staying is the primary action; going ahead is the quiet secondary one, because it
 * ends the round.
 */
export function LeaveWhileGeneratingDialog({
  open,
  unprotectedDraft = false,
  intent = 'leave',
  roundEnded,
  producedOutput,
  showDailyCount,
  onStay,
  onLeave,
}: LeaveWhileGeneratingDialogProps) {
  const { t } = useTranslation(['chat', 'editor']);
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);

  useDialogInteractions({
    open,
    dialogRef,
    onEscape: onStay,
    initialFocusSelector: '[data-leave-dialog-stay]',
  });

  if (!open) return null;

  const title = unprotectedDraft ? t('editor:deletedFile.leaveTitle') : roundEnded
    ? t('chat:leaveWhileGenerating.endedTitle', { defaultValue: '这一轮已经结束' })
    : t('chat:leaveWhileGenerating.title', { defaultValue: 'AI 还在写' });
  const newChat = intent === 'newChat';
  let message: string;
  if (unprotectedDraft) {
    message = t(roundEnded ? 'editor:deletedFile.leaveMessage' : 'editor:deletedFile.leaveMessageWhileGenerating');
  } else if (newChat) {
    message = newChatMessage(t, { roundEnded, producedOutput, showDailyCount });
  } else if (roundEnded) {
    message = t('chat:leaveWhileGenerating.endedMessage', { defaultValue: '现在离开不会中断任何内容。' });
  } else if (producedOutput) {
    message = showDailyCount
      ? t('chat:leaveWhileGenerating.messageWithOutput', {
        defaultValue: '离开这个页面会中断这一轮。已经写出的内容会保存，这一轮计入今日 AI 消息。',
      })
      : t('chat:leaveWhileGenerating.messageWithOutputUnlimited', {
        defaultValue: '离开这个页面会中断这一轮，已经写出的内容会保存。',
      });
  } else {
    message = showDailyCount
      ? t('chat:leaveWhileGenerating.message', {
        defaultValue: '离开这个页面会中断这一轮；AI 已开始处理时，这一轮仍计入今日 AI 消息。',
      })
      : t('chat:leaveWhileGenerating.messageUnlimited', { defaultValue: '离开这个页面会中断这一轮。' });
  }

  return createPortal(
    <div className="modal-overlay flex items-center justify-center p-4 motion-backdrop-enter" onClick={onStay} role="presentation">
      <div
        ref={dialogRef}
        className="modal ui-sans-headings w-full max-w-[400px] max-h-[calc(100dvh-2rem)] overflow-y-auto motion-surface-enter"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        data-testid={newChat ? 'new-chat-while-generating-dialog' : 'leave-while-generating-dialog'}
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[hsl(var(--warning)/0.1)] text-[hsl(var(--warning-text))]">
            <AlertCircle className="h-5 w-5" aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="break-words text-lg font-semibold leading-snug text-[hsl(var(--text-primary))]">
              {title}
            </h2>
          </div>
        </div>
        <div className="mb-6">
          <p id={descriptionId} className="break-words text-sm leading-relaxed text-[hsl(var(--text-secondary))]">
            {message}
          </p>
        </div>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end sm:gap-3">
          {newChat ? (
            <>
              <Button size="touch" className="w-full sm:w-auto" variant="secondary" onClick={onLeave} data-testid="new-chat-dialog-confirm">
                {roundEnded
                  ? t('chat:newChatWhileGenerating.confirmEnded', { defaultValue: '开始新对话' })
                  : t('chat:newChatWhileGenerating.confirm', { defaultValue: '停止并开始新对话' })}
              </Button>
              <Button size="touch" className="w-full sm:w-auto" variant="primary" onClick={onStay} data-leave-dialog-stay="" data-testid="new-chat-dialog-stay">
                {roundEnded
                  ? t('chat:newChatWhileGenerating.cancel', { defaultValue: '取消' })
                  : t('chat:leaveWhileGenerating.stay', { defaultValue: '继续等' })}
              </Button>
            </>
          ) : (
            <>
              <Button size="touch" className="w-full sm:w-auto" variant="secondary" onClick={onLeave} data-testid="leave-dialog-leave">
                {t('chat:leaveWhileGenerating.leave', { defaultValue: '离开' })}
              </Button>
              <Button size="touch" className="w-full sm:w-auto" variant="primary" onClick={onStay} data-leave-dialog-stay="" data-testid="leave-dialog-stay">
                {roundEnded
                  ? t('chat:leaveWhileGenerating.stayEnded', { defaultValue: '留在这里' })
                  : t('chat:leaveWhileGenerating.stay', { defaultValue: '继续等' })}
              </Button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

type Translate = ReturnType<typeof useTranslation>['t'];

/** A new chat stops this round first (charged once the AI has started working; refunded only before model start). */
function newChatMessage(
  t: Translate,
  { roundEnded, producedOutput, showDailyCount }: { roundEnded: boolean; producedOutput: boolean; showDailyCount: boolean },
): string {
  if (roundEnded) {
    return t('chat:newChatWhileGenerating.endedMessage', { defaultValue: '现在开始新对话不会中断任何内容。' });
  }
  if (producedOutput) {
    return showDailyCount
      ? t('chat:newChatWhileGenerating.messageWithOutput', {
        defaultValue: '开始新对话会先停止这一轮。已经写出的内容会保存，这一轮计入今日 AI 消息。',
      })
      : t('chat:newChatWhileGenerating.messageWithOutputUnlimited', {
        defaultValue: '开始新对话会先停止这一轮，已经写出的内容会保存。',
      });
  }
  return showDailyCount
    ? t('chat:newChatWhileGenerating.message', {
      defaultValue: '开始新对话会先停止这一轮；AI 已开始处理时，这一轮仍计入今日 AI 消息。',
    })
    : t('chat:newChatWhileGenerating.messageUnlimited', { defaultValue: '开始新对话会先停止这一轮。' });
}
