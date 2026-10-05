import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Crown, Sparkles } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { RedeemCodeModal } from "../components/subscription/RedeemCodeModal";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import {
  getEntitlementMetricDefinitions,
  getLocalizedPlanDisplayName,
} from "../lib/subscriptionEntitlements";
import type { QuotaResponse } from "../types/subscription";
import { getUpgradePromptDefinition } from "../config/upgradeExperience";
import { trackUpgradeClick, trackUpgradeConversion } from "../lib/upgradeAnalytics";
import { trackEvent } from "../lib/analytics";
import { inspirationsConfig } from "../config/inspirations";

type UsageKey =
  | "ai_conversations"
  | "projects"
  | "material_decompositions"
  | "skill_creates"
  | "inspiration_copies";

export default function BillingPage() {
  const { t, i18n } = useTranslation(["dashboard", "settings", "common"]);
  const [searchParams] = useSearchParams();
  const trackedConversionSourceRef = useRef<string | null>(null);
  const billingUpgradePrompt = getUpgradePromptDefinition("billing_header_upgrade");
  const [showRedeemCodeModal, setShowRedeemCodeModal] = useState(() => searchParams.get("plan") === "pro");
  const attributionSource = useMemo(() => {
    const rawSource = searchParams.get("source");
    if (!rawSource) {
      return undefined;
    }
    const trimmedSource = rawSource.trim();
    return trimmedSource.length > 0 ? trimmedSource : undefined;
  }, [searchParams]);
  const effectiveUpgradeSource = attributionSource ?? billingUpgradePrompt.source;

  useEffect(() => {
    if (!attributionSource || trackedConversionSourceRef.current === attributionSource) return;
    trackUpgradeConversion(attributionSource, "billing");
    trackedConversionSourceRef.current = attributionSource;
  }, [attributionSource]);

  useEffect(() => {
    trackEvent("billing_page_view", {
      attribution_source: attributionSource,
      effective_upgrade_source: effectiveUpgradeSource,
    });
  }, [attributionSource, effectiveUpgradeSource]);

  const {
    data: status,
    isLoading: isStatusLoading,
    isError: isStatusError,
    refetch: refetchStatus,
  } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
  });

  const {
    data: catalog,
    isLoading: isCatalogLoading,
    isFetching: isCatalogFetching,
    isError: isCatalogError,
    refetch: refetchCatalog,
  } = useQuery({
    queryKey: ["public-subscription-catalog"],
    queryFn: () => subscriptionApi.getCatalog(),
  });

  const {
    data: quota,
    isLoading: isQuotaLoading,
    isError: isQuotaError,
    refetch: refetchQuota,
  } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
  });

  const usageItems = useMemo(
    () =>
      [
        { key: "ai_conversations", label: t("settings:subscription.features.ai_conversations_per_day", "每日 AI 对话次数") },
        { key: "projects", label: t("settings:subscription.features.max_projects", "最大项目数") },
        { key: "material_decompositions", label: t("settings:subscription.features.material_decompositions", "素材拆解次数") },
        { key: "skill_creates", label: t("settings:subscription.features.custom_skills", "自定义技能数量") },
        { key: "inspiration_copies", label: t("settings:subscription.features.inspiration_copies_monthly", "灵感复用次数") },
      ].filter((item) => inspirationsConfig.enabled || item.key !== "inspiration_copies") as { key: UsageKey; label: string }[],
    [t]
  );

  const sortedPlans = useMemo(() => {
    return [...(catalog?.tiers ?? [])].sort((a, b) => a.price_monthly_cents - b.price_monthly_cents);
  }, [catalog?.tiers]);

  const metricDefinitions = useMemo(
    () => getEntitlementMetricDefinitions(t, i18n.language),
    [i18n.language, t]
  );

  const formatPrice = (cents: number, cycle: "month" | "year"): string => {
    if (cents === 0) return t("dashboard:billing.free", "免费");
    const locale = i18n.language?.startsWith("en") ? "en-US" : "zh-CN";
    const amount = (cents / 100).toLocaleString(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    const unit = cycle === "month" ? t("dashboard:billing.perMonth", "/月") : t("dashboard:billing.perYear", "/年");
    return `¥${amount}${unit}`;
  };

  const formatUsage = (metric?: QuotaResponse[UsageKey]) => {
    if (!metric) return "-";
    if (metric.limit === -1) return t("settings:subscription.unlimited", "无限");
    return `${metric.used}/${metric.limit}`;
  };

  const usageProgress = (metric?: QuotaResponse[UsageKey]) => {
    if (!metric || metric.limit <= 0 || metric.limit === -1) return 0;
    return Math.min(100, Math.round((metric.used / metric.limit) * 100));
  };

  const isLoading = isStatusLoading || isCatalogLoading || isQuotaLoading;
  const isCatalogPendingState = isCatalogLoading || (isCatalogFetching && sortedPlans.length === 0);
  const hasError = isStatusError || isCatalogError || isQuotaError;
  const isUpgradableTier = status?.tier === "free";

  return (
    <div className="space-y-6">
      <DashboardPageHeader
        title={t("dashboard:billing.title", "订阅与权益")}
        subtitle={t("dashboard:billing.subtitle", "查看套餐权益、配额使用情况并快速升级")}
        action={
          <div className="flex items-center gap-2">
            {isUpgradableTier && (
              <Button
                size="sm"
                onClick={() => {
                  trackUpgradeClick(
                    effectiveUpgradeSource,
                    "direct",
                    "redeem",
                    "page"
                  );
                  setShowRedeemCodeModal(true);
                }}
              >
                {t("dashboard:billing.ctaUpgradePro", "升级专业版")}
              </Button>
            )}
            <Button size="sm" variant="secondary" onClick={() => setShowRedeemCodeModal(true)}>
              {t("settings:subscription.redeemCode", "兑换码")}
            </Button>
          </div>
        }
      />

      <Card variant="outlined" padding="lg">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm text-[hsl(var(--text-secondary))]">
              {t("dashboard:billing.currentPlan", "当前套餐")}
            </div>
            <div className="mt-1 flex items-center gap-2">
              <Badge variant={status?.tier === "pro" ? "purple" : "neutral"} size="md">
                {status
                  ? getLocalizedPlanDisplayName(
                      {
                        display_name: status.display_name,
                        display_name_en: status.display_name_en,
                      },
                      i18n.language
                    )
                  : t("dashboard:billing.unknownPlan", "未开通")}
              </Badge>
              {status?.status && (
                <span className="text-xs text-[hsl(var(--text-secondary))]">
                  {t(`settings:subscription.${status.status}`, status.status)}
                </span>
              )}
            </div>
            {isUpgradableTier && (
              <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
                {t("dashboard:billing.unlockHint", "点击“升级专业版”可直接兑换开通，无需再跳转套餐页。")}
              </p>
            )}
          </div>
        </div>
      </Card>

      <Card variant="outlined" padding="lg">
        <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
          {t("dashboard:billing.activationTitle", "如何开通专业版")}
        </h2>
        <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
          {t("dashboard:billing.activationGuide", "专业版目前通过兑换码开通，暂不支持在线支付。已有兑换码可直接兑换；还没有兑换码，请联系下方微信咨询套餐与获取方式。")}
        </p>
        <p className="mt-2 text-sm text-[hsl(var(--text-secondary))]">
          {t("settings:subscription.wechatGuide", "没有兑换码？可添加微信号获取：AIchuangzuo999")}
        </p>
      </Card>

      <Card variant="outlined" padding="lg">
        <div className="flex items-center gap-2 mb-4">
          <Sparkles className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t("dashboard:billing.usageTitle", "当前配额使用")}
          </h2>
        </div>
        {hasError && (
          <div className="mb-4 rounded-lg border border-[hsl(var(--error)/0.35)] bg-[hsl(var(--error)/0.08)] p-3 flex items-center justify-between gap-3">
            <p className="text-sm text-[hsl(var(--error))]">{t("common:error", "加载失败")}</p>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                void refetchStatus();
                void refetchCatalog();
                void refetchQuota();
              }}
            >
              {t("common:retry", "重试")}
            </Button>
          </div>
        )}
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-20 animate-pulse rounded-lg bg-[hsl(var(--bg-tertiary))]" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {usageItems.map((item) => {
              const metric = quota?.[item.key];
              const progress = usageProgress(metric);
              const isWarning = metric && metric.limit !== -1 && metric.limit > 0 && progress >= 80;
              return (
                <div key={item.key} className="rounded-lg border border-[hsl(var(--border-color))] p-3">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-[hsl(var(--text-secondary))]">{item.label}</span>
                    <span className={`font-semibold ${isWarning ? "text-[hsl(var(--warning))]" : "text-[hsl(var(--text-primary))]"}`}>
                      {formatUsage(metric)}
                    </span>
                  </div>
                  {metric?.limit !== -1 && (
                    <div className="mt-2 h-1.5 rounded-full bg-[hsl(var(--bg-tertiary))] overflow-hidden">
                      <div
                        className={`h-full ${isWarning ? "bg-[hsl(var(--warning))]" : "bg-[hsl(var(--accent-primary))]"}`}
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card variant="outlined" padding="lg">
        <div className="flex items-center gap-2 mb-4">
          <Crown className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t("dashboard:billing.compareTitle", "套餐权益对比")}
          </h2>
        </div>
        {isCatalogPendingState ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {Array.from({ length: 2 }).map((_, index) => (
              <div key={index} className="h-56 animate-pulse rounded-xl bg-[hsl(var(--bg-tertiary))]" />
            ))}
          </div>
        ) : sortedPlans.length === 0 ? (
          <div className="text-sm text-[hsl(var(--text-secondary))] py-2">
            {t("common:noData", "暂无数据")}
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {sortedPlans.map((plan) => {
              const isCurrent = status?.tier === plan.name;
              const isFreePlan = plan.price_monthly_cents === 0 && plan.price_yearly_cents === 0;
              return (
                <div
                  key={plan.id}
                  className={`rounded-xl border p-4 ${
                    isCurrent
                      ? "border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.06)]"
                      : "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div>
                      <div className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                        {getLocalizedPlanDisplayName(plan, i18n.language)}
                      </div>
                      <div className="text-xs text-[hsl(var(--text-secondary))] mt-0.5">
                        {formatPrice(plan.price_monthly_cents, "month")}
                        {!isFreePlan && <> · {formatPrice(plan.price_yearly_cents, "year")}</>}
                      </div>
                    </div>
                    {isCurrent ? (
                      <Badge variant="info">
                        {t("dashboard:billing.current", "当前")}
                      </Badge>
                    ) : plan.recommended ? (
                      <Badge variant="purple">
                        {t("dashboard:billing.recommended", "推荐")}
                      </Badge>
                    ) : null}
                  </div>

                  <div className="space-y-2">
                    {metricDefinitions.map((metric) => (
                      <div key={metric.key} className="flex items-center justify-between text-sm gap-3">
                        <div className="flex items-center gap-1.5 text-[hsl(var(--text-secondary))]">
                          <Check className="w-3.5 h-3.5 text-[hsl(var(--success))]" />
                          <span>{metric.label}</span>
                        </div>
                        <span className="font-medium text-[hsl(var(--text-primary))]">
                          {metric.value(plan)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <RedeemCodeModal
        isOpen={showRedeemCodeModal}
        onClose={() => setShowRedeemCodeModal(false)}
        source={effectiveUpgradeSource}
      />
    </div>
  );
}
