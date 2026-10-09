/**
 * Points Earn Opportunities Component
 */
import { useQuery } from '@tanstack/react-query';
import { pointsApi } from '../../lib/pointsApi';
import { useTranslation } from 'react-i18next';
import { Card } from '../ui/Card';
import { inspirationsConfig } from '../../config/inspirations';

interface EarnOpportunitiesProps {
  className?: string;
}

const OPPORTUNITY_ICONS: Record<string, React.ReactNode> = {
  check_in: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
  ),
  check_in_streak: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z" />
    </svg>
  ),
  referral: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
    </svg>
  ),
  skill_contribution: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
    </svg>
  ),
  inspiration_contribution: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
    </svg>
  ),
  profile_complete: (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
    </svg>
  ),
};

export function EarnOpportunities({ className = '' }: EarnOpportunitiesProps) {
  const { t } = useTranslation('points');
  const { data: opportunities, isLoading, isFetching } = useQuery({
    queryKey: ['earn-opportunities'],
    queryFn: () => pointsApi.getEarnOpportunities(),
  });
  const opportunityItems = (opportunities ?? []).filter(
    (opportunity) => inspirationsConfig.enabled || opportunity.type !== 'inspiration_contribution',
  );
  const isOpportunitiesLoading = isLoading || (isFetching && opportunityItems.length === 0);

  if (!isOpportunitiesLoading && opportunityItems.length === 0) return null;

  return (
    <Card className={className} isLoading={isOpportunitiesLoading}>
      <h3 className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-3">
        {t('earnMore', '获取更多积分')}
      </h3>

      <div className="space-y-2">
        {opportunityItems.map((opportunity) => (
          <div
            key={opportunity.type}
            className={`flex items-center justify-between p-2 rounded-lg border ${
              opportunity.is_completed
                ? 'bg-[hsl(var(--bg-tertiary))] border-[hsl(var(--border-color))]'
                : opportunity.is_available
                ? 'bg-[hsl(var(--accent-primary)/0.12)] border-[hsl(var(--accent-primary)/0.25)]'
                : 'bg-[hsl(var(--bg-tertiary))] border-[hsl(var(--border-color))] opacity-60'
            }`}
          >
            <div className="flex items-center gap-3">
              <div className={`${
                opportunity.is_completed
                  ? 'text-[hsl(var(--text-secondary))]'
                  : opportunity.is_available
                  ? 'text-[hsl(var(--accent-primary))]'
                  : 'text-[hsl(var(--text-secondary))]'
              }`}>
                {OPPORTUNITY_ICONS[opportunity.type] || (
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                )}
              </div>
              <div>
                <p className={`text-sm ${
                  opportunity.is_completed
                    ? 'text-[hsl(var(--text-secondary))] line-through'
                    : 'text-[hsl(var(--text-primary))]'
                }`}>
                  {t(`opportunityDescriptions.${opportunity.type}`, opportunity.description)}
                </p>
                {opportunity.is_completed && (
                  <p className="text-xs text-[hsl(var(--success))]">
                    {t('completed', '已完成')}
                  </p>
                )}
              </div>
            </div>
            <span className={`text-sm font-medium ${
              opportunity.is_completed
                ? 'text-[hsl(var(--text-secondary))]'
                : 'text-[hsl(var(--accent-secondary))]'
            }`}>
              +{opportunity.points}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
