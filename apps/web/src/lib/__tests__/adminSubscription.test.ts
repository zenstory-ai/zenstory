import { describe, expect, it } from "vitest";
import { buildSubscriptionUpdate } from "../adminSubscription";

const current = { plan_name: "free", status: "active" };

describe("buildSubscriptionUpdate", () => {
  it("sends plan and days together for a plan change or extension", () => {
    expect(buildSubscriptionUpdate(current, { plan_name: "pro", duration_days: 30, status: "active" }))
      .toEqual({ ok: true, payload: { plan_name: "pro", duration_days: 30 } });
    expect(buildSubscriptionUpdate(current, { plan_name: "free", duration_days: 7, status: "active" }))
      .toEqual({ ok: true, payload: { plan_name: "free", duration_days: 7 } });
  });

  it("needs days when the plan changes", () => {
    expect(buildSubscriptionUpdate(current, { plan_name: "pro", duration_days: 0, status: "active" }))
      .toEqual({ ok: false, reason: "durationRequired" });
  });

  it("sends only the status for a status change", () => {
    expect(buildSubscriptionUpdate(current, { plan_name: "free", duration_days: 0, status: "cancelled" }))
      .toEqual({ ok: true, payload: { status: "cancelled" } });
  });

  it("reports when nothing changed", () => {
    expect(buildSubscriptionUpdate(current, { plan_name: "free", duration_days: 0, status: "active" }))
      .toEqual({ ok: false, reason: "noChanges" });
  });
});
