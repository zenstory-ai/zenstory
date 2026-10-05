/**
 * Skill form limits. Keep in sync with apps/server/agent/skills/package.py
 * (MAX_NAME_CHARS / MAX_DESCRIPTION_CHARS / MAX_INSTRUCTIONS_CHARS /
 * MAX_TRIGGERS / MAX_TRIGGER_CHARS); the API rejects longer values with 422.
 */
export const SKILL_FIELD_LIMITS = {
  name: 100,
  description: 1024,
  instructions: 50_000,
  triggers: 50,
  trigger: 100,
} as const;

/** Public skills fetched per "load more" page on the discover tab. */
export const PUBLIC_SKILLS_PAGE_SIZE = 20;

/** Debounce for the discover search box (matches the my-skills search). */
export const PUBLIC_SKILLS_SEARCH_DEBOUNCE_MS = 300;
