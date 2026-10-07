import React, { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Save, Trash2, Loader2, AlertTriangle } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import type { PromptConfigRequest } from "../../types/admin";
import { FormSection, FormField, TouchCheckbox, AdminSelect } from "../../components/admin";
import { toast } from "../../lib/toast";

export const PromptEditor: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const navigate = useNavigate();
  const { projectType } = useParams<{ projectType: string }>();
  const queryClient = useQueryClient();
  const isNew = projectType === "new" || !projectType;
  const decodedProjectType = projectType ? decodeURIComponent(projectType) : "";
  const [selectedProjectType, setSelectedProjectType] = useState("novel");

  const [formData, setFormData] = useState<PromptConfigRequest>({
    role_definition: "",
    capabilities: "",
    directory_structure: "",
    content_structure: "",
    file_types: "",
    writing_guidelines: "",
    include_dialogue_guidelines: false,
    is_active: true,
  });
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const navigationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Each route identity starts with its own form baseline.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormData({
      role_definition: "", capabilities: "", directory_structure: "", content_structure: "",
      file_types: "", writing_guidelines: "", include_dialogue_guidelines: false, is_active: true,
    });
    setSelectedProjectType("novel");
    setShowDeleteConfirm(false);
    return () => {
      if (navigationTimerRef.current !== null) clearTimeout(navigationTimerRef.current);
      navigationTimerRef.current = null;
    };
  }, [decodedProjectType]);

  // 获取现有配置
  const {
    data: existingConfig,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["admin", "prompts", projectType],
    queryFn: () => adminApi.getPrompt(decodedProjectType),
    enabled: !isNew && !!projectType,
    staleTime: 30 * 1000,
  });

  // 初始化表单数据
  useEffect(() => {
    if (existingConfig) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFormData({
        role_definition: existingConfig.role_definition || "",
        capabilities: existingConfig.capabilities || "",
        directory_structure: existingConfig.directory_structure || "",
        content_structure: existingConfig.content_structure || "",
        file_types: existingConfig.file_types || "",
        writing_guidelines: existingConfig.writing_guidelines || "",
        include_dialogue_guidelines: existingConfig.include_dialogue_guidelines || false,
        is_active: existingConfig.is_active ?? true,
        expected_version: existingConfig.version,
      });
    }
  }, [existingConfig, decodedProjectType]);

  // 保存/更新 mutation
  const saveMutation = useMutation({
    mutationFn: (data: PromptConfigRequest) => {
      const targetProjectType = isNew ? selectedProjectType : decodedProjectType;
      return adminApi.upsertPrompt(targetProjectType, data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "prompts"] });
      toast.success(t("promptEditor.saveSuccess"));
      if (navigationTimerRef.current !== null) clearTimeout(navigationTimerRef.current);
      navigationTimerRef.current = setTimeout(() => {
        navigationTimerRef.current = null;
        navigate("/admin/prompts");
      }, 500);
    },
    onError: () => {
      toast.error(t("promptEditor.saveFailed"));
    },
  });

  // 删除 mutation
  const deleteMutation = useMutation({
    mutationFn: () => adminApi.deletePrompt(decodedProjectType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "prompts"] });
      toast.success(t("promptEditor.deleteSuccess"));
      setShowDeleteConfirm(false);
      if (navigationTimerRef.current !== null) clearTimeout(navigationTimerRef.current);
      navigationTimerRef.current = setTimeout(() => {
        navigationTimerRef.current = null;
        navigate("/admin/prompts");
      }, 500);
    },
    onError: () => {
      toast.error(t("promptEditor.deleteFailed"));
      setShowDeleteConfirm(false);
    },
  });

  const handleSave = () => {
    if (!formData.role_definition.trim() || !formData.capabilities.trim()) {
      return;
    }
    saveMutation.mutate({
      ...formData,
      role_definition: formData.role_definition.trim(),
      capabilities: formData.capabilities.trim(),
    });
  };

  const handleDeleteClick = () => {
    setShowDeleteConfirm(true);
  };

  const handleDeleteConfirm = () => {
    deleteMutation.mutate();
  };

  const handleInputChange = (field: keyof PromptConfigRequest, value: string | boolean) => {
    setFormData({ ...formData, [field]: value });
  };

  if (!isNew && isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="animate-spin text-[hsl(var(--text-secondary))]" size={32} />
      </div>
    );
  }

  if (!isNew && isError) {
    const errorText = error instanceof Error && error.message
      ? error.message
      : t("common:error");
    return (
      <div className="admin-page">
        <div className="admin-surface flex flex-col items-center gap-4 py-12 text-center">
          <AlertTriangle className="text-[hsl(var(--error))]" size={32} />
          <p className="text-[hsl(var(--text-secondary))]">{errorText}</p>
          <button
            type="button"
            onClick={() => { void refetch(); }}
            className="px-4 py-2.5 min-h-11 rounded-lg bg-[hsl(var(--accent-primary))] text-white"
          >
            {t("common:retry")}
          </button>
        </div>
      </div>
    );
  }

  const canSave = formData.role_definition.trim().length > 0
    && formData.capabilities.trim().length > 0
    && (isNew || Boolean(existingConfig));

  return (
    <div className="admin-page">
      {/* 头部 */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2 sm:gap-4 min-w-0 flex-1">
          <button
            onClick={() => navigate("/admin/prompts")}
            className="p-2.5 touch-target shrink-0 hover:bg-[hsl(var(--bg-tertiary))] rounded-lg transition-colors min-h-11"
          >
            <ArrowLeft size={20} className="text-[hsl(var(--text-primary))]" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="admin-page-title truncate">
              {t("promptEditor.title")}
            </h1>
            <p className="admin-page-subtitle truncate">
              {isNew ? t("promptEditor.new") : decodeURIComponent(projectType!)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!isNew && (
            <button
              onClick={handleDeleteClick}
              disabled={deleteMutation.isPending}
              className="hidden sm:flex items-center gap-2 px-4 py-2 min-h-11 bg-[hsl(var(--error))] text-white rounded-lg hover:bg-[hsl(var(--error)/0.9)] active:scale-95 transition-all disabled:opacity-50"
            >
              <Trash2 size={18} />
              <span>{t("prompts.delete")}</span>
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saveMutation.isPending || !canSave}
            className="flex items-center justify-center gap-2 px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all disabled:opacity-50 flex-1 sm:flex-none"
          >
            {saveMutation.isPending ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Save size={18} />
            )}
            <span className="hidden sm:inline">{t("promptEditor.save")}</span>
          </button>
        </div>
        {!isNew && (
          <button
            onClick={handleDeleteClick}
            disabled={deleteMutation.isPending}
            className="sm:hidden w-full flex items-center justify-center gap-2 px-4 py-2.5 min-h-11 bg-[hsl(var(--error))] text-white rounded-lg hover:bg-[hsl(var(--error)/0.9)] active:scale-95 transition-all disabled:opacity-50"
          >
            <Trash2 size={18} />
            <span>{t("prompts.delete")}</span>
          </button>
        )}
      </div>

      {/* 表单 */}
      <div className="space-y-6">
        {/* 基本信息 */}
        <FormSection title={t("promptEditor.basicInfo")}>
          {isNew ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
              <FormField label={t("promptEditor.projectType")} className="sm:col-span-2">
                <AdminSelect
                  fullWidth
                  value={selectedProjectType}
                  onChange={(e) => setSelectedProjectType(e.target.value)}
                  className="text-[hsl(var(--text-primary))]"
                >
                  <option value="novel">{t("promptEditor.novel")}</option>
                  <option value="short">{t("promptEditor.short")}</option>
                  <option value="screenplay">{t("promptEditor.screenplay")}</option>
                </AdminSelect>
              </FormField>
            </div>
          ) : (
            <FormField label={t("promptEditor.projectType")}>
              <input
                type="text"
                value={decodeURIComponent(projectType!)}
                disabled
                className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--separator-color))] rounded-lg text-[hsl(var(--text-secondary))] cursor-not-allowed"
              />
            </FormField>
          )}

          <TouchCheckbox
            checked={formData.is_active}
            onChange={(checked) => handleInputChange("is_active", checked)}
            label={t("promptEditor.isActive")}
          />
        </FormSection>

        {/* Prompt 配置 */}
        <FormSection title={t("promptEditor.promptConfig")}>
          <FormField label={t("promptEditor.roleDefinition")}>
            <textarea
              aria-label={t("promptEditor.roleDefinition")}
              required
              value={formData.role_definition}
              onChange={(e) => handleInputChange("role_definition", e.target.value)}
              rows={6}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[120px]"
            />
          </FormField>

          <FormField label={t("promptEditor.capabilities")}>
            <textarea
              aria-label={t("promptEditor.capabilities")}
              required
              value={formData.capabilities}
              onChange={(e) => handleInputChange("capabilities", e.target.value)}
              rows={8}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[160px]"
            />
          </FormField>

          <FormField label={t("promptEditor.directoryStructure")}>
            <textarea
              value={formData.directory_structure}
              onChange={(e) => handleInputChange("directory_structure", e.target.value)}
              rows={10}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[200px]"
            />
          </FormField>

          <FormField label={t("promptEditor.contentStructure")}>
            <textarea
              value={formData.content_structure}
              onChange={(e) => handleInputChange("content_structure", e.target.value)}
              rows={16}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[320px]"
            />
          </FormField>

          <FormField label={t("promptEditor.fileTypes")}>
            <textarea
              value={formData.file_types}
              onChange={(e) => handleInputChange("file_types", e.target.value)}
              rows={6}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[120px]"
            />
          </FormField>

          <FormField label={t("promptEditor.writingGuidelines")}>
            <textarea
              value={formData.writing_guidelines}
              onChange={(e) => handleInputChange("writing_guidelines", e.target.value)}
              rows={20}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[400px]"
            />
          </FormField>

          <TouchCheckbox
            checked={formData.include_dialogue_guidelines}
            onChange={(checked) => handleInputChange("include_dialogue_guidelines", checked)}
            label={t("promptEditor.includeDialogueGuidelines")}
          />
        </FormSection>
      </div>

      {/* 删除确认对话框 */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <AlertTriangle size={24} className="text-red-500" />
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("prompts.delete")}
              </h2>
            </div>
            <div className="px-4 sm:px-6 py-4">
              <p className="text-[hsl(var(--text-secondary))]">
                {t("prompts.deleteConfirm")}
              </p>
            </div>
            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                disabled={deleteMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleDeleteConfirm}
                disabled={deleteMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--error))] text-white rounded-lg hover:bg-[hsl(var(--error)/0.9)] active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {deleteMutation.isPending ? t("common:loading") : t("common:confirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PromptEditor;
