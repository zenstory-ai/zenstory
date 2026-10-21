import { beforeEach, describe, expect, it } from "vitest";
import { getAcquisition, rememberAcquisition } from "../acquisition";

const at = (pathname: string, search = "") => ({ pathname, search, hostname: "app.zenstory.ai" });
const DAY = 24 * 60 * 60 * 1000;

describe("acquisition", () => {
  beforeEach(() => localStorage.clear());

  it("keeps campaign codes, the referrer host and a landing path without identifiers", () => {
    rememberAcquisition(
      at("/project/3f2b6c1e-8a1d-4c3e-9b7a-2d5e6f708192", "?utm_source=Newsletter&utm_medium=email&email=a@b.c"),
      "https://www.google.com/search?q=private",
      1_000,
    );

    expect(getAcquisition(2_000)).toEqual({
      utm_source: "newsletter",
      utm_medium: "email",
      referrer_host: "www.google.com",
      landing_path: "/project/:id",
    });
  });

  it("keeps the first visit with a signal, but lets a signal replace a direct visit", () => {
    rememberAcquisition(at("/"), "", 1_000);
    rememberAcquisition(at("/register", "?source=org_header"), "https://zenstory.ai/", 2_000);
    rememberAcquisition(at("/"), "https://chatgpt.com/", 3_000);

    expect(getAcquisition(4_000)).toEqual({ source: "org_header", referrer_host: "zenstory.ai", landing_path: "/register" });
  });

  it("ignores our own redirect hops and expires after 30 days", () => {
    rememberAcquisition(at("/auth/callback"), "https://accounts.google.com/", 1_000);
    expect(getAcquisition(1_000)).toBeUndefined();

    rememberAcquisition(at("/"), "https://app.zenstory.ai/dashboard", 1_000);
    expect(getAcquisition(2_000)).toEqual({ landing_path: "/" });
    expect(getAcquisition(1_000 + 30 * DAY)).toBeUndefined();
  });
});
