export const PAYMENT_STATUSES = ["creating", "open", "creation_unknown", "paid", "failed", "expired"] as const;
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number];

export interface Product {
  id: string;
  name: string;
  description: string;
  unit_amount: number;
  currency: string;
}

export interface Payment {
  id: string;
  product_id: string;
  quantity: number;
  unit_amount: number;
  amount: number;
  currency: string;
  status: PaymentStatus;
  stripe_checkout_session_id: string | null;
  stripe_payment_intent_id: string | null;
  checkout_expires_at: string | null;
  failure_reason: string | null;
  paid_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaymentEvent {
  from_status: PaymentStatus | null;
  to_status: PaymentStatus;
  source: string;
  reason: string | null;
  created_at: string;
}

export interface PaymentDetail extends Payment {
  history: PaymentEvent[];
}

export interface PaymentPage {
  items: Payment[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaymentStats {
  total: number;
  by_status: Record<PaymentStatus, number>;
  paid_totals: { currency: string; amount: number }[];
}

export interface CheckoutRequest {
  product_id: string;
  quantity: number;
  customer_email?: string;
}

export interface CheckoutResponse {
  payment_id: string;
  checkout_session_id: string | null;
  checkout_url: string | null;
  checkout_expires_at: string | null;
  amount: number;
  currency: string;
  status: PaymentStatus;
}

export interface RagSource {
  number: number;
  title: string;
  source_type: string;
  source_key: string;
  score: number;
  cited: boolean;
}

export interface RagAnswer {
  answer: string;
  sources: RagSource[];
  used_llm: boolean;
  warnings?: string[];
}

export interface ApiHealth {
  status: string;
  stripe_mode: string;
}

export interface RagHealth {
  ollama: string;
  qdrant: string;
  status: string;
}
