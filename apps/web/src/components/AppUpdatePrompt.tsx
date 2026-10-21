import { useEffect, useRef, useSyncExternalStore } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { Button } from "./ui/Button";
import { dismissAppUpdate, getAppUpdateState, subscribeAppUpdate } from "../lib/appUpdate";

const isProjectPath = (pathname: string) => pathname.startsWith("/project/");

/**
 * Offers a reload once a newer build is deployed. Outside the project editor,
 * the next route change loads the new build as a full page navigation, so the
 * tab never asks the server for chunks of the build it replaced. Inside the
 * editor nothing reloads on its own; the author chooses when.
 */
export function AppUpdatePrompt() {
  const { t } = useTranslation("common", { useSuspense: false });
  const { available, required } = useSyncExternalStore(subscribeAppUpdate, getAppUpdateState);
  const { pathname } = useLocation();
  const previousPathname = useRef(pathname);

  useEffect(() => {
    const previous = previousPathname.current;
    previousPathname.current = pathname;
    if (previous === pathname || !getAppUpdateState().available) return;
    if (isProjectPath(previous) || isProjectPath(pathname)) return;
    window.location.reload();
  }, [pathname]);

  if (!available) return null;

  return (
    // Below 768px (the mobile workspace with bottom tabs) it sits above the tabs,
    // the iPhone home indicator and the toast stack; from 768px, bottom right.
    <div
      role={required ? "alert" : "status"}
      aria-live={required ? "assertive" : "polite"}
      className="fixed inset-x-4 bottom-[calc(8.5rem+env(safe-area-inset-bottom))] z-[1090] flex items-start gap-3 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-4 py-3 text-[hsl(var(--text-primary))] shadow-lg md:inset-x-auto md:bottom-4 md:right-4 md:w-96"
    >
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.13)] text-[hsl(var(--accent-primary))]">
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold leading-5">{t("appUpdate.title", "ZenStory 已更新")}</p>
        <p className="mt-0.5 text-sm leading-5 text-[hsl(var(--text-secondary))]">
          {required
            ? t("appUpdate.requiredDescription", "需要刷新页面才能继续加载。如有正在编辑的内容，建议先保存。")
            : t("appUpdate.description", "刷新页面即可使用新版本。")}
        </p>
        <div className="mt-2 flex gap-2">
          <Button size="touch" onClick={() => window.location.reload()}>
            {t("appUpdate.reload", "刷新")}
          </Button>
          {!required && (
            <Button variant="ghost" size="touch" onClick={dismissAppUpdate}>
              {t("appUpdate.later", "稍后")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
