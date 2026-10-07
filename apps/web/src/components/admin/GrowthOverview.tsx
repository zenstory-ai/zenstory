import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Users, Sparkles, CreditCard, Coins, Gift, UserCheck } from 'lucide-react';
import { adminApi } from '@/lib/adminApi';
import { AdminPageState } from './AdminPageState';
import { StatsCard } from './StatsCard';
import type { GrowthMetrics } from '@/types/admin';

export function GrowthOverview({ days }: { days: 7 | 14 | 30 }) {
  const { t, i18n } = useTranslation(['admin', 'common']);
  const query = useQuery({
    queryKey: ['admin', 'dashboard', 'growth', days],
    queryFn: () => adminApi.getGrowthDashboard(days),
  });
  const percent = (value: number | null) => value === null ? '—' : `${(value * 100).toFixed(1)}%`;
  const money = (cents: number) => `¥${(cents / 100).toFixed(2)}`;
  const data = query.data;
  const metrics = data?.current.metrics;
  const prior = data?.previous.metrics;
  const end = data ? new Intl.DateTimeFormat(i18n?.language ?? 'zh', {
    timeZone: data.timezone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(data.current.period_end)) : '—';
  const cards = [
    { key: 'new_users', title: 'newUsers', icon: Users },
    { key: 'ai_active_users', title: 'aiActive', icon: Sparkles },
    { key: 'cohort_activation_rate', title: 'activation', icon: UserCheck, format: percent },
    { key: 'signup_to_paid_rate', title: 'paidRate', icon: CreditCard, format: percent },
    { key: 'paid_users', title: 'paidUsers', icon: Users },
    { key: 'paid_orders', title: 'orders', icon: CreditCard },
    { key: 'revenue_cents', title: 'revenue', icon: Coins, format: (value: number | null) => money(value ?? 0) },
    { key: 'grant_upgrade_users', title: 'grants', icon: Gift },
  ] as const;
  const value = (row: GrowthMetrics, card: typeof cards[number]) => {
    const number = row[card.key];
    return 'format' in card ? card.format(number) : number ?? '—';
  };
  return (
    <section className="space-y-4" aria-labelledby="growth-title">
      <div>
        <h2 id="growth-title" className="text-lg font-semibold text-[hsl(var(--text-primary))]">{t('admin:growth.title')}</h2>
        <p className="text-sm text-[hsl(var(--text-secondary))]">{t('admin:growth.period', { end })}</p>
      </div>
      <AdminPageState isLoading={query.isLoading} isFetching={query.isFetching} isError={query.isError}
        isEmpty={!data} onRetry={() => { void query.refetch(); }}>
        {metrics && prior && <>
          <p className="text-xs text-[hsl(var(--text-secondary))]">{t('admin:growth.comparison')}</p>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {cards.map(card => <StatsCard key={card.key} icon={<card.icon className="h-5 w-5" />}
              title={t(`admin:growth.${card.title}`)} value={value(metrics, card)}
              trend="neutral" trendValue={t('admin:growth.previous', { value: value(prior, card) })} />)}
          </div>
          <p className="text-sm text-[hsl(var(--text-secondary))]">{t('admin:growth.definition')}</p>
          <div className="admin-surface p-4">
            <h3 className="font-semibold mb-2">{t('admin:growth.daily')}</h3>
            <p className="text-xs text-[hsl(var(--text-secondary))] mb-3">{t('admin:growth.dailyDefinition')}</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[600px] text-sm">
                <thead><tr className="text-[hsl(var(--text-secondary))]">
                  <th scope="col" className="text-left py-2">{t('admin:growth.date')}</th>
                  {['newUsers', 'aiActive', 'orders', 'revenue'].map(key =>
                    <th scope="col" className="text-right px-2 py-2 font-medium" key={key}>{t(`admin:growth.${key}`)}</th>)}
                </tr></thead>
                <tbody>{data?.daily.map(row => <tr key={row.date} className="border-t border-[hsl(var(--separator-color))]">
                  <th scope="row" className="text-left py-2 font-normal">{row.date}</th>
                  {[row.new_users, row.ai_active_users, row.paid_orders, money(row.revenue_cents)].map((cell, index) =>
                    <td className="text-right px-2 py-2 tabular-nums" key={index}>{cell}</td>)}
                </tr>)}</tbody>
              </table>
            </div>
          </div>
        </>}
      </AdminPageState>
    </section>
  );
}
