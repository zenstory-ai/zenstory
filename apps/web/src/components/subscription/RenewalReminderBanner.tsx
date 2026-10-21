import { useEffect } from "react";
import { CalendarClock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { formatBeijingPeriodDate } from "../../lib/dateUtils";
import { trackUpgradeClick, trackUpgradeExpose } from "../../lib/upgradeAnalytics";
import { buildUpgradeUrl } from "../../config/upgradeExperience";
import type { RenewalReminder } from "../../hooks/useRenewalReminder";
import { DashboardBanner, dashboardBannerActionClassName } from "../announcements/DashboardBanner";

/** Attribution source carried to checkout and stored on the order's upgrade_source. */
const RENEWAL_REMINDER_SOURCE = "renewal_reminder";

export function RenewalReminderBanner({ reminder }: { reminder: RenewalReminder }) {
  const { t } = useTranslation("dashboard");
  const { stage, periodEnd, daysRemaining } = reminder;
  const date = formatBeijingPeriodDate(periodEnd);

  useEffect(() => {
    trackUpgradeExpose(RENEWAL_REMINDER_SOURCE, "card", { stage, days_remaining: daysRemaining });
  }, [stage, periodEnd, daysRemaining]);

  return (
    <DashboardBanner
      regionLabel={t("renewalReminder.regionLabel", "Pro 到期提醒")}
      icon={<CalendarClock className="h-4 w-4" aria-hidden="true" />}
      title={stage === "final"
        ? t("renewalReminder.titleFinal", "Pro 即将到期，{{date}} 后回到免费版", { date })
        : t("renewalReminder.titleWeek", "Pro 将于 {{date}} 到期", { date })}
      description={stage === "final"
        ? t("renewalReminder.descriptionFinal", "现在续费，剩余天数照样保留，新的时长从原到期日起顺延。")
        : t("renewalReminder.descriptionWeek", "提前续费不会浪费剩余天数，新的时长从原到期日起顺延。")}
      action={(
        <Link
          to={buildUpgradeUrl("/dashboard/billing?plan=pro", RENEWAL_REMINDER_SOURCE)}
          className={dashboardBannerActionClassName}
          onClick={() => trackUpgradeClick(RENEWAL_REMINDER_SOURCE, "primary", "billing", "card")}
        >
          {t("renewalReminder.action", "续费 Pro")}
        </Link>
      )}
      closeLabel={t("renewalReminder.close", "关闭提醒")}
      onClose={reminder.dismiss}
    />
  );
}
