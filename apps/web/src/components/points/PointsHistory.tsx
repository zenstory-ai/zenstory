/**
 * Points History Component - Transaction history list with pagination
 */
import { useState } from 'react';
import { parseUTCDate } from '@/lib/dateUtils';
import { useQuery } from '@tanstack/react-query';
import { pointsApi } from '../../lib/pointsApi';
import { useTranslation } from 'react-i18next';
import type { PointsTransaction } from '../../types/points';
import { Card } from '../ui/Card';

interface PointsHistoryProps {
  className?: string;
  pageSize?: number;
}

const TRANSACTION_TYPE_ICONS: Record<string, React.ReactNode> = {
  check_in: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
  ),
  check_in_streak: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z" />
    </svg>
  ),
  referral: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  ),
  redeem_pro: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
    </svg>
  ),
  default: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8V7m0 1v8m0 0v1" />
    </svg>
  ),
};

function formatTransactionDate(
  dateStr: string,
  t: (key: string, options?: Record<string, unknown>) => string,
  localeCode: string,
): string {
  const date = parseUTCDate(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) {
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    if (diffHours === 0) {
      const diffMins = Math.floor(diffMs / (1000 * 60));
      return t('relativeTime.minutesAgo', { count: diffMins });
    }
    return t('relativeTime.hoursAgo', { count: diffHours });
  } else if (diffDays === 1) {
    return t('relativeTime.yesterday');
  } else if (diffDays < 7) {
    return t('relativeTime.daysAgo', { count: diffDays });
  } else {
    return date.toLocaleDateString(localeCode);
  }
}

export function PointsHistory({ className = '', pageSize = 10 }: PointsHistoryProps) {
  const { t, i18n } = useTranslation(['points', 'common']);
  const [page, setPage] = useState(1);
  const localeCode = i18n.language === 'zh' ? 'zh-CN' : 'en-US';

  const { data, isLoading, isFetching, isError } = useQuery({
    queryKey: ['points-transactions', page, pageSize],
    queryFn: () => pointsApi.getTransactions(page, pageSize),
  });

  const { transactions, total, total_pages } = data || { transactions: [], total: 0, total_pages: 0 };
  const isHistoryLoading = isLoading || (isFetching && transactions.length === 0);

  if (isError || (!isHistoryLoading && !data)) {
    return (
      <Card className={className}>
        <div className="p-4 text-center text-[hsl(var(--text-secondary))]">
          {t('historyError')}
        </div>
      </Card>
    );
  }

  if (!isHistoryLoading && transactions.length === 0) {
    return (
      <Card className={className}>
        <div className="p-4 text-center text-[hsl(var(--text-secondary))]">
          <svg className="w-12 h-12 mx-auto mb-2 text-[hsl(var(--text-secondary)/0.5)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
          <p>{t('noHistory')}</p>
        </div>
      </Card>
    );
  }

  return (
    <Card className={className} padding="none" isLoading={isHistoryLoading}>
      <div className="p-4 border-b border-[hsl(var(--border-color))]">
        <h3 className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {t('history')}
        </h3>
        <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
          {t('totalTransactions', { count: total })}
        </p>
      </div>

      <div className="divide-y divide-[hsl(var(--separator-color))]">
        {transactions.map((tx: PointsTransaction) => (
          <div key={tx.id} className="p-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-full ${
                tx.amount > 0
                  ? 'bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success-light))]'
                  : 'bg-[hsl(var(--error)/0.15)] text-[hsl(var(--error))]'
              }`}>
                {TRANSACTION_TYPE_ICONS[tx.transaction_type] || TRANSACTION_TYPE_ICONS.default}
              </div>
              <div>
                <p className="text-sm text-[hsl(var(--text-primary))]">
                  {t(`transactionTypes.${tx.transaction_type}`, tx.transaction_type)}
                  {tx.description && !['check_in', 'check_in_streak', 'redeem_pro'].includes(tx.transaction_type)
                    ? ` - ${tx.description}`
                    : ''}
                </p>
                <p className="text-xs text-[hsl(var(--text-secondary))]">
                  {formatTransactionDate(tx.created_at, t, localeCode)}
                </p>
              </div>
            </div>
            <div className="text-right">
              <p className={`text-sm font-medium ${
                tx.amount > 0
                  ? 'text-[hsl(var(--success-light))]'
                  : 'text-[hsl(var(--error))]'
              }`}>
                {tx.amount > 0 ? '+' : ''}{tx.amount}
              </p>
              {tx.is_expired && (
                <p className="text-xs text-[hsl(var(--error))]">
                  {t('expired')}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Pagination */}
      {total_pages > 1 && (
        <div className="p-3 border-t border-[hsl(var(--border-color))] flex items-center justify-between">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {t('common:previous')}
          </button>
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {page} / {total_pages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(total_pages, p + 1))}
            disabled={page === total_pages}
            className="px-3 py-1 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {t('common:next')}
          </button>
        </div>
      )}
    </Card>
  );
}
