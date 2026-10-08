import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { parseUTCDate } from '@/lib/dateUtils';
import { Users, Gift, Coins, Loader2, Award } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getLocaleCode } from '@/lib/i18n-helpers';
import { referralApi } from '@/lib/referralApi';
import type { UserReward } from '@/types/referral';
import { Badge } from '@/components/ui/Badge';

/**
 * Referral statistics and rewards display
 */
export const ReferralStats: React.FC = () => {
  const { t } = useTranslation('referral');
  // Fetch stats
  const { data: stats, isLoading: statsLoading, isFetching: statsFetching, error: statsError } = useQuery({
    queryKey: ['referralStats'],
    queryFn: referralApi.getStats,
  });
  const isStatsLoading = statsLoading || (statsFetching && !stats);

  // Fetch rewards
  const { data: rewards = [], isLoading: rewardsLoading, isFetching: rewardsFetching, error: rewardsError } = useQuery({
    queryKey: ['userRewards'],
    queryFn: referralApi.getRewards,
  });
  const isRewardsLoading = rewardsLoading || (rewardsFetching && rewards.length === 0);

  if (isStatsLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 size={24} className="animate-spin text-[hsl(var(--text-secondary))]" />
      </div>
    );
  }

  if (statsError || !stats) {
    return (
      <div className="text-center py-8 text-[hsl(var(--error))]">
        {t('stats.loadError')}
      </div>
    );
  }

  const getRewardTypeLabel = (type: UserReward['reward_type']) => {
    switch (type) {
      case 'points':
        return t('rewardTypes.points');
      case 'pro_trial':
        return t('rewardTypes.pro_trial');
      case 'credits':
        return t('rewardTypes.credits');
      default:
        return type;
    }
  };

  const formatRewardSource = (source: string) => {
    // 已知来源显示本地化说明（后端存的是 referral 这类机器值）；未知来源截断后原样显示
    if (source === 'referral') {
      return t('rewardSources.referral');
    }
    if (source.length > 30) {
      return source.substring(0, 30) + '...';
    }
    return source;
  };

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      <div className="grid grid-cols-2 gap-3">
        {/* Total invites */}
        <div className="bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl p-4 transition-all hover:shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <Users size={16} className="text-[hsl(var(--text-secondary))]" />
            <span className="text-xs text-[hsl(var(--text-secondary))] font-medium">{t('stats.totalInvites')}</span>
          </div>
          <div className="text-2xl font-bold text-[hsl(var(--text-primary))]">
            {stats.total_invites}
          </div>
        </div>

        {/* Successful invites */}
        <div className="bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl p-4 transition-all hover:shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <Award size={16} className="text-[hsl(var(--success))]" />
            <span className="text-xs text-[hsl(var(--text-secondary))] font-medium">{t('stats.successfulInvites')}</span>
          </div>
          <div className="text-2xl font-bold text-[hsl(var(--text-primary))]">
            {stats.successful_invites}
          </div>
        </div>

        {/* Total points */}
        <div className="bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl p-4 transition-all hover:shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <Gift size={16} className="text-[hsl(var(--text-secondary))]" />
            <span className="text-xs text-[hsl(var(--text-secondary))] font-medium">{t('stats.totalPoints')}</span>
          </div>
          <div className="text-2xl font-bold text-[hsl(var(--text-primary))]">
            {stats.total_points}
          </div>
        </div>

        {/* Available points */}
        <div className="bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl p-4 transition-all hover:shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <Coins size={16} className="text-[hsl(var(--warning))]" />
            <span className="text-xs text-[hsl(var(--text-secondary))] font-medium">{t('stats.availablePoints')}</span>
          </div>
          <div className="text-2xl font-bold text-[hsl(var(--accent-primary))]">
            {stats.available_points}
          </div>
        </div>
      </div>

      {/* Rewards history */}
      <div>
        <h3 className="text-base font-semibold text-[hsl(var(--text-primary))] mb-3">{t('stats.rewardHistory')}</h3>

        {isRewardsLoading ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 size={20} className="animate-spin text-[hsl(var(--text-secondary))]" />
          </div>
        ) : rewardsError && rewards.length === 0 ? (
          <div className="text-center py-8 text-[hsl(var(--error))]">
            {t('stats.loadError')}
          </div>
        ) : rewards.length > 0 ? (
          <div className="space-y-2">
            {rewards.map((reward) => (
              <div
                key={reward.id}
                className="flex items-center justify-between p-3 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl transition-all hover:shadow-md"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-[hsl(var(--success))]">
                      +{reward.amount} {getRewardTypeLabel(reward.reward_type)}
                    </span>
                    {reward.is_used && (
                      <Badge variant="neutral" size="sm">{t('used')}</Badge>
                    )}
                  </div>
                  <div className="text-xs text-[hsl(var(--text-secondary))] truncate mt-0.5">
                    {formatRewardSource(reward.source)}
                  </div>
                </div>
                <div className="text-xs text-[hsl(var(--text-secondary))] ml-3 shrink-0">
                  {parseUTCDate(reward.created_at).toLocaleDateString(getLocaleCode())}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl">
            <p className="text-sm text-[hsl(var(--text-secondary))]">{t('stats.noRewards')}</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ReferralStats;
