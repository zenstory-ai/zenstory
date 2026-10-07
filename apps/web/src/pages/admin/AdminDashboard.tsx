import { useMemo, useState } from 'react';
import { useQuery, useQueryClient, useIsFetching } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Users, FolderOpen, Lightbulb, CreditCard, RefreshCw, Coins, CalendarCheck, Gift, UserPlus } from 'lucide-react';
import { adminApi } from '@/lib/adminApi';
import { AdminPageState } from '@/components/admin';
import { StatsCard } from '@/components/admin/StatsCard';
import { GrowthOverview } from '@/components/admin/GrowthOverview';
import { RecentActivityList } from '@/components/admin/RecentActivityList';
import { inspirationsConfig } from '@/config/inspirations';

const WINDOW_OPTIONS = [7, 14, 30] as const;
const MAX_VISIBLE_SOURCES = 8;

export default function AdminDashboard() {
  const { t } = useTranslation(['admin', 'common']);
  const queryClient = useQueryClient();
  const growthFetching = useIsFetching({queryKey: ['admin', 'dashboard', 'growth']});
  const [selectedWindowDays, setSelectedWindowDays] = useState<(typeof WINDOW_OPTIONS)[number]>(7);
  const [showAllUpgradeSources, setShowAllUpgradeSources] = useState(false);
  const [showAllConversionSources, setShowAllConversionSources] = useState(false);

  const { data: stats, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['admin', 'dashboard', 'stats'],
    queryFn: adminApi.getDashboardStats,
  });
  const {
    data: activationFunnel,
    isLoading: activationFunnelLoading,
    isError: activationFunnelError,
    isFetching: activationFunnelFetching,
    refetch: refetchActivationFunnel,
  } = useQuery({
    queryKey: ['admin', 'dashboard', 'activation-funnel', selectedWindowDays],
    queryFn: () => adminApi.getActivationFunnel(selectedWindowDays),
  });
  const {
    data: upgradeConversion,
    isLoading: upgradeConversionLoading,
    isError: upgradeConversionError,
    isFetching: upgradeConversionFetching,
    refetch: refetchUpgradeConversion,
  } = useQuery({
    queryKey: ['admin', 'dashboard', 'upgrade-conversion', selectedWindowDays],
    queryFn: () => adminApi.getUpgradeConversionStats(selectedWindowDays),
  });
  const {
    data: upgradeFunnel,
    isLoading: upgradeFunnelLoading,
    isError: upgradeFunnelError,
    isFetching: upgradeFunnelFetching,
    refetch: refetchUpgradeFunnel,
  } = useQuery({
    queryKey: ['admin', 'dashboard', 'upgrade-funnel', selectedWindowDays],
    queryFn: () => adminApi.getUpgradeFunnelStats(selectedWindowDays),
  });
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t('common:error');
  const isRefreshing = growthFetching > 0 || isFetching || activationFunnelFetching || upgradeFunnelFetching || upgradeConversionFetching;

  const upgradeTotals = upgradeFunnel?.totals ?? { expose: 0, click: 0, conversion: 0 };
  const upgradeConversionTotal = upgradeConversion?.total_conversions ?? 0;
  const upgradeConversionPaid = upgradeConversion?.paid_conversions ?? 0;
  const conversionChannels = upgradeConversion?.channels ?? [];
  const upgradeConversionUnattributed = upgradeConversion?.unattributed_conversions ?? 0;
  const upgradeConversionAttributed = Math.max(upgradeConversionTotal - upgradeConversionUnattributed, 0);
  const upgradeConversionAttributedShare = useMemo(() => {
    if (upgradeConversionTotal <= 0) return null;
    return upgradeConversionAttributed / upgradeConversionTotal;
  }, [upgradeConversionAttributed, upgradeConversionTotal]);

  const allUpgradeSources = upgradeFunnel?.sources ?? [];
  const visibleUpgradeSources = showAllUpgradeSources
    ? allUpgradeSources
    : allUpgradeSources.slice(0, MAX_VISIBLE_SOURCES);
  const hasMoreUpgradeSources = allUpgradeSources.length > MAX_VISIBLE_SOURCES;
  const allConversionSources = upgradeConversion?.sources ?? [];
  const visibleConversionSources = showAllConversionSources
    ? allConversionSources
    : allConversionSources.slice(0, MAX_VISIBLE_SOURCES);
  const hasMoreConversionSources = allConversionSources.length > MAX_VISIBLE_SOURCES;

  const formatPercent = (value: number | null | undefined) => {
    if (value === null || value === undefined) return '-';
    return `${(value * 100).toFixed(1)}%`;
  };

  return (
    <div className="admin-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="admin-page-title">
            {t('admin:dashboard.title')}
          </h1>
          <p className="admin-page-subtitle">
            {t('admin:dashboard.subtitle')}
          </p>
        </div>
        <button
          className="btn-ghost flex items-center gap-2 text-sm"
          onClick={() => {
            void Promise.all([queryClient.invalidateQueries({queryKey: ['admin', 'dashboard', 'growth']}), refetch(), refetchActivationFunnel(), refetchUpgradeFunnel(), refetchUpgradeConversion()]);
          }}
          disabled={isRefreshing}
        >
          <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} />
          {t('admin:dashboard.refresh')}
        </button>
      </div>

      <div className="admin-surface p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {t('admin:dashboard.windowLabel', '时间窗口')}
          </span>
          {WINDOW_OPTIONS.map((days) => (
            <button
              key={days}
              type="button"
              onClick={() => {
                setSelectedWindowDays(days);
                setShowAllUpgradeSources(false);
                setShowAllConversionSources(false);
              }}
              aria-pressed={selectedWindowDays === days}
              className={`min-h-11 px-3 rounded-md text-sm transition-colors ${
                selectedWindowDays === days
                  ? 'bg-[hsl(var(--accent-primary))] text-white'
                  : 'bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]'
              }`}
            >
              {t(`admin:dashboard.window${days}`, `${days}天`)}
            </button>
          ))}
        </div>
      </div>

      <GrowthOverview days={selectedWindowDays} />

      <AdminPageState
        isLoading={isLoading}
        isFetching={isRefreshing}
        isError={isError}
        isEmpty={!stats}
        loadingText={t('common:loading')}
        errorText={queryErrorText}
        emptyText={t('common:noData')}
        retryText={t('common:retry')}
        onRetry={() => {
          void refetch();
        }}
      >
        <>
          {/* Stats Grid - Basic */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatsCard
              icon={<Users className="h-5 w-5" />}
              title={t('admin:dashboard.totalUsers')}
              value={stats?.total_users ?? 0}
              isLoading={isLoading}
            />
            <StatsCard
              icon={<FolderOpen className="h-5 w-5" />}
              title={t('admin:dashboard.totalProjects')}
              value={stats?.total_projects ?? 0}
              isLoading={isLoading}
            />
            {inspirationsConfig.enabled && (
              <StatsCard
                icon={<Lightbulb className="h-5 w-5" />}
                title={t('admin:dashboard.totalInspirations')}
                value={stats?.total_inspirations ?? 0}
                isLoading={isLoading}
              />
            )}
            <StatsCard
              icon={<CreditCard className="h-5 w-5" />}
              title={t('admin:dashboard.paidProUsers', '当前 Pro 用户（含赠送）')}
              value={stats?.pro_users ?? 0}
              isLoading={isLoading}
            />
          </div>

          {/* Stats Grid - Commercial */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatsCard
              icon={<Coins className="h-5 w-5" />}
              title={t('admin:dashboard.totalPointsInCirculation')}
              value={stats?.total_points_in_circulation ?? 0}
              isLoading={isLoading}
            />
            <StatsCard
              icon={<CalendarCheck className="h-5 w-5" />}
              title={t('admin:dashboard.todayCheckIns')}
              value={stats?.today_check_ins ?? 0}
              isLoading={isLoading}
            />
            <StatsCard
              icon={<Gift className="h-5 w-5" />}
              title={t('admin:dashboard.activeInviteCodes')}
              value={stats?.active_invite_codes ?? 0}
              isLoading={isLoading}
            />
            <StatsCard
              icon={<UserPlus className="h-5 w-5" />}
              title={t('admin:dashboard.weekReferrals')}
              value={stats?.week_referrals ?? 0}
              isLoading={isLoading}
            />
          </div>

          <div className="admin-surface p-4">
            <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
              {t('admin:growth.events')}
            </h2>

            {activationFunnelLoading && (
              <p className="text-sm text-[hsl(var(--text-secondary))]">
                {t('common:loading')}
              </p>
            )}

            {!activationFunnelLoading && activationFunnelError && (
              <p className="text-sm text-[hsl(var(--error))]">
                {t('admin:dashboard.loadError', '加载失败，请重试')}
              </p>
            )}

            {!activationFunnelLoading && !activationFunnelError && (
              <div className="space-y-2">
                {activationFunnel?.steps.map((step) => (
                  <div
                    key={step.event_name}
                    className="grid grid-cols-2 gap-2 text-sm py-2 border-b border-[hsl(var(--separator-color)/0.4)] last:border-0"
                  >
                    <span className="text-[hsl(var(--text-primary))]">{step.label}</span>
                    <span className="text-[hsl(var(--text-primary))] font-medium">{step.users}</span>

                  </div>
                ))}

                <p className="pt-2 text-sm text-[hsl(var(--text-secondary))]">{t('admin:growth.eventsNote')}</p>
              </div>
            )}
          </div>

          <div className="admin-surface p-4">
            <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
              {t('admin:dashboard.upgradeEntryEvents')}
            </h2>

            {upgradeFunnelLoading && (
              <p className="text-sm text-[hsl(var(--text-secondary))]">
                {t('common:loading')}
              </p>
            )}

            {!upgradeFunnelLoading && upgradeFunnelError && (
              <p className="text-sm text-[hsl(var(--error))]">
                {t('admin:dashboard.loadError', '加载失败，请重试')}
              </p>
            )}

            {!upgradeFunnelLoading && !upgradeFunnelError && upgradeFunnel && (
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-2 text-sm">
                  <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                    <div className="text-[hsl(var(--text-secondary))]">
                      {t('admin:dashboard.upgradeExpose', '曝光')}
                    </div>
                    <div className="text-[hsl(var(--text-primary))] font-semibold">
                      {upgradeTotals.expose}
                    </div>
                  </div>
                  <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                    <div className="text-[hsl(var(--text-secondary))]">
                      {t('admin:dashboard.upgradeClick', '点击')}
                    </div>
                    <div className="text-[hsl(var(--text-primary))] font-semibold">
                      {upgradeTotals.click}
                    </div>
                  </div>
                  <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                    <div className="text-[hsl(var(--text-secondary))]">
                      {t('admin:dashboard.upgradeConversion', '转化')}
                    </div>
                    <div className="text-[hsl(var(--text-primary))] font-semibold">
                      {upgradeTotals.conversion}
                    </div>
                  </div>



                </div>

                <div className="space-y-2">
                  {allUpgradeSources.length > 0 && (
                    <div className="text-xs text-[hsl(var(--text-secondary))]">
                      {t('admin:dashboard.upgradeSourceCount', {
                        count: allUpgradeSources.length,
                        defaultValue: `来源数：${allUpgradeSources.length}`,
                      })}
                    </div>
                  )}

                  {allUpgradeSources.length === 0 && (
                    <p className="text-sm text-[hsl(var(--text-secondary))]">
                      {t('common:noData')}
                    </p>
                  )}

                  {allUpgradeSources.length > 0 && (
                    <div className="overflow-x-auto rounded-md border border-[hsl(var(--separator-color)/0.45)]">
                      <table className="w-full min-w-[760px] text-sm">
                        <thead className="bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                          <tr>
                            <th className="px-3 py-2 text-left font-medium">
                              {t('admin:dashboard.upgradeSource', '来源')}
                            </th>
                            <th className="px-3 py-2 text-right font-medium">
                              {t('admin:dashboard.upgradeExpose', '曝光')}
                            </th>
                            <th className="px-3 py-2 text-right font-medium">
                              {t('admin:dashboard.upgradeClick', '点击')}
                            </th>
                            <th className="px-3 py-2 text-right font-medium">
                              {t('admin:dashboard.upgradeConversion', '转化')}
                            </th>


                          </tr>
                        </thead>
                        <tbody>
                          {visibleUpgradeSources.map((item) => (
                            <tr key={item.source} className="border-t border-[hsl(var(--separator-color)/0.35)]">
                              <td className="px-3 py-2 text-[hsl(var(--text-primary))] truncate max-w-[320px]" title={item.source}>
                                {item.source}
                              </td>
                              <td className="px-3 py-2 text-right text-[hsl(var(--text-primary))]">{item.exposes}</td>
                              <td className="px-3 py-2 text-right text-[hsl(var(--text-primary))]">{item.clicks}</td>
                              <td className="px-3 py-2 text-right text-[hsl(var(--text-primary))]">{item.conversions}</td>


                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {hasMoreUpgradeSources && (
                    <button
                      type="button"
                      className="text-sm text-[hsl(var(--accent-primary))] hover:underline"
                      onClick={() => setShowAllUpgradeSources((prev) => !prev)}
                    >
                      {showAllUpgradeSources
                        ? t('admin:dashboard.upgradeShowTopSources', {
                            count: MAX_VISIBLE_SOURCES,
                            defaultValue: `仅显示前 ${MAX_VISIBLE_SOURCES} 个`,
                          })
                        : t('admin:dashboard.upgradeShowAllSources', {
                            count: allUpgradeSources.length,
                            defaultValue: `查看全部（${allUpgradeSources.length}）`,
                          })}
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="border-t border-[hsl(var(--separator-color)/0.35)] pt-3">
              <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                {t('admin:dashboard.upgradePaidAttributionTitle', '付费转化归因')}
              </h3>
              <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">{t('admin:growth.legacyUpgradeNote')}</p>

              {upgradeConversionLoading && (
                <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
                  {t('common:loading')}
                </p>
              )}

              {!upgradeConversionLoading && upgradeConversionError && (
                <p className="mt-2 text-sm text-[hsl(var(--error))]">
                  {t('admin:dashboard.loadError', '加载失败，请重试')}
                </p>
              )}

              {!upgradeConversionLoading && !upgradeConversionError && (
                <div className="mt-3 space-y-3">
                  <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
                    <div className="rounded-md border border-[hsl(var(--accent-primary)/0.5)] p-2">
                      <div className="text-[hsl(var(--text-secondary))]">
                        {t('admin:dashboard.upgradePaidConversions', '付费转化（支付宝）')}
                      </div>
                      <div className="text-[hsl(var(--text-primary))] font-semibold" data-testid="paid-conversions">
                        {upgradeConversionPaid}
                      </div>
                    </div>
                    <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                      <div className="text-[hsl(var(--text-secondary))]">
                        {t('admin:dashboard.upgradeAllConversions', '全部升级')}
                      </div>
                      <div className="text-[hsl(var(--text-primary))] font-semibold">
                        {upgradeConversionTotal}
                      </div>
                    </div>
                    <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                      <div className="text-[hsl(var(--text-secondary))]">
                        {t('admin:dashboard.upgradeAttributedConversions', '已归因转化')}
                      </div>
                      <div className="text-[hsl(var(--text-primary))] font-semibold">
                        {upgradeConversionAttributed}
                      </div>
                    </div>
                    <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                      <div className="text-[hsl(var(--text-secondary))]">
                        {t('admin:dashboard.upgradeUnattributedConversions', '未归因转化')}
                      </div>
                      <div className="text-[hsl(var(--text-primary))] font-semibold">
                        {upgradeConversionUnattributed}
                      </div>
                    </div>
                    <div className="rounded-md border border-[hsl(var(--separator-color)/0.5)] p-2">
                      <div className="text-[hsl(var(--text-secondary))]">
                        {t('admin:dashboard.upgradeAttributedShare', '归因覆盖率')}
                      </div>
                      <div className="text-[hsl(var(--text-primary))] font-semibold">
                        {formatPercent(upgradeConversionAttributedShare)}
                      </div>
                    </div>
                  </div>

                  {conversionChannels.length > 0 && (
                    <ul className="flex flex-wrap gap-2 text-xs" aria-label={t('admin:dashboard.upgradeChannels', '升级渠道')}>
                      {conversionChannels.map((item) => (
                        <li
                          key={item.channel}
                          className={`rounded-full border px-2 py-0.5 ${
                            item.paid
                              ? 'border-[hsl(var(--accent-primary)/0.6)] text-[hsl(var(--text-primary))]'
                              : 'border-[hsl(var(--separator-color)/0.6)] text-[hsl(var(--text-secondary))]'
                          }`}
                        >
                          {t(`admin:dashboard.upgradeChannel.${item.channel}`, item.channel)} · {item.conversions}
                        </li>
                      ))}
                    </ul>
                  )}

                  {allConversionSources.length > 0 && (
                    <div className="text-xs text-[hsl(var(--text-secondary))]">
                      {t('admin:dashboard.upgradeConversionSourceCount', {
                        count: allConversionSources.length,
                        defaultValue: `归因来源：${allConversionSources.length}`,
                      })}
                    </div>
                  )}

                  {allConversionSources.length === 0 && (
                    <p className="text-sm text-[hsl(var(--text-secondary))]">
                      {t('common:noData')}
                    </p>
                  )}

                  {allConversionSources.length > 0 && (
                    <div className="overflow-x-auto rounded-md border border-[hsl(var(--separator-color)/0.45)]">
                      <table className="w-full min-w-[560px] text-sm">
                        <thead className="bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                          <tr>
                            <th className="px-3 py-2 text-left font-medium">
                              {t('admin:dashboard.upgradeSource', '来源')}
                            </th>
                            <th className="px-3 py-2 text-right font-medium">
                              {t('admin:dashboard.upgradeConversion', '转化')}
                            </th>
                            <th className="px-3 py-2 text-right font-medium">
                              {t('admin:dashboard.upgradeConversionShare', '转化占比')}
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {visibleConversionSources.map((item) => (
                            <tr key={item.source} className="border-t border-[hsl(var(--separator-color)/0.35)]">
                              <td className="px-3 py-2 text-[hsl(var(--text-primary))] truncate max-w-[320px]" title={item.source}>
                                {item.source}
                              </td>
                              <td className="px-3 py-2 text-right text-[hsl(var(--text-primary))]">{item.conversions}</td>
                              <td className="px-3 py-2 text-right text-[hsl(var(--text-secondary))]">
                                {formatPercent(item.share)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {hasMoreConversionSources && (
                    <button
                      type="button"
                      className="text-sm text-[hsl(var(--accent-primary))] hover:underline"
                      onClick={() => setShowAllConversionSources((prev) => !prev)}
                    >
                      {showAllConversionSources
                        ? t('admin:dashboard.upgradeShowTopConversionSources', {
                            count: MAX_VISIBLE_SOURCES,
                            defaultValue: `仅显示前 ${MAX_VISIBLE_SOURCES} 个归因来源`,
                          })
                        : t('admin:dashboard.upgradeShowAllConversionSources', {
                            count: allConversionSources.length,
                            defaultValue: `查看全部归因来源（${allConversionSources.length}）`,
                          })}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      </AdminPageState>

      {/* Recent Activity */}
      <div className="admin-surface p-4">
        <h2 className="text-lg font-semibold mb-4 text-[hsl(var(--text-primary))]">
          {t('admin:dashboard.recentActivity')}
        </h2>
        <RecentActivityList />
      </div>
    </div>
  );
}
