export type PaymentCycle = 'month' | 'year'

export type PaymentMethod = 'alipay'

// The backend returns the stored string as-is; values outside these unions
// (e.g. a row an operator edited by hand) must render, not crash.
export type PaymentOrderStatus = 'pending' | 'paid' | (string & {})

export type PaymentFulfillmentStatus = 'pending' | 'succeeded' | 'failed' | (string & {})

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
  upgrade_source?: string | null
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
  upgrade_source?: string
}

export interface CreatePaymentOrderResponse {
  order: PaymentOrder
  checkout: PaymentCheckout
}
