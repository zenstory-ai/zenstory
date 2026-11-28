import { useTranslation } from "react-i18next";
import i18n from "../../lib/i18n";
import type { MaterialNovel } from "../../lib/materialsApi";
import { materialJobErrorText } from "../../lib/materialsAccess";
import { AlertCircle, ChevronLeft, Download, Loader2, RefreshCw } from "../icons";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { isActiveMaterialStatus } from "./materialHelpers";

export interface MaterialDetailHeaderProps {
  material: MaterialNovel;
  isMobile: boolean;
  isExporting: boolean;
  onBack: () => void;
  onExport: () => void;
}

export function MaterialDetailHeader({
  material,
  isMobile,
  isExporting,
  onBack,
  onExport,
}: MaterialDetailHeaderProps) {
  const { t } = useTranslation(["materials", "common"]);
  const isExportDisabled = isActiveMaterialStatus(material.status);
  const exportLabel = t("materials:export.button", { defaultValue: "导出拆解（TXT）" });
  const exportDisabledTitle = isExportDisabled
    ? t("materials:export.unavailableWhileProcessing", { defaultValue: "拆解完成后可导出" })
    : undefined;

  return (
    <header className="shrink-0 border-b border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-rail))]">
      <div className="flex min-h-12 items-center justify-between gap-2 px-3 py-1.5 md:gap-3 md:px-4">
        <div className="flex min-w-0 flex-1 items-center gap-2 md:gap-2.5">
          <IconButton
            size="touch"
            label={t("common:back", { defaultValue: "返回" })}
            icon={<ChevronLeft className="h-5 w-5" />}
            onClick={onBack}
          />
          <div className="flex min-w-0 flex-1 items-baseline gap-2">
            <h1
              id="material-title"
              title={material.title}
              className="truncate text-sm font-semibold tracking-tight text-[hsl(var(--text-primary))] md:text-base"
            >
              {material.title}
            </h1>
            <span className="shrink-0 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
              {material.chapters_count == null ? "—" : material.chapters_count.toLocaleString(i18n.language)} {t("materials:detail.readableChapters")}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center">
          {isMobile ? (
            <IconButton
              size="touch"
              label={exportLabel}
              title={exportDisabledTitle ?? exportLabel}
              icon={isExporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              onClick={onExport}
              disabled={isExportDisabled || isExporting}
              aria-busy={isExporting || undefined}
            />
          ) : (
            <Button
              variant="secondary"
              size="sm"
              onClick={onExport}
              isLoading={isExporting}
              disabled={isExportDisabled}
              title={exportDisabledTitle}
              leftIcon={<Download className="h-4 w-4" />}
              className="shrink-0"
            >
              {exportLabel}
            </Button>
          )}
        </div>
      </div>
    </header>
  );
}

export interface MaterialStatusBannerProps {
  material: MaterialNovel;
  canRetry: boolean;
  isRetrying: boolean;
  isMobile: boolean;
  onRetry: () => void;
}

export function MaterialStatusBanner({
  material,
  canRetry,
  isRetrying,
  isMobile,
  onRetry,
}: MaterialStatusBannerProps) {
  const { t } = useTranslation(["materials", "common"]);

  if (material.status !== "failed" && material.status !== "completed_with_errors") {
    return null;
  }

  return (
    <div
      role="alert"
      className={`shrink-0 flex flex-wrap items-center gap-3 border-b px-4 py-2.5 text-sm ${
        material.status === "failed"
          ? "border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] text-[hsl(var(--error-text))]"
          : "border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.08)] text-[hsl(var(--warning-text))]"
      }`}
    >
      <AlertCircle className="h-4 w-4 shrink-0" />
      <span className="flex-1 min-w-0">
        <span className="font-medium">
          {material.status === "failed"
            ? t("materials:detail.failedBanner", { defaultValue: "拆解失败" })
            : t("materials:detail.partialBanner", { defaultValue: "部分内容没有拆出来" })}
        </span>
        {material.error_message && (
          <span className="ml-2 text-[hsl(var(--text-secondary))]">
            {materialJobErrorText(material.error_message)}
          </span>
        )}
      </span>
      {canRetry && (
        <Button
          variant="secondary"
          size={isMobile ? "touch" : "sm"}
          onClick={onRetry}
          isLoading={isRetrying}
          leftIcon={<RefreshCw className="h-3.5 w-3.5" />}
        >
          {t("common:retry", { defaultValue: "重试" })}
        </Button>
      )}
    </div>
  );
}
