import { describe, expect, it } from "vitest";

import {
  buildUpgradeUrl,
  getUpgradePromptDefinition,
} from "../upgradeExperience";

describe("upgradeExperience", () => {
  it("returns modal strategy for chat quota blocking", () => {
    const strategy = getUpgradePromptDefinition("chat_quota_blocked");
    expect(strategy.surface).toBe("modal");
    expect(strategy.billingPath).toBe("/dashboard/billing");
  });

  it("returns page strategy for settings upgrade entry", () => {
    const strategy = getUpgradePromptDefinition("settings_subscription_upgrade");
    expect(strategy.surface).toBe("page");
    expect(strategy.pricingPath).toBe("/pricing");
  });

  it("returns modal strategy for project quota blocking", () => {
    const strategy = getUpgradePromptDefinition("project_quota_blocked");
    expect(strategy.surface).toBe("modal");
    expect(strategy.billingPath).toBe("/dashboard/billing");
  });

  it("returns modal strategy for material upload quota blocking", () => {
    const strategy = getUpgradePromptDefinition("material_upload_quota_blocked");
    expect(strategy.surface).toBe("modal");
    expect(strategy.pricingPath).toBe("/pricing");
  });

  it("returns modal strategy for skill creation quota blocking", () => {
    const strategy = getUpgradePromptDefinition("skill_create_quota_blocked");
    expect(strategy.surface).toBe("modal");
    expect(strategy.billingPath).toBe("/dashboard/billing");
  });

  it("returns modal strategy for material decompose quota blocking", () => {
    const strategy = getUpgradePromptDefinition("material_decompose_quota_blocked");
    expect(strategy.surface).toBe("modal");
    expect(strategy.pricingPath).toBe("/pricing");
  });

  it("appends source query when building upgrade URL", () => {
    const url = buildUpgradeUrl("/dashboard/billing", "chat_quota_blocked");
    expect(url).toBe("/dashboard/billing?source=chat_quota_blocked");
  });

  it("preserves existing query params when building upgrade URL", () => {
    const url = buildUpgradeUrl("/pricing?plan=pro", "project_quota_blocked");
    expect(url).toBe("/pricing?plan=pro&source=project_quota_blocked");
  });

  it("overrides existing source query param when rebuilding upgrade URL", () => {
    const url = buildUpgradeUrl("/pricing?plan=pro&source=old_source", "new_source");
    expect(url).toBe("/pricing?plan=pro&source=new_source");
  });

  it("keeps hash fragment while injecting source query", () => {
    const url = buildUpgradeUrl("/pricing?plan=pro#features", "billing_header_upgrade");
    expect(url).toBe("/pricing?plan=pro&source=billing_header_upgrade#features");
  });
});
