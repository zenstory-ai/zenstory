import type { MaterialEnabledStages, MaterialNovel } from "../../lib/materialsApi";

export const ACTIVE_MATERIAL_STATUSES = new Set<MaterialNovel["status"]>(["pending", "processing"]);

export function isActiveMaterialStatus(status: MaterialNovel["status"]): boolean {
  return ACTIVE_MATERIAL_STATUSES.has(status);
}

export function formatMaterialResetDate(
  resetAt: string | null | undefined,
  language: string | undefined,
): string | null {
  if (!resetAt) return null;
  const parsed = new Date(resetAt);
  if (Number.isNaN(parsed.getTime())) return null;
  const locale = language?.startsWith("en") ? "en-US" : "zh-CN";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(parsed);
}

export function filterMaterialsByQuery(materials: MaterialNovel[], searchQuery: string): MaterialNovel[] {
  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  if (!normalizedSearch) return materials;
  return materials.filter(
    (material) =>
      material.title.toLocaleLowerCase().includes(normalizedSearch) ||
      (material.original_filename?.toLocaleLowerCase().includes(normalizedSearch) ?? false),
  );
}

export type MaterialTreeItemType =
  | "folder"
  | "chapter"
  | "character"
  | "story"
  | "worldview"
  | "plot"
  | "storyline"
  | "relationship"
  | "goldenfinger";

export interface MaterialTreeItem {
  id: string;
  type: MaterialTreeItemType;
  title: string;
  children?: MaterialTreeItem[];
  data?: unknown;
  /** Explicit, user-readable search corpus for this row. Never contains internal identifiers. */
  searchText?: string;
  /** Aggregate count from the material detail response; undefined means unknown. */
  count?: number;
}

export const MATERIAL_ITEM_FOLDERS: Partial<Record<MaterialTreeItemType, string>> = {
  chapter: "chapters",
  character: "characters",
  story: "stories",
  plot: "plots",
  storyline: "storylines",
  relationship: "relationships",
  goldenfinger: "goldenfingers",
  worldview: "worldview",
};

export function materialTreeItemKey(item: Pick<MaterialTreeItem, "type" | "id">): string {
  return `${item.type}:${item.id}`;
}

/**
 * Whether a decomposition stage ran for this material. Jobs created before the
 * enabled-stage snapshot existed report null, which keeps the legacy behavior
 * (treat every stage as enabled).
 */
export function isStageEnabled(
  enabledStages: MaterialEnabledStages | null | undefined,
  stage: keyof MaterialEnabledStages,
): boolean {
  return enabledStages?.[stage] !== false;
}

/** Keep historical/unknown folders visible; hide only confirmed-empty disabled stages. */
export function shouldShowFolder(stageEnabled: boolean, dataCount: number | boolean | undefined): boolean {
  return stageEnabled || dataCount === undefined || Number(dataCount) > 0;
}

export function readableText(values: Array<string | null | undefined | string[]>): string {
  return values.flatMap((value) => (Array.isArray(value) ? value : [value ?? ""])).join(" ");
}

export function parseThemes(themes: string | null | undefined): string[] {
  if (!themes) return [];
  try {
    const parsed: unknown = JSON.parse(themes);
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [themes];
  } catch {
    return themes.split(",").map((theme) => theme.trim()).filter(Boolean);
  }
}

export function formatFileSize(bytes: number, language: string): string {
  if (bytes < 1024) return `${bytes.toLocaleString(language)} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toLocaleString(language, { maximumFractionDigits: 1 })} ${unit}`;
}
