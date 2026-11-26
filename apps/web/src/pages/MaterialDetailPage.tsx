import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { BookOpen, Loader2 } from "../components/icons";
import { ApiError } from "../lib/apiClient";
import { isFeatureNotIncludedError } from "../lib/materialsAccess";
import { useIsMobile } from "../hooks/useMediaQuery";
import { Button } from "../components/ui/Button";
import { MaterialsUpgradeNotice } from "../components/subscription/MaterialsUpgradePrompt";
import { MaterialContentDetail } from "../components/materials/MaterialContentDetail";
import { MaterialDetailHeader, MaterialStatusBanner } from "../components/materials/MaterialDetailHeader";
import { MaterialFileTree } from "../components/materials/MaterialFileTree";
import { MaterialOverview } from "../components/materials/MaterialOverview";
import { materialTreeItemKey } from "../components/materials/materialHelpers";
import { useMaterialDetailState } from "../components/materials/useMaterialDetailState";

export default function MaterialDetailPage() {
  const { novelId } = useParams<{ novelId: string }>();
  return <MaterialDetailContent key={novelId} novelId={novelId} />;
}

function MaterialDetailContent({ novelId }: { novelId?: string }) {
  const navigate = useNavigate();
  const { t } = useTranslation(["materials", "common"]);
  const isMobile = useIsMobile();
  const state = useMaterialDetailState(novelId, isMobile);

  if (state.materialLoading) {
    return (
      <div role="status" className="flex items-center justify-center h-dvh">
        <div aria-hidden="true" className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
        <span className="sr-only">{t("common:loading", { defaultValue: "加载中…" })}</span>
      </div>
    );
  }

  if (state.materialError && isFeatureNotIncludedError(state.materialError)) {
    return (
      <div className="flex h-dvh items-center justify-center px-4">
        <MaterialsUpgradeNotice source="material_detail" className="max-w-md text-center" />
      </div>
    );
  }

  if (state.materialError) {
    const isNotFound = state.materialError instanceof ApiError && state.materialError.status === 404;
    return (
      <div className="flex flex-col items-center justify-center gap-3 h-dvh">
        <p role="alert" className="text-[hsl(var(--text-secondary))]">
          {isNotFound
            ? t("materials:detail.notFound")
            : t("materials:detail.loadError", { defaultValue: "素材详情加载失败，请重试。" })}
        </p>
        {!isNotFound && (
          <Button variant="secondary" size="touch" onClick={() => void state.refetchMaterial()}>
            {t("common:retry", { defaultValue: "重试" })}
          </Button>
        )}
      </div>
    );
  }

  if (!state.material) {
    return (
      <div className="flex items-center justify-center h-dvh">
        <p className="text-[hsl(var(--text-secondary))]">{t("materials:detail.notFound")}</p>
      </div>
    );
  }

  return (
    <div className="viewport-shell ui-sans-headings h-dvh flex flex-col overflow-hidden bg-[hsl(var(--bg-primary))]">
      <MaterialDetailHeader
        material={state.material}
        isMobile={isMobile}
        showMobileContent={state.showMobileContent}
        searchQuery={state.searchQuery}
        isExporting={state.isExporting}
        onBack={() => {
          if (isMobile && state.showMobileContent) state.handleMobileBack();
          else navigate("/dashboard/materials");
        }}
        onSearchChange={state.handleSearchChange}
        onExport={() => void state.handleExport()}
      />

      <MaterialStatusBanner
        material={state.material}
        canRetry={state.canRetry}
        isRetrying={state.isRetrying}
        isMobile={isMobile}
        onRetry={() => void state.handleRetry()}
      />

      <main aria-labelledby="material-title" className="flex min-h-0 flex-1 overflow-hidden">
        {(!isMobile || !state.showMobileContent) && (
          <aside
            aria-label={t("materials:detail.contents")}
            className={`${isMobile ? "flex-1" : "w-64 shrink-0 border-r lg:w-72"} overflow-y-auto border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))]`}
          >
            <div className="px-3 py-5">
              <h2 className="mb-3 px-2 text-xs font-medium text-[hsl(var(--text-secondary))]">
                {t("materials:detail.contents")}
              </h2>
              <button
                type="button"
                aria-current={state.selectedItem === null ? "true" : undefined}
                onClick={state.handleOverviewClick}
                className={`mb-2 flex min-h-11 w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] ${
                  state.selectedItem === null
                    ? "bg-[hsl(var(--accent-primary)/0.08)] text-[hsl(var(--accent-primary))]"
                    : "text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
                }`}
              >
                <BookOpen className="h-4 w-4 shrink-0" />
                <span>{t("materials:detail.overview")}</span>
              </button>
              <MaterialFileTree
                items={state.filteredTree}
                selectedKey={state.selectedItem ? materialTreeItemKey(state.selectedItem) : undefined}
                expandedFolders={state.expandedFolders}
                onItemClick={state.handleItemClick}
                loadingStates={state.loadingStates}
                loadedStates={state.loadedStates}
                folderErrors={state.folderErrors}
                onRetryFolder={state.handleRetryFolder}
                searchActive={Boolean(state.searchQuery.trim())}
              />
              {state.searchQuery.trim() && state.searchComplete && state.filteredTree.length === 0 && (
                <div className="px-2 py-5 space-y-2.5">
                  <p className="text-sm text-[hsl(var(--text-secondary))]">
                    {t("materials:detail.noSearchResults")}
                  </p>
                  <Button
                    type="button"
                    variant="secondary"
                    size={isMobile ? "touch" : "sm"}
                    onClick={() => state.setSearchQuery("")}
                  >
                    {t("common:clearSearch", { defaultValue: "清空搜索" })}
                  </Button>
                </div>
              )}
            </div>
          </aside>
        )}
        {(!isMobile || state.showMobileContent) && (
          <section
            aria-label={t("materials:detail.contentLabel")}
            className="min-w-0 flex-1 overflow-y-auto bg-[hsl(var(--bg-secondary))]"
          >
            <div className="mx-auto w-full max-w-[44rem] px-5 py-7 md:px-8 md:py-10">
              {state.selectedContentItem ? (
                state.isFetchingSelectedChapter && state.selectedContentItem.type === "chapter" ? (
                  <div role="status" aria-label={t("common:loading")} className="flex min-h-40 items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-[hsl(var(--text-secondary))]" />
                  </div>
                ) : state.selectedContentItem.type === "chapter" && state.selectedChapterError && !state.selectedChapter ? (
                  <div role="alert" className="space-y-3 py-8 text-sm text-[hsl(var(--text-secondary))]">
                    <p>{t("materials:detail.loadError", { defaultValue: "素材详情加载失败，请重试。" })}</p>
                    <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={() => void state.refetchSelectedChapter()}>
                      {t("common:retry", { defaultValue: "重试" })}
                    </Button>
                  </div>
                ) : (
                  <MaterialContentDetail item={state.selectedContentItem} />
                )
              ) : (
                <MaterialOverview
                  material={state.material}
                  folders={state.treeData}
                  onCategoryClick={state.handleOverviewCategoryClick}
                />
              )}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
