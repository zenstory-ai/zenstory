import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Megaphone } from "lucide-react";
import { useTranslation } from "react-i18next";
import { announcementApi } from "../../lib/announcementApi";
import { safeAnnouncementUrl } from "../../lib/announcementLinks";
import { DashboardBanner, dashboardBannerActionClassName } from "./DashboardBanner";

interface AnnouncementBannerProps {
  accountId: string;
}

function dismissalKey(accountId: string, version: number): string {
  return `zenstory:announcement-dismissed:${encodeURIComponent(accountId)}:${version}`;
}

export function AnnouncementBanner({ accountId }: AnnouncementBannerProps) {
  const { t } = useTranslation("announcements");
  const [dismissedInSession, setDismissedInSession] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["announcement", "current", accountId],
    queryFn: announcementApi.getCurrent,
    retry: false,
    staleTime: 60_000,
    refetchOnMount: "always",
  });

  const announcement = query.data;
  if (!announcement) return null;

  const key = dismissalKey(accountId, announcement.version);
  let dismissedPersistently = false;
  try {
    dismissedPersistently = localStorage.getItem(key) === "1";
  } catch {
    // Storage can be unavailable; the in-memory dismissal below still works.
  }
  if (dismissedPersistently || dismissedInSession === key) return null;

  const actionUrl = safeAnnouncementUrl(announcement.action_url);
  const dismiss = () => {
    setDismissedInSession(key);
    try {
      localStorage.setItem(key, "1");
    } catch {
      // Do not claim persistence when the browser rejects storage.
    }
  };

  return (
    <DashboardBanner
      regionLabel={t("banner.regionLabel", "站内公告")}
      icon={<Megaphone className="h-4 w-4" aria-hidden="true" />}
      title={announcement.title}
      description={announcement.description}
      action={actionUrl && announcement.action_label && (
        <a href={actionUrl} target="_blank" rel="noopener noreferrer" className={dashboardBannerActionClassName}>
          {announcement.action_label}
        </a>
      )}
      closeLabel={t("banner.close", "关闭公告")}
      onClose={dismiss}
    />
  );
}
