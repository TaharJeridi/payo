import type {
  ApiHealth,
  CheckoutRequest,
  CheckoutResponse,
  PaymentDetail,
  PaymentPage,
  PaymentStats,
  PaymentStatus,
  Product,
  RagAnswer,
  RagHealth,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfterSeconds = retryAfterSeconds;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export interface CheckoutResult {
  response: CheckoutResponse;
  replayed: boolean;
}

type Fetch = typeof fetch;

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: Record<string, string>;
  auth?: boolean;
  acceptStatuses?: number[];
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly apiKey: string | null;
  private readonly fetchImpl: Fetch;

  constructor(options: { baseUrl?: string; apiKey?: string | null; fetch?: Fetch } = {}) {
    this.baseUrl = (options.baseUrl ?? "/api").replace(/\/$/, "");
    this.apiKey = options.apiKey ?? null;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  withApiKey(apiKey: string | null): ApiClient {
    return new ApiClient({ baseUrl: this.baseUrl, apiKey, fetch: this.fetchImpl });
  }

  health(): Promise<ApiHealth> {
    return this.request<ApiHealth>("/health", { auth: false });
  }

  ragHealth(): Promise<RagHealth> {
    return this.request<RagHealth>("/rag/health", { auth: false, acceptStatuses: [503] });
  }

  products(): Promise<Product[]> {
    return this.request<Product[]>("/payments/products", { auth: false });
  }

  stats(): Promise<PaymentStats> {
    return this.request<PaymentStats>("/payments/stats");
  }

  payments(params: { status?: PaymentStatus | undefined; limit: number; offset: number }): Promise<PaymentPage> {
    const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.status) query.set("status", params.status);
    return this.request<PaymentPage>(`/payments?${query.toString()}`);
  }

  payment(id: string): Promise<PaymentDetail> {
    return this.request<PaymentDetail>(`/payments/${encodeURIComponent(id)}`);
  }

  async checkout(body: CheckoutRequest, idempotencyKey: string): Promise<CheckoutResult> {
    const { data, status } = await this.send<CheckoutResponse>("/payments/checkout", {
      method: "POST",
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    return { response: data, replayed: status === 200 };
  }

  ask(question: string): Promise<RagAnswer> {
    return this.request<RagAnswer>("/rag/ask", { method: "POST", body: { question } });
  }

  async verifyKey(): Promise<void> {
    await this.payments({ limit: 1, offset: 0 });
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return (await this.send<T>(path, options)).data;
  }

  private async send<T>(path: string, options: RequestOptions = {}): Promise<{ data: T; status: number }> {
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (options.auth !== false) {
      if (!this.apiKey) throw new ApiError(401, "unauthorized", "Missing API key");
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: options.method ?? "GET",
        headers,
        body: options.body === undefined ? null : JSON.stringify(options.body),
        credentials: "omit",
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "network_error", "Cannot reach the API");
    }
    const payload: unknown = await response.json().catch(() => null);
    if (response.ok || options.acceptStatuses?.includes(response.status)) {
      return { data: payload as T, status: response.status };
    }
    throw toApiError(response, payload);
  }
}

function toApiError(response: Response, payload: unknown): ApiError {
  const retryAfter = Number(response.headers.get("Retry-After"));
  const retryAfterSeconds = Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null;
  if (isRecord(payload) && isRecord(payload.error)) {
    const error = payload.error;
    return new ApiError(
      response.status,
      typeof error.code === "string" ? error.code : "error",
      typeof error.message === "string" ? error.message : `Error ${response.status}`,
      isRecord(error.details) ? error.details : {},
      retryAfterSeconds,
    );
  }
  if (isRecord(payload) && "detail" in payload) {
    return new ApiError(response.status, "validation_error", "Invalid request", {}, retryAfterSeconds);
  }
  return new ApiError(response.status, "http_error", `Error ${response.status}`, {}, retryAfterSeconds);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
