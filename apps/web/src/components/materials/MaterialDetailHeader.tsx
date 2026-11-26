import { useTranslation } from "react-i18next";
import i18n from "../../lib/i18n";
import type { MaterialNovel } from "../../lib/materialsApi";
import { materialJobErrorText } from "../../lib/materialsAccess";
import { AlertCircle, ChevronLeft, Download, RefreshCw } from "../icons";
import { DashboardSearchBar } from "../dashboard/DashboardSearchBar";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { isActiveMaterialStatus } from "./materialHelpers";

export interface MaterialDetailHeaderProps {
  material: MaterialNovel;
  isMobile: boolean;
  showMobileContent: boolean;
  searchQuery: string;
  isExporting: boolean;
  onBack: () => void;
  onSearchChange: (query: string) => void;
  onExport: () => void;
}

export function MaterialDetailHeader({
  material,
  isMobile,
  showMobileContent,
  searchQuery,
  isExporting,
  onBack,
  onSearchChange,
  onExport,
}: MaterialDetailHeaderProps) {
  const { t } = useTranslation(["materials", "common"]);

  return (
    <header className="shrink-0 border-b border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))]">
      <div className={`flex gap-3 ${isMobile ? "flex-col px-3 py-3" : "items-center justify-between px-5 py-3"}`}>
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <IconButton
            size="touch"
            label={t("common:back", { defaultValue: "返回" })}
            icon={<ChevronLeft className="h-5 w-5" />}
            onClick={onBack}
          />
          <div className="min-w-0">
            <h1 id="material-title" className="heading-display break-words text-xl font-semibold tracking-tight text-[hsl(var(--text-primary))] md:text-2xl">
              {material.title}
            </h1>
            <p className="mt-0.5 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
              {material.chapters_count == null ? "—" : material.chapters_count.toLocaleString(i18n.language)} {t("materials:detail.readableChapters")}
            </p>
          </div>
        </div>
        <div className={isMobile ? "flex w-full flex-col items-stretch gap-2" : "flex shrink-0 items-center gap-2"}>
          {!(isMobile && showMobileContent) && (
            <DashboardSearchBar
              value={searchQuery}
              onChange={onSearchChange}
              placeholder={t("materials:detail.searchPlaceholder")}
              className={isMobile ? "w-full" : "w-56 shrink-0 lg:w-64"}
            />
          )}
          <Button
            variant="secondary"
            size={isMobile ? "touch" : "md"}
            onClick={onExport}
            isLoading={isExporting}
            disabled={isActiveMaterialStatus(material.status)}
            title={
              isActiveMaterialStatus(material.status)
                ? t("materials:export.unavailableWhileProcessing", { defaultValue: "拆解完成后可导出" })
                : undefined
            }
            leftIcon={<Download className="h-4 w-4" />}
            className={isMobile ? "w-full" : "shrink-0"}
          >
            {t("materials:export.button", { defaultValue: "导出拆解（TXT）" })}
          </Button>
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
