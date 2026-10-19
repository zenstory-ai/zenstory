import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("inspirationsConfig", () => {
  it.each([undefined, "", "false", "0", "off", "invalid"])(
    "keeps the optional library disabled for %s",
    async (value) => {
      vi.stubEnv("VITE_INSPIRATIONS_ENABLED", value);
      vi.resetModules();
      const { inspirationsConfig } = await import("../inspirations");
      expect(inspirationsConfig.enabled).toBe(false);
    },
  );

  it.each(["true", " TRUE\n", "1", "yes", "on"])(
    "explicitly enables the library for %s",
    async (value) => {
      vi.stubEnv("VITE_INSPIRATIONS_ENABLED", value);
      vi.resetModules();
      const { inspirationsConfig } = await import("../inspirations");
      expect(inspirationsConfig.enabled).toBe(true);
    },
  );
});
