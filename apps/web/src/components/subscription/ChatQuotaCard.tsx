import { useMobileLayout } from "../../contexts/MobileLayoutContext";
import { usePaidPlanWhenOpen } from "../../hooks/usePaidPlanWhenOpen";
import { useUpgradeExposure } from "../../hooks/useUpgradeExposure";
import { useTranslation } from "react-i18next";
import { Clock } from "lucide-react";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { formatAiQuotaResetDay, formatAiQuotaResetLocal, aiQuotaPeriodKey } from "../../hooks/useAiMessageQuota";
import { trackUpgradeClick } from "../../lib/upgradeAnalytics";
import { CHAT_CARD_PRIMARY_BUTTON, CHAT_CARD_SURFACE } from "../chatCardStyles";

const chatQuotaPrompt = getUpgradePromptDefinition("chat_quota_blocked");

interface ChatQuotaCardProps {
  /**
   * Today's free AI message allowance (3 on the free plan). Left out when the server
   * refused a round while the cached count still shows room: the card then names no count.
   */
  limit?: number;
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
  const { isMobile, activePanel } = useMobileLayout();
  const { isPaid, resolved } = usePaidPlanWhenOpen(true);
  const canUpgrade = resolved && !isPaid;
  const exposureRef = useUpgradeExposure({ enabled: canUpgrade && (!isMobile || activePanel === "chat"), source: chatQuotaPrompt.source, surface: "card", placement: "chat-input", period: aiQuotaPeriodKey(resetAt) });
  const resetDay = formatAiQuotaResetDay(resetAt, i18n?.language);
  const resetLocal = formatAiQuotaResetLocal(resetAt, i18n?.language);



  return (
    <div
      ref={exposureRef}
      data-testid="chat-quota-card"
      role="status"
      className={`mb-2 p-3 ${CHAT_CARD_SURFACE}`}
    >
      <p className="flex items-start gap-2 text-xs font-semibold leading-5 text-[hsl(var(--text-primary))]">
        <Clock size={15} aria-hidden="true" className="mt-0.5 shrink-0 text-[hsl(var(--text-secondary))]" />
        <span>
          {!canUpgrade ? t("chat:quotaCard.paidTitle", { defaultValue: "今天的 AI 消息额度已用完" }) : typeof limit === "number" && limit > 0
            ? t("chat:quotaCard.title", { defaultValue: "今天的 {{limit}} 条免费 AI 消息用完了", limit })
            : t("chat:panel.quotaExceededTitle", { defaultValue: "今天的免费 AI 消息用完了" })}
        </span>
      </p>
      <p className="mt-1 pl-[23px] text-xs leading-5 text-[hsl(var(--text-secondary))]">
        {resetDay && resetLocal
          ? t("chat:quotaCard.resetLocal", {
              defaultValue: "北京时间 {{day}} 00:00 恢复，也就是你那里的 {{local}}。写好的内容会留在输入框里，到时再发。",
              day: resetDay,
              local: resetLocal,
            })
          : resetDay
          ? t("chat:quotaCard.resetOnDay", {
              defaultValue: "北京时间 {{day}} 00:00 恢复。写好的内容会留在输入框里，到时再发。",
              day: resetDay,
            })
          : t("chat:quotaCard.resetTomorrow", {
              defaultValue: "北京时间明天 00:00 恢复。写好的内容会留在输入框里，到时再发。",
            })}
        {" "}
        {canUpgrade && t("chat:quotaCard.upgradeHint", { defaultValue: "想现在接着写，可以开通 Pro，AI 消息不限条数。" })}
      </p>
      {canUpgrade && <div className="mt-2.5 pl-[23px]">
        <button
          type="button"
          data-testid="chat-quota-card-upgrade"
          onClick={() => {
            trackUpgradeClick(chatQuotaPrompt.source, "primary", "billing", "card");
            window.location.assign(buildUpgradeUrl(chatQuotaPrompt.billingPath, chatQuotaPrompt.source));
          }}
          className={CHAT_CARD_PRIMARY_BUTTON}
        >
          {t("dashboard:billing.ctaUpgradePro", { defaultValue: "开通 Pro" })}
        </button>
      </div>}
    </div>
  );
}

export default ChatQuotaCard;
