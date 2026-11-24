import type { ReactNode } from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { Skeleton } from '../ui/Skeleton';
import { Card } from '../ui/Card';
import { IconWrapper } from '../ui/IconWrapper';

interface StatsCardProps {
  icon: ReactNode;
  title: string;
  value: number | string;
  isLoading?: boolean;
  trend?: 'up' | 'down' | 'neutral';
  trendValue?: string;
  description?: string;
}

export function StatsCard({
  icon,
  title,
  value,
  isLoading = false,
  trend,
  trendValue,
  description,
}: StatsCardProps) {
  const trendIcon = {
    up: <TrendingUp className="h-4 w-4 text-[hsl(var(--success-text))]" />,
    down: <TrendingDown className="h-4 w-4 text-[hsl(var(--error-text))]" />,
    neutral: <Minus className="h-4 w-4 text-[hsl(var(--text-secondary))]" />,
  };

  const trendColor = {
    up: 'text-[hsl(var(--success-text))]',
    down: 'text-[hsl(var(--error-text))]',
    neutral: 'text-[hsl(var(--text-secondary))]',
  };

  if (isLoading) {
    return (
      <Card
        variant="outlined"
        borderColor="separator"
        rounded="xl"
        padding="responsive"
        className="h-full border bg-[hsl(var(--bg-secondary))]"
      >
        <div className="flex items-center gap-4">
          {/* Icon skeleton */}
          <Skeleton variant="rectangular" width={40} height={40} className="rounded-lg" />
          <div className="flex-1 space-y-2">
            {/* Title skeleton */}
            <Skeleton variant="text" width={96} height={16} />
            {/* Value skeleton */}
            <Skeleton variant="text" width={64} height={24} />
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card
      variant="outlined"
      borderColor="separator"
      rounded="xl"
      padding="responsive"
      className="h-full border bg-[hsl(var(--bg-secondary))]"
    >
      <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-4 motion-content-reveal">
        <IconWrapper size="2xl" variant="primary" rounded="md">
          {icon}
        </IconWrapper>
        <div className="w-full min-w-0 sm:flex-1">
          <p className="text-sm text-[hsl(var(--text-secondary))] break-words">{title}</p>
          <div className="flex flex-wrap items-baseline gap-2">
            <p className="text-xl sm:text-2xl leading-tight font-semibold tabular-nums text-[hsl(var(--text-primary))] whitespace-nowrap">
              {typeof value === 'number' ? value.toLocaleString() : value}
            </p>
            {trend && trendValue && (
              <span className={`flex items-center gap-1 text-xs ${trendColor[trend]}`}>
                {trendIcon[trend]}
                {trendValue}
              </span>
            )}
          </div>
          {description && <p className="mt-1 text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">{description}</p>}
        </div>
      </div>
    </Card>
  );
}

export default StatsCard;
