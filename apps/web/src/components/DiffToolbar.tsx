/**
 * @fileoverview DiffToolbar component - Review controls for AI-generated file changes.
 *
 * This component provides a toolbar interface for reviewing and managing
 * pending AI-generated edits. It displays edit statistics and offers
 * bulk actions for accepting or rejecting changes.
 *
 * Features:
 * - Edit status badges showing pending, accepted, and rejected counts
 * - Accept all / Reject all bulk action buttons
 * - Finish review button with conditional styling (changes based on review state)
 * - Keyboard shortcut hints on desktop; on phones the hint points below instead of right
 * - i18n support for all text content
 *
 * @module components/DiffToolbar
 */
import { useTranslation } from "react-i18next";
import { Check, CheckCheck, XCircle, GitCompare, Sparkles } from "lucide-react";
import type { PendingEdit } from "../types";
import { useMobileLayout } from "../contexts/MobileLayoutContext";

/**
 * Props for the DiffToolbar component.
 */
interface DiffToolbarProps {
  /** Array of pending edits to review, each with status (pending/accepted/rejected) */
  pendingEdits: PendingEdit[];
  /** Callback fired when user clicks "Accept All" - accepts all pending edits */
  onAcceptAll: () => void;
  /** Callback fired when user clicks "Reject All" - rejects all pending edits */
  onRejectAll: () => void;
  /** Callback fired when user clicks "Finish Review" - applies changes and exits review mode */
  onFinish: () => void;
}

/**
 * DiffToolbar component for managing AI-generated edit reviews.
 *
 * Renders a toolbar with status information and action buttons for reviewing
 * pending AI edits. The component tracks edit statuses (pending, accepted, rejected)
 * and provides bulk action buttons for accepting or rejecting all pending edits.
 *
 * The "Finish Review" button changes appearance based on review state:
 * - When pending edits remain: Neutral styling with check icon
 * - When all edits reviewed: Accent color with sparkle icon ("Apply Changes")
 *
 * @param props - Component props
 * @param props.pendingEdits - Array of edits with status property
 * @param props.onAcceptAll - Handler for accepting all pending edits
 * @param props.onRejectAll - Handler for rejecting all pending edits
 * @param props.onFinish - Handler for finishing the review process
 * @returns The rendered diff toolbar component
 *
 * @example
 * ```tsx
 * <DiffToolbar
 *   pendingEdits={[
 *     { id: '1', status: 'pending', original: 'old', modified: 'new' },
 *     { id: '2', status: 'accepted', original: 'foo', modified: 'bar' },
 *   ]}
 *   onAcceptAll={() => acceptAllEdits()}
 *   onRejectAll={() => rejectAllEdits()}
 *   onFinish={() => applyChangesAndClose()}
 * />
 * ```
 */
export const DiffToolbar = ({
  pendingEdits,
  onAcceptAll,
  onRejectAll,
  onFinish,
}: DiffToolbarProps) => {
  const { t } = useTranslation(['editor']);
  // Phone layout (Layout wraps the editor in MobileLayoutProvider under 768px): the
  // review queue sits below the text and there is no keyboard.
  const { isMobile } = useMobileLayout();

  // Count edit statuses
  const pendingCount = pendingEdits.filter(e => e.status === 'pending').length;
  const acceptedCount = pendingEdits.filter(e => e.status === 'accepted').length;
  const rejectedCount = pendingEdits.filter(e => e.status === 'rejected').length;
  const totalCount = pendingEdits.length;

  // All edits have been reviewed
  const allReviewed = pendingCount === 0;

  // Bulk actions should stay available even when nothing is "pending"
  // (e.g. if edits default to accepted, or user already reviewed everything).
  const canAcceptAll = totalCount > 0 && acceptedCount !== totalCount;
  const canRejectAll = totalCount > 0 && rejectedCount !== totalCount;

  return (
    <div className="shrink-0 border-b border-[hsl(var(--border-primary)/0.35)] bg-[hsl(var(--bg-secondary)/0.58)] px-4 py-2.5 backdrop-blur-sm">
      {/* Left: Status info */}
      <div className="flex flex-col gap-2.5 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:gap-4">
          <div className="flex items-center gap-2">
            <div className="rounded-xl bg-[hsl(var(--accent-primary)/0.12)] p-1.5 ring-1 ring-[hsl(var(--accent-primary)/0.08)]">
              <GitCompare size={14} className="text-[hsl(var(--accent-primary))]" />
            </div>
            <div className="min-w-0">
              <span className="text-sm font-medium leading-tight text-[hsl(var(--text-primary))]">
                {t('editor:reviewMode')}
              </span>
              <span className="mt-0.5 block text-[10px] leading-tight text-[hsl(var(--text-secondary))]">
                {t(isMobile ? 'editor:reviewModeHintMobile' : 'editor:reviewModeHint')}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[hsl(var(--bg-primary)/0.88)] px-2.5 py-1 text-xs text-[hsl(var(--text-secondary))] ring-1 ring-[hsl(var(--border-primary)/0.3)]">
              {t('editor:paragraphBlocksCount', { count: totalCount })}
            </span>
            {pendingCount > 0 && (
              <span className="rounded-full bg-[hsl(var(--warning)/0.14)] px-2.5 py-1 text-xs font-medium text-[hsl(var(--warning))]">
                {t('editor:pendingCount', { count: pendingCount })}
              </span>
            )}
            {acceptedCount > 0 && (
              <span className="rounded-full bg-[hsl(var(--success)/0.14)] px-2.5 py-1 text-xs text-[hsl(var(--success))]">
                ✓ {acceptedCount}
              </span>
            )}
            {rejectedCount > 0 && (
              <span className="rounded-full bg-[hsl(var(--error)/0.14)] px-2.5 py-1 text-xs text-[hsl(var(--error))]">
                ✗ {rejectedCount}
              </span>
            )}
          </div>
        </div>

        {/* Right: Action buttons */}
        <div className="flex flex-wrap items-center gap-2 xl:justify-end">
          {/* Accept All button */}
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={onAcceptAll}
            disabled={!canAcceptAll}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs transition-all
            text-[hsl(var(--success))] hover:bg-[hsl(var(--success)/0.1)]
            disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
            title={t('editor:acceptAll')}
          >
            <CheckCheck size={14} />
            <span>{t('editor:acceptAll')}</span>
          </button>

          {/* Reject All button */}
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={onRejectAll}
            disabled={!canRejectAll}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs transition-all
            text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.1)]
            disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
            title={t('editor:rejectAll')}
          >
            <XCircle size={14} />
            <span>{t('editor:rejectAll')}</span>
          </button>

          {/* Divider */}
          <div className="mx-1 hidden h-5 w-px bg-[hsl(var(--border-primary)/0.7)] sm:block" />

          {/* Finish Review button */}
          <button
            onMouseDown={(e) => e.preventDefault()}
            onClick={onFinish}
            className={`flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium transition-all ${
              allReviewed
                ? 'bg-[hsl(var(--accent-primary))] text-white shadow-sm hover:bg-[hsl(var(--accent-dark))]'
                : 'bg-[hsl(var(--bg-primary)/0.88)] text-[hsl(var(--text-primary))] ring-1 ring-[hsl(var(--border-primary)/0.3)] hover:bg-[hsl(var(--bg-primary))]'
            }`}
            title={allReviewed
              ? t('editor:applyChanges')
              : t('editor:finishReview')}
          >
            {allReviewed ? (
              <>
                <Sparkles size={14} />
                {t('editor:applyChanges')}
              </>
            ) : (
              <>
                <Check size={14} />
                {t('editor:finishReview')}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};
