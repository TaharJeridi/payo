import { describe, expect, it, vi } from "vitest";

import { apiError, fakeApi, json, VALID_KEY } from "../test/utils";
import { IdempotencyKeys, checkoutWithRetry } from "./idempotency";

const REQUEST = { product_id: "payflow-pro", quantity: 1 };
const OK = { payment_id: "p1", status: "open", amount: 2990, currency: "eur", checkout_url: null };

describe("IdempotencyKeys", () => {
  it("keeps the key while parameters are unchanged and renews it after success or a change", () => {
    let n = 0;
    const keys = new IdempotencyKeys(() => `key-${++n}`);

    const first = keys.keyFor(REQUEST);
    expect(keys.keyFor({ ...REQUEST })).toBe(first);
    expect(keys.keyFor({ ...REQUEST, customer_email: " A@B.it " })).toBe("key-2");
    expect(keys.keyFor({ ...REQUEST, customer_email: "a@b.it" })).toBe("key-2");
    expect(keys.keyFor({ ...REQUEST, quantity: 2 })).toBe("key-3");
    keys.completed();
    expect(keys.keyFor({ ...REQUEST, quantity: 2 })).toBe("key-4");
  });
});

describe("checkoutWithRetry", () => {
  it("retries unknown outcomes and in-flight conflicts with the same key, honouring Retry-After", async () => {
    const responses = [
      () => apiError(503, "payment_outcome_unknown", "lost", { "Retry-After": "5" }),
      () => apiError(409, "idempotency_operation_in_progress", "busy", { "Retry-After": "2" }),
      () => json(OK, 200),
    ];
    const { client, calls } = fakeApi({ "POST /payments/checkout": () => responses.shift()!() });
    const sleep = vi.fn(async (_ms: number) => undefined);

    const result = await checkoutWithRetry(client.withApiKey(VALID_KEY), REQUEST, "same-key", { sleep });

    expect(result.replayed).toBe(true);
    expect(calls.map((c) => c.headers["Idempotency-Key"])).toEqual(["same-key", "same-key", "same-key"]);
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([5000, 2000]);
  });

  it("never retries definitive errors", async () => {
    const { client, calls } = fakeApi({
      "POST /payments/checkout": () => apiError(409, "idempotency_key_reused"),
    });

    await expect(
      checkoutWithRetry(client.withApiKey(VALID_KEY), REQUEST, "k", { sleep: async () => undefined }),
    ).rejects.toMatchObject({ code: "idempotency_key_reused" });
    expect(calls).toHaveLength(1);
  });

  it("gives up after the maximum number of attempts and caps the wait", async () => {
    const { client, calls } = fakeApi({
      "POST /payments/checkout": () => apiError(503, "payment_outcome_unknown", "lost", { "Retry-After": "120" }),
    });
    const sleep = vi.fn(async (_ms: number) => undefined);

    await expect(
      checkoutWithRetry(client.withApiKey(VALID_KEY), REQUEST, "k", { sleep, maxAttempts: 3 }),
    ).rejects.toMatchObject({ code: "payment_outcome_unknown" });
    expect(calls).toHaveLength(3);
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([10_000, 10_000]);
  });
});
