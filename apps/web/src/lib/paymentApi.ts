import { api } from './apiClient'
import type {
  CreatePaymentOrderRequest,
  CreatePaymentOrderResponse,
  PaymentOptions,
  PaymentOrder,
} from '../types/payment'

export const paymentQueryKeys = {
  options: () => ['payment-options'] as const,
  order: (outTradeNo: string) => ['payment-order', outTradeNo] as const,
}

export const paymentApi = {
  getOptions: () => api.get<PaymentOptions>('/api/v1/payments/options'),
  createOrder: (request: CreatePaymentOrderRequest) =>
    api.post<CreatePaymentOrderResponse>('/api/v1/payments/orders', request),
  getOrder: (outTradeNo: string) =>
    api.get<PaymentOrder>(`/api/v1/payments/orders/${encodeURIComponent(outTradeNo)}`),
}

export default paymentApi
