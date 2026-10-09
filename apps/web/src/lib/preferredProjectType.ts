import type { ProjectType } from "../types";

/**
 * The project type a visitor picked before they had an account (landing page
 * type card) or declared during onboarding (short-story / screenwriter
 * persona). The dashboard opens on this tab so the first project matches it.
 *
 * Stored device-wide rather than per user: the landing page has no user yet.
 * Storage failures (private mode, quota, blocked site data) only lose the
 * preference; the dashboard then falls back to "novel".
 */
export const PREFERRED_PROJECT_TYPE_STORAGE_KEY = "zenstory_preferred_project_type";
export const PREFERRED_PROJECT_TYPE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const PREFERABLE_PROJECT_TYPES: readonly ProjectType[] = ["novel", "short", "screenplay"];

interface StoredPreferredProjectType {
  type: ProjectType;
  ts: number;
}

export function isPreferableProjectType(value: unknown): value is ProjectType {
  return typeof value === "string" && PREFERABLE_PROJECT_TYPES.includes(value as ProjectType);
}

export function setPreferredProjectType(type: ProjectType): void {
  if (!isPreferableProjectType(type)) return;
  try {
    const record: StoredPreferredProjectType = { type, ts: Date.now() };
    localStorage.setItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Preference is a convenience; losing it is harmless.
  }
}

export function getPreferredProjectType(now: number = Date.now()): ProjectType | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as Partial<StoredPreferredProjectType> | null;
    if (
      !parsed ||
      !isPreferableProjectType(parsed.type) ||
      typeof parsed.ts !== "number" ||
      !Number.isFinite(parsed.ts) ||
      now - parsed.ts > PREFERRED_PROJECT_TYPE_TTL_MS ||
      parsed.ts - now > PREFERRED_PROJECT_TYPE_TTL_MS
    ) {
      clearPreferredProjectType();
      return null;
    }
    return parsed.type;
  } catch {
    clearPreferredProjectType();
    return null;
  }
}

export function clearPreferredProjectType(): void {
  try {
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
}
