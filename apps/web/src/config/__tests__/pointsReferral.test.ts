import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

const flags = [
  ["VITE_POINTS_ENABLED", async () => (await import("../points")).pointsConfig],
  ["VITE_REFERRAL_ENABLED", async () => (await import("../referral")).referralConfig],
] as const;

describe.each(flags)("%s", (name, load) => {
  it.each([undefined, "", "false", "0", "off", "invalid"])("stays off for %s", async (value) => {
    vi.stubEnv(name, value);
    vi.resetModules();
    expect((await load()).enabled).toBe(false);
  });

  it.each(["true", " TRUE\n", "1", "yes", "on"])("turns on for %s", async (value) => {
    vi.stubEnv(name, value);
    vi.resetModules();
    expect((await load()).enabled).toBe(true);
  });
});
