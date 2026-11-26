import { useTranslation } from "react-i18next";
import { CheckSquare, Plus, Search, Square, Trash2 } from "../icons";
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

  const hasSkills = userSkills.length > 0 || addedSkills.length > 0;
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
      {/* Search Box — progressive disclosure: hidden when there are 0 skills and no active search */}
      {(hasSkills || Boolean(searchQuery)) && (
        <DashboardSearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t("searchMySkillsPlaceholder")}
          className="mb-6"
        />
      )}

      {hasError && <SkillListError onRetry={onRetry} />}
      {(!hasError || hasSkills) && (
        <>
          {/* Batch Action Bar */}
          {(userSkills.length > 0 || addedSkills.length > 0) && (
            <div className={`mb-4 flex ${isMobile ? "flex-col items-start gap-2" : "items-center justify-between"}`}>
              <div className="flex items-center gap-3 flex-wrap">
                <Button
                  type="button"
                  variant="ghost"
                  size={isMobile ? "touch" : "sm"}
                  onClick={onSelectAll}
                  leftIcon={
                    selectedSkillIds.size === userSkills.length + addedSkills.length && selectedSkillIds.size > 0 ? (
                      <CheckSquare className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
                    ) : (
                      <Square className="w-4 h-4" />
                    )
                  }
                >
                  {t("batch.selectAll")}
                </Button>
                {selectedSkillIds.size > 0 && (
                  <span className="text-sm text-[hsl(var(--text-tertiary))]">
                    {t("batch.selected", { count: selectedSkillIds.size })}
                  </span>
                )}
              </div>
              {selectedSkillIds.size > 0 && (
                <div className={`flex items-center gap-2 ${isMobile ? "w-full flex-wrap" : ""}`}>
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
              )}
            </div>
          )}

          {/* User Skills */}
          <div className="mb-7">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t("userSkills")}</h2>
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]">
                  {userSkills.length}
                </span>
              </div>
              <Button
                size={isMobile ? "touch" : "md"}
                onClick={onCreate}
                leftIcon={<Plus className="w-4 h-4" />}
              >
                {t("create")}
              </Button>
            </div>

            {userSkills.length === 0 ? (
              <DashboardEmptyState
                icon={Search}
                title={searchQuery ? t("noSearchResults") : t("noUserSkills")}
                description={searchQuery ? undefined : t("noUserSkillsHint")}
                action={
                  searchQuery ? (
                    <Button variant="secondary" size={isMobile ? "touch" : "sm"} onClick={() => setSearchQuery("")}>
                      {t("common:clearSearch")}
                    </Button>
                  ) : (
                    <Button size={isMobile ? "touch" : "md"} onClick={onCreate}>
                      {t("createFirst")}
                    </Button>
                  )
                }
              />
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
                icon={Search}
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
