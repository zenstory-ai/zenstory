/**
 * Daily Check-in Component
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { pointsApi } from '../../lib/pointsApi';
import { useTranslation } from 'react-i18next';
import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';

interface DailyCheckInProps {
  className?: string;
}

export function DailyCheckIn({ className = '' }: DailyCheckInProps) {
  const { t } = useTranslation(['points', 'common']);
  const queryClient = useQueryClient();

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['check-in-status'],
    queryFn: () => pointsApi.getCheckInStatus(),
  });

  const checkInMutation = useMutation({
    mutationFn: () => pointsApi.checkIn(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['check-in-status'] });
      queryClient.invalidateQueries({ queryKey: ['points-balance'] });
      queryClient.invalidateQueries({ queryKey: ['points-transactions'] });
    },
  });

  const isCheckedIn = status?.checked_in;
  const streakDays = status?.streak_days ?? 0;

  const handleCheckIn = () => {
    checkInMutation.mutate();
  };

  return (
    <Card className={className} isLoading={statusLoading}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <svg className="w-5 h-5 text-orange-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          <span className="text-sm font-medium text-[hsl(var(--text-secondary))]">
            {t('dailyCheckIn', '每日签到')}
          </span>
        </div>
        {streakDays > 0 && (
          <Badge variant="warning">
            {t('streakDays', '连续 {{days}} 天', { days: streakDays })}
          </Badge>
        )}
      </div>

      {isCheckedIn ? (
        <div className="flex items-center gap-2 text-[hsl(var(--success))]">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <span className="text-sm">
            {t('alreadyCheckedIn', '今日已签到')}
          </span>
          {status?.points_earned_today && status.points_earned_today > 0 && (
            <span className="text-xs text-[hsl(var(--text-secondary))]">
              (+{status.points_earned_today})
            </span>
          )}
        </div>
      ) : (
        <button
          onClick={handleCheckIn}
          disabled={checkInMutation.isPending}
          className="w-full py-2 px-4 bg-gradient-to-r from-[hsl(var(--accent-secondary-dark))] to-[hsl(var(--accent-secondary))] text-white font-medium rounded-lg hover:from-[hsl(var(--accent-secondary))] hover:to-[hsl(var(--accent-secondary-light))] disabled:opacity-50 disabled:cursor-not-allowed transition-all"
        >
          {checkInMutation.isPending
            ? t('common:loading', '处理中...')
            : t('checkIn', '签到领积分')}
        </button>
      )}

      {checkInMutation.isSuccess && checkInMutation.data && (
        <div className="mt-2 p-2 bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))] text-sm rounded">
          {t('checkInSuccess', '签到成功！+{{points}} 积分', { points: checkInMutation.data.points_earned })}
        </div>
      )}

      {checkInMutation.isError && (
        <div className="mt-2 p-2 bg-[hsl(var(--error)/0.15)] text-[hsl(var(--error))] text-sm rounded">
          {t('checkInFailed', '签到失败，请重试')}
        </div>
      )}
    </Card>
  );
}
