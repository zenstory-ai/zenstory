import type { ProjectDashboardStatsResponse } from '../../types/writingStats';

/**
 * Project statistics name a writing unit after the project type: 章 for
 * novels, 篇 for short stories, 集 for screenplays. Copy that mentions the
 * unit lives under `statistics.byType.<type>` in the dashboard namespace.
 */
export type StatsUnitType = 'novel' | 'short' | 'screenplay';

export function statsUnitType(projectType: string | null | undefined): StatsUnitType {
  return projectType === 'short' || projectType === 'screenplay' ? projectType : 'novel';
}

export function statsUnitKey(
  stats: Pick<ProjectDashboardStatsResponse, 'project_type'> | null | undefined,
  key: string,
): string {
  return `statistics.byType.${statsUnitType(stats?.project_type)}.${key}`;
}
