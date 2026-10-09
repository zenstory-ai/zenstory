import { useTranslation } from "react-i18next";
import { BookOpen } from "../icons";
import { Button } from "../ui/Button";
import { DashboardEmptyState } from "../dashboard/DashboardEmptyState";
import type { MaterialNovel } from "../../lib/materialsApi";
import type { MaterialTrialSelection } from "../../lib/materialUploadValidation";

/** Upload dialog: how much of the picked book the free trial covers. */
export function MaterialTrialSelectionNote({ selection }: { selection: MaterialTrialSelection }) {
  const { t } = useTranslation(["materials"]);
  const rest = selection.totalChapters - selection.keptChapters;
  return (
    <p
      data-testid="materials-trial-selection-note"
      className="mt-2 text-sm text-[hsl(var(--text-secondary))]"
    >
      {rest > 0
        ? t("materials:trialSelectionCut", {
            defaultValue: "这本书共 {{total}} 章，免费试拆只拆前 {{chapters}} 章，后 {{rest}} 章不拆。",
            total: selection.totalChapters,
            chapters: selection.keptChapters,
            rest,
          })
        : t("materials:trialSelectionAll", {
            defaultValue: "这本书共 {{total}} 章，会全部拆解。",
            total: selection.totalChapters,
          })}
    </p>
  );
}

/** Library card: a trial book that covers only the first chapters of the file. */
export function MaterialTrialBookNote({ material }: { material: MaterialNovel }) {
  const { t } = useTranslation(["materials"]);
  const limit = material.trial_chapter_limit;
  const total = material.source_chapter_count;
  if (!limit || !total || total <= limit) return null;
  return (
    <p data-testid="materials-trial-book-note" className="mt-2 text-xs text-[hsl(var(--text-tertiary))]">
      {t("materials:trialBookNote", {
        defaultValue: "免费试拆：全书 {{total}} 章，只拆了前 {{chapters}} 章",
        total,
        chapters: limit,
      })}
    </p>
  );
}

interface MaterialTrialUsedEmptyStateProps {
  onUpgrade: () => void;
  /** The Pro entry's label (「开通 Pro」, or 「兑换码开通」 while online checkout is off). */
  label: string;
  /** Page-action size: `md` on desktop, `touch` (44px) on phones. */
  size: "md" | "touch";
}

/** Empty library after the free trial was used: nothing left to upload without Pro. */
export function MaterialTrialUsedEmptyState({ onUpgrade, label, size }: MaterialTrialUsedEmptyStateProps) {
  const { t } = useTranslation(["materials"]);
  return (
    <div data-testid="materials-trial-used-empty">
      <DashboardEmptyState
        icon={BookOpen}
        title={t("materials:trialUsedEmptyTitle", { defaultValue: "免费试拆已经用过了" })}
        description={t("materials:trialUsedEmptyBody", {
          defaultValue: "开通 Pro 后，每月可以拆解参考小说，每本最多 30 万字；拆出的角色、设定和章节梗概写作时随时引用。",
        })}
        action={
          <Button size={size} onClick={onUpgrade}>
            {label}
          </Button>
        }
      />
    </div>
  );
}
