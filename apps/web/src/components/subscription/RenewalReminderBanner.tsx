import { useEffect } from "react";
import { CalendarClock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { formatBeijingMonthDay } from "../../lib/dateUtils";
import { trackUpgradeClick, trackUpgradeExpose } from "../../lib/upgradeAnalytics";
import { buildUpgradeUrl } from "../../config/upgradeExperience";
import { useOnlineCheckoutKnownOff } from "../../hooks/useOnlineCheckoutKnownOff";
import type { RenewalReminder } from "../../hooks/useRenewalReminder";
import { DashboardBanner, dashboardBannerActionClassName } from "../announcements/DashboardBanner";

/** Attribution source carried to checkout and stored on the order's upgrade_source. */
const RENEWAL_REMINDER_SOURCE = "renewal_reminder";

export function RenewalReminderBanner({ reminder }: { reminder: RenewalReminder }) {
  const { t } = useTranslation("dashboard");
  const { stage, periodEnd, daysRemaining } = reminder;
  const date = formatBeijingMonthDay(periodEnd);
  // With online checkout off, ?plan=pro would open the legacy code activation dialog;
  // send authors to the plain billing page instead.
  const checkoutOff = useOnlineCheckoutKnownOff();
  const renewPath = checkoutOff ? "/dashboard/billing" : "/dashboard/billing?plan=pro";

  useEffect(() => {
    trackUpgradeExpose(RENEWAL_REMINDER_SOURCE, "card", { stage, days_remaining: daysRemaining });
  }, [stage, periodEnd, daysRemaining]);

  return (
    <DashboardBanner
      regionLabel={t("renewalReminder.regionLabel", "Pro 到期提醒")}
      icon={<CalendarClock className="h-4 w-4" aria-hidden="true" />}
      title={stage === "final"
        ? t("renewalReminder.titleFinal", "你的 Pro 快到期了")
        : t("renewalReminder.titleWeek", "你的 Pro {{date}}到期", { date })}
      description={stage === "final"
        ? t("renewalReminder.descriptionFinal", "{{date}}之后会回到免费版。现在续费，剩下的天数照样算。", { date })
        : t("renewalReminder.descriptionWeek", "现在续费不会浪费剩下的天数，新时长接在到期日之后。")}
      action={(
        <Link
          to={buildUpgradeUrl(renewPath, RENEWAL_REMINDER_SOURCE)}
          className={dashboardBannerActionClassName}
          onClick={() => {
            trackUpgradeClick(RENEWAL_REMINDER_SOURCE, "primary", "billing", "card");
            // Reminded authors have been buying monthly, so checkout opens on the monthly cycle.
            try {
              sessionStorage.setItem("payment_cycle_intent", "month");
            } catch {
              // Without storage the checkout keeps its usual default.
            }
          }}
        >
          {t("renewalReminder.action", "续费 Pro")}
        </Link>
      )}
      closeLabel={t("renewalReminder.close", "关闭提醒")}
      onClose={reminder.dismiss}
    />
  );
}
