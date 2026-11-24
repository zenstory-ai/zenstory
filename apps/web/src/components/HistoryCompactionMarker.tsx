import { useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import type { Components } from 'react-markdown';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Layers, Loader2 } from 'lucide-react';
import { LazyMarkdown } from './LazyMarkdown';
import { AnimatedCollapse } from './ui/AnimatedCollapse';
import { CHAT_CARD_SURFACE } from './chatCardStyles';
import { formatCompactionSummary } from '../lib/compactionSummary';

const summaryMarkdownComponents: Components = {
  h1: ({ children }) => <h3 className="mt-3 first:mt-0 text-sm font-semibold leading-snug">{children}</h3>,
  h2: ({ children }) => <h4 className="mt-3 first:mt-0 text-xs font-semibold leading-snug">{children}</h4>,
  h3: ({ children }) => <h5 className="mt-2.5 first:mt-0 text-xs font-semibold leading-snug">{children}</h5>,
  h4: ({ children }) => <h6 className="mt-2.5 first:mt-0 text-xs font-semibold leading-snug">{children}</h6>,
  p: ({ children }) => <p className="mt-1.5 first:mt-0">{children}</p>,
  ul: ({ children }) => <ul className="mt-1.5 list-disc space-y-1 pl-4">{children}</ul>,
  ol: ({ children }) => <ol className="mt-1.5 list-decimal space-y-1 pl-4">{children}</ol>,
  table: ({ children }) => (
    <div tabIndex={0} className="mt-2 max-w-full overflow-x-auto rounded-lg border border-[hsl(var(--separator-color))]">
      <table className="w-max min-w-full border-collapse text-left">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[hsl(var(--bg-tertiary)/0.6)]">{children}</thead>,
  th: ({ children }) => (
    <th className="border-b border-r border-[hsl(var(--separator-color))] px-2 py-1.5 font-semibold last:border-r-0">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-r border-[hsl(var(--separator-color))] px-2 py-1.5 align-top last:border-r-0">
      {children}
    </td>
  ),
  code: ({ children }) => (
    <code className="break-all rounded bg-[hsl(var(--bg-tertiary)/0.6)] px-1 py-0.5 font-mono text-[0.9em]">
      {children}
    </code>
  ),
  pre: ({ children }: { children?: ReactNode }) => (
    <pre className="mt-2 max-w-full overflow-x-auto rounded-lg bg-[hsl(var(--bg-tertiary)/0.6)] p-2 font-mono text-[0.9em]">
      {children}
    </pre>
  ),
};

/**
 * Earlier conversation summarized before this round (single loop agent).
 *
 * While the summary call runs: a visible step with a spinner and elapsed seconds, where the
 * reply will appear. Once done: a centered divider chip that stays in the conversation and
 * opens the read-only handoff summary the AI now works from.
 */
export function HistoryCompactionMarker({ state, summary, startedAt }: {
  state: 'running' | 'done';
  summary?: string;
  /** When the running step started (elapsed seconds count from here). */
  startedAt?: Date;
}) {
  const { t } = useTranslation(['chat']);
  const [open, setOpen] = useState(false);
  const summaryId = useId();
  const elapsed = useElapsedSeconds(state === 'running' ? startedAt : undefined);

  if (state === 'running') {
    return (
      <div className="mb-3" data-testid="history-compaction-running">
        <div
          role="status"
          className={`inline-flex max-w-full items-center gap-2 px-3 py-2 text-xs ${CHAT_CARD_SURFACE}`}
        >
          <Loader2 size={14} aria-hidden="true" className="shrink-0 animate-spin text-[hsl(var(--accent-primary))]" />
          <span className="font-medium text-[hsl(var(--text-primary))]">{t('compaction.running', { ns: 'chat' })}</span>
          {elapsed > 0 && (
            <span className="tabular-nums text-[hsl(var(--text-secondary))]">
              {t('compaction.elapsed', { ns: 'chat', seconds: elapsed })}
            </span>
          )}
        </div>
      </div>
    );
  }

  const text = formatCompactionSummary(summary);
  const chipClass =
    'inline-flex min-h-11 md:min-h-7 max-w-full items-center gap-1.5 rounded-full border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 text-xs font-medium text-[hsl(var(--text-secondary))]';
  return (
    <div className="mb-3 min-w-0" data-testid="history-compaction-marker">
      <div className="flex items-center gap-3">
        <div aria-hidden="true" className="h-px flex-1 bg-[hsl(var(--separator-color))]" />
        {text ? (
          <button
            type="button"
            className={`${chipClass} transition-colors hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]`}
            aria-expanded={open}
            aria-controls={summaryId}
            onClick={() => setOpen((value) => !value)}
          >
            <Layers size={13} aria-hidden="true" className="shrink-0" />
            <span>{t('compaction.done', { ns: 'chat' })}</span>
            <ChevronDown
              size={13}
              aria-hidden="true"
              className={`shrink-0 motion-chevron ${open ? 'rotate-180' : ''}`}
            />
          </button>
        ) : (
          <span className={chipClass}>
            <Layers size={13} aria-hidden="true" className="shrink-0" />
            <span>{t('compaction.done', { ns: 'chat' })}</span>
          </span>
        )}
        <div aria-hidden="true" className="h-px flex-1 bg-[hsl(var(--separator-color))]" />
      </div>
      <p className="mt-1.5 text-center text-xs text-[hsl(var(--text-secondary))]">
        {t('compaction.doneHint', { ns: 'chat' })}
      </p>
      {text && (
        <AnimatedCollapse open={open}>
          <div
            id={summaryId}
            className={`mt-2 min-w-0 max-w-full max-h-72 overflow-auto p-3 ${CHAT_CARD_SURFACE}`}
          >
            <div className="mb-1.5 text-xs font-medium text-[hsl(var(--text-secondary))]">
              {t('compaction.summaryLabel', { ns: 'chat' })}
            </div>
            <LazyMarkdown
              className="min-w-0 max-w-full break-words text-xs leading-relaxed text-[hsl(var(--text-primary))]"
              components={summaryMarkdownComponents}
            >
              {text}
            </LazyMarkdown>
          </div>
        </AnimatedCollapse>
      )}
    </div>
  );
}

function useElapsedSeconds(startedAt: Date | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  if (!startedAt) return 0;
  return Math.max(0, Math.floor((now - startedAt.getTime()) / 1000));
}
