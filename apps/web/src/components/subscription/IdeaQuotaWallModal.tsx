import { useTranslation } from "react-i18next";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { formatAiQuotaResetDay } from "../../hooks/useAiMessageQuota";
import { UpgradePromptModal } from "./UpgradePromptModal";

// Same wall as in the chat: today's free AI messages are used up.
const chatQuotaPrompt = getUpgradePromptDefinition("chat_quota_blocked");

interface IdeaQuotaWallModalProps {
  open: boolean;
  onClose: () => void;
  /** Next Beijing midnight from the quota API. */
  resetAt: string | null;
}

/**
 * Shown on the home page instead of creating a project whose first message could not
 * be sent today. The idea stays in the input; nothing is created.
 */
export function IdeaQuotaWallModal({ open, onClose, resetAt }: IdeaQuotaWallModalProps) {
  const { t, i18n } = useTranslation(["dashboard", "chat", "home"]);
  const resetDay = formatAiQuotaResetDay(resetAt, i18n?.language);

  return (
    <UpgradePromptModal
      open={open}
      onClose={onClose}
      source={chatQuotaPrompt.source}
      primaryDestination="billing"
      secondaryDestination="pricing"
      title={t("chat:panel.quotaExceededTitle", { defaultValue: "今天的免费 AI 消息用完了" })}
      description={
        resetDay
          ? t("dashboard:ideaQuota.descriptionOnDay", {
              defaultValue:
                "北京时间 {{day}} 00:00 恢复。你的想法还留在输入框里，到时点「开始创作」就能接着写。想现在就写，可以开通 Pro，AI 消息不限条数。",
              day: resetDay,
            })
          : t("dashboard:ideaQuota.descriptionTomorrow", {
              defaultValue:
                "北京时间明天 00:00 恢复。你的想法还留在输入框里，到时点「开始创作」就能接着写。想现在就写，可以开通 Pro，AI 消息不限条数。",
            })
      }
      primaryLabel={t("dashboard:billing.ctaUpgradePro", { defaultValue: "开通 Pro" })}
      onPrimary={() => {
        window.location.assign(buildUpgradeUrl(chatQuotaPrompt.billingPath, chatQuotaPrompt.source));
      }}
      secondaryLabel={t("home:pricingTeaser.viewPricing")}
      onSecondary={() => {
        window.location.assign(buildUpgradeUrl(chatQuotaPrompt.pricingPath, chatQuotaPrompt.source));
      }}
    />
  );
}

export default IdeaQuotaWallModal;
