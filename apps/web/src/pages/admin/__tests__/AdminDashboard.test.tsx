import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockGetGrowthDashboard = vi.fn();
const mockGetDashboardStats = vi.fn();
const mockGetActivationFunnel = vi.fn();
const mockGetUpgradeFunnelStats = vi.fn();
const mockGetUpgradeConversionStats = vi.fn();
const inspirationFeature = vi.hoisted(() => ({ enabled: true }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: string | { defaultValue?: string }) => {
      if (typeof options === 'string') {
        return options;
      }
      return options?.defaultValue ?? key;
    },
  }),
}));

vi.mock('@/lib/adminApi', () => ({
  adminApi: {
    getGrowthDashboard: (days: number) => mockGetGrowthDashboard(days),
    getDashboardStats: () => mockGetDashboardStats(),
    getActivationFunnel: (days: number) => mockGetActivationFunnel(days),
    getUpgradeFunnelStats: (days: number) => mockGetUpgradeFunnelStats(days),
    getUpgradeConversionStats: (days: number) => mockGetUpgradeConversionStats(days),
  },
}));

vi.mock('@/components/admin/RecentActivityList', () => ({
  RecentActivityList: () => <div data-testid="recent-activity-placeholder" />,
}));

vi.mock('@/config/inspirations', () => ({
  inspirationsConfig: inspirationFeature,
}));

import AdminDashboard from '../AdminDashboard';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    );
  };
}

describe('AdminDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inspirationFeature.enabled = true;

    const metrics = { new_users: 10, ai_active_users: 4, cohort_activated_users: 3, cohort_activation_rate: 0.3, paid_orders: 2, revenue_cents: 29900, paid_users: 1, cohort_paid_users: 1, signup_to_paid_rate: 0.1, grant_upgrade_events: 5, grant_upgrade_users: 4, grant_channels: [] };
    mockGetGrowthDashboard.mockResolvedValue({days: 7, timezone: 'Asia/Shanghai', current: {period_start: '2026-10-01T16:00:00Z', period_end: '2026-10-07T08:00:00Z', metrics}, previous: {period_start: '2026-09-24T16:00:00Z', period_end: '2026-09-30T08:00:00Z', metrics: {...metrics, new_users: 5}}, daily: [{date: '2026-10-07', ...metrics}], definitions: {}});
    mockGetDashboardStats.mockResolvedValue({
      total_users: 100,
      total_projects: 40,
      total_inspirations: 22,
      active_subscriptions: 12,
      pro_users: 7,
      total_points_in_circulation: 5000,
      today_check_ins: 20,
      active_invite_codes: 9,
      week_referrals: 4,
    });

    mockGetActivationFunnel.mockResolvedValue({
      window_days: 7,
      period_start: '2026-03-01T00:00:00Z',
      period_end: '2026-03-08T00:00:00Z',
      activation_rate: 0.5,
      steps: [],
    });

    mockGetUpgradeFunnelStats.mockResolvedValue({
      window_days: 7,
      period_start: '2026-03-01T00:00:00Z',
      period_end: '2026-03-08T00:00:00Z',
      totals: { expose: 20, click: 10, conversion: 4 },
      sources: [],
    });

    mockGetUpgradeConversionStats.mockResolvedValue({
      window_days: 7,
      period_start: '2026-03-01T00:00:00Z',
      period_end: '2026-03-08T00:00:00Z',
      total_conversions: 5,
      paid_conversions: 3,
      unattributed_conversions: 2,
      channels: [
        { channel: 'zpay', conversions: 3, paid: true },
        { channel: 'admin_update', conversions: 2, paid: false },
      ],
      sources: [
        {
          source: 'chat_quota_blocked',
          conversions: 2,
          share: 0.4,
        },
        {
          source: 'settings_subscription_upgrade',
          conversions: 1,
          share: 0.2,
        },
      ],
    });
  });

  it('puts cohort growth before account totals and does not display independent event ratios', async () => {
    mockGetActivationFunnel.mockResolvedValue({ steps: [{event_name: 'ai', label: 'AI telemetry', users: 31, conversion_from_previous: 3.1}], activation_rate: 3.1 });
    mockGetUpgradeFunnelStats.mockResolvedValue({totals: {expose: 10, click: 20, conversion: 30}, sources: []});
    render(<AdminDashboard />, {wrapper: createWrapper()});
    expect(await screen.findByText('admin:growth.title')).toBeInTheDocument();
    expect(await screen.findByText('AI telemetry')).toBeInTheDocument();
    expect(screen.queryByText('310.0%')).not.toBeInTheDocument();
    expect(screen.queryByText('200.0%')).not.toBeInTheDocument();
    expect(screen.getAllByText('30.0%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('¥299.00').length).toBeGreaterThan(0);
  });

  it('retries growth failures independently from account totals', async () => {
    mockGetGrowthDashboard.mockRejectedValueOnce(new Error('failed'));
    render(<AdminDashboard />, {wrapper: createWrapper()});
    const retry = await screen.findByRole('button', {name: 'common:retry'});
    fireEvent.click(retry);
    await waitFor(() => expect(mockGetGrowthDashboard).toHaveBeenCalledTimes(2));
    expect(mockGetDashboardStats).toHaveBeenCalledTimes(1);
  });

  it('renders paid conversion attribution and refetches on window change', async () => {
    render(<AdminDashboard />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('付费转化归因')).toBeInTheDocument();
    });

    expect(mockGetUpgradeConversionStats).toHaveBeenCalledWith(7);
    expect(screen.getByText('归因来源：2')).toBeInTheDocument();
    expect(screen.getByText('chat_quota_blocked')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '14天' }));

    await waitFor(() => {
      expect(mockGetUpgradeConversionStats).toHaveBeenCalledWith(14);
      expect(mockGetGrowthDashboard).toHaveBeenCalledWith(14);
    });
  });

  it('hides the inspiration statistics card when disabled', async () => {
    inspirationFeature.enabled = false;
    render(<AdminDashboard />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('付费转化归因')).toBeInTheDocument();
    });
    expect(screen.queryByText('admin:dashboard.totalInspirations')).not.toBeInTheDocument();
    expect(screen.queryByText('22')).not.toBeInTheDocument();
  });

  it('shows paid Pro users and separates paid conversions from grants', async () => {
    render(<AdminDashboard />, { wrapper: createWrapper() });

    expect(await screen.findByText('当前 Pro 用户（含赠送）')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.queryByText('12')).not.toBeInTheDocument();
    expect(await screen.findByTestId('paid-conversions')).toHaveTextContent('3');
    expect(screen.getByText('zpay · 3')).toBeInTheDocument();
    expect(screen.getByText('admin_update · 2')).toBeInTheDocument();
  });
});
