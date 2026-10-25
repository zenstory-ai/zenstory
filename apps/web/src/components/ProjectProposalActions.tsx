import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { projectApi } from '../lib/api';
import { ApiError } from '../lib/apiClient';
import { logger } from '../lib/logger';
import { adoptProjectProposals, ignoreProjectProposals, pendingProposalEntries } from '../lib/projectNotes';
import { dispatchProjectStatusUpdated } from '../lib/projectStatusEvents';
import { ProposalRows } from './ProposalRows';

type EntryState = 'adopted' | 'ignored' | 'handled';

interface ProjectProposalActionsProps {
  projectId: string;
  /** Exact stored texts of the proposals this update_project call added (`proposals_added`). */
  entries: string[];
}

/**
 * Typed human actions on the AI proposals an update_project call added: no chat reply and no
 * AI message. Current state is read on click (cards restored from history issue no reads on
 * mount); the write is the human notes PATCH guarded by the notes just read.
 */
export const ProjectProposalActions: React.FC<ProjectProposalActionsProps> = ({ projectId, entries }) => {
  const { t } = useTranslation('chat');
  const [states, setStates] = useState<Record<string, EntryState>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<'conflict' | 'failed' | null>(null);
  const open = entries.filter(entry => !states[entry]);
  // The pending block unmounts when its last entry is handled; focus then lands on the summary
  // line instead of falling back to the page body.
  const summaryRef = useRef<HTMLParagraphElement>(null);
  const refocusRef = useRef(false);
  useEffect(() => {
    if (!refocusRef.current || busy) return;
    refocusRef.current = false;
    if (open.length === 0) summaryRef.current?.focus();
  }, [busy, open.length]);

  const act = async (targets: string[], action: 'adopted' | 'ignored') => {
    if (busy || targets.length === 0) return;
    refocusRef.current = true;
    setBusy(true);
    setMessage(null);
    try {
      const fresh = (await projectApi.get(projectId)).notes || '';
      const pending = new Set(pendingProposalEntries(fresh));
      const still = targets.filter(entry => pending.has(entry));
      const handled = Object.fromEntries(targets.filter(entry => !pending.has(entry)).map(entry => [entry, 'handled' as const]));
      if (still.length > 0) {
        const next = action === 'adopted' ? adoptProjectProposals(fresh, still) : ignoreProjectProposals(fresh, still);
        if (next !== null) {
          await projectApi.patch(projectId, { notes: next.trim(), expected_notes: fresh });
          Object.assign(handled, Object.fromEntries(still.map(entry => [entry, action])));
          dispatchProjectStatusUpdated({ projectId, updatedFields: ['notes'] });
        }
      }
      setStates(current => ({ ...current, ...handled }));
    } catch (err) {
      logger.error('Failed to apply AI memory proposal action:', err);
      setMessage(err instanceof ApiError && err.status === 409 ? 'conflict' : 'failed');
    } finally {
      setBusy(false);
    }
  };

  const count = (state: EntryState) => entries.filter(entry => states[entry] === state).length;
  const summary = (['adopted', 'ignored', 'handled'] as const)
    .map(state => ({ state, n: count(state) }))
    .filter(({ n }) => n > 0)
    .map(({ state, n }) => t(`proposals.summary.${state}`, { count: n }))
    .join(' · ');

  return (
    <div className="mt-2 space-y-1 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-1.5">
      {open.length > 0 && (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-[hsl(var(--text-secondary))]">{t('proposals.pendingTitle', { count: open.length })}</span>
            {open.length > 1 && (
              <button type="button" disabled={busy} onClick={() => void act(open, 'adopted')}
                className="inline-flex min-h-[44px] shrink-0 items-center rounded-md px-2.5 text-xs font-medium text-[hsl(var(--accent-primary))] transition-colors hover:bg-[hsl(var(--accent-primary)/0.1)] disabled:cursor-not-allowed disabled:opacity-50 md:min-h-8 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]">
                {t('proposals.adoptAll')}
              </button>
            )}
          </div>
          <ProposalRows entries={open} disabled={busy} onAct={(entry, action) => void act([entry], action)}
            labels={{
              adopt: t('proposals.adopt'),
              ignore: t('proposals.ignore'),
              adoptLabel: entry => t('proposals.adoptLabel', { entry }),
              ignoreLabel: entry => t('proposals.ignoreLabel', { entry }),
              showMore: n => t('proposals.showMore', { count: n }),
              showLess: t('proposals.showLess'),
            }} />
        </>
      )}
      <p ref={summaryRef} tabIndex={-1} aria-live="polite"
        className={summary ? 'py-0.5 text-xs text-[hsl(var(--text-secondary))] focus:outline-none' : 'sr-only'}>
        {summary}
      </p>
      {message && (
        <p role="alert" className="text-xs text-[hsl(var(--error-text))]">{t(`proposals.${message}`)}</p>
      )}
    </div>
  );
};
