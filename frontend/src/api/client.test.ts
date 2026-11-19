import { describe, expect, it } from "vitest";

import { apiError, fakeApi, json, VALID_KEY } from "../test/utils";
import { ApiError } from "./client";

describe("ApiClient", () => {
  it("sends the API key only on authenticated calls and never cookies", async () => {
    const { client, calls, fetchMock } = fakeApi({
      "GET /payments/products": () => json([]),
      "GET /payments/stats": () => json({ total: 0, by_status: {}, paid_totals: [] }),
    });

    await client.products();
    await client.withApiKey(VALID_KEY).stats();

    expect(calls[0]?.headers.Authorization).toBeUndefined();
    expect(calls[1]?.headers.Authorization).toBe(`Bearer ${VALID_KEY}`);
    expect(fetchMock.mock.calls[1]?.[1]?.credentials).toBe("omit");
  });

  it("refuses authenticated calls without a key, without hitting the network", async () => {
    const { client, fetchMock } = fakeApi({});

    await expect(client.stats()).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("parses the backend error envelope and Retry-After", async () => {
    const { client } = fakeApi({
      "POST /payments/checkout": () =>
        json({ error: { code: "payment_outcome_unknown", message: "lost", details: { payment_id: "p1" } } }, 503, {
          "Retry-After": "5",
        }),
    });

    const error = await client
      .withApiKey(VALID_KEY)
      .checkout({ product_id: "payflow-pro", quantity: 1 }, "key-1")
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 503, code: "payment_outcome_unknown", retryAfterSeconds: 5 });
    expect((error as ApiError).details).toEqual({ payment_id: "p1" });
  });

  it("marks idempotent replays and sends the Idempotency-Key header", async () => {
    const response = { payment_id: "p1", status: "open", amount: 2990, currency: "eur" };
    const { client, calls } = fakeApi({ "POST /payments/checkout": () => json(response, 200) });

    const result = await client.withApiKey(VALID_KEY).checkout({ product_id: "payflow-pro", quantity: 1 }, "key-1");

    expect(result.replayed).toBe(true);
    expect(calls[0]?.headers["Idempotency-Key"]).toBe("key-1");
    expect(calls[0]?.body).toEqual({ product_id: "payflow-pro", quantity: 1 });
  });

  it("returns the degraded RAG health body instead of throwing on 503", async () => {
    const { client } = fakeApi({
      "GET /rag/health": () => json({ ollama: "unavailable", qdrant: "ok", status: "degraded" }, 503),
    });

    await expect(client.ragHealth()).resolves.toEqual({ ollama: "unavailable", qdrant: "ok", status: "degraded" });
  });

  it("maps network failures to a typed error", async () => {
    const { client, fetchMock } = fakeApi({});
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(client.health()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("maps FastAPI validation errors", async () => {
    const { client } = fakeApi({ "POST /rag/ask": () => json({ detail: [{ msg: "bad" }] }, 422) });

    await expect(client.withApiKey(VALID_KEY).ask("x")).rejects.toMatchObject({ code: "validation_error" });
  });

  it("encodes path parameters", async () => {
    const { client, calls } = fakeApi({ "GET /payments/..%2Fstats": () => apiError(404, "payment_not_found") });

    await client.withApiKey(VALID_KEY).payment("../stats").catch(() => undefined);

    expect(calls[0]?.path).toBe("/payments/..%2Fstats");
  });
});
