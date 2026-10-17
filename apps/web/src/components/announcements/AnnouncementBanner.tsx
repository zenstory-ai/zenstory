import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Megaphone, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { announcementApi } from "../../lib/announcementApi";
import { IconButton } from "../ui/IconButton";
import { safeAnnouncementUrl } from "../../lib/announcementLinks";

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
    <section
      className="mb-5 flex items-start gap-3 rounded-2xl border border-[hsl(var(--accent-primary)/0.22)] bg-[hsl(var(--accent-primary)/0.07)] px-4 py-3.5 text-[hsl(var(--text-primary))] sm:items-center"
      aria-label={t("banner.regionLabel", "站内公告")}
    >
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.13)] text-[hsl(var(--accent-primary))] sm:mt-0">
        <Megaphone className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1 sm:flex sm:items-center sm:gap-4">
        <div className="min-w-0 flex-1 break-words">
          <p className="text-sm font-semibold leading-5">{announcement.title}</p>
          <p className="mt-0.5 text-sm leading-5 text-[hsl(var(--text-secondary))]">{announcement.description}</p>
        </div>
        {actionUrl && announcement.action_label && (
          <a
            href={actionUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-flex min-h-11 shrink-0 items-center rounded-xl border border-[hsl(var(--accent-primary)/0.35)] px-3 text-sm font-medium text-[hsl(var(--accent-primary))] transition-colors hover:bg-[hsl(var(--accent-primary)/0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] sm:mt-0"
          >
            {announcement.action_label}
          </a>
        )}
      </div>
      <IconButton
        className="-mr-2 -mt-1 shrink-0 sm:-mt-0.5"
        label={t("banner.close", "关闭公告")}
        icon={<X className="h-5 w-5" />}
        size="touch"
        onClick={dismiss}
      />
    </section>
  );
}
