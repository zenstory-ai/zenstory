import { describe, expect, it } from "vitest";

import {
  filterAvailableMetrics,
  formatEntitlementLimit,
  getEntitlementMetricDefinitions,
  getLocalizedPlanDisplayName,
  getSubscriptionFeatureRows,
  getSubscriptionStatusLine,
  getYearlySavings,
} from "../subscriptionEntitlements";
import type { SubscriptionCatalogTier } from "../../types/subscription";

const t = (_key: string, fallback: string, options?: Record<string, unknown>) =>
  options
    ? fallback.replace(/{{\s*(\w+)\s*}}/g, (_, name: string) => String(options[name] ?? ""))
    : fallback;

describe("getEntitlementMetricDefinitions", () => {
  it("omits retired inspiration copy entitlements", () => {
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
    // TXT is the only format on every plan, so a row would carry no information.
    expect(keys).not.toContain("export_formats");
  });

  it("writes unlimited on its own, counts messages in 条, and marks a 0 allowance as not included", () => {
    const definitions = getEntitlementMetricDefinitions(t, "zh-CN");
    const valueOf = (key: string, entitlements: Record<string, unknown>) =>
      definitions
        .find((metric) => metric.key === key)!
        .value({ entitlements } as unknown as SubscriptionCatalogTier);

    expect(valueOf("ai_conversations_per_day", { ai_conversations_per_day: 10 })).toBe("10 条/天");
    expect(valueOf("ai_conversations_per_day", { ai_conversations_per_day: -1 })).toBe("不限");
    expect(valueOf("active_projects_limit", { active_projects_limit: -1 })).toBe("不限");
    expect(valueOf("material_decompositions_monthly", { material_decompositions_monthly: 0 })).toBe("不含");
    expect(valueOf("material_decompositions_monthly", { material_decompositions_monthly: 5 })).toBe("5 次/月");
  });
});

describe("getLocalizedPlanDisplayName", () => {
  const legacyFree = { display_name: "免费试用", display_name_en: "Free Trial" };

  it("names the free and Pro tiers by the glossary whatever the stored name says", () => {
    expect(getLocalizedPlanDisplayName({ ...legacyFree, tier: "free" }, "zh-CN")).toBe("免费版");
    expect(getLocalizedPlanDisplayName({ ...legacyFree, tier: "free" }, "en-US")).toBe("Free");
    expect(getLocalizedPlanDisplayName({ display_name: "专业版", display_name_en: "Pro Plan", tier: "pro" }, "zh-CN")).toBe("Pro");
  });

  it("keeps the stored name when no tier is given or the tier is not in the glossary", () => {
    expect(getLocalizedPlanDisplayName(legacyFree, "zh-CN")).toBe("免费试用");
    expect(getLocalizedPlanDisplayName(legacyFree, "en-US")).toBe("Free Trial");
    expect(getLocalizedPlanDisplayName({ display_name: "团队版", display_name_en: "Team", tier: "team" }, "en-US")).toBe("Team");
  });
});

describe("getSubscriptionStatusLine", () => {
  it("shows no status word for the free plan", () => {
    expect(getSubscriptionStatusLine({ tier: "free", status: "none", current_period_end: null, days_remaining: null }, t)).toBeNull();
    expect(getSubscriptionStatusLine({ tier: "free", status: "active", current_period_end: null, days_remaining: null }, t)).toBeNull();
  });

  it("shows when an active Pro plan ends", () => {
    localStorage.setItem("zenstory-language", "zh");
    try {
      expect(
        getSubscriptionStatusLine(
          { tier: "pro", status: "active", current_period_end: "2026-11-07T04:00:00Z", days_remaining: 30 },
          t,
        ),
      ).toBe("有效期至 2026/11/07 · 剩余 30 天");

      localStorage.setItem("zenstory-language", "en");
      expect(
        getSubscriptionStatusLine(
          { tier: "pro", status: "active", current_period_end: "2026-11-07T04:00:00Z", days_remaining: 30 },
          t,
        ),
      ).toBe("有效期至 11/07/2026 · 剩余 30 天");
    } finally {
      localStorage.clear();
    }
  });

  it("says an expired plan fell back to Free", () => {
    expect(
      getSubscriptionStatusLine({ tier: "pro", status: "expired", current_period_end: null, days_remaining: null }, t),
    ).toBe("已到期，现在是免费版");
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
    const full = Object.fromEntries(definitions.map((metric) => [metric.key, 1]));
    expect(filterAvailableMetrics(definitions, [plan(full)])).toHaveLength(definitions.length);
  });
});

describe("formatEntitlementLimit", () => {
  it("does not throw on a limit the API did not send", () => {
    expect(formatEntitlementLimit(undefined, "zh-CN", t)).toBe("-");
    expect(formatEntitlementLimit(-1, "zh-CN", t)).toBe("不限");
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

  it("counts skills and versions in their own unit, not projects", () => {
    const english: Record<string, string> = {
      "dashboard:billing.projectsUnit": "projects",
      "dashboard:billing.skillsUnit": "skills",
      "dashboard:billing.versionsUnit": "versions",
    };
    const tEn = (key: string, fallback: string) => english[key] ?? fallback;
    const rows = Object.fromEntries(
      getSubscriptionFeatureRows({ ...proFeatures, max_projects: 3 }, tEn, "en-US").map((row) => [row.key, row.value]),
    );

    expect(rows.max_projects).toBe("3 projects");
    expect(rows.custom_skills).toBe("20 skills");
    expect(rows.file_versions_per_file).toBe("100 versions");
  });

  it("lists only enforced limits with labels, never raw keys or unimplemented perks", () => {
    const rows = getSubscriptionFeatureRows(proFeatures, t, "zh-CN");
    const text = rows.map((row) => `${row.label}:${row.value}`).join("|");
    expect(rows.map((row) => row.key)).not.toEqual(
      expect.arrayContaining(["context_window_tokens", "priority_support", "custom_prompts", "materials_library_access"]),
    );
    expect(text).not.toMatch(/materials_library_access|priority|16384|优先支持|是|否/);
    expect(text).toContain("每日 AI 消息:不限");
    expect(text).toContain("素材拆解:5 次/月");
    expect(text).toContain("每个文件保留的历史版本:100 个");
  });

  it("never lists material uploads (not metered) or export formats (TXT on every plan)", () => {
    const keys = getSubscriptionFeatureRows(proFeatures, t, "zh-CN").map((row) => row.key);
    expect(keys).not.toContain("material_uploads");
    expect(keys).not.toContain("export_formats");
  });

  it("counts daily AI messages in 条", () => {
    const rows = getSubscriptionFeatureRows({ ...proFeatures, ai_conversations_per_day: 10 }, t, "zh-CN");
    expect(rows.find((row) => row.key === "ai_conversations_per_day")?.value).toBe("10 条/天");
  });

  it("marks material limits as not included without library access", () => {
    const rows = getSubscriptionFeatureRows(
      { ...proFeatures, materials_library_access: false, material_uploads: 0, material_decompositions: 0 },
      t,
      "zh-CN",
    );
    expect(rows.find((row) => row.key === "material_decompositions")?.value).toBe("不含");
  });
});


describe("catalog and settings entitlement consistency", () => {
  const en = (key: string, fallback: string) => ({
    "dashboard:billing.messagesPerDay": "/day",
    "dashboard:billing.timesPerMonth": "/month",
    "dashboard:billing.versionsUnit": "versions",
  }[key] ?? fallback);

  it("compares the actual per-file version limit and hides it for older APIs", () => {
    const definitions = getEntitlementMetricDefinitions(t, "zh");
    const metric = definitions.find((item) => item.key === "file_versions_per_file");
    expect(metric).toBeDefined();
    const tier = { entitlements: { file_versions_per_file: 42 } } as SubscriptionCatalogTier;
    expect(metric?.value(tier)).toBe("42 个");
    expect(metric?.compareValue(tier)).toBe(42);
    expect(filterAvailableMetrics(definitions, [tier]).map((item) => item.key)).toEqual(["file_versions_per_file"]);
    expect(filterAvailableMetrics(definitions, [tier, { entitlements: {} } as SubscriptionCatalogTier])).toEqual([]);
  });

  it("joins slash units without spaces in both comparison and settings", () => {
    const definitions = getEntitlementMetricDefinitions(en, "en");
    const tier = { entitlements: { ai_conversations_per_day: 3, material_decompositions_monthly: 7 } } as SubscriptionCatalogTier;
    expect(definitions.find((item) => item.key === "ai_conversations_per_day")?.value(tier)).toBe("3/day");
    expect(definitions.find((item) => item.key === "material_decompositions_monthly")?.value(tier)).toBe("7/month");
    const rows = getSubscriptionFeatureRows({ ai_conversations_per_day: 3, material_decompositions: 7, materials_library_access: true, file_versions_per_file: 42 }, en, "en");
    expect(rows.find((item) => item.key === "ai_conversations_per_day")?.value).toBe("3/day");
    expect(rows.find((item) => item.key === "material_decompositions")?.value).toBe("7/month");
    expect(rows.find((item) => item.key === "file_versions_per_file")?.value).toBe("42 versions");
  });
});
