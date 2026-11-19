import { ApiError, type ApiClient, type CheckoutResult } from "../api/client";
import type { CheckoutRequest } from "../api/types";

const RETRYABLE_CODES = new Set(["idempotency_operation_in_progress", "payment_outcome_unknown"]);
const MAX_WAIT_SECONDS = 10;

export function isRetryable(error: unknown): error is ApiError {
  return error instanceof ApiError && (RETRYABLE_CODES.has(error.code) || error.code === "network_error");
}

export function requestFingerprint(request: CheckoutRequest): string {
  return JSON.stringify([request.product_id, request.quantity, request.customer_email?.trim().toLowerCase() ?? null]);
}

export class IdempotencyKeys {
  private current: { fingerprint: string; key: string } | null = null;

  constructor(private readonly generate: () => string = () => crypto.randomUUID()) {}

  keyFor(request: CheckoutRequest): string {
    const fingerprint = requestFingerprint(request);
    if (this.current?.fingerprint !== fingerprint) {
      this.current = { fingerprint, key: this.generate() };
    }
    return this.current.key;
  }

  completed(): void {
    this.current = null;
  }
}

export interface RetryOptions {
  maxAttempts?: number;
  sleep?: (ms: number) => Promise<void>;
  onRetry?: (attempt: number, error: ApiError) => void;
}

export async function checkoutWithRetry(
  client: ApiClient,
  request: CheckoutRequest,
  idempotencyKey: string,
  options: RetryOptions = {},
): Promise<CheckoutResult> {
  const maxAttempts = options.maxAttempts ?? 4;
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await client.checkout(request, idempotencyKey);
    } catch (error) {
      if (!isRetryable(error) || attempt >= maxAttempts) throw error;
      options.onRetry?.(attempt, error);
      const seconds = Math.min(error.retryAfterSeconds ?? attempt * 2, MAX_WAIT_SECONDS);
      await sleep(seconds * 1000);
    }
  }
}
