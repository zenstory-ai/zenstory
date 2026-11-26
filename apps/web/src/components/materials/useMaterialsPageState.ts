import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { materialsApi, type MaterialNovel } from "../../lib/materialsApi";
import { ApiError } from "../../lib/apiClient";
import { handleApiError } from "../../lib/errorHandler";
import { subscriptionApi, subscriptionQueryKeys } from "../../lib/subscriptionApi";
import { trackEvent } from "../../lib/analytics";
import { toast } from "../../lib/toast";
import { hasMaterialsLibraryAccess } from "../../lib/materialsAccess";
import {
  prepareMaterialUpload,
  resolveMaterialUploadErrorMessage,
  type MaterialTrialSelection,
} from "../../lib/materialUploadValidation";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { useMaterialsCheckoutUnavailableText, useMaterialsProEntry } from "../../hooks/useMaterialsProEntry";
import { MATERIAL_LIBRARY_SUMMARY_QUERY_KEY } from "../../hooks/useMaterialLibrary";
import { useRefreshMaterialLibraryOnCompletion } from "../../hooks/useMaterialLibraryRefresh";
import { useProMaterialDecompositionsLimit } from "../../hooks/useProMaterialDecompositionsLimit";
import { formatMaterialResetDate, isActiveMaterialStatus } from "./materialHelpers";

export function useMaterialsPageState() {
  const { t, i18n } = useTranslation(["materials", "common"]);
  const materialUploadUpgradePrompt = getUpgradePromptDefinition("material_upload_quota_blocked");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const teaserTrackedRef = useRef(false);

  const [showUploadModal, setShowUploadModal] = useState(false);
  const [showMaterialAccessUpgradeModal, setShowMaterialAccessUpgradeModal] = useState(false);
  /** Funnel source of the open redeem dialog (null = closed). */
  const [redeemSource, setRedeemSource] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Upload modal state
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trialSelection, setTrialSelection] = useState<MaterialTrialSelection | null>(null);
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

  // The quota also carries the free trial (once per account, first N chapters), so free
  // authors need it too.
  const {
    data: quota,
    isLoading: isQuotaLoading,
  } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
    enabled: materialsAccess !== undefined,
  });

  const materialTrial = quota?.material_trial;
  const trialAvailable = materialsAccess === false && materialTrial?.available === true;
  const trialCharacterLimit = (materialTrial?.max_characters ?? 0).toLocaleString("en-US");
  // A free author who used the trial reads their trial book in the library.
  const trialUsed = materialsAccess === false && materialTrial?.used === true;
  const canReadLibrary = hasWorkspaceAccess || trialUsed;
  const showTeaser = materialsAccess === false && !trialUsed;
  const proDecompositionsLimit = useProMaterialDecompositionsLimit(materialsAccess === false);
  // Online checkout off: the Pro entries become 「兑换码开通」 and open the redeem dialog here.
  const proEntry = useMaterialsProEntry(materialsAccess === false);
  const checkoutUnavailableText = useMaterialsCheckoutUnavailableText();

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
    enabled: canReadLibrary,
    staleTime: 30 * 1000, // 30 seconds - prevents refetch on tab switch
    // Poll every 3 seconds when there are pending/processing items
    refetchInterval: (query) => {
      const data = query.state.data as MaterialNovel[] | undefined;
      const hasProcessing = data?.some((m) => isActiveMaterialStatus(m.status));
      return hasProcessing ? 3000 : false;
    },
  });

  // A free author's page (teaser, trial or library) depends on the trial state in the
  // quota: wait for it instead of flashing the Pro teaser first.
  const isTrialStateLoading = materialsAccess === false && isQuotaLoading;
  const isMaterialsLoading =
    isSubscriptionLoading ||
    isTrialStateLoading ||
    (canReadLibrary && (isLoading || (isFetching && materials.length === 0)));
  useRefreshMaterialLibraryOnCompletion(canReadLibrary ? materials : undefined);

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
      isTrialStateLoading ||
      !showTeaser ||
      teaserTrackedRef.current
    ) {
      return;
    }

    trackEvent("materials_teaser_exposed", {
      source: "materials_teaser",
    });
    teaserTrackedRef.current = true;
  }, [isSubscriptionError, isSubscriptionLoading, isTrialStateLoading, showTeaser]);

  const openUpgradePath = (destination: "billing" | "pricing", source = "materials_teaser") => {
    if (destination === "billing" && proEntry.checkoutOff) {
      // The billing page would only offer the redeem dialog: open it here instead.
      trackEvent("materials_upgrade_clicked", { source, destination: "redeem" });
      setRedeemSource(source);
      return;
    }
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

  const exhaustedMessage = t("materials:quotaExhausted", {
    defaultValue: "本月 {{limit}} 次拆解已用完，将于 {{resetAt}} 恢复。已拆好的内容仍可查看和引用。",
    limit: materialDecomposeQuota?.limit ?? 5,
    resetAt:
      formatMaterialResetDate(materialDecomposeQuota?.reset_at, i18n?.language) ??
      t("materials:quotaResetFallback", { defaultValue: "下月 1 日" }),
  });

  const handleDelete = (id: string) => {
    deleteMutation.mutate(id);
  };

  const handleCardClick = (novelId: string) => {
    navigate(`/materials/${novelId}`);
  };

  const handleCloseUploadModal = () => {
    if (uploading) return;
    setShowUploadModal(false);
    setFile(null);
    setTrialSelection(null);
    setTitle("");
    setError(null);
  };

  const handleStartTrial = () => {
    trackEvent("materials_trial_started", { source: "materials_teaser" });
    setShowUploadModal(true);
  };

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      // A free trial only checks and uploads the first chapters of the book.
      const prepared = await prepareMaterialUpload(selectedFile, t, {
        trialMaxChapters: trialAvailable ? materialTrial?.max_chapters : null,
        trialMaxCharacters: trialAvailable ? materialTrial?.max_characters : null,
      });
      if (prepared.error !== null) {
        setFile(null);
        setTrialSelection(null);
        setError(prepared.error);
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
        return;
      }

      setFile(selectedFile);
      setTrialSelection(prepared.trial);
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
    const uploadingWithTrial = trialAvailable;

    try {
      await materialsApi.upload(file, title || undefined);
      setShowUploadModal(false);
      setFile(null);
      setTrialSelection(null);
      setTitle("");
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
    } catch (err) {
      const message = resolveMaterialUploadErrorMessage(err, t, t("materials:uploadError"));
      setError(message);
      if (uploadingWithTrial) {
        // A failure after the trial was committed leaves a used trial and a failed book.
        queryClient.invalidateQueries({ queryKey: ["materials"] });
        queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
      }
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
          defaultValue: "已重新开始拆解",
        }),
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

  const handleExport = async (material: MaterialNovel) => {
    setExportingId(material.id);
    try {
      await materialsApi.exportBreakdown(material.id, material.title);
      toast.success(
        t("materials:export.success", {
          defaultValue: "拆解结果已导出为 TXT 文件",
        }),
      );
    } catch (err) {
      toast.error(handleApiError(err));
    } finally {
      setExportingId(null);
    }
  };

  // Only show the header Pro button once loading finishes and when the page body does
  // not already present a primary upgrade CTA (teaser without trial, or empty trial-used).
  const showHeaderUpgrade =
    !hasWorkspaceAccess &&
    !isMaterialsLoading &&
    !isMaterialsError &&
    !((showTeaser && !trialAvailable) || (materials.length === 0 && trialUsed));

  return {
    materialsAccess,
    hasWorkspaceAccess,
    isSubscriptionLoading,
    isSubscriptionError,
    refetchSubscriptionStatus,
    materialTrial,
    trialAvailable,
    trialCharacterLimit,
    trialUsed,
    showTeaser,
    proDecompositionsLimit,
    proEntry,
    checkoutUnavailableText,
    materialDecomposeQuota,
    remainingDecompositions,
    isMaterialsQuotaExhausted,
    exhaustedMessage,
    materials,
    isMaterialsLoading,
    isMaterialsError,
    refetchMaterials,
    showHeaderUpgrade,
    showUploadModal,
    setShowUploadModal,
    showMaterialAccessUpgradeModal,
    setShowMaterialAccessUpgradeModal,
    redeemSource,
    setRedeemSource,
    deletingId,
    setDeletingId,
    isDeleting: deleteMutation.isPending,
    retryingId,
    exportingId,
    searchQuery,
    setSearchQuery,
    file,
    title,
    setTitle,
    uploading,
    error,
    trialSelection,
    fileInputRef,
    openUpgradePath,
    handleDelete,
    handleCardClick,
    handleCloseUploadModal,
    handleStartTrial,
    handleFileChange,
    handleSelectFile,
    handleUpload,
    handleRetry,
    handleExport,
  };
}
