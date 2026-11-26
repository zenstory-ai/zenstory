import { useTranslation } from "react-i18next";
import { Upload } from "../components/icons";
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { MaterialsUpgradePromptModal } from "../components/subscription/MaterialsUpgradePrompt";
import { RedeemCodeModal } from "../components/subscription/RedeemCodeModal";
import { MaterialErrorAlert, MaterialLibrarySection } from "../components/materials/MaterialLibrarySection";
import { MaterialTeaserView } from "../components/materials/MaterialTeaserView";
import { MaterialUploadModal } from "../components/materials/MaterialUploadModal";
import { useMaterialsPageState } from "../components/materials/useMaterialsPageState";

export default function MaterialsPage() {
  const { t } = useTranslation(["materials", "common"]);
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const showDeleteAction = isMobile || isTablet;
  /** Page and card actions: 40px on desktop, 44px touch target on phones. */
  const actionSize = isMobile ? "touch" : "md";
  const state = useMaterialsPageState();

  if (state.isSubscriptionError) {
    return (
      <>
        <DashboardPageHeader
          title={t("materials:title")}
          subtitle={t("materials:description")}
        />
        <MaterialErrorAlert
          title={t("common:error", { defaultValue: "加载失败" })}
          message={t("materials:statusLoadError", {
            defaultValue: "没能确认你的 Pro 状态，请重试。",
          })}
          actionSize={actionSize}
          onRetry={() => {
            void state.refetchSubscriptionStatus();
          }}
        />
      </>
    );
  }

  if (!state.isSubscriptionLoading && state.materialsAccess === null) {
    return (
      <>
        <DashboardPageHeader
          title={t("materials:title")}
          subtitle={t("materials:description")}
        />
        <MaterialErrorAlert
          message={t("materials:statusLoadError", {
            defaultValue: "没能确认你的 Pro 状态，请重试。",
          })}
          actionSize={actionSize}
          onRetry={() => {
            void state.refetchSubscriptionStatus();
          }}
        />
      </>
    );
  }

  return (
    <>
      {/* Header */}
      <DashboardPageHeader
        title={t("materials:title")}
        subtitle={
          state.hasWorkspaceAccess
            ? t("materials:paidSubtitle", {
                defaultValue: "上传参考小说，拆出角色、世界观和章节梗概，写作时随时引用",
              })
            : t("materials:teaserSubtitle", {
                defaultValue: "素材库是 Pro 功能：把参考小说拆成章节梗概、角色和设定，写作时随时引用。",
              })
        }
        action={
          state.hasWorkspaceAccess ? (
            <Button
              size={actionSize}
              onClick={() => state.setShowUploadModal(true)}
              disabled={state.isMaterialsQuotaExhausted}
              title={state.isMaterialsQuotaExhausted ? state.exhaustedMessage : undefined}
              leftIcon={<Upload className="w-4 h-4" />}
            >
              {state.isMaterialsQuotaExhausted
                ? t("materials:quota.decomposeTitle", {
                    defaultValue: "本月拆解次数已用完",
                  })
                : t("materials:upload")}
            </Button>
          ) : state.showHeaderUpgrade ? (
            <Button
              variant="primary"
              size={actionSize}
              onClick={() => state.openUpgradePath("billing")}
              data-testid="materials-header-upgrade"
            >
              {state.proEntry.label}
            </Button>
          ) : undefined
        }
      />

      {state.proEntry.checkoutOff && !state.hasWorkspaceAccess && (
        <p
          data-testid="materials-checkout-unavailable"
          className="mb-4 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-sm text-[hsl(var(--text-secondary))]"
        >
          {state.checkoutUnavailableText}
        </p>
      )}

      {state.isMaterialsLoading ? (
        <div role="status" className="flex items-center justify-center h-64">
          <div aria-hidden="true" className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
          <span className="sr-only">{t("common:loading", { defaultValue: "加载中…" })}</span>
        </div>
      ) : state.isMaterialsError ? (
        <MaterialErrorAlert
          message={t("materials:listLoadError", { defaultValue: "素材列表加载失败，请重试。" })}
          actionSize={actionSize}
          onRetry={() => {
            void state.refetchMaterials();
          }}
        />
      ) : state.showTeaser ? (
        <MaterialTeaserView
          proDecompositionsLimit={state.proDecompositionsLimit}
          trialAvailable={state.trialAvailable}
          trialMaxChapters={state.materialTrial?.max_chapters}
          trialCharacterLimit={state.trialCharacterLimit}
          proEntryLabel={state.proEntry.label}
          actionSize={actionSize}
          onStartTrial={state.handleStartTrial}
          onOpenUpgrade={(destination) => state.openUpgradePath(destination)}
        />
      ) : (
        <MaterialLibrarySection
          materials={state.materials}
          hasWorkspaceAccess={state.hasWorkspaceAccess}
          trialUsed={state.trialUsed}
          trialMaxChapters={state.materialTrial?.max_chapters}
          proEntryLabel={state.proEntry.label}
          actionSize={actionSize}
          isMobile={isMobile}
          showDeleteAction={showDeleteAction}
          decomposeQuotaLimit={state.materialDecomposeQuota?.limit}
          remainingDecompositions={state.remainingDecompositions}
          isMaterialsQuotaExhausted={state.isMaterialsQuotaExhausted}
          exhaustedMessage={state.exhaustedMessage}
          searchQuery={state.searchQuery}
          onSearchChange={state.setSearchQuery}
          exportingId={state.exportingId}
          retryingId={state.retryingId}
          onOpenUpload={() => state.setShowUploadModal(true)}
          onOpenUpgrade={state.openUpgradePath}
          onCardClick={state.handleCardClick}
          onDelete={state.setDeletingId}
          onExport={state.handleExport}
          onRetry={state.handleRetry}
        />
      )}

      {/* Upload Modal */}
      <MaterialUploadModal
        open={state.showUploadModal}
        file={state.file}
        title={state.title}
        uploading={state.uploading}
        error={state.error}
        trialAvailable={state.trialAvailable}
        trialMaxChapters={state.materialTrial?.max_chapters}
        trialCharacterLimit={state.trialCharacterLimit}
        trialSelection={state.trialSelection}
        fileInputRef={state.fileInputRef}
        onClose={state.handleCloseUploadModal}
        onFileChange={(e) => void state.handleFileChange(e)}
        onSelectFile={state.handleSelectFile}
        onTitleChange={state.setTitle}
        onUpload={() => void state.handleUpload()}
      />

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!state.deletingId}
        onClose={() => state.setDeletingId(null)}
        onConfirm={() => state.handleDelete(state.deletingId!)}
        title={t("materials:deleteConfirm.title")}
        message={t("materials:deleteConfirm.message")}
        variant="danger"
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
        loading={state.isDeleting}
      />

      <MaterialsUpgradePromptModal
        open={state.showMaterialAccessUpgradeModal}
        onClose={() => state.setShowMaterialAccessUpgradeModal(false)}
      />
      <RedeemCodeModal
        isOpen={state.redeemSource !== null}
        onClose={() => state.setRedeemSource(null)}
        source={state.redeemSource ?? undefined}
      />
    </>
  );
}
