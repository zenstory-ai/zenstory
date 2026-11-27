/**
 * Workflow status cards rendered inside live and saved chat message timelines.
 *
 * @module components/chat/WorkflowStatusCards
 */

import { useTranslation } from 'react-i18next';
import { AlertTriangle, Bot, CirclePause } from 'lucide-react';
import { useMobileLayout } from '../../contexts/MobileLayoutContext';
import { isClarificationStop as isClarificationStopItem } from '../../lib/nextStep';
import {
  CHAT_CARD_PRIMARY_BUTTON,
  CHAT_CARD_PROMPT_SURFACE,
  CHAT_CARD_SECONDARY_BUTTON,
  CHAT_CARD_SURFACE,
  CHAT_CARD_WARNING_SURFACE,
} from '../chatCardStyles';
import { iterationExhaustedText } from './messageHelpers';
import type { MessageListProps } from './messageTypes';

/** The author pressed stop: a quiet note, not a warning (nothing went wrong). */
export function UserStoppedNote() {
  const { t } = useTranslation(['chat']);
  return (
    <div className="mb-2 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]" data-testid="user-stopped-note">
      <CirclePause size={14} className="shrink-0" />
      <span>{t('workflow.userStopped', { ns: 'chat' })}</span>
    </div>
  );
}

type IterationExhaustedCardData = Parameters<NonNullable<MessageListProps['onIterationAssistAction']>>[1]
  & Parameters<typeof iterationExhaustedText>[1];

/** A paused round (not a failure): calm card surface; one primary CTA and uniform neutral secondary options. */
export function IterationExhaustedCard({ card, onIterationAssistAction, nextStepsBlocked }: {
  card: IterationExhaustedCardData;
  onIterationAssistAction?: MessageListProps['onIterationAssistAction'];
  nextStepsBlocked: boolean;
}) {
  const { t } = useTranslation(['chat']);
  const { isMobile } = useMobileLayout();
  const exhausted = iterationExhaustedText(t, card, nextStepsBlocked);
  const showNextSteps = !nextStepsBlocked && card.layer !== 'unexecuted_tool_text';
  return (
    <div className={`inline-flex max-w-full items-start gap-2.5 ${CHAT_CARD_SURFACE} ${isMobile ? 'px-3 py-2.5' : 'p-3'}`}>
      <CirclePause size={15} className="text-[hsl(var(--text-secondary))] mt-0.5 shrink-0" />
      <div className="flex flex-col gap-1 min-w-0">
        <span className="text-xs font-semibold leading-5 text-[hsl(var(--text-primary))]">
          {exhausted.title}
        </span>
        {exhausted.lines.map((line) => (
          <span key={line} className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {line}
          </span>
        ))}
        {showNextSteps && (
          <div className={`mt-1.5 flex flex-wrap items-center ${isMobile ? 'gap-1.5' : 'gap-2'}`}>
            <button
              type="button"
              className={CHAT_CARD_PRIMARY_BUTTON}
              onClick={() => onIterationAssistAction?.('continue', card)}
            >
              {t('workflow.actionContinue', { ns: 'chat' })}
            </button>
            <button
              type="button"
              className={CHAT_CARD_SECONDARY_BUTTON}
              onClick={() => onIterationAssistAction?.('split', card)}
            >
              {t('workflow.actionSplit', { ns: 'chat' })}
            </button>
            <button
              type="button"
              className={CHAT_CARD_SECONDARY_BUTTON}
              onClick={() => onIterationAssistAction?.('manual', card)}
            >
              {t('workflow.actionManual', { ns: 'chat' })}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Shared card for clarification requests and workflow-stopped states across live and saved timelines. */
export function WorkflowStoppedCard({ stop }: {
  stop: {
    type?: string;
    reason?: string;
    message?: string;
    question?: string;
    context?: string;
    details?: string[];
  };
}) {
  const { t } = useTranslation(['chat']);
  const { isMobile } = useMobileLayout();
  const workflowQuestion = stop.question?.trim() || stop.message?.trim() || '';
  const workflowContext = stop.context?.trim() || '';
  const workflowDetails = (stop.details ?? []).map((d) => d.trim()).filter(Boolean);
  const isClarificationStop = isClarificationStopItem({ ...stop, type: stop.type ?? 'workflow_stopped' });
  const paddingClass = isMobile ? 'px-3 py-2.5' : 'p-3';

  if (!isClarificationStop) {
    const isErrorStop = stop.reason === 'error';
    const stopMessage = workflowQuestion || t('workflow.stoppedGeneric', { ns: 'chat' });
    return (
      <div className={`inline-flex max-w-full items-start gap-2.5 ${isErrorStop ? CHAT_CARD_WARNING_SURFACE : CHAT_CARD_SURFACE} ${paddingClass}`}>
        {isErrorStop ? (
          <AlertTriangle size={15} className="text-[hsl(var(--warning-text))] mt-0.5 shrink-0" />
        ) : (
          <CirclePause size={15} className="text-[hsl(var(--text-secondary))] mt-0.5 shrink-0" />
        )}
        <div className="flex flex-col gap-1 min-w-0">
          <span className={`text-xs font-semibold leading-5 ${isErrorStop ? 'text-[hsl(var(--warning-text))]' : 'text-[hsl(var(--text-primary))]'}`}>
            {t('workflow.stoppedTitle', { ns: 'chat' })}
          </span>
          <span className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {stopMessage}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={`inline-flex max-w-full items-start gap-2.5 ${CHAT_CARD_PROMPT_SURFACE} ${paddingClass}`}>
      <Bot size={15} className="text-[hsl(var(--accent-primary))] mt-0.5 shrink-0" />
      <div className="flex flex-col gap-1 min-w-0">
        <span className="text-xs font-semibold leading-5 text-[hsl(var(--text-primary))]">
          {t('workflow.waitingForReply', { ns: 'chat' })}
        </span>
        {workflowQuestion ? (
          <span className="text-xs leading-5 text-[hsl(var(--text-primary))] break-words">
            {workflowQuestion}
          </span>
        ) : (
          <span className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {t('workflow.needsConfirmation', { ns: 'chat' })}
          </span>
        )}
        {workflowContext ? (
          <span className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {workflowContext}
          </span>
        ) : null}
        {workflowDetails.length > 0 ? (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs leading-5 text-[hsl(var(--text-primary))]">
            {workflowDetails.map((detail, detailIdx) => (
              <li key={detailIdx} className="break-words">
                {detail}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
