export const ADMIN_BASE = "/api/admin";

export function clampWindowDays(days: number): number {
  return Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
}
