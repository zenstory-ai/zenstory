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
  value: number,
  language: string | undefined,
  t: TranslateFn,
): string {
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
  const formatLimit = (value: number) =>
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
      label: translate(t, "dashboard:billing.metricAiConversations", "每日 AI 对话"),
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

export function toComparableMetricValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.join("|");
  }

  return String(value);
}
