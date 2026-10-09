import { api } from './apiClient';

export type PersonaExperienceLevel = 'beginner' | 'intermediate' | 'advanced';

export interface PersonaOnboardingProfile {
  version: number;
  completed_at: string;
  selected_personas: string[];
  selected_goals: string[];
  experience_level: PersonaExperienceLevel;
  skipped: boolean;
}

export interface PersonaRecommendation {
  id: string;
  title: string;
  description: string;
  action: string;
}

export interface PersonaOnboardingState {
  required: boolean;
  rollout_at: string;
  new_user_window_days: number;
  profile: PersonaOnboardingProfile | null;
  recommendations: PersonaRecommendation[];
}

export interface PersonaOnboardingUpsertRequest {
  selected_personas: string[];
  selected_goals: string[];
  /** Omitted when the author did not answer; the server keeps its default. */
  experience_level?: PersonaExperienceLevel;
  skipped: boolean;
}

export const personaOnboardingQueryKey = (userId: string) =>
  ['persona-onboarding', userId] as const;

export const onboardingPersonaApi = {
  getState: () => api.get<PersonaOnboardingState>('/api/v1/persona/onboarding'),

  save: (payload: PersonaOnboardingUpsertRequest) =>
    api.put<PersonaOnboardingState>('/api/v1/persona/onboarding', payload),

  getRecommendations: async () => {
    const response = await api.get<{ recommendations: PersonaRecommendation[] }>(
      '/api/v1/persona/recommendations'
    );
    return response.recommendations ?? [];
  },
};

export default onboardingPersonaApi;
