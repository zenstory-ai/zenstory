import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { publicSkillsApi, skillsApi } from "../../lib/api";
import { ApiError } from "../../lib/apiClient";
import { logger } from "../../lib/logger";
import { toast } from "../../lib/toast";
import { PUBLIC_SKILLS_PAGE_SIZE, PUBLIC_SKILLS_SEARCH_DEBOUNCE_MS, SKILL_FIELD_LIMITS } from "../../config/skills";
import { getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { useAnimatedPresence } from "../../hooks/useAnimatedPresence";
import type {
  AddedSkill,
  CreateSkillRequest,
  PublicSkill,
  Skill,
  SkillCategory,
  UpdateSkillRequest,
} from "../../types";
import type { SkillImportResult } from "./SkillImportResultModal";
import {
  emptySkillForm,
  IMPORT_EXTENSIONS,
  MAX_IMPORT_BYTES,
  type SkillFormData,
  type SkillsTabType,
} from "./skillHelpers";

export function useSkillsPageState() {
  const { t } = useTranslation(["skills", "common"]);
  const skillCreateUpgradePrompt = getUpgradePromptDefinition("skill_create_quota_blocked");

  // Tab state - default to discover tab
  const [activeTab, setActiveTab] = useState<SkillsTabType>("discover");

  // My Skills state
  const [userSkills, setUserSkills] = useState<Skill[]>([]);
  const [addedSkills, setAddedSkills] = useState<AddedSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasLoadedMySkills, setHasLoadedMySkills] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [formData, setFormData] = useState<SkillFormData>(emptySkillForm);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deletingSkill, setDeletingSkill] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sharingSkill, setSharingSkill] = useState<Skill | null>(null);
  const [lastSharingSkill, setLastSharingSkill] = useState<Skill | null>(null);
  if (sharingSkill && sharingSkill !== lastSharingSkill) {
    setLastSharingSkill(sharingSkill);
  }
  const { isMounted: isShareModalMounted } = useAnimatedPresence(Boolean(sharingSkill), 150);
  const renderedSharingSkill = sharingSkill ?? lastSharingSkill;
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [mySkillsSearchQuery, setMySkillsSearchQuery] = useState("");
  const mySkillsSearchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevActiveTabRef = useRef<SkillsTabType>(activeTab);

  // Batch selection state
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [batchOperating, setBatchOperating] = useState(false);
  const [showSkillCreateUpgradeModal, setShowSkillCreateUpgradeModal] = useState(false);

  // Import / export state
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<SkillImportResult | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);

  // Discover state
  const [publicSkills, setPublicSkills] = useState<PublicSkill[]>([]);
  const [categories, setCategories] = useState<SkillCategory[]>([]);
  const [categoriesError, setCategoriesError] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState<string>("");
  // searchInput 是输入框里的原文；searchQuery 是防抖（并且等输入法组字结束）之后真正用来查询的词
  const [searchInput, setSearchInput] = useState("");
  const [isSearchComposing, setIsSearchComposing] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [discoverLoading, setDiscoverLoading] = useState(false);
  const [discoverError, setDiscoverError] = useState(false);
  const [mySkillsError, setMySkillsError] = useState(false);
  const [loadingMorePublic, setLoadingMorePublic] = useState(false);
  const [publicTotal, setPublicTotal] = useState(0);
  const [publicPage, setPublicPage] = useState(1);
  const [addingId, setAddingId] = useState<string | null>(null);
  const [addedPublicIds, setAddedPublicIds] = useState<Set<string>>(new Set());
  const publicSkillsRequestRef = useRef(0);
  const mySkillsRequestRef = useRef(0);
  const formRequestRef = useRef(0);
  const ownerRef = useRef<object | null>(null);

  useEffect(() => {
    const owner = {};
    ownerRef.current = owner;
    return () => {
      if (ownerRef.current === owner) ownerRef.current = null;
    };
  }, []);

  const loadCategories = useCallback(async () => {
    const owner = ownerRef.current;
    if (!owner) return;
    setCategoriesError(false);
    try {
      const categoriesRes = await publicSkillsApi.getCategories();
      if (owner !== ownerRef.current) return;
      setCategories(categoriesRes.categories);
    } catch (error) {
      if (owner !== ownerRef.current) return;
      logger.error("Failed to load skill categories:", error);
      setCategoriesError(true);
    }
  }, []);

  const loadSkills = useCallback(async (search?: string, showLoading = false) => {
    const owner = ownerRef.current;
    if (!owner) return;
    const requestId = ++mySkillsRequestRef.current;
    setMySkillsError(false);
    try {
      // Only show loading spinner on initial load, not during search
      if (showLoading) {
        setLoading(true);
      }
      const response = await skillsApi.mySkills({ search: search || undefined });
      if (owner !== ownerRef.current || requestId !== mySkillsRequestRef.current) return;
      setUserSkills(response.user_skills);
      setAddedSkills(response.added_skills);
      // Track which public skills are already added
      const addedIds = new Set(response.added_skills.map((s) => s.public_skill_id));
      setAddedPublicIds(addedIds);
    } catch (error) {
      if (owner !== ownerRef.current || requestId !== mySkillsRequestRef.current) return;
      logger.error("Failed to load skills:", error);
      setMySkillsError(true);
    } finally {
      if (owner === ownerRef.current && requestId === mySkillsRequestRef.current) {
        setHasLoadedMySkills(true);
        setLoading(false);
      }
    }
  }, []);

  /** Returns false when the request failed (stale responses count as handled). */
  const loadPublicSkills = useCallback(async (page = 1): Promise<boolean> => {
    const requestId = ++publicSkillsRequestRef.current;
    if (page === 1) {
      setDiscoverLoading(true);
      setDiscoverError(false);
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
      if (page === 1) setDiscoverError(true);
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
              : skill,
          ),
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
    formRequestRef.current += 1;
    setFormData(emptySkillForm);
    setEditingSkill(null);
    setIsCreating(true);
  };

  const handleEdit = (skill: Skill) => {
    formRequestRef.current += 1;
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
    const owner = ownerRef.current;
    if (!owner) return;
    const formRequestId = formRequestRef.current;

    const triggers = formData.triggers
      .split(",")
      .map((trigger) => trigger.trim())
      .filter((trigger) => trigger.length > 0);
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
      if (owner !== ownerRef.current) return;
      await loadSkills();
      if (owner === ownerRef.current && formRequestId === formRequestRef.current) {
        setIsCreating(false);
        setEditingSkill(null);
        setFormData(emptySkillForm);
      }
    } catch (error) {
      logger.error("Failed to save skill:", error);
      if (
        !editingSkill &&
        error instanceof ApiError &&
        error.errorCode === "ERR_QUOTA_EXCEEDED" &&
        skillCreateUpgradePrompt.surface === "modal"
      ) {
        if (owner === ownerRef.current && formRequestId === formRequestRef.current) {
          setShowSkillCreateUpgradeModal(true);
        }
      } else {
        showSkillError(error, "skills:errors.saveFailed");
      }
    } finally {
      if (owner === ownerRef.current) setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (deletingSkill) return;
    setDeletingSkill(true);
    try {
      await skillsApi.delete(id);
      await loadSkills();
      setDeletingId(null);
    } catch (error) {
      logger.error("Failed to delete skill:", error);
      showSkillError(error, "skills:errors.deleteFailed");
    } finally {
      setDeletingSkill(false);
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

    const owner = ownerRef.current;
    if (!owner) return;
    setImporting(true);
    try {
      const result = await skillsApi.importSkill(file);
      if (owner !== ownerRef.current) return;
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
        if (owner === ownerRef.current) setShowSkillCreateUpgradeModal(true);
      } else {
        toast.error(error instanceof Error && error.message ? error.message : t("skills:import.failed"));
      }
    } finally {
      if (owner === ownerRef.current) setImporting(false);
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
    if (saving) return;
    formRequestRef.current += 1;
    setIsCreating(false);
    setEditingSkill(null);
    setFormData(emptySkillForm);
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

  const handleToggleSelect = (skillId: string) => {
    setSelectedSkillIds((prev) => {
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
    const allIds = [...userSkills.map((s) => s.id), ...addedSkills.map((s) => s.id)];
    if (selectedSkillIds.size === allIds.length) {
      setSelectedSkillIds(new Set());
    } else {
      setSelectedSkillIds(new Set(allIds));
    }
  };

  const handleClearSelection = () => {
    setSelectedSkillIds(new Set());
  };

  const handleBatchDelete = () => {
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

  const handleResetDiscoverFilters = () => {
    setSearchInput("");
    setSearchQuery("");
    setSelectedCategory("");
  };

  return {
    activeTab,
    setActiveTab,
    userSkills,
    addedSkills,
    loading,
    hasLoadedMySkills,
    mySkillsError,
    editingSkill,
    isCreating,
    formData,
    setFormData,
    deletingId,
    setDeletingId,
    deletingSkill,
    saving,
    sharingSkill,
    setSharingSkill,
    isShareModalMounted,
    renderedSharingSkill,
    removingId,
    mySkillsSearchQuery,
    setMySkillsSearchQuery,
    selectedSkillIds,
    batchDeleting,
    setBatchDeleting,
    batchOperating,
    showSkillCreateUpgradeModal,
    setShowSkillCreateUpgradeModal,
    importing,
    importResult,
    setImportResult,
    exportingId,
    publicSkills,
    categories,
    categoriesError,
    selectedCategory,
    setSelectedCategory,
    searchInput,
    setSearchInput,
    setIsSearchComposing,
    searchQuery,
    discoverLoading,
    discoverError,
    loadingMorePublic,
    hasMorePublic: publicSkills.length < publicTotal,
    addingId,
    addedPublicIds,
    loadCategories,
    loadSkills,
    loadPublicSkills,
    handleLoadMorePublic,
    handleAddPublicSkill,
    handleCreate,
    handleEdit,
    handleSave,
    handleDelete,
    handleImportFile,
    handleExport,
    handleCancel,
    handleRemoveAdded,
    handleToggleSelect,
    handleSelectAll,
    handleClearSelection,
    handleBatchDelete,
    handleConfirmBatchDelete,
    handleResetDiscoverFilters,
  };
}
