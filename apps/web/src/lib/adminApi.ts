/**
 * Admin API barrel — composes domain modules under `./admin/*` and re-exports
 * every public type so existing callers keep importing from `lib/adminApi`.
 */
import { billingAdminApi } from "./admin/billingApi";
import { contentAdminApi } from "./admin/contentApi";
import { metricsAdminApi } from "./admin/metricsApi";
import { usersAdminApi } from "./admin/usersApi";

export * from "./admin/types";

export const adminApi = {
  ...usersAdminApi,
  ...contentAdminApi,
  ...billingAdminApi,
  ...metricsAdminApi,
};
