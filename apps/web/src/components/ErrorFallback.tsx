import { useTranslation } from "react-i18next";

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
}: {
  variant: "page" | "panel";
  onRetry: () => void;
}) {
  const { t } = useTranslation("common", { useSuspense: false });

  const title = t("errorBoundary.title", "Something went wrong");
  const description = variant === "page"
    ? t("errorBoundary.pageDescription", "The page hit an unexpected error. Reload to continue; your saved work is not affected.")
    : t("errorBoundary.panelDescription", "This panel hit an unexpected error. Other panels keep working.");

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
        {variant === "panel" && (
          <button type="button" onClick={onRetry} className="btn btn-ghost">
            {t("errorBoundary.retry", "Try again")}
          </button>
        )}
        <button type="button" onClick={reloadPage} className="btn btn-primary">
          {t("errorBoundary.reload", "Reload page")}
        </button>
      </div>
    </div>
  );
}
