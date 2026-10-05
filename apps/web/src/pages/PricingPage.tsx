import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Check, ArrowRight, RefreshCw, ShieldCheck } from "lucide-react";
import { PublicHeader } from "../components/PublicHeader";
import { Card } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { subscriptionApi } from "../lib/subscriptionApi";
import { cn } from "../lib/utils";
import {
  getEntitlementMetricDefinitions,
  getLocalizedPlanDisplayName,
  toComparableMetricValue,
} from "../lib/subscriptionEntitlements";
import { buildUpgradeUrl } from "../config/upgradeExperience";
import { useAuth } from "../contexts/AuthContext";
import { authConfig } from "../config/auth";
import { useIsMobile } from "../hooks/useMediaQuery";
import type { SubscriptionCatalogTier } from "../types/subscription";
import { trackUpgradeConversion } from "../lib/upgradeAnalytics";
import { trackEvent } from "../lib/analytics";
import { PaymentCheckoutModal } from "../components/subscription/PaymentCheckoutModal";

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
  const isMobile = useIsMobile();
  const [billingCycle, setBillingCycle] = useState<BillingCycle>("month");
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

  const formatCurrency = (cents: number): string => {
    const locale = i18n.language?.startsWith("en") ? "en-US" : "zh-CN";
    const amount = (cents / 100).toLocaleString(locale, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    return `¥${amount}`;
  };

  const formatPrice = (cents: number, cycle: BillingCycle): string => {
    if (cents === 0) return t("dashboard:billing.free", "免费");
    return cycle === "month"
      ? `${formatCurrency(cents)}${t("dashboard:billing.perMonth", "/月")}`
      : `${formatCurrency(cents)}${t("dashboard:billing.perYear", "/年")}`;
  };

  const metricDefinitions = useMemo(
    () => getEntitlementMetricDefinitions(t, i18n.language),
    [i18n.language, t]
  );

  const getYearlySavings = (plan: SubscriptionCatalogTier): { amount: number; percent: number } | null => {
    if (plan.price_monthly_cents <= 0 || plan.price_yearly_cents <= 0) return null;
    const yearlyPriceFromMonthly = plan.price_monthly_cents * 12;
    if (yearlyPriceFromMonthly <= plan.price_yearly_cents) return null;
    const amount = yearlyPriceFromMonthly - plan.price_yearly_cents;
    const percent = Math.round((amount / yearlyPriceFromMonthly) * 100);
    return { amount, percent };
  };

  const maxYearlySavingsPercent = useMemo(
    () =>
      sortedPlans.reduce((max, plan) => {
        const savings = getYearlySavings(plan);
        return savings ? Math.max(max, savings.percent) : max;
      }, 0),
    [sortedPlans]
  );

  const getSummary = (summaryKey: string): string => {
    if (summaryKey === "creator") {
      return t("dashboard:billing.summaryCreator", "连续日更与稳定产出，减少关键时刻配额中断。");
    }
    return t("dashboard:billing.summaryStarter", "先跑通从灵感到完稿的完整流程，再按产能升级。");
  };

  const getTargetUser = (targetUserKey: string): string => {
    if (targetUserKey === "daily_writer") {
      return t("dashboard:billing.targetDailyWriter", "日更作者、连载作者与长篇创作者");
    }
    return t("dashboard:billing.targetExplorer", "新用户、轻度创作与探索期用户");
  };

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

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))]">
      <PublicHeader maxWidth="max-w-6xl" />
      <main className="max-w-6xl mx-auto px-4 md:px-6 py-8 md:py-14 pb-28 md:pb-14 space-y-6 md:space-y-8">
        <section className="text-center space-y-2 md:space-y-3">
          <div className="inline-flex items-center rounded-full border border-[hsl(var(--accent-primary)/0.35)] px-3 py-1 text-xs text-[hsl(var(--accent-light))]">
            {t("dashboard:billing.taskLedBadge", "按创作任务选方案")}
          </div>
          <h1 className="text-3xl md:text-4xl font-bold text-[hsl(var(--text-primary))]">
            {isCatalogLoading ? t("common:loading", "加载中...") : t("dashboard:billing.compareTitle", "套餐权益对比")}
          </h1>
          <p className="text-sm md:text-base text-[hsl(var(--text-secondary))]">
            {t(
              "dashboard:billing.catalogSubtitle",
              "从“你下一步要完成什么”出发选择计划：先跑通流程，再按产能升级。"
            )}
          </p>
        </section>

        <section className="sticky top-14 z-20 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary)/0.9)] p-3 md:p-4 backdrop-blur-sm">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="space-y-1">
              <p id="billing-cycle-label" className="text-sm font-medium text-[hsl(var(--text-primary))]">
                {t("dashboard:billing.billingCycleLabel", "选择计费周期")}
              </p>
              <p className="text-xs text-[hsl(var(--text-secondary))]">
                {maxYearlySavingsPercent > 0
                  ? t("dashboard:billing.yearlyMaxSaveHint", "年付最高优惠 {{percent}}", {
                      percent: `${maxYearlySavingsPercent}%`,
                    })
                  : t("dashboard:billing.yearlyMaxSaveHintFallback", "切换月付/年付，按预算灵活决策")}
              </p>
            </div>

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
                      "h-8 px-3 text-sm rounded-md transition-colors",
                      billingCycle === cycle
                        ? "bg-[hsl(var(--accent-primary))] text-white"
                        : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
                    )}
                  >
                    {cycle === "month"
                      ? t("dashboard:billing.monthlyToggle", "月付")
                      : t("dashboard:billing.yearlyToggle", "年付")}
                  </button>
                ))}
              </div>

              <button
                type="button"
                aria-pressed={showOnlyDifferences}
                onClick={() => setShowOnlyDifferences((prev) => !prev)}
                className="inline-flex h-8 items-center justify-between gap-2 rounded-lg border border-[hsl(var(--border-color))] px-3 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
              >
                <span>{t("dashboard:billing.showOnlyDiff", "仅看差异")}</span>
                <span
                  className={cn(
                    "relative h-4 w-8 rounded-full transition-colors",
                    showOnlyDifferences ? "bg-[hsl(var(--accent-primary))]" : "bg-[hsl(var(--bg-tertiary))]"
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform",
                      showOnlyDifferences ? "translate-x-4" : "translate-x-0.5"
                    )}
                  />
                </span>
              </button>
            </div>
          </div>
        </section>

        {isCatalogLoading && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {Array.from({ length: 2 }).map((_, index) => (
              <div key={index} className="h-72 animate-pulse rounded-xl bg-[hsl(var(--bg-tertiary))]" />
            ))}
          </div>
        )}

        {isError && (
          <Card variant="outlined" padding="lg">
            <div className="flex flex-col items-center gap-3 py-2">
              <p className="text-sm text-[hsl(var(--error))]">{t("common:error", "加载失败")}</p>
              <Button size="sm" variant="secondary" onClick={() => void refetch()}>
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
                  "rounded-2xl relative transition-all",
                  plan.recommended
                    ? "border-[hsl(var(--accent-primary))] bg-[linear-gradient(180deg,hsl(var(--accent-primary)/0.14),hsl(var(--bg-secondary))_46%)] shadow-[0_16px_36px_hsl(var(--accent-primary)/0.16)]"
                    : "border-[hsl(var(--border-color))]"
                )}
              >
                {plan.recommended && (
                  <div className="absolute left-0 right-0 top-0 h-1 rounded-t-2xl bg-[hsl(var(--accent-primary))]" />
                )}
                <div className="flex items-start justify-between mb-3 pt-1">
                  <div>
                    <h2 className="text-xl font-semibold text-[hsl(var(--text-primary))]">
                      {getLocalizedPlanDisplayName(plan, i18n.language)}
                    </h2>
                    <div className="mt-1 flex items-center flex-wrap gap-2">
                      <p className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                        {formatPrice(
                          billingCycle === "month" ? plan.price_monthly_cents : plan.price_yearly_cents,
                          billingCycle
                        )}
                      </p>
                      {billingCycle === "year" && getYearlySavings(plan) && (
                        <Badge variant="purple">
                          {t("dashboard:billing.yearlySaveBadge", "年付省 {{amount}} · {{percent}}%", {
                            amount: formatCurrency(getYearlySavings(plan)?.amount ?? 0),
                            percent: getYearlySavings(plan)?.percent ?? 0,
                          })}
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-[hsl(var(--text-secondary))]">
                      {billingCycle === "month"
                        ? t("dashboard:billing.alternateYearlyPrice", "年付 {{price}}", {
                            price: formatPrice(plan.price_yearly_cents, "year"),
                          })
                        : t("dashboard:billing.alternateMonthlyPrice", "月付 {{price}}", {
                            price: formatPrice(plan.price_monthly_cents, "month"),
                          })}
                    </p>
                    <p className="text-sm text-[hsl(var(--text-secondary))] mt-2">
                      {getSummary(plan.summary_key)}
                    </p>
                    <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
                      {t("dashboard:billing.forUsers", "适合：{{target}}", { target: getTargetUser(plan.target_user_key) })}
                    </p>
                  </div>
                  {plan.recommended && (
                    <Badge variant="purple">
                      {t("dashboard:billing.recommended", "推荐")}
                    </Badge>
                  )}
                </div>
                <div className="space-y-2">
                  {visibleMetrics.map((metric) => (
                    <div key={metric.key} className="flex items-center justify-between text-sm gap-3">
                      <div className="text-[hsl(var(--text-secondary))] min-w-0">
                        <div className="flex items-center gap-1.5">
                          <Check className="w-3.5 h-3.5 text-[hsl(var(--success))] shrink-0" />
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
                      {t("dashboard:billing.noDiffMetric", "当前维度下，这些套餐暂无可见差异")}
                    </div>
                  )}
                </div>
                <div className="mt-4">
                  <Button
                    className="w-full"
                    onClick={plan.price_monthly_cents > 0 ? handleUpgradeCta : handlePrimaryCta}
                    variant={plan.price_monthly_cents > 0 ? "primary" : "secondary"}
                  >
                    {plan.price_monthly_cents > 0
                      ? t("dashboard:billing.ctaUpgradePro", "升级专业版")
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
              text: t("dashboard:billing.trustSecurity", "数据全程加密与隔离，保障内容安全"),
            },
          ].map((item) => (
            <div
              key={item.key}
              className="rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-xs text-[hsl(var(--text-secondary))] flex items-center gap-2"
            >
              <item.icon className="w-4 h-4 text-[hsl(var(--success))] shrink-0" />
              <span>{item.text}</span>
            </div>
          ))}
        </section>

        {!isMobile && (
          <section className="flex justify-center gap-3">
            <Button onClick={handlePrimaryCta} rightIcon={<ArrowRight className="w-4 h-4" />}>
              {user
                ? t("home:nav.goDashboard", "进入工作台")
                : t("dashboard:billing.ctaFreeStart", "免费开始")}
            </Button>
            <Button onClick={handleUpgradeCta} variant="outline">
              {t("dashboard:billing.ctaUpgradePro", "升级专业版")}
            </Button>
          </section>
        )}
      </main>

      {isMobile && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary)/0.95)] backdrop-blur-sm">
          <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-2">
            <Button className="flex-1" onClick={handlePrimaryCta} rightIcon={<ArrowRight className="w-4 h-4" />}>
              {user
                ? t("home:nav.goDashboard", "进入工作台")
                : t("dashboard:billing.ctaFreeStart", "免费开始")}
            </Button>
            <Button className="flex-1" onClick={handleUpgradeCta} variant="outline">
              {t("dashboard:billing.ctaUpgradePro", "升级专业版")}
            </Button>
          </div>
        </div>
      )}

      {showPaymentModal && (
        <PaymentCheckoutModal
          isOpen
          onClose={() => setShowPaymentModal(false)}
          initialCycle={billingCycle}
          monthlyPriceCents={proPlan?.price_monthly_cents}
          yearlyPriceCents={proPlan?.price_yearly_cents}
        />
      )}
    </div>
  );
}
