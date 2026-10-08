import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  filterAvailableMetrics,
  formatEntitlementLimit,
  getEntitlementMetricDefinitions,
  getSubscriptionFeatureRows,
  getYearlySavings,
} from "../subscriptionEntitlements";
import type { SubscriptionCatalogTier } from "../../types/subscription";

const inspirationFeature = vi.hoisted(() => ({ enabled: true }));

vi.mock("../../config/inspirations", () => ({
  inspirationsConfig: inspirationFeature,
}));

const t = (_key: string, fallback: string) => fallback;

describe("getEntitlementMetricDefinitions", () => {
  beforeEach(() => {
    inspirationFeature.enabled = true;
  });

  it("includes inspiration copy entitlements when enabled", () => {
    expect(getEntitlementMetricDefinitions(t, "zh-CN").map((metric) => metric.key))
      .toContain("inspiration_copies_monthly");
  });

  it("omits inspiration copy entitlements when disabled", () => {
    inspirationFeature.enabled = false;

    expect(getEntitlementMetricDefinitions(t, "zh-CN").map((metric) => metric.key))
      .not.toContain("inspiration_copies_monthly");
  });

  it("never advertises entitlements the backend does not enforce", () => {
    const keys = getEntitlementMetricDefinitions(t, "zh-CN").map((metric) => metric.key as string);
    expect(keys).not.toContain("context_tokens_limit");
    expect(keys).not.toContain("priority_queue_level");
    expect(keys).not.toContain("writing_credits_monthly");
    expect(keys).not.toContain("agent_runs_monthly");
    expect(keys).toContain("ai_conversations_per_day");
    // Materials stay: decomposition runs in production.
    expect(keys).toContain("material_decompositions_monthly");
    expect(keys).toContain("custom_skills_limit");
  });
});

describe("filterAvailableMetrics", () => {
  const plan = (entitlements: Record<string, unknown>) =>
    ({ entitlements }) as unknown as SubscriptionCatalogTier;

  it("drops metrics that some plan does not report yet", () => {
    const definitions = getEntitlementMetricDefinitions(t, "zh-CN");
    const plans = [
      plan({ active_projects_limit: 3, custom_skills_limit: 3 }),
      plan({ active_projects_limit: -1, custom_skills_limit: 20, ai_conversations_per_day: -1 }),
    ];

    const keys = filterAvailableMetrics(definitions, plans).map((metric) => metric.key);
    expect(keys).toContain("active_projects_limit");
    expect(keys).toContain("custom_skills_limit");
    expect(keys).not.toContain("ai_conversations_per_day");
  });

  it("keeps every metric when all plans report it", () => {
    const definitions = getEntitlementMetricDefinitions(t, "zh-CN");
    const full = Object.fromEntries(definitions.map((metric) => [metric.key, metric.key === "export_formats" ? [] : 1]));
    expect(filterAvailableMetrics(definitions, [plan(full)])).toHaveLength(definitions.length);
  });
});

describe("formatEntitlementLimit", () => {
  it("does not throw on a limit the API did not send", () => {
    expect(formatEntitlementLimit(undefined, "zh-CN", t)).toBe("-");
    expect(formatEntitlementLimit(-1, "zh-CN", t)).toBe("无限");
    expect(formatEntitlementLimit(1000, "en-US", t)).toBe("1,000");
  });
});

describe("getYearlySavings", () => {
  it("reports the saving and monthly equivalent of a discounted yearly price", () => {
    expect(getYearlySavings(4900, 39900)).toEqual({ amount: 18900, percent: 32, monthlyEquivalent: 3325 });
  });

  it("reports nothing when yearly is not cheaper or a price is missing", () => {
    expect(getYearlySavings(4900, 58800)).toBeNull();
    expect(getYearlySavings(undefined, 39900)).toBeNull();
    expect(getYearlySavings(0, 0)).toBeNull();
  });
});

describe("getSubscriptionFeatureRows", () => {
  const proFeatures = {
    ai_conversations_per_day: -1,
    context_window_tokens: 16384,
    file_versions_per_file: 100,
    max_projects: -1,
    export_formats: ["txt"],
    custom_prompts: true,
    materials_library_access: true,
    material_uploads: 5,
    material_decompositions: 5,
    custom_skills: 20,
    inspiration_copies_monthly: 100,
    priority_support: false,
  };

  it("lists only enforced limits with labels, never raw keys or unimplemented perks", () => {
    const rows = getSubscriptionFeatureRows(proFeatures, t, "zh-CN");
    const text = rows.map((row) => `${row.label}:${row.value}`).join("|");
    expect(rows.map((row) => row.key)).not.toEqual(
      expect.arrayContaining(["context_window_tokens", "priority_support", "custom_prompts", "materials_library_access"]),
    );
    expect(text).not.toMatch(/materials_library_access|priority|16384|优先支持|是|否/);
    expect(text).toContain("每日 AI 消息:无限");
    expect(text).toContain("素材拆解次数:5 次/月");
    expect(text).toContain("导出格式:TXT");
  });

  it("marks material limits as not included without library access", () => {
    const rows = getSubscriptionFeatureRows(
      { ...proFeatures, materials_library_access: false, material_uploads: 0, material_decompositions: 0 },
      t,
      "zh-CN",
    );
    expect(rows.find((row) => row.key === "material_uploads")?.value).toBe("不含");
    expect(rows.find((row) => row.key === "material_decompositions")?.value).toBe("不含");
  });
});
