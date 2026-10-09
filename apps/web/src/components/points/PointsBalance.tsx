/**
 * Points Balance Display Component
 */
import { useQuery } from '@tanstack/react-query';
import { pointsApi } from '../../lib/pointsApi';
import { useTranslation } from 'react-i18next';
import { Card } from '../ui/Card';

interface PointsBalanceProps {
  className?: string;
  showExpiration?: boolean;
}

export function PointsBalance({ className = '', showExpiration = true }: PointsBalanceProps) {
  const { t } = useTranslation('points');
  const { data: balance, isLoading } = useQuery({
    queryKey: ['points-balance'],
    queryFn: () => pointsApi.getBalance(),
    refetchInterval: 60000,
  });
  // Tell the author what points are for; hidden until the price is known.
  const { data: config } = useQuery({
    queryKey: ['points-config'],
    queryFn: () => pointsApi.getConfig(),
  });
  const proCost = config?.pro_7days_cost;

  if (!balance && !isLoading) return null;

  return (
    <Card className={className} isLoading={isLoading}>
      <div className="flex items-center gap-2 mb-2">
        <svg className="w-5 h-5 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
          <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.736 6.979C9.208 6.193 9.696 6 10 6c.304 0 .792.193 1.264.979a1 1 0 001.715-1.029C12.279 4.784 11.232 4 10 4s-2.279.784-2.979 1.95c-.285.475-.507 1-.67 1.55H6a1 1 0 000 2h.013a9.358 9.358 0 000 1H6a1 1 0 100 2h.351c.163.55.385 1.075.67 1.55C7.721 15.216 8.768 16 10 16s2.279-.784 2.979-1.95a1 1 0 10-1.715-1.029c-.472.786-.96.979-1.264.979-.304 0-.792-.193-1.264-.979a4.265 4.265 0 01-.264-.521H10a1 1 0 100-2H8.017a7.36 7.36 0 010-1H10a1 1 0 100-2H8.472a4.265 4.265 0 01.264-.521z" />
        </svg>
        <span className="text-xs font-medium text-[hsl(var(--text-secondary))]">
          {t('balance', '积分余额')}
        </span>
      </div>

      <div className="text-2xl font-bold text-[hsl(var(--text-primary))]">
        {balance?.available.toLocaleString() ?? 0}
      </div>

      {typeof proCost === 'number' && Number.isFinite(proCost) && proCost > 0 && (
        <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
          {t('balanceHint', '{{cost}} 积分可兑换 7 天 Pro', { cost: proCost.toLocaleString() })}
        </p>
      )}

      {showExpiration && balance?.pending_expiration && balance.pending_expiration > 0 && (
        <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
          {t('pendingExpiration', '{{count}} 积分即将过期', {
            count: balance.pending_expiration,
          })}
        </p>
      )}
    </Card>
  );
}
