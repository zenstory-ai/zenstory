/**
 * Shared token-based surface and control classes for all Agent cards in the chat panel.
 *
 * Keeps card geometry (12px outer radius, 8px inner buttons, hairline theme borders)
 * and action hierarchy (one solid primary CTA, calm neutral secondary siblings, never
 * arbitrary warning colors on non-warning options) consistent across every chat card.
 */

/** Standard calm card shell: crisp white over pale canvas in light mode, ink card in dark mode. */
export const CHAT_CARD_SURFACE =
  'rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]';

/** Conversational prompt / invitation shell (next-step offer, starter prompt, clarification question). */
export const CHAT_CARD_PROMPT_SURFACE =
  'rounded-xl border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.05)]';

/** Genuine warning / interrupted stream alert shell. */
export const CHAT_CARD_WARNING_SURFACE =
  'rounded-xl border border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.06)]';

/** Single recommended primary action on a chat card (AA contrast across all 6 user accents in both schemes). */
export const CHAT_CARD_PRIMARY_BUTTON =
  'inline-flex items-center justify-center min-h-11 md:min-h-8 rounded-lg border border-transparent bg-[hsl(var(--primary))] px-3 text-xs font-medium text-[hsl(var(--primary-foreground))] hover:bg-[hsl(var(--primary-hover))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]';

/** Calm neutral secondary button shared by all alternative/sibling choices on a chat card. */
export const CHAT_CARD_SECONDARY_BUTTON =
  'inline-flex items-center justify-center min-h-11 md:min-h-8 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] px-3 text-xs font-medium text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]';

/** Compact inline ghost control inside dense tool result headers (e.g. Undo). */
export const CHAT_CARD_INLINE_ACTION =
  'inline-flex items-center gap-1 min-h-11 md:min-h-7 -my-2 md:my-0 shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]';
