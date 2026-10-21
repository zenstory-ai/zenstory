import { useRenewalReminder } from "../../hooks/useRenewalReminder";
import { RenewalReminderBanner } from "../subscription/RenewalReminderBanner";
import { AnnouncementBanner } from "./AnnouncementBanner";

/** The dashboard home shows at most one banner: an imminent Pro expiry outranks the site-wide announcement. */
export function DashboardBannerSlot({ accountId }: { accountId: string }) {
  const renewalReminder = useRenewalReminder(accountId);
  return renewalReminder
    ? <RenewalReminderBanner reminder={renewalReminder} />
    : <AnnouncementBanner accountId={accountId} />;
}
