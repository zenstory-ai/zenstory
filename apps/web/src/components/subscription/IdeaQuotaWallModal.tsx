import { useTranslation } from "react-i18next";
import { formatAiQuotaResetDay, formatAiQuotaResetLocal } from "../../hooks/useAiMessageQuota";
import { UpgradePromptModal } from "./UpgradePromptModal";

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
  const activeLanguage = (i18n?.resolvedLanguage || i18n?.language || "").trim().toLowerCase();
  const resetLocal = Boolean(activeLanguage) && !activeLanguage.startsWith("zh")
    ? formatAiQuotaResetLocal(resetAt, activeLanguage)
    : null;

  return (
    <UpgradePromptModal
      open={open}
      onClose={onClose}
      promptId="chat_quota_blocked"
      title={t("chat:panel.quotaExceededTitle", { defaultValue: "今天的免费 AI 消息用完了" })}
      description={
        resetLocal
          ? t("dashboard:ideaQuota.descriptionLocal", {
              defaultValue:
                "They come back on {{local}} your time (00:00 UTC+8). Your idea stays in the box; press \"Start Writing\" then to pick it up. To write now, get Pro: no daily message limit.",
              local: resetLocal,
              day: resetDay ?? "",
            })
          : resetDay
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
      secondaryLabel={t("home:pricingTeaser.viewPricing")}
    />
  );
}

export default IdeaQuotaWallModal;
