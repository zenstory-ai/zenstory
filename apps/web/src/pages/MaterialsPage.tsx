import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Upload, BookOpen, FileText, Clock, CheckCircle, AlertCircle, RefreshCw, Trash2 } from "../components/icons";
import { materialsApi } from "../lib/materialsApi";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import { trackEvent } from "../lib/analytics";
import { toast } from "../lib/toast";
import type { MaterialNovel } from "../lib/materialsApi";
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { DashboardEmptyState } from "../components/dashboard/DashboardEmptyState";
import { Modal } from "../components/ui/Modal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { MaterialsUpgradePromptModal } from "../components/subscription/MaterialsUpgradePrompt";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";
import {
  resolveMaterialUploadErrorMessage,
  validateMaterialUploadFile,
} from "../lib/materialUploadValidation";
import { hasMaterialsLibraryAccess, materialJobErrorText } from "../lib/materialsAccess";
import { MATERIAL_LIBRARY_SUMMARY_QUERY_KEY } from "../hooks/useMaterialLibrary";
import { useRefreshMaterialLibraryOnCompletion } from "../hooks/useMaterialLibraryRefresh";

export default function MaterialsPage() {
  const { t, i18n } = useTranslation(["materials", "common"]);
  const materialUploadUpgradePrompt = getUpgradePromptDefinition("material_upload_quota_blocked");
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const showDeleteAction = isMobile || isTablet;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const teaserTrackedRef = useRef(false);

  const [showUploadModal, setShowUploadModal] = useState(false);
  const [showMaterialAccessUpgradeModal, setShowMaterialAccessUpgradeModal] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

  // Upload modal state
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    data: subscriptionStatus,
    isLoading: isSubscriptionLoading,
    isError: isSubscriptionError,
    refetch: refetchSubscriptionStatus,
  } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
  });

  const subscriptionFeatures = (subscriptionStatus?.features ?? {}) as Record<string, unknown>;
  const materialsAccess = hasMaterialsLibraryAccess(
    subscriptionFeatures,
    subscriptionStatus?.tier,
  );
  const hasWorkspaceAccess = materialsAccess === true;
  const showTeaser = materialsAccess === false;

  const {
    data: quota,
  } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
    enabled: hasWorkspaceAccess,
  });

  const materialDecomposeQuota = quota?.material_decompositions;
  const remainingDecompositions =
    materialDecomposeQuota == null || materialDecomposeQuota.limit === -1
      ? null
      : Math.max(0, materialDecomposeQuota.limit - materialDecomposeQuota.used);
  const isMaterialsQuotaExhausted = Boolean(
    hasWorkspaceAccess &&
      materialDecomposeQuota &&
      materialDecomposeQuota.limit !== -1 &&
      remainingDecompositions === 0,
  );

  // Fetch materials list
  const {
    data: materials = [],
    isLoading,
    isFetching,
    isError: isMaterialsError,
    refetch: refetchMaterials,
  } = useQuery({
    queryKey: ["materials"],
    queryFn: () => materialsApi.list(),
    enabled: hasWorkspaceAccess,
    staleTime: 30 * 1000, // 30 seconds - prevents refetch on tab switch
    // Poll every 3 seconds when there are pending/processing items
    refetchInterval: (query) => {
      const data = query.state.data as MaterialNovel[] | undefined;
      const hasProcessing = data?.some(
        (m) => m.status === "pending" || m.status === "processing"
      );
      return hasProcessing ? 3000 : false;
    },
  });
  const isMaterialsLoading =
    isSubscriptionLoading ||
    (hasWorkspaceAccess && (isLoading || (isFetching && materials.length === 0)));
  useRefreshMaterialLibraryOnCompletion(hasWorkspaceAccess ? materials : undefined);

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (novelId: string) => materialsApi.delete(novelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: MATERIAL_LIBRARY_SUMMARY_QUERY_KEY });
      setDeletingId(null);
    },
    onError: (err) => {
      toast.error(handleApiError(err));
    },
  });

  useEffect(() => {
    if (
      isSubscriptionLoading ||
      isSubscriptionError ||
      !showTeaser ||
      teaserTrackedRef.current
    ) {
      return;
    }

    trackEvent("materials_teaser_exposed", {
      source: "materials_teaser",
    });
    teaserTrackedRef.current = true;
  }, [isSubscriptionError, isSubscriptionLoading, showTeaser]);

  if (isSubscriptionError) {
    return (
      <>
        <DashboardPageHeader
          title={t("materials:title")}
          subtitle={t("materials:description")}
        />
        <div className="rounded-2xl border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-6">
          <div className="space-y-3">
            <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
              {t("common:error", { defaultValue: "加载失败" })}
            </h2>
            <p className="text-sm text-[hsl(var(--text-secondary))]">
              {t("materials:statusLoadError", {
                defaultValue: "订阅权益状态加载失败，请重试后再查看素材库。",
              })}
            </p>
            <button
              onClick={() => {
                void refetchSubscriptionStatus();
              }}
              className="btn-secondary h-11 px-4"
            >
              {t("common:retry", { defaultValue: "重试" })}
            </button>
          </div>
        </div>
      </>
    );
  }

  if (!isSubscriptionLoading && materialsAccess === null) {
    return (
      <>
        <DashboardPageHeader
          title={t("materials:title")}
          subtitle={t("materials:description")}
        />
        <div className="rounded-2xl border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-6">
          <p className="text-sm text-[hsl(var(--text-secondary))]">
            {t("materials:statusLoadError", {
              defaultValue: "订阅权益状态加载失败，请重试后再查看素材库。",
            })}
          </p>
        </div>
      </>
    );
  }

  const openUpgradePath = (destination: "billing" | "pricing", source = "materials_teaser") => {
    trackEvent("materials_upgrade_clicked", {
      source,
      destination,
    });

    window.location.assign(
      buildUpgradeUrl(
        destination === "billing"
          ? materialUploadUpgradePrompt.billingPath
          : materialUploadUpgradePrompt.pricingPath,
        source,
      ),
    );
  };

  const formatResetAt = (resetAt: string | null | undefined): string | null => {
    if (!resetAt) return null;
    const parsed = new Date(resetAt);
    if (Number.isNaN(parsed.getTime())) return null;
    const locale = i18n?.language?.startsWith("en") ? "en-US" : "zh-CN";
    return new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(parsed);
  };

  const exhaustedMessage = t("materials:quotaExhausted", {
    defaultValue: "本月 {{limit}} 次素材拆解已用完，将于 {{resetAt}} 自动恢复。",
    limit: materialDecomposeQuota?.limit ?? 5,
    resetAt:
      formatResetAt(materialDecomposeQuota?.reset_at) ??
      t("materials:quotaResetFallback", { defaultValue: "下月" }),
  });

  const handleDelete = (id: string) => {
    deleteMutation.mutate(id);
  };

  const handleCardClick = (novelId: string) => {
    navigate(`/materials/${novelId}`);
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      const validationError = await validateMaterialUploadFile(selectedFile, t);
      if (validationError) {
        setFile(null);
        setError(validationError);
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
        return;
      }

      setFile(selectedFile);
      if (!title) {
        setTitle(selectedFile.name.replace(/\.[^/.]+$/, ""));
      }
      setError(null);
    }
  };

  const handleSelectFile = () => {
    fileInputRef.current?.click();
  };

  const handleUpload = async () => {
    if (!file || isMaterialsQuotaExhausted) return;

    setUploading(true);
    setError(null);

    try {
      await materialsApi.upload(file, title || undefined);
      setShowUploadModal(false);
      setFile(null);
      setTitle("");
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
    } catch (err) {
      setError(
        resolveMaterialUploadErrorMessage(err, t, t("materials:uploadError"))
      );
      if (err instanceof ApiError && err.errorCode === "ERR_FEATURE_NOT_INCLUDED") {
        trackEvent("materials_upload_blocked_free", {
          source: "materials_upload_runtime_guard",
        });
        setShowMaterialAccessUpgradeModal(true);
      } else if (err instanceof ApiError && err.errorCode === "ERR_QUOTA_EXCEEDED") {
        trackEvent("materials_quota_exhausted_paid", {
          source: "materials_upload",
        });
        queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
        setError(exhaustedMessage);
      }
    } finally {
      setUploading(false);
    }
  };

  const handleRetry = async (novelId: string) => {
    setRetryingId(novelId);

    try {
      await materialsApi.retry(novelId);
      toast.success(
        t("materials:retrySuccess", {
          defaultValue: "已重新提交分解任务，请稍候查看处理状态。",
        })
      );
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
    } catch (err) {
      if (err instanceof ApiError && err.errorCode === "ERR_QUOTA_EXCEEDED") {
        trackEvent("materials_quota_exhausted_paid", {
          source: "materials_retry",
        });
        queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
        toast.error(exhaustedMessage);
      } else if (err instanceof ApiError && err.errorCode === "ERR_FEATURE_NOT_INCLUDED") {
        trackEvent("materials_upload_blocked_free", {
          source: "materials_retry_runtime_guard",
        });
        setShowMaterialAccessUpgradeModal(true);
      } else {
        toast.error(handleApiError(err));
      }
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <>
      {/* Header */}
      <DashboardPageHeader
        title={t("materials:title")}
        subtitle={
          hasWorkspaceAccess
            ? t("materials:paidSubtitle", {
                defaultValue: "上传参考小说并查看结构化拆解结果",
              })
            : t("materials:teaserSubtitle", {
                defaultValue: "预览素材库能力，开通会员后即可开始使用",
              })
        }
        action={
          hasWorkspaceAccess ? (
            <button
              onClick={() => setShowUploadModal(true)}
              disabled={isMaterialsQuotaExhausted}
              className={`btn-primary rounded-xl flex items-center justify-center gap-2 ${isMobile ? "px-3 py-2.5" : "h-11 px-4"} active:scale-95 transition-transform disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              <Upload className="w-4 h-4" />
              {!isMobile &&
                (isMaterialsQuotaExhausted
                  ? t("materials:quota.decomposeTitle", {
                      defaultValue: "本月素材拆解次数已用完",
                    })
                  : t("materials:upload"))}
            </button>
          ) : (
            <button
              onClick={() => openUpgradePath("billing")}
              className={`btn-primary rounded-xl flex items-center justify-center gap-2 ${isMobile ? "px-3 py-2.5" : "h-11 px-4"} active:scale-95 transition-transform`}
            >
              <BookOpen className="w-4 h-4" />
              {!isMobile &&
                t("materials:teaserPrimary", {
                  defaultValue: "开通会员",
                })}
            </button>
          )
        }
      />

      {isMaterialsLoading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
        </div>
      ) : isMaterialsError ? (
        <div className="rounded-2xl border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-6">
          <p className="text-sm text-[hsl(var(--text-secondary))]">
            {t("materials:listLoadError", { defaultValue: "素材列表加载失败，请重试。" })}
          </p>
          <button className="btn-secondary mt-3 h-10 px-4" onClick={() => void refetchMaterials()}>
            {t("common:retry", { defaultValue: "重试" })}
          </button>
        </div>
      ) : showTeaser ? (
        <div className="space-y-4">
          <div className="rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-6">
            <div className="max-w-3xl space-y-4">
              <h2 className="text-xl font-semibold text-[hsl(var(--text-primary))]">
                {t("materials:teaserTitle", {
                  defaultValue: "上传参考小说，一键拆出章节梗概、角色和世界观",
                })}
              </h2>
              <p className="text-sm leading-6 text-[hsl(var(--text-secondary))]">
                {t("materials:teaserDescription", {
                  defaultValue:
                    "开通会员后，每月可使用 5 次素材拆解，快速提炼高价值参考素材。",
                })}
              </p>
              <ul className="space-y-2 text-sm text-[hsl(var(--text-secondary))]">
                <li>• {t("materials:teaserFeatureOne", { defaultValue: "查看章节梗概、角色、世界观、金手指等拆解示例" })}</li>
                <li>• {t("materials:teaserFeatureTwo", { defaultValue: "付费会员每月可使用 5 次素材拆解" })}</li>
                <li>• {t("materials:teaserFeatureThree", { defaultValue: "已拆解素材可持续浏览和复用" })}</li>
              </ul>
              <div className="grid gap-3 pt-2 md:grid-cols-3">
                <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-4">
                  <div className="text-xs font-medium text-[hsl(var(--accent-primary))]">
                    {t("materials:teaserCardCharacter", {
                      defaultValue: "角色拆解示例",
                    })}
                  </div>
                  <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                    {t("materials:teaserCardCharacterBody", {
                      defaultValue: "林舟 · 导师型配角 · 首次出场第 3 章",
                    })}
                  </p>
                </div>
                <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-4">
                  <div className="text-xs font-medium text-[hsl(var(--accent-primary))]">
                    {t("materials:teaserCardSynopsis", {
                      defaultValue: "章节梗概示例",
                    })}
                  </div>
                  <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                    {t("materials:teaserCardSynopsisBody", {
                      defaultValue: "第 3 章：林舟入宗门试炼，暗中护住主角，身份险些暴露",
                    })}
                  </p>
                </div>
                <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))] p-4">
                  <div className="text-xs font-medium text-[hsl(var(--accent-primary))]">
                    {t("materials:teaserCardWorldview", {
                      defaultValue: "世界观拆解示例",
                    })}
                  </div>
                  <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                    {t("materials:teaserCardWorldviewBody", {
                      defaultValue: "灵脉阶层、宗门辖区、禁术代价",
                    })}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-3 pt-2">
                <button
                  onClick={() => openUpgradePath("billing")}
                  className="btn-primary h-11 px-4"
                >
                  {t("materials:teaserPrimary", { defaultValue: "开通会员" })}
                </button>
                <button
                  onClick={() => openUpgradePath("pricing")}
                  className="inline-flex h-11 items-center justify-center rounded-md border border-[hsl(var(--accent-primary)/0.24)] bg-[hsl(var(--accent-primary)/0.08)] px-4 text-sm font-medium text-[hsl(var(--accent-primary))] transition-colors hover:border-[hsl(var(--accent-primary)/0.36)] hover:bg-[hsl(var(--accent-primary)/0.14)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.35)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                >
                  {t("materials:teaserSecondary", { defaultValue: "查看权益详情" })}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : materials.length === 0 ? (
        <>
          {materialDecomposeQuota && (
            <div className="mb-4 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4">
              <p className="text-sm font-medium text-[hsl(var(--text-primary))]">
                {remainingDecompositions == null
                  ? t("common:unlimited", { defaultValue: "不限" })
                  : t("materials:quotaRemaining", {
                      defaultValue: "本月素材拆解剩余 {{remaining}} / {{limit}} 次",
                      remaining: remainingDecompositions,
                      limit: materialDecomposeQuota.limit,
                    })}
              </p>
              {isMaterialsQuotaExhausted && (
                <p className="mt-2 text-sm text-[hsl(var(--warning))]">
                  {exhaustedMessage}
                </p>
              )}
            </div>
          )}
          <DashboardEmptyState
            icon={BookOpen}
            title={t("materials:noMaterials")}
            action={
              !isMaterialsQuotaExhausted ? (
                <button
                  onClick={() => setShowUploadModal(true)}
                  className="text-sm text-[hsl(var(--accent-primary))] hover:underline"
                >
                  {t("materials:uploadFirst")}
                </button>
              ) : undefined
            }
          />
        </>
      ) : (
        <div className="space-y-4">
          {materialDecomposeQuota && (
            <div className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4">
              <p className="text-sm font-medium text-[hsl(var(--text-primary))]">
                {remainingDecompositions == null
                  ? t("common:unlimited", { defaultValue: "不限" })
                  : t("materials:quotaRemaining", {
                      defaultValue: "本月素材拆解剩余 {{remaining}} / {{limit}} 次",
                      remaining: remainingDecompositions,
                      limit: materialDecomposeQuota.limit,
                    })}
              </p>
              {isMaterialsQuotaExhausted && (
                <p className="mt-2 text-sm text-[hsl(var(--warning))]">
                  {exhaustedMessage}
                </p>
              )}
            </div>
          )}
          <div className={`grid gap-3.5 ${isMobile ? "grid-cols-1" : "grid-cols-2 lg:grid-cols-3"}`}>
            {materials.map((material) => (
              <MaterialCard
                key={material.id}
                material={material}
                onClick={() => handleCardClick(material.id)}
                onDelete={() => setDeletingId(material.id)}
                onRetry={
                  isMaterialsQuotaExhausted ? undefined : () => handleRetry(material.id)
                }
                isRetrying={retryingId === material.id}
                isMobile={isMobile}
                showDeleteAction={showDeleteAction}
              />
            ))}
          </div>
        </div>
      )}

      {/* Upload Modal */}
      <Modal
        open={showUploadModal}
        onClose={() => {
          if (uploading) return;
          setShowUploadModal(false);
          setFile(null);
          setTitle("");
          setError(null);
        }}
        title={t("materials:uploadModal.title")}
        size="md"
        showCloseButton={!uploading}
        closeOnBackdropClick={!uploading}
        closeOnEscape={!uploading}
        footer={
          <>
            <button
              onClick={() => {
                if (uploading) return;
                setShowUploadModal(false);
                setFile(null);
                setTitle("");
                setError(null);
              }}
              disabled={uploading}
              className="btn-ghost flex-1 h-11"
            >
              {t("common:cancel")}
            </button>
            <button
              onClick={handleUpload}
              disabled={!file || uploading}
              className="btn-primary flex items-center justify-center gap-2 flex-1 h-11"
            >
              {uploading ? (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
                  {t("materials:uploadModal.uploading")}
                </>
              ) : (
                <>
                  <Upload className="w-4 h-4" />
                  {t("materials:uploadModal.upload")}
                </>
              )}
            </button>
          </>
        }
      >
        {/* File Input */}
        <div>
          <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-2">
            {t("materials:uploadModal.selectFile")} *
          </label>
          {/* Hidden native file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt"
            onChange={handleFileChange}
            className="hidden"
          />
          {/* Custom file select button */}
          <div
            onClick={handleSelectFile}
            className="flex items-center gap-3 p-3 border border-dashed border-[hsl(var(--border-color))] rounded-lg cursor-pointer hover:border-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-all"
          >
            <div className="w-10 h-10 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center">
              <Upload className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            </div>
            <div className="flex-1 min-w-0">
              {file ? (
                <>
                  <p className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                    {file.name}
                  </p>
                  <p className="text-xs text-[hsl(var(--text-tertiary))]">
                    {(file.size / 1024).toFixed(1)} KB
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-[hsl(var(--text-secondary))]">
                    {t("materials:uploadModal.clickToSelect")}
                  </p>
                  <p className="text-xs text-[hsl(var(--text-tertiary))]">
                    {t("materials:uploadModal.supportedFormats")}
                  </p>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Title Input */}
        <div className="mt-4">
          <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {t("materials:uploadModal.title")}
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="input"
            placeholder={t("materials:uploadModal.titlePlaceholder")}
          />
          <p className="text-xs text-[hsl(var(--text-tertiary))] mt-1">
            {t("materials:uploadModal.titleHint")}
          </p>
        </div>

        {/* Error Message */}
        {error && (
          <div className="mt-4 p-3 rounded-lg bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)]">
            <p className="text-sm text-[hsl(var(--error))]">{error}</p>
          </div>
        )}
      </Modal>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deletingId}
        onClose={() => setDeletingId(null)}
        onConfirm={() => handleDelete(deletingId!)}
        title={t("materials:deleteConfirm.title")}
        message={t("materials:deleteConfirm.message")}
        variant="danger"
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
        loading={deleteMutation.isPending}
      />

      <MaterialsUpgradePromptModal
        open={showMaterialAccessUpgradeModal}
        onClose={() => setShowMaterialAccessUpgradeModal(false)}
      />
    </>
  );
}

// Material Card Component
function MaterialCard({
  material,
  onClick,
  onDelete,
  onRetry,
  isRetrying,
  isMobile,
  showDeleteAction,
}: {
  material: MaterialNovel;
  onClick: () => void;
  onDelete: () => void;
  onRetry?: () => void;
  isRetrying?: boolean;
  isMobile?: boolean;
  showDeleteAction?: boolean;
}) {
  const { t } = useTranslation(["materials", "common"]);
  const openMaterialLabel = `${t("materials:viewMaterialDetail", {
    defaultValue: "查看素材详情",
  })}: ${material.title}`;

  const handleOpenByKeyboard = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onClick();
    }
  };

  const getStatusIcon = () => {
    switch (material.status) {
      case "completed":
        return <CheckCircle className="w-4 h-4 text-[hsl(var(--success))]" />;
      case "completed_with_errors":
        return <AlertCircle className="w-4 h-4 text-[hsl(var(--warning))]" />;
      case "processing":
        return <RefreshCw className="w-4 h-4 text-[hsl(var(--info))] animate-spin" />;
      case "failed":
        return <AlertCircle className="w-4 h-4 text-[hsl(var(--error))]" />;
      default:
        return <Clock className="w-4 h-4 text-[hsl(var(--text-tertiary))]" />;
    }
  };

  const getStatusText = () => {
    switch (material.status) {
      case "completed":
        return t("materials:status.completed");
      case "completed_with_errors":
        return t("materials:status.completed_with_errors", {
          defaultValue: "部分完成",
        });
      case "processing":
        return t("materials:status.processing");
      case "failed":
        return t("materials:status.failed");
      default:
        return t("materials:status.pending");
    }
  };

  const getStatusColor = () => {
    switch (material.status) {
      case "completed":
        return "text-[hsl(var(--success))] bg-[hsl(var(--success)/0.1)]";
      case "completed_with_errors":
        return "text-[hsl(var(--warning))] bg-[hsl(var(--warning)/0.1)]";
      case "processing":
        return "text-[hsl(var(--info))] bg-[hsl(var(--info)/0.1)]";
      case "failed":
        return "text-[hsl(var(--error))] bg-[hsl(var(--error)/0.1)]";
      default:
        return "text-[hsl(var(--text-tertiary))] bg-[hsl(var(--bg-tertiary))]";
    }
  };

  return (
    <div
      className={`group relative bg-[hsl(var(--bg-secondary))] rounded-xl border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-lg transition-all ${isMobile ? "p-3" : "p-4"}`}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={handleOpenByKeyboard}
        aria-label={openMaterialLabel}
        className="cursor-pointer rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="w-10 h-10 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center shrink-0">
              <BookOpen className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className={`font-semibold text-[hsl(var(--text-primary))] truncate ${isMobile ? "text-sm" : "text-base"}`}>
                {material.title}
              </h3>
              <p className="text-xs text-[hsl(var(--text-tertiary))] truncate">
                {material.original_filename}
              </p>
            </div>
          </div>
        </div>

        {/* Status Badge */}
        <div className="flex items-center gap-2 mb-3">
          <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium ${getStatusColor()}`}>
            {getStatusIcon()}
            {getStatusText()}
          </span>
        </div>

        {/* Stats */}
        {(material.status === "completed" || material.status === "completed_with_errors") && (
          <div className="flex items-center gap-4 text-xs text-[hsl(var(--text-secondary))]">
            <div className="flex items-center gap-1">
              <FileText className="w-3.5 h-3.5" />
              <span>{material.chapters_count || 0} {t("materials:chapters")}</span>
            </div>
          </div>
        )}

        {/* Error Message */}
        {(material.status === "failed" || material.status === "completed_with_errors") && material.error_message && (
          <p className="text-xs text-[hsl(var(--error))] mt-2 line-clamp-2">
            {materialJobErrorText(material.error_message)}
          </p>
        )}

        {(material.status === "failed" || material.status === "completed_with_errors") && onRetry && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRetry();
            }}
            disabled={isRetrying}
            className="mt-3 h-8 px-3 inline-flex items-center gap-1.5 rounded-lg text-xs font-medium border border-[hsl(var(--border-color))] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRetrying ? "animate-spin" : ""}`} />
            {isRetrying
              ? t("materials:status.processing", { defaultValue: "处理中" })
              : t("common:retry", { defaultValue: "重试" })}
          </button>
        )}
      </div>

      {/* Delete Button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        className={`absolute top-3 right-3 rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--error)/0.1)] hover:text-[hsl(var(--error))] transition-all ${
          showDeleteAction
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
        } ${isMobile ? "p-1.5" : "p-2"}`}
      >
        <Trash2 className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
      </button>
    </div>
  );
}
