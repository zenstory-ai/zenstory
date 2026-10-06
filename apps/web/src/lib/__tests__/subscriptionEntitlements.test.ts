import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  filterAvailableMetrics,
  getEntitlementMetricDefinitions,
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
