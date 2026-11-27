/**
 * Pure filtering, grouping, and formatting helpers for chat message timelines.
 *
 * @module components/chat/messageHelpers
 */

import type { TFunction } from 'i18next';
import type { BadgeProps } from '../ui/Badge';
import type { StreamRenderItem } from '../../hooks/streaming/types';
import type { AgentContextItem, IterationExhaustedLayer, ToolCall } from '../../types';
import { stripThinkTags } from '../../lib/utils';
import { exhaustionLeftFilesUnfinished } from '../../lib/chatRoundEnd';
import { isInternalOrderingRefusal } from '../../lib/agentRoundProgress';
import type { Message } from './messageTypes';

/**
 * Auto-injected open editor tab (not explicitly attached or quoted by the user).
 * Hidden from the chat context card so the active editor tab is not mistaken for
 * AI retrieval results or explicit attachments.
 */
export const isAutoFocusOnlyContextItem = (item: AgentContextItem): boolean => {
  const meta = item.metadata;
  if (!meta || meta.is_focus !== true) return false;
  if (meta.attached === true || meta.library_material === true || meta.relation === 'attached') {
    return false;
  }
  if (item.type === 'quote' || meta.is_quote === true) return false;
  return true;
};

export const visibleContextItems = (items: AgentContextItem[] | undefined): AgentContextItem[] =>
  (items ?? []).filter((item) => !isAutoFocusOnlyContextItem(item));

/**
 * Returns the calm origin badge label and shared semantic variant for a context item.
 * Never uses red `error` styling for routine context items.
 */
export const contextOriginBadge = (
  item: AgentContextItem,
  t: TFunction,
): { label: string; variant: BadgeProps['variant'] } => {
  const meta = item.metadata;
  if (item.type === 'quote' || meta?.is_quote === true) {
    return { label: t('context.origin.quote', { ns: 'chat' }), variant: 'neutral' };
  }
  if (meta?.attached === true || meta?.library_material === true || meta?.relation === 'attached') {
    return { label: t('context.origin.attached', { ns: 'chat' }), variant: 'neutral' };
  }
  if (meta?.is_focus === true) {
    return { label: t('context.origin.openFile', { ns: 'chat' }), variant: 'neutral' };
  }
  switch (item.priority) {
    case 'constraint':
      return { label: t('context.priority.constraint', { ns: 'chat' }), variant: 'info' };
    case 'relevant':
      return { label: t('context.priority.relevant', { ns: 'chat' }), variant: 'success' };
    case 'inspiration':
      return { label: t('context.priority.inspiration', { ns: 'chat' }), variant: 'purple' };
    case 'critical':
    default:
      return { label: t('context.origin.attached', { ns: 'chat' }), variant: 'neutral' };
  }
};

/**
 * Returns a localized label for a context item type.
 */
export const contextTypeLabel = (item: AgentContextItem, t: TFunction): string => {
  // 组装器把正文/剧本/文档都装成 type="outline" 的条目，真实类型在 metadata.file_type。
  const fileType = item.metadata?.file_type;
  const type = typeof fileType === 'string' && fileType ? fileType : item.type;
  return t(`fileType.${type}`, { ns: 'chat', defaultValue: t('fileType.default', { ns: 'chat' }) });
};

/**
 * 用户引用的条目标题由后端拼成「引用自: 文件名」，只在中文界面成立；
 * 有原文件名时直接显示文件名，类型徽标已经写明「引用」。
 */
export const contextItemTitle = (item: AgentContextItem): string => {
  const fileTitle = item.metadata?.file_title;
  if (item.type === 'quote' && typeof fileTitle === 'string' && fileTitle.trim()) {
    return fileTitle.trim();
  }
  return item.title || item.id;
};

/**
 * What an iteration_exhausted card says. A round that stopped with a file that never got its text
 * (empty_file_correction, or a tool-turn stop that names such files) names the files left
 * unfinished and the empty ones removed, instead of the 「任务比较大」 summary.
 */
export function iterationExhaustedText(
  t: TFunction,
  card: { layer?: IterationExhaustedLayer; limit?: string; unfinishedFiles?: string[]; rolledBackFiles?: string[] },
  nextStepsBlocked: boolean,
): { title: string; lines: string[] } {
  // A step only written out as text: say it did not run; nothing to resume, so no continue wording.
  if (card.layer === 'unexecuted_tool_text') {
    return {
      title: t('workflow.unexecutedToolText', { ns: 'chat' }),
      lines: [t('workflow.unexecutedToolTextSummary', { ns: 'chat' })],
    };
  }
  // Used-up day: the same facts without 「点继续完成」 (the quota card says when sending comes back).
  const paused = nextStepsBlocked ? 'Paused' : '';
  if (exhaustionLeftFilesUnfinished(card)) {
    const joiner = t('panel.removedFilesJoiner', { ns: 'chat' });
    const lines = [
      card.unfinishedFiles?.length
        ? t(`workflow.emptyFileUnresolved${paused}`, { ns: 'chat', titles: card.unfinishedFiles.join(joiner) })
        : null,
      card.rolledBackFiles?.length
        ? t(`workflow.emptyFileRolledBack${paused}`, { ns: 'chat', titles: card.rolledBackFiles.join(joiner) })
        : null,
    ].filter((line): line is string => Boolean(line));
    return {
      title: t('workflow.emptyFileUnfinished', { ns: 'chat' }),
      lines: lines.length > 0 ? lines : [t(`workflow.emptyFileUnfinishedSummary${paused}`, { ns: 'chat' })],
    };
  }
  const summary = card.limit === 'wall_clock'
    ? t(`workflow.timeLimitSummary${paused}`, { ns: 'chat' })
    : nextStepsBlocked
      ? t('workflow.stepLimitSummaryPaused', { ns: 'chat' })
      : card.layer === 'collaboration'
        ? t('workflow.collaborationExhaustedSummary', { ns: 'chat' })
        : t('workflow.toolCallExhaustedSummary', { ns: 'chat' });
  return { title: t('workflow.iterationExhausted', { ns: 'chat' }), lines: [summary] };
}

/**
 * handoff_to_agent / request_clarification 是流程控制工具：交接已经有
 * thinking_status / agent_selected 提示，澄清已经有 workflow_stopped 提问卡片，
 * 再画一张「handoff_to_agent 完成」的工具卡片只会把内部名字露给作者。
 */
const CONTROL_FLOW_TOOL_NAMES = new Set(['handoff_to_agent', 'request_clarification']);
const READ_ONLY_TOOL_NAMES = new Set([
  'query_files',
  'read_files',
  'search_files',
  'hybrid_search',
  'load_skill',
  'read_skill_resource',
]);

const isCollapsibleReadOnlyToolCall = (call: ToolCall): boolean =>
  READ_ONLY_TOOL_NAMES.has(call.tool_name) && call.status === 'success' && !call.error;

export type ToolCallRun =
  | { kind: 'single'; call: ToolCall; key: string }
  | { kind: 'readonly_group'; calls: ToolCall[]; key: string };

export const groupToolCallsForDisplay = (calls: ToolCall[], keyPrefix: string): ToolCallRun[] => {
  const runs: ToolCallRun[] = [];
  let pendingReadOnly: ToolCall[] = [];
  let runStartIdx = 0;

  const flushReadOnly = () => {
    if (pendingReadOnly.length === 0) return;
    if (pendingReadOnly.length >= 2) {
      runs.push({
        kind: 'readonly_group',
        calls: pendingReadOnly,
        key: `${keyPrefix}-ro-${runStartIdx}`,
      });
    } else {
      runs.push({
        kind: 'single',
        call: pendingReadOnly[0],
        key: `${keyPrefix}-${runStartIdx}`,
      });
    }
    pendingReadOnly = [];
  };

  calls.forEach((call, idx) => {
    if (isCollapsibleReadOnlyToolCall(call)) {
      if (pendingReadOnly.length === 0) runStartIdx = idx;
      pendingReadOnly.push(call);
    } else {
      flushReadOnly();
      runs.push({ kind: 'single', call, key: `${keyPrefix}-${idx}` });
    }
  });
  flushReadOnly();
  return runs;
};

const isGuardSkippedResult = (result: Record<string, unknown> | undefined): boolean => {
  if (!result) return false;
  if (result.guard_skipped === true) return true;
  const nested = result.data && typeof result.data === 'object'
    ? (result.data as Record<string, unknown>)
    : undefined;
  return nested?.guard_skipped === true;
};

/**
 * The cards the author sees: no control-flow calls, no internal ordering/repeat-edit refusals,
 * and no guard-skipped no-op calls (the server steered the model without mutating the author's
 * work, regardless of whether the persisted tool status is `error` or `success`).
 */
export const visibleToolCalls = (calls: ToolCall[] | undefined): ToolCall[] =>
  (calls ?? []).filter((call) => {
    if (CONTROL_FLOW_TOOL_NAMES.has(call.tool_name)) return false;
    const result = call.result && typeof call.result === 'object'
      ? (call.result as Record<string, unknown>)
      : undefined;
    return !isInternalOrderingRefusal(result) && !isGuardSkippedResult(result);
  });

export const toolCardPayload = (tc: { result?: unknown; arguments?: unknown }): Record<string, unknown> =>
  tc.result && typeof tc.result === 'object'
    ? (tc.result as Record<string, unknown>)
    : tc.arguments && typeof tc.arguments === 'object'
      ? (tc.arguments as Record<string, unknown>)
      : {};

export const hasRenderableDisplayItem = (item: StreamRenderItem): boolean => {
  if (item.type === 'content') {
    return Boolean(item.content && stripThinkTags(item.content).trim());
  }
  if (item.type === 'tool_calls') {
    return visibleToolCalls(item.toolCalls).length > 0;
  }
  if (item.type === 'context') {
    return visibleContextItems(item.items).length > 0;
  }
  return true;
};

export const isMessageVisible = (m: Message): boolean => {
  const visibleContent =
    m.role === 'assistant' ? stripThinkTags(m.content) : m.content;

  const hasVisibleContent = Boolean(visibleContent && visibleContent.trim());
  const hasToolCalls = m.role === 'assistant' && visibleToolCalls(m.toolCalls).length > 0;
  const hasToolResults = m.role === 'assistant' && visibleToolCalls(m.toolResults).length > 0;
  const hasStatusCards = m.role === 'assistant' && Boolean(m.statusCards?.length);

  // A stopped / interrupted round with nothing else to show still shows how it ended.
  const hasStopOutcome = m.role === 'assistant' && Boolean(m.stopOutcome);

  return (
    Boolean(m.displayItems?.some(hasRenderableDisplayItem)) ||
    hasStopOutcome ||
    hasVisibleContent ||
    hasToolCalls ||
    hasToolResults ||
    hasStatusCards
  );
};

export const normalizeOrderedStreamItems = (items: StreamRenderItem[]): StreamRenderItem[] => {
  const merged: StreamRenderItem[] = [];
  for (const item of items) {
    if (item.type === 'tool_calls' && item.toolCalls) {
      const visible = visibleToolCalls(item.toolCalls);
      if (visible.length === 0) continue;
      const prev = merged[merged.length - 1];
      if (prev && prev.type === 'tool_calls' && prev.toolCalls) {
        merged[merged.length - 1] = {
          ...prev,
          toolCalls: [...prev.toolCalls, ...visible],
        };
        continue;
      }
      merged.push({ ...item, toolCalls: visible });
    } else if (item.type === 'context') {
      const visible = visibleContextItems(item.items);
      if (visible.length === 0) continue;
      merged.push({ ...item, items: visible });
    } else if (item.type === 'thinking_status' && item.transient) {
      const content = item.content?.trim();
      if (!content) continue;
      const prev = merged[merged.length - 1];
      if (prev && prev.type === 'thinking_status' && prev.transient) {
        // Update consecutive transient progress statuses (e.g. tool-arg preparation updates)
        // in place using the first item's key so the status pill transitions smoothly without stacking or flicker.
        merged[merged.length - 1] = {
          ...prev,
          content,
          timestamp: item.timestamp,
        };
        continue;
      }
      merged.push({ ...item, content });
    } else {
      merged.push(item);
    }
  }
  return merged;
};

export const findLastActiveStreamIndex = (
  normalizedItems: StreamRenderItem[],
  isStreaming: boolean,
  agentLabel: (agentType?: string, agentName?: string) => string | null,
): number => {
  if (!isStreaming) return -1;
  for (let i = normalizedItems.length - 1; i >= 0; i -= 1) {
    const candidate = normalizedItems[i];
    const isVisible =
      (candidate.type === 'thinking_status' && Boolean(candidate.content?.trim()))
      || (candidate.type === 'history_compaction' && Boolean(candidate.compactionState))
      || (candidate.type === 'thinking_content' && Boolean(candidate.content?.trim()))
      || (candidate.type === 'content' && Boolean(candidate.content && stripThinkTags(candidate.content).trim()))
      || (candidate.type === 'tool_calls' && Boolean(candidate.toolCalls?.length))
      || (candidate.type === 'context' && Boolean(candidate.items?.length))
      || (candidate.type === 'agent_selected' && Boolean(agentLabel(candidate.agentType, candidate.agentName)))
      || (candidate.type === 'iteration_exhausted' && candidate.layer !== 'empty_file_correction')
      || candidate.type === 'workflow_stopped'
      || candidate.type === 'workflow_complete';
    if (isVisible) {
      return i;
    }
  }
  return -1;
};

export const extractCopyableText = (
  role: Message['role'],
  hasDisplayItems: boolean,
  orderedDisplayItems: StreamRenderItem[] | undefined,
  displayContent: string,
): string => {
  if (role !== 'assistant') return '';
  if (hasDisplayItems && orderedDisplayItems) {
    return orderedDisplayItems
      .filter((item): item is StreamRenderItem & { content: string } => item.type === 'content' && Boolean(item.content))
      .map((item) => stripThinkTags(item.content).trim())
      .filter(Boolean)
      .join('\n\n');
  }
  return displayContent ? displayContent.trim() : '';
};
