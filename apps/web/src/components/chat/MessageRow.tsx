/**
 * Renders a single message row in the chat conversation list.
 *
 * @module components/chat/MessageRow
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Check, Copy, ThumbsDown, ThumbsUp } from 'lucide-react';
import { toast } from '../../lib/toast';
import { LazyMarkdown } from '../LazyMarkdown';
import { ToolResultCard } from '../ToolResultCard';
import { stripThinkTags } from '../../lib/utils';
import { useMobileLayout } from '../../contexts/MobileLayoutContext';
import { RoundEndNote } from '../RoundEndNote';
import {
  CHAT_CARD_PRIMARY_BUTTON,
  CHAT_CARD_SECONDARY_BUTTON,
} from '../chatCardStyles';
import { OrderedMessageItems } from './OrderedMessageItems';
import { ReadOnlyToolGroup } from './ReadOnlyToolGroup';
import {
  IterationExhaustedCard,
  UserStoppedNote,
  WorkflowStoppedCard,
} from './WorkflowStatusCards';
import {
  canPassOpenFileForToolCall,
  extractCopyableText,
  groupToolCallsForDisplay,
  toolCardPayload,
  visibleToolCalls,
} from './messageHelpers';
import type { RowDataProps } from './messageTypes';

/**
 * Renders a single message row in the conversation list.
 * Handles user/assistant message bubbles, thinking content,
 * tool calls/results, status cards, round-end delivery receipts,
 * copy/feedback actions, and timestamps.
 *
 * Consecutive messages from the same role are grouped together with
 * the timestamp only shown on the last message in a role group.
 */
export const MessageRow = React.memo(function MessageRow({
  message,
  previousRole,
  nextRole,
  formatTime,
  onUndo,
  onRestoreDeletion,
  onOpenFile,
  onOpenAIMemory,
  onSubmitFeedback,
  isFeedbackPending,
  showDailyCount = true,
  onIterationAssistAction,
  nextStepsBlocked = false,
}: RowDataProps): React.ReactElement {
  const { t } = useTranslation(['chat']);
  const { isMobile, switchToEditor } = useMobileLayout();
  // Phones keep the chat in front while the AI writes; the latest round that wrote files offers
  // the manuscript and opens the latest written file (even after a reload or device switch).
  const showReadWritten = isMobile
    && nextRole === undefined
    && message.role === 'assistant'
    && message.stopOutcome?.reason === 'completed'
    && message.stopOutcome.writtenFiles.length > 0;
  const readWrittenTarget = showReadWritten
    ? [...(message.stopOutcome?.writtenFiles ?? [])]
      .reverse()
      .find((file): file is typeof file & { id: string } => Boolean(file.id))
    : undefined;
  const displayContent =
    message.role === 'assistant' ? stripThinkTags(message.content) : message.content;
  const hasDisplayContent = Boolean(displayContent && displayContent.trim());
  const quietIncompleteCard = message.stopOutcome?.reason === 'incomplete'
    ? message.displayItems?.find((item) => item.type === 'iteration_exhausted' && item.layer === 'empty_file_correction')
      ?? message.statusCards?.find((card) => card.type === 'iteration_exhausted' && card.layer === 'empty_file_correction')
    : undefined;
  // A round that dropped (the author left or lost the connection) after files were saved: the chat may
  // show little or nothing of it, so the receipt offers the saved files directly (at most three).
  const deliveredFiles = message.role === 'assistant'
    && message.stopOutcome?.reason === 'client_disconnected'
    && message.stopOutcome.savedOutput
    && onOpenFile
    ? message.stopOutcome.writtenFiles
      .filter((file): file is typeof file & { id: string } => Boolean(file.id))
      .slice(0, 3)
    : [];
  const orderedDisplayItems = quietIncompleteCard
    ? message.displayItems?.filter((item) => item !== quietIncompleteCard)
    : message.displayItems;
  const hasDisplayItems = message.role === 'assistant' && Boolean(orderedDisplayItems?.length);
  const hasVisibleIterationExhaustedCard = message.role === 'assistant' && (
    hasDisplayItems
      ? Boolean(orderedDisplayItems?.some((item) => item.type === 'iteration_exhausted'))
      : Boolean(message.statusCards?.some(
          (card) => card.type === 'iteration_exhausted'
            && !(message.stopOutcome?.reason === 'incomplete' && card.layer === 'empty_file_correction'),
        ))
  );
  const toolCalls = useMemo(() => visibleToolCalls(message.toolCalls), [message.toolCalls]);
  const toolResults = useMemo(() => visibleToolCalls(message.toolResults), [message.toolResults]);
  const shouldShowFeedbackActions =
    message.role === 'assistant' && Boolean(onSubmitFeedback) && !message.feedbackUnavailable;
  const canSubmitFeedback = shouldShowFeedbackActions && Boolean(message.backendMessageId);
  const selectedFeedbackVote = message.feedback?.vote;
  const isFeedbackDisabled = isFeedbackPending || !canSubmitFeedback;

  const copyableText = useMemo(
    () => extractCopyableText(message.role, hasDisplayItems, orderedDisplayItems, displayContent),
    [message.role, hasDisplayItems, orderedDisplayItems, displayContent],
  );

  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  const handleCopyReply = useCallback(async () => {
    if (!copyableText) return;
    try {
      await navigator.clipboard.writeText(copyableText);
      setCopied(true);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => {
        copyTimerRef.current = null;
        setCopied(false);
      }, 2000);
    } catch {
      toast.error(t('copyFailed', { ns: 'common', defaultValue: '复制失败，请手动复制' }));
    }
  }, [copyableText, t]);

  // Check if this is a consecutive message from the same role
  const isConsecutiveSameRole = previousRole === message.role;
  const isLastInRoleGroup = nextRole !== message.role;

  // Always show timestamp for the last message in a role group, or for standalone messages
  const shouldShowTimestamp = isLastInRoleGroup;
  const isUser = message.role === 'user';
  const hasAssistantFooterActions = !isUser && (shouldShowFeedbackActions || Boolean(copyableText));
  const hasActiveFooterState = copied || Boolean(selectedFeedbackVote) || (shouldShowFeedbackActions && !canSubmitFeedback);

  const rowPaddingClass = isUser
    ? isConsecutiveSameRole
      ? 'pt-1 pb-1.5'
      : previousRole
        ? (isMobile ? 'pt-3.5 pb-1.5' : 'pt-4 pb-1.5')
        : 'pt-1 pb-1.5'
    : isConsecutiveSameRole
      ? 'pt-1 pb-2.5'
      : (isMobile ? 'pt-1.5 pb-2.5' : 'pt-2 pb-3');

  return (
    <div className={`group/msg flex flex-col ${isUser ? 'items-end' : 'items-stretch'} ${rowPaddingClass}`}>
      {/* Content */}
      <div className={isUser ? 'max-w-[88%] min-w-0' : 'w-full min-w-0 overflow-hidden'}>
        {hasDisplayItems ? (
          <OrderedMessageItems
            items={orderedDisplayItems!}
            onUndo={onUndo}
            onRestoreDeletion={onRestoreDeletion}
            onOpenFile={onOpenFile}
            onOpenAIMemory={onOpenAIMemory}
            onIterationAssistAction={onIterationAssistAction}
            nextStepsBlocked={nextStepsBlocked}
            isStreaming={false}
            chatMessageId={message.backendMessageId}
          />
        ) : (
          hasDisplayContent && (
            <div className={isUser ? 'flex justify-end' : 'w-full'}>
              <div
                className={
                  isUser
                    ? `chat-message-bubble chat-message-bubble-user inline-block max-w-full text-sm ${
                        isMobile ? 'px-3.5 py-2' : 'px-3.5 py-2.5'
                      }`
                    : 'chat-message-bubble-ai w-full max-w-full text-sm'
                }
              >
                {message.role === 'assistant' ? (
                  <div className="markdown-content">
                    <LazyMarkdown>{displayContent}</LazyMarkdown>
                  </div>
                ) : (
                  <div className="whitespace-pre-wrap">
                    {displayContent}
                  </div>
                )}
              </div>
            </div>
          )
        )}

        {/* Tool calls */}
        {message.role === 'assistant' && !hasDisplayItems && toolCalls.length > 0 && (
          <div className={hasDisplayContent ? 'mt-2.5 space-y-2' : 'space-y-2'}>
            {toolCalls.map((toolCall, idx) => (
              <ToolResultCard
                key={idx}
                type={toolCall.status === 'pending' ? 'tool_call' : 'tool_result'}
                toolName={toolCall.tool_name}
                result={toolCardPayload(toolCall)}
                error={toolCall.status === 'error' ? toolCall.error : undefined}
                isPending={false}
                interrupted={toolCall.status === 'pending'}
                onUndo={onUndo}
                onRestoreDeletion={onRestoreDeletion}
                toolUseId={toolCall.id}
                chatMessageId={message.backendMessageId}
                onOpenFile={canPassOpenFileForToolCall(toolCall) ? onOpenFile : undefined}
                onOpenAIMemory={onOpenAIMemory}
              />
            ))}
          </div>
        )}

        {/* Tool results */}
        {message.role === 'assistant' && !hasDisplayItems && toolResults.length > 0 && (
          <div className="mt-2.5 space-y-2">
            {groupToolCallsForDisplay(toolResults, `${message.id}-res`).map((run) =>
              run.kind === 'readonly_group' ? (
                <ReadOnlyToolGroup
                  key={run.key}
                  calls={run.calls}
                  isStreaming={false}
                  onUndo={onUndo}
                  onRestoreDeletion={onRestoreDeletion}
                  chatMessageId={message.backendMessageId}
                  onOpenFile={onOpenFile}
                  onOpenAIMemory={onOpenAIMemory}
                />
              ) : (
                <ToolResultCard
                  key={run.key}
                  type="tool_result"
                  toolName={run.call.tool_name}
                  result={toolCardPayload(run.call)}
                  error={run.call.status === 'error' ? run.call.error : undefined}
                  onUndo={onUndo}
                  onRestoreDeletion={onRestoreDeletion}
                  toolUseId={run.call.id}
                  chatMessageId={message.backendMessageId}
                  onOpenFile={
                    run.call.status === 'success' || canPassOpenFileForToolCall(run.call)
                      ? onOpenFile
                      : undefined
                  }
                  onOpenAIMemory={onOpenAIMemory}
                />
              ),
            )}
          </div>
        )}

        {/* Status cards (e.g. clarification needed / iteration exhausted) */}
        {message.role === 'assistant' && !hasDisplayItems && message.statusCards && message.statusCards.length > 0 && (
          <div className="mt-2.5 space-y-2">
            {message.statusCards.map((card, idx) => {
              if (card.type === 'workflow_stopped' && card.reason === 'user_stopped') {
                return <UserStoppedNote key={`status-${idx}`} />;
              }
              if (card.type === 'workflow_stopped') {
                return (
                  <div key={`status-${idx}`} className="max-w-full">
                    <WorkflowStoppedCard stop={card} />
                  </div>
                );
              }

              if (card.type === 'iteration_exhausted') {
                if (message.stopOutcome?.reason === 'incomplete' && card.layer === 'empty_file_correction') return null;
                // 暂停（不是失败）：中性样式。
                return (
                  <div key={`status-${idx}`} className="max-w-full">
                    <IterationExhaustedCard card={card} onIterationAssistAction={onIterationAssistAction} nextStepsBlocked={nextStepsBlocked} />
                  </div>
                );
              }

              return null;
            })}
          </div>
        )}

        {/* Round-end delivery receipt (+ compact mobile jump-to-editor pill), chronologically after tool cards */}
        {message.role === 'assistant' && message.stopOutcome && (
          <>
            <div className="mt-2.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5">
              <RoundEndNote
                outcome={message.stopOutcome}
                showDailyCount={showDailyCount}
                nextStepsBlocked={nextStepsBlocked}
                pauseCardShown={hasVisibleIterationExhaustedCard}
                className="min-w-0"
              />
              {showReadWritten && (
                <button
                  type="button"
                  data-testid="read-written-files"
                  onClick={() => {
                    if (readWrittenTarget && onOpenFile) {
                      onOpenFile(readWrittenTarget.id, '', readWrittenTarget.title);
                    }
                    switchToEditor();
                  }}
                  className="relative inline-flex shrink-0 items-center gap-1 h-8 rounded-full border border-[hsl(var(--accent-primary)/0.28)] bg-[hsl(var(--accent-primary)/0.12)] px-3 text-xs font-medium text-[hsl(var(--info-text))] hover:bg-[hsl(var(--accent-primary)/0.2)] transition-colors active:scale-[0.97] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-rail))] before:content-[''] before:absolute before:-inset-y-1.5 before:inset-x-0"
                >
                  <span>{t('chat:panel.readWrittenFiles')}</span>
                  <ArrowRight size={13} className="shrink-0" aria-hidden="true" />
                </button>
              )}
            </div>
            {quietIncompleteCard && !nextStepsBlocked && (
              <div className="mt-2">
                <button
                  type="button"
                  className={CHAT_CARD_PRIMARY_BUTTON}
                  onClick={() => onIterationAssistAction?.('continue', quietIncompleteCard)}
                >
                  {t('workflow.actionContinue', { ns: 'chat' })}
                </button>
              </div>
            )}
            {deliveredFiles.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2" data-testid="round-delivery-receipt">
                {deliveredFiles.map((file) => (
                  <button
                    key={file.id}
                    type="button"
                    className={`max-w-full text-left break-words ${CHAT_CARD_SECONDARY_BUTTON}`}
                    onClick={() => onOpenFile?.(file.id, '', file.title)}
                  >
                    {t('panel.openWrittenFile', { ns: 'chat', title: file.title })}
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {/* Footer: borderless ghost actions + muted timestamp on one compact row */}
        {(hasAssistantFooterActions || shouldShowTimestamp) && (
          <div
            className={`flex items-center gap-2 ${
              isUser
                ? 'justify-end mt-1 px-0.5'
                : hasAssistantFooterActions
                  ? 'justify-between mt-1.5'
                  : 'justify-start mt-1.5'
            }`}
          >
            {hasAssistantFooterActions && (
              <div
                className={`flex items-center gap-0.5 transition-opacity duration-[var(--duration-instant)] ease-[var(--ease-standard)] ${
                  hasActiveFooterState
                    ? 'opacity-100'
                    : 'opacity-100 md:opacity-50 md:group-hover/msg:opacity-100 md:group-focus-within/msg:opacity-100'
                }`}
              >
                {copyableText && (
                  <>
                    <button
                      type="button"
                      data-testid="copy-message-button"
                      onClick={() => { void handleCopyReply(); }}
                      aria-label={copied
                        ? t('message.copiedReply', { ns: 'chat', defaultValue: '已复制' })
                        : t('message.copyReply', { ns: 'chat', defaultValue: '复制回复' })}
                      title={copied
                        ? t('message.copiedReply', { ns: 'chat', defaultValue: '已复制' })
                        : t('message.copyReply', { ns: 'chat', defaultValue: '复制回复' })}
                      className={`inline-flex items-center justify-center rounded-md border transition-[transform,background-color,border-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-95 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary)/0.6)] w-11 h-11 md:w-7 md:h-7 ${
                        copied
                          ? 'border-[hsl(var(--success)/0.35)] bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]'
                          : 'border-transparent bg-transparent text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]'
                      }`}
                    >
                      {copied ? (
                        <Check key="copied" size={14} className="motion-icon-pop" />
                      ) : (
                        <Copy key="copy" size={14} />
                      )}
                    </button>
                    <span role="status" aria-live="polite" className="sr-only">
                      {copied ? t('message.copiedReply', { ns: 'chat', defaultValue: '已复制' }) : ''}
                    </span>
                  </>
                )}
                {shouldShowFeedbackActions && (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        if (!canSubmitFeedback) return;
                        void onSubmitFeedback?.(message, 'up');
                      }}
                      disabled={isFeedbackDisabled}
                      aria-pressed={selectedFeedbackVote === 'up'}
                      aria-label={t('feedback.like', { ns: 'chat' })}
                      title={!canSubmitFeedback
                        ? t('feedback.pendingSave', { ns: 'chat' })
                        : t('feedback.like', { ns: 'chat' })}
                      className={`inline-flex items-center justify-center rounded-md border transition-[transform,background-color,border-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-95 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary)/0.6)] w-11 h-11 md:w-7 md:h-7 ${
                        selectedFeedbackVote === 'up'
                          ? 'border-[hsl(var(--success)/0.35)] bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]'
                          : 'border-transparent bg-transparent text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]'
                      } ${isFeedbackDisabled ? 'cursor-not-allowed opacity-55' : ''}`}
                    >
                      <ThumbsUp key={selectedFeedbackVote === 'up' ? 'up-active' : 'up'} size={14} className={selectedFeedbackVote === 'up' ? 'motion-icon-pop' : undefined} />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (!canSubmitFeedback) return;
                        void onSubmitFeedback?.(message, 'down');
                      }}
                      disabled={isFeedbackDisabled}
                      aria-pressed={selectedFeedbackVote === 'down'}
                      aria-label={t('feedback.dislike', { ns: 'chat' })}
                      title={!canSubmitFeedback
                        ? t('feedback.pendingSave', { ns: 'chat' })
                        : t('feedback.dislike', { ns: 'chat' })}
                      className={`inline-flex items-center justify-center rounded-md border transition-[transform,background-color,border-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-95 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary)/0.6)] w-11 h-11 md:w-7 md:h-7 ${
                        selectedFeedbackVote === 'down'
                          ? 'border-[hsl(var(--warning)/0.4)] bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning-text))]'
                          : 'border-transparent bg-transparent text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]'
                      } ${isFeedbackDisabled ? 'cursor-not-allowed opacity-55' : ''}`}
                    >
                      <ThumbsDown key={selectedFeedbackVote === 'down' ? 'down-active' : 'down'} size={14} className={selectedFeedbackVote === 'down' ? 'motion-icon-pop' : undefined} />
                    </button>
                    {!canSubmitFeedback && (
                      <span className="ml-1 text-xs text-[hsl(var(--text-secondary))]">
                        {t('feedback.pendingSave', { ns: 'chat' })}
                      </span>
                    )}
                  </>
                )}
              </div>
            )}
            {shouldShowTimestamp && (
              <div className="chat-timestamp shrink-0">
                {formatTime(message.timestamp)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
});
