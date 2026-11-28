import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useInRouterContext, useNavigate } from "react-router-dom";
import { MAX_SELECTED_SKILLS, useSkillTrigger } from "../../contexts/SkillTriggerContext";
import { skillsApi } from "../../lib/api";
import { ApiError } from "../../lib/apiClient";
import { toast } from "../../lib/toast";
import { SKILL_FIELD_LIMITS } from "../../config/skills";
import { getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { useIsMobile } from "../../hooks/useMediaQuery";
import type { CreateSkillRequest, Skill } from "../../types";
import { LazyMarkdown } from "../LazyMarkdown";
import { logger } from "../../lib/logger";
import {
  Zap,
  Plus,
  Check,
  Compass,
} from "lucide-react";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { SkillEditorModal } from "../skills/SkillEditorModal";
import { emptySkillForm, type SkillFormData } from "../skills/skillHelpers";
import { UpgradePromptModal } from "../subscription/UpgradePromptModal";

function RouterNavigateAction({
  to,
  render,
}: {
  to: string;
  render: (onNavigate: () => void) => React.ReactNode;
}) {
  const navigate = useNavigate();
  return <>{render(() => navigate(to))}</>;
}

function SafeNavigateAction({
  to,
  render,
}: {
  to: string;
  render: (onNavigate: () => void) => React.ReactNode;
}) {
  const inRouter = useInRouterContext();
  if (inRouter) {
    return <RouterNavigateAction to={to} render={render} />;
  }
  return <>{render(() => window.location.assign(to))}</>;
}

/**
 * SkillsPane - Skills browser component
 *
 * Sidebar pane for skills management.
 * Displays available skills with a detail modal, quick custom-skill creation,
 * and navigation to the full skills management & discovery page. The "use"
 * action selects the skill for the next chat message (shown as a chip in MessageInput).
 */
export const SkillsPane: React.FC = () => {
  const { t } = useTranslation(['editor', 'skills', 'common']);
  const isMobile = useIsMobile();
  const skillCreateUpgradePrompt = getUpgradePromptDefinition("skill_create_quota_blocked");
  const { selectedSkills, selectSkill, removeSkill } = useSkillTrigger();
  const selectedIds = useMemo(
    () => new Set(selectedSkills.map((s) => s.id)),
    [selectedSkills],
  );
  const selectionFull = selectedSkills.length >= MAX_SELECTED_SKILLS;

  // Skills state
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  // Quick-create skill modal state
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [createFormData, setCreateFormData] = useState<SkillFormData>(emptySkillForm);
  const [creatingSkill, setCreatingSkill] = useState(false);
  const [showSkillCreateUpgradeModal, setShowSkillCreateUpgradeModal] = useState(false);

  const mountedRef = useRef(true);
  const loadRequestRef = useRef(0);
  const creatingSkillRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const fetchSkills = useCallback(async (options?: { silent?: boolean }) => {
    const requestId = ++loadRequestRef.current;
    const silent = options?.silent ?? false;
    try {
      if (!silent) {
        setLoading(true);
        setLoadFailed(false);
      }
      const response = await skillsApi.list();
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;
      setSkills((response.skills || []).filter((skill) => skill.is_active));
      setLoadFailed(false);
    } catch (error) {
      logger.error("Failed to load skills:", error);
      // A failed request is not an empty skill list.
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;
      if (!silent) setLoadFailed(true);
    } finally {
      if (mountedRef.current && requestId === loadRequestRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // Load skills on mount
  useEffect(() => {
    void fetchSkills();
  }, [fetchSkills]);

  // Handle skill click - show detail modal
  const handleSkillClick = useCallback((skill: Skill) => {
    setSelectedSkill(skill);
  }, []);

  // Handle close modal
  const handleCloseModal = useCallback(() => {
    setSelectedSkill(null);
  }, []);

  // Toggle skill selection for the next message (clicking again removes it)
  const handleToggleSkill = useCallback((skill: Skill) => {
    if (selectedIds.has(skill.id)) {
      removeSkill(skill.id);
      return;
    }
    selectSkill({ id: skill.id, name: skill.name });
  }, [selectedIds, removeSkill, selectSkill]);

  const handleOpenCreate = useCallback(() => {
    setCreateFormData(emptySkillForm);
    setIsCreateModalOpen(true);
  }, []);

  const handleCancelCreate = useCallback(() => {
    if (creatingSkillRef.current || creatingSkill) return;
    setIsCreateModalOpen(false);
    setCreateFormData(emptySkillForm);
  }, [creatingSkill]);

  const handleSaveCreate = useCallback(async () => {
    if (
      creatingSkillRef.current ||
      creatingSkill ||
      !createFormData.name.trim() ||
      !createFormData.instructions.trim()
    ) {
      return;
    }

    const triggers = createFormData.triggers
      .split(/[,，]/)
      .map((trigger) => trigger.trim())
      .filter((trigger) => trigger.length > 0);
    if (
      triggers.length > SKILL_FIELD_LIMITS.triggers ||
      triggers.some((trigger) => trigger.length > SKILL_FIELD_LIMITS.trigger)
    ) {
      toast.error(t('skills:form.triggersTooLong', {
        count: SKILL_FIELD_LIMITS.triggers,
        max: SKILL_FIELD_LIMITS.trigger,
      }));
      return;
    }

    creatingSkillRef.current = true;
    setCreatingSkill(true);
    try {
      const createData: CreateSkillRequest = {
        name: createFormData.name,
        description: createFormData.description || undefined,
        triggers,
        instructions: createFormData.instructions,
      };
      const created = await skillsApi.create(createData);
      if (!mountedRef.current) return;
      if (created && created.is_active) {
        setSkills((prev) =>
          prev.some((skill) => skill.id === created.id) ? prev : [...prev, created],
        );
      }
      await fetchSkills({ silent: true });
      if (!mountedRef.current) return;
      setIsCreateModalOpen(false);
      setCreateFormData(emptySkillForm);
    } catch (error) {
      logger.error("Failed to create skill:", error);
      if (!mountedRef.current) return;
      if (
        error instanceof ApiError &&
        error.errorCode === "ERR_QUOTA_EXCEEDED"
      ) {
        if (skillCreateUpgradePrompt.surface === "modal") {
          setShowSkillCreateUpgradeModal(true);
        } else {
          toast.error(error.message || t('skills:quota.createTitle'));
        }
      } else {
        toast.error(
          error instanceof Error && error.message
            ? error.message
            : t('skills:errors.saveFailed'),
        );
      }
    } finally {
      creatingSkillRef.current = false;
      if (mountedRef.current) setCreatingSkill(false);
    }
  }, [createFormData, creatingSkill, fetchSkills, skillCreateUpgradePrompt.surface, t]);

  return (
    <div className="w-full h-full text-sm overflow-auto p-2">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 mb-2 px-2">
        <div className="flex min-w-0 items-center gap-2">
          <Zap size={14} className="shrink-0 text-[hsl(var(--text-secondary))]" aria-hidden="true" />
          <span className="truncate text-sm font-medium text-[hsl(var(--text-primary))]">
            {t('editor:fileTree.skillsFolder')}
          </span>
          <span className="shrink-0 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
            ({skills.length})
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconButton
            onClick={handleOpenCreate}
            label={t('editor:fileTree.createSkill')}
            icon={<Plus size={15} />}
          />
          <SafeNavigateAction
            to="/skills"
            render={(onNavigate) => (
              <IconButton
                onClick={onNavigate}
                label={t('editor:fileTree.manageSkills')}
                icon={<Compass size={15} />}
              />
            )}
          />
        </div>
      </div>

      {/* Skills list */}
      {loading ? (
        <div className="px-2 py-2 text-[hsl(var(--text-secondary))] text-xs italic">
          {t('common:loading')}
        </div>
      ) : loadFailed ? (
        <div role="alert" className="px-2 py-2 text-xs">
          <p className="text-[hsl(var(--error-text))]">{t('common:error')}</p>
          <Button
            size="sm"
            variant="secondary"
            className="mt-2 max-md:min-h-11"
            onClick={() => void fetchSkills()}
          >
            {t('common:retry')}
          </Button>
        </div>
      ) : skills.length === 0 ? (
        <div className="mx-1 mt-1 rounded-xl border border-dashed border-[hsl(var(--border-color))] px-3 py-4 text-center">
          <p className="text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
            {t('editor:fileTree.skillsEmpty')}
          </p>
          <div className="mt-3 flex flex-col gap-2">
            <Button
              size="sm"
              variant="primary"
              className="w-full max-md:min-h-11"
              leftIcon={<Plus size={14} />}
              onClick={handleOpenCreate}
            >
              {t('editor:fileTree.createSkill')}
            </Button>
            <SafeNavigateAction
              to="/skills"
              render={(onNavigate) => (
                <Button
                  size="sm"
                  variant="secondary"
                  className="w-full max-md:min-h-11"
                  leftIcon={<Compass size={14} />}
                  onClick={onNavigate}
                >
                  {t('editor:fileTree.discoverSkills')}
                </Button>
              )}
            />
          </div>
        </div>
      ) : (
        <div className="mt-0.5">
          {skills.map((skill) => {
            const isSelected = selectedIds.has(skill.id);
            return (
            <div
              key={skill.id}
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg group transition-colors text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
            >
              {/* Opening details is its own native button, a sibling of the use button. */}
              <button
                type="button"
                aria-haspopup="dialog"
                onClick={() => handleSkillClick(skill)}
                className="flex min-w-0 flex-1 items-center gap-2 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
              >
                <span className="shrink-0 text-[hsl(var(--text-secondary))]">
                  <Zap size={14} aria-hidden="true" />
                </span>
                <span className="flex-1 truncate">{skill.name}</span>
              </button>
              <button
                type="button"
                onClick={() => handleToggleSkill(skill)}
                disabled={!isSelected && selectionFull}
                aria-pressed={isSelected}
                className={`p-1.5 rounded-lg max-md:min-h-11 max-md:min-w-11 [@media(hover:none)]:min-h-11 [@media(hover:none)]:min-w-11 flex items-center justify-center transition-all focus-visible:opacity-100 disabled:cursor-not-allowed ${
                  isSelected
                    ? "opacity-100 text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.12)]"
                    : selectionFull
                      ? "opacity-0 group-hover:opacity-40 group-focus-within:opacity-40 [@media(hover:none)]:opacity-40 text-[hsl(var(--text-secondary))]"
                      : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 hover:bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]"
                }`}
                title={
                  isSelected
                    ? t('editor:fileTree.removeSkill')
                    : selectionFull
                      ? t('editor:fileTree.skillSelectionFull', { max: MAX_SELECTED_SKILLS })
                      : t('editor:fileTree.useSkill')
                }
                aria-label={`${isSelected ? t('editor:fileTree.removeSkill') : t('editor:fileTree.useSkill')}: ${skill.name}`}
              >
                {isSelected ? <Check size={14} /> : <Plus size={14} />}
              </button>
            </div>
            );
          })}
        </div>
      )}

      {/* Skill detail modal */}
      {selectedSkill && (
        <Modal
          open
          onClose={handleCloseModal}
          title={(
            <div className="flex items-center gap-2">
              <Zap size={18} className="text-[hsl(var(--accent-primary))]" />
              <span>{selectedSkill.name}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                selectedSkill.source === 'builtin'
                  ? 'bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]'
                  : 'bg-[hsl(var(--success)/0.1)] text-[hsl(var(--success-text))]'
              }`}>
                {selectedSkill.source === 'builtin' ? t('editor:fileTree.builtinSkill') : t('editor:fileTree.userSkill')}
              </span>
            </div>
          )}
          footer={(
            <div className="flex w-full items-center justify-end gap-3">
              {!selectedIds.has(selectedSkill.id) && selectionFull && (
                <span className="mr-auto text-xs text-[hsl(var(--text-secondary))]">
                  {t('editor:fileTree.skillSelectionFull', { max: MAX_SELECTED_SKILLS })}
                </span>
              )}
              <Button
                size="touch"
                variant={selectedIds.has(selectedSkill.id) ? "secondary" : "primary"}
                onClick={() => {
                  handleToggleSkill(selectedSkill);
                  handleCloseModal();
                }}
                disabled={!selectedIds.has(selectedSkill.id) && selectionFull}
                title={
                  !selectedIds.has(selectedSkill.id) && selectionFull
                    ? t('editor:fileTree.skillSelectionFull', { max: MAX_SELECTED_SKILLS })
                    : undefined
                }
              >
                {selectedIds.has(selectedSkill.id)
                  ? t('editor:fileTree.removeSkill')
                  : t('editor:fileTree.useSkill')}
              </Button>
            </div>
          )}
          size="md"
        >
          {/* Description */}
          {selectedSkill.description && (
            <p className="text-sm text-[hsl(var(--text-secondary))] mb-4">
              {selectedSkill.description}
            </p>
          )}

          {/* Triggers — only shown when the skill defines triggers */}
          {selectedSkill.triggers.length > 0 && (
            <div className="mb-4">
              <h4 className="text-xs font-medium text-[hsl(var(--text-secondary))] uppercase mb-2">
                {t('editor:fileTree.skillTriggers')}
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {selectedSkill.triggers.map((trigger, idx) => (
                  <span
                    key={idx}
                    className="px-2 py-1 text-xs bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] rounded"
                  >
                    {trigger}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Instructions (added official skills: not returned by the API) */}
          {selectedSkill.instructions_hidden ? (
            <p className="text-xs text-[hsl(var(--text-tertiary))]">
              {t('editor:fileTree.skillInstructionsHidden')}
            </p>
          ) : (
            <div>
              <h4 className="text-xs font-medium text-[hsl(var(--text-secondary))] uppercase mb-2">
                {t('editor:fileTree.skillInstructions')}
              </h4>
              <div className="markdown-content p-3 bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))]">
                <LazyMarkdown>
                  {selectedSkill.instructions}
                </LazyMarkdown>
              </div>
            </div>
          )}
        </Modal>
      )}

      {/* Quick create custom skill modal */}
      <SkillEditorModal
        open={isCreateModalOpen}
        editingSkill={null}
        formData={createFormData}
        onFormChange={setCreateFormData}
        saving={creatingSkill}
        onCancel={handleCancelCreate}
        onSave={() => void handleSaveCreate()}
        onResourcesChange={() => {}}
        isMobile={isMobile}
      />

      {showSkillCreateUpgradeModal && (
        <UpgradePromptModal
          open={showSkillCreateUpgradeModal}
          onClose={() => setShowSkillCreateUpgradeModal(false)}
          promptId="skill_create_quota_blocked"
          title={t("skills:quota.createTitle")}
          description={t("skills:quota.createDescription")}
          paidDescription={t("skills:quota.createPaidDescription")}
          primaryLabel={t("skills:quota.upgradePrimary")}
          secondaryLabel={t("skills:quota.upgradeSecondary")}
        />
      )}
    </div>
  );
};
