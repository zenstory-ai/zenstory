import type {
  SubscriptionCatalogEntitlements,
  SubscriptionCatalogTier,
} from "../types/subscription";
import { inspirationsConfig } from "../config/inspirations";

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
};

function resolveLocale(language?: string): string {
  return language?.startsWith("en") ? "en-US" : "zh-CN";
}

export function getLocalizedPlanDisplayName(
  plan: LocalizedPlan,
  language: string | undefined,
): string {
  if (language?.startsWith("en")) {
    return plan.display_name_en?.trim() || plan.display_name;
  }

  return plan.display_name;
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
    return translate(t, "settings:subscription.unlimited", "无限");
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
  const dayUnit = translate(t, "dashboard:billing.timesPerDay", "次/天");

  // Only entitlements the backend enforces. Context window size and priority
  // queueing were advertised without an implementation and are not listed.
  const definitions: EntitlementMetricDefinition[] = [
    {
      key: "ai_conversations_per_day",
      label: translate(t, "dashboard:billing.metricAiConversations", "每日 AI 消息"),
      outcome: translate(
        t,
        "dashboard:billing.metricAiConversationsOutcome",
        "北京时间 00:00 重置",
      ),
      value: (plan) =>
        `${formatLimit(plan.entitlements.ai_conversations_per_day)} ${dayUnit}`,
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
      value: (plan) =>
        `${formatLimit(plan.entitlements.active_projects_limit)} ${translate(
          t,
          "dashboard:billing.projectsUnit",
          "个",
        )}`,
      compareValue: (plan) => plan.entitlements.active_projects_limit,
    },
    {
      key: "material_decompositions_monthly",
      label: translate(
        t,
        "dashboard:billing.metricMaterialDecompositions",
        "素材拆解次数",
      ),
      outcome: translate(
        t,
        "dashboard:billing.metricMaterialDecompositionsOutcome",
        "把参考小说拆成人物、情节等要点",
      ),
      value: (plan) =>
        `${formatLimit(plan.entitlements.material_decompositions_monthly)} ${monthUnit}`,
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
      value: (plan) =>
        `${formatLimit(plan.entitlements.inspiration_copies_monthly)} ${monthUnit}`,
      compareValue: (plan) => plan.entitlements.inspiration_copies_monthly,
    },
    {
      key: "export_formats",
      label: translate(t, "dashboard:billing.metricExport", "导出格式"),
      outcome: translate(
        t,
        "dashboard:billing.metricExportOutcome",
        "把作品导出成文件，用于投稿或备份",
      ),
      value: (plan) =>
        plan.entitlements.export_formats.length > 0
          ? plan.entitlements.export_formats.join(", ").toUpperCase()
          : translate(t, "dashboard:billing.noExportFormats", "暂无"),
      compareValue: (plan) => [...plan.entitlements.export_formats].sort(),
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
 * (context_window_tokens, priority_support, custom_prompts) are never shown.
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
    if (value === -1) return translate(t, "settings:subscription.unlimited", "无限");
    return `${value.toLocaleString(resolveLocale(language))} ${unit}`;
  };
  const monthUnit = translate(t, "dashboard:billing.timesPerMonth", "次/月");
  const dayUnit = translate(t, "dashboard:billing.timesPerDay", "次/天");
  const countUnit = translate(t, "dashboard:billing.projectsUnit", "个");
  const notIncluded = translate(t, "settings:subscription.notIncluded", "不含");
  const hasMaterials = features.materials_library_access === true;
  const materialValue = (key: string) => (hasMaterials ? withUnit(numberOf(key), monthUnit) : notIncluded);
  const exportFormats = Array.isArray(features.export_formats)
    ? (features.export_formats as unknown[]).filter((item): item is string => typeof item === "string")
    : null;

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
      key: "material_uploads",
      label: translate(t, "dashboard:billing.metricMaterialUploads", "素材上传"),
      value: materialValue("material_uploads") ?? "",
    },
    {
      key: "material_decompositions",
      label: translate(t, "dashboard:billing.metricMaterialDecompositions", "素材拆解次数"),
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
    exportFormats
      ? {
          key: "export_formats",
          label: translate(t, "dashboard:billing.metricExport", "导出格式"),
          value:
            exportFormats.length > 0
              ? exportFormats.join(", ").toUpperCase()
              : translate(t, "dashboard:billing.noExportFormats", "暂无"),
        }
      : null,
  ];

  return rows.filter((row): row is SubscriptionFeatureRow => Boolean(row && row.value));
}

export function toComparableMetricValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join("|");
  }

  return String(value);
}
