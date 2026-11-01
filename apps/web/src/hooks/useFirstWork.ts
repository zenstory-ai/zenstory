import { useQuery } from '@tanstack/react-query';
import { firstWorkApi, firstWorkQueryKey, type FirstWorkState } from '../lib/firstWorkApi';
import type { ProjectType } from '../types';

export function useFirstWork(userId: string | null) {
  return useQuery({
    queryKey: firstWorkQueryKey(userId),
    queryFn: firstWorkApi.get,
    enabled: Boolean(userId),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  });
}

export function needsStaticFirstWorkGuidance(state: FirstWorkState | undefined, projectId: string | null) {
  // The original manuscript may have been deleted while the project still needs static guidance.
  return Boolean(projectId && state?.project_id === projectId && state.static_guidance);
}

/**
 * The first work while it is still untouched: its manuscript is there and the author has
 * neither written in it nor asked the AI. Until then the dashboard's 「开始创作」 opens it
 * instead of creating a second empty work.
 */
export function untouchedFirstWork(state: FirstWorkState | undefined): { projectId: string; fileId: string; projectType: ProjectType | null } | null {
  if (!state?.available || !state.static_guidance || state.first_manual_write_at || state.first_ai_requested_at) return null;
  if (!state.project_id || !state.file_id) return null;
  return { projectId: state.project_id, fileId: state.file_id, projectType: state.project_type ?? null };
}
