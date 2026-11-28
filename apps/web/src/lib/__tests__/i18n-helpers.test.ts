import { describe, expect, it, vi, beforeEach } from "vitest";

const i18nextMock = vi.hoisted(() => ({
  language: "" as string | undefined,
  resolvedLanguage: "" as string | undefined,
}));

vi.mock("i18next", () => ({
  default: i18nextMock,
}));

describe("i18n helpers", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    i18nextMock.language = "";
    i18nextMock.resolvedLanguage = "";
  });

  it("normalizes locale variants to supported locales (only explicit zh* resolves to zh)", async () => {
    const { normalizeLocale, DEFAULT_LOCALE } = await import("../i18n-helpers");

    expect(DEFAULT_LOCALE).toBe("en");
    expect(normalizeLocale("zh")).toBe("zh");
    expect(normalizeLocale("zh-CN")).toBe("zh");
    expect(normalizeLocale("zh-TW")).toBe("zh");
    expect(normalizeLocale("ZH-Hans")).toBe("zh");
    expect(normalizeLocale("en-US")).toBe("en");
    expect(normalizeLocale("EN-gb")).toBe("en");
    expect(normalizeLocale(undefined)).toBe("en");
    expect(normalizeLocale("")).toBe("en");
    expect(normalizeLocale("fr-FR")).toBe("en");
    expect(normalizeLocale("id")).toBe("en");
    expect(normalizeLocale("pt-BR")).toBe("en");
    expect(normalizeLocale("es")).toBe("en");
  });

  it("prefers active i18next language first, then localStorage, then defaults to en", async () => {
    const { getLocale, getLocaleCode } = await import("../i18n-helpers");

    // Nothing set -> defaults to 'en'
    expect(getLocale()).toBe("en");
    expect(getLocaleCode()).toBe("en-US");

    // Reads zenstory-language from localStorage when i18next is unset
    localStorage.setItem("zenstory-language", "zh");
    expect(getLocale()).toBe("zh");
    expect(getLocaleCode()).toBe("zh-CN");

    // Unknown non-zh stored value falls back to 'en'
    localStorage.setItem("zenstory-language", "id");
    expect(getLocale()).toBe("en");

    // Active i18next language takes precedence over localStorage
    localStorage.setItem("zenstory-language", "zh");
    i18nextMock.language = "en-US";
    expect(getLocale()).toBe("en");
    expect(getLocaleCode()).toBe("en-US");

    i18nextMock.resolvedLanguage = "zh-CN";
    expect(getLocale()).toBe("zh");
    expect(getLocaleCode()).toBe("zh-CN");

    // Unknown non-zh active i18next language falls back to 'en'
    i18nextMock.resolvedLanguage = "pt-BR";
    expect(getLocale()).toBe("en");
  });

  it("persists and switches locale only when changing to a different language", async () => {
    const { switchLocale } = await import("../i18n-helpers");
    const changeLanguage = vi.fn().mockResolvedValue(undefined);

    await switchLocale({ changeLanguage }, "zh", "zh");
    expect(changeLanguage).not.toHaveBeenCalled();
    expect(localStorage.getItem("zenstory-language")).toBeNull();

    await switchLocale({ changeLanguage }, "zh", "en");
    expect(localStorage.getItem("zenstory-language")).toBe("en");
    expect(changeLanguage).toHaveBeenCalledWith("en");
  });
});
