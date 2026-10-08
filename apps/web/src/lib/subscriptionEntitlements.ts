import type {
  SubscriptionCatalogEntitlements,
  SubscriptionCatalogTier,
  SubscriptionStatusResponse,
} from "../types/subscription";
import { inspirationsConfig } from "../config/inspirations";
import { formatBeijingPeriodDate } from "./dateUtils";

type TranslateFn = (
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>
) => string;

export interface EntitlementMetricDefinition {
  key: keyof SubscriptionCatalogEntitlements;
  label: string;
  outcome: string;
  value: (plan: SubscriptionCatalogTier) => string;
  compareValue: (plan: SubscriptionCatalogTier) => unknown;
}

type LocalizedPlan = {
  display_name: string;
  display_name_en?: string | null;
  /**
   * Plan tier (`free` / `pro`). When given, author-facing names follow the
   * product glossary instead of the stored display_name, which older rows
   * still hold as "免费试用 / Free Trial" although the free plan never ends.
   */
  tier?: string | null;
};

const GLOSSARY_PLAN_NAMES: Record<string, { zh: string; en: string }> = {
  free: { zh: "免费版", en: "Free" },
  pro: { zh: "Pro", en: "Pro" },
};

function resolveLocale(language?: string): string {
  return language?.startsWith("en") ? "en-US" : "zh-CN";
}

export function getLocalizedPlanDisplayName(
  plan: LocalizedPlan,
  language: string | undefined,
): string {
  const glossaryName = plan.tier ? GLOSSARY_PLAN_NAMES[plan.tier] : undefined;
  if (glossaryName) {
    return language?.startsWith("en") ? glossaryName.en : glossaryName.zh;
  }

  if (language?.startsWith("en")) {
    return plan.display_name_en?.trim() || plan.display_name;
  }

  return plan.display_name;
}

/**
 * The status line next to the plan badge. The free plan never ends, so it gets
 * no status word ("未开通" read as if the account could not be used); Pro is
 * one-time payment without auto-renewal, so it shows when it ends.
 */
export function getSubscriptionStatusLine(
  status: Pick<SubscriptionStatusResponse, "tier" | "status" | "current_period_end" | "days_remaining">,
  t: TranslateFn,
): string | null {
  if (status.tier === "free") return null;
  if (status.status === "active") {
    if (!status.current_period_end) return null;
    return translate(t, "settings:subscription.activeUntil", "有效期至 {{date}} · 剩余 {{days}} 天", {
      date: formatBeijingPeriodDate(status.current_period_end),
      days: status.days_remaining ?? 0,
    });
  }
  if (status.status === "expired") {
    return translate(t, "settings:subscription.expired", "已到期，现在是免费版");
  }
  if (status.status === "cancelled") {
    return translate(t, "settings:subscription.cancelled", "已取消");
  }
  return null;
}

function translate(
  t: TranslateFn,
  key: string,
  defaultValue: string,
  options?: Record<string, unknown>
): string {
  return options ? t(key, defaultValue, options) : t(key, defaultValue);
}

export function formatEntitlementLimit(
  value: number | undefined,
  language: string | undefined,
  t: TranslateFn,
): string {
  if (value === undefined) {
    return "-";
  }
  if (value === -1) {
    return translate(t, "settings:subscription.unlimited", "不限");
  }

  return value.toLocaleString(resolveLocale(language));
}

// The web app ships before the API (Vercel deploys on merge, Railway after E2E),
// so a newly added entitlement can be missing from the catalog for a while.
// Hide those rows instead of formatting an undefined limit.
export function filterAvailableMetrics(
  definitions: EntitlementMetricDefinition[],
  plans: SubscriptionCatalogTier[],
): EntitlementMetricDefinition[] {
  return definitions.filter((metric) =>
    plans.every((plan) => plan.entitlements?.[metric.key] != null),
  );
}

export function getEntitlementMetricDefinitions(
  t: TranslateFn,
  language: string | undefined,
): EntitlementMetricDefinition[] {
  const formatLimit = (value: number | undefined) =>
    formatEntitlementLimit(value, language, t);

  const monthUnit = translate(
    t,
    "dashboard:billing.timesPerMonth",
    "次/月",
  );
  const dayUnit = translate(t, "dashboard:billing.messagesPerDay", "条/天");
  const notIncluded = translate(t, "settings:subscription.notIncluded", "不含");
  const projectsUnit = translate(t, "dashboard:billing.projectsUnit", "个");
  // "不限" stands alone ("不限 条/天" reads oddly), and a monthly allowance of 0
  // means the plan does not include the feature: "0 次/月" looks broken.
  const withUnit = (value: number | undefined, unit: string) =>
    value === -1 || value === undefined ? formatLimit(value) : `${formatLimit(value)} ${unit}`;
  const formatMonthly = (value: number | undefined) =>
    value === 0 ? notIncluded : withUnit(value, monthUnit);

  // Only entitlements the backend enforces and that differ by plan. Context
  // window size and priority queueing were advertised without an
  // implementation; export formats are TXT on every plan.
  const definitions: EntitlementMetricDefinition[] = [
    {
      key: "ai_conversations_per_day",
      label: translate(t, "dashboard:billing.metricAiConversations", "每日 AI 消息"),
      outcome: translate(
        t,
        "dashboard:billing.metricAiConversationsOutcome",
        "北京时间 00:00 重置",
      ),
      value: (plan) => withUnit(plan.entitlements.ai_conversations_per_day, dayUnit),
      compareValue: (plan) => plan.entitlements.ai_conversations_per_day,
    },
    {
      key: "active_projects_limit",
      label: translate(t, "dashboard:billing.metricProjects", "项目数"),
      outcome: translate(
        t,
        "dashboard:billing.metricProjectsOutcome",
        "可以同时写几部作品",
      ),
      value: (plan) => withUnit(plan.entitlements.active_projects_limit, projectsUnit),
      compareValue: (plan) => plan.entitlements.active_projects_limit,
    },
    {
      key: "material_decompositions_monthly",
      label: translate(
        t,
        "dashboard:billing.metricMaterialDecompositions",
        "素材拆解",
      ),
      outcome: translate(
        t,
        "dashboard:billing.metricMaterialDecompositionsOutcome",
        "把参考小说拆成人物、情节等要点",
      ),
      value: (plan) => formatMonthly(plan.entitlements.material_decompositions_monthly),
      compareValue: (plan) => plan.entitlements.material_decompositions_monthly,
    },
    {
      key: "custom_skills_limit",
      label: translate(
        t,
        "dashboard:billing.metricCustomSkills",
        "自定义技能",
      ),
      outcome: translate(
        t,
        "dashboard:billing.metricCustomSkillsOutcome",
        "把常用的写作要求存成技能，随时调用",
      ),
      value: (plan) => formatLimit(plan.entitlements.custom_skills_limit),
      compareValue: (plan) => plan.entitlements.custom_skills_limit,
    },
    {
      key: "inspiration_copies_monthly",
      label: translate(
        t,
        "dashboard:billing.metricInspirationCopies",
        "复制灵感",
      ),
      outcome: translate(
        t,
        "dashboard:billing.metricInspirationCopiesOutcome",
        "把灵感库中的灵感复制成你的项目",
      ),
      value: (plan) => formatMonthly(plan.entitlements.inspiration_copies_monthly),
      compareValue: (plan) => plan.entitlements.inspiration_copies_monthly,
    },
  ];

  return definitions.filter(
    (definition) => inspirationsConfig.enabled || definition.key !== "inspiration_copies_monthly",
  );
}

export interface YearlySavings {
  /** Cents saved versus twelve monthly payments. */
  amount: number;
  percent: number;
  /** Yearly price spread over twelve months, in cents. */
  monthlyEquivalent: number;
}

export function getYearlySavings(
  monthlyCents: number | undefined,
  yearlyCents: number | undefined,
): YearlySavings | null {
  if (!monthlyCents || !yearlyCents || monthlyCents <= 0 || yearlyCents <= 0) return null;
  const twelveMonths = monthlyCents * 12;
  if (twelveMonths <= yearlyCents) return null;
  const amount = twelveMonths - yearlyCents;
  return {
    amount,
    percent: Math.round((amount / twelveMonths) * 100),
    monthlyEquivalent: Math.round(yearlyCents / 12),
  };
}

export function formatYuan(cents: number, language: string | undefined): string {
  return `¥${(cents / 100).toLocaleString(resolveLocale(language), {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
}

export interface SubscriptionFeatureRow {
  key: string;
  label: string;
  value: string;
}

/**
 * Rows for the settings subscription card, built from `/subscription/me`
 * features. Only limits the backend enforces are listed, with the same labels
 * as the billing comparison; stored keys without an implementation
 * (context_window_tokens, priority_support, custom_prompts, material_uploads)
 * and export_formats (TXT on every plan) are never shown.
 */
export function getSubscriptionFeatureRows(
  features: Record<string, unknown> | undefined,
  t: TranslateFn,
  language: string | undefined,
): SubscriptionFeatureRow[] {
  if (!features) return [];
  const numberOf = (key: string): number | undefined => {
    const value = features[key];
    return typeof value === "number" ? value : undefined;
  };
  const withUnit = (value: number | undefined, unit: string): string | null => {
    if (value === undefined) return null;
    if (value === -1) return translate(t, "settings:subscription.unlimited", "不限");
    return `${value.toLocaleString(resolveLocale(language))} ${unit}`;
  };
  const monthUnit = translate(t, "dashboard:billing.timesPerMonth", "次/月");
  const dayUnit = translate(t, "dashboard:billing.messagesPerDay", "条/天");
  const countUnit = translate(t, "dashboard:billing.projectsUnit", "个");
  const notIncluded = translate(t, "settings:subscription.notIncluded", "不含");
  const hasMaterials = features.materials_library_access === true;
  const materialValue = (key: string) => (hasMaterials ? withUnit(numberOf(key), monthUnit) : notIncluded);

  const rows: Array<SubscriptionFeatureRow | null> = [
    {
      key: "ai_conversations_per_day",
      label: translate(t, "dashboard:billing.metricAiConversations", "每日 AI 消息"),
      value: withUnit(numberOf("ai_conversations_per_day"), dayUnit) ?? "",
    },
    {
      key: "max_projects",
      label: translate(t, "dashboard:billing.metricProjects", "项目数"),
      value: withUnit(numberOf("max_projects"), countUnit) ?? "",
    },
    {
      key: "material_decompositions",
      label: translate(t, "dashboard:billing.metricMaterialDecompositions", "素材拆解"),
      value: materialValue("material_decompositions") ?? "",
    },
    {
      key: "custom_skills",
      label: translate(t, "dashboard:billing.metricCustomSkills", "自定义技能"),
      value: withUnit(numberOf("custom_skills"), countUnit) ?? "",
    },
    inspirationsConfig.enabled
      ? {
          key: "inspiration_copies_monthly",
          label: translate(t, "dashboard:billing.metricInspirationCopies", "复制灵感"),
          value: withUnit(numberOf("inspiration_copies_monthly"), monthUnit) ?? "",
        }
      : null,
    {
      key: "file_versions_per_file",
      label: translate(t, "dashboard:billing.metricFileVersions", "每个文件保留的历史版本"),
      value: withUnit(numberOf("file_versions_per_file"), countUnit) ?? "",
    },
  ];

  return rows.filter((row): row is SubscriptionFeatureRow => Boolean(row && row.value));
}

export function toComparableMetricValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join("|");
  }

  return String(value);
}
