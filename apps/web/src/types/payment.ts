export type PaymentCycle = 'month' | 'year'

export type PaymentMethod = 'alipay'

export type PaymentOrderStatus = 'pending' | 'paid'

export type PaymentFulfillmentStatus = 'pending' | 'succeeded' | 'failed'

export interface PaymentOptions {
  enabled: boolean
  payment_methods: PaymentMethod[]
}

export interface PaymentOrder {
  id: string
  out_trade_no: string
  trade_no: string | null
  user_id: string
  plan_name: string
  plan_display_name: string
  cycle: PaymentCycle
  amount_cents: number
  payment_method: PaymentMethod
  status: PaymentOrderStatus
  fulfillment_status: PaymentFulfillmentStatus
  created_at: string
  paid_at: string | null
  fulfilled_at: string | null
  failure_reason: string | null
}

export interface PaymentCheckout {
  action: string
  method: 'POST'
  fields: Record<string, string>
}

export interface CreatePaymentOrderRequest {
  plan_name: 'pro'
  cycle: PaymentCycle
  payment_method: PaymentMethod
}

export interface CreatePaymentOrderResponse {
  order: PaymentOrder
  checkout: PaymentCheckout
}
