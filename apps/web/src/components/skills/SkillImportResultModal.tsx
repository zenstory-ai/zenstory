import { useTranslation } from "react-i18next";
import { AlertTriangle } from "../icons";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";

export interface SkillImportResult {
  skillName: string;
  warnings: string[];
}

export interface SkillImportResultModalProps {
  result: SkillImportResult | null;
  onClose: () => void;
}

export function SkillImportResultModal({ result, onClose }: SkillImportResultModalProps) {
  const { t } = useTranslation(["skills"]);

  return (
    <Modal
      open={!!result}
      onClose={onClose}
      title={t("skills:import.successTitle")}
      size="md"
      footer={
        <Button type="button" size="touch" onClick={onClose} className="flex-1">
          {t("skills:import.done")}
        </Button>
      }
    >
      {result && (
        <div className="space-y-3" data-testid="skill-import-result">
          <p className="text-sm text-[hsl(var(--text-primary))]">
            {t("skills:import.success", { name: result.skillName })}
          </p>
          {result.warnings.length > 0 && (
            <div className="rounded-lg border border-[hsl(var(--warning)/0.4)] bg-[hsl(var(--warning)/0.08)] p-3">
              <div className="flex items-center gap-2 mb-2 text-sm font-medium text-[hsl(var(--warning-text))]">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                {t("skills:import.warningsTitle", { count: result.warnings.length })}
              </div>
              <ul className="list-disc pl-5 space-y-1 text-xs text-[hsl(var(--text-secondary))] break-all">
                {result.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
