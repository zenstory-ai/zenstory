import i18n from "i18next";
import { Loader2 } from "../components/icons";

function resolveLoadingLabel(): string {
  if (i18n.isInitialized && i18n.hasLoadedNamespace("common")) {
    const translated = i18n.t("common:loading");
    if (translated && translated !== "loading" && translated !== "common:loading") {
      return translated;
    }
  }

  let storedLang = "";
  try {
    storedLang = window.localStorage.getItem("zenstory-language") ?? "";
  } catch {
    // Ignore storage access errors.
  }

  const rawLang =
    i18n.resolvedLanguage ||
    i18n.language ||
    storedLang ||
    (typeof document !== "undefined" ? document.documentElement.lang : "") ||
    "";

  return rawLang.trim().toLowerCase().startsWith("zh") ? "加载中…" : "Loading…";
}

/**
 * Loading 组件 - 用于路由懒加载与根级 Suspense fallback
 *
 * 刻意不调用 useTranslation()（避免在 locale bundle 尚未下载完时再次触发
 * Suspense 造成首屏白屏），已加载 common 命名空间时直接读取译文，否则按当前语言回退。
 */
export function PageLoader() {
  const label = resolveLoadingLabel();

  return (
    <div
      role="status"
      aria-live="polite"
      className="min-h-screen bg-[hsl(var(--bg-primary))] flex items-center justify-center"
    >
      <div className="flex flex-col items-center gap-4">
        <Loader2 aria-hidden="true" className="w-8 h-8 text-[hsl(var(--accent-primary))] animate-spin" />
        <p className="text-sm text-[hsl(var(--text-secondary))]">{label}</p>
      </div>
    </div>
  );
}
