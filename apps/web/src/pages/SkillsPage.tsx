import { useState, useEffect, useRef, useCallback } from "react";
import type { ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { LazyMarkdown } from "../components/LazyMarkdown";
import { Plus, Pencil, Trash2, Zap, Check, ChevronDown, ChevronUp, BarChart3, Share2, Compass, MinusCircle, Search, Square, CheckSquare, Download, Upload, AlertTriangle } from "../components/icons";
import { skillsApi, publicSkillsApi } from "../lib/api";
import { ApiError } from "../lib/apiClient";
import type { Skill, AddedSkill, CreateSkillRequest, UpdateSkillRequest, PublicSkill, SkillCategory, SkillShareStatus } from "../types";
import { PUBLIC_SKILLS_PAGE_SIZE, PUBLIC_SKILLS_SEARCH_DEBOUNCE_MS, SKILL_FIELD_LIMITS } from "../config/skills";
import { useIsMobile } from "../hooks/useMediaQuery";
import { useProject } from "../contexts/ProjectContext";
import { SkillStatsDialog } from "../components/SkillStatsDialog";
import { ShareSkillModal } from "../components/skills/ShareSkillModal";
import { SkillResourcesSection } from "../components/skills/SkillResourcesSection";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { DashboardFilterPills } from "../components/dashboard/DashboardFilterPills";
import { DashboardSearchBar } from "../components/dashboard/DashboardSearchBar";
import { DashboardEmptyState } from "../components/dashboard/DashboardEmptyState";
import { Modal } from "../components/ui/Modal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { UpgradePromptModal } from "../components/subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";

interface SkillFormData {
  name: string;
  description: string;
  triggers: string;
  instructions: string;
}

const emptyForm: SkillFormData = {
  name: "",
  description: "",
  triggers: "",
  instructions: "",
};

type TabType = "my-skills" | "discover";

/** Server rejects skill packages above 1 MiB; fail fast before uploading. */
const MAX_IMPORT_BYTES = 1024 * 1024;
const IMPORT_EXTENSIONS = [".zip", ".md"];

const SKILL_CATEGORY_LABEL_KEYS: Record<string, string> = {
  writing: "categories.writing",
  plot: "categories.plot",
  style: "categories.style",
  character: "categories.character",
  worldbuilding: "categories.worldbuilding",
};

const getLocalizedSkillCategory = (
  category: string,
  t: (key: string, options?: { defaultValue?: string }) => string
) => {
  const normalized = category.trim().toLowerCase();
  const translationKey = SKILL_CATEGORY_LABEL_KEYS[normalized];

  if (!translationKey) {
    return category;
  }

  const translated = t(translationKey, { defaultValue: category });
  return translated === translationKey ? category : translated;
};

export default function SkillsPage() {
  const { t } = useTranslation(["skills", "common"]);
  const skillCreateUpgradePrompt = getUpgradePromptDefinition("skill_create_quota_blocked");
  const isMobile = useIsMobile();
  const { currentProject } = useProject();

  // Tab state - default to discover tab
  const [activeTab, setActiveTab] = useState<TabType>("discover");

  // My Skills state
  const [userSkills, setUserSkills] = useState<Skill[]>([]);
  const [addedSkills, setAddedSkills] = useState<AddedSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasLoadedMySkills, setHasLoadedMySkills] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<SkillFormData>(emptyForm);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showStats, setShowStats] = useState(false);
  const [sharingSkill, setSharingSkill] = useState<Skill | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [mySkillsSearchQuery, setMySkillsSearchQuery] = useState("");
  const mySkillsSearchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevActiveTabRef = useRef<TabType>(activeTab);

  // Batch selection state
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [batchOperating, setBatchOperating] = useState(false);
  const [showSkillCreateUpgradeModal, setShowSkillCreateUpgradeModal] = useState(false);

  // Import / export state
  const importInputRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<{ skillName: string; warnings: string[] } | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);

  // Discover state
  const [publicSkills, setPublicSkills] = useState<PublicSkill[]>([]);
  const [categories, setCategories] = useState<SkillCategory[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  // searchInput 是输入框里的原文；searchQuery 是防抖（并且等输入法组字结束）之后真正用来查询的词
  const [searchInput, setSearchInput] = useState("");
  const [isSearchComposing, setIsSearchComposing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const [loadingMorePublic, setLoadingMorePublic] = useState(false);
  const [publicTotal, setPublicTotal] = useState(0);
  const [publicPage, setPublicPage] = useState(1);
  const [addingId, setAddingId] = useState<string | null>(null);
  const [addedPublicIds, setAddedPublicIds] = useState<Set<string>>(new Set());
  const publicSkillsRequestRef = useRef(0);

  // Define all async functions with useCallback before useEffect hooks
  const loadCategories = useCallback(async () => {
    try {
      const categoriesRes = await publicSkillsApi.getCategories();
      setCategories(categoriesRes.categories);
    } catch (error) {
      logger.error("Failed to load skill categories:", error);
    }
  }, []);

  const loadSkills = useCallback(async (search?: string, showLoading = false) => {
    try {
      // Only show loading spinner on initial load, not during search
      if (showLoading) {
        setLoading(true);
      }
      const response = await skillsApi.mySkills({ search: search || undefined });
      setUserSkills(response.user_skills);
      setAddedSkills(response.added_skills);
      // Track which public skills are already added
      const addedIds = new Set(response.added_skills.map(s => s.public_skill_id));
      setAddedPublicIds(addedIds);
    } catch (error) {
      logger.error("Failed to load skills:", error);
    } finally {
      setHasLoadedMySkills(true);
      if (showLoading) {
        setLoading(false);
      }
    }
  }, []);

  /** Returns false when the request failed (stale responses count as handled). */
  const loadPublicSkills = useCallback(async (page = 1): Promise<boolean> => {
    const requestId = ++publicSkillsRequestRef.current;
    if (page === 1) {
      setDiscoverLoading(true);
    } else {
      setLoadingMorePublic(true);
    }
    try {
      const response = await publicSkillsApi.list({
        category: selectedCategory || undefined,
        search: searchQuery || undefined,
        page,
        page_size: PUBLIC_SKILLS_PAGE_SIZE,
      });
      if (requestId !== publicSkillsRequestRef.current) return true;
      setPublicSkills((prev) => {
        if (page === 1) return response.skills;
        // 排序依据（添加次数）在两次请求之间可能变化，按 id 去重避免同一技能出现两次
        const seen = new Set(prev.map((skill) => skill.id));
        return [...prev, ...response.skills.filter((skill) => !seen.has(skill.id))];
      });
      setPublicTotal(response.total);
      setPublicPage(page);
      setAddedPublicIds((prev) => {
        const merged = new Set(prev);
        response.skills
          .filter((skill) => skill.is_added)
          .forEach((skill) => merged.add(skill.id));
        return merged;
      });
      return true;
    } catch (error) {
      if (requestId !== publicSkillsRequestRef.current) return true;
      logger.error("Failed to load public skills:", error);
      return false;
    } finally {
      if (requestId === publicSkillsRequestRef.current) {
        setDiscoverLoading(false);
        setLoadingMorePublic(false);
      }
    }
  }, [selectedCategory, searchQuery]);

  const handleLoadMorePublic = async () => {
    const ok = await loadPublicSkills(publicPage + 1);
    if (!ok) {
      toast.error(t("skills:errors.loadMoreFailed"));
    }
  };

  const showSkillError = useCallback((error: unknown, fallbackKey: string) => {
    // ApiError.message 已经是翻译过的后端错误（含 error_detail 原因）；其他错误用本地化兜底文案
    toast.error(error instanceof Error && error.message ? error.message : t(fallbackKey));
  }, [t]);

  // Categories are independent metadata; the list effect below owns list loading.
  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  // Load my skills data when tab changes to my-skills
  useEffect(() => {
    if (activeTab === "my-skills" && !hasLoadedMySkills && !loading) {
      loadSkills(undefined, true); // Initial load - show loading spinner
    }
  }, [activeTab, hasLoadedMySkills, loading, loadSkills]);

  // Reload public skills when search or category changes
  useEffect(() => {
    if (activeTab === "discover") {
      void loadPublicSkills(1);
    }
  }, [selectedCategory, searchQuery, activeTab, loadPublicSkills]);

  // Debounce the discover search box; never query with half-composed IME text.
  useEffect(() => {
    if (isSearchComposing) return;
    const next = searchInput.trim();
    if (next === searchQuery) return;
    const timer = setTimeout(() => setSearchQuery(next), PUBLIC_SKILLS_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, isSearchComposing, searchQuery]);

  // Debounced search for my skills (300ms)
  useEffect(() => {
    if (activeTab !== "my-skills") {
      prevActiveTabRef.current = activeTab;
      return;
    }

    // Check if this is a tab switch (not a search query change)
    const isTabSwitch = prevActiveTabRef.current !== activeTab;
    prevActiveTabRef.current = activeTab;

    // Skip reload on tab switch - initial load is handled by the other useEffect
    if (isTabSwitch) {
      return;
    }

    if (mySkillsSearchTimeoutRef.current) {
      clearTimeout(mySkillsSearchTimeoutRef.current);
    }

    mySkillsSearchTimeoutRef.current = setTimeout(() => {
      loadSkills(mySkillsSearchQuery);
    }, 300);

    return () => {
      if (mySkillsSearchTimeoutRef.current) {
        clearTimeout(mySkillsSearchTimeoutRef.current);
      }
    };
  }, [mySkillsSearchQuery, activeTab, loadSkills]);

  const handleAddPublicSkill = async (skillId: string) => {
    try {
      setAddingId(skillId);
      const result = await publicSkillsApi.add(skillId);

      if (result.success) {
        setPublicSkills((prev) =>
          prev.map((skill) =>
            skill.id === skillId
              ? { ...skill, is_added: true, add_count: skill.add_count + 1 }
              : skill
          )
        );
      }

      if (result.success || result.added_skill_id) {
        setAddedPublicIds((prev) => new Set([...prev, skillId]));
        // Refresh my skills to keep "my skills" and discover state in sync.
        await loadSkills();
      }
    } catch (error) {
      logger.error("Failed to add skill:", error);
      showSkillError(error, "skills:errors.addFailed");
    } finally {
      setAddingId(null);
    }
  };

  const handleCreate = () => {
    setFormData(emptyForm);
    setEditingSkill(null);
    setIsCreating(true);
  };

  const handleEdit = (skill: Skill) => {
    setFormData({
      name: skill.name,
      description: skill.description || "",
      triggers: skill.triggers.join(", "),
      instructions: skill.instructions,
    });
    setEditingSkill(skill);
    setIsCreating(true);
  };

  const handleSave = async () => {
    if (!formData.name.trim() || !formData.instructions.trim()) return;

    const triggers = formData.triggers
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
    if (
      triggers.length > SKILL_FIELD_LIMITS.triggers ||
      triggers.some((trigger) => trigger.length > SKILL_FIELD_LIMITS.trigger)
    ) {
      toast.error(t("skills:form.triggersTooLong", {
        count: SKILL_FIELD_LIMITS.triggers,
        max: SKILL_FIELD_LIMITS.trigger,
      }));
      return;
    }

    setSaving(true);
    try {
      if (editingSkill) {
        const updateData: UpdateSkillRequest = {
          name: formData.name,
          // 编辑时原样发送（含空字符串），清空描述才能生效
          description: formData.description,
          triggers,
          instructions: formData.instructions,
        };
        await skillsApi.update(editingSkill.id, updateData);
      } else {
        const createData: CreateSkillRequest = {
          name: formData.name,
          description: formData.description || undefined,
          triggers,
          instructions: formData.instructions,
        };
        await skillsApi.create(createData);
      }
      await loadSkills();
      setIsCreating(false);
      setEditingSkill(null);
      setFormData(emptyForm);
    } catch (error) {
      logger.error("Failed to save skill:", error);
      if (
        !editingSkill &&
        error instanceof ApiError &&
        error.errorCode === "ERR_QUOTA_EXCEEDED" &&
        skillCreateUpgradePrompt.surface === "modal"
      ) {
        setShowSkillCreateUpgradeModal(true);
      } else {
        showSkillError(error, "skills:errors.saveFailed");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await skillsApi.delete(id);
      await loadSkills();
      setDeletingId(null);
    } catch (error) {
      logger.error("Failed to delete skill:", error);
      showSkillError(error, "skills:errors.deleteFailed");
    }
  };

  const handleImportFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset so selecting the same file again still fires onChange
    e.target.value = "";
    if (!file) return;

    const lowerName = file.name.toLowerCase();
    if (!IMPORT_EXTENSIONS.some((ext) => lowerName.endsWith(ext))) {
      toast.error(t("skills:import.invalidType"));
      return;
    }
    if (file.size > MAX_IMPORT_BYTES) {
      toast.error(t("skills:import.tooLarge"));
      return;
    }

    setImporting(true);
    try {
      const result = await skillsApi.importSkill(file);
      setImportResult({ skillName: result.skill.name, warnings: result.warnings ?? [] });
      setActiveTab("my-skills");
      await loadSkills();
    } catch (error) {
      logger.error("Failed to import skill:", error);
      if (
        error instanceof ApiError &&
        error.errorCode === "ERR_QUOTA_EXCEEDED" &&
        skillCreateUpgradePrompt.surface === "modal"
      ) {
        setShowSkillCreateUpgradeModal(true);
      } else {
        toast.error(error instanceof Error && error.message ? error.message : t("skills:import.failed"));
      }
    } finally {
      setImporting(false);
    }
  };

  const handleExport = async (id: string, name: string) => {
    setExportingId(id);
    try {
      await skillsApi.exportSkill(id, name);
    } catch (error) {
      logger.error("Failed to export skill:", error);
      toast.error(error instanceof Error && error.message ? error.message : t("skills:export.failed"));
    } finally {
      setExportingId(null);
    }
  };

  const handleCancel = () => {
    setIsCreating(false);
    setEditingSkill(null);
    setFormData(emptyForm);
  };

  const handleRemoveAdded = async (publicSkillId: string) => {
    try {
      setRemovingId(publicSkillId);
      await publicSkillsApi.remove(publicSkillId);
      await loadSkills();
    } catch (error) {
      logger.error("Failed to remove skill:", error);
      showSkillError(error, "skills:errors.removeFailed");
    } finally {
      setRemovingId(null);
    }
  };

  // Batch operation handlers
  const handleToggleSelect = (skillId: string) => {
    setSelectedSkillIds(prev => {
      const newSet = new Set(prev);
      if (newSet.has(skillId)) {
        newSet.delete(skillId);
      } else {
        newSet.add(skillId);
      }
      return newSet;
    });
  };

  const handleSelectAll = () => {
    const allIds = [...userSkills.map(s => s.id), ...addedSkills.map(s => s.id)];
    if (selectedSkillIds.size === allIds.length) {
      setSelectedSkillIds(new Set());
    } else {
      setSelectedSkillIds(new Set(allIds));
    }
  };

  const handleClearSelection = () => {
    setSelectedSkillIds(new Set());
  };

  const handleBatchDelete = async () => {
    if (selectedSkillIds.size === 0) return;
    setBatchDeleting(true);
  };

  const handleConfirmBatchDelete = async () => {
    try {
      setBatchOperating(true);
      await skillsApi.batchUpdate(Array.from(selectedSkillIds), "delete");
      await loadSkills();
      setSelectedSkillIds(new Set());
      setBatchDeleting(false);
    } catch (error) {
      logger.error("Failed to batch delete:", error);
      showSkillError(error, "skills:errors.batchDeleteFailed");
    } finally {
      setBatchOperating(false);
    }
  };

  return (
    <div className="flex flex-col">
      {/* Header */}
      <DashboardPageHeader
        title={t("title")}
        subtitle={t("description")}
        action={
          <div className="flex items-center gap-2">
            <input
              ref={importInputRef}
              type="file"
              accept=".zip,.md"
              className="hidden"
              onChange={handleImportFile}
              data-testid="skill-import-input"
            />
            <button
              onClick={() => importInputRef.current?.click()}
              disabled={importing}
              className={`rounded-xl flex items-center justify-center gap-2 ${isMobile ? "px-3 py-2.5" : "h-11 px-4"} active:scale-95 transition-all text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-secondary))] disabled:opacity-50`}
              title={t("skills:import.hint")}
              aria-label={t("skills:import.button")}
            >
              {importing ? (
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
              ) : (
                <Upload className="w-4 h-4" />
              )}
              {!isMobile && t("skills:import.button")}
            </button>
            {currentProject && (
              <button
                onClick={() => setShowStats(true)}
                className={`rounded-xl flex items-center justify-center gap-2 ${isMobile ? "px-3 py-2.5" : "h-11 px-4"} active:scale-95 transition-all text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-secondary))]`}
                title={t("stats.title")}
              >
                <BarChart3 className="w-4 h-4" />
                {!isMobile && t("stats.title")}
              </button>
            )}
          </div>
        }
      />

      {/* Tabs */}
      <DashboardFilterPills
        options={[
          { value: "discover", label: t("discoverTab"), icon: Compass },
          { value: "my-skills", label: t("mySkillsTab") },
        ]}
        value={activeTab}
        onChange={(value) => setActiveTab(value as TabType)}
        className="mb-6"
      />

      {/* Tab Content */}
      {activeTab === "my-skills" ? (
        <MySkillsContent
          userSkills={userSkills}
          addedSkills={addedSkills}
          onEdit={handleEdit}
          onDelete={setDeletingId}
          onShare={setSharingSkill}
          onExport={handleExport}
          exportingId={exportingId}
          onCreate={handleCreate}
          onRemoveAdded={handleRemoveAdded}
          removingId={removingId}
          isMobile={isMobile}
          onSwitchToDiscover={() => setActiveTab("discover")}
          loading={loading}
          hasLoaded={hasLoadedMySkills}
          searchQuery={mySkillsSearchQuery}
          setSearchQuery={setMySkillsSearchQuery}
          selectedSkillIds={selectedSkillIds}
          onToggleSelect={handleToggleSelect}
          onSelectAll={handleSelectAll}
          onClearSelection={handleClearSelection}
          onBatchDelete={handleBatchDelete}
        />
      ) : (
        <DiscoverContent
          publicSkills={publicSkills}
          categories={categories}
          selectedCategory={selectedCategory}
          setSelectedCategory={setSelectedCategory}
          searchQuery={searchInput}
          setSearchQuery={setSearchInput}
          onSearchCompositionChange={setIsSearchComposing}
          loading={discoverLoading}
          loadingMore={loadingMorePublic}
          hasMore={publicSkills.length < publicTotal}
          onLoadMore={() => void handleLoadMorePublic()}
          addingId={addingId}
          addedPublicIds={addedPublicIds}
          onAdd={handleAddPublicSkill}
          isMobile={isMobile}
        />
      )}

      {/* Create/Edit Modal */}
      <Modal
        open={isCreating}
        onClose={handleCancel}
        title={editingSkill ? t("skills:editSkill") : t("skills:createSkill")}
        size="lg"
        footer={
          <>
            <button onClick={handleCancel} className="btn-ghost flex-1 h-11">
              {t("common:cancel")}
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !formData.name.trim() || !formData.instructions.trim()}
              className="btn-primary flex items-center justify-center gap-2 flex-1 h-11"
            >
              {saving ? (
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
              ) : (
                <Check className="w-4 h-4" />
              )}
              {t("common:save")}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t("skills:form.name")} *
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input"
              placeholder={t("skills:form.namePlaceholder")}
              maxLength={SKILL_FIELD_LIMITS.name}
            />
            <CharCount value={formData.name} max={SKILL_FIELD_LIMITS.name} />
          </div>

          <div>
            <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t("skills:form.description")}
            </label>
            <input
              type="text"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              className="input"
              placeholder={t("skills:form.descriptionPlaceholder")}
              maxLength={SKILL_FIELD_LIMITS.description}
            />
            <CharCount value={formData.description} max={SKILL_FIELD_LIMITS.description} />
          </div>

          <div>
            <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t("skills:form.triggers")}
            </label>
            <input
              type="text"
              value={formData.triggers}
              onChange={(e) => setFormData({ ...formData, triggers: e.target.value })}
              className="input"
              placeholder={t("skills:form.triggersPlaceholder")}
            />
            <p className="text-xs text-[hsl(var(--text-tertiary))] mt-1">
              {t("skills:form.triggersHint")}
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t("skills:form.instructions")} *
            </label>
            <textarea
              value={formData.instructions}
              onChange={(e) => setFormData({ ...formData, instructions: e.target.value })}
              className="input min-h-[120px] resize-y font-mono text-sm"
              placeholder={t("skills:form.instructionsPlaceholder")}
              maxLength={SKILL_FIELD_LIMITS.instructions}
            />
            <div className="flex items-start justify-between gap-3 mt-1">
              <p className="text-xs text-[hsl(var(--text-tertiary))]">
                {t("skills:form.instructionsHint")}
              </p>
              <CharCount value={formData.instructions} max={SKILL_FIELD_LIMITS.instructions} inline />
            </div>
          </div>

          {editingSkill ? (
            <SkillResourcesSection
              key={editingSkill.id}
              skillId={editingSkill.id}
              isMobile={isMobile}
              onChange={() => loadSkills()}
            />
          ) : (
            <p className="text-xs text-[hsl(var(--text-tertiary))]">
              {t("skills:resources.saveFirst")}
            </p>
          )}
        </div>
      </Modal>

      {/* Import Result */}
      <Modal
        open={!!importResult}
        onClose={() => setImportResult(null)}
        title={t("skills:import.successTitle")}
        size="md"
        footer={
          <button onClick={() => setImportResult(null)} className="btn-primary flex-1 h-11">
            {t("skills:import.done")}
          </button>
        }
      >
        {importResult && (
          <div className="space-y-3" data-testid="skill-import-result">
            <p className="text-sm text-[hsl(var(--text-primary))]">
              {t("skills:import.success", { name: importResult.skillName })}
            </p>
            {importResult.warnings.length > 0 && (
              <div className="rounded-lg border border-[hsl(var(--warning)/0.4)] bg-[hsl(var(--warning)/0.08)] p-3">
                <div className="flex items-center gap-2 mb-2 text-sm font-medium text-[hsl(var(--warning))]">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  {t("skills:import.warningsTitle", { count: importResult.warnings.length })}
                </div>
                <ul className="list-disc pl-5 space-y-1 text-xs text-[hsl(var(--text-secondary))] break-all">
                  {importResult.warnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={!!deletingId}
        onClose={() => setDeletingId(null)}
        onConfirm={() => handleDelete(deletingId!)}
        title={t("skills:deleteConfirm.title")}
        message={t("skills:deleteConfirm.message")}
        variant="danger"
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
      />

      {/* Batch Delete Confirmation */}
      <ConfirmDialog
        open={batchDeleting}
        onClose={() => setBatchDeleting(false)}
        onConfirm={handleConfirmBatchDelete}
        title={t("skills:batch.deleteConfirmTitle")}
        message={t("skills:batch.deleteConfirmMessage", { count: selectedSkillIds.size })}
        variant="danger"
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
        loading={batchOperating}
      />

      {/* Share Skill Modal */}
      {sharingSkill && (
        <ShareSkillModal
          skill={sharingSkill}
          onClose={() => setSharingSkill(null)}
          onSuccess={loadSkills}
          isMobile={isMobile}
        />
      )}

      {/* Stats Dialog */}
      {currentProject && (
        <SkillStatsDialog
          isOpen={showStats}
          onClose={() => setShowStats(false)}
          projectId={currentProject.id || ""}
        />
      )}

      <UpgradePromptModal
        open={showSkillCreateUpgradeModal}
        onClose={() => setShowSkillCreateUpgradeModal(false)}
        source={skillCreateUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t("skills:quota.createTitle", { defaultValue: "自定义技能数量已达上限" })}
        description={t("skills:quota.createDescription", {
          defaultValue: "当前套餐的自定义技能已用满，升级套餐后可以继续创建。",
        })}
        primaryLabel={t("skills:quota.upgradePrimary", { defaultValue: "查看升级方案" })}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(
              skillCreateUpgradePrompt.billingPath,
              skillCreateUpgradePrompt.source
            )
          );
        }}
        secondaryLabel={t("skills:quota.upgradeSecondary", { defaultValue: "查看套餐对比" })}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(
              skillCreateUpgradePrompt.pricingPath,
              skillCreateUpgradePrompt.source
            )
          );
        }}
      />
    </div>
  );
}

// Skill Card Component
function SkillCard({
  skill,
  onEdit,
  onDelete,
  onShare,
  onExport,
  exporting,
  readonly,
  isMobile,
  isSelected,
  onToggleSelect,
}: {
  skill: Skill;
  onEdit?: () => void;
  onDelete?: () => void;
  onShare?: () => void;
  onExport?: () => void;
  exporting?: boolean;
  readonly?: boolean;
  isMobile?: boolean;
  isSelected?: boolean;
  onToggleSelect?: () => void;
}) {
  const { t } = useTranslation(["skills"]);
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`group relative bg-[hsl(var(--bg-secondary))] rounded-lg border ${isSelected ? "border-[hsl(var(--accent-primary))]" : "border-[hsl(var(--border-color))]"} hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-lg transition-all ${isMobile ? "p-3" : "p-4"}`}>
      <div className="flex items-start justify-between gap-3">
        {/* Checkbox */}
        {onToggleSelect && (
          <button
            onClick={onToggleSelect}
            className="shrink-0 mt-1"
          >
            {isSelected ? (
              <CheckSquare className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            ) : (
              <Square className="w-5 h-5 text-[hsl(var(--text-tertiary))] hover:text-[hsl(var(--text-secondary))]" />
            )}
          </button>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <div className={`w-9 h-9 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center group-hover:scale-110 transition-transform shrink-0`}>
              <Zap className={`text-[hsl(var(--accent-primary))] ${isMobile ? "w-4 h-4" : "w-4.5 h-4.5"}`} />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className={`font-semibold text-[hsl(var(--text-primary))] truncate leading-snug ${isMobile ? "text-sm" : "text-sm"}`}>
                {skill.name}
              </h3>
            </div>
            {readonly && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-tertiary))] shrink-0">
                {t("skills:readonly")}
              </span>
            )}
            {skill.share_status && <ShareStatusBadge status={skill.share_status} />}
          </div>
          {skill.description && (
            <p className={`text-[hsl(var(--text-secondary))] mb-2.5 line-clamp-2 ${isMobile ? "text-xs" : "text-sm"}`}>
              {skill.description}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {skill.triggers.slice(0, isMobile ? 3 : 5).map((trigger, i) => (
              <span
                key={i}
                className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]"
              >
                {trigger}
              </span>
            ))}
            {skill.triggers.length > (isMobile ? 3 : 5) && (
              <span className="text-xs text-[hsl(var(--text-tertiary))]">
                +{skill.triggers.length - (isMobile ? 3 : 5)}
              </span>
            )}
            {!!skill.resource_count && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                {t("skills:resources.count", { count: skill.resource_count })}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {!readonly && (
            <>
              <button
                onClick={onEdit}
                title={t("skills:editSkill")}
                aria-label={t("skills:editSkill")}
                className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] transition-colors ${isMobile ? "p-1.5" : "p-2"}`}
              >
                <Pencil className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
              </button>
              <button
                onClick={onDelete}
                className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--error)/0.1)] hover:text-[hsl(var(--error))] transition-colors ${isMobile ? "p-1.5" : "p-2"}`}
              >
                <Trash2 className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
              </button>
              <button
                onClick={onShare}
                className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--accent-primary)/0.1)] hover:text-[hsl(var(--accent-primary))] transition-colors ${isMobile ? "p-1.5" : "p-2"}`}
                title={t("skills:share.title")}
              >
                <Share2 className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
              </button>
            </>
          )}
          {onExport && (
            <ExportButton onExport={onExport} exporting={exporting} isMobile={isMobile} />
          )}
          <button
            onClick={() => setExpanded(!expanded)}
            className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] transition-colors ${isMobile ? "p-1.5" : "p-2"}`}
            title={expanded ? t("skills:collapse") : t("skills:expand")}
          >
            {expanded ? (
              <ChevronUp className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
            ) : (
              <ChevronDown className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
            )}
          </button>
        </div>
      </div>

      {/* Instructions - Markdown rendered */}
      {expanded && skill.instructions && (
        <div className={`mt-3 pt-3 border-t border-[hsl(var(--border-color))]`}>
          <div className="flex items-center gap-1.5 mb-2">
            <span className={`text-xs font-medium text-[hsl(var(--text-tertiary))]`}>
              {t("form.instructions")}
            </span>
          </div>
          <div className={`markdown-content bg-[hsl(var(--bg-tertiary))] rounded-xl ${isMobile ? "p-3 text-xs" : "p-4 text-sm"}`}>
            <LazyMarkdown>{skill.instructions}</LazyMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

// My Skills Content Component
function MySkillsContent({
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
  searchQuery,
  setSearchQuery,
  selectedSkillIds,
  onToggleSelect,
  onSelectAll,
  onClearSelection,
  onBatchDelete,
}: {
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
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  selectedSkillIds: Set<string>;
  onToggleSelect: (id: string) => void;
  onSelectAll: () => void;
  onClearSelection: () => void;
  onBatchDelete: () => void;
}) {
  const { t } = useTranslation(["skills"]);

  if (loading || !hasLoaded) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
      </div>
    );
  }

  return (
    <div>
      {/* Search Box */}
      <DashboardSearchBar
        value={searchQuery}
        onChange={setSearchQuery}
        placeholder={t("searchMySkillsPlaceholder")}
        className="mb-6"
      />

      {/* Batch Action Bar */}
      {(userSkills.length > 0 || addedSkills.length > 0) && (
        <div className={`mb-4 flex ${isMobile ? "flex-col items-start gap-2" : "items-center justify-between"}`}>
          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={onSelectAll}
              className="flex items-center gap-2 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
            >
              {selectedSkillIds.size === userSkills.length + addedSkills.length && selectedSkillIds.size > 0 ? (
                <CheckSquare className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
              ) : (
                <Square className="w-4 h-4" />
              )}
              {t("batch.selectAll")}
            </button>
            {selectedSkillIds.size > 0 && (
              <span className="text-sm text-[hsl(var(--text-tertiary))]">
                {t("batch.selected", { count: selectedSkillIds.size })}
              </span>
            )}
          </div>
          {selectedSkillIds.size > 0 && (
            <div className={`flex items-center gap-2 ${isMobile ? "w-full flex-wrap" : ""}`}>
              <button
                onClick={onClearSelection}
                className="text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
              >
                {t("batch.clearSelection")}
              </button>
              <button
                onClick={onBatchDelete}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.2)] transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
                {t("batch.delete")}
              </button>
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
          <button
            onClick={onCreate}
            className={`btn-primary rounded-xl flex items-center justify-center gap-2 ${isMobile ? "px-3 py-2" : "h-9 px-4"} active:scale-95 transition-transform text-sm`}
          >
            <Plus className="w-4 h-4" />
            {!isMobile && t("create")}
          </button>
        </div>

        {userSkills.length === 0 ? (
          <DashboardEmptyState
            icon={Search}
            title={searchQuery ? t("noSearchResults") : t("noUserSkills")}
            action={
              !searchQuery && (
                <button
                  onClick={onCreate}
                  className="text-sm text-[hsl(var(--accent-primary))] hover:underline"
                >
                  {t("createFirst")}
                </button>
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
          <button onClick={onSwitchToDiscover} className="text-sm text-[hsl(var(--accent-primary))] hover:underline">
            {t("discoverMore")}
          </button>
        </div>

        {addedSkills.length === 0 ? (
          <DashboardEmptyState
            icon={Search}
            title={searchQuery ? t("noSearchResults") : t("noAddedSkills")}
            action={
              !searchQuery && (
                <button
                  onClick={onSwitchToDiscover}
                  className="text-sm text-[hsl(var(--accent-primary))] hover:underline"
                >
                  {t("browsePublic")}
                </button>
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
    </div>
  );
}

// Added Skill Card Component (for skills added from public library)
function AddedSkillCard({
  skill,
  onRemove,
  removing,
  onExport,
  exporting,
  isMobile,
  isSelected,
  onToggleSelect,
}: {
  skill: AddedSkill;
  onRemove: () => void;
  removing?: boolean;
  onExport?: () => void;
  exporting?: boolean;
  isMobile?: boolean;
  isSelected?: boolean;
  onToggleSelect?: () => void;
}) {
  const { t } = useTranslation(["skills"]);
  const [expanded, setExpanded] = useState(false);
  const localizedCategory = getLocalizedSkillCategory(skill.category, t);

  return (
    <div className={`group relative bg-[hsl(var(--bg-secondary))] rounded-lg border ${isSelected ? "border-[hsl(var(--accent-primary))]" : "border-[hsl(var(--border-color))]"} hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-lg transition-all ${isMobile ? "p-3" : "p-4"}`}>
      <div className="flex items-start justify-between gap-3">
        {/* Checkbox */}
        {onToggleSelect && (
          <button
            onClick={onToggleSelect}
            className="shrink-0 mt-1"
          >
            {isSelected ? (
              <CheckSquare className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
            ) : (
              <Square className="w-5 h-5 text-[hsl(var(--text-tertiary))] hover:text-[hsl(var(--text-secondary))]" />
            )}
          </button>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <div className={`w-9 h-9 rounded-lg bg-[hsl(var(--bg-tertiary))] flex items-center justify-center group-hover:scale-110 transition-transform shrink-0`}>
              <Zap className={`text-[hsl(var(--text-secondary))] ${isMobile ? "w-4 h-4" : "w-4.5 h-4.5"}`} />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className={`font-semibold text-[hsl(var(--text-primary))] truncate leading-snug ${isMobile ? "text-sm" : "text-sm"}`}>
                {skill.name}
              </h3>
            </div>
            <span className="text-xs px-1.5 py-0.5 rounded bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-tertiary))] shrink-0">
              {t("skills:added")}
            </span>
          </div>
          {skill.description && (
            <p className={`text-[hsl(var(--text-secondary))] mb-2.5 line-clamp-2 ${isMobile ? "text-xs" : "text-sm"}`}>
              {skill.description}
            </p>
          )}
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
              {localizedCategory}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={onRemove}
            disabled={removing}
            className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--error)/0.1)] hover:text-[hsl(var(--error))] transition-colors disabled:opacity-50 ${isMobile ? "p-1.5" : "p-2"}`}
            title={t("skills:remove")}
          >
            {removing ? (
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
            ) : (
              <MinusCircle className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
            )}
          </button>
          {onExport && (
            <ExportButton onExport={onExport} exporting={exporting} isMobile={isMobile} />
          )}
          <button
            onClick={() => setExpanded(!expanded)}
            className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] transition-colors ${isMobile ? "p-1.5" : "p-2"}`}
            title={expanded ? t("skills:collapse") : t("skills:expand")}
          >
            {expanded ? (
              <ChevronUp className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
            ) : (
              <ChevronDown className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
            )}
          </button>
        </div>
      </div>

      {/* Instructions - Markdown rendered */}
      {expanded && skill.instructions && (
        <div className={`mt-3 pt-3 border-t border-[hsl(var(--border-color))]`}>
          <div className="flex items-center gap-1.5 mb-2">
            <span className={`text-xs font-medium text-[hsl(var(--text-tertiary))]`}>
              {t("form.instructions")}
            </span>
          </div>
          <div className={`markdown-content bg-[hsl(var(--bg-tertiary))] rounded-xl ${isMobile ? "p-3 text-xs" : "p-4 text-sm"}`}>
            <LazyMarkdown>{skill.instructions}</LazyMarkdown>
          </div>
          <div className="mt-3">
            <SkillResourcesSection skillId={skill.id} readOnly isMobile={isMobile} />
          </div>
        </div>
      )}
    </div>
  );
}

// Export (download zip) button shared by user and added skill cards
function ExportButton({
  onExport,
  exporting,
  isMobile,
}: {
  onExport: () => void;
  exporting?: boolean;
  isMobile?: boolean;
}) {
  const { t } = useTranslation(["skills"]);
  return (
    <button
      onClick={onExport}
      disabled={exporting}
      className={`rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] transition-colors disabled:opacity-50 ${isMobile ? "p-1.5" : "p-2"}`}
      title={t("skills:export.button")}
      aria-label={t("skills:export.button")}
    >
      {exporting ? (
        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
      ) : (
        <Download className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
      )}
    </button>
  );
}

// Discover Content Component
function DiscoverContent({
  publicSkills,
  categories,
  selectedCategory,
  setSelectedCategory,
  searchQuery,
  setSearchQuery,
  onSearchCompositionChange,
  loading,
  loadingMore,
  hasMore,
  onLoadMore,
  addingId,
  addedPublicIds,
  onAdd,
  isMobile,
}: {
  publicSkills: PublicSkill[];
  categories: SkillCategory[];
  selectedCategory: string;
  setSelectedCategory: (cat: string) => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  onSearchCompositionChange: (composing: boolean) => void;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  addingId: string | null;
  addedPublicIds: Set<string>;
  onAdd: (id: string) => void;
  isMobile?: boolean;
}) {
  const { t } = useTranslation(["skills"]);

  // Build category filter options for DashboardFilterPills
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
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-tertiary))]" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onCompositionStart={() => onSearchCompositionChange(true)}
            onCompositionEnd={(e) => {
              onSearchCompositionChange(false);
              setSearchQuery(e.currentTarget.value);
            }}
            placeholder={t("searchPlaceholder")}
            className="input pl-10 w-full"
            data-testid="public-skill-search"
          />
        </div>
        <DashboardFilterPills
          options={categoryOptions}
          value={selectedCategory}
          onChange={setSelectedCategory}
        />
      </div>

      {/* Skills Grid */}
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[hsl(var(--accent-primary))]" />
        </div>
      ) : publicSkills.length === 0 ? (
        <DashboardEmptyState
          icon={Compass}
          title={t("noSkillsFound")}
        />
      ) : (
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
      )}

      {!loading && hasMore && (
        <div className="flex justify-center mt-6">
          <button
            onClick={onLoadMore}
            disabled={loadingMore}
            className="btn-ghost h-10 px-5 flex items-center gap-2 disabled:opacity-50"
          >
            {loadingMore && (
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
            )}
            {t("loadMore")}
          </button>
        </div>
      )}
    </div>
  );
}

// Public Skill Card Component
function PublicSkillCard({
  skill,
  isAdded,
  adding,
  onAdd,
  isMobile,
}: {
  skill: PublicSkill;
  isAdded: boolean;
  adding: boolean;
  onAdd: () => void;
  isMobile?: boolean;
}) {
  const { t } = useTranslation(["skills"]);
  const [expanded, setExpanded] = useState(false);
  const [fullInstructions, setFullInstructions] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const handleToggleExpanded = async () => {
    const next = !expanded;
    setExpanded(next);
    // 列表只带正文预览；展开时再取完整正文
    if (!next || !skill.instructions_truncated || fullInstructions !== null) return;
    setLoadingDetail(true);
    try {
      const detail = await publicSkillsApi.get(skill.id);
      setFullInstructions(detail.instructions);
    } catch (error) {
      logger.error("Failed to load public skill detail:", error);
      toast.error(error instanceof Error && error.message ? error.message : t("skills:errors.detailFailed"));
    } finally {
      setLoadingDetail(false);
    }
  };
  const instructions = fullInstructions ?? skill.instructions;

  return (
    <div className={`flex flex-col h-full bg-[hsl(var(--bg-primary))] rounded-xl border border-[hsl(var(--border-color))] hover:border-[hsl(var(--border-color)/0.8)] hover:shadow-sm transition-all ${isMobile ? "p-3" : "p-5"}`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-3">
        <div className="w-8 h-8 rounded-lg bg-[hsl(var(--accent-primary)/0.08)] flex items-center justify-center shrink-0">
          <Zap className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-medium text-[hsl(var(--text-primary))] text-sm truncate">{skill.name}</h3>
        </div>
        {skill.source === "official" && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.08)] text-[hsl(var(--accent-primary))] shrink-0">
            {t("official")}
          </span>
        )}
      </div>

      {/* Description - fixed 2-line height for alignment */}
      <p className="text-[13px] leading-relaxed text-[hsl(var(--text-secondary))] line-clamp-2 min-h-[2.6em] mb-4">
        {skill.description || "\u00A0"}
      </p>

      {/* Footer - pushed to bottom */}
      <div className="flex items-center justify-between mt-auto pt-3 border-t border-[hsl(var(--border-color)/0.5)]">
        <button
          onClick={() => void handleToggleExpanded()}
          className="text-xs text-[hsl(var(--text-tertiary))] hover:text-[hsl(var(--text-secondary))] flex items-center gap-1 transition-colors"
        >
          {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          {expanded ? t("collapse") : t("expand")}
        </button>

        <div className="flex items-center gap-3">
          <span className="text-xs text-[hsl(var(--text-tertiary))]">
            {skill.add_count} {t("addCount")}
          </span>
          <button
            onClick={onAdd}
            disabled={isAdded || adding}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              isAdded
                ? "text-[hsl(var(--text-tertiary))] cursor-default"
                : "text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.08)]"
            }`}
          >
            {adding ? (
              <div className="w-3 h-3 animate-spin rounded-full border-2 border-[hsl(var(--accent-primary))] border-t-transparent" />
            ) : isAdded ? (
              <>{t("alreadyAdded")}</>
            ) : (
              <>{t("addToMine")}</>
            )}
          </button>
        </div>
      </div>

      {/* Expanded instructions */}
      {expanded && instructions && (
        <div className="mt-3 pt-3 border-t border-[hsl(var(--border-color)/0.5)]">
          <div className="markdown-content bg-[hsl(var(--bg-secondary))] rounded-lg p-3 text-xs max-h-48 overflow-y-auto">
            {loadingDetail ? (
              <div className="flex justify-center py-4">
                <div className="w-4 h-4 animate-spin rounded-full border-2 border-[hsl(var(--accent-primary))] border-t-transparent" />
              </div>
            ) : (
              <LazyMarkdown>{instructions}</LazyMarkdown>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// Character counter shown under length-limited form fields
function CharCount({ value, max, inline }: { value: string; max: number; inline?: boolean }) {
  const nearLimit = value.length >= max * 0.9;
  return (
    <p
      className={`text-xs shrink-0 ${inline ? "" : "mt-1 text-right"} ${
        nearLimit ? "text-[hsl(var(--warning))]" : "text-[hsl(var(--text-tertiary))]"
      }`}
    >
      {value.length.toLocaleString()} / {max.toLocaleString()}
    </p>
  );
}

const SHARE_STATUS_STYLES: Record<SkillShareStatus, string> = {
  pending: "bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning))]",
  approved: "bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success))]",
  unpublished: "bg-[hsl(var(--error)/0.1)] text-[hsl(var(--error))]",
};

// Review status of a skill the user shared to the public library
function ShareStatusBadge({ status }: { status: SkillShareStatus }) {
  const { t } = useTranslation(["skills"]);
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded shrink-0 ${SHARE_STATUS_STYLES[status] ?? SHARE_STATUS_STYLES.pending}`}
      data-testid="skill-share-status"
    >
      {t(`skills:shareStatus.${status}`)}
    </span>
  );
}
