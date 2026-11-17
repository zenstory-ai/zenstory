import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Layers, Loader2 } from 'lucide-react';

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
          className="inline-flex max-w-full items-center gap-2 rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary))] px-3 py-2 text-xs"
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

  const text = summary?.trim();
  const chipClass =
    'inline-flex min-h-11 md:min-h-7 max-w-full items-center gap-1.5 rounded-full border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))] px-3 text-xs font-medium text-[hsl(var(--text-secondary))]';
  return (
    <div className="mb-3" data-testid="history-compaction-marker">
      <div className="flex items-center gap-3">
        <div aria-hidden="true" className="h-px flex-1 bg-[hsl(var(--separator-color))]" />
        {text ? (
          <button
            type="button"
            className={`${chipClass} transition-colors hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]`}
            aria-expanded={open}
            aria-controls={summaryId}
            onClick={() => setOpen((value) => !value)}
          >
            <Layers size={12} aria-hidden="true" className="shrink-0" />
            <span>{t('compaction.done', { ns: 'chat' })}</span>
            <ChevronDown
              size={12}
              aria-hidden="true"
              className={`shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
            />
          </button>
        ) : (
          <span className={chipClass}>
            <Layers size={12} aria-hidden="true" className="shrink-0" />
            <span>{t('compaction.done', { ns: 'chat' })}</span>
          </span>
        )}
        <div aria-hidden="true" className="h-px flex-1 bg-[hsl(var(--separator-color))]" />
      </div>
      <p className="mt-1.5 text-center text-xs text-[hsl(var(--text-secondary))]">
        {t('compaction.doneHint', { ns: 'chat' })}
      </p>
      {text && open && (
        <div
          id={summaryId}
          className="mt-2 max-h-72 overflow-auto rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary))] p-3"
        >
          <div className="mb-1.5 text-xs font-medium text-[hsl(var(--text-secondary))]">
            {t('compaction.summaryLabel', { ns: 'chat' })}
          </div>
          <div className="whitespace-pre-wrap break-words text-xs leading-relaxed text-[hsl(var(--text-primary))]">
            {text}
          </div>
        </div>
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
