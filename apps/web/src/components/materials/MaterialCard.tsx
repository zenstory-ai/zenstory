import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  BookOpen,
  CheckCircle,
  Clock,
  FileText,
  RefreshCw,
  Trash2,
} from "../icons";
import type { MaterialNovel } from "../../lib/materialsApi";
import { materialJobErrorText } from "../../lib/materialsAccess";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { MaterialTrialBookNote } from "./MaterialTrialNotes";

export interface MaterialCardProps {
  material: MaterialNovel;
  onClick: () => void;
  onDelete: () => void;
  onRetry?: () => void;
  isRetrying?: boolean;
  isMobile?: boolean;
  showDeleteAction?: boolean;
}

interface StatusPresentation {
  icon: ReactNode;
  text: string;
  colorClass: string;
}

function getMaterialStatusPresentation(
  status: MaterialNovel["status"],
  t: (key: string, options?: { defaultValue?: string }) => string,
): StatusPresentation {
  switch (status) {
    case "completed":
      return {
        icon: <CheckCircle className="w-4 h-4 text-[hsl(var(--success-text))]" />,
        text: t("materials:status.completed"),
        colorClass: "text-[hsl(var(--success-text))] bg-[hsl(var(--success)/0.1)]",
      };
    case "completed_with_errors":
      return {
        icon: <AlertCircle className="w-4 h-4 text-[hsl(var(--warning-text))]" />,
        text: t("materials:status.completed_with_errors", { defaultValue: "部分完成" }),
        colorClass: "text-[hsl(var(--warning-text))] bg-[hsl(var(--warning)/0.1)]",
      };
    case "processing":
      return {
        icon: <RefreshCw className="w-4 h-4 text-[hsl(var(--info-text))] animate-spin" />,
        text: t("materials:status.processing"),
        colorClass: "text-[hsl(var(--info-text))] bg-[hsl(var(--info)/0.1)]",
      };
    case "failed":
      return {
        icon: <AlertCircle className="w-4 h-4 text-[hsl(var(--error-text))]" />,
        text: t("materials:status.failed"),
        colorClass: "text-[hsl(var(--error-text))] bg-[hsl(var(--error)/0.1)]",
      };
    default:
      return {
        icon: <Clock className="w-4 h-4 text-[hsl(var(--text-tertiary))]" />,
        text: t("materials:status.pending"),
        colorClass: "text-[hsl(var(--text-tertiary))] bg-[hsl(var(--bg-tertiary))]",
      };
  }
}

export function MaterialCard({
  material,
  onClick,
  onDelete,
  onRetry,
  isRetrying,
  isMobile,
  showDeleteAction,
}: MaterialCardProps) {
  const { t } = useTranslation(["materials", "common"]);
  const canRetryCard =
    Boolean(onRetry) &&
    (material.status === "failed" || material.status === "completed_with_errors");
  const openMaterialLabel = `${t("materials:viewMaterialDetail", {
    defaultValue: "查看素材详情",
  })}: ${material.title}`;

  const handleOpenByKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onClick();
    }
  };

  const statusPresentation = getMaterialStatusPresentation(material.status, t);

  return (
    <div
      data-testid="material-card"
      className={`group relative bg-[hsl(var(--bg-secondary))] rounded-2xl border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.3)] transition-colors ${isMobile ? "p-3" : "p-4"}`}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={handleOpenByKeyboard}
        aria-label={openMaterialLabel}
        className="cursor-pointer rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
      >
        <div className="flex items-start justify-between gap-3 mb-3 pr-10">
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="w-10 h-10 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center shrink-0">
              <BookOpen className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))] truncate">
                {material.title}
              </h3>
              <p className="text-xs text-[hsl(var(--text-tertiary))] truncate">
                {material.original_filename}
              </p>
            </div>
          </div>
        </div>

        {/* Status Badge */}
        <div className="flex items-center gap-2 mb-3">
          <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium ${statusPresentation.colorClass}`}>
            {statusPresentation.icon}
            {statusPresentation.text}
          </span>
        </div>

        {/* Stats */}
        {(material.status === "completed" || material.status === "completed_with_errors") && (
          <div className="flex items-center gap-4 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
            <div className="flex items-center gap-1">
              <FileText className="w-3.5 h-3.5" />
              <span>{material.chapters_count || 0} {t("materials:chapters")}</span>
            </div>
          </div>
        )}

        <MaterialTrialBookNote material={material} />

        {/* Error Message */}
        {(material.status === "failed" || material.status === "completed_with_errors") && material.error_message && (
          <p className="text-xs text-[hsl(var(--error-text))] mt-2 line-clamp-2">
            {materialJobErrorText(material.error_message)}
          </p>
        )}
      </div>

      {canRetryCard && onRetry && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="touch"
            onClick={(e) => {
              e.stopPropagation();
              onRetry();
            }}
            disabled={isRetrying}
            leftIcon={<RefreshCw className={`w-3.5 h-3.5 ${isRetrying ? "animate-spin" : ""}`} />}
          >
            {isRetrying
              ? t("materials:status.processing", { defaultValue: "处理中" })
              : t("common:retry", { defaultValue: "重试" })}
          </Button>
        </div>
      )}

      {/* Delete Button */}
      <IconButton
        label={t("materials:deleteConfirm.title", { defaultValue: "删除素材" })}
        tone="danger"
        icon={<Trash2 className="w-4 h-4" />}
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className={`absolute top-2 right-2 ${
          showDeleteAction
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
        }`}
      />
    </div>
  );
}
