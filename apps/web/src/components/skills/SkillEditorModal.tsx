import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Check } from "../icons";
import { SKILL_FIELD_LIMITS } from "../../config/skills";
import type { Skill } from "../../types";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { SkillResourcesSection } from "./SkillResourcesSection";
import type { SkillFormData } from "./skillHelpers";
import { CharCount } from "./SkillSharedPrimitives";

export interface SkillEditorModalProps {
  open: boolean;
  editingSkill: Skill | null;
  formData: SkillFormData;
  onFormChange: (next: SkillFormData) => void;
  saving: boolean;
  onCancel: () => void;
  onSave: () => void;
  onResourcesChange: () => void;
  isMobile: boolean;
}

export function SkillEditorModal({
  open,
  editingSkill,
  formData,
  onFormChange,
  saving,
  onCancel,
  onSave,
  onResourcesChange,
  isMobile,
}: SkillEditorModalProps) {
  const { t } = useTranslation(["skills", "common"]);
  const skillNameInputId = useId();
  const skillDescriptionInputId = useId();
  const skillTriggersInputId = useId();
  const skillInstructionsInputId = useId();

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={editingSkill ? t("skills:editSkill") : t("skills:createSkill")}
      size="lg"
      showCloseButton={!saving}
      closeOnBackdropClick={!saving}
      closeOnEscape={!saving}
      footer={
        <>
          <Button
            type="button"
            variant="ghost"
            size="touch"
            onClick={onCancel}
            disabled={saving}
            className="flex-1"
          >
            {t("common:cancel")}
          </Button>
          <Button
            type="button"
            size="touch"
            onClick={onSave}
            disabled={saving || !formData.name.trim() || !formData.instructions.trim()}
            title={
              !saving && (!formData.name.trim() || !formData.instructions.trim())
                ? t("skills:form.requiredFieldsHint")
                : undefined
            }
            isLoading={saving}
            leftIcon={<Check className="w-4 h-4" />}
            className="flex-1"
          >
            {t("common:save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label htmlFor={skillNameInputId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {t("skills:form.name")} *
          </label>
          <input
            id={skillNameInputId}
            type="text"
            value={formData.name}
            onChange={(e) => onFormChange({ ...formData, name: e.target.value })}
            className="input"
            placeholder={t("skills:form.namePlaceholder")}
            maxLength={SKILL_FIELD_LIMITS.name}
          />
          <CharCount value={formData.name} max={SKILL_FIELD_LIMITS.name} />
        </div>

        <div>
          <label htmlFor={skillDescriptionInputId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {t("skills:form.description")}
          </label>
          <input
            id={skillDescriptionInputId}
            type="text"
            value={formData.description}
            onChange={(e) => onFormChange({ ...formData, description: e.target.value })}
            className="input"
            placeholder={t("skills:form.descriptionPlaceholder")}
            maxLength={SKILL_FIELD_LIMITS.description}
          />
          <CharCount value={formData.description} max={SKILL_FIELD_LIMITS.description} />
        </div>

        <div>
          <label htmlFor={skillTriggersInputId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {t("skills:form.triggers")}
          </label>
          <input
            id={skillTriggersInputId}
            type="text"
            value={formData.triggers}
            onChange={(e) => onFormChange({ ...formData, triggers: e.target.value })}
            className="input"
            placeholder={t("skills:form.triggersPlaceholder")}
          />
          <p className="text-xs text-[hsl(var(--text-tertiary))] mt-1">
            {t("skills:form.triggersHint")}
          </p>
        </div>

        <div>
          <label htmlFor={skillInstructionsInputId} className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
            {t("skills:form.instructions")} *
          </label>
          <textarea
            id={skillInstructionsInputId}
            value={formData.instructions}
            onChange={(e) => onFormChange({ ...formData, instructions: e.target.value })}
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
            onChange={onResourcesChange}
          />
        ) : (
          <p className="text-xs text-[hsl(var(--text-tertiary))]">
            {t("skills:resources.saveFirst")}
          </p>
        )}
      </div>
    </Modal>
  );
}
