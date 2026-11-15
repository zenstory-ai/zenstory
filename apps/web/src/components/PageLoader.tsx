import { useTranslation } from "react-i18next";
import { Loader2 } from "../components/icons";

/**
 * Loading 组件 - 用于路由懒加载时的 Suspense fallback
 * 提供简洁优雅的加载动画
 */
export function PageLoader() {
  const { t } = useTranslation(["common"]);

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <Loader2 className="w-8 h-8 text-[hsl(var(--accent-primary))] animate-spin" />
        <p className="text-sm text-[hsl(var(--text-secondary))]">{t("common:loading")}</p>
      </div>
    </div>
  );
}
