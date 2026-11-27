/**
 * Renders an ordered sequence of live or persisted StreamRenderItems.
 *
 * @module components/chat/OrderedMessageItems
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Loader2, Sparkles } from 'lucide-react';
import type { StreamRenderItem } from '../../hooks/streaming/types';
import { LazyMarkdown } from '../LazyMarkdown';
import { ToolResultCard } from '../ToolResultCard';
import { ThinkingContent } from '../ThinkingContent';
import { HistoryCompactionMarker } from '../HistoryCompactionMarker';
import { stripThinkTags } from '../../lib/utils';
import { getAgentDisplayName } from '../../lib/agentDisplayName';
import { useMobileLayout } from '../../contexts/MobileLayoutContext';
import { CHAT_CARD_SURFACE } from '../chatCardStyles';
import { ContextItemsView } from './ContextItemsView';
import { ReadOnlyToolGroup } from './ReadOnlyToolGroup';
import {
  IterationExhaustedCard,
  UserStoppedNote,
  WorkflowStoppedCard,
} from './WorkflowStatusCards';
import {
  canPassOpenFileForToolCall,
  findLastActiveStreamIndex,
  findUnresolvedEditFailures,
  groupToolCallsForDisplay,
  normalizeOrderedStreamItems,
  toolCardPayload,
  visibleToolCalls,
} from './messageHelpers';
import type { MessageListProps } from './messageTypes';

export interface OrderedMessageItemsProps {
  items: StreamRenderItem[];
  nextStepsBlocked?: boolean;
  onUndo?: MessageListProps['onUndo'];
  onRestoreDeletion?: MessageListProps['onRestoreDeletion'];
  /** The saved assistant message these items belong to (absent while streaming). */
  chatMessageId?: string;
  onOpenFile?: MessageListProps['onOpenFile'];
  onOpenAIMemory?: MessageListProps['onOpenAIMemory'];
  onIterationAssistAction?: MessageListProps['onIterationAssistAction'];
  onRetryEditFailure?: MessageListProps['onRetryEditFailure'];
  isStreaming?: boolean;
}

export function OrderedMessageItems({
  items,
  onUndo,
  onRestoreDeletion,
  onOpenFile,
  onOpenAIMemory,
  onIterationAssistAction,
  onRetryEditFailure,
  nextStepsBlocked = false,
  isStreaming = false,
  chatMessageId,
}: OrderedMessageItemsProps) {
  const { t } = useTranslation(['chat']);
  const { isMobile } = useMobileLayout();
  const translate = t as unknown as (key: string, options?: Record<string, unknown>) => string;
  // Show role names (大纲规划师, 内容创作者...) instead of internal agent ids.
  const agentLabel = (agentType?: string, agentName?: string) =>
    getAgentDisplayName(agentType, translate)
    ?? (agentName && agentName !== agentType ? agentName : null);

  const normalizedItems = useMemo(() => normalizeOrderedStreamItems(items), [items]);
  const unresolvedEditFailures = useMemo(() => {
    const allCalls = normalizedItems.flatMap((item) =>
      item.type === 'tool_calls' && item.toolCalls ? item.toolCalls : [],
    );
    return findUnresolvedEditFailures(allCalls);
  }, [normalizedItems]);

  // Only the last visibly rendered item in an active stream is still live.
  // Earlier thinking blocks stop pulsing as soon as a newer step (tool prep, tool call, prose, or new thinking) starts.
  const lastActiveIndex = findLastActiveStreamIndex(normalizedItems, isStreaming, agentLabel);

  return (
    <>
      {normalizedItems.map((item, index) => {
        if (item.type === 'thinking_status' && item.content) {
          // 1. thinking 事件 - 轻量状态小标签（不透明文字 token）
          // Transient progress pills only render while currently active; superseded ones disappear cleanly.
          const isActiveStatus = isStreaming && index === lastActiveIndex && Boolean(item.transient);
          if (item.transient && !isActiveStatus) return null;
          return (
            <div key={item.id} className="mb-2">
              <div
                data-testid={isActiveStatus ? 'active-thinking-status' : undefined}
                className={`inline-flex max-w-full items-center gap-1.5 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] text-xs ${isMobile ? 'px-2.5 py-0.5' : 'px-2.5 py-1'}`}
              >
                {isActiveStatus && (
                  <Loader2 size={12} aria-hidden="true" className="shrink-0 animate-spin motion-reduce:animate-none" />
                )}
                <span className="min-w-0 break-words tabular-nums">{item.content}</span>
              </div>
            </div>
          );
        } else if (item.type === 'history_compaction' && item.compactionState) {
          // 历史压缩：进行中是带转圈的一步，完成后是会话里可展开摘要的「对话已压缩」分隔标记。
          return (
            <HistoryCompactionMarker
              key={item.id}
              state={item.compactionState}
              summary={item.content}
              startedAt={item.timestamp}
            />
          );
        } else if (item.type === 'thinking_content' && item.content) {
          // 2. thinking_content - 思考过程，可折叠（仅最新且未被后续步骤接管的思考块保持活跃流式态）
          const isActiveThinking = isStreaming && index === lastActiveIndex;
          return (
            <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
              <ThinkingContent content={item.content} isStreaming={isActiveThinking} />
            </div>
          );
        } else if (item.type === 'content' && item.content) {
          // 3. content - AI回复内容，无框排版直接落在面板表面
          const cleanContent = stripThinkTags(item.content);
          if (!cleanContent.trim()) return null;
          return (
            <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
              <div className="w-full max-w-full text-sm text-[hsl(var(--text-primary))]">
                <div className="markdown-content">
                  <LazyMarkdown>{cleanContent}</LazyMarkdown>
                </div>
              </div>
            </div>
          );
        } else if (item.type === 'tool_calls' && item.toolCalls) {
          // 4. tool_calls - 工具调用卡片（连续 >=2 个成功的纯只读探索工具自动收纳，写卡/进行中/错误绝不折叠）
          const toolCalls = visibleToolCalls(item.toolCalls);
          if (toolCalls.length === 0) return null;
          const runs = groupToolCallsForDisplay(toolCalls, item.id);
          return (
            <div key={item.id} className={`${isMobile ? 'mb-2' : 'mb-2.5'} space-y-2`}>
              {runs.map((run) =>
                run.kind === 'readonly_group' ? (
                  <ReadOnlyToolGroup
                    key={run.key}
                    calls={run.calls}
                    isStreaming={isStreaming}
                    onUndo={onUndo}
                    onRestoreDeletion={onRestoreDeletion}
                    chatMessageId={chatMessageId}
                    onOpenFile={onOpenFile}
                    onOpenAIMemory={onOpenAIMemory}
                  />
                ) : (
                  <ToolResultCard
                    key={run.key}
                    type={run.call.status === 'pending' ? 'tool_call' : 'tool_result'}
                    toolName={run.call.tool_name}
                    result={toolCardPayload(run.call)}
                    toolArguments={run.call.arguments}
                    error={run.call.error}
                    // A call still pending in a finished round (stopped / dropped) never got its
                    // result: it did not finish, it is not still running.
                    isPending={run.call.status === 'pending' && isStreaming}
                    interrupted={run.call.status === 'pending' && !isStreaming}
                    isStreaming={isStreaming}
                    nextStepsBlocked={nextStepsBlocked}
                    onUndo={onUndo}
                    onRestoreDeletion={isStreaming ? undefined : onRestoreDeletion}
                    toolUseId={run.call.id}
                    chatMessageId={chatMessageId}
                    onOpenFile={canPassOpenFileForToolCall(run.call) ? onOpenFile : undefined}
                    onOpenAIMemory={onOpenAIMemory}
                    onRetryEditFailure={
                      !isStreaming && !nextStepsBlocked && unresolvedEditFailures.has(run.call)
                        ? onRetryEditFailure
                        : undefined
                    }
                  />
                ),
              )}
            </div>
          );
        } else if (item.type === 'context' && item.items) {
          // 5. context - 引用来源，次要
          return (
            <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
              <ContextItemsView items={item.items} />
            </div>
          );
        } else if (item.type === 'agent_selected' && agentLabel(item.agentType, item.agentName)) {
          // Keep the useful task-role activity label, not internal iteration budgets.
          return (
            <div key={item.id} className="mb-2">
              <div className={`inline-flex items-center rounded-lg ${
                isMobile ? 'gap-1.5 px-2.5 py-0.5' : 'gap-1.5 px-2.5 py-1'
              } bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.2)]`}>
                <Bot size={13} className="text-[hsl(var(--accent-primary))] shrink-0" />
                <span className={`text-xs font-medium text-[hsl(var(--accent-primary))] ${isMobile ? 'truncate max-w-[140px]' : ''}`}>
                  {agentLabel(item.agentType, item.agentName)}
                </span>
              </div>
            </div>
          );
        } else if (item.type === 'iteration_exhausted') {
          // History is saved asynchronously before done. Keep this exact internal
          // card in raw state, but wait for the canonical footer while streaming.
          // If the stream fails, the retained card still offers recovery afterward.
          if (isStreaming && item.layer === 'empty_file_correction') return null;
          // 7. iteration_exhausted - 这一轮的步数/协作轮数用完，或有文件没写成正文：暂停，用中性样式。
          return (
            <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
              <IterationExhaustedCard card={item} onIterationAssistAction={onIterationAssistAction} nextStepsBlocked={nextStepsBlocked} />
            </div>
          );
        } else if (item.type === 'workflow_stopped') {
          const workflowReason = (item as { reason?: string }).reason;
          if (workflowReason === 'user_stopped') {
            return <UserStoppedNote key={item.id} />;
          }
          return (
            <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
              <WorkflowStoppedCard stop={item as { reason?: string; message?: string; question?: string; context?: string; details?: string[] }} />
            </div>
          );
        } else if (item.type === 'workflow_complete') {
          // workflow_complete - 任务完成
          return (
            <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
              <div className={`inline-flex items-center gap-2 ${CHAT_CARD_SURFACE} ${isMobile ? 'px-3 py-2' : 'px-3 py-2'}`}>
                <Sparkles size={14} className="text-[hsl(var(--success-text))] shrink-0" />
                <span className="text-xs font-medium text-[hsl(var(--text-primary))]">
                  {t('workflow.taskCompleted', { ns: 'chat' })}
                </span>
              </div>
            </div>
          );
        }
        return null;
      })}
    </>
  );
}
