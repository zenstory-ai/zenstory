import { Plus, Minus } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { PendingEdit } from "../types";

/**
 * Small, reusable preview renderer for a single Diff Review edit.
 *
 * Task focus: ensure `replace` operations show both old + new text.
 *
 * This component is intended for the Diff Review B right-side "change cards" UI.
 */
export const DiffReviewEditPreview = ({ edit }: { edit: PendingEdit }) => {
  const { t } = useTranslation(["editor"]);
  const deleteLabel = t("editor:editLabelDelete");
  const addLabel = t("editor:editLabelAdd");
  // overscroll-contain 只在 md 起生效：桌面队列是独立滚动区，预览框滚到头不带动队列；
  // 手机上外层审阅面板是唯一滚动区，contain 会截断从预览框起手的触摸滑动，不能加。
  const previewBodyClass =
    "max-h-36 overflow-y-auto md:overscroll-contain pr-1 whitespace-pre-wrap break-words text-sm leading-6 text-[hsl(var(--text-primary))] sm:max-h-40";

  const renderPanel = (
    kind: "delete" | "add",
    label: string,
    content: string
  ) => {
    const isDelete = kind === "delete";
    const Icon = isDelete ? Minus : Plus;

    return (
      <div
        className={
          isDelete
            ? "rounded-2xl bg-[hsl(var(--diff-remove-bg)/0.72)] p-3 shadow-[inset_0_1px_0_hsl(0_0%_100%_/_0.38)] ring-1 ring-[hsl(var(--diff-remove-text)/0.1)]"
            : "rounded-2xl bg-[hsl(var(--diff-add-bg)/0.72)] p-3 shadow-[inset_0_1px_0_hsl(0_0%_100%_/_0.38)] ring-1 ring-[hsl(var(--diff-add-text)/0.1)]"
        }
      >
        <div className="mb-2 flex items-center gap-1.5 border-b border-[hsl(var(--border-primary)/0.22)] pb-2">
          <Icon
            className={
              isDelete
                ? "h-3.5 w-3.5 text-[hsl(var(--diff-remove-text)/0.82)]"
                : "h-3.5 w-3.5 text-[hsl(var(--diff-add-text)/0.82)]"
            }
          />
          <span
            className={
              isDelete
                ? "text-[11px] font-semibold tracking-[0.01em] text-[hsl(var(--diff-remove-text)/0.88)]"
                : "text-[11px] font-semibold tracking-[0.01em] text-[hsl(var(--diff-add-text)/0.88)]"
            }
          >
            {label}
          </span>
        </div>
        <div
          className={previewBodyClass}
          onMouseDown={(event) => event.stopPropagation()}
          onWheel={(event) => event.stopPropagation()}
        >
          {content}
        </div>
      </div>
    );
  };

  if (edit.op === "replace") {
    return (
      <div className="space-y-2.5">
        {renderPanel("delete", deleteLabel, edit.oldText ?? "")}
        {renderPanel("add", addLabel, edit.newText ?? "")}
      </div>
    );
  }

  if (edit.op === "delete") {
    return renderPanel("delete", deleteLabel, edit.oldText ?? "");
  }

  // insert_* / append / prepend
  return renderPanel("add", addLabel, edit.newText ?? "");
};
