/**
 * Collapsible list of explicit context items (attachments & quotes) sent with a turn.
 *
 * @module components/chat/ContextItemsView
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import { AnimatedCollapse } from '../ui/AnimatedCollapse';
import type { AgentContextItem } from '../../types';
import { CHAT_CARD_SURFACE } from '../chatCardStyles';
import {
  contextItemTitle,
  contextOriginBadge,
  contextTypeLabel,
  visibleContextItems,
} from './messageHelpers';

const ContextItemCard: React.FC<{ item: AgentContextItem }> = ({ item }) => {
  const { t } = useTranslation(['chat']);
  const [expanded, setExpanded] = React.useState(false);
  const origin = contextOriginBadge(item, t);

  return (
    <div data-testid="context-item-card" className={`${CHAT_CARD_SURFACE} overflow-hidden`}>
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className={`w-full cursor-pointer select-none min-h-11 md:min-h-0 px-3 py-2 text-xs flex items-center gap-2 min-w-0 text-left ${expanded ? 'rounded-t-xl' : 'rounded-xl'} hover:bg-[hsl(var(--bg-tertiary)/0.5)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))]`}
      >
        <svg
          aria-hidden="true"
          className={`w-3 h-3 text-[hsl(var(--text-secondary))] motion-chevron shrink-0 ${expanded ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <Badge className="shrink-0 max-w-28 [&>span]:truncate">
          {contextTypeLabel(item, t)}
        </Badge>
        <span className="min-w-0 flex-1 truncate text-[hsl(var(--text-primary))]">
          {contextItemTitle(item)}
        </span>
        <Badge
          variant={origin.variant}
          className="shrink-0 max-w-28 [&>span]:truncate"
        >
          {origin.label}
        </Badge>
      </button>

      <AnimatedCollapse open={expanded} fast>
        <div className="border-t border-[hsl(var(--separator-color))] px-3 py-2.5">
          <div className="text-xs leading-relaxed text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words max-h-40 overflow-auto">
            {item.content}
          </div>
        </div>
      </AnimatedCollapse>
    </div>
  );
};

/**
 * Displays a collapsible list of explicit context items (attachments & quotes) sent with the turn.
 */
export const ContextItemsView: React.FC<{ items: AgentContextItem[] }> = ({ items }) => {
  const { t } = useTranslation(['chat']);
  const [isOpen, setIsOpen] = React.useState(false);
  const visibleItems = visibleContextItems(items);
  if (visibleItems.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        className="cursor-pointer select-none min-h-11 md:min-h-0 -my-1.5 md:my-0 rounded text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors flex items-center gap-1 w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))]"
      >
        <svg
          aria-hidden="true"
          className={`w-3 h-3 shrink-0 motion-chevron ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
        <span>
          {t('context.label', { ns: 'chat' })}
          <span className="ml-1 tabular-nums">({visibleItems.length})</span>
        </span>
      </button>

      <AnimatedCollapse open={isOpen} fast>
        <div className="mt-2 space-y-1.5">
          {visibleItems.map((item, index) => (
            <ContextItemCard key={item.id || `${item.type}-${index}`} item={item} />
          ))}
        </div>
      </AnimatedCollapse>
    </div>
  );
};
