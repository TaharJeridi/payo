# PayFlow AI — Payment Engine architecture

This document explains *why* the Payment Engine is built the way it is. The README covers how to run it.

## 1. Data model

```
merchants 1───* merchant_api_keys          (sha256 hash, prefix, revoked_at)
    │
    1───* payments ───* (Stripe Checkout Session, 1 per payment)
               │
stripe_events  (one row per processed Stripe event id, for webhook dedup)
```

`payments` columns added in migration `0002`:

| Column | Purpose |
|---|---|
| `merchant_id` | Owner (tenant). `NOT NULL`, FK `RESTRICT`. |
| `idempotency_key`, `request_fingerprint` | Client idempotency: key and SHA-256 of the canonical request. `UNIQUE (merchant_id, idempotency_key)`. |
| `checkout_url` | Returned again on replays while the payment is `open`. |
| `checkout_expires_at` | Decided once before the first Stripe call, sent as `expires_at`, then replaced by the value Stripe reports. |
| `checkout_creation_error` | Set when Stripe's answer to the creation request was lost. |
| `last_reconciled_at` | Last time the reconciler looked at the row (fairness when ordering work). |

Rows that existed before `0002` are assigned to a merchant called `Legacy (pre-0002)`.

Migration `0003` adds:

| Column / table | Purpose |
|---|---|
| `creation_attempts`, `creation_attempt_at` | Lease of the request currently talking to Stripe; a retry may take over only after it expires. |
| `next_reconcile_at`, `reconcile_attempts`, `last_reconcile_error` | Per-payment reconciliation schedule and exponential backoff. `NULL` for final states. |
| `payment_events` | Append-only history: `from_status`, `to_status`, `source` (`api`, `webhook`, `reconciliation`, `migration`), `reason`, `stripe_event_id`. |

`0003` splits the old `pending` by what is actually known: with a session id → `open`; without → `creation_unknown` (the reconciler resolves it). `failed` rows whose reason was `checkout_session_expired` become `expired`. Every existing row gets one `migration` history event.

## 2. Payment state machine

```
             ┌──────────────► creation_unknown ──┐ (timeout / 5xx / 409 / 429, or any error on a retry)
             │                   │  ▲ retry       │
creating ────┼──► open ──────────┼──┴────────────►├──► paid (terminal)
 (row        │      │            │                │       ▲
 committed,  │      ├──► expired ┘ (Stripe says   │       │ only with verified Stripe data
 Stripe call │      │              expired)       │       │
 in flight)  └──────┴──► failed  ─────────────────┘───────┘
```

| State | Meaning | Final |
|---|---|---|
| `creating` | Row committed, one request is calling Stripe (lease) | no |
| `open` | The Checkout Session exists and can be paid (or an async method is still processing) | no |
| `creation_unknown` | Stripe's answer to the creation was lost: the session may or may not exist | no |
| `paid` | Stripe reports `payment_status=paid` and the session matches the payment | yes, terminal |
| `failed` | Definitive rejection on the first attempt, async payment failure, failed verification, or a session proven never created | yes (`→paid` only with verified data) |
| `expired` | Stripe reports the session `expired` | yes (`→paid` only with verified data) |

A network error never produces `failed`. Every transition goes through `PaymentLedger.transition`, which validates it and appends a `payment_events` row, so the history is a gapless chain (`from_status` = previous `to_status`).

## 3. Client idempotency

**Problem.** A mobile app sends `POST /payments/checkout` and the connection drops before the response arrives. The app does not know whether the purchase was created. If it retries blindly and the first request had succeeded, the customer gets two pending payments and may pay twice. If it doesn't retry, the purchase may be lost.

**Contract.** The client generates one key per purchase intent (a UUIDv4) and reuses it only to retry that same purchase.

| Situation | Result |
|---|---|
| New key | `201`, new payment |
| Same key, same parameters | `200` + `Idempotent-Replayed: true`, the *same* payment in its current state |
| Same key, different parameters | `409 idempotency_key_reused` (a client bug: never silently "fix" it) |
| Different keys, identical parameters | Two distinct purchases. Buying the same thing twice is legitimate. |

**Implementation.**
1. `request_fingerprint` = SHA-256 of `{"product_id", "quantity", "customer_email" (lower-cased)}` serialized canonically. "Same parameters" is decided by comparing hashes.
2. `INSERT … ON CONFLICT (merchant_id, idempotency_key) DO NOTHING RETURNING id`. With N concurrent requests carrying the same key, PostgreSQL's unique index makes exactly one `INSERT` succeed; the others wait for its transaction and then see the conflict. No application lock or Redis is needed. This is tested with 10 threads on a real PostgreSQL.
3. The winner commits the `creating` row **before** calling Stripe, so the operation exists even if the process crashes during the call. While its lease (`CHECKOUT_CREATION_LEASE_SECONDS`) runs, other requests with the same key get `409 idempotency_operation_in_progress` instead of calling Stripe a second time; after it, a retry may take over (crashed process).
4. Keys are scoped per merchant: two merchants may use the same key independently, and a merchant can never replay another merchant's operation.

## 4. Unknown outcomes and recovery

`StripeGateway` splits Stripe errors into two families:

| Family | Errors | Meaning | Payment |
|---|---|---|---|
| Definitive | `InvalidRequestError`, `AuthenticationError`, `PermissionError`, `CardError` | Stripe answered "no": nothing was created | `failed` (first attempt only) |
| Unknown outcome | `APIConnectionError` (timeouts), `APIError` (5xx), `RateLimitError`, `IdempotencyError`, any error with HTTP 409/429/5xx | The request may or may not have been executed | `creation_unknown`, client gets `503` |

Recovery relies on **two layers of idempotency**:
- client → PayFlow: `Idempotency-Key`;
- PayFlow → Stripe: `idempotency_key = checkout-<payment_id>`.

When the client retries with the same key, PayFlow finds the `creation_unknown` payment and calls Stripe again with **exactly the same parameters**. That is why `expires_at` is stored instead of recomputed: Stripe replays an idempotent request only if the parameters are identical. If Stripe had created the session, it returns that same session; if not, it creates it now. Either way there is exactly one session per payment.

A definitive rejection on a *retry* does not close the payment: the original attempt might have succeeded, so the payment stays `creation_unknown`, the client gets `503`, and the decision is left to reconciliation. A retry after the stored `expires_at` does not call Stripe at all.

The default session lifetime is 2 hours (`CHECKOUT_SESSION_TTL_SECONDS`). With the former 31 minutes, a retry made more than about one minute after a request that never reached Stripe carried an `expires_at` less than 30 minutes away, which Stripe rejects: the purchase could not be recovered by the client.

## 5. Reconciliation

**Problem.** Webhooks are "at least once, eventually": they can still be lost. In local development, events sent while the Stripe CLI is off are never forwarded. A creation response can also be lost and the client may never retry. Without reconciliation such payments would stay `open` or `creation_unknown` forever.

**Scheduling.** Every non-final payment has a `next_reconcile_at`. A run claims due rows with `FOR UPDATE SKIP LOCKED` and pushes them forward by a lease, so parallel reconcilers never get the same payment and never wait on each other.

| State | First check | Then |
|---|---|---|
| `creating` | after the creation lease | skipped while the lease runs |
| `creation_unknown` | after 1 minute | backoff |
| `open` | after `RECONCILE_OPEN_FIRST_CHECK_SECONDS` (15 min) | backoff, never later than Stripe's real `expires_at` + 5 min |

Backoff is `base × 2^attempts` (default 60 s, capped at 6 h, ±20 % jitter). A Stripe error never changes the state: it increments `reconcile_attempts`, stores `last_reconcile_error` and reschedules. A successful transition resets both.

**Rule: no invented timeouts.** Expiry always comes from data: the `expires_at` Stripe reports for the session, or, for a session never received, the `expires_at` every creation attempt must carry (identical parameters). Absence of a session is concluded only after that time: before it, a client retry may still create it.

| Case | What the reconciler does |
|---|---|
| Session id known | `retrieve` → same verification + state machine as webhooks |
| Session id unknown (lost response) | Lists sessions created in `[payment.created_at − skew, min(now, expires_at) + skew]` and matches `metadata.payment_id`; found → adopted and verified |
| No session found after expiry | `failed` (`checkout_session_not_created`). No session can be created anymore, because any retry would carry an `expires_at` in the past, which Stripe rejects. |
| Stripe unreachable | Nothing changes; retried with exponential backoff |
| Rows from before `0002` without stored expiry | `retrieve` reads Stripe's real `expires_at` and stores it |

Properties:
- **Read-only towards Stripe**: it never creates or modifies sessions.
- **Safe to run concurrently with webhooks and client retries**: Stripe is called outside the transaction, then `SELECT … FOR UPDATE` serializes; the reconciler re-checks that the payment is still non-final and that no client retry started meanwhile (`creation_attempts`) before writing (tested).
- **Shared logic**: `payments/sync.py` holds the verification (`amount`, `currency`, `metadata.payment_id`, session id, `livemode`) and the decision used by both webhooks and the reconciler.

Run it as the `reconciler` Compose service (`python -m app.reconcile --loop`, every `RECONCILE_INTERVAL_SECONDS`), or once by hand with `python -m app.reconcile`.

## 6. Merchants, API keys and isolation

**Authentication.** `Authorization: Bearer pf_<prefix>_<secret>`.
- `secret`: 32 bytes from `secrets.token_urlsafe` (256 bits).
- Only `sha256(full key)` is stored. A slow password hash (bcrypt/argon2) protects *low-entropy* human passwords against guessing; a 256-bit random key cannot be guessed, so a fast hash is enough and keeps every request cheap.
- `prefix` (12 hex characters, not secret) locates the row with an index lookup; the hash is compared with `hmac.compare_digest` (constant time). An unknown prefix still computes a comparison against a dummy hash.
- Revocation sets `revoked_at`; the key stops working on the next request. A merchant can hold several keys, so keys can be rotated without downtime.
- Administration is CLI-only (`python -m app.merchants.cli`). Not having an HTTP admin API means there is no admin API to attack.

**Isolation (multi-tenancy).**
- The tenant comes **only** from the authenticated key (`get_current_merchant`), never from the URL, body or query.
- Every client-facing repository method takes `merchant_id` and adds it to the `WHERE` clause: the rule lives in one place.
- Another merchant's payment returns **404 with the same body as a non-existent id**. A 403 would confirm that the id exists.
- Webhooks and the reconciler are internal: they identify payments through Stripe ids that Stripe has verified, not through client input.
- Tests prove it: listing, filtering, `GET /payments/{id}` and idempotency replays across merchants A/B.

## 7. Production guards

- `ENVIRONMENT=production` (or `prod`) disables `/payments/demo/*` whatever `DEMO_ENDPOINTS_ENABLED` says, and logs a warning if the flag is set.
- `sk_live_`/`pk_`/other keys are rejected at startup; `livemode` events and sessions never change state.
- Rate limiting is per merchant and in-process (one instance). Several replicas would need a shared store such as Redis.

## 8. RAG assistant

### 8.1 Scope and models

`POST /rag/ask` answers a merchant's questions about *its own* payments and about the public documentation in `docs/`. Qwen (`qwen2.5:7b-instruct`) runs in Ollama on the host; embeddings (`bge-m3`, multilingual, chosen over `nomic-embed-text` by the evaluation below) are stored in Qdrant.

### 8.2 Personal and bearer data excluded from the index

Each payment becomes one text document, written in Italian: product, amount, status with its meaning, failure reason, timestamps, Stripe ids, verified derived facts and the full `payment_events` history (`app/rag/documents.py`). Customer email, checkout URL and the client's Idempotency-Key are **not** indexed: the email is personal data, the checkout URL and the Idempotency-Key are bearer data, and none of them is needed to explain what happened to a payment. A test checks that a document built from a payment with an email never contains it.

### 8.3 Incremental indexing

`app/rag/indexer.py`, `python -m app.rag.index [--loop]`:
- Each `docs/*.md` file is split by heading path (`Title > Section > Subsection`) with a size cap and overlap.
- `rag_sources` stores, per source, the hash of the content + embedding model, the payment `updated_at` seen, and the chunk count. Only changed sources are re-embedded (the embedding signature is model + document prefix + collection, so changing any of them re-indexes everything); shrunk sources lose their extra chunks; deleted docs are removed. Point ids are `uuid5(source_key#position)`, so a re-run never duplicates. Each embedding model gets its own collection (`<QDRANT_COLLECTION>__<model>`), so switching model never meets a collection of another dimension (which would be refused); task prefixes default to the ones the model was trained with (`search_query:`/`search_document:` for nomic, none for bge-m3).
- A PostgreSQL advisory lock keeps one indexer active at a time.

### 8.4 SQL-first routing

`app/rag/query_parser.py`, `app/rag/structured.py`. PostgreSQL is the source of truth for transactional facts; the vector index is for explanations and documentation. Before any vector search a deterministic parser turns the question into a typed `QueryPlan`: intent (`find`, `count`, `total`, `top_product`, `unsupported_metric`, `semantic`) and validated `PaymentFilters` (payment ids, statuses, products, quantity, amount in integer cents with inclusive/exclusive bounds and ranges, currency, creation date range, derived facts). Italian and English amount formats are parsed with `Decimal` (`89,70 €`, `1.234,50`, `89.70 EUR`); dates are UTC calendar ranges ("questo mese", "28 ottobre 2026", ISO ranges). The LLM never writes SQL: the backend builds parameterized SQLAlchemy queries in `PaymentRepository`, always with the authenticated `merchant_id`, which `PaymentFilters` cannot even carry (`extra="forbid"`). A merchant id written in the question is ignored and the user is told so.

| Route | When | Evidence | Answer |
|---|---|---|---|
| `sql_find` | ids, an amount, or descriptors (product, quantity, dates, status, derived facts) with a request for payments | the matching payments' documents, generated from the database at query time, + up to `RAG_MAX_DOC_CHUNKS` documentation chunks; the search summary (filters, number of matches) is given to the model as backend data, not as a numbered source | Qwen, cited |
| `sql_aggregate` | counts, totals, best-selling product | one SQL result | deterministic template, no LLM |
| `no_match` | the filters match nothing for this merchant | — | deterministic "nothing found with: …" |
| `unsupported` | refunds, disputes, fees, payouts, net revenue | — | deterministic: not recorded, never estimated |
| `semantic` | everything else | vector search (as before) | Qwen, cited |

When the question identifies a payment (id or exact amount), a status in it is treated as the question ("è scaduto?"), not as a filter, so a wrong guess does not turn into "not found".

### 8.5 Derived facts

Derived facts (`app/payments/facts.py`) are computed from `payment_events`, never stored and never produced by the model; the same rule exists in Python (documents, evidence) and SQL (`EXISTS` filters), and a test checks they agree on every seed payment:
- *recovered*: the creation outcome was lost (`creation_unknown`) and the first later transition to `open`/`paid` exists; its `source` is the recovery source (`reconciliation` or `api` for a client retry);
- *paid after expiry*: a transition `expired → paid`;
- *confirmed by*: the source of the transition to `paid`.
Payment documents carry them as "Fatti verificati" lines, and amounts in both formats (`89,70 € (89.70 EUR)`).

### 8.6 Financial metrics

*Incassato (gross paid)* = sum of `amount` of `paid` payments, per currency. Refunds, disputes, fees and payouts are not modelled, so net and settled amounts are not available and the assistant says so. "Best-selling product" = most units in paid payments; the answer also names the product with the highest revenue when it differs.

### 8.7 Tenant isolation and Row-Level Security

Row-Level Security was evaluated and not introduced: tenancy is enforced in one repository layer with tests, and RLS would need a dedicated database role plus a per-request `SET app.merchant_id`, which pays off once more code paths (or humans) query the database directly.

### 8.8 Semantic route

`app/rag/service.py`:
1. Payment ids quoted in the question are fetched exactly from Qdrant (same scope filter) and ranked first; other payments are then excluded from the context (a 7B model was observed citing a look-alike payment). Then the question is embedded (with the model's query prefix, if any) and Qdrant is searched with a mandatory scope filter `[merchant_id, "public"]`. The scope comes only from the API key; a search without scope raises, and every returned point's scope is re-checked.
2. Keep hits above `RAG_MIN_SCORE`. When the question is about a payment (quoted id, or the best hit is a payment) at most `RAG_MAX_DOC_CHUNKS` documentation chunks are kept, so architecture notes do not crowd out the payment. If nothing remains, answer "I don't know" **without calling the LLM**.
3. Send Qwen the hits as numbered sources inside `<context-RANDOM>` … `</context-RANDOM>`, with a system prompt that treats the block as untrusted data, requires `[n]` citations and forbids invented facts. The random tag cannot be closed by injected text.
4. Return the answer and the sources, each marked `cited` when the answer references it.

### 8.9 Refusals and backend-written facts

The backend writes the answer itself, in the question's language (Italian by default, English when the question is in English; `app/rag/language.py`), whenever it can establish the outcome on its own:
- no chunk passes `RAG_MIN_SCORE` on the semantic route: "Non ho trovato informazioni sufficienti…". This states what was found, not that the information does not exist;
- SQL finds no payment for the filters, or the metric is not recorded (refunds, net, payouts…);
- SQL finds the payment but the question asks for an attribute the assistant cannot see (customer email, name, phone, address, card data, checkout link, Idempotency-Key): "Ho trovato il pagamento … [1], ma l'email del cliente non è disponibile". The attribute is never read or shown. A found payment is not enough to answer every question about it.

The prompt asks Qwen to say it does not know when the sources lack the answer; it is not asked to emit special tokens (an earlier `NO_EVIDENCE` marker made the 7B model decline answerable questions and shortened its answers, so it was removed after measurement). After generation, a refusal written in a language other than the question's is replaced by the backend's message in the right language; on the SQL route that message keeps the refusal visible next to the payments that were found. Answers with content are never replaced, and a refusal in the right language is kept as written.

When payments match a derived-fact filter (recovered, paid after expiry), the backend appends "Fatti verificati dal backend" lines built from `payment_events`, each citing the payment's own source number, e.g. "recuperato tramite riconciliazione dopo che l'esito della creazione era andato perso [1]". The model is not asked to reconstruct these facts.

### 8.10 Evaluation

`app/rag/evaluation/`, `python -m app.rag.evaluation seed|run`. A dedicated merchant gets 20 deterministic synthetic payments (and a second tenant 2 more, used only to prove isolation) (uuid5 ids, every history validated against the state machine, `next_reconcile_at` NULL so the reconciler ignores them). 50 cases reference them by name and expect a source (`payment:<ref>` or `doc:<file>#<section>`), an answer substring, a count or total computed from the database, or a refusal. Retrieval is measured on the ranked candidates *before* the threshold (hit@k, MRR: ranking quality) and on what survives it (what the model sees); the threshold report lists the relevant-chunk scores of answerable questions and the top scores of questions that must be refused, with the cut-off that maximizes balanced accuracy. Answer checks are substring heuristics with a negation guard ("non è stato pagato" does not count as "pagato"); refusals are also checked for the question's language; the JSON report keeps every answer for review. Expectation changes are recorded in a `note` field of the case. SQL-first cases also check the exact set of payments shown to the model, isolation (another tenant's ids never appear in sources or answers) and, as diagnostics, the parser's intent and filters. `scripts/rag_eval_phases.sh` runs the same cases with the phases switched off and on (`RAG_DOCUMENT_FACTS`, `RAG_SQL_LOOKUP`, `RAG_SQL_AGGREGATES`). The parser was written against these cases, so its accuracy on them is not evidence of generalization.

### 8.11 Failure handling

Ollama/Qdrant errors become `503 rag_unavailable`; `GET /rag/health` reports each dependency. The payment engine does not depend on the RAG: if Ollama is down, payments keep working.

## 9. Merchant dashboard

`frontend/` is a single-page app (React 18, TypeScript strict, Tailwind, TanStack Query, React Router).

- **Single origin.** nginx serves the static build and proxies `/api/*` to the api service, so the browser never needs CORS and the CSP can be `default-src 'self'`. `/api/webhooks/stripe` returns 404 through nginx: webhooks reach the api directly.
- **Credentials.** The merchant pastes its API key; it is verified with a real API call and kept in React state. "Remember for this session" stores it in `sessionStorage`; it is never written to `localStorage`. Requests use `credentials: "omit"`. Any `401` clears the key and the query cache. A browser-held bearer key is acceptable for a demo; a production login would use an httpOnly session cookie with CSRF protection.
- **Idempotent checkout from the UI.** One Idempotency-Key per purchase intent (fingerprint of product, quantity, email). The key survives automatic retries on `payment_outcome_unknown` (503) and `idempotency_operation_in_progress` (409), which honour `Retry-After` (capped at 10 s, 4 attempts), and manual "Riprova"; it is replaced only after a success or when the parameters change. An end-to-end run with Stripe unreachable produced one payment with four creation attempts, all with the same key, in `creation_unknown`.
- **Untrusted text.** Assistant answers and sources are rendered as text; `[n]` markers become anchors to the numbered sources, and only `payment:<uuid>` sources become links. Checkout links are rendered only for `https://checkout.stripe.com/` URLs and open with `rel="noopener noreferrer"`.
- **KPIs.** `GET /payments/stats` aggregates in SQL (counts per status, paid totals per currency) for the authenticated merchant, so the dashboard never downloads the whole payment table.

## 10. Known limitations

- An async payment method that fails without an `async_payment_failed` webhook looks like `complete` + `unpaid` to the reconciler and stays `open` (polled with backoff). Telling it apart from "still processing" requires reading the PaymentIntent.
- Refunds and disputes are not modelled yet.
- Finding a lost session lists Checkout Sessions by creation time. This is fine at portfolio scale; at high volume, Stripe's Search API on PaymentIntent metadata would be cheaper.
- Rate limiter state is per process.
- RAG answers are as good as a local 7B model: they are grounded and cited, but phrasing errors are possible. Amounts and statuses must be read from the API, not from the assistant.
- The indexer scans payments changed since their last indexing; at very high volume an outbox/event stream would be cheaper than polling `updated_at`.
- The dashboard holds a bearer API key in the browser: an XSS would expose it. The CSP and text-only rendering reduce the risk; a production version should use server-side sessions.
