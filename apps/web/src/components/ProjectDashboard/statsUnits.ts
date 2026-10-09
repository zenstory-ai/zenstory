import type { ChapterCompletionResponse, ProjectDashboardStatsResponse } from '../../types/writingStats';

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

/**
 * Planned chapter count from the outline, or null when unknown (no plan in
 * the outline, or an older server). Without it there is no completion
 * percentage and no "all done": written chapters alone say nothing about how
 * many are left.
 */
export function plannedTotal(
  completion: Pick<ChapterCompletionResponse, 'planned_total'> | null | undefined,
): number | null {
  const planned = completion?.planned_total;
  return typeof planned === 'number' && planned > 0 ? planned : null;
}

/** Chapters with prose written so far (finished or in progress). */
export function writtenChapters(
  completion: Pick<ChapterCompletionResponse, 'completed_chapters' | 'in_progress_chapters'>,
): number {
  return completion.completed_chapters + completion.in_progress_chapters;
}
