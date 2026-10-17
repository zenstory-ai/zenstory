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
      <div>
        <div className="mb-1.5 flex items-center gap-1.5">
          <Icon
            className={
              isDelete
                ? "h-3.5 w-3.5 text-[hsl(var(--error-text))]"
                : "h-3.5 w-3.5 text-[hsl(var(--success-text))]"
            }
          />
          <span
            className={
              isDelete
                ? "text-[11px] font-semibold tracking-[0.01em] text-[hsl(var(--error-text))]"
                : "text-[11px] font-semibold tracking-[0.01em] text-[hsl(var(--success-text))]"
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
      <div className="space-y-4">
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
