import { getApiBase } from "./apiClient";

export type ObservationStatus = "operational" | "degraded" | "unknown";

export interface DailyStatusObservation {
  date: string;
  status: ObservationStatus;
  success_rate: number | null;
}

export interface ServiceStatusResponse {
  generated_at: string;
  expires_at: string;
  window_start: string;
  window_minutes: number;
  timezone: string;
  status: ObservationStatus;
  success_rate: number | null;
  history: DailyStatusObservation[];
}

export class StatusApiError extends Error {
  public readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "StatusApiError";
    this.status = status;
  }
}

const VALID_OBSERVATION_STATUSES = new Set<ObservationStatus>([
  "operational",
  "degraded",
  "unknown",
]);

function isObservationStatus(value: unknown): value is ObservationStatus {
  return typeof value === "string" && VALID_OBSERVATION_STATUSES.has(value as ObservationStatus);
}

function isValidSuccessRate(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1)
  );
}

function isDailyObservation(value: unknown): value is DailyStatusObservation {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.date === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(item.date) &&
    isObservationStatus(item.status) &&
    isValidSuccessRate(item.success_rate)
  );
}

function isServiceStatusResponse(value: unknown): value is ServiceStatusResponse {
  if (!value || typeof value !== "object") return false;
  const payload = value as Record<string, unknown>;
  return (
    typeof payload.generated_at === "string" &&
    typeof payload.expires_at === "string" &&
    typeof payload.window_start === "string" &&
    typeof payload.window_minutes === "number" &&
    typeof payload.timezone === "string" &&
    isObservationStatus(payload.status) &&
    isValidSuccessRate(payload.success_rate) &&
    Array.isArray(payload.history) &&
    payload.history.every(isDailyObservation)
  );
}

/**
 * Anonymous fetch for public service status observations (`GET /api/status`).
 * Never attaches auth tokens or triggers token refresh / login redirects.
 */
export async function fetchServiceStatus(signal?: AbortSignal): Promise<ServiceStatusResponse> {
  const response = await fetch(`${getApiBase()}/api/status`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    cache: "no-store",
    credentials: "omit",
    signal,
  });

  if (!response.ok) {
    throw new StatusApiError(
      response.status,
      `Status request failed with HTTP ${response.status}`,
    );
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new StatusApiError(response.status, "Invalid status JSON response");
  }

  if (!isServiceStatusResponse(data)) {
    throw new StatusApiError(response.status, "Malformed status response payload");
  }

  return data;
}

export function isStatusSnapshotExpired(
  snapshot: Pick<ServiceStatusResponse, "expires_at">,
  nowMs: number = Date.now(),
): boolean {
  const expiresMs = Date.parse(snapshot.expires_at);
  return !Number.isFinite(expiresMs) || nowMs >= expiresMs;
}

export function formatSuccessRate(rate: number | null | undefined): string | null {
  if (typeof rate !== "number" || !Number.isFinite(rate) || rate < 0 || rate > 1) {
    return null;
  }
  if (rate === 1) return "100%";
  if (rate === 0) return "0%";
  const percent = rate * 100;
  if (percent > 99.9) return ">99.9%";
  if (percent < 0.1) return "<0.1%";
  return `${percent.toFixed(1).replace(/\.0$/, "")}%`;
}
