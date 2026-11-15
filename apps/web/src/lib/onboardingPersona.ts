export const PERSONA_ONBOARDING_STORAGE_KEY_PREFIX = 'zenstory_onboarding_persona_v1';

export type PersonaExperienceLevel = 'beginner' | 'intermediate' | 'advanced';

export interface PersonaOnboardingData {
  version: 1;
  completed_at: string;
  selected_personas: string[];
  selected_goals: string[];
  experience_level: PersonaExperienceLevel;
  skipped: boolean;
}

const buildStorageKey = (userId: string): string =>
  `${PERSONA_ONBOARDING_STORAGE_KEY_PREFIX}:${userId}`;

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');

const isExperienceLevel = (value: unknown): value is PersonaExperienceLevel =>
  value === 'beginner' || value === 'intermediate' || value === 'advanced';

export const getPersonaOnboardingData = (userId: string): PersonaOnboardingData | null => {
  if (!userId) return null;

  try {
    const raw = localStorage.getItem(buildStorageKey(userId));
    if (!raw) return null;

    const parsed = JSON.parse(raw) as Partial<PersonaOnboardingData>;

    if (
      parsed.version !== 1 ||
      typeof parsed.completed_at !== 'string' ||
      !isStringArray(parsed.selected_personas) ||
      !isStringArray(parsed.selected_goals) ||
      !isExperienceLevel(parsed.experience_level) ||
      typeof parsed.skipped !== 'boolean'
    ) {
      return null;
    }

    return parsed as PersonaOnboardingData;
  } catch {
    return null;
  }
};

export const savePersonaOnboardingData = (
  userId: string,
  data: Omit<PersonaOnboardingData, 'version' | 'completed_at'>
): PersonaOnboardingData | null => {
  if (!userId) return null;

  const payload: PersonaOnboardingData = {
    version: 1,
    completed_at: new Date().toISOString(),
    selected_personas: data.selected_personas,
    selected_goals: data.selected_goals,
    experience_level: data.experience_level,
    skipped: data.skipped,
  };

  try {
    localStorage.setItem(buildStorageKey(userId), JSON.stringify(payload));
    return payload;
  } catch {
    return null;
  }
};
