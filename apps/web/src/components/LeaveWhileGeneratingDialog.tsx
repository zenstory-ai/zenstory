import React, { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle } from 'lucide-react';
import { Button } from './ui/Button';
import { useDialogInteractions } from './ui/dialogFocus';

export interface LeaveWhileGeneratingDialogProps {
  open: boolean;
  /** The round finished while the dialog was open: leaving no longer cuts anything off. */
  roundEnded: boolean;
  /** This round already wrote something (prose or a file): leaving keeps it and is charged. */
  producedOutput: boolean;
  /** Free plan: mention whether the round counts toward today's AI messages (Pro: never). */
  showDailyCount: boolean;
  onStay: () => void;
  onLeave: () => void;
}

/**
 * Asked before leaving the workbench (in-app link, Back, system back) mid-round.
 * Staying is the primary action; leaving is the quiet secondary one, because it ends the round.
 */
export function LeaveWhileGeneratingDialog({
  open,
  roundEnded,
  producedOutput,
  showDailyCount,
  onStay,
  onLeave,
}: LeaveWhileGeneratingDialogProps) {
  const { t } = useTranslation(['chat']);
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

  const title = roundEnded
    ? t('chat:leaveWhileGenerating.endedTitle', { defaultValue: '这一轮已经结束' })
    : t('chat:leaveWhileGenerating.title', { defaultValue: 'AI 还在写' });
  let message: string;
  if (roundEnded) {
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
        defaultValue: '离开这个页面会中断这一轮。还没写出内容的话，这一轮不计入今日 AI 消息。',
      })
      : t('chat:leaveWhileGenerating.messageUnlimited', { defaultValue: '离开这个页面会中断这一轮。' });
  }

  return createPortal(
    <div className="modal-overlay flex items-center justify-center p-4" onClick={onStay} role="presentation">
      <div
        ref={dialogRef}
        className="modal w-full max-w-[400px] animate-scale-in"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        data-testid="leave-while-generating-dialog"
      >
        <div className="flex items-start gap-3 mb-4">
          <div className="flex-shrink-0 text-[hsl(var(--warning))]">
            <AlertCircle className="w-10 h-10" />
          </div>
          <div className="flex-1 pt-1">
            <h2 id={titleId} className="text-lg font-semibold text-[hsl(var(--text-primary))]">
              {title}
            </h2>
          </div>
        </div>
        <div className="mb-6">
          <p id={descriptionId} className="text-sm text-[hsl(var(--text-secondary))] leading-relaxed">
            {message}
          </p>
        </div>
        <div className="flex justify-end gap-3">
          <Button variant="secondary" onClick={onLeave} data-testid="leave-dialog-leave">
            {t('chat:leaveWhileGenerating.leave', { defaultValue: '离开' })}
          </Button>
          <Button variant="primary" onClick={onStay} data-leave-dialog-stay="" data-testid="leave-dialog-stay">
            {roundEnded
              ? t('chat:leaveWhileGenerating.stayEnded', { defaultValue: '留在这里' })
              : t('chat:leaveWhileGenerating.stay', { defaultValue: '继续等' })}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
