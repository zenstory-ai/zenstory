import React, { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { projectApi } from '../lib/api';
import { ApiError } from '../lib/apiClient';
import { logger } from '../lib/logger';
import { withoutNoteEntries } from '../lib/projectNotes';
import { dispatchProjectStatusUpdated } from '../lib/projectStatusEvents';

interface RememberedNotesProps {
  projectId: string;
  /** Exact stored entries this update_project call appended to the notes (`notes_added`). */
  entries: string[];
  /** Called once the entries are no longer stored (undone now, or already gone). */
  onUndone?: () => void;
}

type Outcome = { kind: 'undone'; changed: number } | { kind: 'gone' };

const isConflict = (err: unknown) => err instanceof ApiError && err.status === 409;

/**
 * What the chat AI wrote into AI 记忆 notes because the author asked it to remember something,
 * with one undo for the whole call. Undo is an author action (human notes PATCH guarded by
 * `expected_notes`, no AI message): it reads the current notes on click, so cards restored from
 * history issue no reads on mount, and removes only lines still stored exactly as written.
 */
export const RememberedNotes: React.FC<RememberedNotesProps> = ({ projectId, entries, onUndone }) => {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<'conflict' | 'failed' | null>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const titleId = useId();

  // The undo button unmounts with the list; keep keyboard focus in the card.
  useEffect(() => {
    if (!outcome) return;
    statusRef.current?.focus();
    onUndone?.();
  }, [outcome, onUndone]);

  const undo = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // One client-side retry on 409, with the fresh baseline, only while the entries are still there.
      for (let attempt = 0; ; attempt++) {
        const fresh = (await projectApi.get(projectId)).notes || '';
        const next = withoutNoteEntries(fresh, entries);
        if (next.removed.length === 0) {
          setOutcome({ kind: 'gone' });
          return;
        }
        try {
          await projectApi.patch(projectId, { notes: next.text, expected_notes: fresh });
        } catch (err) {
          if (attempt === 0 && isConflict(err)) continue;
          throw err;
        }
        dispatchProjectStatusUpdated({ projectId, updatedFields: ['notes'] });
        setOutcome({ kind: 'undone', changed: next.missing.length });
        return;
      }
    } catch (err) {
      logger.error('Failed to undo remembered AI memory notes:', err);
      setError(isConflict(err) ? 'conflict' : 'failed');
    } finally {
      setBusy(false);
    }
  };

  if (outcome) {
    return (
      <div className="mt-2 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-1.5">
        <p ref={statusRef} tabIndex={-1} aria-live="polite"
          className="py-0.5 text-xs text-[hsl(var(--text-secondary))] focus:outline-none">
          {outcome.kind === 'gone' ? t('chat:notesAdded.alreadyGone') : t('chat:notesAdded.undone')}
        </p>
        {outcome.kind === 'undone' && outcome.changed > 0 && (
          <p className="pb-0.5 text-xs text-[hsl(var(--text-secondary))]">
            {t('chat:notesAdded.partlyChanged', { count: outcome.changed })}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="mt-2 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-1.5">
      <div className="flex items-center justify-between gap-2">
        <span id={titleId} className="text-xs text-[hsl(var(--text-secondary))]">
          {t('chat:notesAdded.title')}
        </span>
        <button type="button" disabled={busy} aria-describedby={titleId} onClick={() => void undo()}
          className="inline-flex min-h-[44px] shrink-0 items-center rounded-md px-2.5 text-xs font-medium text-[hsl(var(--accent-primary))] transition-colors hover:bg-[hsl(var(--accent-primary)/0.1)] disabled:cursor-not-allowed disabled:opacity-50 md:min-h-8 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]">
          {t('chat:notesAdded.undo')}
        </button>
      </div>
      <ul className="space-y-0.5 pb-1">
        {entries.map(entry => (
          <li key={entry} className="break-words text-sm text-[hsl(var(--text-primary))] before:mr-1.5 before:text-[hsl(var(--text-secondary))] before:content-['·']">
            {entry}
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="pb-0.5 text-xs text-[hsl(var(--error-text))]">{t(`chat:notesAdded.${error}`)}</p>
      )}
    </div>
  );
};
