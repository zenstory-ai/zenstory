/**
 * InlineDiffEditor — renders an inline diff between original and modified content.
 *
 * Diff Review B 方案: this view is read-only (no inline ✅/❌ actions). Users review edits
 * via the right-side review queue, and can "locate" an edit in this diff view.
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useTranslation } from "react-i18next";
import type { PendingEdit } from "../types";
import { cn } from "../lib/utils";
import {
  buildReviewSegmentsFromDiffs,
  computeParagraphReviewDiffs,
  type ReviewDiffSegment,
} from "../lib/diffReview";

export interface InlineDiffEditorHandle {
  scrollToEdit: (editId: string) => void;
}

interface InlineDiffEditorProps {
  originalContent: string;
  modifiedContent: string;
  pendingEdits: PendingEdit[];
  activeEditId?: string | null;
  onSelectEdit?: (editId: string) => void;
}

export const InlineDiffEditor = forwardRef<InlineDiffEditorHandle, InlineDiffEditorProps>(function InlineDiffEditor(
  { originalContent, modifiedContent, pendingEdits, activeEditId, onSelectEdit },
  ref
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation(["editor"]);
  const initialAutoScrollDoneRef = useRef(false);

  const diffSegments = useMemo((): ReviewDiffSegment[] => {
    const diffs = computeParagraphReviewDiffs(originalContent, modifiedContent);
    return buildReviewSegmentsFromDiffs(diffs);
  }, [originalContent, modifiedContent]);

  useEffect(() => {
    // If content changes, allow auto-scroll again.
    initialAutoScrollDoneRef.current = false;
  }, [originalContent, modifiedContent]);

  const scrollToEdit = useCallback((editId: string) => {
    if (!editId) return;

    try {
      const el = containerRef.current?.querySelector<HTMLElement>(`[data-edit-id="${editId}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (error) {
      if (import.meta.env.DEV) {
        console.debug("[InlineDiffEditor] scrollToEdit failed", { editId, error });
      }
    }
  }, []);

  useEffect(() => {
    // Only auto-scroll when entering diff review (or when the underlying content changes),
    // to avoid jumpiness while the user is actively reviewing.
    if (initialAutoScrollDoneRef.current) return;

    const firstPendingId = pendingEdits.find((e) => e.status === "pending")?.id;
    const firstAnyId = pendingEdits[0]?.id;
    const targetId = firstPendingId ?? firstAnyId;
    if (!targetId) return;

    initialAutoScrollDoneRef.current = true;

    const timer = setTimeout(() => {
      scrollToEdit(targetId);
    }, 100);

    return () => clearTimeout(timer);
  }, [pendingEdits, scrollToEdit]);

  useImperativeHandle(ref, () => ({ scrollToEdit }), [scrollToEdit]);

  const renderSegment = useCallback((segment: ReviewDiffSegment, index: number) => {
    if (segment.type === "equal") {
      return (
        <span key={index} className="text-[hsl(var(--text-primary))]">
          {segment.text}
        </span>
      );
    }

    const edit = segment.editIndex != null ? pendingEdits[segment.editIndex] : undefined;
    const editId = edit?.id;
    const status = edit?.status;
    const isActive = !!activeEditId && !!editId && activeEditId === editId;

    const statusClass = status === "accepted"
      ? "opacity-75"
      : status === "rejected"
        ? "opacity-45"
        : "";

    const activeClass = isActive
      ? "outline outline-2 outline-[hsl(var(--accent-primary)/0.55)] outline-offset-2"
      : "";

    const clickable = editId && onSelectEdit ? "cursor-pointer" : "";
    const onClick = editId && onSelectEdit ? () => onSelectEdit(editId) : undefined;
    const onKeyDown = editId && onSelectEdit
      ? (event: ReactKeyboardEvent<HTMLSpanElement>) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelectEdit(editId);
          }
        }
      : undefined;
    const interactiveProps = editId && onSelectEdit
      ? {
          role: "button" as const,
          tabIndex: 0,
          onKeyDown,
          "aria-label": t("editor:locateChangeIndexed", {
            defaultValue: "定位到第 {{index}} 处修改",
            index: (segment.editIndex ?? 0) + 1,
          }),
        }
      : {};

    if (segment.type === "delete") {
      return (
        <span
          key={index}
          data-edit-id={editId}
          data-status={status}
          onClick={onClick}
          {...interactiveProps}
          className={cn(
            "diff-remove rounded-sm border-l border-[hsl(var(--diff-remove-text)/0.75)] px-1.5 py-0.5",
            statusClass,
            activeClass,
            clickable
          )}
        >
          {segment.text}
        </span>
      );
    }

    if (segment.type === "insert") {
      return (
        <span
          key={index}
          data-edit-id={editId}
          data-status={status}
          onClick={onClick}
          {...interactiveProps}
          className={cn(
            "diff-add rounded-sm border-l border-[hsl(var(--diff-add-text)/0.75)] px-1.5 py-0.5",
            statusClass,
            activeClass,
            clickable
          )}
        >
          {segment.text}
        </span>
      );
    }

    return (
      <span
        key={index}
        data-edit-id={editId}
        data-status={status}
        onClick={onClick}
        {...interactiveProps}
        className={cn("inline-flex rounded-sm overflow-hidden", statusClass, activeClass, clickable)}
      >
        <span className="diff-remove border-l border-[hsl(var(--diff-remove-text)/0.75)] px-1.5 py-0.5">
          {segment.text}
        </span>
        <span className="diff-add border-r border-[hsl(var(--diff-add-text)/0.75)] px-1.5 py-0.5">
          {segment.newText}
        </span>
      </span>
    );
  }, [activeEditId, onSelectEdit, pendingEdits, t]);

  return (
    <div ref={containerRef} className="flex-1 min-h-0 overflow-auto bg-[hsl(var(--bg-primary))] px-6 pb-4 pt-2">
      <div className="whitespace-pre-wrap leading-[1.8] text-base">
        {diffSegments.map((segment, index) => renderSegment(segment, index))}
      </div>
    </div>
  );
});
