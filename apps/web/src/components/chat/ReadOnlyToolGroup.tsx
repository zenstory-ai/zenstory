/**
 * Collapsible group for consecutive successful read-only exploration tool calls.
 *
 * @module components/chat/ReadOnlyToolGroup
 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Search } from 'lucide-react';
import { ToolResultCard } from '../ToolResultCard';
import { AnimatedCollapse } from '../ui/AnimatedCollapse';
import type { ToolCall } from '../../types';
import { toolCardPayload } from './messageHelpers';
import type { MessageListProps } from './messageTypes';

export interface ReadOnlyToolGroupProps {
  calls: ToolCall[];
  isStreaming: boolean;
  onUndo?: MessageListProps['onUndo'];
  onRestoreDeletion?: MessageListProps['onRestoreDeletion'];
  chatMessageId?: string;
  onOpenFile?: MessageListProps['onOpenFile'];
  onOpenAIMemory?: MessageListProps['onOpenAIMemory'];
}

export const ReadOnlyToolGroup: React.FC<ReadOnlyToolGroupProps> = ({
  calls,
  isStreaming,
  onUndo,
  onRestoreDeletion,
  chatMessageId,
  onOpenFile,
  onOpenAIMemory,
}) => {
  const { t } = useTranslation(['chat']);
  const [expanded, setExpanded] = useState(false);

  return (
    <div data-testid="readonly-tool-group" className="max-w-full">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className="inline-flex max-w-full min-h-11 md:min-h-7 -ml-1.5 items-center gap-1.5 rounded-lg px-2 py-1 text-left text-xs text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary)/0.75)] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[hsl(var(--accent-primary))]"
      >
        <Search size={12} className="shrink-0 opacity-80" aria-hidden="true" />
        <span className="min-w-0 truncate tabular-nums">
          {t('tool.readonlyGroupSummary', { ns: 'chat', count: calls.length })}
        </span>
        <ChevronDown
          size={12}
          aria-hidden="true"
          className={`shrink-0 opacity-75 motion-chevron ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      <AnimatedCollapse open={expanded} fast>
        <div className="mt-1.5 space-y-1.5 pl-2.5 border-l border-[hsl(var(--separator-color))]">
          {calls.map((toolCall, idx) => (
            <ToolResultCard
              key={toolCall.id || idx}
              type="tool_result"
              toolName={toolCall.tool_name}
              result={toolCardPayload(toolCall)}
              error={toolCall.error}
              isPending={false}
              interrupted={false}
              onUndo={onUndo}
              onRestoreDeletion={isStreaming ? undefined : onRestoreDeletion}
              toolUseId={toolCall.id}
              chatMessageId={chatMessageId}
              onOpenFile={toolCall.status === 'success' && toolCall.result ? onOpenFile : undefined}
              onOpenAIMemory={onOpenAIMemory}
            />
          ))}
        </div>
      </AnimatedCollapse>
    </div>
  );
};
