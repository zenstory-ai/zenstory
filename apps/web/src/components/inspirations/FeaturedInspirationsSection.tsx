import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Compass } from "../icons";
import { DashboardEmptyState } from "../dashboard/DashboardEmptyState";
import { useFeaturedInspirations } from "../../hooks/useInspirations";

interface FeaturedInspirationsSectionProps {
  isMobile: boolean;
  isTablet: boolean;
}

export function FeaturedInspirationsSection({
  isMobile,
  isTablet,
}: FeaturedInspirationsSectionProps) {
  const { t } = useTranslation("dashboard");
  const navigate = useNavigate();
  const {
    featured: featuredInspirations,
    isLoading,
    isFetching,
  } = useFeaturedInspirations(3);
  const showLoading = isLoading || (isFetching && featuredInspirations.length === 0);
  const skeletonCount = isMobile ? 1 : isTablet ? 2 : 3;
  const gridClassName = `grid ${
    isMobile ? "grid-cols-1" : isTablet ? "grid-cols-2" : "lg:grid-cols-3"
  } gap-3.5`;

  return (
    <div
      className="mb-7"
      data-testid="featured-inspirations-section"
    >
      <div
        className="flex items-center justify-between mb-4"
      >
        <div className="flex items-center gap-2">
          <Compass className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t("inspirations.featured")}
          </h2>
        </div>
        <button
          type="button"
          onClick={() => navigate("/dashboard/inspirations")}
          className="text-xs text-[hsl(var(--accent-primary))] hover:underline"
        >
          {t("inspirations.viewAll")}
        </button>
      </div>

      {showLoading ? (
        <div className={gridClassName} data-testid="featured-inspirations-loading">
          {Array.from({ length: skeletonCount }).map((_, index) => (
            <div
              key={`featured-skeleton-${index}`}
              className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4"
            >
              <div className="h-4 w-2/3 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-3" />
              <div className="h-3 w-full rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-2" />
              <div className="h-3 w-4/5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
            </div>
          ))}
        </div>
      ) : featuredInspirations.length > 0 ? (
        <div className={gridClassName}>
          {featuredInspirations.map((inspiration) => (
            <button
              type="button"
              key={inspiration.id}
              onClick={() => navigate(`/dashboard/inspirations/${inspiration.id}`)}
              className="group w-full text-left bg-[hsl(var(--bg-secondary))] rounded-lg border border-[hsl(var(--border-color))] cursor-pointer hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-lg transition-all p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
            >
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center group-hover:scale-110 transition-transform">
                  <Compass className="w-4 h-4 text-blue-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-[hsl(var(--text-primary))] truncate text-sm">
                    {inspiration.name}
                  </h3>
                  {inspiration.description && (
                    <p className="text-xs text-[hsl(var(--text-secondary))] line-clamp-2 mt-1">
                      {inspiration.description}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-2 text-xs text-[hsl(var(--text-secondary))]">
                    <span className="px-2 py-0.5 rounded bg-[hsl(var(--bg-tertiary))]">
                      {t(`projectType.${inspiration.project_type}.name`)}
                    </span>
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <DashboardEmptyState
          icon={Compass}
          title={t("inspirations.emptyTitle")}
          description={t("inspirations.emptyHint")}
          className="py-9 px-4"
          action={(
            <button
              type="button"
              onClick={() => navigate("/dashboard/inspirations")}
              className="text-sm font-medium text-[hsl(var(--accent-primary))] hover:underline"
            >
              {t("inspirations.emptyCta")}
            </button>
          )}
        />
      )}
    </div>
  );
}
