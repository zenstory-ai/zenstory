export const PLAN_INTENTS = ["free", "pro"] as const;

export type PlanIntent = (typeof PLAN_INTENTS)[number];

const PLAN_INTENT_SET = new Set<string>(PLAN_INTENTS);
const OAUTH_PLAN_INTENT_KEY = 'oauth_plan_intent';

/**
 * Normalize plan intent query param into a known value.
 *
 * Unknown values are treated as null to avoid unsafe redirects.
 */
export function normalizePlanIntent(rawPlan: string | null | undefined): PlanIntent | null {
  if (!rawPlan) return null;
  const normalized = rawPlan.trim().toLowerCase();
  if (!PLAN_INTENT_SET.has(normalized)) return null;
  return normalized as PlanIntent;
}

export function saveOAuthPlanIntent(rawPlan: string | null | undefined): void {
  const plan = normalizePlanIntent(rawPlan);
  if (plan) {
    sessionStorage.setItem(OAUTH_PLAN_INTENT_KEY, plan);
  } else {
    sessionStorage.removeItem(OAUTH_PLAN_INTENT_KEY);
  }
}

export function consumeOAuthPlanIntent(): PlanIntent | null {
  const plan = normalizePlanIntent(sessionStorage.getItem(OAUTH_PLAN_INTENT_KEY));
  sessionStorage.removeItem(OAUTH_PLAN_INTENT_KEY);
  return plan;
}

// Private router-entry provenance for a successful Login continuation.
export const LOGIN_ATTEMPT_KEY = 'zenstoryLoginAttempt';
export type LoginAttempt = { kind: 'login'; id: string };
