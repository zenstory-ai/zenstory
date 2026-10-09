import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, ChevronUp, Crosshair, RotateCcw, X } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { PendingEdit } from "../types";
import { cn } from "../lib/utils";
import { InlineDiffEditor, type InlineDiffEditorHandle } from "./InlineDiffEditor";
import { DiffReviewEditPreview } from "./DiffReviewEditPreview";

type EditFilter = "all" | "pending" | "accepted" | "rejected";

interface DiffReviewSplitViewProps {
  originalContent: string;
  modifiedContent: string;
  pendingEdits: PendingEdit[];
  onAcceptEdit: (editId: string) => void;
  onRejectEdit: (editId: string) => void;
  onResetEdit: (editId: string) => void;
}

function pickAdjacentId(list: PendingEdit[], currentId: string): string | null {
  const index = list.findIndex((edit) => edit.id === currentId);
  if (index < 0) return list[0]?.id ?? null;
  return list[index + 1]?.id ?? list[index - 1]?.id ?? null;
}

function getEditSize(edit: PendingEdit): number {
  return Math.max((edit.oldText ?? "").trim().length, (edit.newText ?? "").trim().length);
}

export const DiffReviewSplitView = ({
  originalContent,
  modifiedContent,
  pendingEdits,
  onAcceptEdit,
  onRejectEdit,
  onResetEdit,
}: DiffReviewSplitViewProps) => {
  const { t } = useTranslation(["editor"]);

  const rootRef = useRef<HTMLDivElement>(null);
  const diffRef = useRef<InlineDiffEditorHandle>(null);
  const queueScrollRef = useRef<HTMLDivElement>(null);

  const getOpLabel = useCallback((op: PendingEdit["op"]) => {
    switch (op) {
      case "replace":
        return t("editor:diffOpReplace");
      case "delete":
        return t("editor:diffOpDelete");
      case "insert_after":
      case "insert_before":
      case "append":
      case "prepend":
        return t("editor:diffOpInsert");
      default:
        return t("editor:diffOpEdit");
    }
  }, [t]);

  const getStatusLabel = useCallback((status: PendingEdit["status"]) => {
    switch (status) {
      case "pending":
        return t("editor:diffStatusPending");
      case "accepted":
        return t("editor:diffStatusAccepted");
      case "rejected":
        return t("editor:diffStatusRejected");
      default:
        return status;
    }
  }, [t]);

  const [filter, setFilter] = useState<EditFilter>("pending");
  const [activeEditId, setActiveEditId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const pending = pendingEdits.filter((edit) => edit.status === "pending").length;
    const accepted = pendingEdits.filter((edit) => edit.status === "accepted").length;
    const rejected = pendingEdits.filter((edit) => edit.status === "rejected").length;
    return {
      all: pendingEdits.length,
      pending,
      accepted,
      rejected,
    };
  }, [pendingEdits]);

  const filteredEdits = useMemo(() => {
    if (filter === "all") return pendingEdits;
    return pendingEdits.filter((edit) => edit.status === filter);
  }, [filter, pendingEdits]);

  useEffect(() => {
    if (filter === "pending" && counts.pending === 0 && counts.all > 0) {
      setFilter("all");
    }
  }, [counts.all, counts.pending, filter]);

  const resolvedActiveEditId = useMemo(() => {
    if (filteredEdits.length === 0) {
      return null;
    }

    if (activeEditId && filteredEdits.some((edit) => edit.id === activeEditId)) {
      return activeEditId;
    }

    const preferred = filteredEdits.find((edit) => edit.status === "pending") ?? filteredEdits[0];
    return preferred?.id ?? null;
  }, [activeEditId, filteredEdits]);

  const activeIndex = useMemo(() => {
    if (!resolvedActiveEditId) return -1;
    return filteredEdits.findIndex((edit) => edit.id === resolvedActiveEditId);
  }, [filteredEdits, resolvedActiveEditId]);

  // eslint-disable-next-line react-hooks/incompatible-library
  const queueVirtualizer = useVirtualizer({
    count: filteredEdits.length,
    getScrollElement: () => queueScrollRef.current,
    estimateSize: () => 260,
    overscan: 6,
  });

  const getDisplayNumber = useCallback((editId: string) => {
    const index = pendingEdits.findIndex((edit) => edit.id === editId);
    return index >= 0 ? index + 1 : null;
  }, [pendingEdits]);

  useEffect(() => {
    if (activeIndex >= 0) {
      queueVirtualizer.scrollToIndex(activeIndex, { align: "center" });
    }
  }, [activeIndex, queueVirtualizer]);

  const locateEdit = useCallback((editId: string) => {
    const edit = pendingEdits.find((candidate) => candidate.id === editId);
    if (edit && filter !== "all" && edit.status !== filter) {
      setFilter("all");
    }

    setActiveEditId(editId);
    diffRef.current?.scrollToEdit(editId);
  }, [filter, pendingEdits]);

  const goPrev = useCallback(() => {
    if (filteredEdits.length === 0) return;
    const index = activeIndex >= 0 ? activeIndex : 0;
    const previous = filteredEdits[index - 1] ?? filteredEdits[filteredEdits.length - 1];
    locateEdit(previous.id);
  }, [activeIndex, filteredEdits, locateEdit]);

  const goNext = useCallback(() => {
    if (filteredEdits.length === 0) return;
    const index = activeIndex >= 0 ? activeIndex : -1;
    const next = filteredEdits[index + 1] ?? filteredEdits[0];
    locateEdit(next.id);
  }, [activeIndex, filteredEdits, locateEdit]);

  const acceptEdit = useCallback((editId: string) => {
    const nextId = filter === "pending" ? pickAdjacentId(filteredEdits, editId) : null;
    onAcceptEdit(editId);
    if (nextId) {
      locateEdit(nextId);
    }
  }, [filter, filteredEdits, locateEdit, onAcceptEdit]);

  const rejectEdit = useCallback((editId: string) => {
    const nextId = filter === "pending" ? pickAdjacentId(filteredEdits, editId) : null;
    onRejectEdit(editId);
    if (nextId) {
      locateEdit(nextId);
    }
  }, [filter, filteredEdits, locateEdit, onRejectEdit]);

  const resetEdit = useCallback((editId: string) => {
    onResetEdit(editId);
    locateEdit(editId);
  }, [locateEdit, onResetEdit]);

  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  const handleRootMouseDown = useCallback((event: MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement | null;
    if (!target) return;

    if (target.closest("button,a,input,textarea,select,[role='button']")) {
      return;
    }

    if (window.getSelection?.()?.type === "Range") {
      return;
    }

    rootRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (!resolvedActiveEditId) return;

    if (event.key === "ArrowUp" || event.key === "k" || event.key === "K") {
      event.preventDefault();
      goPrev();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "j" || event.key === "J") {
      event.preventDefault();
      goNext();
      return;
    }

    if (!event.shiftKey && (event.key === "y" || event.key === "Y")) {
      event.preventDefault();
      acceptEdit(resolvedActiveEditId);
      return;
    }
    if (!event.shiftKey && (event.key === "n" || event.key === "N")) {
      event.preventDefault();
      rejectEdit(resolvedActiveEditId);
      return;
    }
    if (!event.shiftKey && (event.key === "u" || event.key === "U" || event.key === "r" || event.key === "R")) {
      event.preventDefault();
      resetEdit(resolvedActiveEditId);
      return;
    }

    if (!event.shiftKey && (event.key === "l" || event.key === "L")) {
      event.preventDefault();
      diffRef.current?.scrollToEdit(resolvedActiveEditId);
    }
  }, [acceptEdit, goNext, goPrev, rejectEdit, resetEdit, resolvedActiveEditId]);

  return (
    <div
      ref={rootRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      onMouseDown={handleRootMouseDown}
      className="flex flex-1 min-h-0 flex-col overflow-hidden focus:outline-none md:flex-row"
    >
      <div className="flex-1 min-h-0 min-w-0 overflow-hidden">
        <InlineDiffEditor
          ref={diffRef}
          originalContent={originalContent}
          modifiedContent={modifiedContent}
          pendingEdits={pendingEdits}
          activeEditId={resolvedActiveEditId}
          onSelectEdit={locateEdit}
        />
      </div>

      <div className="flex min-h-0 w-full shrink-0 flex-col overflow-hidden border-t border-[hsl(var(--border-primary)/0.45)] bg-[hsl(var(--bg-secondary)/0.18)] md:w-[400px] md:border-l md:border-t-0 lg:w-[420px]">
        <div className="shrink-0 px-3 pb-3 pt-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                {t("editor:reviewQueue")}
              </div>
              <div className="mt-1 text-[11px] leading-5 text-[hsl(var(--text-secondary))]">
                {t("editor:pendingDefaultAcceptedHint")}
              </div>
            </div>
            <div className="shrink-0 whitespace-nowrap rounded-full bg-[hsl(var(--bg-primary)/0.92)] px-2.5 py-1 text-[11px] font-medium text-[hsl(var(--text-secondary))] shadow-[0_6px_18px_-16px_hsl(var(--text-primary)/0.5)] ring-1 ring-[hsl(var(--border-primary)/0.32)]">
              {t("editor:reviewQueueCount", {
                pending: counts.pending,
                all: counts.all,
              })}
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <div className="grid flex-1 grid-cols-4 gap-1 rounded-full bg-[hsl(var(--bg-primary)/0.92)] p-1 shadow-[0_8px_24px_-20px_hsl(var(--text-primary)/0.45)] ring-1 ring-[hsl(var(--border-primary)/0.32)]">
              {([
                { key: "all" as const, label: t("editor:filterAll"), count: counts.all },
                { key: "pending" as const, label: t("editor:filterPending"), count: counts.pending },
                { key: "accepted" as const, label: t("editor:filterAccepted"), count: counts.accepted },
                { key: "rejected" as const, label: t("editor:filterRejected"), count: counts.rejected },
              ]).map((item) => (
                <button
                  key={item.key}
                  onClick={() => setFilter(item.key)}
                  className={cn(
                    "flex items-center justify-center gap-1 rounded-full px-2 py-1.5 text-[11px] leading-none transition-colors",
                    filter === item.key
                      ? "bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] shadow-sm"
                      : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
                  )}
                  title={`${item.label} (${item.count})`}
                >
                  <span>{item.label}</span>
                  <span className="text-[10px] opacity-70">{item.count}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="mt-3 flex items-center justify-between gap-2 rounded-2xl bg-[hsl(var(--bg-primary)/0.92)] px-2.5 py-2 shadow-[0_10px_28px_-22px_hsl(var(--text-primary)/0.55)] ring-1 ring-[hsl(var(--border-primary)/0.3)]">
            <div className="flex items-center gap-1">
              <button
                onClick={goPrev}
                disabled={filteredEdits.length === 0}
                aria-label={t("editor:prevChange")}
                className="h-8 w-8 rounded-full bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] transition-colors hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:cursor-not-allowed disabled:opacity-40"
                title={t("editor:prevChange")}
              >
                <ChevronUp size={16} className="mx-auto" />
              </button>
              <button
                onClick={goNext}
                disabled={filteredEdits.length === 0}
                aria-label={t("editor:nextChange")}
                className="h-8 w-8 rounded-full bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] transition-colors hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:cursor-not-allowed disabled:opacity-40"
                title={t("editor:nextChange")}
              >
                <ChevronDown size={16} className="mx-auto" />
              </button>
            </div>

            <div className="text-xs font-medium tabular-nums text-[hsl(var(--text-secondary))]">
              {filteredEdits.length === 0 ? "0/0" : `${Math.max(activeIndex, 0) + 1}/${filteredEdits.length}`}
            </div>

            {/* Keyboard shortcuts mean nothing on a phone. */}
            <div data-testid="review-shortcut-hints" className="hidden whitespace-nowrap text-[10px] text-[hsl(var(--text-tertiary))] md:block">
              <span className="mr-2">Y {t("editor:acceptOne")}</span>
              <span className="mr-2">N {t("editor:rejectOne")}</span>
              <span>U {t("editor:resetOne")}</span>
            </div>
          </div>
        </div>

        <div ref={queueScrollRef} className="min-h-0 flex-1 overflow-auto px-3 pb-3">
          {filteredEdits.length === 0 ? (
            <div className="px-1 py-6 text-center text-xs text-[hsl(var(--text-secondary))]">
              {t("editor:noChangesInFilter")}
            </div>
          ) : (
            <div className="w-full">
              {filteredEdits.map((edit, index) => {
                if (!edit) return null;

                return (
                  <div
                    key={edit.id}
                    data-index={index}
                    className="pb-3"
                  >
                    <div
                      onClick={() => locateEdit(edit.id)}
                      className={cn(
                        "cursor-pointer rounded-2xl bg-[hsl(var(--bg-primary)/0.96)] p-3.5 shadow-[0_10px_30px_-24px_hsl(var(--text-primary)/0.6)] ring-1 transition-all",
                        resolvedActiveEditId === edit.id
                          ? "ring-[hsl(var(--accent-primary)/0.5)] shadow-[0_18px_44px_-28px_hsl(var(--accent-primary)/0.55)]"
                          : "ring-[hsl(var(--border-primary)/0.24)] hover:ring-[hsl(var(--accent-primary)/0.2)]"
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded-full bg-[hsl(var(--bg-secondary))] px-2 py-0.5 text-[10px] font-mono text-[hsl(var(--text-tertiary))]">
                              {t("editor:reviewParagraphLabel")} #{getDisplayNumber(edit.id) ?? index + 1}
                            </span>
                            <span
                              className={cn(
                                "rounded-full px-2 py-0.5 text-[10px] font-medium",
                                edit.status === "pending" && "bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))]",
                                edit.status === "accepted" && "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]",
                                edit.status === "rejected" && "bg-[hsl(var(--error)/0.15)] text-[hsl(var(--error))]"
                              )}
                            >
                              {getStatusLabel(edit.status)}
                            </span>
                            <span className="rounded-full bg-[hsl(var(--bg-secondary))] px-2 py-0.5 text-[10px] text-[hsl(var(--text-secondary))]">
                              {getOpLabel(edit.op)}
                            </span>
                            <span className="rounded-full bg-[hsl(var(--bg-secondary))] px-2 py-0.5 text-[10px] text-[hsl(var(--text-tertiary))]">
                              {t("editor:paragraphLength", { count: getEditSize(edit) })}
                            </span>
                          </div>
                        </div>

                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            locateEdit(edit.id);
                          }}
                          aria-label={t("editor:locateChange")}
                          className="h-8 w-8 rounded-full bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] transition-colors hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]"
                          title={t("editor:locateChange")}
                        >
                          <Crosshair size={16} className="mx-auto" />
                        </button>
                      </div>

                      <div className="mt-3">
                        <DiffReviewEditPreview edit={edit} />
                      </div>

                      <div className="mt-3 flex flex-wrap items-center gap-2 sm:flex-nowrap">
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            acceptEdit(edit.id);
                          }}
                          disabled={edit.status === "accepted"}
                          className="flex-1 rounded-xl bg-[hsl(var(--success)/0.12)] px-2.5 py-2 text-xs font-medium text-[hsl(var(--success))] transition-colors hover:bg-[hsl(var(--success)/0.16)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-[hsl(var(--success)/0.12)]"
                          title={t("editor:acceptChange")}
                        >
                          <span className="flex items-center justify-center gap-1.5">
                            <Check size={14} />
                            {t("editor:acceptOne")}
                          </span>
                        </button>
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            rejectEdit(edit.id);
                          }}
                          disabled={edit.status === "rejected"}
                          className="flex-1 rounded-xl bg-[hsl(var(--error)/0.08)] px-2.5 py-2 text-xs font-medium text-[hsl(var(--error))] transition-colors hover:bg-[hsl(var(--error)/0.12)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-[hsl(var(--error)/0.08)]"
                          title={t("editor:rejectChange")}
                        >
                          <span className="flex items-center justify-center gap-1.5">
                            <X size={14} />
                            {t("editor:rejectOne")}
                          </span>
                        </button>
                        <button
                          onClick={(event) => {
                            event.stopPropagation();
                            resetEdit(edit.id);
                          }}
                          disabled={edit.status === "pending"}
                          aria-label={t("editor:resetChange")}
                          className="rounded-xl bg-[hsl(var(--bg-secondary))] px-3 py-2 text-xs text-[hsl(var(--text-secondary))] transition-colors hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-[hsl(var(--bg-secondary))]"
                          title={t("editor:resetChange")}
                        >
                          <span className="flex items-center justify-center gap-1.5">
                            <RotateCcw size={14} />
                          </span>
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
