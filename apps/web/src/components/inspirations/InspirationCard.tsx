import { useTranslation } from "react-i18next";
import { Copy, Users } from "../icons";
import { Card } from "../ui/Card";
import type { Inspiration } from "../../types";

interface InspirationCardProps {
  inspiration: Inspiration;
  onCopy?: (id: string) => void;
  onView?: (id: string) => void;
  isCopying?: boolean;
}

/**
 * Card component for displaying an inspiration in grid/list view
 */
export function InspirationCard({
  inspiration,
  onCopy,
  onView,
  isCopying = false,
}: InspirationCardProps) {
  const { t } = useTranslation("inspirations");

  const projectTypeLabels: Record<string, string> = {
    novel: t("projectTypes.novel"),
    short: t("projectTypes.short"),
    screenplay: t("projectTypes.screenplay"),
  };
  const mediaToneClasses: Record<string, string> = {
    novel: "from-[hsl(var(--accent-primary)/0.22)] via-[hsl(var(--bg-tertiary))] to-[hsl(var(--accent-light)/0.18)]",
    short: "from-emerald-500/18 via-[hsl(var(--bg-tertiary))] to-cyan-500/14",
    screenplay: "from-amber-500/18 via-[hsl(var(--bg-tertiary))] to-rose-500/14",
  };
  const mediaHintByType: Record<string, string> = {
    novel: t("heroHints.novel"),
    short: t("heroHints.short"),
    screenplay: t("heroHints.screenplay"),
  };
  const projectTypeLabel = projectTypeLabels[inspiration.project_type] || inspiration.project_type;
  const fallbackPreview = inspiration.description?.trim() || mediaHintByType[inspiration.project_type] || mediaHintByType.novel;
  const mediaPreview = fallbackPreview.length > 40 ? `${fallbackPreview.slice(0, 40)}…` : fallbackPreview;
  const isCommunity = inspiration.source === "community";
  const hasCoverImage = Boolean(inspiration.cover_image);
  const mediaToneClass = mediaToneClasses[inspiration.project_type] || mediaToneClasses.novel;

  return (
    <Card
      className="group flex h-full flex-col overflow-hidden rounded-2xl border border-[hsl(var(--border-color))]
                 bg-[hsl(var(--bg-secondary))] transition-all duration-300
                 hover:-translate-y-0.5 hover:border-[hsl(var(--accent-primary)/0.35)]
                 hover:shadow-[0_20px_45px_hsl(0_0%_0%_/_0.35)]"
      padding="none"
      rounded="lg"
    >
      {/* Cover Image */}
      <button
        type="button"
        data-testid={hasCoverImage ? "inspiration-card-media" : "inspiration-card-placeholder"}
        className="relative aspect-[16/10] w-full overflow-hidden border-b border-[hsl(var(--separator-color))]
                   bg-[hsl(var(--bg-tertiary))] text-left focus-visible:outline-none focus-visible:ring-2
                   focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-inset"
        onClick={() => onView?.(inspiration.id)}
        aria-label={t("view") + `：${inspiration.name}`}
      >
        {hasCoverImage ? (
          <>
            <img
              src={inspiration.cover_image ?? ""}
              alt={inspiration.name}
              className="absolute inset-0 h-full w-full object-cover opacity-60 transition-all duration-500
                         group-hover:scale-[1.04] group-hover:opacity-75"
            />
            <div className="pointer-events-none absolute inset-0 bg-black/15" />
          </>
        ) : (
          <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${mediaToneClass}`} />
        )}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/5 via-black/25 to-black/70" />

        <div className="relative flex h-full flex-col justify-between p-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full border border-[hsl(var(--separator-color))] bg-black/30 px-2.5 py-1 font-semibold text-white/90 whitespace-nowrap">
              {projectTypeLabel}
            </span>
            {isCommunity && (
              <span className="inline-flex items-center gap-1 rounded-full border border-[hsl(var(--separator-color))]
                               bg-black/30 px-2.5 py-1 text-white/85 whitespace-nowrap">
                <Users className="h-3 w-3" />
                {t("community")}
              </span>
            )}
            {inspiration.is_featured && (
              <span className="rounded-full border border-yellow-500/40 bg-yellow-500/20 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-yellow-300 whitespace-nowrap">
                {t("featured")}
              </span>
            )}
          </div>

          <div className="space-y-2">
            <p className="line-clamp-2 text-sm font-medium leading-5 text-white/95 break-normal [line-break:strict]">
              {mediaPreview}
            </p>
          </div>
        </div>
      </button>

      {/* Content */}
      <div className="flex flex-1 flex-col gap-3 p-5">
        {/* Header */}
        <div className="space-y-2">
          <div className="flex items-start justify-between gap-2">
            <h3 className="line-clamp-1 text-[15px] font-semibold md:text-base">
              <button
                type="button"
                onClick={() => onView?.(inspiration.id)}
                className="line-clamp-1 text-[hsl(var(--text-primary))] transition-colors hover:text-[hsl(var(--accent-primary))]
                           focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]
                           focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))] rounded"
                aria-label={t("view") + `：${inspiration.name}`}
              >
                {inspiration.name}
              </button>
            </h3>
          </div>
        </div>

        {/* Description */}
        {inspiration.description && (
          <p className="line-clamp-2 flex-1 text-sm text-[hsl(var(--text-secondary))] break-normal [line-break:strict]">
            {inspiration.description}
          </p>
        )}

        {/* Tags */}
        {inspiration.tags.length > 0 && (
          <div className="mb-1 flex flex-wrap gap-1.5">
            {inspiration.tags.slice(0, 3).map((tag, index) => (
              <span
                key={index}
                className="rounded-full bg-[hsl(var(--accent-primary)/0.12)] px-2.5 py-1 text-xs
                           text-[hsl(var(--accent-light))]"
              >
                {tag}
              </span>
            ))}
            {inspiration.tags.length > 3 && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                +{inspiration.tags.length - 3}
              </span>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="mt-auto border-t border-[hsl(var(--separator-color))] pt-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="inline-flex max-w-full items-center gap-1 text-xs text-[hsl(var(--text-secondary))] whitespace-nowrap">
                <Copy className="h-3 w-3 shrink-0" />
                <span className="truncate">{t("copyCount", { count: inspiration.copy_count })}</span>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => onView?.(inspiration.id)}
                className="min-h-[44px] min-w-[44px] whitespace-nowrap rounded-lg bg-[hsl(var(--bg-tertiary))]
                          px-3 py-2 text-sm text-[hsl(var(--text-secondary))] transition-colors
                          hover:text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2
                          focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2
                          focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                aria-label={t("view") + `：${inspiration.name}`}
              >
                {t("view")}
              </button>
              <button
                type="button"
                onClick={() => onCopy?.(inspiration.id)}
                disabled={isCopying}
                className="flex min-h-[44px] min-w-[44px] items-center gap-1 whitespace-nowrap rounded-lg
                          bg-[hsl(var(--accent-primary))] px-4 py-2 text-sm text-[hsl(var(--primary-foreground))]
                          transition-colors hover:bg-[hsl(var(--accent-dark))]
                          focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.4)]
                          disabled:cursor-not-allowed disabled:opacity-50"
                aria-label={(isCopying ? t("copying") : t("use")) + `：${inspiration.name}`}
              >
                <Copy className="h-3.5 w-3.5 shrink-0" />
                {isCopying ? t("copying") : t("use")}
              </button>
            </div>
          </div>
          {/* keep actions and count single-line to avoid awkward CJK wraps */}
        </div>
      </div>
    </Card>
  );
}
