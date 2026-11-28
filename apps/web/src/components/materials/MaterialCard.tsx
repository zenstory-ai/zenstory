import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  CheckCircle,
  Clock,
  FileText,
  RefreshCw,
  Trash2,
} from "../icons";
import type { MaterialNovel } from "../../lib/materialsApi";
import { materialJobErrorText } from "../../lib/materialsAccess";
import { Badge, type BadgeProps } from "../ui/Badge";
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
  variant: NonNullable<BadgeProps["variant"]>;
}

function getMaterialStatusPresentation(
  status: MaterialNovel["status"],
  t: (key: string, options?: { defaultValue?: string }) => string,
): StatusPresentation {
  switch (status) {
    case "completed":
      return {
        icon: <CheckCircle className="w-3.5 h-3.5" aria-hidden="true" />,
        text: t("materials:status.completed"),
        variant: "success",
      };
    case "completed_with_errors":
      return {
        icon: <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />,
        text: t("materials:status.completed_with_errors", { defaultValue: "部分完成" }),
        variant: "warning",
      };
    case "processing":
      return {
        icon: <RefreshCw className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />,
        text: t("materials:status.processing"),
        variant: "info",
      };
    case "failed":
      return {
        icon: <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" />,
        text: t("materials:status.failed"),
        variant: "error",
      };
    default:
      return {
        icon: <Clock className="w-3.5 h-3.5" aria-hidden="true" />,
        text: t("materials:status.pending"),
        variant: "neutral",
      };
  }
}

export function MaterialCard({
  material,
  onClick,
  onDelete,
  onRetry,
  isRetrying,
  showDeleteAction,
}: MaterialCardProps) {
  const { t } = useTranslation(["materials", "common"]);
  const canRetryCard =
    Boolean(onRetry) &&
    (material.status === "failed" || material.status === "completed_with_errors");
  const showChapterCount =
    material.status === "completed" || material.status === "completed_with_errors";
  const openMaterialLabel = `${t("materials:viewMaterialDetail", {
    defaultValue: "查看素材详情",
  })}: ${material.title}`;

  const statusPresentation = getMaterialStatusPresentation(material.status, t);

  return (
    <article
      data-testid="material-card"
      onClick={onClick}
      className="group relative flex flex-col rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5 cursor-pointer transition-[border-color,box-shadow,background-color] duration-[var(--duration-fast)] ease-[var(--ease-standard)] hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-sm"
    >
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onClick();
        }}
        aria-label={openMaterialLabel}
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
      />
      <div className="relative pointer-events-none flex flex-1 flex-col">
        <div className="flex items-center justify-between gap-2">
          <Badge
            variant={statusPresentation.variant}
            size="md"
            icon={statusPresentation.icon}
            className="font-medium"
          >
            {statusPresentation.text}
          </Badge>
          <IconButton
            label={t("materials:deleteConfirm.title", { defaultValue: "删除素材" })}
            tone="danger"
            icon={<Trash2 className="w-4 h-4" />}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className={`relative pointer-events-auto -mr-1.5 -my-1.5 ${
              showDeleteAction
                ? "opacity-100"
                : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
            }`}
          />
        </div>

        <h3
          title={material.title}
          className="mt-2 line-clamp-2 text-sm font-semibold leading-snug tracking-[-0.01em] text-[hsl(var(--text-primary))]"
        >
          {material.title}
        </h3>

        {material.original_filename && (
          <p
            title={material.original_filename}
            className="mt-1.5 truncate text-xs text-[hsl(var(--text-secondary))]"
          >
            {material.original_filename}
          </p>
        )}

        <MaterialTrialBookNote material={material} />

        {(material.status === "failed" || material.status === "completed_with_errors") && material.error_message && (
          <p className="mt-2 line-clamp-2 text-xs text-[hsl(var(--error-text))]">
            {materialJobErrorText(material.error_message)}
          </p>
        )}

        <div data-testid="material-card-footer" className="mt-auto pt-4">
          <div className="flex items-center justify-between gap-3 border-t border-[hsl(var(--separator-color)/0.55)] pt-3">
            <div className="min-w-0 flex-1 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
              {showChapterCount ? (
                <span className="inline-flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                  <span>{material.chapters_count || 0} {t("materials:chapters")}</span>
                </span>
              ) : (
                <span>—</span>
              )}
            </div>
            {canRetryCard && onRetry && (
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
                className="relative pointer-events-auto shrink-0"
              >
                {isRetrying
                  ? t("materials:status.processing", { defaultValue: "处理中" })
                  : t("common:retry", { defaultValue: "重试" })}
              </Button>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}
