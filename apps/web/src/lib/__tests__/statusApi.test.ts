import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  StatusApiError,
  fetchServiceStatus,
  formatSuccessRate,
  isStatusSnapshotExpired,
  type ServiceStatusResponse,
} from "../statusApi";

function buildValidSnapshot(): ServiceStatusResponse {
  return {
    generated_at: "2026-11-07T11:00:00+00:00",
    expires_at: "2026-11-07T11:02:00+00:00",
    window_start: "2026-11-07T10:00:00+00:00",
    window_minutes: 60,
    timezone: "Asia/Shanghai",
    status: "operational",
    success_rate: 1,
    history: [
      { date: "2026-11-06", status: "degraded", success_rate: 0.98 },
      { date: "2026-11-07", status: "operational", success_rate: 1 },
    ],
  };
}

describe("statusApi", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("fetches /api/status anonymously without Authorization headers or auth side effects", async () => {
    localStorage.setItem("access_token", "user-jwt-token");
    localStorage.setItem("refresh_token", "user-refresh-token");
    const payload = buildValidSnapshot();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await fetchServiceStatus();

    expect(result).toEqual(payload);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/status$/);
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBeNull();
    expect(init?.credentials).toBe("omit");
    expect(localStorage.getItem("access_token")).toBe("user-jwt-token");
  });

  it("throws StatusApiError on HTTP 503 without touching stored credentials", async () => {
    localStorage.setItem("access_token", "user-jwt-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Status data is temporarily unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchServiceStatus()).rejects.toBeInstanceOf(StatusApiError);
    expect(localStorage.getItem("access_token")).toBe("user-jwt-token");
  });

  it("rejects malformed payloads that omit required status fields", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "operational" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(fetchServiceStatus()).rejects.toBeInstanceOf(StatusApiError);
  });

  it("detects when a snapshot has reached or passed expires_at", () => {
    const snapshot = { expires_at: "2026-11-07T11:02:00+00:00" };
    expect(isStatusSnapshotExpired(snapshot, Date.parse("2026-11-07T11:01:59Z"))).toBe(false);
    expect(isStatusSnapshotExpired(snapshot, Date.parse("2026-11-07T11:02:00Z"))).toBe(true);
    expect(isStatusSnapshotExpired(snapshot, Date.parse("2026-11-07T11:05:00Z"))).toBe(true);
    expect(isStatusSnapshotExpired({ expires_at: "invalid-date" }, Date.now())).toBe(true);
  });

  it("formats 0..1 call success rates with boundary protection so <1 never rounds to 100% and >0 never rounds to 0%", () => {
    expect(formatSuccessRate(null)).toBeNull();
    expect(formatSuccessRate(undefined)).toBeNull();
    expect(formatSuccessRate(-0.01)).toBeNull();
    expect(formatSuccessRate(1.01)).toBeNull();
    expect(formatSuccessRate(1)).toBe("100%");
    expect(formatSuccessRate(0.9999)).toBe(">99.9%");
    expect(formatSuccessRate(0.9995)).toBe(">99.9%");
    expect(formatSuccessRate(0.999)).toBe("99.9%");
    expect(formatSuccessRate(0.98)).toBe("98%");
    expect(formatSuccessRate(0.985)).toBe("98.5%");
    expect(formatSuccessRate(0.001)).toBe("0.1%");
    expect(formatSuccessRate(0.0004)).toBe("<0.1%");
    expect(formatSuccessRate(0)).toBe("0%");
  });
});
