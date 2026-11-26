import React from "react";
import { FileText, Quote, X, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AttachedMaterial } from "../../contexts/MaterialAttachmentContext";
import type { TextQuote } from "../../types";
import { MAX_SELECTED_SKILLS, type SelectedSkill } from "../../contexts/SkillTriggerContext";

interface ChatAttachmentChipsProps {
  attachedMaterials: AttachedMaterial[];
  quotes: TextQuote[];
  matchedSkills: Array<{ name: string; trigger: string }>;
  selectedSkills: SelectedSkill[];
  isFillLayout: boolean;
  isMobile: boolean;
  onRemoveMaterial: (id: string) => void;
  onRemoveQuote: (id: string) => void;
  onRemoveSkill: (id: string) => void;
}

export const ChatAttachmentChips: React.FC<ChatAttachmentChipsProps> = ({
  attachedMaterials,
  quotes,
  matchedSkills,
  selectedSkills,
  isFillLayout,
  isMobile,
  onRemoveMaterial,
  onRemoveQuote,
  onRemoveSkill,
}) => {
  const { t } = useTranslation(["chat"]);

  const compactAccessoryRowClass = isFillLayout
    ? "shrink-0 flex min-w-0 items-center gap-1.5 overflow-x-auto pb-0.5 scrollbar-none"
    : "shrink-0 flex flex-wrap items-center gap-1.5";
  const accessoryLabelClass = `text-xs text-[hsl(var(--text-secondary))] ${isFillLayout ? "shrink-0" : ""}`;
  const removeButtonClass = `shrink-0 opacity-70 hover:opacity-100 hover:text-[hsl(var(--error-text))] transition-opacity focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[hsl(var(--accent-primary))] rounded-full ${isMobile ? "p-1 -m-1" : ""}`;

  return (
    <>
      {/* Attached materials display */}
      {attachedMaterials.length > 0 && (
        <div className={compactAccessoryRowClass}>
          <span className={accessoryLabelClass}>{t("chat:input.attachedMaterials")}</span>
          {attachedMaterials.map((material) => (
            <span
              key={material.id}
              className={`inline-flex min-w-0 items-center gap-1.5 px-2.5 py-0.5 rounded-full border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))] text-xs font-medium ${isFillLayout ? "shrink-0" : ""}`}
            >
              <FileText size={12} className="shrink-0" />
              <span className="max-w-[110px] truncate">{material.title}</span>
              <button
                type="button"
                onClick={() => onRemoveMaterial(material.id)}
                className={removeButtonClass}
                title={t("chat:input.remove")}
                aria-label={t("chat:input.removeMaterial", { title: material.title })}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Quoted text display */}
      {quotes.length > 0 && (
        <div className={compactAccessoryRowClass}>
          <span className={accessoryLabelClass}>{t("chat:input.quotedText")}</span>
          {quotes.map((quote) => (
            <span
              key={quote.id}
              className={`inline-flex min-w-0 items-center gap-1.5 px-2.5 py-0.5 rounded-full border border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.1)] text-[hsl(var(--warning-text))] text-xs font-medium ${isFillLayout ? "shrink-0" : ""}`}
              title={quote.text}
            >
              <Quote size={12} className="shrink-0" />
              <span className="max-w-[150px] truncate">
                {quote.text.length > 50 ? `${quote.text.slice(0, 50)}…` : quote.text}
              </span>
              <button
                type="button"
                onClick={() => onRemoveQuote(quote.id)}
                className={removeButtonClass}
                title={t("chat:input.remove")}
                aria-label={t("chat:input.removeQuote")}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Matched skills indicator */}
      {matchedSkills.length > 0 && (
        <div className={compactAccessoryRowClass}>
          {matchedSkills.map((skill, index) => (
            <span
              key={index}
              className={`inline-flex min-w-0 items-center gap-1.5 px-2.5 py-0.5 rounded-full border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))] text-xs font-medium ${isFillLayout ? "shrink-0" : ""}`}
            >
              <Zap size={10} className="shrink-0" />
              <span className="max-w-[180px] truncate">{skill.name}</span>
            </span>
          ))}
        </div>
      )}

      {/* Explicitly selected skills (sent as selected_skill_ids) */}
      {selectedSkills.length > 0 && (
        <div className={compactAccessoryRowClass} data-testid="chat-selected-skill-row">
          <span className={accessoryLabelClass}>
            {t("chat:skill.selectedLabel", { count: selectedSkills.length, max: MAX_SELECTED_SKILLS })}
          </span>
          {selectedSkills.map((skill) => (
            <span
              key={skill.id}
              className={`inline-flex min-w-0 items-center gap-1.5 px-2.5 py-0.5 rounded-full border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] text-xs font-medium ${isFillLayout ? "shrink-0" : ""}`}
              data-testid="chat-selected-skill-chip"
            >
              <Zap size={11} className="shrink-0 text-[hsl(var(--accent-primary))]" />
              <span className="max-w-[200px] truncate">{skill.name}</span>
              <button
                type="button"
                onClick={() => onRemoveSkill(skill.id)}
                className={removeButtonClass}
                title={t("chat:input.remove")}
                aria-label={t("chat:skill.removeSelected", { name: skill.name })}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
    </>
  );
};
