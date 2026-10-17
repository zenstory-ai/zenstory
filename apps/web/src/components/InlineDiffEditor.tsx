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
    // 手机上审阅改为上下排列，正文对比区高度为 0、只显示修改卡片列表；此时 scrollIntoView
    // 会去滚外层审阅容器，把作者刚滑到的「接受/拒绝」按钮又拉回顶部。
    if (!containerRef.current?.parentElement?.clientHeight) return;

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

    const clickable = editId && onSelectEdit
      ? "cursor-pointer focus-visible:outline-2 focus-visible:outline-[hsl(var(--accent-primary))] focus-visible:outline-offset-2"
      : "";
    const onClick = editId && onSelectEdit ? () => onSelectEdit(editId) : undefined;
    const onKeyDown = editId && onSelectEdit
      ? (event: ReactKeyboardEvent<HTMLSpanElement>) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            event.stopPropagation();
            onSelectEdit(editId);
          }
        }
      : undefined;
    const interactiveProps = editId && onSelectEdit
      ? {
          role: "button" as const,
          tabIndex: 0,
          onKeyDown,
          "aria-current": isActive ? "true" as const : undefined,
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
            "diff-remove",
            statusClass,
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
            "diff-add",
            statusClass,
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
        className={cn("inline-flex", statusClass, clickable)}
      >
        <span className="diff-remove px-1.5 py-0.5">
          {segment.text}
        </span>
        <span className="diff-add px-1.5 py-0.5">
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
