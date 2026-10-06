import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { parseUTCDate } from '../../lib/dateUtils';
import { Activity, User, Code, CreditCard, Lightbulb } from 'lucide-react';
import { adminApi } from '@/lib/adminApi';
import { Skeleton } from '../ui/Skeleton';
import { IconWrapper } from '../ui/IconWrapper';

const resourceIcons: Record<string, React.ReactNode> = {
  user: <User className="h-4 w-4" />,
  code: <Code className="h-4 w-4" />,
  subscription: <CreditCard className="h-4 w-4" />,
  inspiration: <Lightbulb className="h-4 w-4" />,
};

const actionColors: Record<string, string> = {
  create: 'text-green-500',
  update: 'text-blue-500',
  delete: 'text-red-500',
  approve: 'text-green-500',
  reject: 'text-red-500',
};

function capitalize(value: string): string {
  if (!value) return value;
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
}

// Simple relative time formatting without date-fns
function formatRelativeTime(dateString: string, locale: string, t: (key: string, options?: Record<string, unknown>) => string): string {
  const date = parseUTCDate(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffMin < 1) return t('dashboard:time.justNow');
  if (diffMin < 60) return t('dashboard:time.minutesAgo', { count: diffMin });
  if (diffHour < 24) return t('dashboard:time.hoursAgo', { count: diffHour });
  if (diffDay < 7) return t('dashboard:time.daysAgo', { count: diffDay });
  return date.toLocaleDateString(locale === 'zh' ? 'zh-CN' : 'en-US');
}

export function RecentActivityList() {
  const { t, i18n } = useTranslation(['admin', 'common', 'dashboard']);

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['admin', 'audit-logs', 'recent'],
    queryFn: () => adminApi.getAuditLogs({ page: 1, page_size: 10 }),
  });

  const items = data?.items || [];
  const isActivityLoading = isLoading || (isFetching && items.length === 0);

  if (isActivityLoading) {
    return (
      <div className="space-y-3">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton variant="circular" width={32} height={32} />
            <div className="flex-1 space-y-1">
              <Skeleton variant="text" width="75%" height={16} />
              <Skeleton variant="text" width="50%" height={12} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-[hsl(var(--error)/0.28)] bg-[hsl(var(--error)/0.1)] p-4 text-sm text-[hsl(var(--error))]">
        {t('admin:dashboard.loadError', '加载失败，请重试')}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
        <Activity className="h-10 w-10 mb-2 opacity-50" />
        <p>{t('admin:dashboard.noActivity', '暂无最近操作')}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((item) => {
        const actionBase = item.action.split('_')[0];
        const actionKey = `admin:auditLogs.action${capitalize(actionBase)}`;
        const resourceKey = `admin:auditLogs.resource${capitalize(item.resource_type)}`;
        return (
        <div
          key={item.id}
          className="admin-surface flex items-start gap-3 p-3"
        >
          <IconWrapper size="xl" variant="gray" rounded="full">
            {resourceIcons[item.resource_type] || <Activity className="h-4 w-4" />}
          </IconWrapper>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium truncate text-[hsl(var(--text-primary))]">{item.admin_name}</span>
              <span className={`text-xs font-medium ${actionColors[actionBase] || 'text-[hsl(var(--text-secondary))]'}`}>
                {t(actionKey, { defaultValue: item.action })}
              </span>
            </div>
            <p className="text-xs text-[hsl(var(--text-secondary))] truncate">
              {t(resourceKey, { defaultValue: item.resource_type })}
            </p>
          </div>
          <span className="text-xs text-[hsl(var(--text-tertiary))] whitespace-nowrap">
            {formatRelativeTime(item.created_at, i18n.language, t)}
          </span>
        </div>
        );
      })}
    </div>
  );
}
