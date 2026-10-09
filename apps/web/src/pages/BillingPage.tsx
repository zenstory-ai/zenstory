import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Crown, Mail, Sparkles, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { Button } from "../components/ui/Button";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { useIsMobile } from "../hooks/useMediaQuery";
import { RedeemCodeModal } from "../components/subscription/RedeemCodeModal";
import { PaymentCheckoutModal } from "../components/subscription/PaymentCheckoutModal";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import { paymentApi, paymentQueryKeys } from "../lib/paymentApi";
import {
  filterAvailableMetrics,
  formatYuan,
  getEntitlementMetricDefinitions,
  getLocalizedPlanDisplayName,
  getSubscriptionStatusLine,
  getYearlySavings,
} from "../lib/subscriptionEntitlements";
import type { SubscriptionCatalogTier } from "../types/subscription";
import type { QuotaResponse } from "../types/subscription";
import { getUpgradePromptDefinition } from "../config/upgradeExperience";
import { trackUpgradeClick, trackUpgradeConversion } from "../lib/upgradeAnalytics";
import { trackEvent } from "../lib/analytics";
import { inspirationsConfig } from "../config/inspirations";
import { SUPPORT_EMAIL, SUPPORT_MAILTO } from "../config/support";
import type { PaymentCycle } from "../types/payment";

type UsageKey =
  | "ai_conversations"
  | "projects"
  | "material_decompositions"
  | "skill_creates"
  | "inspiration_copies";

/** Attribution source for the paid header button, which renews rather than upgrades. */
const RENEW_SOURCE = "billing_header_renew";

export default function BillingPage() {
  const { t, i18n } = useTranslation(["dashboard", "settings", "common"]);
  const [searchParams] = useSearchParams();
  const trackedConversionSourceRef = useRef<string | null>(null);
  // Page-header actions: the standard 40px button, 44px touch target on phones.
  const headerActionSize = useIsMobile() ? "touch" : "md";
  const billingUpgradePrompt = getUpgradePromptDefinition("billing_header_upgrade");
  const [paymentCycle] = useState<PaymentCycle>(() => {
    // Yearly is the default offer; an explicit monthly choice on the pricing page wins.
    const storedCycle = sessionStorage.getItem("payment_cycle_intent");
    return storedCycle === "month" ? "month" : "year";
  });
  useEffect(() => {
    sessionStorage.removeItem("payment_cycle_intent");
  }, []);
  const [isProPlanIntentHandled, setIsProPlanIntentHandled] = useState(() => searchParams.get("plan") !== "pro");
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const [showRedeemCodeModal, setShowRedeemCodeModal] = useState(false);
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

  // Online checkout stays hidden until the server reports Zpay as configured;
  // until then Pro is activated with redeem codes, as before payments existed.
  const {
    data: paymentOptions,
    isLoading: isPaymentOptionsLoading,
    isError: isPaymentOptionsError,
  } = useQuery({
    queryKey: paymentQueryKeys.options(),
    queryFn: paymentApi.getOptions,
    retry: false,
  });
  const isCheckoutEnabled = paymentOptions?.enabled === true;
  // Only the server's explicit "off" means checkout is off. A failed options request is
  // unknown, not off: keep offering 开通 Pro (the checkout dialog asks again and explains
  // if paying online really is unavailable) instead of steering everyone to redeem codes.
  const isCheckoutKnownOff = paymentOptions?.enabled === false;
  const showCheckoutButton = isCheckoutEnabled || isPaymentOptionsError;
  // A ?plan=pro deep link opens whichever activation path is available once known.
  const isProPlanIntentReady = !isProPlanIntentHandled && !isPaymentOptionsLoading;
  const isPaymentModalOpen = showPaymentModal || (isProPlanIntentReady && showCheckoutButton);
  const isRedeemCodeModalOpen = showRedeemCodeModal || (isProPlanIntentReady && isCheckoutKnownOff);

  const usageItems = useMemo(
    () =>
      [
        { key: "ai_conversations", label: t("dashboard:billing.metricAiConversations", "每日 AI 消息") },
        { key: "projects", label: t("dashboard:billing.metricProjects", "项目数") },
        { key: "material_decompositions", label: t("dashboard:billing.metricMaterialDecompositions", "素材拆解") },
        { key: "skill_creates", label: t("dashboard:billing.metricCustomSkills", "自定义技能") },
        { key: "inspiration_copies", label: t("dashboard:billing.metricInspirationCopies", "复制灵感") },
      ].filter((item) => inspirationsConfig.enabled || item.key !== "inspiration_copies") as { key: UsageKey; label: string }[],
    [t]
  );

  const sortedPlans = useMemo(() => {
    return [...(catalog?.tiers ?? [])].sort((a, b) => a.price_monthly_cents - b.price_monthly_cents);
  }, [catalog?.tiers]);
  const proPlan = sortedPlans.find((plan) => plan.name === "pro");

  const metricDefinitions = useMemo(
    () => filterAvailableMetrics(getEntitlementMetricDefinitions(t, i18n.language), sortedPlans),
    [i18n.language, t, sortedPlans]
  );

  const formatPrice = (cents: number, cycle: "month" | "year"): string => {
    if (cents === 0) return t("dashboard:billing.free", "免费");
    const locale = i18n.language?.startsWith("en") ? "en-US" : "zh-CN";
    const amount = (cents / 100).toLocaleString(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    const unit = cycle === "month" ? t("dashboard:billing.perMonth", "/月") : t("dashboard:billing.perYear", "/年");
    return `¥${amount}${unit}`;
  };

  // Yearly is the offer we lead with: spell out the monthly equivalent and the
  // saving next to the two prices instead of leaving the maths to the reader.
  const formatPlanPrice = (plan: SubscriptionCatalogTier): string => {
    const monthly = formatPrice(plan.price_monthly_cents, "month");
    if (plan.price_monthly_cents === 0 && plan.price_yearly_cents === 0) return monthly;
    const yearly = formatPrice(plan.price_yearly_cents, "year");
    const savings = getYearlySavings(plan.price_monthly_cents, plan.price_yearly_cents);
    if (!savings) return `${monthly} · ${yearly}`;
    return t("dashboard:billing.priceWithYearlyOffer", "{{monthly}}，或 {{yearly}}（折合 {{equivalent}}/月，省 {{percent}}%）", {
      monthly,
      yearly,
      equivalent: formatYuan(savings.monthlyEquivalent, i18n.language),
      percent: savings.percent,
    });
  };

  // A limit of 0 means the plan does not include the feature; "0/0" with an
  // empty bar reads like something broke.
  const isNotIncluded = (metric?: QuotaResponse[UsageKey]) => metric?.limit === 0;

  const formatUsage = (metric?: QuotaResponse[UsageKey]) => {
    if (!metric) return "-";
    if (metric.limit === -1) return t("settings:subscription.unlimited", "不限");
    if (isNotIncluded(metric)) return t("dashboard:billing.availableWithPro", "Pro 可用");
    return `${metric.used}/${metric.limit}`;
  };

  const usageProgress = (metric?: QuotaResponse[UsageKey]) => {
    if (!metric || metric.limit <= 0 || metric.limit === -1) return 0;
    return Math.min(100, Math.round((metric.used / metric.limit) * 100));
  };

  const isLoading = isStatusLoading || isCatalogLoading || isQuotaLoading;
  const isCatalogPendingState = isCatalogLoading || (isCatalogFetching && sortedPlans.length === 0);
  const hasError = isStatusError || isCatalogError || isQuotaError;
  // Until the status query resolves we do not know which tier the user is on;
  // never claim "renew" for someone who may be on the free plan.
  const isUpgradableTier = status?.tier === "free";
  const isPaidTier = Boolean(status?.tier) && !isUpgradableTier;
  // A paid author's header button renews; keep it apart from upgrades in attribution.
  const checkoutSource = isPaidTier && !attributionSource ? RENEW_SOURCE : effectiveUpgradeSource;
  const statusLine = status ? getSubscriptionStatusLine(status, t) : null;
  // Online checkout off (isCheckoutKnownOff, above): "开通 Pro" would only open the redeem
  // dialog, so say so and offer the redeem code itself.
  const subtitle = isUpgradableTier
    ? isCheckoutKnownOff
      ? t("dashboard:billing.subtitleRedeemOnly", "查看当前套餐和用量，需要更多额度时可以用兑换码开通 Pro。")
      : t("dashboard:billing.subtitle", "查看当前套餐和用量，需要更多额度时可升级或使用兑换码。")
    : isPaidTier
    ? isCheckoutKnownOff
      ? t("dashboard:billing.subtitlePaidRedeemOnly", "查看当前套餐和用量，到期前可以用兑换码续期。")
      : t("dashboard:billing.subtitlePaid", "查看当前套餐和用量，到期前可以续费 Pro 或使用兑换码。")
    : t("dashboard:billing.subtitleNeutral", "查看当前套餐和用量。");

  return (
    <div className="space-y-6">
      <DashboardPageHeader
        title={t("dashboard:billing.title", "订阅权益")}
        subtitle={subtitle}
        action={
          <div className="flex items-center gap-2">
            {showCheckoutButton ? (
              <Button
                size={headerActionSize}
                onClick={() => {
                  trackUpgradeClick(
                    checkoutSource,
                    "direct",
                    "checkout",
                    "page"
                  );
                  setShowPaymentModal(true);
                }}
              >
                {isUpgradableTier
                  ? t("dashboard:billing.ctaBuyPro", "开通 Pro")
                  : isPaidTier
                  ? t("dashboard:billing.ctaRenewPro", "续费 Pro")
                  : t("dashboard:billing.ctaProNeutral", "开通或续费 Pro")}
              </Button>
            ) : null}
            <Button
              size={headerActionSize}
              // The only way to get Pro while checkout is off: make it the main button.
              variant={isCheckoutKnownOff && isUpgradableTier ? "primary" : "secondary"}
              onClick={() => {
                if (isCheckoutKnownOff && isUpgradableTier) {
                  trackUpgradeClick(effectiveUpgradeSource, "direct", "redeem", "page");
                }
                setShowRedeemCodeModal(true);
              }}
            >
              {t("settings:subscription.redeemCode", "兑换码")}
            </Button>
          </div>
        }
      />

      {isCheckoutKnownOff && isUpgradableTier && (
        <p
          data-testid="billing-checkout-unavailable"
          className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-sm text-[hsl(var(--text-secondary))]"
        >
          {t("dashboard:billing.checkoutUnavailableNotice", "暂时不能在线付款。有兑换码的话，点「兑换码」就能开通 Pro。")}
        </p>
      )}

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
                        tier: status.tier,
                      },
                      i18n.language
                    )
                  : t("dashboard:billing.unknownPlan", "未开通")}
              </Badge>
              {statusLine && (
                <span className="text-xs text-[hsl(var(--text-secondary))]">
                  {statusLine}
                </span>
              )}
            </div>
          </div>
        </div>
      </Card>

      <Card variant="outlined" padding="lg">
        <div className="flex items-center gap-2 mb-4">
          <Sparkles className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t("dashboard:billing.usageTitle", "当前用量")}
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
              const isLimited = Boolean(metric && metric.limit !== -1 && metric.limit > 0);
              // Same states as the header badge: used up is an error, close to it a warning.
              const isUsedUp = isLimited && Boolean(metric && metric.used >= metric.limit);
              const isWarning = isLimited && !isUsedUp && progress >= 80;
              const usageTextClass = isUsedUp
                ? "text-[hsl(var(--error))]"
                : isWarning
                ? "text-[hsl(var(--warning))]"
                : "text-[hsl(var(--text-primary))]";
              const usageBarClass = isUsedUp
                ? "bg-[hsl(var(--error))]"
                : isWarning
                ? "bg-[hsl(var(--warning))]"
                : "bg-[hsl(var(--accent-primary))]";
              const notIncluded = isNotIncluded(metric);
              return (
                <div key={item.key} className="rounded-lg border border-[hsl(var(--border-color))] p-3">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-[hsl(var(--text-secondary))]">{item.label}</span>
                    <span
                      data-testid={`billing-usage-${item.key}`}
                      className={`font-semibold ${usageTextClass}`}
                    >
                      {formatUsage(metric)}
                    </span>
                  </div>
                  {metric?.limit !== -1 && !notIncluded && (
                    <div className="mt-2 h-1.5 rounded-full bg-[hsl(var(--bg-tertiary))] overflow-hidden">
                      <div
                        data-testid={`billing-usage-bar-${item.key}`}
                        className={`h-full ${usageBarClass}`}
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  )}
                  {item.key === "ai_conversations" && (
                    <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
                      {isUpgradableTier
                        ? t(
                            "dashboard:billing.freeDailyMessageLimitHint",
                            "免费用户每日最多 {{limit}} 条 AI 消息，北京时间次日 00:00 恢复。",
                            { limit: metric?.limit ?? 0 }
                          )
                        : metric?.limit === -1
                        ? t("dashboard:billing.proNoDailyLimit", "Pro 每天的 AI 消息不限条数。")
                        : t("dashboard:billing.dailyQuotaResetHint", "每天的 AI 消息在北京时间 00:00 恢复。")}
                    </p>
                  )}
                  {(item.key === "material_decompositions" || item.key === "inspiration_copies") && !notIncluded && (
                    <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
                      {t("dashboard:billing.monthlyQuotaResetHint", "每月额度于北京时间每月 1 日 00:00 重置。")}
                    </p>
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
            {t("dashboard:billing.compareTitle", "选一个适合你的方案")}
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
                      <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {getLocalizedPlanDisplayName({ ...plan, tier: plan.name }, i18n.language)}
                      </h3>
                      <div className="text-xs text-[hsl(var(--text-secondary))] mt-0.5">
                        {formatPlanPrice(plan)}
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
                    {metricDefinitions.map((metric) => {
                      // A limit of 0 means the plan does not include the feature
                      // (shown as 「不含」): a neutral cross, not a green check.
                      const included = metric.compareValue(plan) !== 0;
                      return (
                        <div
                          key={metric.key}
                          className="flex items-center justify-between text-sm gap-3"
                          data-testid={`billing-plan-metric-${metric.key}`}
                          data-included={included}
                        >
                          <div className="flex items-center gap-1.5 text-[hsl(var(--text-secondary))]">
                            {included ? (
                              <Check className="w-3.5 h-3.5 text-[hsl(var(--success))]" aria-hidden="true" />
                            ) : (
                              <X className="w-3.5 h-3.5 text-[hsl(var(--text-tertiary))]" aria-hidden="true" />
                            )}
                            <span>{metric.label}</span>
                          </div>
                          <span
                            className={`font-medium ${
                              included
                                ? "text-[hsl(var(--text-primary))]"
                                : "text-[hsl(var(--text-tertiary))]"
                            }`}
                          >
                            {metric.value(plan)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <p
        className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-[hsl(var(--text-secondary))]"
        data-testid="billing-support-email"
      >
        <Mail size={16} className="shrink-0" aria-hidden="true" />
        <span>{t("dashboard:billing.supportHint", "订阅、扣费或退款有疑问？请联系客服：")}</span>
        <a
          href={SUPPORT_MAILTO}
          className="text-[hsl(var(--accent-primary))] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] rounded"
        >
          {SUPPORT_EMAIL}
        </a>
      </p>

      <RedeemCodeModal
        isOpen={isRedeemCodeModalOpen}
        onClose={() => {
          setShowRedeemCodeModal(false);
          setIsProPlanIntentHandled(true);
        }}
        source={effectiveUpgradeSource}
      />
      {isPaymentModalOpen && (
        <PaymentCheckoutModal
          isOpen
          onClose={() => {
            setShowPaymentModal(false);
            setIsProPlanIntentHandled(true);
          }}
          initialCycle={paymentCycle}
          monthlyPriceCents={proPlan?.price_monthly_cents}
          yearlyPriceCents={proPlan?.price_yearly_cents}
          upgradeSource={checkoutSource}
          isRenewal={isPaidTier}
          redeemEntry="on-page"
        />
      )}
    </div>
  );
}
