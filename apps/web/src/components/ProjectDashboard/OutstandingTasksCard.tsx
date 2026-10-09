import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  FileX,
  BookOpen,
  ArrowRight,
  CircleDashed,
} from 'lucide-react';
import type { ProjectDashboardStatsResponse, ChapterDetailItem } from '../../types/writingStats';
import { useIsMobile } from '../../hooks/useMediaQuery';
import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { IconWrapper } from '../ui/IconWrapper';
import { statsUnitKey } from './statsUnits';

interface OutstandingTasksCardProps {
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
  /** Project ID for navigation */
  projectId: string | undefined;
  /** Maximum number of tasks to show */
  maxVisibleTasks?: number;
}

/**
 * Task types for outstanding items
 */
type TaskType = 'outline_without_draft' | 'not_started';

/**
 * Outstanding task item
 */
interface OutstandingTask {
  id: string;
  type: TaskType;
  title: string;
  description: string;
  priority: 'high' | 'medium' | 'low';
  chapter?: ChapterDetailItem;
}

/**
 * Get task display configuration
 */
function getTaskDisplay(
  type: TaskType,
  t: (key: string) => string
): {
  icon: React.ElementType;
  colorClass: string;
  bgClass: string;
  label: string;
} {
  switch (type) {
    case 'outline_without_draft':
      return {
        icon: FileX,
        colorClass: 'text-blue-500',
        bgClass: 'bg-blue-500/10',
        label: t('statistics.outstandingTasks.types.outlineWithoutDraft'),
      };
    case 'not_started':
    default:
      return {
        icon: CircleDashed,
        colorClass: 'text-[hsl(var(--text-secondary))]',
        bgClass: 'bg-[hsl(var(--bg-tertiary))]',
        label: t('statistics.outstandingTasks.types.notStarted'),
      };
  }
}

/**
 * OutstandingTasksCard component showing items needing attention
 */
export function OutstandingTasksCard({
  stats,
  isLoading = false,
  projectId,
  maxVisibleTasks = 5,
}: OutstandingTasksCardProps) {
  const { t } = useTranslation(['dashboard']);
  const navigate = useNavigate();
  const isMobile = useIsMobile();

  // Calculate outstanding tasks from stats
  const outstandingTasks = useMemo((): OutstandingTask[] => {
    if (!stats?.chapter_completion?.chapter_details) return [];

    const tasks: OutstandingTask[] = [];
    const chapters = stats.chapter_completion.chapter_details;

    chapters.forEach((chapter) => {
      // Outline exists but no matching draft.
      if (!chapter.draft_id) {
        tasks.push({
          id: `outline-no-draft-${chapter.outline_id}`,
          type: 'outline_without_draft',
          title: chapter.title,
          description: t('statistics.outstandingTasks.outlineWithoutDraftDesc'),
          priority: 'high',
          chapter,
        });
        return;
      }

      // Draft exists but chapter is still not started.
      if (chapter.status === 'not_started') {
        tasks.push({
          id: `not-started-${chapter.outline_id}`,
          type: 'not_started',
          title: chapter.title,
          description: t(statsUnitKey(stats, 'notStartedDesc')),
          priority: 'low',
          chapter,
        });
      }
    });

    // Sort by priority (high > medium > low)
    const priorityOrder = { high: 0, medium: 1, low: 2 };
    tasks.sort((a, b) => priorityOrder[a.priority] - priorityOrder[b.priority]);

    return tasks;
  }, [stats, t]);

  // Calculate summary counts
  const taskSummary = useMemo(() => {
    const notStarted = outstandingTasks.filter((t) => t.type === 'not_started').length;
    const outlineWithoutDraft = outstandingTasks.filter(
      (t) => t.type === 'outline_without_draft'
    ).length;

    return {
      total: outstandingTasks.length,
      notStarted,
      outlineWithoutDraft,
    };
  }, [outstandingTasks]);

  // Visible tasks (limited for display)
  const visibleTasks = useMemo(() => {
    return outstandingTasks.slice(0, maxVisibleTasks);
  }, [outstandingTasks, maxVisibleTasks]);

  // Remaining tasks count
  const remainingCount = useMemo(() => {
    return Math.max(0, outstandingTasks.length - maxVisibleTasks);
  }, [outstandingTasks, maxVisibleTasks]);

  // Handle task click - navigate to the file
  const handleTaskClick = (task: OutstandingTask) => {
    if (!projectId || !task.chapter) return;

    // Navigate to the appropriate file
    const fileId = task.chapter.draft_id || task.chapter.outline_id;
    if (fileId) {
      navigate(`/project/${projectId}?file=${fileId}`);
    }
  };

  // Loading skeleton
  if (isLoading) {
    return (
      <Card padding={isMobile ? 'sm' : 'lg'} isLoading>
        <div className="flex items-center gap-2 mb-4">
          <div className="h-5 w-5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="h-5 w-28 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className={`flex ${isMobile ? 'flex-wrap gap-2' : 'gap-3'} mb-4`}>
          {[1, 2, 3].map((i) => (
            <div key={i} className={`rounded-full bg-[hsl(var(--bg-tertiary))] animate-pulse ${isMobile ? 'h-5 w-14' : 'h-6 w-16'}`} />
          ))}
        </div>
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className={`${isMobile ? 'h-14' : 'h-12'} rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse`} />
          ))}
        </div>
      </Card>
    );
  }

  // No outstanding tasks - all good!
  if (outstandingTasks.length === 0) {
    return (
      <Card padding={isMobile ? 'sm' : 'lg'}>
        <div className="flex items-center gap-2 mb-4">
          <IconWrapper size={isMobile ? 'lg' : 'xl'} variant="warning">
            <AlertTriangle className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'}`} />
          </IconWrapper>
          <h3 className={`${isMobile ? 'text-sm' : 'text-base'} font-semibold text-[hsl(var(--text-primary))]`}>
            {t('statistics.outstandingTasks.title')}
          </h3>
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
          <BookOpen className={`${isMobile ? 'w-8 h-8' : 'w-10 h-10'} mb-2 text-emerald-500 opacity-70`} />
          <p className={`${isMobile ? 'text-xs' : 'text-sm'} font-medium text-emerald-500`}>
            {t('statistics.outstandingTasks.allClear')}
          </p>
          <p className="text-xs mt-1 text-[hsl(var(--text-secondary)/0.7)]">
            {t('statistics.outstandingTasks.allClearHint')}
          </p>
        </div>
      </Card>
    );
  }

  return (
    <Card padding={isMobile ? 'sm' : 'lg'} hoverable>
      {/* Header */}
      <div className={`flex items-center ${isMobile ? 'gap-2' : 'justify-between'} mb-4`}>
        <div className="flex items-center gap-2">
          <IconWrapper size={isMobile ? 'lg' : 'xl'} variant="warning">
            <AlertTriangle className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'}`} />
          </IconWrapper>
          <h3 className={`${isMobile ? 'text-sm' : 'text-base'} font-semibold text-[hsl(var(--text-primary))]`}>
            {t('statistics.outstandingTasks.title')}
          </h3>
        </div>
        {!isMobile && (
          <div className="text-sm text-[hsl(var(--text-secondary))]">
            {t('statistics.outstandingTasks.total', { count: taskSummary.total })}
          </div>
        )}
      </div>

      {/* Summary Tags */}
      <div className={`flex flex-wrap ${isMobile ? 'gap-1.5' : 'gap-2'} mb-4`}>
        {taskSummary.notStarted > 0 && (
          <Badge variant="neutral" size="sm">
            {taskSummary.notStarted} {t(statsUnitKey(stats, 'notStartedCount'))}
          </Badge>
        )}
        {taskSummary.outlineWithoutDraft > 0 && (
          <Badge variant="info" size="sm">
            {taskSummary.outlineWithoutDraft} {t(statsUnitKey(stats, 'outlineWithoutDraftCount'))}
          </Badge>
        )}
      </div>

      {/* Task List */}
      <div className="space-y-2">
        {visibleTasks.map((task) => {
          const display = getTaskDisplay(task.type, t);
          const TaskIcon = display.icon;

          return (
            <button
              key={task.id}
              onClick={() => handleTaskClick(task)}
              className={`w-full flex items-center ${isMobile ? 'gap-2 p-2' : 'gap-3 p-2.5'} rounded-lg bg-[hsl(var(--bg-tertiary)/0.5)] hover:bg-[hsl(var(--bg-tertiary))] transition-colors group text-left`}
            >
              {/* Task Icon */}
              <div className={`flex ${isMobile ? 'h-7 w-7' : 'h-8 w-8'} items-center justify-center rounded-lg ${display.bgClass}`}>
                <TaskIcon className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'} ${display.colorClass}`} />
              </div>

              {/* Task Info */}
              <div className="flex-1 min-w-0">
                <div className={`flex items-center ${isMobile ? 'flex-wrap gap-1' : 'gap-2'}`}>
                  <span className={`${isMobile ? 'text-xs' : 'text-sm'} font-medium text-[hsl(var(--text-primary))] truncate`}>
                    {task.title}
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-xs ${display.bgClass} ${display.colorClass}`}>
                    {display.label}
                  </span>
                </div>
                {!isMobile && (
                  <p className="text-xs text-[hsl(var(--text-secondary))] truncate mt-0.5">
                    {task.description}
                  </p>
                )}
              </div>

              {/* Arrow */}
              <ArrowRight className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'} text-[hsl(var(--text-secondary))] opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0`} />
            </button>
          );
        })}
      </div>

      {/* Show More */}
      {remainingCount > 0 && (
        <div className="mt-3 text-center">
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t('statistics.outstandingTasks.moreTasks', { count: remainingCount })}
          </span>
        </div>
      )}
    </Card>
  );
}

export default OutstandingTasksCard;
