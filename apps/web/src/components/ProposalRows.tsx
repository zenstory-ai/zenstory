import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

/** Pending rows shown before the rest sit behind a disclosure. */
export const VISIBLE_PROPOSALS = 3;

interface ProposalRowsLabels {
  adopt: string;
  ignore: string;
  adoptLabel: (entry: string) => string;
  ignoreLabel: (entry: string) => string;
  showMore: (count: number) => string;
  showLess: string;
}

interface ProposalRowsProps {
  entries: string[];
  disabled: boolean;
  labels: ProposalRowsLabels;
  onAct: (entry: string, action: 'adopted' | 'ignored') => void;
}

// Compact workspace-toolbar rows (32px) on desktop; 44px touch targets below 768px.
const actionClass = 'inline-flex shrink-0 items-center justify-center rounded-md px-2.5 text-xs font-medium '
  + 'min-h-[44px] md:min-h-8 transition-colors disabled:cursor-not-allowed disabled:opacity-50 '
  + 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]';

/**
 * Pending AI proposals as compact rows: entry text on the left (wraps), the two actions inline on
 * the right at every width. Only the first few rows show until the author expands the rest.
 *
 * After a row action settles, focus moves to the first action of the row now at the acted row's
 * position (the next entry, or the previous one when the last row went), so keyboard and
 * screen-reader users keep their place. When no row is left the list unmounts and the parent
 * owns the focus target.
 */
export const ProposalRows: React.FC<ProposalRowsProps> = ({ entries, disabled, labels, onAct }) => {
  const [expanded, setExpanded] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);
  const focusIndexRef = useRef<number | null>(null);
  const hidden = Math.max(0, entries.length - VISIBLE_PROPOSALS);
  const shown = expanded ? entries : entries.slice(0, VISIBLE_PROPOSALS);
  const entriesKey = entries.join('\u0000');

  useEffect(() => {
    const index = focusIndexRef.current;
    if (index === null || disabled) return;
    focusIndexRef.current = null;
    const firstActions = listRef.current?.querySelectorAll<HTMLButtonElement>('[data-proposal-first-action]');
    if (!firstActions || firstActions.length === 0) return;
    firstActions[Math.min(index, firstActions.length - 1)].focus();
  }, [entriesKey, disabled]);

  const act = (entry: string, index: number, action: 'adopted' | 'ignored') => {
    focusIndexRef.current = index;
    onAct(entry, action);
  };

  return (
    <>
      <ul ref={listRef} className="divide-y divide-[hsl(var(--border-color))]">
        {shown.map((entry, index) => (
          <li key={entry} className="flex items-center gap-2 py-0.5">
            <span className="min-w-0 flex-1 break-words py-1 text-sm text-[hsl(var(--text-primary))]">{entry}</span>
            <button type="button" disabled={disabled} aria-label={labels.adoptLabel(entry)}
              data-proposal-first-action=""
              onClick={() => act(entry, index, 'adopted')}
              className={`${actionClass} text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.1)]`}>
              {labels.adopt}
            </button>
            <button type="button" disabled={disabled} aria-label={labels.ignoreLabel(entry)}
              onClick={() => act(entry, index, 'ignored')}
              className={`${actionClass} text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]`}>
              {labels.ignore}
            </button>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(open => !open)}
          className="inline-flex min-h-[44px] items-center gap-1 rounded-md px-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] md:min-h-8 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]">
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
          {expanded ? labels.showLess : labels.showMore(hidden)}
        </button>
      )}
    </>
  );
};
