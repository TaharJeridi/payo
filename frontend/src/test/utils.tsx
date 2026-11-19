import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { AppRoutes, ROUTER_FUTURE } from "../App";
import { ApiClient } from "../api/client";
import type { Payment, PaymentDetail } from "../api/types";
import { AuthProvider } from "../auth/AuthContext";

export const VALID_KEY = "pf_0123456789ab_fake_test_key_not_real_1";

export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call) => Response | Promise<Response>;

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

export function apiError(status: number, code: string, message = code, headers: Record<string, string> = {}) {
  return json({ error: { code, message } }, status, headers);
}

export function fakeApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://dashboard.test");
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>));
    const call: Call = {
      method: init?.method ?? "GET",
      path: url.pathname.replace(/^\/api/, "") + url.search,
      headers,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const route = `${call.method} ${call.path.split("?")[0]}`;
    const handler = routes[route] ?? routes[`${call.method} ${call.path}`];
    if (!handler) return apiError(404, "not_found", `No fake route for ${route}`);
    return handler(call);
  });
  return { client: new ApiClient({ fetch: fetchMock as unknown as typeof fetch }), calls, fetchMock };
}

export function renderApp(route: string, client: ApiClient, { loggedIn = true } = {}) {
  if (loggedIn) window.sessionStorage.setItem("payflow.apiKey", VALID_KEY);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]} future={ROUTER_FUTURE}>
        <AuthProvider baseClient={client}>
          <AppRoutes />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function payment(overrides: Partial<Payment> = {}): Payment {
  return {
    id: "7785103b-2196-4e89-ac3f-1778aac84f31",
    product_id: "payflow-pro",
    quantity: 1,
    unit_amount: 2990,
    amount: 2990,
    currency: "eur",
    status: "open",
    stripe_checkout_session_id: "cs_test_1",
    stripe_payment_intent_id: null,
    checkout_expires_at: "2026-11-16T12:00:00Z",
    failure_reason: null,
    paid_at: null,
    created_at: "2026-11-16T10:00:00Z",
    updated_at: "2026-11-16T10:00:00Z",
    ...overrides,
  };
}

export function paymentDetail(overrides: Partial<PaymentDetail> = {}): PaymentDetail {
  return {
    ...payment(),
    history: [
      { from_status: null, to_status: "creating", source: "api", reason: null, created_at: "2026-11-16T10:00:00Z" },
      { from_status: "creating", to_status: "open", source: "api", reason: null, created_at: "2026-11-16T10:00:01Z" },
    ],
    ...overrides,
  };
}
