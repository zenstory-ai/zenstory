import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Check, Minus, RefreshCw, ShieldCheck } from "lucide-react";
import { PublicHeader } from "../components/PublicHeader";
import { Card } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import { cn } from "../lib/utils";
import {
  filterAvailableMetrics,
  formatUsd,
  formatYuan,
  getEntitlementMetricDefinitions,
  getLocalizedPlanDisplayName,
  getYearlySavings,
  toComparableMetricValue,
} from "../lib/subscriptionEntitlements";
import { buildUpgradeUrl } from "../config/upgradeExperience";
import { PRO_USD_PRICE_CENTS, usesUsdPricing } from "../config/usdPricing";
import { useAuth } from "../contexts/AuthContext";
import { authConfig } from "../config/auth";
import { trackUpgradeConversion } from "../lib/upgradeAnalytics";
import { trackEvent } from "../lib/analytics";
import { PaymentCheckoutModal } from "../components/subscription/PaymentCheckoutModal";
import { parsePaymentCycleIntent } from "../lib/paymentCycleIntent";

type BillingCycle = "month" | "year";

export default function PricingPage() {
  const { t, i18n } = useTranslation(["home", "dashboard", "settings", "common"]);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const trackedConversionSourceRef = useRef<string | null>(null);
  const trackedPageViewKeyRef = useRef<string | null>(null);
  const { user, loading } = useAuth();
  const userId = user?.id ?? null;
  const isAuthenticated = Boolean(userId);
  const [billingCycle, setBillingCycle] = useState<BillingCycle>(
    () => parsePaymentCycleIntent(searchParams.get("cycle")) ?? "month"
  );
  const [showOnlyDifferences, setShowOnlyDifferences] = useState(false);
  const [showPaymentModal, setShowPaymentModal] = useState(false);
  const attributionSource = useMemo(() => {
    const rawSource = searchParams.get("source");
    if (!rawSource) {
      return undefined;
    }
    const trimmedSource = rawSource.trim();
    return trimmedSource.length > 0 ? trimmedSource : undefined;
  }, [searchParams]);
  const withAttributionSource = (path: string): string =>
    attributionSource ? buildUpgradeUrl(path, attributionSource) : path;

  useEffect(() => {
    if (!attributionSource || trackedConversionSourceRef.current === attributionSource) return;
    trackUpgradeConversion(attributionSource, "pricing");
    trackedConversionSourceRef.current = attributionSource;
  }, [attributionSource]);

  useEffect(() => {
    if (loading) return;

    const trackingKey = `${attributionSource ?? ""}:${userId ?? "anonymous"}`;
    if (trackedPageViewKeyRef.current === trackingKey) return;

    trackEvent("pricing_page_view", {
      attribution_source: attributionSource,
      is_authenticated: isAuthenticated,
    });
    trackedPageViewKeyRef.current = trackingKey;
  }, [attributionSource, isAuthenticated, loading, userId]);

  const { data: catalog, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["public-subscription-catalog"],
    queryFn: () => subscriptionApi.getCatalog(),
    staleTime: 60 * 1000,
  });

  const sortedPlans = useMemo(
    () => [...(catalog?.tiers ?? [])].sort((a, b) => a.price_monthly_cents - b.price_monthly_cents),
    [catalog?.tiers]
  );
  const isCatalogLoading = isLoading || (isFetching && sortedPlans.length === 0);
  const proPlan = sortedPlans.find((plan) => plan.name === "pro");

  // Signed-in Pro users extend their plan here, so the actions say "renew".
  const { data: subscriptionStatus } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
    enabled: isAuthenticated,
  });
  const isProUser = isAuthenticated && subscriptionStatus?.tier === "pro";
  const upgradeCtaLabel = isProUser
    ? t("dashboard:billing.ctaRenewPro", "续费 Pro")
    : t("dashboard:billing.ctaUpgradePro", "开通 Pro");

  const showUsdPricing = usesUsdPricing(i18n.language);
  const getDisplayPrices = (plan: (typeof sortedPlans)[number]) =>
    showUsdPricing && plan.name === "pro"
      ? { monthly: PRO_USD_PRICE_CENTS.month, yearly: PRO_USD_PRICE_CENTS.year, format: formatUsd }
      : { monthly: plan.price_monthly_cents, yearly: plan.price_yearly_cents, format: formatYuan };

  const formatPrice = (plan: (typeof sortedPlans)[number], cycle: BillingCycle): string => {
    const prices = getDisplayPrices(plan);
    const cents = cycle === "month" ? prices.monthly : prices.yearly;
    if (cents === 0) return t("dashboard:billing.free", "免费");
    return cycle === "month"
      ? `${prices.format(cents, i18n.language)}${t("dashboard:billing.perMonth", "/月")}`
      : `${prices.format(cents, i18n.language)}${t("dashboard:billing.perYear", "/年")}`;
  };

  const metricDefinitions = useMemo(
    () => filterAvailableMetrics(getEntitlementMetricDefinitions(t, i18n.language), sortedPlans),
    [i18n.language, t, sortedPlans]
  );

  // Only Pro is paid, so the plan with the largest yearly saving is the offer.
  const bestYearlySavings = sortedPlans.reduce<ReturnType<typeof getYearlySavings>>((best, plan) => {
    const prices = getDisplayPrices(plan);
    const savings = getYearlySavings(prices.monthly, prices.yearly);
    return savings && (!best || savings.percent > best.percent) ? savings : best;
  }, null);

  const handlePrimaryCta = () => {
    const destination = user
      ? "/dashboard"
      : authConfig.registrationEnabled
        ? "/register"
        : "/login";
    trackEvent("pricing_primary_cta_clicked", {
      attribution_source: attributionSource,
      destination,
      is_authenticated: Boolean(user),
    });
    if (user) {
      navigate(withAttributionSource("/dashboard"));
      return;
    }
    if (authConfig.registrationEnabled) {
      navigate(withAttributionSource("/register"));
      return;
    }
    navigate(withAttributionSource("/login"));
  };

  const handleUpgradeCta = () => {
    const destination = user
      ? "/dashboard/billing?plan=pro"
      : authConfig.registrationEnabled
        ? "/register?plan=pro"
        : "/login?plan=pro";
    trackEvent("pricing_upgrade_cta_clicked", {
      attribution_source: attributionSource,
      destination,
      is_authenticated: Boolean(user),
    });
    if (user) {
      setShowPaymentModal(true);
      return;
    }
    // Persist only this public UI preference, never payment details or credentials.
    sessionStorage.setItem("payment_cycle_intent", billingCycle === "year" ? "year" : "month");
    if (authConfig.registrationEnabled) {
      navigate(withAttributionSource("/register?plan=pro"));
      return;
    }
    navigate(withAttributionSource("/login?plan=pro"));
  };

  const visibleMetrics = useMemo(() => {
    if (!showOnlyDifferences || sortedPlans.length <= 1) {
      return metricDefinitions;
    }

    return metricDefinitions.filter((metric) => {
      const uniqueValues = new Set(
        sortedPlans.map((plan) =>
          toComparableMetricValue(metric.compareValue(plan))
        )
      );
      return uniqueValues.size > 1;
    });
  }, [showOnlyDifferences, metricDefinitions, sortedPlans]);

  const hasSharedMetrics = !isCatalogLoading && !isError && sortedPlans.length > 1 &&
    metricDefinitions.some((metric) => new Set(
      sortedPlans.map((plan) => toComparableMetricValue(metric.compareValue(plan)))
    ).size === 1);

  return (
    <div className="public-page min-h-screen bg-[hsl(var(--bg-primary))]">
      <PublicHeader maxWidth="max-w-6xl" />
      <main className="max-w-6xl mx-auto px-4 md:px-6 py-8 md:py-14 pb-14 space-y-6 md:space-y-8">
        <section className="text-center space-y-2 md:space-y-3">
          <h1 className="text-3xl md:text-4xl font-bold text-[hsl(var(--text-primary))]">
            {t("dashboard:billing.compareTitle", "选一个适合你的方案")}
          </h1>
          <p className="text-sm md:text-base text-[hsl(var(--text-secondary))]">
            {t("dashboard:billing.catalogSubtitle", "免费版每天包含 3 条 AI 消息。")}
          </p>
        </section>

        <section className="sticky top-14 z-20 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary)/0.9)] p-3 md:p-4 backdrop-blur-sm">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p id="billing-cycle-label" className="text-sm font-medium text-[hsl(var(--text-primary))]">
              {t("dashboard:billing.billingCycleLabel", "购买时长")}
            </p>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div
                className="inline-flex rounded-lg bg-[hsl(var(--bg-tertiary))] p-1"
                role="radiogroup"
                aria-labelledby="billing-cycle-label"
              >
                {(["month", "year"] as BillingCycle[]).map((cycle) => (
                  <button
                    key={cycle}
                    id={`billing-cycle-${cycle}`}
                    type="button"
                    role="radio"
                    aria-checked={billingCycle === cycle}
                    disabled={isCatalogLoading || isError || sortedPlans.length === 0}
                    tabIndex={billingCycle === cycle ? 0 : -1}
                    onClick={() => setBillingCycle(cycle)}
                    onKeyDown={(event) => {
                      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
                      event.preventDefault();
                      const nextCycle: BillingCycle = cycle === "month" ? "year" : "month";
                      setBillingCycle(nextCycle);
                      document.getElementById(`billing-cycle-${nextCycle}`)?.focus();
                    }}
                    className={cn(
                      "min-h-11 px-4 text-sm rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                      billingCycle === cycle
                        ? "bg-[hsl(var(--accent-primary))] text-white"
                        : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
                    )}
                  >
                    {cycle === "month"
                      ? t("dashboard:billing.monthlyToggle", "月付")
                      : bestYearlySavings
                        ? t("dashboard:billing.yearlyToggleWithSavings", "年付 · 省 {{percent}}%", {
                            percent: bestYearlySavings.percent,
                          })
                        : t("dashboard:billing.yearlyToggle", "年付")}
                  </button>
                ))}
              </div>

              {hasSharedMetrics && <button
                type="button"
                role="switch"
                aria-checked={showOnlyDifferences}
                onClick={() => setShowOnlyDifferences((prev) => !prev)}
                className="inline-flex min-h-11 items-center justify-between gap-3 rounded-lg border border-[hsl(var(--border-color))] px-3 text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
              >
                <span>{t("dashboard:billing.showOnlyDiff", "仅看差异")}</span>
                <span
                  aria-hidden="true"
                  className={cn(
                    "relative h-5 w-9 shrink-0 rounded-full transition-colors",
                    showOnlyDifferences ? "bg-[hsl(var(--accent-primary))]" : "bg-[hsl(var(--text-tertiary))]"
                  )}
                >
                  <span
                    className={cn(
                      "absolute left-0 top-0.5 h-4 w-4 rounded-full bg-white transition-transform",
                      showOnlyDifferences ? "translate-x-4" : "translate-x-0.5"
                    )}
                  />
                </span>
              </button>}
            </div>
          </div>
        </section>

        {isCatalogLoading && (
          <div role="status" className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <span className="sr-only">{t("common:loading", "加载中…")}</span>
            {Array.from({ length: 2 }).map((_, index) => (
              <div key={index} className="h-72 animate-pulse rounded-xl bg-[hsl(var(--bg-tertiary))]" />
            ))}
          </div>
        )}

        {isError && (
          <Card variant="outlined" padding="lg">
            <div className="flex flex-col items-center gap-3 py-2">
              <p role="alert" className="text-sm text-[hsl(var(--error-text))]">{t("common:error", "加载失败")}</p>
              <Button size="sm" className="max-md:min-h-11" variant="secondary" onClick={() => void refetch()}>
                {t("common:retry", "重试")}
              </Button>
            </div>
          </Card>
        )}

        {!isCatalogLoading && !isError && sortedPlans.length === 0 && (
          <Card variant="outlined" padding="lg">
            <div className="text-center text-sm text-[hsl(var(--text-secondary))] py-2">
              {t("common:noData", "暂无数据")}
            </div>
          </Card>
        )}

        {!isCatalogLoading && !isError && sortedPlans.length > 0 && (
          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {sortedPlans.map((plan) => (
              <Card
                key={plan.id}
                variant="outlined"
                padding="lg"
                className={cn(
                  "rounded-2xl relative flex flex-col border!",
                  plan.recommended
                    ? "border-[hsl(var(--accent-primary)/0.55)]!"
                    : "border-[hsl(var(--border-color))]"
                )}
              >
                <div className="flex items-start justify-between gap-3 mb-5 lg:min-h-[68px]">
                  <div>
                    <h2 className="text-xl font-semibold text-[hsl(var(--text-primary))]">
                      {getLocalizedPlanDisplayName({ ...plan, tier: plan.name }, i18n.language)}
                    </h2>
                    {plan.name !== "free" && (
                      <p className="mt-2 text-2xl font-semibold text-[hsl(var(--text-primary))]">
                        {formatPrice(plan, billingCycle)}
                      </p>
                    )}
                  </div>
                  {plan.recommended && (
                    <Badge variant="neutral">
                      {t("dashboard:billing.recommended", "推荐")}
                    </Badge>
                  )}
                </div>
                <div className="space-y-2">
                  {visibleMetrics.map((metric) => (
                    <div key={metric.key} className="flex items-center justify-between text-sm gap-3">
                      <div className="text-[hsl(var(--text-secondary))] min-w-0">
                        <div className="flex items-center gap-1.5">
                          {metric.compareValue(plan) === 0 ? (
                            <Minus aria-hidden="true" className="w-3.5 h-3.5 text-[hsl(var(--text-tertiary))] shrink-0" />
                          ) : (
                            <Check aria-hidden="true" className="w-3.5 h-3.5 text-[hsl(var(--success-text))] shrink-0" />
                          )}
                          <span className="truncate">{metric.label}</span>
                        </div>
                        <p className="ml-5 mt-0.5 text-xs text-[hsl(var(--text-tertiary))]">{metric.outcome}</p>
                      </div>
                      <span className="font-medium text-[hsl(var(--text-primary))] shrink-0">
                        {metric.value(plan)}
                      </span>
                    </div>
                  ))}
                  {showOnlyDifferences && visibleMetrics.length === 0 && (
                    <div className="rounded-lg border border-dashed border-[hsl(var(--border-color))] px-3 py-2 text-xs text-[hsl(var(--text-secondary))]">
                      {t("dashboard:billing.noDiffMetric", "各套餐在这些项目上没有区别")}
                    </div>
                  )}
                </div>
                <div className="mt-auto pt-4">
                  <Button
                    className="w-full"
                    size="touch"
                    onClick={plan.price_monthly_cents > 0 ? handleUpgradeCta : handlePrimaryCta}
                    variant={plan.price_monthly_cents > 0 ? "primary" : "secondary"}
                  >
                    {plan.price_monthly_cents > 0
                      ? upgradeCtaLabel
                      : t("dashboard:billing.ctaFreeStart", "免费开始")}
                  </Button>
                </div>
              </Card>
            ))}
          </section>
        )}

        <section className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {[
            {
              key: "trust-flexible",
              icon: RefreshCw,
              text: t("dashboard:billing.trustFlexible", "支付宝单次支付，不会自动续费"),
            },
            {
              key: "trust-security",
              icon: ShieldCheck,
              text: t("dashboard:billing.trustSecurity", "作品数据通过加密连接传输"),
            },
          ].map((item) => (
            <div
              key={item.key}
              className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-xs text-[hsl(var(--text-secondary))] flex items-center gap-2"
            >
              <item.icon className="w-4 h-4 text-[hsl(var(--success-text))] shrink-0" />
              <span>{item.text}</span>
            </div>
          ))}
        </section>

      </main>

      {showPaymentModal && (
        <PaymentCheckoutModal
          isOpen
          onClose={() => setShowPaymentModal(false)}
          initialCycle={billingCycle}
          monthlyPriceCents={proPlan?.price_monthly_cents}
          yearlyPriceCents={proPlan?.price_yearly_cents}
          isRenewal={isProUser}
        />
      )}
    </div>
  );
}
