import { useTranslation } from "react-i18next";
import { CheckSquare, Compass, Search, Square, Trash2, Zap } from "../icons";
import type { AddedSkill, Skill } from "../../types";
import { DashboardEmptyState } from "../dashboard/DashboardEmptyState";
import { DashboardSearchBar } from "../dashboard/DashboardSearchBar";
import { Button } from "../ui/Button";
import { AddedSkillCard, SkillCard } from "./SkillCard";
import { SkillListError } from "./SkillSharedPrimitives";

export interface MySkillsTabProps {
  userSkills: Skill[];
  addedSkills: AddedSkill[];
  onEdit: (skill: Skill) => void;
  onDelete: (id: string) => void;
  onShare: (skill: Skill) => void;
  onExport: (id: string, name: string) => void;
  exportingId: string | null;
  onCreate: () => void;
  onRemoveAdded: (publicSkillId: string) => void;
  removingId: string | null;
  isMobile?: boolean;
  onSwitchToDiscover: () => void;
  loading?: boolean;
  hasLoaded?: boolean;
  hasError: boolean;
  onRetry: () => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  selectedSkillIds: Set<string>;
  onToggleSelect: (id: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onBatchDelete: () => void;
}

export function MySkillsTab({
  userSkills,
  addedSkills,
  onEdit,
  onDelete,
  onShare,
  onExport,
  exportingId,
  onCreate,
  onRemoveAdded,
  removingId,
  isMobile,
  onSwitchToDiscover,
  loading,
  hasLoaded,
  hasError,
  onRetry,
  searchQuery,
  setSearchQuery,
  selectedSkillIds,
  onToggleSelect,
  onSelectAll,
  onClearSelection,
  onBatchDelete,
}: MySkillsTabProps) {
  const { t } = useTranslation(["skills", "common"]);

  const visibleSkillIds = [...userSkills.map((s) => s.id), ...addedSkills.map((s) => s.id)];
  const hasSkills = visibleSkillIds.length > 0;
  const allSelected = hasSkills && visibleSkillIds.every((id) => selectedSkillIds.has(id));

  if ((loading || !hasLoaded) && !hasSkills) {
    return (
      <div role="status" className="flex items-center justify-center h-64">
        <div aria-hidden="true" className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
        <span className="sr-only">{t("common:loading")}</span>
      </div>
    );
  }

  return (
    <div>
      {/* Search + Select All toolbar & contextual batch bar — hidden when 0 skills and no active search */}
      {(hasSkills || Boolean(searchQuery)) && (
        <div className="mb-6">
          <div className={`flex gap-3 ${isMobile ? "flex-col items-stretch" : "items-center"}`}>
            <DashboardSearchBar
              value={searchQuery}
              onChange={setSearchQuery}
              placeholder={t("searchMySkillsPlaceholder")}
              className="flex-1"
            />
            {hasSkills && (
              <Button
                type="button"
                variant="secondary"
                size={isMobile ? "touch" : "md"}
                onClick={onSelectAll}
                aria-pressed={allSelected}
                className={`shrink-0 whitespace-nowrap ${isMobile ? "self-start" : ""}`}
                leftIcon={
                  allSelected ? (
                    <CheckSquare className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
                  ) : (
                    <Square className="w-4 h-4 text-[hsl(var(--text-secondary))]" />
                  )
                }
              >
                {t("batch.selectAll")}
              </Button>
            )}
          </div>

          {hasSkills && !hasError && selectedSkillIds.size > 0 && (
            <div
              data-testid="skills-selection-bar"
              role="region"
              aria-label={t("batch.selected", { count: selectedSkillIds.size })}
              className={`mt-3 rounded-xl border border-[hsl(var(--accent-primary)/0.3)] bg-[hsl(var(--accent-primary)/0.06)] px-3.5 py-2.5 flex gap-3 ${
                isMobile ? "flex-col items-stretch" : "items-center justify-between"
              }`}
            >
              <div className="flex items-center gap-2 text-sm font-medium text-[hsl(var(--text-primary))] tabular-nums">
                <CheckSquare aria-hidden="true" className="w-4 h-4 text-[hsl(var(--accent-primary))] shrink-0" />
                <span>{t("batch.selected", { count: selectedSkillIds.size })}</span>
              </div>
              <div className={`flex items-center gap-2 ${isMobile ? "flex-wrap" : "shrink-0"}`}>
                <Button
                  type="button"
                  variant="ghost"
                  size={isMobile ? "touch" : "sm"}
                  onClick={onClearSelection}
                >
                  {t("batch.clearSelection")}
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  size={isMobile ? "touch" : "sm"}
                  onClick={onBatchDelete}
                  leftIcon={<Trash2 className="w-3.5 h-3.5" />}
                >
                  {t("batch.delete")}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {hasError && <SkillListError onRetry={onRetry} />}
      {(!hasError || hasSkills) && (
        <>
          {/* User Skills */}
          <div className="mb-7">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t("userSkills")}</h2>
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]">
                  {userSkills.length}
                </span>
              </div>
            </div>

            {userSkills.length === 0 ? (
              searchQuery ? (
                <DashboardEmptyState
                  icon={Search}
                  title={t("noSearchResults")}
                  action={
                    <Button variant="secondary" size={isMobile ? "touch" : "sm"} onClick={() => setSearchQuery("")}>
                      {t("common:clearSearch")}
                    </Button>
                  }
                />
              ) : addedSkills.length > 0 ? (
                <div
                  data-testid="empty-user-skills-compact"
                  className={`rounded-2xl border border-dashed border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] ${
                    isMobile ? "p-4 flex flex-col items-start gap-3" : "px-5 py-4 flex items-center justify-between gap-4"
                  }`}
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <div className="w-9 h-9 rounded-lg bg-[hsl(var(--accent-primary)/0.08)] flex items-center justify-center shrink-0 mt-0.5">
                      <Zap aria-hidden="true" className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("noUserSkills")}
                      </h3>
                      <p className="mt-0.5 text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
                        {t("noUserSkillsHint")}
                      </p>
                    </div>
                  </div>
                  <Button
                    variant="secondary"
                    size={isMobile ? "touch" : "sm"}
                    onClick={onCreate}
                    className="shrink-0"
                  >
                    {t("createFirst")}
                  </Button>
                </div>
              ) : (
                <DashboardEmptyState
                  icon={Zap}
                  title={t("noUserSkills")}
                  description={t("noUserSkillsHint")}
                  action={
                    <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={onCreate}>
                      {t("createFirst")}
                    </Button>
                  }
                />
              )
            ) : (
              <div className="grid gap-3 grid-cols-1">
                {userSkills.map((skill) => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    onEdit={() => onEdit(skill)}
                    onDelete={() => onDelete(skill.id)}
                    onShare={() => onShare(skill)}
                    onExport={() => onExport(skill.id, skill.name)}
                    exporting={exportingId === skill.id}
                    isMobile={isMobile}
                    isSelected={selectedSkillIds.has(skill.id)}
                    onToggleSelect={() => onToggleSelect(skill.id)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Added Skills */}
          <div className="mb-7">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t("addedSkills")}</h2>
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                  {addedSkills.length}
                </span>
              </div>
              <Button variant="ghost" size={isMobile ? "touch" : "sm"} onClick={onSwitchToDiscover}>
                {t("discoverMore")}
              </Button>
            </div>

            {addedSkills.length === 0 ? (
              <DashboardEmptyState
                icon={searchQuery ? Search : Compass}
                title={searchQuery ? t("noSearchResults") : t("noAddedSkills")}
                action={
                  searchQuery ? (
                    <Button variant="secondary" size={isMobile ? "touch" : "sm"} onClick={() => setSearchQuery("")}>
                      {t("common:clearSearch")}
                    </Button>
                  ) : (
                    <Button variant="secondary" size={isMobile ? "touch" : "md"} onClick={onSwitchToDiscover}>
                      {t("browsePublic")}
                    </Button>
                  )
                }
              />
            ) : (
              <div className="grid gap-3 grid-cols-1">
                {addedSkills.map((skill) => (
                  <AddedSkillCard
                    key={skill.id}
                    skill={skill}
                    onRemove={() => onRemoveAdded(skill.public_skill_id)}
                    removing={removingId === skill.public_skill_id}
                    onExport={() => onExport(skill.id, skill.name)}
                    exporting={exportingId === skill.id}
                    isMobile={isMobile}
                    isSelected={selectedSkillIds.has(skill.id)}
                    onToggleSelect={() => onToggleSelect(skill.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
