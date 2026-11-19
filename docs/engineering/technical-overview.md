# PayFlow — technical overview

> This page was the repository README until 2026-11-19. It keeps the full technical description, run instructions and the history of measured results. The short project introduction is the [README](../../README.md).

> **Portfolio project.** PayFlow AI is a sample project built to show how I work as an AI developer: an LLM feature (retrieval-augmented question answering) built on top of a real, correctness-critical backend, with the parts that usually get skipped in demos — tenant isolation, prompt-injection fencing, measurable retrieval quality, failure handling. It is **not a product** and **not for production use**: Stripe runs in TEST mode only and every payment is fake.

## What this project demonstrates

| Area | What to look at |
|---|---|
| **RAG on private, multi-tenant data** | Every vector search is filtered by the authenticated merchant inside Qdrant and re-checked in Python; incremental indexing keyed by content hash and embedding signature; PII is never indexed ([`app/rag/`](../../backend/app/rag), [architecture §8](../../docs/architecture.md#8-rag-assistant)) |
| **SQL-first, then semantic** | Amounts, statuses, dates, counts and totals come from parameterized, merchant-scoped PostgreSQL queries built from a deterministic parser; counts and totals never reach the LLM ([`query_parser.py`](../../backend/app/rag/query_parser.py), [`structured.py`](../../backend/app/rag/structured.py)) |
| **Grounded answers** | Local Qwen 2.5 7B via Ollama, numbered citations, retrieved text fenced as untrusted data with a random per-request delimiter, no LLM call when nothing relevant is retrieved |
| **Evaluation, not vibes** | Synthetic seed data (two tenants) + 50 labelled cases; hit@k, MRR, exact payment sets, tenant isolation, citation and refusal accuracy, data-driven score threshold, phase-by-phase comparison ([`app/rag/evaluation/`](../../backend/app/rag/evaluation)) |
| **Payments correctness** | Idempotency under concurrency, explicit "outcome unknown" state, verified webhooks, reconciliation with backoff based on Stripe's real expiry ([architecture §2–5](../../docs/architecture.md)) |
| **Engineering hygiene** | 900+ backend tests on a real PostgreSQL (unit, concurrency, migrations, security, evaluation scorer) and frontend tests, targeted mutation testing, mypy strict, ruff, Docker Compose deployment, CI |
| **Product surface** | React + TypeScript merchant dashboard served behind nginx with a strict CSP |

| Overview | Assistant |
|---|---|
| ![Merchant dashboard overview with payment KPIs](../../docs/screenshots/overview.png) | ![RAG assistant answering with cited sources](../../docs/screenshots/assistant.png) |

Screenshots taken on the synthetic evaluation merchant (`python -m app.rag.evaluation seed`); every payment shown is fake.

Everything runs locally: Docker Compose for the stack, Ollama on the host for the models. No paid API is required.

**How it was built.** Specification, architecture decisions, review and verification are mine; much of the code was written with an AI coding agent. Reviewing, testing and measuring generated code is part of the skill set this project is meant to show.

## Overview

The **Payment Engine** provides multi-merchant Stripe Checkout (TEST mode only), a PostgreSQL ledger of transactions, client-side idempotency, hardened Stripe webhooks and reconciliation of payments whose outcome was never confirmed.
On top of it, a **RAG assistant** answers a merchant's questions about its own payments with **Qwen via Ollama** and **Qdrant**, and a **React dashboard** (Vite, TypeScript, Tailwind, TanStack Query) lets the merchant follow payments, create checkouts and talk to the assistant.

> ⚠️ Demo only. The app refuses `sk_live_` keys at startup and ignores `livemode` events/sessions. No card data ever touches this backend: card entry happens on the Stripe-hosted Checkout page.

Design notes and the reasoning behind each decision: [`docs/architecture.md`](../../docs/architecture.md).

## Architecture

```
Merchant ──POST /payments/checkout──────────────────────────────▶ FastAPI
           Authorization: Bearer pf_…   Idempotency-Key: <uuid>      │ 1. API key → merchant (tenant)
                                                                     │ 2. price/currency from server catalog
                                                                     │ 3. INSERT payment ON CONFLICT (merchant, key) DO NOTHING
                                                                     │    → new: `creating` row + session expiry + lease, COMMIT
                                                                     │    → existing: other params → 409, in flight → 409, else replay/resume
                                                                     │ 4. Stripe Checkout Session (Stripe idempotency key = payment id)
                                                                     │    ok → `open`; timeout/5xx → 503, payment `creation_unknown`
◀──────────────────────────────────── 201 / 200 replay {checkout_url}┘
Browser ──▶ Stripe Checkout (hosted) ──▶ success_url (informative only, never confirms)

Stripe ──POST /webhooks/stripe──▶ verify HMAC → dedup event → re-fetch session → lock row → verify → transition
Reconciler (every 60 s) ──▶ non-final payments whose next_reconcile_at is due (SKIP LOCKED) → ask Stripe →
                            same verification → transition, or exponential backoff on Stripe errors
```

```
backend/app
├── core/           settings (test-mode + production guards), domain errors, logging, rate limiter
├── db/             declarative base, engine/session factory, model registry
├── integrations/   PaymentGateway Protocol + StripeGateway (definitive vs unknown-outcome errors)
├── merchants/      Merchant + MerchantApiKey models, key hashing, auth service, admin CLI
├── payments/       catalog, state machine, model, repository (tenant-scoped), checkout service,
│                   ledger (transitions + payment_events history), sync (shared verification),
│                   reconciliation, router, schemas
├── webhooks/       signature verification, StripeEvent model, processor, router
├── reconcile.py    `python -m app.reconcile [--loop]`
├── dependencies.py DI wiring: authentication, rate limit, gateway (overridable in tests)
└── main.py         app factory, error mapping, /health
```

### Key decisions

| Topic | Decision | Why |
|---|---|---|
| Tenancy | Every payment belongs to a merchant; the merchant comes **only** from the API key | A client cannot read or write another merchant's data by changing an id in the request |
| API keys | `pf_<prefix>_<secret>`, 256-bit secret, only `sha256(key)` stored, revocable | A database leak does not leak usable keys; random keys need no slow password hash |
| Isolation | Repository methods take `merchant_id`; other merchants' payments return **404** | One place to enforce scoping; no "exists but forbidden" oracle |
| Client idempotency | Required `Idempotency-Key`, unique per merchant, stored with a SHA-256 fingerprint of the request | Retries are safe; same key + other params → 409; different keys → distinct purchases |
| Concurrency | `INSERT … ON CONFLICT DO NOTHING` on `(merchant_id, idempotency_key)` | PostgreSQL's unique index serializes concurrent requests: exactly one creates the payment |
| Lifecycle | `creating` → `open` → `paid`; `creation_unknown`, `failed`, `expired` as explicit states; every change appended to `payment_events` | "In preparation", "waiting for the buyer" and "we don't know" need different handling and must be auditable |
| Unknown outcome | Timeout/5xx/409/429 from Stripe, or any error on a retry → `creation_unknown`, client gets 503 + `Retry-After` | A timeout is not a failure: Stripe may have created the session |
| In-flight requests | Same key while the first request is still `creating` (lease) → `409 idempotency_operation_in_progress` | Only one request talks to Stripe per operation |
| Recovery | Retries resend **identical** parameters (stored `expires_at`) with Stripe idempotency key `checkout-<payment_id>` | Stripe returns the session it already created instead of a new one |
| Reconciliation | Per-payment schedule (`next_reconcile_at`), exponential backoff on Stripe errors, open sessions re-polled but never later than Stripe's real `expires_at` + grace; absence of a session concluded only after that expiry; read-only towards Stripe | Lost webhooks are caught early, outages do not hammer Stripe, no invented timeouts |
| Pricing | Immutable server catalog, request schema `extra="forbid"` | A client cannot inject `amount`/`currency`; any extra field → 422 |
| Signature | `stripe.WebhookSignature.verify_header` on the raw body, then `json.loads` | Re-serialized JSON breaks the HMAC; parsing only after verification |
| Trust model | Event payload is a *notification*; state comes from `checkout.sessions.retrieve` | Forged/stale payloads and out-of-order delivery become irrelevant |
| Event dedup | `stripe_events.id` PK + `INSERT … ON CONFLICT DO NOTHING` inside the business transaction | Exactly-once effects under concurrent re-delivery |
| Ordering | `SELECT … FOR UPDATE` + state machine; `paid` terminal; `failed/expired→paid` only with verified Stripe data | Late `expired` can't downgrade a paid payment; webhook and reconciler can't double-apply |
| Network vs locks | Stripe calls happen before `BEGIN` | No row locks held during network I/O |
| Demo pages | `/payments/demo/*` only if `DEMO_ENDPOINTS_ENABLED` **and** `ENVIRONMENT` is not production | The flag cannot re-enable them in production |
| Abuse | Sliding-window limit on checkout **per merchant** | One merchant cannot exhaust another's quota; single instance only (Redis for replicas) |

## API

All `/payments` endpoints except `/payments/products` require `Authorization: Bearer <api key>`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | – | DB connectivity check |
| `GET` | `/payments/products` | – | Server-side catalog |
| `POST` | `/payments/checkout` | ✅ + `Idempotency-Key` | Body `{product_id, quantity?, customer_email?}` |
| `GET` | `/payments?status=&limit=&offset=` | ✅ | The merchant's own payments |
| `GET` | `/payments/stats` | ✅ | Counts per status and paid totals per currency (dashboard KPIs) |
| `GET` | `/payments/{id}` | ✅ | One payment of the merchant with its `history` (404 otherwise) |
| `POST` | `/webhooks/stripe` | Stripe signature | `checkout.session.completed`, `.async_payment_succeeded`, `.async_payment_failed`, `.expired` |

`POST /payments/checkout` responses:

| Status | Meaning | Client action |
|---|---|---|
| `201` | New payment, `checkout_url` ready | Redirect the buyer |
| `200` + `Idempotent-Replayed: true` | Same key and parameters as an earlier request: same payment, current state | Use the returned state |
| `400 invalid_idempotency_key` | Header missing/malformed (1–255 printable ASCII, no spaces) | Send a new UUID per purchase |
| `401` | Missing, invalid or revoked API key | – |
| `409 idempotency_key_reused` | Key already used with different parameters | Use a new key for a new purchase |
| `409 idempotency_operation_in_progress` | The first request with this key is still talking to Stripe (`Retry-After`) | Retry with the same key |
| `429` | Rate limit (`Retry-After`) | Wait |
| `502 payment_provider_error` | Stripe definitively rejected the first attempt (payment `failed`) | New key |
| `503 payment_outcome_unknown` | Stripe's answer was lost; payment `creation_unknown`, `details.payment_id` | **Retry with the same key** |
| `503 payment_provider_not_configured` | `STRIPE_SECRET_KEY` missing | Fix configuration |

Webhook response: `200 {"received": true, "outcome": "<processed_open|processed_paid|processed_failed|processed_expired|no_change|stale_event|duplicate|ignored_event_type|ignored_livemode|payment_not_found|verification_failed>"}`.
Errors: `400` invalid signature/payload, `413` too large, `503` Stripe unreachable or webhook secret not configured (Stripe retries).

Swagger UI: http://localhost:8000/docs (use **Authorize** with your API key).

## Status and limitations

- **Not production software.** Stripe runs in TEST mode only, all data is synthetic, and the assistant has been measured only on synthetic datasets (419 questions in four sets, three runs per set). See [AI reliability → requirements before production](../../docs/quality/ai-reliability.md#requirements-before-production).
- **What is measured and what is not.** Every figure in this README names the dataset and the run series it comes from. The latest change (verified financial claims, [ADR 0014](../../docs/decisions/0014-verified-financial-claims.md)) is verified on saved answers and by tests; its real-model runs are still pending.
- **Requirements.** Docker with Compose; [Ollama](https://ollama.com) on the host with `qwen2.5:7b-instruct` and `bge-m3` (a machine able to run a 7B model locally; the project was developed on an Apple Silicon Mac); Node 22 and Python 3.12 only for running parts outside Docker. Models and their caches live in Ollama's own directory, never in this repository.
- **Evaluation artefacts.** Datasets, scorers and derived evidence are in the repository; full run outputs and frozen baselines are kept outside it ([why](../../docs/quality/evaluation.md#artefacts-that-are-not-in-the-repository)).

## Run

### 1. Configure
```bash
cp .env.example .env
# set STRIPE_SECRET_KEY=sk_test_...  (STRIPE_WEBHOOK_SECRET: see "Webhooks" below)
```

### 2. Docker Compose
```bash
docker compose up --build -d db api reconciler   # migrations run when the api starts
docker compose --profile test run --rm tests      # pytest against Postgres (payflow_test DB)
```
`db`, `api` and `reconciler` use `restart: unless-stopped`: once started they come back by themselves whenever Docker starts (e.g. after a reboot), until you `docker compose stop` them.
The `payflow_test` database is created by `docker/postgres/init-test-db.sql` only on the first init of the volume (`docker compose down -v` to reset).

### 2b. Local deployment with RAG (Ollama on the host)
```bash
brew install ollama && ollama serve &                 # native, uses Metal on Apple Silicon
ollama pull qwen2.5:7b-instruct && ollama pull bge-m3
docker compose up --build -d db api reconciler qdrant rag-indexer
./scripts/smoke_local.sh                              # end-to-end check, prints PASS/FAIL per step
```
Every port is bound to `127.0.0.1`: nothing is reachable from the network. Containers reach Ollama through `host.docker.internal:11434` (override with `OLLAMA_BASE_URL_DOCKER`). `rag-indexer` re-indexes changed payments and `docs/*.md` every `RAG_INDEX_INTERVAL_SECONDS`; run one pass by hand with `docker compose exec rag-indexer python -m app.rag.index`.

Ask a question:
```bash
curl -s -X POST localhost:8000/rag/ask -H "Authorization: Bearer $PAYFLOW_KEY" \
  -H 'content-type: application/json' -d '{"question":"Perché il mio ultimo pagamento è scaduto?"}'
```
Integration tests against the real services: `RAG_INTEGRATION=1 pytest -m rag_live -rs` (reported as `BLOCKED` when Ollama or Qdrant are not reachable).

### 2b-bis. Evaluate the assistant

```bash
docker compose exec rag-indexer python -m app.rag.evaluation seed            # synthetic merchant, 20 payments
docker compose exec rag-indexer python -m app.rag.index                      # index them
docker compose exec -T rag-indexer python -m app.rag.evaluation run --retrieval-only > rag_eval_retrieval.log
docker compose exec -T rag-indexer python -m app.rag.evaluation run > rag_eval.log   # also asks Qwen (minutes)
docker compose exec -T rag-indexer python -m app.rag.evaluation run --retrieval-only --min-score 0.55
./scripts/rag_eval_compare.sh nomic-embed-text bge-m3                       # same cases, one log per embedding model
./scripts/rag_eval_phases.sh gen-1 gen-2 gen-3                              # 3 runs on the independent dataset
./scripts/rag_eval_report.sh generalization gen-1 gen-2 gen-3 > rag_eval_report_generalization.md
```

50 cases in `backend/app/rag/evaluation/cases.json` (payment by id, payment described in words, docs asked in Italian and in English, amount/status/date lookups, aggregate questions, no-result, cross-tenant and prompt-injection attempts, out-of-domain and unanswerable questions). The first 29 are unchanged since the first run. The report gives hit@1/hit@3/MRR per category, refusal and answer accuracy, and the score threshold that best separates answerable from unanswerable questions. Seed payments use `cs_test_seed_*` ids, never reach Stripe and are never claimed by the reconciler.

#### Results (2026-11-16, Qwen 2.5 7B, same 29 cases and seed data)

| Metric | `nomic-embed-text` | `bge-m3` |
|---|---|---|
| Cases passed | 17/29 | **21/29** |
| Documentation, Italian questions — hit@1 | 0.00 | **1.00** |
| Documentation, English questions — hit@1 | 1.00 | 1.00 |
| Payment described in words — hit@3 | 0.67 | 0.78 |
| Payment quoted by id — hit@1 | 1.00 | 1.00 |
| Aggregate questions (counts, totals) | 1/3 | 0/3 |
| Out-of-scope questions refused | 5/6 | 4/6 |
| Best score cut-off, balanced accuracy | 0.871 | 0.912 |
| Mean latency per answer | 9.0 s | 7.4 s |

What the numbers showed and what changed because of them:
- The docs are in English and the questions in Italian: `nomic-embed-text` never retrieved the right section for an Italian question, and Qwen then invented an answer from unrelated payment data ("a session lasts one hour"). `bge-m3` is now the default.
- With `bge-m3` the old threshold (0.35) let every question through. It is now 0.48: no answerable question in the set falls below it, and 4 of the 6 out-of-scope questions no longer reach the LLM. With 23 data points this is a calibration, not a guarantee.
- Retrieval found the right payment but the model cited a look-alike one: when a question quotes a payment id, other payments are now kept out of the context.
- Aggregate questions fail with every embedding model: a top-k retriever cannot count. They need a tool call to `GET /payments/stats`, not better embeddings.
- Answer checks are substring heuristics. Three correct refusals ("Non sono in grado…", "Do not know") were scored as failures across these runs (one for nomic, two for bge-m3); the markers have been fixed since, and the rows above are the raw results.

#### SQL-first assistant (2026-11-17)

Amounts, statuses, dates, counts and totals are now answered from PostgreSQL, not from vector similarity: a deterministic parser turns the question into typed filters, the backend runs parameterized, merchant-scoped queries, and Qwen only phrases lookups from that evidence. Counts, totals and refunds-style questions never reach the LLM. Details in [architecture §8](../../docs/architecture.md#8-rag-assistant).

```bash
./scripts/rag_eval_phases.sh        # baseline → sql-first → aggregations, same 50 cases
```

Measured on 2026-11-17 on a MacBook (Qwen 2.5 7B + bge-m3 via Ollama, `RAG_MIN_SCORE=0.48`), one run per phase and three for *Finale*, same 50 cases and seed data:

| Test / Metrica | Baseline | SQL-first | Aggregazioni | Hybrid Retrieval | Finale |
|---|---|---|---|---|---|
| Cases passed (50) | 26 | 40 | **45** | not implemented | 45 / 48 / 46 (mean 46.3) |
| Original 29 cases passed | 21 | 24 | **27** | — | 25 / 28 / 26 |
| Payment described in words, hit@1 | 0.33 | **1.00** | **1.00** | — | 1.00 (all runs) |
| Amount/status/date lookups, exact payment set | 0/10 | **10/10** | **10/10** | — | 10/10 (all runs) |
| Aggregates answered correctly | 2/7 | 3/7 | **7/7** | — | 7/7 (all runs) |
| No-result questions handled | 2/3 | 2/3 | **3/3** | — | 3/3 (all runs) |
| Security cases (cross-tenant, injection) passed | 1/4 | 3/4 | **4/4** | — | 4/4 (all runs) |
| Other tenant's data in sources or answers | 0 | 0 | 0 | — | 0 (all runs) |
| Questions that must be refused, refused | 11/12 | 10/12 | **12/12** | — | 11/12 / 12/12 / 12/12 |
| Expected source cited | 16/26 | **25/26** | 22/26 | — | 26/26 / 26/26 / 25/26 |
| Answers containing the expected fact | 24/34 | 27/34 | **31/34** | — | 30/34 / 32/34 / 31/34 |
| LLM calls (of 50) | 45 | 41 | **33** | — | 33 / 33 / 33 |
| Mean latency per question | 7.5 s | 6.8 s | **5.3 s** | — | 5.4 / 5.2 / 5.1 s |
| Parser intent / filter accuracy | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | — | 1.00 / 1.00 (all runs) |

**Finale** = three independent runs of the same code, model, configuration and dataset (`final`, `final-2`, `final-3`):

| Run | Cases passed | LLM calls | Mean latency |
|---|---|---|---|
| final | 45/50 | 33 | 5.4 s |
| final-2 | 48/50 | 33 | 5.2 s |
| final-3 | 46/50 | 33 | 5.1 s |

Mean 46.3/50, observed range 45–48. Retrieval, routing and the evidence given to the model were identical in the three runs; only the generated text changed. Three runs are not enough to estimate the distribution of results precisely, so a difference of a few cases between two single runs can be generation variability rather than an effect of the code. Per-case comparison: 2 cases failed in all three runs (`docs-it-pii`: the right documentation chunk ranked 8th; `derived-recovered`: correct answer without the recovery source, which the case requires), 4 failed in one or two runs (two correct answers worded differently from the check, one answer without citations, one refusal in Chinese).

How to read it:
- Isolation was already correct in the baseline (the vector search is scope-filtered); the security *failures* there were wrong answers, not leaks.
- Parser accuracy is measured on the cases it was written against: it shows the parser is consistent, not that it generalizes.
- "Expected source cited" drops from 25 to 22 between two phases whose lookup path is identical: with temperature 0, Ollama is still not perfectly deterministic, so differences of a few cases between single runs are within noise.
- What still fails in the last phase is generation, not retrieval: in 3 SQL lookups the right payment is source [1] but Qwen cites the SQL summary ([2]) instead, or leaves out the amount; one doc question ("quali dati personali non vengono indicizzati") retrieves the right section and the model still says it is not listed.
- Change made after this run (column *Finale*): the SQL search summary is no longer a numbered source; it is passed to the model as backend data, so the only citable sources of a lookup are the payments themselves.

#### Targeted fixes after the three final runs

Changed after the per-case analysis, without new infrastructure: section 8 of `docs/architecture.md` split into titled subsections (the paragraph on personal data excluded from the index ranked 8th inside a long mixed chunk); refusals written by the backend in the question's language; "Fatti verificati dal backend" lines for derived-fact matches; two answer checks extended with equivalent wordings (documented in the cases' `note`), a negation guard on every answer check, and a language check on refusals. Measured with three runs of the same configuration (`targeted-1..3`). Because the answer checks changed, the three *Finale* runs were re-scored with the new checks from their saved answers (the old bilingual "IT / EN" refusal is accepted as Italian):

| | Finale, re-scored | Targeted fixes |
|---|---|---|
| Cases passed (50), per run | 46 / 46 / 45 (mean 45.7) | 45 / 44 / 44 (mean 44.3) |
| LLM calls | 33 / 33 / 33 | 33 / 33 / 33 |
| Mean latency | 5.4 / 5.2 / 5.1 s | 4.6 / 4.2 / 4.6 s |

No overall improvement: the difference is within the observed run-to-run range, and per case the fixes both helped and hurt.
- Fixed in all three runs: `derived-recovered` (backend fact line), refusals in the question's language (`ood-*`, `nores-name`; before, Qwen also answered in English or Chinese). `docs-it-pii` now retrieves the right subsection first and passes in 2 of 3 runs (the third answer omits the email).
- New stable failures, caused by the `NO_EVIDENCE` instruction added to the prompt: Qwen now declines `docs-it-isolation` although the answer (404) is in its sources, and declines `lookup-async`, which then gets the backend's plain listing without the failure reason. `pii-email` fails because the backend refuses to accept a refusal on the SQL route even though the email is genuinely not available. `amt-en-dot` (3/3) and `amt-it-comma` (2/3) now get terse answers without the amount.
- `docs-it-open-check` produced a mixed English/Chinese answer in one run: language drift also affects normal answers, which these fixes do not cover.


#### Corrections after the targeted fixes

Prompt rule 4 restored (no `NO_EVIDENCE` marker); refusals by the model normalized to the question's language only after generation; on the SQL route a model refusal is no longer overridden, and questions about attributes the assistant cannot see (e.g. the customer's email) get a deterministic "found, but not available" answer; singular/plural agreement in backend messages fixed. Kept: backend fact lines, section 8 subsections. Measured with three runs (`corrected-1..3`), same checks as the targeted series:

| | Finale, re-scored | Targeted fixes | Corrections (current) |
|---|---|---|---|
| Cases passed (50) | 46 / 46 / 45 | 45 / 44 / 44 | **49 / 49 / 48** |
| Financial facts correct | 23/24 ×3 | 22 / 21 / 21 of 24 | **24/24 ×3** |
| Expected source cited | 26 / 26 / 25 of 26 | 25 / 24 / 25 of 26 | 26 / 26 / 24 of 26 |
| Must-refuse questions refused | 11–12/12 | 11/12 ×3 | **12/12 ×3** |
| Answerable questions refused | 0–1/38 | 1/38 ×3 | **0/38 ×3** |
| Answers in the question's language | 48/50 ×3 | 48–49/50 | **50/50 ×3** |
| Tenant isolation | 4/4 ×3 | 4/4 ×3 | 4/4 ×3 |
| LLM calls / mean latency | 33 / 5.1–5.4 s | 33 / 4.2–4.6 s | 32 / 5.0–5.1 s |

All regressions of the targeted series are gone (`docs-it-isolation`, `lookup-async`, `pii-email`, `amt-*`). Amounts came back with only the prompt rule restored and the documentation unchanged, so the prompt was the cause. What still fails is variable, never 3/3: `docs-it-pii` incomplete in 2 runs, one uncited and one irrelevant citation in 1 run. Three runs per series cannot show whether 48–49 differs from 46 by more than chance; the per-case changes above are deterministic backend behaviour or failures that disappeared in all three runs.

After these runs a non-blocking answer validator was added (warnings `uncited`, `invalid_citation`, `unknown_payment_id`, `amount_not_in_sources`, `personal_data`, shown in the dashboard), database errors on the SQL route became `503`, and the evaluation checks the language of every answer. Measured with three more runs (`validated-1..3`), same configuration and dataset:

| | Corrections | Validator added |
|---|---|---|
| Cases passed (50) | 49 / 49 / 48 | **50 / 49 / 50** |
| Financial facts correct | 24/24 ×3 | 24/24 ×3 |
| Expected source cited | 26 / 26 / 24 of 26 | 26/26 ×3 |
| Must-refuse refused / answerable refused | 12/12, 0/38 ×3 | 12/12, 0/38 ×3 |
| Answers in the question's language | 50/50 ×3 | 50/50 ×3 |
| Tenant isolation | 4/4 ×3 | 4/4 ×3 |
| LLM calls / mean / p95 latency | 32 / 5.0–5.1 s / 11.6–12.3 s | 32 / 5.0–5.3 s / 11.7–12.2 s |

The answer text is unchanged by the validator, so the difference between the two series (1–2 cases, all on the documentation questions `docs-it-pii`, `docs-it-keys`, `docs-it-open-check`) is generation variability, not an effect of the code. Across the six runs no case fails in every run and nothing that passed before regressed.

Validator on real answers: `amount_not_in_sources` fired 6 times (2 per run) and all 6 were **false positives** — the answers repeat the threshold of the question ("supera i 90 euro", "inferiore a 10,00 €"), which is not an amount of any payment. Applying the other checks to the 366 saved model answers of all 12 runs: `uncited` flagged 3 answers, all genuinely uncited (including the 2 citation failures found by the evaluation); `invalid_citation`, `unknown_payment_id` and `personal_data` flagged nothing and no such defect occurred, so their recall is shown only by unit tests. Fixing the false positive is tracked in Linear.

#### Validator by amount role, runner metrics, generalization dataset (2026-11-17)

- **Amount validator.** Amounts are now checked by their role in the sentence ([ADR 0009](../../docs/decisions/0009-amount-verification-by-role.md)):
  - a threshold or range bound must equal the filter applied with SQL;
  - a total must equal a backend computation;
  - any other amount must belong to the payment it is attributed to.

  An amount typed in the question is never accepted on its own. On the 366 saved answers of the 12 real runs, with sources rebuilt from the seed: the old rule flags 25 answers, all false positives, and the new rule flags 0. Raising one amount by 1 € in each of the 126 answers that contain one gives 126 flags out of 126. Mutation tests kill 11 of 12 mutants; the survivor is equivalent for the current document format. The three real runs (`hardening-1..3`) are still to do.
- **Runner metrics.** The runner computes every reliability metric (`not_applicable` when a case does not exercise it), error classes, p95 latency and results by group and language. `rag_eval_report.sh` writes a Markdown comparison with stable failures, variable failures and regressions. Old JSON runs are readable, and the report reproduces the figures above.
- **Generalization dataset.** 117 new questions in 16 groups, frozen with a sha256 ([ADR 0010](../../docs/decisions/0010-independent-generalization-dataset.md), [details](../../docs/quality/generalization.md)). First measurement covers only the deterministic components (parser, SQL route, backend-written answers), with no models:

| | Historical (50) | Generalization (117) |
|---|---|---|
| Parser intent | 45/45 | **89/103** |
| Parser filters | 12/12 | **35/56** |
| Exact payment set on the SQL route | 13/13 | **36/53** |
| Backend-written answers correct | 14/14 | **20/36** (23/36 after manual review) |

The parser does not generalize as well as the historical score suggested.

- **Fragile:** dates ("dopo il 25 ottobre" becomes the 25th only), negations ("non riuscite" becomes paid), colloquial phrasing and typos.
- **Wrong backend-written answers:** nine of them answer a different question from the one asked, including one total of 444,70 € instead of 365,30 €. Each states the criteria it applied.

The issues are tracked in Linear; the parser is unchanged, so this stays an independent baseline.

**Real runs (Qwen 2.5 7B, bge-m3, 3 runs each):**

| | Historical, `hardening-1..3` | Generalization, `gen-1..3` |
|---|---|---|
| Cases passed | 49 / 48 / 49 of 50 | 74 / 73 / 75 of 117 (80 / 79 / 81 after manual review) |
| Financial facts | 24/24 ×3 | 36–37 of 46 |
| Citations | 26 / 25 / 25 of 26 | 28–30 of 40 |
| Answerable questions refused | 0/38 | 9–11 of 90 |
| Isolation | 4/4 | 6/6 |
| Amount warnings | 0 | 2 true positives, 3 false positives (since fixed) |

On the historical set nothing regressed: failures stay variable and limited to two documentation answers. On new questions, 42 of the 44 failures repeat in every run.

- **Parser:** 26 failures.
- **Wrong language:** English aggregates answered in Italian, 5 failures.
- **Qwen with correct evidence:** 7 failures.
- **Scorer or dataset errors:** 6.
- **Most serious new finding:** given 8 payments, Qwen sometimes states that a payment in its own evidence does not exist ("non ci sono pagamenti PayFlow Pro con quantità 3").

Details in [generalization](../../docs/quality/generalization.md).

#### Financial evidence consistency (TAH-139)

**Problem:** Qwen claimed that payments did not exist although they were among its sources. In two cases SQL had already answered the question exactly; in the other two the parser had dropped a filter.

**Fix** ([ADR 0011](../../docs/decisions/0011-backend-written-lookups-and-plan-coverage.md)):

- Lookups asking for existence or a list are now answered by the backend from SQL, with total, citations and lookup limit. The model is not involved.
- The parser reports what it could not interpret. An incomplete plan never yields "no payments", a count or a total; it names the part it did not understand.
- A model answer that denies payments found by SQL is replaced, with the warning `contradicted_evidence`.

**Measured without models.** Generalization v1 is now a regression set because the fixes were developed on it. New cases are in v2: 102 questions, frozen before the first run.

| | Historical | v1 (regression) | v2 (new) |
|---|---|---|---|
| Backend-written answers correct | 30/31 | 61/77 | 53/61 |
| Incomplete plans detected | 0 | 14 | 4 |
| Complete plan, "no payments" while payments exist | 0 | 1 | 1 |

The contradiction guard never replaced an answer that had a complete plan, across the 489 saved real answers on the SQL route.

Still open:

- some misreadings slip through plan coverage (1 false absence and 3 lists for another question on v2);

**Real runs (2026-11-18).**

- **TAH-139 criterion met:** in 9 runs no answer denied a payment found by SQL.
- **Historical set:** 48/48/48 of 50, with half the LLM calls and lower latency. The two stable failures are `lookup-late-paid`, whose wording the substring check does not match although the answer is correct, and the documentation answer `docs-it-pii`.
- **v1:** 82/83/81, up from 74/73/75.
- **v2, first independent measurement:** 70/74/71 of 102. Most failures come from questions the parser does not recognise, which go to the semantic route, where Qwen counted or summed wrongly ("due checkout aperti" when there are 3). That is the next priority (TAH-138).

**TAH-138** ([ADR 0012](../../docs/decisions/0012-no-payment-aggregates-from-semantic-retrieval.md)): a question that asks for a count, a total or a list of payments and that the parser cannot structure is refused, without retrieval and without Qwen. The answer says how to rephrase it.

Re-scoring the saved runs exactly:

- **Wrong answers removed:** 13 counts or totals made up by Qwen ("due checkout aperti", "totale 147,00 €").
- **Correct answers lost:** 16, now refused.
- **Cases passed:** v1 78 / 79 / 77, v2 69 / 72 / 70.

v2 is now a regression set too; the next independent measurement needs a new dataset.

**TAH-134 and TAH-135, parser fixes.** The parser now handles day ranges, "dopo/prima/fino al", halves and weeks of a month, English dates, negated comparators ("non superano"), "massimo", "quantità 3" and "paid after the checkout expired". Questions about a phone number or about one payment id are no longer counts.

Without models, on the regression sets:

| | v1 | v2 |
|---|---|---|
| Correct filters | 35 → 47 of 56 | 31 → 35 of 44 |
| Exact payment set | 36 → 47 of 55 | 31 → 37 of 45 |
| Backend answers to the wrong question | – | 4 → 0 |

The historical set is unchanged.

**TAH-141: payment sets named without "pagamenti".** The parser now also recognises:

- a product name, "checkout" or "vendite" as the subject ("Starter pagati a ottobre", "Quanti checkout aperti ho?");
- status adjectives after a noun ("Pagamenti rifiutati", "andati male", "non riuscite");
- colloquial money phrases ("quanti soldi ho fatto", "quanto ho tirato su", "how much did … bring in").

Misspelled payment nouns, money verbs and months are corrected, and each correction is recorded in the plan. Misspelled statuses are still reported rather than guessed. Vague periods ("last spring", "trimestre scorso") are reported as unresolved.

Without models, on the regression sets:

| | v1 | v2 |
|---|---|---|
| Refused as unrecognised payment sets | 7 → 0 | 13 → 0 |
| Correct filters | 47 → 54 of 56 | 35 → 43 of 44 |
| Exact payment set | 47 → 50 of 55 | 37 → 46 of 46 |

The historical set is unchanged, answer for answer.

Real runs with Qwen (3 per dataset, regression sets):

| | Before | After |
|---|---|---|
| v1 | 92 / 91 / 91 | 99 / 101 / 101 |
| v2 | 80 / 79 / 81 | 93 / 94 / 93 |
| historical | 48 / 48 / 48 | 49 / 49 / 48 |

No figure written by the backend was wrong, and v2 had no new failures. Generalization still has to be measured on an unseen set (TAH-140).

**TAH-140: blind dataset v3.** 150 new questions (89 Italian, 61 English) were written and reviewed by isolated agents that saw only a neutral specification, never the parser or the earlier datasets. Every numeric golden was verified by independent SQL and Python oracles, and the set was frozen before any run. Provenance and limits are in [`evaluation/blind_v3/`](../../evaluation/blind_v3/README.md). The first measurement is `./scripts/rag_eval_phases.sh blind-v3-1 blind-v3-2 blind-v3-3`.

**TAH-142: semantic constraint coverage ([ADR 0013](../../docs/decisions/0013-semantic-constraint-coverage.md)).** On the blind set the backend wrote 5 wrong totals and counts as if complete, because the parser dropped words it did not know ("escluso", "confermati via riconciliazione", "più di un pezzo"). Now:

- every word of a question whose answer the backend writes must be explained by a recognised constraint, by the intent, or by a closed list of non-constraining words;
- otherwise the answer names what was not interpreted ("incomplete"), asks for a precise criterion ("big payments": ambiguous), or states that PayFlow cannot filter on it ("weekend": unsupported);
- excluded and negated products and statuses are never applied as filters.

Measured on real runs (3 per dataset, [analysis](../../evaluation/tah142/README.md)):

| Dataset | Passed after TAH-142 | Baseline |
|---|---|---|
| v3 | 110 / 108 / 111 | 105 / 106 / 105 |
| v1 | 98 / 101 / 100 | 99 / 101 / 101 |
| v2 | 93 / 95 / 92 | 93 / 94 / 93 |
| historical | 49 / 48 / 47 | 49 / 49 / 48 |

- **False backend answers presented as complete on v3:** 10 per run before, 0 in every run after.
- **Cost:** 2 answers that were right by coincidence are now labelled partial.
- **Everything else:** isolation is unchanged, and the remaining differences are Qwen variability on identical input.

Engineering documentation: [AI system](../../docs/architecture/ai-system.md) · [answer lifecycle](../../docs/architecture/answer-lifecycle.md) · [AI reliability](../../docs/quality/ai-reliability.md) · [evaluation procedure](../../docs/quality/evaluation.md) · [decisions](../../docs/decisions/README.md). These files are outside `docs/*.md`, so the assistant does not index them.


#### Scorer integrity and verified financial claims (TAH-145, TAH-143, 2026-11-19)

- **Scorer** ([details](../../evaluation/scorer_integrity/README.md)): payment ids are now judged by what the answer claims (a complete list must hold exactly the expected payments; a partial list is never a pass), and a refusal followed by generated content is not a refusal. Re-scoring the saved TAH-142 runs: v3 109 / 108 / 111, v2 91 / 94 / 91, v1 and historical unchanged. Compare new series with the `-rescored` baselines.
- **Claims in Qwen's answers** ([ADR 0014](../../docs/decisions/0014-verified-financial-claims.md), [analysis](../../evaluation/tah143/README.md)): statuses and durations are checked against the payment they are about; absences on the semantic route, silent choices among payments of the same amount and invented offers are not shown. Elapsed times are computed by the backend; product rankings and out-of-scope requests (promotional content, pricing advice) are answered without Qwen. On 768 saved Qwen answers: 31 wrong answers replaced, 0 correct ones. **Real runs on the Mac: pending.**

Only measured results go in this table. Hybrid retrieval (full-text + RRF) and a reranker are deliberately not implemented yet: they are decided after measuring the SQL-first phases.

### 2c. Dashboard
```bash
docker compose up --build -d dashboard        # http://127.0.0.1:8080, sign in with a merchant API key
cd frontend && npm ci && npm run dev          # development: http://127.0.0.1:5173, /api proxied to :8000
npm run typecheck && npm test && npm run build
```
Pages: overview (KPIs from `GET /payments/stats`, recent payments, system status), payments (status filter, pagination, detail with history timeline), new checkout (Idempotency-Key generated per purchase and reused on automatic retries after `503`/`409`, honouring `Retry-After`), assistant (answers with clickable `[n]` citations; payment sources link to the payment) and system status.
The API key stays in memory in the tab (optionally `sessionStorage`), never `localStorage`; requests never send cookies. nginx serves the build and proxies `/api` on the same origin, with a strict CSP, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`; the Stripe webhook path is not exposed through it.

### 3. Create a merchant and its API key
```bash
docker compose exec api python -m app.merchants.cli create "My Shop"
# merchant_id=…  key_id=…  api_key=pf_…   ← shown once, store it now
docker compose exec api python -m app.merchants.cli list
docker compose exec api python -m app.merchants.cli issue-key <merchant_id> --label laptop
docker compose exec api python -m app.merchants.cli revoke-key <key_id>
```
Payments created before migration `0002` are assigned to a merchant named `Legacy (pre-0002)`, which has no key: issue one with `issue-key` to see them.

### 4. Webhooks via the bundled Stripe CLI
```bash
# 1. print the signing secret (stable for your Stripe account + this CLI) and put it in .env
docker compose --profile stripe run --rm stripe-cli listen --print-secret
# 2. recreate the api so it reads the new STRIPE_WEBHOOK_SECRET
docker compose up -d --force-recreate api
# 3. start forwarding test events to the api
docker compose --profile stripe up -d stripe-cli
```
Events sent while the CLI is not running are not forwarded later: the `reconciler` service settles those payments once their session has expired.

### 5. Local (without Docker for the API)
```bash
docker compose up -d db
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://payflow:payflow@localhost:5432/payflow
# settings are read from ../.env (project root) and then backend/.env; real env vars win
alembic upgrade head
uvicorn app.main:app --reload
python -m app.reconcile          # one reconciliation pass
pytest   # uses TEST_DATABASE_URL, default postgresql+psycopg://payflow:payflow@localhost:5432/payflow_test
```

### 6. Lint, types, CI
```bash
cd backend
ruff check . && ruff format --check .
mypy            # strict, configured in pyproject.toml
```
`.github/workflows/ci.yml` runs ruff, mypy and pytest (with a Postgres service) plus both Docker build targets on every push / PR.

## Try a test payment

1. Stripe account in **Test mode** → Developers → API keys → secret key `sk_test_...` into `.env`. A restricted key `rk_test_...` with *Checkout Sessions: write* is even better.
2. Create a merchant (step 3 above) and keep the `api_key`.
3. Create a checkout — always with a **new** `Idempotency-Key` per purchase:
   ```bash
   curl -s -X POST localhost:8000/payments/checkout \
     -H "Authorization: Bearer $PAYFLOW_API_KEY" \
     -H "Idempotency-Key: $(uuidgen)" \
     -H 'content-type: application/json' -d '{"product_id":"payflow-pro","quantity":1}'
   ```
   Open `checkout_url`, pay with `4242 4242 4242 4242`, any future date, any CVC.
   Useful test cards: `4000 0000 0000 0002` (declined), `4000 0025 0000 3155` (3DS).
4. `curl -s -H "Authorization: Bearer $PAYFLOW_API_KEY" localhost:8000/payments` → status `paid`.

`stripe trigger checkout.session.completed` creates sessions *not* created by this backend: they are acknowledged with `payment_not_found`, by design.

## Security checklist
- Only `sk_test_`/`rk_test_` keys accepted; `livemode` events and sessions never change state.
- Secrets only via environment (`SecretStr`, never logged); `.env` is git-ignored.
- No PAN/CVC stored or processed; only Stripe ids, amounts and status.
- Payment data requires a merchant API key; keys are stored hashed and can be revoked; tenants are isolated.
- Webhook body capped at 512 KB; signature tolerance 5 minutes (replay protection).
- `POST /payments/checkout` rate-limited per merchant (default 10/min).
- Demo pages are disabled unless `DEMO_ENDPOINTS_ENABLED=true`, and always in production.
- Merchant administration is CLI-only: there is no HTTP admin API to attack.
- RAG: every vector search is filtered by the authenticated merchant's scope (+ public docs) and results are re-checked; customer email, checkout URL and Idempotency-Key are never indexed; retrieved text is fenced as untrusted data with a random per-request delimiter; questions are not logged; `/rag/ask` is rate-limited per merchant (default 20/min).

---

Copyright © 2026 Tahar Jeridi. All rights reserved.
