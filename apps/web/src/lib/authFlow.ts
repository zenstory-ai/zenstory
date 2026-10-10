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

// 登录后回跳目标（ProtectedRoute 写入的 router state `from`）的账号归属。
// 身份变化时 AuthIdentityQueryBoundary 会整棵重挂路由，组件内 state/ref 记不住
// 上一个账号，所以最近登录过的用户 id 放在模块内存里（按标签页、按页面加载）。
let lastSignedInUserId: string | null = null;

// 传 null 用于测试之间重置（同一测试文件共享模块状态）。
export function rememberSignedInUser(id: string | null): void {
  lastSignedInUserId = id;
}

export function lastSignedInUser(): string | null {
  return lastSignedInUserId;
}

export const RETURN_OWNER_KEY = 'fromUserId';

export type ReturnTarget = { pathname: string; search?: string; hash?: string; state?: object };

/**
 * 只把回跳目标交给它所属的账号：带归属且与当前登录账号不一致时丢弃，
 * 避免登出 A 再登入 B 时被送回 A 的项目。无归属（冷启动深链接）保持原行为。
 */
export function ownedReturnTarget(
  state: Record<string, unknown> | null | undefined,
  userId: string | null | undefined,
): ReturnTarget | undefined {
  const from = state?.from as Partial<ReturnTarget> | undefined;
  if (!from || typeof from.pathname !== 'string') return undefined;
  const owner = state?.[RETURN_OWNER_KEY];
  if (owner !== undefined && owner !== userId) return undefined;
  return from as ReturnTarget;
}
