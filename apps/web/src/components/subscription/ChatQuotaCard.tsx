import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Clock } from "lucide-react";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { formatAiQuotaResetDay } from "../../hooks/useAiMessageQuota";
import { trackUpgradeClick, trackUpgradeExpose } from "../../lib/upgradeAnalytics";

const chatQuotaPrompt = getUpgradePromptDefinition("chat_quota_blocked");

interface ChatQuotaCardProps {
  /** Today's free AI message allowance (10 on the free plan). */
  limit: number;
  /** Next Beijing midnight from the quota API; the card says "明天" when it is missing. */
  resetAt: string | null;
}

/**
 * Stays above the chat input while today's free AI messages are used up: why sending
 * waits, when it comes back, and the way to keep writing now. Only the daily message
 * count is ever named here.
 */
export function ChatQuotaCard({ limit, resetAt }: ChatQuotaCardProps) {
  const { t, i18n } = useTranslation(["chat", "dashboard"]);
  const exposedRef = useRef(false);
  const resetDay = formatAiQuotaResetDay(resetAt, i18n?.language);

  useEffect(() => {
    if (exposedRef.current) return;
    exposedRef.current = true;
    trackUpgradeExpose(chatQuotaPrompt.source, "toast");
  }, []);

  return (
    <div
      data-testid="chat-quota-card"
      role="status"
      className="mb-2 rounded-lg border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.06)] px-3 py-2.5"
    >
      <p className="flex items-start gap-1.5 text-xs font-medium leading-5 text-[hsl(var(--text-primary))]">
        <Clock size={14} aria-hidden="true" className="mt-[3px] shrink-0 text-[hsl(var(--error))]" />
        {t("chat:quotaCard.title", { defaultValue: "今天的 {{limit}} 条免费 AI 消息用完了", limit })}
      </p>
      <p className="mt-1 pl-5 text-xs leading-5 text-[hsl(var(--text-secondary))]">
        {resetDay
          ? t("chat:quotaCard.resetOnDay", {
              defaultValue: "北京时间 {{day}} 00:00 恢复。写好的内容会留在输入框里，到时再发。",
              day: resetDay,
            })
          : t("chat:quotaCard.resetTomorrow", {
              defaultValue: "北京时间明天 00:00 恢复。写好的内容会留在输入框里，到时再发。",
            })}
        {" "}
        {t("chat:quotaCard.upgradeHint", { defaultValue: "想现在接着写，可以开通 Pro，AI 消息不限条数。" })}
      </p>
      <div className="mt-2 pl-5">
        <button
          type="button"
          data-testid="chat-quota-card-upgrade"
          onClick={() => {
            trackUpgradeClick(chatQuotaPrompt.source, "primary", "billing", "toast");
            window.location.assign(buildUpgradeUrl(chatQuotaPrompt.billingPath, chatQuotaPrompt.source));
          }}
          className="btn-primary rounded-md px-3 text-xs min-h-[44px] md:min-h-0 md:h-7"
        >
          {t("dashboard:billing.ctaUpgradePro", { defaultValue: "开通 Pro" })}
        </button>
      </div>
    </div>
  );
}

export default ChatQuotaCard;
