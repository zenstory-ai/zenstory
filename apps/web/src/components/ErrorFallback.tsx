import { useTranslation } from "react-i18next";
import { Button } from "./ui/Button";

function reloadPage(): void {
  window.location.reload();
}

/**
 * Render-error fallback. Translations are loaded without suspending, with
 * built-in defaults, because this may render before i18n resources arrive
 * and outside any Suspense boundary.
 */
export function ErrorFallback({
  variant,
  onRetry,
  staleBuild = false,
}: {
  variant: "page" | "panel";
  onRetry: () => void;
  /**
   * The error is code from a replaced build failing to load. Re-rendering would
   * reuse the failed import, so only a page reload helps.
   */
  staleBuild?: boolean;
}) {
  const { t } = useTranslation("common", { useSuspense: false });

  const title = staleBuild ? t("appUpdate.title", "ZenStory 已更新") : t("errorBoundary.title", "出了点问题");
  const description = staleBuild
    ? t("appUpdate.requiredDescription", "需要刷新页面才能继续加载。如有正在编辑的内容，建议先保存。")
    : variant === "page"
      ? t("errorBoundary.pageDescription", "页面遇到意外错误。重新加载即可继续，已保存的内容不受影响。")
      : t("errorBoundary.panelDescription", "这个面板遇到意外错误，其他面板仍可正常使用。");

  return (
    <div
      role="alert"
      className={`flex flex-col items-center justify-center gap-3 p-6 text-center bg-[hsl(var(--bg-primary))] ${
        variant === "page" ? "min-h-screen" : "h-full"
      }`}
    >
      <p className="text-base font-medium text-[hsl(var(--text-primary))]">{title}</p>
      <p className="max-w-sm text-sm text-[hsl(var(--text-secondary))]">{description}</p>
      <div className="flex gap-2">
        {variant === "panel" && !staleBuild && (
          <Button variant="secondary" size="touch" onClick={onRetry}>
            {t("errorBoundary.retry", "重试")}
          </Button>
        )}
        <Button size="touch" onClick={reloadPage}>
          {t("errorBoundary.reload", "重新加载页面")}
        </Button>
      </div>
    </div>
  );
}
