/**
 * @fileoverview MessageList component - Chat message display for AI conversations.
 *
 * Thin container shell composing MessageRow (saved messages) and
 * OrderedMessageItems (live streaming timeline), preserving scrollToBottom
 * ref imperative handle and custom React.memo comparison.
 *
 * @module components/MessageList
 */

import React, { useMemo, useRef, forwardRef, useImperativeHandle, useCallback } from 'react';
import { getLocaleCode } from '../lib/i18n-helpers';
import { useMobileLayout } from '../contexts/MobileLayoutContext';
import { MessageRow } from './chat/MessageRow';
import { OrderedMessageItems } from './chat/OrderedMessageItems';
import { hasRenderableDisplayItem, isMessageVisible } from './chat/messageHelpers';
import type { MessageListProps, MessageListRef } from './chat/messageTypes';

export type {
  MessageFeedbackVote,
  MessageFeedback,
  MessageStatusCard,
  Message,
  MessageListRef,
} from './chat/messageTypes';

/**
 * Main chat message list component that displays conversation history.
 *
 * Uses React.memo with custom comparison for optimized re-rendering
 * during streaming updates.
 */
export const MessageList = React.memo(
  forwardRef<MessageListRef, MessageListProps>(({
    messages,
    onUndo,
    onRestoreDeletion,
    onOpenFile,
    onOpenAIMemory,
    onSubmitFeedback,
    feedbackPendingMessageId,
    showDailyCount,
    streamRenderItems,
    onIterationAssistAction,
    nextStepsBlocked,
    scrollContainerRef: externalScrollContainerRef,
  }, ref) => {
    const { isMobile } = useMobileLayout();

    // Internal scroll container ref (used if external ref not provided)
    const internalScrollContainerRef = useRef<HTMLDivElement>(null);

    // Use external ref if provided, otherwise use internal ref
    const scrollContainerRef = externalScrollContainerRef || internalScrollContainerRef;

    // Filter out completely invisible messages (e.g. empty assistant content segments)
    // so we don't render ugly empty rows/bubbles.
    const visibleMessages = useMemo(() => messages.filter(isMessageVisible), [messages]);

    /**
     * Scrolls to the bottom of the message list.
     * Uses direct container scrolling to avoid virtualization measurement jitter.
     */
    const scrollToBottom = useCallback((smooth: boolean = true) => {
      const scrollElement = scrollContainerRef.current;
      if (!scrollElement) return;

      requestAnimationFrame(() => {
        if (scrollElement) {
          scrollElement.scrollTo({
            top: scrollElement.scrollHeight,
            behavior: smooth ? 'smooth' : 'instant',
          });
        }
      });
    }, [scrollContainerRef]);

    // Expose scrollToBottom method via ref
    useImperativeHandle(ref, () => ({
      scrollToBottom,
    }), [scrollToBottom]);

    // 检查是否应该显示 streamRenderItems
    // 因为 onComplete 中立即清空了 streamRenderItems，所以只需要简单检查是否有内容即可
    const safeStreamRenderItems = useMemo(
      () => streamRenderItems ?? [],
      [streamRenderItems],
    );
    const shouldShowStreamItems = useMemo(() => {
      return safeStreamRenderItems.some(hasRenderableDisplayItem);
    }, [safeStreamRenderItems]);

    const formatTime = useMemo(() => {
      const localeCode = getLocaleCode();
      return (date: Date) => {
        return new Intl.DateTimeFormat(localeCode, {
          hour: '2-digit',
          minute: '2-digit',
        }).format(date);
      };
    }, []); // Empty deps since getLocaleCode reads from localStorage which is stable

    // Don't render anything if there are no visible messages and no stream items
    if (visibleMessages.length === 0 && !shouldShowStreamItems) {
      return null;
    }

    return (
      // ph-no-capture: prompts and replies must never reach analytics capture.
      <div className="ph-no-capture" aria-live="polite" aria-atomic="true">
        {visibleMessages.map((message, index) => (
          <MessageRow
            key={message.id}
            message={message}
            previousRole={visibleMessages[index - 1]?.role}
            nextRole={visibleMessages[index + 1]?.role}
            formatTime={formatTime}
            onUndo={onUndo}
            onRestoreDeletion={onRestoreDeletion}
            onOpenFile={onOpenFile}
            onOpenAIMemory={onOpenAIMemory}
            onSubmitFeedback={onSubmitFeedback}
            isFeedbackPending={feedbackPendingMessageId === message.id}
            showDailyCount={showDailyCount}
            onIterationAssistAction={onIterationAssistAction}
            nextStepsBlocked={nextStepsBlocked}
          />
        ))}

        {/* 然后渲染流式内容 - 按服务端顺序直接追加在最后 */}
        {shouldShowStreamItems && (
          <div key="stream-items" className={`flex flex-col ${isMobile ? 'pt-1.5 pb-2.5' : 'pt-2 pb-3'}`}>
            {/* Stream items */}
            <div className="w-full min-w-0 overflow-hidden">
              <OrderedMessageItems
                items={safeStreamRenderItems}
                onUndo={onUndo}
                onRestoreDeletion={onRestoreDeletion}
                onOpenFile={onOpenFile}
                onOpenAIMemory={onOpenAIMemory}
                onIterationAssistAction={onIterationAssistAction}
                nextStepsBlocked={nextStepsBlocked}
                isStreaming={true}
              />

              {/* Timestamp */}
              <div className="chat-timestamp mt-1.5">
                {formatTime(safeStreamRenderItems[safeStreamRenderItems.length - 1].timestamp)}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }),
  (prevProps, nextProps) => {
    // Custom comparison function for React.memo.
    // Prefer cheap reference checks to avoid O(n) message comparisons.
    if (prevProps.onUndo !== nextProps.onUndo) return false;
    if (prevProps.onRestoreDeletion !== nextProps.onRestoreDeletion) return false;
    if (prevProps.onOpenFile !== nextProps.onOpenFile) return false;
    if (prevProps.onOpenAIMemory !== nextProps.onOpenAIMemory) return false;
    if (prevProps.onSubmitFeedback !== nextProps.onSubmitFeedback) return false;
    // The iteration-exhausted action buttons call this; omitting it here let the
    // buttons keep invoking a stale closure after the parent re-created it.
    if (prevProps.onIterationAssistAction !== nextProps.onIterationAssistAction) return false;
    if (prevProps.feedbackPendingMessageId !== nextProps.feedbackPendingMessageId) return false;
    if (prevProps.showDailyCount !== nextProps.showDailyCount) return false;
    if (prevProps.nextStepsBlocked !== nextProps.nextStepsBlocked) return false;
    if (prevProps.messages !== nextProps.messages) return false;
    if (prevProps.streamRenderItems !== nextProps.streamRenderItems) return false;

    return true;
  },
);
