import React, { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { MAX_SELECTED_SKILLS, useSkillTrigger } from "../../contexts/SkillTriggerContext";
import { skillsApi } from "../../lib/api";
import type { Skill } from "../../types";
import { LazyMarkdown } from "../LazyMarkdown";
import { logger } from "../../lib/logger";
import {
  Zap,
  Plus,
  Check,
} from "lucide-react";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";

/**
 * SkillsPane - Skills browser component
 *
 * Sidebar pane for skills management.
 * Displays available skills with a detail modal. The "use" action selects the
 * skill for the next chat message (shown as a chip in MessageInput).
 */
export const SkillsPane: React.FC = () => {
  const { t } = useTranslation(['editor', 'common']);
  const { selectedSkills, selectSkill } = useSkillTrigger();
  const selectedIds = new Set(selectedSkills.map((s) => s.id));
  const selectionFull = selectedSkills.length >= MAX_SELECTED_SKILLS;

  // Skills state
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Load skills (again on retry)
  useEffect(() => {
    let cancelled = false;
    const loadSkills = async () => {
      try {
        setLoading(true);
        setLoadFailed(false);
        const response = await skillsApi.list();
        if (!cancelled) setSkills((response.skills || []).filter((skill) => skill.is_active));
      } catch (error) {
        logger.error("Failed to load skills:", error);
        // A failed request is not an empty skill list.
        if (!cancelled) setLoadFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadSkills();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // Handle skill click - show detail modal
  const handleSkillClick = useCallback((skill: Skill) => {
    setSelectedSkill(skill);
  }, []);

  // Handle close modal
  const handleCloseModal = useCallback(() => {
    setSelectedSkill(null);
  }, []);

  // Handle use skill - select it for the next message
  const handleUseSkill = useCallback((skill: Skill) => {
    selectSkill({ id: skill.id, name: skill.name });
  }, [selectSkill]);

  return (
    <div className="w-full h-full text-sm overflow-auto p-2">
      {/* Header */}
      <div className="flex items-center gap-2 mb-2 px-2">
        <Zap size={14} className="text-[hsl(var(--text-secondary))]" />
        <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {t('editor:fileTree.skillsFolder')}
        </span>
        <span className="text-xs text-[hsl(var(--text-secondary))]">
          ({skills.length})
        </span>
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
            onClick={() => setReloadKey((key) => key + 1)}
          >
            {t('common:retry')}
          </Button>
        </div>
      ) : skills.length === 0 ? (
        <div className="px-2 py-2 text-[hsl(var(--text-secondary))] text-xs italic">
          {t('editor:fileTree.skillsEmpty')}
        </div>
      ) : (
        <div className="mt-0.5">
          {skills.map((skill) => {
            const isSelected = selectedIds.has(skill.id);
            return (
            <div
              key={skill.id}
              className="flex items-center gap-2 px-2 py-1.5 rounded group transition-colors text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
            >
              {/* Opening details is its own native button, a sibling of the use button. */}
              <button
                type="button"
                aria-haspopup="dialog"
                onClick={() => handleSkillClick(skill)}
                className="flex min-w-0 flex-1 items-center gap-2 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
              >
                <span className="w-3.5 shrink-0" />
                <span className="text-[hsl(var(--text-secondary))]">
                  <Zap size={14} aria-hidden="true" />
                </span>
                <span className="flex-1 truncate">{skill.name}</span>
              </button>
              <button
                type="button"
                onClick={() => handleUseSkill(skill)}
                disabled={isSelected || selectionFull}
                aria-pressed={isSelected}
                className={`p-1 rounded transition-all focus-visible:opacity-100 disabled:cursor-default ${
                  isSelected
                    ? "opacity-100 text-[hsl(var(--accent-primary))]"
                    : "opacity-0 group-hover:opacity-100 hover:bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] disabled:opacity-0"
                }`}
                title={
                  isSelected
                    ? t('editor:fileTree.skillSelected')
                    : selectionFull
                      ? t('editor:fileTree.skillSelectionFull', { max: MAX_SELECTED_SKILLS })
                      : t('editor:fileTree.useSkill')
                }
                aria-label={`${t('editor:fileTree.useSkill')}: ${skill.name}`}
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
            <Button
              size="md"
              onClick={() => {
                handleUseSkill(selectedSkill);
                handleCloseModal();
              }}
              disabled={selectedIds.has(selectedSkill.id) || selectionFull}
            >
              {selectedIds.has(selectedSkill.id)
                ? t('editor:fileTree.skillSelected')
                : t('editor:fileTree.useSkill')}
            </Button>
          )}
          size="md"
        >
          {/* Description */}
          {selectedSkill.description && (
            <p className="text-sm text-[hsl(var(--text-secondary))] mb-4">
              {selectedSkill.description}
            </p>
          )}

          {/* Triggers */}
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
    </div>
  );
};
