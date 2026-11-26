import { useTranslation } from "react-i18next";
import { CheckSquare, Download, Square } from "../icons";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import type { SkillShareStatus } from "../../types";

export function SkillListError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation(["skills", "common"]);
  return (
    <div role="alert" className="mb-4 rounded-2xl border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-4">
      <p className="text-sm text-[hsl(var(--text-primary))]">{t("errors.loadFailed")}</p>
      <Button variant="secondary" size="touch" className="mt-3" onClick={onRetry}>
        {t("common:retry")}
      </Button>
    </div>
  );
}

export function SkillSelectionCheckbox({
  skillName,
  isSelected,
  onToggleSelect,
}: {
  skillName: string;
  isSelected?: boolean;
  onToggleSelect: () => void;
}) {
  const { t } = useTranslation(["skills"]);
  return (
    <button
      type="button"
      onClick={onToggleSelect}
      data-testid="skill-selection-toggle"
      aria-pressed={isSelected}
      aria-label={isSelected ? t("skills:batch.deselect", { name: skillName }) : t("skills:batch.select", { name: skillName })}
      className="shrink-0 min-h-11 min-w-11 md:min-h-8 md:min-w-8 inline-flex items-center justify-center rounded-lg -ml-2 -mt-1 md:ml-0 md:mt-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
    >
      {isSelected ? (
        <CheckSquare className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
      ) : (
        <Square className="w-5 h-5 text-[hsl(var(--text-tertiary))] hover:text-[hsl(var(--text-secondary))]" />
      )}
    </button>
  );
}

/** Export (download zip) button shared by user and added skill cards. */
export function ExportSkillButton({
  onExport,
  exporting,
  isMobile,
}: {
  onExport: () => void;
  exporting?: boolean;
  isMobile?: boolean;
}) {
  const { t } = useTranslation(["skills"]);
  return (
    <IconButton
      onClick={onExport}
      disabled={exporting}
      label={t("skills:export.button")}
      size={isMobile ? "touch" : "md"}
      icon={
        exporting ? (
          <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
        ) : (
          <Download className="w-4 h-4" />
        )
      }
    />
  );
}

/** Character counter shown under length-limited form fields. */
export function CharCount({ value, max, inline }: { value: string; max: number; inline?: boolean }) {
  const nearLimit = value.length >= max * 0.9;
  return (
    <p
      className={`text-xs shrink-0 ${inline ? "" : "mt-1 text-right"} ${
        nearLimit ? "text-[hsl(var(--warning-text))]" : "text-[hsl(var(--text-tertiary))]"
      }`}
    >
      {value.length.toLocaleString()} / {max.toLocaleString()}
    </p>
  );
}

const SHARE_STATUS_STYLES: Record<SkillShareStatus, string> = {
  pending: "bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning-text))]",
  approved: "bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]",
  unpublished: "bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error-text))]",
};

/** Review status of a skill the user shared to the public library. */
export function ShareStatusBadge({ status }: { status: SkillShareStatus }) {
  const { t } = useTranslation(["skills"]);
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded shrink-0 ${SHARE_STATUS_STYLES[status] ?? SHARE_STATUS_STYLES.pending}`}
      data-testid="skill-share-status"
    >
      {t(`skills:shareStatus.${status}`)}
    </span>
  );
}
