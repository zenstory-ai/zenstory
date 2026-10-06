import { useTranslation } from "react-i18next";
import { useState, useEffect, useRef } from "react";
import { Search, Filter, ChevronLeft, ChevronRight } from "../icons";
import { InspirationCard } from "./InspirationCard";
import { InspirationDetailDialog } from "./InspirationDetailDialog";
import { useInspirations } from "../../hooks/useInspirations";
import { ApiError } from "../../lib/apiClient";
import { toast } from "../../lib/toast";
import { Skeleton } from "../ui/Skeleton";
import { UpgradePromptModal } from "../subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";

interface InspirationGridProps {
  /** Filter by project type */
  projectType?: string;
  /** Initial search query */
  initialSearch?: string;
  /** Show featured only */
  featuredOnly?: boolean;
  /** PageSize */
  pageSize?: number;
}

/**
 * Custom hook for debouncing a value
 */
function useDebouncedValue<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Clear previous timeout
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }

    // Set new timeout
    timeoutRef.current = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    // Cleanup on unmount or when value/delay changes
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [value, delay]);

  return debouncedValue;
}

/**
 * Loading skeleton card for grid using Skeleton component
 */
function InspirationCardSkeleton() {
  return (
    <div
      data-testid="inspiration-card-skeleton"
      className="flex h-full flex-col overflow-hidden rounded-2xl border border-[hsl(var(--separator-color))]
                 bg-[hsl(var(--bg-tertiary)/0.35)]"
    >
      {/* Cover skeleton */}
      <Skeleton variant="rectangular" className="aspect-video rounded-none" />

      {/* Content skeleton */}
      <div className="flex flex-1 flex-col gap-3 p-5">
        <div className="mb-3">
          <Skeleton variant="text" className="w-3/4 h-5 mb-2" />
          <Skeleton variant="text" className="w-1/3 h-4" />
        </div>
        <div className="space-y-2 flex-1">
          <Skeleton variant="text" className="w-full h-3" />
          <Skeleton variant="text" className="w-4/5 h-3" />
        </div>
        <div className="flex gap-1 mt-3">
          <Skeleton variant="rectangular" className="w-14 h-5 rounded" />
          <Skeleton variant="rectangular" className="w-14 h-5 rounded" />
          <Skeleton variant="rectangular" className="w-14 h-5 rounded" />
        </div>
        <div className="flex items-center justify-between pt-3 mt-3 border-t border-[hsl(var(--border-color))]">
          <Skeleton variant="text" className="w-16 h-4" />
          <div className="flex gap-2">
            <Skeleton variant="rectangular" className="w-14 h-8 rounded" />
            <Skeleton variant="rectangular" className="w-14 h-8 rounded" />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Grid component for browsing and searching inspirations
 */
export function InspirationGrid({
  projectType,
  initialSearch = "",
  featuredOnly = false,
  pageSize = 12,
}: InspirationGridProps) {
  const { t } = useTranslation("inspirations");
  const inspirationUpgradePrompt = getUpgradePromptDefinition("inspiration_copy_quota_blocked");
  const projectQuotaUpgradePrompt = getUpgradePromptDefinition("project_quota_blocked");
  const [searchInput, setSearchInput] = useState(initialSearch);
  const [page, setPage] = useState(1);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [upgradeModalVariant, setUpgradeModalVariant] = useState<"inspiration_copy" | "project_quota">(
    "inspiration_copy"
  );

  // Debounce search input before passing to API
  const debouncedSearch = useDebouncedValue(searchInput, 300);

  const {
    inspirations,
    total,
    isLoading,
    isFetching,
    error,
    currentDetail,
    isCopying,
    getDetail,
    copyInspiration,
  } = useInspirations({
    projectType,
    search: debouncedSearch,
    page,
    pageSize,
    featuredOnly,
  });
  const isGridLoading = isLoading || (isFetching && inspirations.length === 0);

  const totalPages = Math.ceil(total / pageSize);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1); // Reset to first page on new search
  };

  const handleViewInspiration = async (id: string) => {
    const detail = await getDetail(id);
    if (detail) setIsDialogOpen(true);
  };

  const handleCopyInspiration = async (
    id: string,
    projectName?: string,
    throwOnError: boolean = false
  ) => {
    try {
      const result = await copyInspiration(id, projectName);
      if (result.success) {
        toast.success(t("copySuccess"));
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        toast.error(t("copyAuthRequired"));
      } else if (error instanceof ApiError && error.status === 402) {
        if (error.errorCode === "ERR_QUOTA_PROJECTS_EXCEEDED") {
          toast.error(error.message);
          if (projectQuotaUpgradePrompt.surface === "modal") {
            setUpgradeModalVariant("project_quota");
            setShowUpgradeModal(true);
          }
        } else {
          toast.error(t("copyQuotaExceeded"));
          if (inspirationUpgradePrompt.surface === "modal") {
            setUpgradeModalVariant("inspiration_copy");
            setShowUpgradeModal(true);
          }
        }
      } else {
        toast.error(t("copyError"));
      }
      if (throwOnError) {
        throw error;
      }
    }
  };

  const handleDialogCopy = async (id: string, projectName?: string) => {
    await handleCopyInspiration(id, projectName, true);
  };

  const handleCloseDialog = () => {
    setIsDialogOpen(false);
  };

  if (error) {
    return (
      <div className="text-center py-12 bg-[hsl(var(--bg-secondary))] rounded-2xl border border-[hsl(var(--border-color))]">
        <p className="text-[hsl(var(--error))]">{t("loadError")}</p>
        <button
          onClick={() => window.location.reload()}
          className="btn-primary mt-4 px-4 h-10 rounded-xl"
        >
          {t("retry")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Search Bar */}
      <form onSubmit={handleSearch} className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-secondary))]" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => {
              setSearchInput(e.target.value);
              setPage(1);
            }}
            placeholder={t("searchPlaceholder")}
            className="h-10 w-full rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]
                       pl-10 pr-4 text-sm text-[hsl(var(--text-primary))]
                       placeholder:text-[hsl(var(--text-secondary))]
                       focus:border-[hsl(var(--accent-primary)/0.6)] focus:outline-none
                       focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.2)]"
          />
        </div>
        <button
          type="submit"
          className="btn-primary h-10 rounded-xl px-4 sm:px-5"
        >
          {t("search")}
        </button>
      </form>

      {/* Loading State */}
      {isGridLoading && (
        <div
          className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
        >
          {Array.from({ length: pageSize }).map((_, index) => (
            <InspirationCardSkeleton key={index} />
          ))}
        </div>
      )}

      {/* Empty State */}
      {!isGridLoading && inspirations.length === 0 && (
        <div className="rounded-2xl border border-[hsl(var(--border-color))]
                        bg-[hsl(var(--bg-secondary))] py-16 text-center">
          <Filter className="w-12 h-12 text-[hsl(var(--text-secondary))] mx-auto mb-4 opacity-50" />
          <p className="text-[hsl(var(--text-secondary))]">{t("noResults")}</p>
        </div>
      )}

      {/* Inspiration Grid */}
      {!isGridLoading && inspirations.length > 0 && (
        <>
          <div className="text-sm text-[hsl(var(--text-secondary))]">
            {t("resultsCount", { count: total })}
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {inspirations.map((inspiration) => (
              <InspirationCard
                key={inspiration.id}
                inspiration={inspiration}
                onView={handleViewInspiration}
                onCopy={(id) => {
                  void handleCopyInspiration(id);
                }}
                isCopying={isCopying}
              />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-6">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                aria-label={t("pagination.previous")}
                className="h-9 w-9 rounded-lg border border-[hsl(var(--border-color))]
                          bg-[hsl(var(--bg-secondary))] hover:bg-[hsl(var(--bg-tertiary))]
                          disabled:opacity-50 disabled:cursor-not-allowed transition-colors
                          text-[hsl(var(--text-primary))] flex items-center justify-center"
              >
                <ChevronLeft className="w-5 h-5" />
              </button>
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t("page", { current: page, total: totalPages })}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                aria-label={t("pagination.next")}
                className="h-9 w-9 rounded-lg border border-[hsl(var(--border-color))]
                          bg-[hsl(var(--bg-secondary))] hover:bg-[hsl(var(--bg-tertiary))]
                          disabled:opacity-50 disabled:cursor-not-allowed transition-colors
                          text-[hsl(var(--text-primary))] flex items-center justify-center"
              >
                <ChevronRight className="w-5 h-5" />
              </button>
            </div>
          )}
        </>
      )}

      {/* Detail Dialog */}
      <InspirationDetailDialog
        inspiration={currentDetail}
        isOpen={isDialogOpen}
        onClose={handleCloseDialog}
        onCopy={handleDialogCopy}
        isCopying={isCopying}
      />

      {(() => {
        const prompt =
          upgradeModalVariant === "project_quota" ? projectQuotaUpgradePrompt : inspirationUpgradePrompt;

        const modalCopy =
          upgradeModalVariant === "project_quota"
            ? {
                title: t("projectQuotaExceededTitle"),
                description: t("projectQuotaExceededModalDesc"),
              }
            : {
                title: t("copyQuotaExceededTitle"),
                description: t("copyQuotaExceededModalDesc"),
              };

        return (
      <UpgradePromptModal
        open={showUpgradeModal}
        onClose={() => setShowUpgradeModal(false)}
        source={prompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={modalCopy.title}
        description={modalCopy.description}
        primaryLabel={t("copyQuotaUpgradePrimary")}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(prompt.billingPath, prompt.source)
          );
        }}
        secondaryLabel={t("copyQuotaUpgradeSecondary")}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(prompt.pricingPath, prompt.source)
          );
        }}
      />
        );
      })()}
    </div>
  );
}
