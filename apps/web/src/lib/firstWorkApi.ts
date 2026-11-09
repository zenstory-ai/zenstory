import { api } from './apiClient';
import type { ProjectType } from '../types';

export interface FirstWorkState {
  status: 'ineligible' | 'pending' | 'disabled' | 'waiting_verification' | 'waiting_onboarding' | 'skipped_existing' | 'created';
  project_id: string | null;
  /** The first work's type while it is available; absent from servers older than this field. */
  project_type?: ProjectType | null;
  file_id: string | null;
  available: boolean;
  static_guidance: boolean;
  opened_at: string | null;
  first_manual_write_at: string | null;
  first_ai_requested_at: string | null;
}

export const firstWorkQueryKey = (userId: string | null) => ['workspace-first-work', userId] as const;
export const firstWorkApi = {
  get: () => api.get<FirstWorkState>('/api/v1/workspace/first-work'),
  /** `null` lets the server choose from the onboarding persona. */
  initialize: (preferredType: ProjectType | null, language: string, signal?: AbortSignal) =>
    api.post<FirstWorkState>('/api/v1/workspace/first-work', { preferred_type: preferredType }, {
      headers: { 'Accept-Language': language },
      signal,
    }),
  open: () => api.post<FirstWorkState>('/api/v1/workspace/first-work/open'),
};
