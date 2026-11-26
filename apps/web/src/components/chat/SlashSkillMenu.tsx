import React from "react";
import { Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MAX_SELECTED_SKILLS } from "../../contexts/SkillTriggerContext";
import type { Skill } from "../../types";

interface SlashSkillMenuProps {
  menuRef: React.RefObject<HTMLDivElement | null>;
  isLoading: boolean;
  loadFailed: boolean;
  hasLoadedAnySkills: boolean;
  filteredSkills: Skill[];
  selectedIndex: number;
  selectionFull: boolean;
  isSkillPickable: (skill: Skill) => boolean;
  onPickSkill: (skill: Skill) => void;
  onRetry: () => void;
}

export const SlashSkillMenu: React.FC<SlashSkillMenuProps> = ({
  menuRef,
  isLoading,
  loadFailed,
  hasLoadedAnySkills,
  filteredSkills,
  selectedIndex,
  selectionFull,
  isSkillPickable,
  onPickSkill,
  onRetry,
}) => {
  const { t } = useTranslation(["chat"]);

  return (
    <div
      ref={menuRef}
      className="shrink-0 relative bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg shadow-lg max-h-64 overflow-y-auto"
    >
      {isLoading ? (
        <div className="px-3 py-4 text-center text-sm text-[hsl(var(--text-secondary))]">
          {t("chat:skill.loading")}
        </div>
      ) : loadFailed && !hasLoadedAnySkills ? (
        <div className="px-3 py-4 flex flex-col items-center gap-2 text-center">
          <p role="alert" className="text-sm text-[hsl(var(--error-text))]">
            {t("chat:skill.loadFailed")}
          </p>
          <button
            type="button"
            onClick={onRetry}
            className="px-2.5 py-1 text-xs rounded bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary)/0.8)] transition-colors"
          >
            {t("chat:skill.retry")}
          </button>
        </div>
      ) : filteredSkills.length === 0 ? (
        <div className="px-3 py-4 text-center text-sm text-[hsl(var(--text-secondary))]">
          {t("chat:skill.noResults")}
        </div>
      ) : (
        <div className="py-1">
          {selectionFull && (
            <div
              role="status"
              className="px-3 py-2 text-xs text-[hsl(var(--warning-text))]"
            >
              {t("chat:skill.selectionFull", { max: MAX_SELECTED_SKILLS })}
            </div>
          )}
          {filteredSkills.map((skill, index) => (
            <button
              key={skill.id}
              type="button"
              onClick={() => onPickSkill(skill)}
              disabled={!isSkillPickable(skill)}
              className={`w-full px-3 py-2 text-left hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:bg-[hsl(var(--bg-tertiary))] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent ${
                index === selectedIndex
                  ? "bg-[hsl(var(--accent-primary)/0.1)] border-l-2 border-l-[hsl(var(--accent-primary))]"
                  : ""
              }`}
            >
              <div className="flex items-center gap-2">
                <Zap size={14} className="text-[hsl(var(--accent-primary))] shrink-0" />
                <span className="text-sm font-medium text-[hsl(var(--text-primary))]">
                  {skill.name}
                </span>
                <span className="text-xs text-[hsl(var(--text-secondary))] px-1.5 py-0.5 bg-[hsl(var(--bg-tertiary))] rounded">
                  {skill.source === "builtin" ? t("chat:skill.builtin") : t("chat:skill.user")}
                </span>
              </div>
              {skill.description && (
                <p className="mt-1 text-xs text-[hsl(var(--text-secondary))] line-clamp-1 ml-5">
                  {skill.description}
                </p>
              )}
              <div className="mt-1 flex flex-wrap gap-1 ml-5">
                {skill.triggers.slice(0, 3).map((trigger, i) => (
                  <span
                    key={i}
                    className="text-xs text-[hsl(var(--accent-primary)/0.8)] bg-[hsl(var(--accent-primary)/0.1)] px-1.5 py-0.5 rounded"
                  >
                    {trigger}
                  </span>
                ))}
                {skill.triggers.length > 3 && (
                  <span className="text-xs text-[hsl(var(--text-secondary))]">
                    +{skill.triggers.length - 3}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
      <div className="px-3 py-2 border-t border-[hsl(var(--border-color))] text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1.5">
        <span>{t("chat:skill.hint")}</span>
      </div>
    </div>
  );
};
