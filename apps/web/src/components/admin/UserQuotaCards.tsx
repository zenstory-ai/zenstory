import React from "react";
import { useTranslation } from "react-i18next";
import { ChartBar, Lightbulb, MessageSquare, Zap } from "lucide-react";
import type { QuotaCounter, UserQuotaDetail } from "../../types/admin";
import { formatAdminDateTime } from "../../lib/dateUtils";
import { inspirationsConfig } from "../../config/inspirations";

const usagePercent = (used: number, limit: number) => {
  if (limit === -1) return 0;
  if (limit === 0) return used > 0 ? 100 : 0;
  return Math.round((used / limit) * 100);
};

const barColor = (percentage: number) => {
  if (percentage >= 90) return "bg-red-500";
  if (percentage >= 70) return "bg-yellow-500";
  return "bg-green-500";
};

const QuotaCard: React.FC<{
  icon: React.ReactNode;
  title: string;
  counter: QuotaCounter;
  resetLabel?: string;
}> = ({ icon, title, counter, resetLabel }) => {
  const { t } = useTranslation(["admin"]);
  const unlimited = counter.limit === -1;
  const percentage = usagePercent(counter.used, counter.limit);
  return (
    <div className="admin-surface p-4" data-testid="quota-card">
      <div className="flex items-center gap-3 mb-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
          {icon}
        </div>
        <div className="min-w-0">
          <h3 className="font-medium text-[hsl(var(--text-primary))]">{title}</h3>
          <p className="text-sm text-[hsl(var(--text-secondary))]">
            {unlimited
              ? t("quota.usedUnlimited", { used: counter.used })
              : `${counter.used} / ${counter.limit}`}
          </p>
        </div>
      </div>
      {!unlimited && (
        <div className="h-2 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
          <div
            className={`h-full ${barColor(percentage)} transition-all`}
            style={{ width: `${Math.min(100, percentage)}%` }}
          />
        </div>
      )}
      {resetLabel && counter.reset_at && (
        <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
          {resetLabel}: {formatAdminDateTime(counter.reset_at)}
        </p>
      )}
    </div>
  );
};

/** The quotas the backend enforces for one user, with their current window. */
export const UserQuotaCards: React.FC<{ quota: UserQuotaDetail }> = ({ quota }) => {
  const { t } = useTranslation(["admin"]);
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <QuotaCard
        icon={<MessageSquare className="h-5 w-5" />}
        title={t("quota.aiConversations")}
        counter={quota.ai_conversations}
        resetLabel={t("quota.resetsAt")}
      />
      <QuotaCard
        icon={<ChartBar className="h-5 w-5" />}
        title={t("quota.materialDecompositions")}
        counter={quota.material_decompositions}
        resetLabel={t("quota.resetsAt")}
      />
      <QuotaCard
        icon={<Zap className="h-5 w-5" />}
        title={t("quota.customSkills")}
        counter={quota.custom_skills}
      />
      {inspirationsConfig.enabled && (
        <QuotaCard
          icon={<Lightbulb className="h-5 w-5" />}
          title={t("quota.inspirationCopy")}
          counter={quota.inspiration_copies}
          resetLabel={t("quota.resetsAt")}
        />
      )}
    </div>
  );
};

export default UserQuotaCards;
