import React, { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2 } from 'lucide-react';

interface RememberedNotesProps {
  /** Exact stored entries this update_project call appended to notes (`notes_added`). */
  entries: string[];
  /** Opens the project's single AI Memory editor at Writing Conventions. */
  onOpen?: () => void;
  /** The parent update_project card already supplies the shared result surface. */
  embedded?: boolean;
  /** Mixed update cards already show the shared success icon. */
  showIcon?: boolean;
}

/** A persistent receipt of what the AI added after the author explicitly asked it to remember. */
export const RememberedNotes: React.FC<RememberedNotesProps> = ({ entries, onOpen, embedded = false, showIcon = true }) => {
  const { t } = useTranslation();
  const titleId = useId();

  return (
    <div className={embedded ? 'pt-2' : 'rounded-lg border border-[hsl(var(--result-border))] bg-[hsl(var(--result-bg))] px-3 py-2'}>
      <div className="flex items-center gap-2">
        {showIcon && <CheckCircle2 className="h-4 w-4 shrink-0 text-[hsl(var(--success-text))]" />}
        <p id={titleId} className="text-sm font-medium text-[hsl(var(--success-text))]">{t('chat:notesAdded.title')}</p>
      </div>
      <ul aria-labelledby={titleId} className="mt-1 space-y-0.5">
        {entries.map((entry, index) => (
          <li key={`${index}-${entry}`} className="break-words text-sm text-[hsl(var(--text-primary))] before:mr-1.5 before:text-[hsl(var(--text-secondary))] before:content-['·']">
            {entry}
          </li>
        ))}
      </ul>
      {onOpen && (
        <button type="button" onClick={onOpen}
          className="mt-2 inline-flex min-h-11 items-center rounded-md px-2.5 text-sm font-medium text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] md:min-h-8">
          {t('chat:notesAdded.open')}
        </button>
      )}
    </div>
  );
};
