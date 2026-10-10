import { describe, expect, it } from "vitest";
import {
  consumeOAuthPlanIntent,
  lastSignedInUser,
  normalizePlanIntent,
  ownedReturnTarget,
  rememberSignedInUser,
  RETURN_OWNER_KEY,
  saveOAuthPlanIntent,
} from "../authFlow";

describe("ownedReturnTarget", () => {
  const from = { pathname: "/project/pA", search: "?file=f1" };

  it("keeps an unowned target (cold deep link)", () => {
    expect(ownedReturnTarget({ from }, "user-b")).toBe(from);
  });

  it("keeps a target owned by the signed-in account", () => {
    expect(ownedReturnTarget({ from, [RETURN_OWNER_KEY]: "user-a" }, "user-a")).toBe(from);
  });

  it("drops a target owned by another account or an unknown account", () => {
    expect(ownedReturnTarget({ from, [RETURN_OWNER_KEY]: "user-a" }, "user-b")).toBeUndefined();
    expect(ownedReturnTarget({ from, [RETURN_OWNER_KEY]: "user-a" }, undefined)).toBeUndefined();
  });

  it("drops missing or malformed targets", () => {
    expect(ownedReturnTarget(null, "user-a")).toBeUndefined();
    expect(ownedReturnTarget({}, "user-a")).toBeUndefined();
    expect(ownedReturnTarget({ from: { pathname: 42 } }, "user-a")).toBeUndefined();
  });

  it("remembers the last signed-in account", () => {
    rememberSignedInUser("user-a");
    expect(lastSignedInUser()).toBe("user-a");
    rememberSignedInUser("user-b");
    expect(lastSignedInUser()).toBe("user-b");
    rememberSignedInUser(null);
    expect(lastSignedInUser()).toBeNull();
  });
});

describe("normalizePlanIntent", () => {
  it("returns normalized value for known plans", () => {
    expect(normalizePlanIntent("PRO")).toBe("pro");
    expect(normalizePlanIntent(" free ")).toBe("free");
  });

  it("returns null for unknown plan values", () => {
    expect(normalizePlanIntent("enterprise")).toBeNull();
    expect(normalizePlanIntent("studio")).toBeNull();
    expect(normalizePlanIntent("")).toBeNull();
    expect(normalizePlanIntent(null)).toBeNull();
    expect(normalizePlanIntent(undefined)).toBeNull();
  });
});

describe("OAuth plan intent", () => {
  it("normalizes and consumes the intent once", () => {
    sessionStorage.clear();
    saveOAuthPlanIntent(" PRO ");
    expect(consumeOAuthPlanIntent()).toBe("pro");
    expect(consumeOAuthPlanIntent()).toBeNull();
  });

  it("does not persist unknown intent", () => {
    sessionStorage.clear();
    saveOAuthPlanIntent("enterprise");
    expect(consumeOAuthPlanIntent()).toBeNull();
  });
});
