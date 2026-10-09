import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { PenLine, FileText, ArrowRight, Clock } from 'lucide-react';
import type { ChapterDetailItem, ProjectDashboardStatsResponse } from '../../types/writingStats';
import { useIsMobile } from '../../hooks/useMediaQuery';
import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { IconWrapper } from '../ui/IconWrapper';
import { statsUnitKey } from './statsUnits';

interface ContinueWritingCardProps {
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
  /** Project ID for navigation */
  projectId: string | undefined;
}

function parseChineseNumber(value: string): number | null {
  const token = value.trim();
  if (!token) return null;

  const map: Record<string, number> = {
    零: 0,
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
    十: 10,
    百: 100,
    千: 1000,
  };

  let result = 0;
  let temp = 0;
  for (const char of token) {
    const num = map[char];
    if (num === undefined) continue;
    if (num === 10 || num === 100 || num === 1000) {
      if (temp === 0) temp = 1;
      result += temp * num;
      temp = 0;
    } else {
      temp = num;
    }
  }
  result += temp;

  return result > 0 ? result : null;
}

function extractChapterSequence(title: string): number | null {
  const text = title.trim();
  if (!text) return null;

  const chapterMatch = text.match(/第\s*([零一二三四五六七八九十百千\d]+)\s*[章节回集话幕场卷]/i);
  if (chapterMatch?.[1]) {
    const token = chapterMatch[1];
    const parsed = /^\d+$/.test(token)
      ? Number.parseInt(token, 10)
      : parseChineseNumber(token);
    return parsed != null && Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  const enMatch = text.match(/\bchapter\s+(\d+)\b/i);
  if (enMatch?.[1]) {
    const parsed = Number.parseInt(enMatch[1], 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  return null;
}

/**
 * Get file type display info for navigation
 */
function getFileTypeInfo(
  fileType: string
): { icon: React.ElementType; colorClass: string; bgClass: string } {
  switch (fileType) {
    case 'draft':
      return {
        icon: FileText,
        colorClass: 'text-blue-500',
        bgClass: 'bg-blue-500/10',
      };
    case 'outline':
      return {
        icon: FileText,
        colorClass: 'text-purple-500',
        bgClass: 'bg-purple-500/10',
      };
    default:
      return {
        icon: FileText,
        colorClass: 'text-[hsl(var(--text-secondary))]',
        bgClass: 'bg-[hsl(var(--bg-tertiary))]',
      };
  }
}

/**
 * ContinueWritingCard component showing last edited file and quick action
 */
export function ContinueWritingCard({
  stats,
  isLoading = false,
  projectId,
}: ContinueWritingCardProps) {
  const { t } = useTranslation(['dashboard']);
  const navigate = useNavigate();
  const isMobile = useIsMobile();

  // Get the recommended file to continue writing
  const recommendedFile = useMemo(() => {
    if (!stats?.chapter_completion?.chapter_details) return null;

    const chapters = stats.chapter_completion.chapter_details;
    const pickLatestByStatus = (status: string): ChapterDetailItem | null => {
      let bestChapter: ChapterDetailItem | null = null;
      let bestSequence = Number.NEGATIVE_INFINITY;
      let bestIndex = -1;

      chapters.forEach((chapter, index) => {
        if (chapter.status !== status) return;
        const sequence = extractChapterSequence(chapter.title) ?? Number.NEGATIVE_INFINITY;
        if (sequence > bestSequence || (sequence === bestSequence && index > bestIndex)) {
          bestChapter = chapter;
          bestSequence = sequence;
          bestIndex = index;
        }
      });

      return bestChapter;
    };

    // Priority 1: Find latest in-progress chapter
    const inProgress = pickLatestByStatus('in_progress');
    if (inProgress) {
      return {
        ...inProgress,
        fileType: 'draft',
        reason: t('statistics.continueWriting.inProgress', '写作中'),
      };
    }

    // Priority 2: Find latest not-started chapter with outline
    const notStarted = pickLatestByStatus('not_started');
    if (notStarted) {
      return {
        ...notStarted,
        fileType: 'outline',
        reason: t('statistics.continueWriting.notStarted', '待开始'),
      };
    }

    // Priority 3: Latest completed (for review)
    const completed = pickLatestByStatus('complete');
    if (completed) {
      return {
        ...completed,
        fileType: 'draft',
        reason: t('statistics.continueWriting.completed', '已完成'),
      };
    }

    return null;
  }, [stats, t]);

  // Handle continue writing action
  const handleContinue = () => {
    if (!projectId || !recommendedFile) return;

    // Navigate to the file in the editor
    const fileId = recommendedFile.draft_id || recommendedFile.outline_id;
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
          <div className="h-5 w-32 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className={`flex items-center ${isMobile ? 'gap-3' : 'gap-4'} mb-4`}>
          <div className={`${isMobile ? 'h-10 w-10' : 'h-14 w-14'} rounded-xl bg-[hsl(var(--bg-tertiary))] animate-pulse`} />
          <div className="flex-1 space-y-2">
            <div className="h-5 w-3/4 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
            <div className="h-4 w-1/2 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          </div>
        </div>
        <div className="h-10 w-full rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse" />
      </Card>
    );
  }

  // No data state
  if (!recommendedFile) {
    return (
      <Card padding={isMobile ? 'sm' : 'lg'}>
        <div className="flex items-center gap-2 mb-4">
          <PenLine className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
          <h3 className={`${isMobile ? 'text-sm' : 'text-base'} font-semibold text-[hsl(var(--text-primary))]`}>
            {t('statistics.continueWriting.title')}
          </h3>
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
          <PenLine className="w-10 h-10 mb-2 opacity-50" />
          <p className="text-sm">{t('statistics.continueWriting.noFiles')}</p>
          <p className="text-xs mt-1 text-[hsl(var(--text-secondary)/0.7)]">
            {t(statsUnitKey(stats, 'createFirst'))}
          </p>
        </div>
      </Card>
    );
  }

  const typeInfo = getFileTypeInfo(recommendedFile.fileType);
  const TypeIcon = typeInfo.icon;

  return (
    <Card padding={isMobile ? 'sm' : 'lg'} hoverable>
      {/* Header */}
      <div className={`flex items-center ${isMobile ? 'flex-wrap gap-2' : 'justify-between'} mb-4`}>
        <div className="flex items-center gap-2">
          <IconWrapper size={isMobile ? 'lg' : 'xl'} variant="primary">
            <PenLine className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'}`} />
          </IconWrapper>
          <h3 className={`${isMobile ? 'text-sm' : 'text-base'} font-semibold text-[hsl(var(--text-primary))]`}>
            {t('statistics.continueWriting.title')}
          </h3>
        </div>
        {/* Status badge */}
        <Badge
          variant={recommendedFile.fileType === 'draft' ? 'info' : 'purple'}
          size="sm"
        >
          {recommendedFile.reason}
        </Badge>
      </div>

      {/* File Info */}
      <div className={`flex items-center ${isMobile ? 'gap-3' : 'gap-4'} mb-4 p-3 rounded-lg bg-[hsl(var(--bg-tertiary)/0.5)]`}>
        {/* File Icon */}
        <div className={`flex ${isMobile ? 'h-10 w-10' : 'h-12 w-12'} items-center justify-center rounded-xl ${typeInfo.bgClass}`}>
          <TypeIcon className={`${isMobile ? 'w-5 h-5' : 'w-6 h-6'} ${typeInfo.colorClass}`} />
        </div>

        {/* File Details */}
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
            {recommendedFile.title}
          </h4>
          <div className={`flex items-center ${isMobile ? 'gap-2' : 'gap-3'} mt-1`}>
            {/* Word count */}
            {recommendedFile.word_count > 0 && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                {recommendedFile.word_count.toLocaleString()} {t('statistics.wordCount.words', { count: recommendedFile.word_count }).replace(/[\d,]+\s*/, '')}
              </span>
            )}
            {/* Progress if target set */}
            {recommendedFile.target_word_count && recommendedFile.target_word_count > 0 && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                {Math.round(recommendedFile.completion_percentage)}%
              </span>
            )}
          </div>
          {/* Progress bar if target set */}
          {recommendedFile.target_word_count && recommendedFile.target_word_count > 0 && (
            <div className="mt-2 h-1.5 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  recommendedFile.completion_percentage >= 100
                    ? 'bg-emerald-500'
                    : recommendedFile.completion_percentage >= 50
                      ? 'bg-amber-500'
                      : 'bg-[hsl(var(--accent-primary))]'
                }`}
                style={{ width: `${Math.min(recommendedFile.completion_percentage, 100)}%` }}
              />
            </div>
          )}
        </div>
      </div>

      {/* Quick Action Button */}
      <button
        onClick={handleContinue}
        className={`w-full flex items-center justify-center gap-2 ${isMobile ? 'px-3 py-2' : 'px-4 py-2.5'} rounded-lg bg-[hsl(var(--accent-primary))] text-white font-medium text-sm hover:opacity-90 transition-opacity`}
      >
        <PenLine className="w-4 h-4" />
        <span>{t('statistics.continueWriting.action')}</span>
        <ArrowRight className="w-4 h-4" />
      </button>

      {/* Hint */}
      <p className={`${isMobile ? 'mt-2' : 'mt-3'} text-xs text-[hsl(var(--text-secondary)/0.7)] flex items-center gap-1.5`}>
        <Clock className="w-3 h-3" />
        {recommendedFile.status === 'in_progress'
          ? t('statistics.continueWriting.hintInProgress')
          : recommendedFile.status === 'not_started'
            ? t(statsUnitKey(stats, 'hintNotStarted'))
            : t(statsUnitKey(stats, 'hintCompleted'))}
      </p>
    </Card>
  );
}

export default ContinueWritingCard;
