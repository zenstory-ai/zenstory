import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, RotateCcw } from 'lucide-react';
import { AnimatedDetails } from '../../ui/AnimatedCollapse';
import { CHAT_CARD_INLINE_ACTION, CHAT_CARD_SECONDARY_BUTTON } from '../../chatCardStyles';
import {
  getEditOpIcon,
  getEditOpLabel,
  resolveToolFailure,
  type EditFailureRetryContext,
  type NormalizedFailedEdit,
  type TranslateFn,
} from './toolCardHelpers';

const CopyAttemptedTextButton: React.FC<{
  text: string;
  t: TranslateFn;
}> = ({ text, t }) => {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setCopied(false);
      }, 2000);
    } catch {
      // Clipboard API unavailable in restricted environments; ignore cleanly.
    }
  }, [text]);

  return (
    <button
      type="button"
      onClick={() => { void handleCopy(); }}
      className={CHAT_CARD_INLINE_ACTION}
    >
      {copied ? (
        <Check className="w-3 h-3 text-[hsl(var(--success-text))]" aria-hidden="true" />
      ) : (
        <Copy className="w-3 h-3" aria-hidden="true" />
      )}
      <span>
        {copied ? t('chat:tool.copied_attempted_text') : t('chat:tool.copy_attempted_text')}
      </span>
    </button>
  );
};

export const FailedEditsSection: React.FC<{
  failedEdits: NormalizedFailedEdit[];
  defaultOpen?: boolean;
  t: TranslateFn;
}> = ({ failedEdits, defaultOpen = true, t }) => {
  if (failedEdits.length === 0) return null;

  return (
    <AnimatedDetails
      className="group mt-1.5"
      defaultOpen={defaultOpen}
      summaryClassName="cursor-pointer select-none pl-6 min-h-11 md:min-h-0 -my-1.5 md:my-0 rounded text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 list-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] [&::-webkit-details-marker]:hidden"
      summaryStyle={{ listStyle: 'none' }}
      summary={(isDetailsOpen) => (
        <>
          <svg
            className="w-3 h-3 shrink-0 motion-chevron group-open:rotate-90"
            style={!isDetailsOpen ? { transform: 'rotate(0deg)' } : undefined}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          {t('chat:tool.failed_edits_summary', { count: failedEdits.length })}
        </>
      )}
    >
      <div className="space-y-2 mt-2">
        {failedEdits.map((item, idx) => {
          const reason = resolveToolFailure(
            { error_type: item.errorType, user_message: item.userMessage },
            t,
          ).message;
          const targetSnippet = item.oldPreview || item.anchorPreview;
          const targetLabel = item.anchorPreview && !item.oldPreview
            ? t('chat:tool.failed_edit_anchor')
            : t('chat:tool.failed_edit_target');
          const attemptedSnippet = item.newPreview || item.textPreview;
          const copyText = item.copyText || attemptedSnippet;

          return (
            <div
              key={`${item.index}-${idx}`}
              className="rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.45)] p-2.5 text-xs space-y-1.5"
            >
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                {getEditOpIcon(item.op)}
                <span className="font-medium text-[hsl(var(--text-primary))]">
                  {getEditOpLabel(item.op, t)}
                </span>
                <span className="text-[hsl(var(--text-secondary))] break-words">
                  · {reason}
                </span>
              </div>

              {targetSnippet && (
                <div className="bg-[hsl(var(--bg-secondary))] p-2 rounded-lg border-l-2 border-[hsl(var(--border-color))]">
                  <div className="text-xs text-[hsl(var(--text-secondary))] mb-0.5">
                    {targetLabel}
                  </div>
                  <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-40 overflow-auto">
                    {targetSnippet}
                  </div>
                </div>
              )}

              {attemptedSnippet && (
                <div className="bg-[hsl(var(--bg-secondary))] p-2 rounded-lg border-l-2 border-[hsl(var(--accent-primary)/0.45)]">
                  <div className="flex items-center justify-between gap-2 mb-0.5">
                    <span className="text-xs text-[hsl(var(--text-secondary))]">
                      {t('chat:tool.failed_edit_attempted')}
                    </span>
                    {copyText && <CopyAttemptedTextButton text={copyText} t={t} />}
                  </div>
                  <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-40 overflow-auto">
                    {attemptedSnippet}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </AnimatedDetails>
  );
};

export const RetryEditFailureAction: React.FC<{
  context: EditFailureRetryContext;
  onRetry: (context: EditFailureRetryContext) => void;
  t: TranslateFn;
}> = ({ context, onRetry, t }) => (
  <div className="mt-2 pl-6">
    <button
      type="button"
      onClick={() => onRetry(context)}
      className={`gap-1.5 ${CHAT_CARD_SECONDARY_BUTTON}`}
    >
      <RotateCcw className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
      <span>{t('chat:tool.retry_reread_edit')}</span>
    </button>
  </div>
);
