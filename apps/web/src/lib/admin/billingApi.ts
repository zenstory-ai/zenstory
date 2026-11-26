import { api } from "../apiClient";
import type {
  PlanUpdateRequest,
  RedemptionCode,
} from "../../types/admin";
import type { SubscriptionPlan } from "../../types/subscription";
import { ADMIN_BASE } from "./shared";
import type {
  CodeBatchCreateResponse,
  CodesListResponse,
  PaymentOrderSyncResponse,
  PaymentOrdersListResponse,
  SubscriptionsListResponse,
  UpdateSubscriptionResponse,
  UserSubscriptionDetailResponse,
} from "./types";

// ==================== 兑换码管理 API ====================

async function getCodes(params?: {
  page?: number;
  page_size?: number;
  tier?: string;
  is_active?: boolean;
}): Promise<CodesListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.tier) searchParams.set("tier", params.tier);
  if (params?.is_active !== undefined) searchParams.set("is_active", String(params.is_active));

  return api.get<CodesListResponse>(`${ADMIN_BASE}/codes?${searchParams.toString()}`);
}

async function createCode(data: {
  tier: string;
  duration_days: number;
  code_type?: "single_use" | "multi_use" | "single" | "multi";
  max_uses?: number;
  notes?: string;
}): Promise<RedemptionCode> {
  return api.post<RedemptionCode>(`${ADMIN_BASE}/codes`, data);
}

async function createCodesBatch(data: {
  tier: string;
  duration_days: number;
  count: number;
  code_type?: "single_use" | "multi_use" | "single" | "multi";
  max_uses?: number;
  notes?: string;
}): Promise<CodeBatchCreateResponse> {
  return api.post<CodeBatchCreateResponse>(`${ADMIN_BASE}/codes/batch`, data);
}

async function updateCode(
  id: string,
  data: { is_active?: boolean; notes?: string }
): Promise<RedemptionCode> {
  return api.put<RedemptionCode>(`${ADMIN_BASE}/codes/${id}`, data);
}

// ==================== 订阅与订单管理 API ====================

async function getSubscriptions(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  search?: string;
}): Promise<SubscriptionsListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.status) searchParams.set("status", params.status);
  if (params?.search) searchParams.set("search", params.search);

  return api.get<SubscriptionsListResponse>(
    `${ADMIN_BASE}/subscriptions?${searchParams.toString()}`
  );
}

async function getPaymentOrders(
  params: {
    page?: number;
    page_size?: number;
    status?: "pending" | "paid" | "refunded" | "disputed" | "failed";
    fulfillment_status?: "pending" | "succeeded" | "failed";
    payment_method?: "alipay" | "card";
    needs_attention?: boolean;
    search?: string;
    user_id?: string;
    include_needs_attention_total?: boolean;
  } = {}
): Promise<PaymentOrdersListResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.status) query.set("status", params.status);
  if (params.fulfillment_status) query.set("fulfillment_status", params.fulfillment_status);
  if (params.payment_method) query.set("payment_method", params.payment_method);
  if (params.needs_attention) query.set("needs_attention", "true");
  if (params.search) query.set("search", params.search);
  if (params.user_id) query.set("user_id", params.user_id);
  if (params.include_needs_attention_total === false) {
    query.set("include_needs_attention_total", "false");
  }
  return api.get<PaymentOrdersListResponse>(`${ADMIN_BASE}/payment-orders?${query.toString()}`);
}

async function syncPaymentOrder(orderId: string): Promise<PaymentOrderSyncResponse> {
  return api.post<PaymentOrderSyncResponse>(
    `${ADMIN_BASE}/payment-orders/${encodeURIComponent(orderId)}/sync`
  );
}

async function getUserSubscription(userId: string): Promise<UserSubscriptionDetailResponse> {
  return api.get<UserSubscriptionDetailResponse>(`${ADMIN_BASE}/subscriptions/${userId}`);
}

async function updateUserSubscription(
  userId: string,
  data: { plan_name?: string; duration_days?: number; status?: string }
): Promise<UpdateSubscriptionResponse> {
  return api.put<UpdateSubscriptionResponse>(`${ADMIN_BASE}/subscriptions/${userId}`, data);
}

// ==================== Subscription Plans API ====================

async function getPlans(): Promise<SubscriptionPlan[]> {
  return api.get<SubscriptionPlan[]>(`${ADMIN_BASE}/plans`);
}

async function updatePlan(planId: string, data: PlanUpdateRequest): Promise<SubscriptionPlan> {
  return api.put<SubscriptionPlan>(`${ADMIN_BASE}/plans/${planId}`, data);
}

export const billingAdminApi = {
  getCodes,
  createCode,
  createCodesBatch,
  updateCode,
  getSubscriptions,
  getPaymentOrders,
  syncPaymentOrder,
  getUserSubscription,
  updateUserSubscription,
  getPlans,
  updatePlan,
};
