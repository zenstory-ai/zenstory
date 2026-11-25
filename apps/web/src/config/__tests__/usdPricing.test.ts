import { describe, expect, it } from "vitest";
import { usesUsdPricing } from "../usdPricing";

describe("usesUsdPricing", () => {
  it("uses CNY for Chinese locales", () => {
    expect(usesUsdPricing("zh")).toBe(false);
    expect(usesUsdPricing("zh-CN")).toBe(false);
    expect(usesUsdPricing("zh-TW")).toBe(false);
    expect(usesUsdPricing("ZH-Hans")).toBe(false);
  });

  it("uses USD for English, other non-Chinese locales, and unset language", () => {
    expect(usesUsdPricing("en")).toBe(true);
    expect(usesUsdPricing("en-US")).toBe(true);
    expect(usesUsdPricing("ja")).toBe(true);
    expect(usesUsdPricing("fr-FR")).toBe(true);
    expect(usesUsdPricing(undefined)).toBe(true);
    expect(usesUsdPricing("")).toBe(true);
  });
});
