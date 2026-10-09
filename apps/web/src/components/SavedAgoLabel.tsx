import { useTranslation } from "react-i18next";
import { useNow } from "../hooks/useNow";
import { getLocaleCode } from "../lib/i18n-helpers";

/** 「刚刚保存」 / 「25 秒前」 / 「3 分钟前」 / clock time, refreshed while the editor stays open. */
export function SavedAgoLabel({ savedAt }: { savedAt: Date }) {
  const { t } = useTranslation(["editor"]);
  const now = useNow(10_000);
  const seconds = Math.max(0, Math.floor((now - savedAt.getTime()) / 1000));

  if (seconds < 10) return <>{t("editor:savedJustNow")}</>;
  if (seconds < 60) return <>{t("editor:secondsAgo", { count: seconds })}</>;
  if (seconds < 3600) return <>{t("editor:minutesAgo", { count: Math.floor(seconds / 60) })}</>;
  return <>{savedAt.toLocaleTimeString(getLocaleCode(), { hour: "2-digit", minute: "2-digit" })}</>;
}
