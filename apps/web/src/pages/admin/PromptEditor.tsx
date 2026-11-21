import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
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
          <AlertTriangle className="text-[hsl(var(--error-text))]" size={32} />
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
            aria-label={t("common:back")}
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
            <Button
              type="button"
              variant="danger"
              size="touch"
              leftIcon={<Trash2 size={18} />}
              onClick={handleDeleteClick}
              disabled={deleteMutation.isPending}
              className="hidden sm:inline-flex"
            >
              {t("prompts.delete")}
            </Button>
          )}
          <Button
            type="button"
            variant="primary"
            size="touch"
            leftIcon={saveMutation.isPending ? <Loader2 size={18} className="animate-spin" /> : <Save size={18} />}
            onClick={handleSave}
            disabled={saveMutation.isPending || !canSave}
            className="flex-1 sm:flex-none"
          >
            {t("promptEditor.save")}
          </Button>
        </div>
        {!isNew && (
          <Button
            type="button"
            variant="danger"
            size="touch"
            leftIcon={<Trash2 size={18} />}
            onClick={handleDeleteClick}
            disabled={deleteMutation.isPending}
            className="sm:hidden w-full"
          >
            {t("prompts.delete")}
          </Button>
        )}
      </div>

      {/* 表单 */}
      <div className="space-y-6">
        {/* 基本信息 */}
        <FormSection title={t("promptEditor.basicInfo")}>
          {isNew ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
              <FormField htmlFor="prompt-project-type" label={t("promptEditor.projectType")} className="sm:col-span-2">
                <AdminSelect
                  id="prompt-project-type"
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
            <FormField htmlFor="prompt-project-type" label={t("promptEditor.projectType")}>
              <input
                id="prompt-project-type"
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
          <FormField htmlFor="prompt-role-definition" label={t("promptEditor.roleDefinition")}>
            <textarea
              id="prompt-role-definition"
              aria-label={t("promptEditor.roleDefinition")}
              required
              value={formData.role_definition}
              onChange={(e) => handleInputChange("role_definition", e.target.value)}
              rows={6}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[120px]"
            />
          </FormField>

          <FormField htmlFor="prompt-capabilities" label={t("promptEditor.capabilities")}>
            <textarea
              id="prompt-capabilities"
              aria-label={t("promptEditor.capabilities")}
              required
              value={formData.capabilities}
              onChange={(e) => handleInputChange("capabilities", e.target.value)}
              rows={8}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[160px]"
            />
          </FormField>

          <FormField htmlFor="prompt-directory-structure" label={t("promptEditor.directoryStructure")}>
            <textarea
              id="prompt-directory-structure"
              value={formData.directory_structure}
              onChange={(e) => handleInputChange("directory_structure", e.target.value)}
              rows={10}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[200px]"
            />
          </FormField>

          <FormField htmlFor="prompt-content-structure" label={t("promptEditor.contentStructure")}>
            <textarea
              id="prompt-content-structure"
              value={formData.content_structure}
              onChange={(e) => handleInputChange("content_structure", e.target.value)}
              rows={16}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[320px]"
            />
          </FormField>

          <FormField htmlFor="prompt-file-types" label={t("promptEditor.fileTypes")}>
            <textarea
              id="prompt-file-types"
              value={formData.file_types}
              onChange={(e) => handleInputChange("file_types", e.target.value)}
              rows={6}
              className="w-full px-3 py-2.5 text-sm sm:text-base bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-y min-h-[120px]"
            />
          </FormField>

          <FormField htmlFor="prompt-writing-guidelines" label={t("promptEditor.writingGuidelines")}>
            <textarea
              id="prompt-writing-guidelines"
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
        <Modal
          open
          ariaLabel={t("prompts.delete")}
          onClose={() => setShowDeleteConfirm(false)}
          closeOnEscape={!(deleteMutation.isPending)}
          closeOnBackdropClick={false}
          showCloseButton={false}
          bodyClassName="p-0"
          className="admin-shell rounded-xl!"
          size="md"
        >
          <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
            <AlertTriangle size={24} className="text-[hsl(var(--error-text))]" />
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
            <Button
              type="button"
              variant="secondary"
              size="touch"
              onClick={() => setShowDeleteConfirm(false)}
              disabled={deleteMutation.isPending}
              className="w-full sm:w-auto"
            >
              {t("common:cancel")}
            </Button>
            <Button
              type="button"
              variant="danger"
              size="touch"
              onClick={handleDeleteConfirm}
              disabled={deleteMutation.isPending}
              className="w-full sm:w-auto"
            >
              {deleteMutation.isPending ? t("common:loading") : t("common:confirm")}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
};

export default PromptEditor;
