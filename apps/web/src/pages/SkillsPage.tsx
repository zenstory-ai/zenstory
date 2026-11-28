import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { Compass, Plus, Upload } from "../components/icons";
import { useIsMobile } from "../hooks/useMediaQuery";
import { ShareSkillModal } from "../components/skills/ShareSkillModal";
import { MySkillsTab } from "../components/skills/MySkillsTab";
import { DiscoverSkillsTab } from "../components/skills/DiscoverSkillsTab";
import { SkillEditorModal } from "../components/skills/SkillEditorModal";
import { SkillImportResultModal } from "../components/skills/SkillImportResultModal";
import { useSkillsPageState } from "../components/skills/useSkillsPageState";
import type { SkillsTabType } from "../components/skills/skillHelpers";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { DashboardFilterPills } from "../components/dashboard/DashboardFilterPills";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { UpgradePromptModal } from "../components/subscription/UpgradePromptModal";

export default function SkillsPage() {
  const { t } = useTranslation(["skills", "common"]);
  const isMobile = useIsMobile();
  const importInputRef = useRef<HTMLInputElement>(null);
  const state = useSkillsPageState();

  return (
    <div className="flex flex-col">
      {/* Header */}
      <DashboardPageHeader
        title={t("title")}
        subtitle={t("description")}
        action={
          <div className={`flex items-center gap-2 ${isMobile ? "w-full flex-wrap" : ""}`}>
            <input
              ref={importInputRef}
              type="file"
              accept=".zip,.md"
              className="hidden"
              onChange={state.handleImportFile}
              data-testid="skill-import-input"
            />
            <Button
              variant="ghost"
              size={isMobile ? "touch" : "md"}
              onClick={() => importInputRef.current?.click()}
              isLoading={state.importing}
              leftIcon={<Upload className="w-4 h-4" />}
              title={t("skills:import.hint")}
            >
              {t("skills:import.button")}
            </Button>
            <Button
              variant="primary"
              size={isMobile ? "touch" : "md"}
              onClick={state.handleCreate}
              leftIcon={<Plus className="w-4 h-4" />}
            >
              {t("create")}
            </Button>
          </div>
        }
      />

      {/* Tabs */}
      <DashboardFilterPills
        options={[
          { value: "discover", label: t("discoverTab"), icon: Compass },
          { value: "my-skills", label: t("mySkillsTab") },
        ]}
        value={state.activeTab}
        onChange={(value) => state.setActiveTab(value as SkillsTabType)}
        className="mb-6 self-start"
      />

      {/* Tab Content */}
      {state.activeTab === "my-skills" ? (
        <MySkillsTab
          userSkills={state.userSkills}
          addedSkills={state.addedSkills}
          onEdit={state.handleEdit}
          onDelete={state.setDeletingId}
          onShare={state.setSharingSkill}
          onExport={state.handleExport}
          exportingId={state.exportingId}
          onCreate={state.handleCreate}
          onRemoveAdded={state.handleRemoveAdded}
          removingId={state.removingId}
          isMobile={isMobile}
          onSwitchToDiscover={() => state.setActiveTab("discover")}
          loading={state.loading}
          hasLoaded={state.hasLoadedMySkills}
          hasError={state.mySkillsError}
          onRetry={() => void state.loadSkills(state.mySkillsSearchQuery, true)}
          searchQuery={state.mySkillsSearchQuery}
          setSearchQuery={state.setMySkillsSearchQuery}
          selectedSkillIds={state.selectedSkillIds}
          onToggleSelect={state.handleToggleSelect}
          onSelectAll={state.handleSelectAll}
          onClearSelection={state.handleClearSelection}
          onBatchDelete={state.handleBatchDelete}
        />
      ) : (
        <DiscoverSkillsTab
          publicSkills={state.publicSkills}
          categories={state.categories}
          categoriesError={state.categoriesError}
          onRetryCategories={() => void state.loadCategories()}
          selectedCategory={state.selectedCategory}
          setSelectedCategory={state.setSelectedCategory}
          searchQuery={state.searchInput}
          appliedSearchQuery={state.searchQuery}
          setSearchQuery={state.setSearchInput}
          onResetFilters={state.handleResetDiscoverFilters}
          onSearchCompositionChange={state.setIsSearchComposing}
          loading={state.discoverLoading}
          hasError={state.discoverError}
          onRetry={() => void state.loadPublicSkills(1)}
          loadingMore={state.loadingMorePublic}
          hasMore={state.hasMorePublic}
          onLoadMore={() => void state.handleLoadMorePublic()}
          addingId={state.addingId}
          addedPublicIds={state.addedPublicIds}
          onAdd={state.handleAddPublicSkill}
          onCreate={state.handleCreate}
          isMobile={isMobile}
        />
      )}

      {/* Create/Edit Modal */}
      <SkillEditorModal
        open={state.isCreating}
        editingSkill={state.editingSkill}
        formData={state.formData}
        onFormChange={state.setFormData}
        saving={state.saving}
        onCancel={state.handleCancel}
        onSave={state.handleSave}
        onResourcesChange={() => void state.loadSkills()}
        isMobile={isMobile}
      />

      {/* Import Result */}
      <SkillImportResultModal
        result={state.importResult}
        onClose={() => state.setImportResult(null)}
      />

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!state.deletingId}
        onClose={() => {
          if (!state.deletingSkill) state.setDeletingId(null);
        }}
        onConfirm={() => state.handleDelete(state.deletingId!)}
        title={t("skills:deleteConfirm.title")}
        message={t("skills:deleteConfirm.message")}
        variant="danger"
        loading={state.deletingSkill}
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
      />

      {/* Batch Delete Confirmation */}
      <ConfirmDialog
        open={state.batchDeleting}
        onClose={() => {
          if (!state.batchOperating) state.setBatchDeleting(false);
        }}
        onConfirm={state.handleConfirmBatchDelete}
        title={t("skills:batch.deleteConfirmTitle")}
        message={t("skills:batch.deleteConfirmMessage", { count: state.selectedSkillIds.size })}
        variant="danger"
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
        loading={state.batchOperating}
      />

      {/* Share Skill Modal */}
      {state.isShareModalMounted && state.renderedSharingSkill && (
        <ShareSkillModal
          open={Boolean(state.sharingSkill)}
          skill={state.renderedSharingSkill}
          onClose={() => state.setSharingSkill(null)}
          onSuccess={state.loadSkills}
          isMobile={isMobile}
        />
      )}

      <UpgradePromptModal
        open={state.showSkillCreateUpgradeModal}
        onClose={() => state.setShowSkillCreateUpgradeModal(false)}
        promptId="skill_create_quota_blocked"
        title={t("skills:quota.createTitle", { defaultValue: "自定义技能数量已达上限" })}
        description={t("skills:quota.createDescription", {
          defaultValue: "删掉不用的技能就能腾出名额，或开通 Pro 建更多技能。",
        })}
        paidDescription={t("skills:quota.createPaidDescription", { defaultValue: "删掉不用的技能就能腾出名额。" })}
        primaryLabel={t("skills:quota.upgradePrimary", { defaultValue: "开通 Pro" })}
        secondaryLabel={t("skills:quota.upgradeSecondary", { defaultValue: "查看套餐对比" })}
      />
    </div>
  );
}
