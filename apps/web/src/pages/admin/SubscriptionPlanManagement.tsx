import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X,
  AlertTriangle,
  Edit,
  DollarSign,
} from "lucide-react";
import { AdminPageState } from "../../components/admin";
import { adminApi } from "../../lib/adminApi";
import type { SubscriptionPlan, SubscriptionFeatures } from "../../types/subscription";
import { toast } from "../../lib/toast";
import { inspirationsConfig } from "../../config/inspirations";

// Plan card component
const PlanCard: React.FC<{
  plan: SubscriptionPlan;
  onEdit: (plan: SubscriptionPlan) => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}> = ({ plan, onEdit, t }) => {
  const formatPrice = (cents: number) => {
    return (cents / 100).toFixed(2);
  };

  const formatFeatureValue = (value: unknown): string => {
    if (value === -1) return t("plans.unlimited");
    if (typeof value === "boolean") return value ? t("plans.yes") : t("plans.no");
    if (Array.isArray(value)) return value.join(", ");
    return String(value);
  };

  const featureLabels: Record<string, string> = {
    ai_conversations_per_day: t("plans.featuresDetail.ai_conversations_per_day"),
    context_window_tokens: t("plans.featuresDetail.context_window_tokens"),
    context_tokens_limit: t("plans.featuresDetail.context_tokens_limit"),
    file_versions_per_file: t("plans.featuresDetail.file_versions_per_file"),
    max_projects: t("plans.featuresDetail.max_projects"),
    active_projects_limit: t("plans.featuresDetail.active_projects_limit"),
    material_uploads: t("plans.featuresDetail.material_uploads"),
    material_uploads_monthly: t("plans.featuresDetail.material_uploads_monthly"),
    material_decompositions: t("plans.featuresDetail.material_decompositions"),
    material_decompositions_monthly: t("plans.featuresDetail.material_decompositions_monthly"),
    custom_skills: t("plans.featuresDetail.custom_skills"),
    custom_skills_limit: t("plans.featuresDetail.custom_skills_limit"),
    inspiration_copies_monthly: t("plans.featuresDetail.inspiration_copies_monthly"),
    export_formats: t("plans.featuresDetail.export_formats"),
    custom_prompts: t("plans.featuresDetail.custom_prompts"),
    priority_support: t("plans.featuresDetail.priority_support"),
    priority_queue_level: t("plans.featuresDetail.priority_queue_level"),
  };

  const featureDisplayOrder = [
    "ai_conversations_per_day",
    "max_projects",
    "active_projects_limit",
    "context_window_tokens",
    "context_tokens_limit",
    "material_uploads",
    "material_uploads_monthly",
    "material_decompositions",
    "material_decompositions_monthly",
    "custom_skills",
    "custom_skills_limit",
    "inspiration_copies_monthly",
    "file_versions_per_file",
    "export_formats",
    "custom_prompts",
    "priority_support",
    "priority_queue_level",
  ];

  const knownFeatureKeys = featureDisplayOrder.filter((key) =>
    Object.prototype.hasOwnProperty.call(plan.features, key)
  );
  const extraFeatureKeys = Object.keys(plan.features)
    .filter(
      (key) =>
        !featureDisplayOrder.includes(key)
        && key !== "writing_credits_monthly"
        && key !== "agent_runs_monthly",
    )
    .sort();
  const planFeatureKeys = [...knownFeatureKeys, ...extraFeatureKeys].filter(
    (key) => inspirationsConfig.enabled || key !== "inspiration_copies_monthly",
  );

  return (
    <div className="admin-surface p-4 space-y-3">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <h3 className="text-lg font-semibold text-[hsl(var(--text-primary))] mb-1">
            {plan.display_name}
          </h3>
          {plan.display_name_en && (
            <p className="text-sm text-[hsl(var(--text-secondary))]">
              {plan.display_name_en}
            </p>
          )}
        </div>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            plan.is_active
              ? "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]"
              : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
          }`}
        >
          {plan.is_active ? t("plans.active") : t("plans.inactive")}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="p-3 bg-[hsl(var(--bg-secondary))] rounded-lg">
          <div className="flex items-center gap-1 text-xs text-[hsl(var(--text-secondary))] mb-1">
            <DollarSign size={12} />
            {t("plans.monthly")}
          </div>
          <div className="text-lg font-bold text-[hsl(var(--text-primary))]">
            {formatPrice(plan.price_monthly_cents)} <span className="text-sm font-normal">{t("plans.currency")}</span>
          </div>
        </div>
        <div className="p-3 bg-[hsl(var(--bg-secondary))] rounded-lg">
          <div className="flex items-center gap-1 text-xs text-[hsl(var(--text-secondary))] mb-1">
            <DollarSign size={12} />
            {t("plans.yearly")}
          </div>
          <div className="text-lg font-bold text-[hsl(var(--text-primary))]">
            {formatPrice(plan.price_yearly_cents)} <span className="text-sm font-normal">{t("plans.currency")}</span>
          </div>
        </div>
      </div>

      <div className="space-y-1.5">
        <div className="text-xs font-medium text-[hsl(var(--text-secondary))] uppercase tracking-wide">
          {t("plans.featuresDetail.title")}
        </div>
        <div className="grid grid-cols-1 gap-1 text-sm">
          {planFeatureKeys.map((key) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-[hsl(var(--text-secondary))]">
                {featureLabels[key] || key}
              </span>
              <span className="text-[hsl(var(--text-primary))] font-medium">
                {formatFeatureValue((plan.features as SubscriptionFeatures)[key])}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="pt-3 border-t border-[hsl(var(--separator-color))] flex items-center justify-end">
        <button
          onClick={() => onEdit(plan)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 transition-all"
        >
          <Edit size={14} />
          {t("plans.edit")}
        </button>
      </div>
    </div>
  );
};

export const SubscriptionPlanManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [editingPlan, setEditingPlan] = useState<SubscriptionPlan | null>(null);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editFormData, setEditFormData] = useState({
    display_name: "",
    display_name_en: "",
    price_monthly_cents: 0,
    price_yearly_cents: 0,
    is_active: true,
    featuresJson: "",
  });
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Fetch plans
  const { data: plans, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "plans"],
    queryFn: () => adminApi.getPlans(),
    staleTime: 30 * 1000,
  });
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  // Update plan mutation
  const updateMutation = useMutation({
    mutationFn: ({
      planId,
      data,
    }: {
      planId: string;
      data: {
        display_name?: string;
        display_name_en?: string;
        price_monthly_cents?: number;
        price_yearly_cents?: number;
        is_active?: boolean;
        features?: Record<string, unknown>;
      };
    }) => adminApi.updatePlan(planId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "plans"] });
      setShowEditModal(false);
      setEditingPlan(null);
      setJsonError(null);
      toast.success(t("plans.updateSuccess"));
    },
    onError: () => {
      toast.error(t("plans.updateFailed"));
    },
  });

  const handleEdit = (plan: SubscriptionPlan) => {
    setEditingPlan(plan);
    setEditFormData({
      display_name: plan.display_name,
      display_name_en: plan.display_name_en || "",
      price_monthly_cents: plan.price_monthly_cents,
      price_yearly_cents: plan.price_yearly_cents,
      is_active: plan.is_active,
      featuresJson: JSON.stringify(plan.features, null, 2),
    });
    setJsonError(null);
    setShowEditModal(true);
  };

  const handleJsonChange = (value: string) => {
    setEditFormData({ ...editFormData, featuresJson: value });
    try {
      JSON.parse(value);
      setJsonError(null);
    } catch {
      setJsonError(t("plans.invalidJson"));
    }
  };

  const handleEditSubmit = () => {
    if (!editingPlan) return;

    let features: Record<string, unknown> | undefined;
    try {
      features = JSON.parse(editFormData.featuresJson);
    } catch {
      setJsonError(t("plans.invalidJson"));
      return;
    }

    updateMutation.mutate({
      planId: editingPlan.id,
      data: {
        display_name: editFormData.display_name,
        display_name_en: editFormData.display_name_en || undefined,
        price_monthly_cents: editFormData.price_monthly_cents,
        price_yearly_cents: editFormData.price_yearly_cents,
        is_active: editFormData.is_active,
        features,
      },
    });
  };

  return (
    <div className="admin-page">
      <div>
        <h1 className="admin-page-title">
          {t("plans.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("plans.subtitle")}
        </p>
      </div>

      {/* Plans grid */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={!plans || plans.length === 0}
        loadingText={t("common:loading")}
        errorText={queryErrorText}
        emptyText={t("common:noData")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
      >
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(plans ?? []).map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              onEdit={handleEdit}
              t={t}
            />
          ))}
        </div>
      </AdminPageState>

      {/* Edit Plan Modal */}
      {showEditModal && editingPlan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-[hsl(var(--bg-primary))] flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("plans.editPlan")} - {editingPlan.name}
              </h2>
              <button
                onClick={() => {
                  setShowEditModal(false);
                  setEditingPlan(null);
                  setJsonError(null);
                }}
                disabled={updateMutation.isPending}
                aria-label={t("common:close")}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4">
              {/* Basic Info Section */}
              <div className="space-y-3">
                <h3 className="text-sm font-medium text-[hsl(var(--text-primary))] uppercase tracking-wide">
                  {t("plans.basicInfo")}
                </h3>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                      {t("plans.displayName")} ({t("plans.zh")})
                    </label>
                    <input
                      type="text"
                      value={editFormData.display_name}
                      onChange={(e) =>
                        setEditFormData({ ...editFormData, display_name: e.target.value })
                      }
                      className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                      {t("plans.displayName")} ({t("plans.en")})
                    </label>
                    <input
                      type="text"
                      value={editFormData.display_name_en}
                      onChange={(e) =>
                        setEditFormData({ ...editFormData, display_name_en: e.target.value })
                      }
                      className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                      {t("plans.monthlyPrice")}
                    </label>
                    <div className="relative">
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={editFormData.price_monthly_cents / 100}
                        onChange={(e) =>
                          setEditFormData({
                            ...editFormData,
                            price_monthly_cents: Math.round((parseFloat(e.target.value) || 0) * 100),
                          })
                        }
                        className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] pr-12"
                      />
                      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[hsl(var(--text-secondary))]">
                        {t("plans.currency")}
                      </span>
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                      {t("plans.yearlyPrice")}
                    </label>
                    <div className="relative">
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={editFormData.price_yearly_cents / 100}
                        onChange={(e) =>
                          setEditFormData({
                            ...editFormData,
                            price_yearly_cents: Math.round((parseFloat(e.target.value) || 0) * 100),
                          })
                        }
                        className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] pr-12"
                      />
                      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[hsl(var(--text-secondary))]">
                        {t("plans.currency")}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() =>
                      setEditFormData({ ...editFormData, is_active: !editFormData.is_active })
                    }
                    className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                      editFormData.is_active
                        ? "bg-[hsl(var(--accent-primary))]"
                        : "bg-[hsl(var(--bg-tertiary))]"
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                        editFormData.is_active ? "translate-x-6" : "translate-x-1"
                      }`}
                    />
                  </button>
                  <span className="text-sm text-[hsl(var(--text-primary))]">
                    {editFormData.is_active ? t("plans.active") : t("plans.inactive")}
                  </span>
                </div>
              </div>

              {/* Features JSON Editor Section */}
              <div className="space-y-3">
                <h3 className="text-sm font-medium text-[hsl(var(--text-primary))] uppercase tracking-wide">
                  {t("plans.featuresDetail.title")}
                </h3>

                <div>
                  <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                    {t("plans.featuresDetail.jsonLabel")}
                  </label>
                  <textarea
                    value={editFormData.featuresJson}
                    onChange={(e) => handleJsonChange(e.target.value)}
                    rows={10}
                    className={`w-full px-3 py-2.5 bg-[hsl(var(--bg-secondary))] border rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] font-mono text-sm ${
                      jsonError
                        ? "border-[hsl(var(--error))]"
                        : "border-[hsl(var(--separator-color))]"
                    }`}
                    placeholder={t("plans.featuresDetail.jsonPlaceholder")}
                  />
                  {jsonError && (
                    <p className="mt-1 text-sm text-[hsl(var(--error))] flex items-center gap-1">
                      <AlertTriangle size={14} />
                      {jsonError}
                    </p>
                  )}
                </div>
              </div>
            </div>

            <div className="sticky bottom-0 bg-[hsl(var(--bg-primary))] flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => {
                  setShowEditModal(false);
                  setEditingPlan(null);
                  setJsonError(null);
                }}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleEditSubmit}
                disabled={updateMutation.isPending || !!jsonError}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {updateMutation.isPending ? t("common:loading") : t("plans.save")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default SubscriptionPlanManagement;
