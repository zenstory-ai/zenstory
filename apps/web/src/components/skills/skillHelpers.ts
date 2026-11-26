export interface SkillFormData {
  name: string;
  description: string;
  triggers: string;
  instructions: string;
}

export const emptySkillForm: SkillFormData = {
  name: "",
  description: "",
  triggers: "",
  instructions: "",
};

export type SkillsTabType = "my-skills" | "discover";

/** Server rejects skill packages above 1 MiB; fail fast before uploading. */
export const MAX_IMPORT_BYTES = 1024 * 1024;
export const IMPORT_EXTENSIONS = [".zip", ".md"];

const SKILL_CATEGORY_LABEL_KEYS: Record<string, string> = {
  writing: "categories.writing",
  plot: "categories.plot",
  style: "categories.style",
  character: "categories.character",
  worldbuilding: "categories.worldbuilding",
};

export function getLocalizedSkillCategory(
  category: string,
  t: (key: string, options?: { defaultValue?: string }) => string,
): string {
  const normalized = category.trim().toLowerCase();
  const translationKey = SKILL_CATEGORY_LABEL_KEYS[normalized];

  if (!translationKey) {
    return category;
  }

  const translated = t(translationKey, { defaultValue: category });
  return translated === translationKey ? category : translated;
}
