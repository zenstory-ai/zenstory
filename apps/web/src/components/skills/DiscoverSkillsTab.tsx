import { useTranslation } from "react-i18next";
import { Compass, Plus } from "../icons";
import type { PublicSkill, SkillCategory } from "../../types";
import { DashboardEmptyState } from "../dashboard/DashboardEmptyState";
import { DashboardFilterPills } from "../dashboard/DashboardFilterPills";
import { DashboardSearchBar } from "../dashboard/DashboardSearchBar";
import { Button } from "../ui/Button";
import { PublicSkillCard } from "./PublicSkillCard";
import { getLocalizedSkillCategory } from "./skillHelpers";
import { SkillListError } from "./SkillSharedPrimitives";

export interface DiscoverSkillsTabProps {
  publicSkills: PublicSkill[];
  categories: SkillCategory[];
  categoriesError?: boolean;
  onRetryCategories?: () => void;
  selectedCategory: string;
  setSelectedCategory: (cat: string) => void;
  searchQuery: string;
  /** The debounced query the current results were loaded with. */
  appliedSearchQuery: string;
  setSearchQuery: (q: string) => void;
  onResetFilters: () => void;
  onSearchCompositionChange: (composing: boolean) => void;
  loading: boolean;
  hasError: boolean;
  onRetry: () => void;
  loadingMore: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  addingId: string | null;
  addedPublicIds: Set<string>;
  onAdd: (id: string) => void;
  onCreate: () => void;
  isMobile?: boolean;
}

export function DiscoverSkillsTab({
  publicSkills,
  categories,
  categoriesError,
  onRetryCategories,
  selectedCategory,
  setSelectedCategory,
  searchQuery,
  appliedSearchQuery,
  setSearchQuery,
  onResetFilters,
  onSearchCompositionChange,
  loading,
  hasError,
  onRetry,
  loadingMore,
  hasMore,
  onLoadMore,
  addingId,
  addedPublicIds,
  onAdd,
  onCreate,
  isMobile,
}: DiscoverSkillsTabProps) {
  const { t } = useTranslation(["skills", "common"]);

  const categoryOptions = [
    { value: "" as string, label: t("allCategories") },
    ...categories.map((cat) => ({
      value: cat.name,
      label: getLocalizedSkillCategory(cat.name, t),
    })),
  ];

  return (
    <div>
      {/* Search and Filter */}
      <div className={`flex gap-3 mb-6 ${isMobile ? "flex-col" : "flex-row"}`}>
        <DashboardSearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          onCompositionStart={() => onSearchCompositionChange(true)}
          onCompositionEnd={(e) => {
            onSearchCompositionChange(false);
            setSearchQuery(e.currentTarget.value);
          }}
          placeholder={t("searchPlaceholder")}
          className="flex-1"
          inputTestId="public-skill-search"
        />
        {categoriesError && categories.length === 0 ? (
          <div role="alert" className="flex items-center gap-2 text-xs text-[hsl(var(--error-text))]">
            <span>{t("skills:errors.categoriesLoadFailed")}</span>
            {onRetryCategories && (
              <button
                type="button"
                onClick={onRetryCategories}
                className="underline hover:no-underline"
              >
                {t("common:retry")}
              </button>
            )}
          </div>
        ) : (
          <DashboardFilterPills
            options={categoryOptions}
            value={selectedCategory}
            onChange={setSelectedCategory}
            testId="discover-category-filters"
            className={isMobile ? "self-start" : "self-center"}
          />
        )}
      </div>

      {hasError && <SkillListError onRetry={onRetry} />}

      {/* Skills Grid */}
      {loading ? (
        <div role="status" className="flex items-center justify-center h-64">
          <div aria-hidden="true" className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
          <span className="sr-only">{t("common:loading")}</span>
        </div>
      ) : hasError && publicSkills.length === 0 ? null : publicSkills.length === 0 ? (
        // Decide on the query these results came from, not the text still in its debounce window.
        appliedSearchQuery.trim() || selectedCategory ? (
          <DashboardEmptyState
            icon={Compass}
            title={t("noSkillsFound")}
            action={
              <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={onResetFilters}>
                {t("skills:clearFilters")}
              </Button>
            }
          />
        ) : (
          // Nothing was searched: the library itself is empty, not a failed search.
          <DashboardEmptyState
            icon={Compass}
            title={t("noDiscoverableSkills")}
            description={t("noDiscoverableSkillsHint")}
            action={
              <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={onCreate} leftIcon={<Plus className="w-4 h-4" />}>
                {t("createSkill")}
              </Button>
            }
          />
        )
      ) : (
        <>
          {publicSkills.some((skill) => skill.source === "official") && (
            <p className="mb-4 text-sm text-[hsl(var(--text-secondary))]">{t("officialUsageHint")}</p>
          )}
          <div className={`grid gap-4 ${isMobile ? "grid-cols-1" : "grid-cols-2 lg:grid-cols-3"}`}>
            {publicSkills.map((skill) => (
              <PublicSkillCard
                key={skill.id}
                skill={skill}
                isAdded={addedPublicIds.has(skill.id) || !!skill.is_added}
                adding={addingId === skill.id}
                onAdd={() => onAdd(skill.id)}
                isMobile={isMobile}
              />
            ))}
          </div>
        </>
      )}

      {!loading && !hasError && hasMore && (
        <div className="flex justify-center mt-6">
          <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={onLoadMore} isLoading={loadingMore}>
            {t("loadMore")}
          </Button>
        </div>
      )}
    </div>
  );
}
