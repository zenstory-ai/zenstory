import { useTranslation } from "react-i18next";
import { BookOpen } from "../icons";
import type { MaterialNovel } from "../../lib/materialsApi";
import { DashboardEmptyState } from "../dashboard/DashboardEmptyState";
import { DashboardSearchBar } from "../dashboard/DashboardSearchBar";
import { Button } from "../ui/Button";
import { MaterialCard } from "./MaterialCard";
import { MaterialTrialUsedEmptyState } from "./MaterialTrialNotes";
import { filterMaterialsByQuery } from "./materialHelpers";

export function MaterialErrorAlert({
  title,
  message,
  actionSize,
  onRetry,
}: {
  title?: string;
  message: string;
  actionSize: "md" | "touch";
  onRetry: () => void;
}) {
  const { t } = useTranslation(["common"]);
  return (
    <div role="alert" className="rounded-2xl border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-6">
      <div className="space-y-3">
        {title && (
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {title}
          </h2>
        )}
        <p className="text-sm text-[hsl(var(--text-secondary))]">{message}</p>
        <Button variant="secondary" size={actionSize} onClick={onRetry}>
          {t("common:retry", { defaultValue: "重试" })}
        </Button>
      </div>
    </div>
  );
}

function MaterialQuotaBanner({
  remainingDecompositions,
  limit,
  isExhausted,
  exhaustedMessage,
  className = "",
}: {
  remainingDecompositions: number | null;
  limit: number;
  isExhausted: boolean;
  exhaustedMessage: string;
  className?: string;
}) {
  const { t } = useTranslation(["materials", "common"]);
  return (
    <div className={`rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4 ${className}`}>
      <p className="text-sm font-medium tabular-nums text-[hsl(var(--text-primary))]">
        {remainingDecompositions == null
          ? t("common:unlimited", { defaultValue: "不限" })
          : t("materials:quotaRemaining", {
              defaultValue: "本月还可拆解 {{remaining}} / {{limit}} 次，每次上传或重试用 1 次",
              remaining: remainingDecompositions,
              limit,
            })}
      </p>
      {isExhausted && (
        <p className="mt-2 text-sm text-[hsl(var(--warning-text))]">
          {exhaustedMessage}
        </p>
      )}
    </div>
  );
}

export interface MaterialLibrarySectionProps {
  materials: MaterialNovel[];
  hasWorkspaceAccess: boolean;
  trialUsed: boolean;
  trialMaxChapters?: number;
  proEntryLabel: string;
  actionSize: "md" | "touch";
  isMobile: boolean;
  showDeleteAction: boolean;
  decomposeQuotaLimit?: number;
  remainingDecompositions: number | null;
  isMaterialsQuotaExhausted: boolean;
  exhaustedMessage: string;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  exportingId: string | null;
  retryingId: string | null;
  onOpenUpload: () => void;
  onOpenUpgrade: (destination: "billing" | "pricing", source?: string) => void;
  onCardClick: (novelId: string) => void;
  onDelete: (novelId: string) => void;
  onExport: (material: MaterialNovel) => void;
  onRetry: (novelId: string) => void;
}

export function MaterialLibrarySection({
  materials,
  hasWorkspaceAccess,
  trialUsed,
  trialMaxChapters,
  proEntryLabel,
  actionSize,
  isMobile,
  showDeleteAction,
  decomposeQuotaLimit,
  remainingDecompositions,
  isMaterialsQuotaExhausted,
  exhaustedMessage,
  searchQuery,
  onSearchChange,
  exportingId,
  retryingId,
  onOpenUpload,
  onOpenUpgrade,
  onCardClick,
  onDelete,
  onExport,
  onRetry,
}: MaterialLibrarySectionProps) {
  const { t } = useTranslation(["materials", "common"]);

  if (materials.length === 0 && trialUsed) {
    return (
      <MaterialTrialUsedEmptyState
        onUpgrade={() => onOpenUpgrade("billing", "materials_trial_used")}
        label={proEntryLabel}
        size={actionSize}
      />
    );
  }

  if (materials.length === 0) {
    return (
      <>
        {decomposeQuotaLimit !== undefined && hasWorkspaceAccess && (
          <MaterialQuotaBanner
            remainingDecompositions={remainingDecompositions}
            limit={decomposeQuotaLimit}
            isExhausted={isMaterialsQuotaExhausted}
            exhaustedMessage={exhaustedMessage}
            className="mb-4"
          />
        )}
        <DashboardEmptyState
          icon={BookOpen}
          title={t("materials:noMaterials")}
          action={
            !isMaterialsQuotaExhausted ? (
              <Button size={actionSize} onClick={onOpenUpload}>
                {t("materials:uploadFirst")}
              </Button>
            ) : undefined
          }
        />
      </>
    );
  }

  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  const filteredMaterials = filterMaterialsByQuery(materials, searchQuery);

  return (
    <div className="space-y-4">
      {trialUsed && (
        <div
          data-testid="materials-trial-banner"
          className="flex flex-wrap items-center gap-3 rounded-xl border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.06)] p-4"
        >
          <p className="flex-1 min-w-0 text-sm text-[hsl(var(--text-primary))]">
            {t("materials:trialUsedBanner", {
              defaultValue: "你已经用掉免费试拆（每个账号 1 次，只拆前 {{chapters}} 章）。开通 Pro 后，每月可以拆解更多参考小说，每本最多 30 万字。",
              chapters: trialMaxChapters,
            })}
            {materials.some((material) => material.status === "failed" || material.status === "completed_with_errors") && (
              <span data-testid="materials-trial-incomplete" className="mt-1 block text-[hsl(var(--text-secondary))]">
                {t("materials:trialIncomplete", {
                  defaultValue: "这次试拆没有全部完成，已拆好的内容仍可查看；免费机会不会恢复，开通 Pro 后可以继续拆解。",
                })}
              </span>
            )}
          </p>
          <Button size={actionSize} onClick={() => onOpenUpgrade("billing")}>
            {proEntryLabel}
          </Button>
        </div>
      )}

      {decomposeQuotaLimit !== undefined && hasWorkspaceAccess && (
        <MaterialQuotaBanner
          remainingDecompositions={remainingDecompositions}
          limit={decomposeQuotaLimit}
          isExhausted={isMaterialsQuotaExhausted}
          exhaustedMessage={exhaustedMessage}
        />
      )}

      {materials.length >= 2 && (
        <DashboardSearchBar
          value={searchQuery}
          onChange={onSearchChange}
          placeholder={t("materials:searchPlaceholder", { defaultValue: "搜索素材" })}
          className="max-w-md"
          inputTestId="materials-search-input"
        />
      )}

      {filteredMaterials.length === 0 && normalizedSearch ? (
        <DashboardEmptyState
          icon={BookOpen}
          title={t("materials:noSearchResults", {
            defaultValue: "没有找到匹配的素材，换个关键词试试",
          })}
          action={
            <Button variant="secondary" size={actionSize} onClick={() => onSearchChange("")}>
              {t("common:clearSearch", { defaultValue: "清空搜索" })}
            </Button>
          }
        />
      ) : (
        <div className={`grid gap-3.5 motion-content-reveal ${isMobile ? "grid-cols-1" : "grid-cols-2 lg:grid-cols-3"}`}>
          {filteredMaterials.map((material) => (
            <MaterialCard
              key={material.id}
              material={material}
              onClick={() => onCardClick(material.id)}
              onDelete={() => onDelete(material.id)}
              onExport={() => onExport(material)}
              isExporting={exportingId === material.id}
              onRetry={
                // Retrying charges a monthly breakdown: the free trial cannot retry.
                !hasWorkspaceAccess || isMaterialsQuotaExhausted
                  ? undefined
                  : () => onRetry(material.id)
              }
              isRetrying={retryingId === material.id}
              isMobile={isMobile}
              showDeleteAction={showDeleteAction}
            />
          ))}
        </div>
      )}
    </div>
  );
}
