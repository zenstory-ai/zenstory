export type PaymentCycle = 'month' | 'year'

export type PaymentMethod = 'alipay' | 'card'

export type PaymentProvider = 'zpay' | 'creem' | (string & {})

export type PaymentCurrency = 'CNY' | 'USD' | (string & {})

// The backend returns the stored string as-is; values outside these unions
// (e.g. a row an operator edited by hand) must render, not crash.
export type PaymentOrderStatus = 'pending' | 'paid' | 'refunded' | 'disputed' | 'failed' | (string & {})

export type PaymentFulfillmentStatus = 'pending' | 'succeeded' | 'failed' | (string & {})

export interface PaymentMethodOption {
  method: PaymentMethod
  provider: PaymentProvider
  currency: PaymentCurrency
}

export interface PaymentOptions {
  enabled: boolean
  payment_methods: PaymentMethod[]
  methods?: PaymentMethodOption[]
}

export interface PaymentOrder {
  id: string
  provider?: PaymentProvider
  currency?: PaymentCurrency
  out_trade_no: string
  trade_no: string | null
  user_id: string
  plan_name: string
  plan_display_name: string
  cycle: PaymentCycle
  amount_cents: number
  payment_method: PaymentMethod | (string & {})
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
  method: 'POST' | 'GET'
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
