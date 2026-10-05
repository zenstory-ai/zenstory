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

export function formatMonthlyOutputEstimate(
  credits: number,
  t: TranslateFn,
): string {
  if (credits === -1) {
    return translate(t, "settings:subscription.unlimited", "无限");
  }

  const estimatedWanWords = Math.max(1, Math.round(credits / 10000));
  return translate(
    t,
    "dashboard:billing.monthlyOutputEstimate",
    "约 {{count}} 万字/月",
    { count: estimatedWanWords },
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

  // Only entitlements the backend enforces. Context window size and priority
  // queueing were advertised without an implementation and are not listed.
  const definitions: EntitlementMetricDefinition[] = [
    {
      key: "writing_credits_monthly",
      label: translate(t, "dashboard:billing.metricWriting", "可创作体量"),
      outcome: translate(
        t,
        "dashboard:billing.metricWritingOutcome",
        "支持从大纲到正文的连续输出",
      ),
      value: (plan) =>
        formatMonthlyOutputEstimate(plan.entitlements.writing_credits_monthly, t),
      compareValue: (plan) => plan.entitlements.writing_credits_monthly,
    },
    {
      key: "agent_runs_monthly",
      label: translate(t, "dashboard:billing.metricAgentRuns", "Agent 深度任务"),
      outcome: translate(
        t,
        "dashboard:billing.metricAgentRunsOutcome",
        "用于拆解任务、扩写、润色与修订",
      ),
      value: (plan) =>
        `${formatLimit(plan.entitlements.agent_runs_monthly)} ${monthUnit}`,
      compareValue: (plan) => plan.entitlements.agent_runs_monthly,
    },
    {
      key: "active_projects_limit",
      label: translate(t, "dashboard:billing.metricProjects", "活跃项目"),
      outcome: translate(
        t,
        "dashboard:billing.metricProjectsOutcome",
        "同时推进多本作品，不必频繁归档",
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
        "每月可拆解参考素材，快速提取结构化要点",
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
        "沉淀团队方法论并复用到日常创作",
      ),
      value: (plan) => formatLimit(plan.entitlements.custom_skills_limit),
      compareValue: (plan) => plan.entitlements.custom_skills_limit,
    },
    {
      key: "inspiration_copies_monthly",
      label: translate(
        t,
        "dashboard:billing.metricInspirationCopies",
        "灵感复用",
      ),
      outcome: translate(
        t,
        "dashboard:billing.metricInspirationCopiesOutcome",
        "将优秀模板复制到项目，快速启动创作",
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
        "完稿可直接交付给编辑、团队或客户",
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
