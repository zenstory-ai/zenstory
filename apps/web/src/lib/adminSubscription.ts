/** Payload rules for an admin plan/status change, shared by the list modal and the user page. */

export interface SubscriptionChangeForm {
  plan_name: string;
  duration_days: number;
  status: string;
}

export interface SubscriptionUpdatePayload {
  plan_name?: string;
  duration_days?: number;
  status?: string;
}

export type SubscriptionChangeResult =
  | { ok: true; payload: SubscriptionUpdatePayload }
  | { ok: false; reason: "durationRequired" | "noChanges" };

/**
 * The API grants a plan for a number of days, so a plan change (or an
 * extension) needs days > 0; a status-only change sends just the status.
 */
export function buildSubscriptionUpdate(
  current: { plan_name: string; status: string },
  form: SubscriptionChangeForm,
): SubscriptionChangeResult {
  const payload: SubscriptionUpdatePayload = {};
  if (form.status !== current.status) {
    payload.status = form.status;
  }
  const isPlanChanged = form.plan_name !== current.plan_name;
  if (isPlanChanged || form.duration_days > 0) {
    if (form.duration_days <= 0) {
      return { ok: false, reason: "durationRequired" };
    }
    payload.plan_name = form.plan_name;
    payload.duration_days = form.duration_days;
  }
  if (Object.keys(payload).length === 0) {
    return { ok: false, reason: "noChanges" };
  }
  return { ok: true, payload };
}
