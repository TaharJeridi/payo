import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { apiError, fakeApi, json, payment, paymentDetail, renderApp, VALID_KEY } from "../test/utils";
import { warningLabel } from "./AssistantPage";

const STATS = {
  total: 3,
  by_status: { creating: 0, open: 1, creation_unknown: 1, paid: 1, failed: 0, expired: 0 },
  paid_totals: [{ currency: "eur", amount: 5980 }],
};
const HEALTH = {
  "GET /health": () => json({ status: "ok", stripe_mode: "test" }),
  "GET /rag/health": () => json({ ollama: "ok", qdrant: "ok", status: "ok" }),
};

describe("login", () => {
  it("redirects to login, verifies the key and keeps it out of localStorage", async () => {
    const { client, calls } = fakeApi({
      ...HEALTH,
      "GET /payments": () => json({ items: [], total: 0, limit: 5, offset: 0 }),
      "GET /payments/stats": () => json(STATS),
    });
    renderApp("/", client, { loggedIn: false });

    await userEvent.type(await screen.findByLabelText(/API key/), VALID_KEY);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/payments?limit=1"))?.headers.Authorization).toBe(`Bearer ${VALID_KEY}`);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.getItem("payflow.apiKey")).toBeNull();
  });

  it("stores the key in sessionStorage only when asked", async () => {
    const { client } = fakeApi({
      ...HEALTH,
      "GET /payments": () => json({ items: [], total: 0, limit: 5, offset: 0 }),
      "GET /payments/stats": () => json(STATS),
    });
    renderApp("/", client, { loggedIn: false });

    await userEvent.type(await screen.findByLabelText(/API key/), VALID_KEY);
    await userEvent.click(screen.getByLabelText(/Remember/));
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("heading", { name: "Overview" });
    expect(window.sessionStorage.getItem("payflow.apiKey")).toBe(VALID_KEY);
    expect(window.localStorage.length).toBe(0);
  });

  it("rejects malformed keys locally and invalid keys from the API", async () => {
    const { client, calls } = fakeApi({ "GET /payments": () => apiError(401, "unauthorized", "Invalid or revoked API key") });
    renderApp("/", client, { loggedIn: false });

    await userEvent.type(await screen.findByLabelText(/API key/), "not-a-key");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Invalid format/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);

    await userEvent.clear(screen.getByLabelText(/API key/));
    await userEvent.type(screen.getByLabelText(/API key/), VALID_KEY);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Invalid or revoked API key")).toBeInTheDocument();
  });

  it("logs out when the API starts answering 401", async () => {
    const { client } = fakeApi({
      ...HEALTH,
      "GET /payments": () => apiError(401, "unauthorized"),
      "GET /payments/stats": () => apiError(401, "unauthorized"),
    });
    renderApp("/", client);

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(window.sessionStorage.getItem("payflow.apiKey")).toBeNull();
  });
});

describe("overview and payments", () => {
  it("shows KPIs from the stats endpoint and recent payments", async () => {
    const { client } = fakeApi({
      ...HEALTH,
      "GET /payments/stats": () => json(STATS),
      "GET /payments": () => json({ items: [payment()], total: 1, limit: 5, offset: 0 }),
    });
    renderApp("/", client);

    expect(await screen.findByTestId("paid-total")).toHaveTextContent(/€59\.80/);
    expect(screen.getByTestId("total-count")).toHaveTextContent("3");
    expect(await screen.findByRole("link", { name: "7785103b…" })).toHaveAttribute(
      "href",
      "/payments/7785103b-2196-4e89-ac3f-1778aac84f31",
    );
  });

  it("shows the Payo brand and navigation, with Ask Payo for the assistant", async () => {
    const { client } = fakeApi({
      ...HEALTH,
      "GET /payments/stats": () => json(STATS),
      "GET /payments": () => json({ items: [payment()], total: 1, limit: 5, offset: 0 }),
    });
    renderApp("/", client);

    expect(await screen.findByRole("img", { name: "Payo" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Ask Payo" })).toHaveAttribute("href", "/assistant");
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByText(/PayFlow/)).toBeNull();
  });

  it("filters by status and paginates through the API", async () => {
    const { client, calls } = fakeApi({
      "GET /payments": (call) => {
        const offset = Number(new URLSearchParams(call.path.split("?")[1]).get("offset"));
        return json({ items: [payment({ status: "paid" })], total: 45, limit: 20, offset });
      },
    });
    renderApp("/payments", client);

    await screen.findByText(/page 1 of 3/);
    await userEvent.click(screen.getByRole("button", { name: "Paid" }));
    await screen.findByText(/page 1 of 3/);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText(/page 2 of 3/);

    const paths = calls.map((c) => c.path);
    expect(paths).toContain("/payments?limit=20&offset=0");
    expect(paths).toContain("/payments?limit=20&offset=0&status=paid");
    expect(paths).toContain("/payments?limit=20&offset=20&status=paid");
  });

  it("renders the payment history as a timeline", async () => {
    const { client } = fakeApi({
      "GET /payments/7785103b-2196-4e89-ac3f-1778aac84f31": () => json(paymentDetail()),
    });
    renderApp("/payments/7785103b-2196-4e89-ac3f-1778aac84f31", client);

    const timeline = await screen.findByRole("list", { name: "Payment history" });
    const items = within(timeline).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Created as Preparing");
    expect(items[1]).toHaveTextContent("Preparing → Awaiting payment");
  });

  it("shows a not-found payment as an error", async () => {
    const { client } = fakeApi({ "GET /payments/x": () => apiError(404, "payment_not_found", "Payment not found") });
    renderApp("/payments/x", client);

    expect(await screen.findByRole("alert")).toHaveTextContent("Payment not found.");
  });
});

describe("checkout", () => {
  const products = [
    { id: "payflow-pro", name: "PayFlow Pro", description: "", unit_amount: 2990, currency: "eur" },
    { id: "payflow-starter", name: "PayFlow Starter", description: "", unit_amount: 990, currency: "eur" },
  ];

  it("creates a checkout and only links to Stripe-hosted pages", async () => {
    const { client, calls } = fakeApi({
      "GET /payments/products": () => json(products),
      "POST /payments/checkout": () =>
        json(
          {
            payment_id: "p1",
            status: "open",
            amount: 5980,
            currency: "eur",
            checkout_url: "https://checkout.stripe.com/c/pay/cs_test_1",
            checkout_session_id: "cs_test_1",
            checkout_expires_at: null,
          },
          201,
        ),
    });
    renderApp("/checkout", client);

    await userEvent.clear(await screen.findByLabelText("Quantity"));
    await userEvent.type(screen.getByLabelText("Quantity"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Create checkout" }));

    const link = await screen.findByRole("link", { name: /Stripe payment page/ });
    expect(link).toHaveAttribute("href", "https://checkout.stripe.com/c/pay/cs_test_1");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ product_id: "payflow-pro", quantity: 2 });
    expect(post?.headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("does not render links to non-Stripe URLs", async () => {
    const { client } = fakeApi({
      "GET /payments/products": () => json(products),
      "POST /payments/checkout": () =>
        json({ payment_id: "p1", status: "open", amount: 2990, currency: "eur", checkout_url: "https://evil.test/x" }, 201),
    });
    renderApp("/checkout", client);

    await userEvent.click(await screen.findByRole("button", { name: "Create checkout" }));

    expect(await screen.findByText(/No payment link/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Stripe/ })).toBeNull();
  });

  it("manual retry after a failure reuses the same idempotency key", async () => {
    const outcomes = [apiError(409, "idempotency_key_reused"), json({ payment_id: "p1", status: "open", amount: 2990, currency: "eur" }, 200)];
    const { client, calls } = fakeApi({
      "GET /payments/products": () => json(products),
      "POST /payments/checkout": () => outcomes.shift()!,
    });
    renderApp("/checkout", client);

    await userEvent.click(await screen.findByRole("button", { name: "Create checkout" }));
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await screen.findByText("Existing checkout (replay)");

    const keys = calls.filter((c) => c.method === "POST").map((c) => c.headers["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("a new purchase after success gets a new key", async () => {
    const { client, calls } = fakeApi({
      "GET /payments/products": () => json(products),
      "POST /payments/checkout": () => json({ payment_id: "p1", status: "open", amount: 2990, currency: "eur" }, 201),
    });
    renderApp("/checkout", client);

    await userEvent.click(await screen.findByRole("button", { name: "Create checkout" }));
    await screen.findByText("Checkout created");
    await userEvent.click(screen.getByRole("button", { name: "Create checkout" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));

    const keys = calls.filter((c) => c.method === "POST").map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).not.toBe(keys[1]);
  });
});

describe("assistant", () => {
  it("shows backend warnings and tells backend-written answers apart from missing sources", async () => {
    const answers = [
      { answer: "Le chiavi sono salvate come hash.", used_llm: true, sources: [], warnings: ["uncited", "amount_not_in_sources"] },
      {
        answer: "Hai 3 pagamenti scaduti [1].",
        used_llm: false,
        sources: [{ number: 1, title: "Dati PayFlow calcolati con SQL: conteggio", source_type: "sql", source_key: "sql:count", score: 1, cited: true }],
        warnings: [],
      },
      { answer: "Non ho trovato informazioni sufficienti.", used_llm: false, sources: [] },
    ];
    let call = 0;
    const { client } = fakeApi({ "POST /rag/ask": () => json(answers[call++]) });
    renderApp("/assistant", client);

    for (const question of ["chiavi?", "quanti scaduti?", "capitale?"]) {
      await userEvent.clear(screen.getByLabelText("Question"));
      await userEvent.type(screen.getByLabelText("Question"), question);
      await userEvent.click(screen.getByRole("button", { name: "Send" }));
    }

    expect(await screen.findByTestId("answer-warnings")).toHaveTextContent(
      "Answer not verified by the backend: no source cited; amount not in the cited sources.",
    );
    expect(await screen.findByText("Answer calculated by the backend from Payo data, without Qwen.")).toBeInTheDocument();
    expect(await screen.findByText("No relevant source: Qwen was not called.")).toBeInTheDocument();
    expect(screen.getAllByTestId("answer-warnings")).toHaveLength(1);
  });

  it("labels every amount warning code in English", () => {
    expect(warningLabel("amount_wrong_payment")).toBe("amount attributed to the wrong payment");
    expect(warningLabel("threshold_unverified")).toBe("threshold differs from the filter applied");
    expect(warningLabel("aggregate_unverified")).toBe("total not calculated by the backend");
    expect(warningLabel("contradicted_evidence")).toBe(
      "the generated answer denied payments that were found and was replaced with backend data",
    );
    expect(warningLabel("future_code")).toBe("future_code");
  });

  it("renders citations as links to sources and payment sources as detail links", async () => {
    const { client, calls } = fakeApi({
      "POST /rag/ask": () =>
        json({
          answer: "Il pagamento è open [1]. <img src=x onerror=alert(1)>",
          used_llm: true,
          sources: [
            {
              number: 1,
              title: "Pagamento 7785103b (open)",
              source_type: "payment",
              source_key: "payment:7785103b-2196-4e89-ac3f-1778aac84f31",
              score: 1,
              cited: true,
            },
            { number: 2, title: "Architecture > Reconciliation", source_type: "doc", source_key: "doc:architecture.md", score: 0.4, cited: false },
          ],
        }),
    });
    renderApp("/assistant", client);

    await userEvent.type(screen.getByLabelText("Question"), "stato?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    const answer = await screen.findByTestId("answer");
    expect(answer.querySelector("img")).toBeNull();
    expect(answer).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(within(answer).getByRole("link", { name: "1" }).getAttribute("href")).toMatch(/^#source-1-1$/);
    expect(screen.getByRole("link", { name: "Pagamento 7785103b (open)" })).toHaveAttribute(
      "href",
      "/payments/7785103b-2196-4e89-ac3f-1778aac84f31",
    );
    expect(screen.queryByRole("link", { name: "Architecture > Reconciliation" })).toBeNull();
    expect(calls[0]?.body).toEqual({ question: "stato?" });
  });

  it("prefills the question from the payment page link and reports RAG outages", async () => {
    const { client } = fakeApi({ "POST /rag/ask": () => apiError(503, "rag_unavailable", "Ollama is not reachable") });
    renderApp(`/assistant?q=${encodeURIComponent("Che cosa è successo al pagamento X?")}`, client);

    expect(screen.getByLabelText("Question")).toHaveValue("Che cosa è successo al pagamento X?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Ollama or Qdrant is not responding/);
  });
});

describe("system status", () => {
  it("shows each dependency, including a degraded RAG", async () => {
    const { client } = fakeApi({
      "GET /health": () => json({ status: "ok", stripe_mode: "test" }),
      "GET /rag/health": () => json({ ollama: "unavailable", qdrant: "ok", status: "degraded" }, 503),
    });
    renderApp("/status", client);

    await waitFor(() => expect(screen.getByTestId("state-API")).toHaveTextContent("operational"));
    await waitFor(() => expect(screen.getByTestId("state-Ollama")).toHaveTextContent("unavailable"));
    expect(screen.getByTestId("state-Qdrant")).toHaveTextContent("operational");
  });
});

describe("checkout quantity input", () => {
  it("lets the user type freely and blocks invalid quantities", async () => {
    const { client, calls } = fakeApi({
      "GET /payments/products": () =>
        json([{ id: "payflow-pro", name: "PayFlow Pro", description: "", unit_amount: 2990, currency: "eur" }]),
    });
    renderApp("/checkout", client);

    const input = await screen.findByLabelText("Quantity");
    await userEvent.clear(input);
    await userEvent.type(input, "12");

    expect(await screen.findByText(/between 1 and 10/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create checkout" })).toBeDisabled();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });
});

describe("login key normalisation", () => {
  it("accepts the whole CLI line, quotes and line breaks, and sends only the key", async () => {
    const { client, calls } = fakeApi({
      ...HEALTH,
      "GET /payments": () => json({ items: [], total: 0, limit: 5, offset: 0 }),
      "GET /payments/stats": () => json(STATS),
    });
    renderApp("/", client, { loggedIn: false });

    const half = Math.floor(VALID_KEY.length / 2);
    await userEvent.type(
      await screen.findByLabelText(/API key/),
      ` "api_key=${VALID_KEY.slice(0, half)} ${VALID_KEY.slice(half)}" `,
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(calls[0]?.headers.Authorization).toBe(`Bearer ${VALID_KEY}`);
  });
});
