import { beforeEach, describe, expect, it, vi } from "vitest";

import { getEntitlementMetricDefinitions } from "../subscriptionEntitlements";

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
