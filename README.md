# PayFlow AI — Payment Engine

Portfolio open-source project. This module is the **Payment Engine**: Stripe Checkout (TEST mode only), PostgreSQL ledger of transactions, hardened Stripe webhooks.
RAG, LLM (Qwen) and React dashboard are intentionally out of scope for this iteration.

> ⚠️ Demo only. The app refuses `sk_live_` keys at startup and ignores `livemode` events/sessions. No card data ever touches this backend: card entry happens on the Stripe-hosted Checkout page.

## Architecture

```
Client ──POST /payments/checkout {product_id, quantity}──▶ FastAPI
                                                            │ 1. price/currency from server catalog
                                                            │ 2. INSERT payment (pending) + COMMIT
                                                            │ 3. Stripe Checkout Session (idempotency key = payment id,
                                                            │    metadata.payment_id, client_reference_id)
                                                            ◀─ checkout_url
Browser ──▶ Stripe Checkout (hosted) ──▶ success_url (informative only, never confirms)

Stripe ──POST /webhooks/stripe (raw body + Stripe-Signature)──▶ FastAPI
   1. HMAC verification on raw bytes (tolerance 300s)
   2. fast duplicate check (stripe_events)
   3. re-fetch Checkout Session from Stripe API   ← source of truth, outside DB locks
   4. BEGIN
        INSERT stripe_events ON CONFLICT DO NOTHING   ← idempotency claim
        SELECT payment FOR UPDATE                     ← serializes concurrent events
        verify amount / currency / metadata / session id / livemode
        apply monotonic state transition
      COMMIT  (any error → rollback → non‑2xx → Stripe retries)
```

```
backend/app
├── core/           settings (test-mode guard), domain errors, logging
├── db/             declarative base, engine/session factory, model registry
├── integrations/   PaymentGateway Protocol + StripeGateway (StripeClient)
├── payments/       catalog, state machine, model, repository, service, router, schemas
├── webhooks/       signature verification, StripeEvent model, processor, router
├── dependencies.py DI wiring (overridable in tests)
└── main.py         app factory, error mapping, /health
```

### Key decisions

| Topic | Decision | Why |
|---|---|---|
| Pricing | Immutable server catalog (`payments/catalog.py`), request schema `extra="forbid"` | A client cannot inject `amount`/`currency`; any extra field → 422 |
| Payment row before Stripe call | `pending` row committed first, id passed as metadata + idempotency key | Webhook can always resolve the payment, even if it arrives before we store `session_id` or if our call timed out after Stripe created the session |
| Signature | `stripe.WebhookSignature.verify_header` on `await request.body()`, then `json.loads` | Re-serialized JSON breaks the HMAC; parsing only after verification |
| Trust model | Event payload is a *notification*; state is derived from `checkout.sessions.retrieve` | Forged/stale payloads and out-of-order delivery become irrelevant |
| Idempotency | `stripe_events.id` PK + `INSERT … ON CONFLICT DO NOTHING` inside the business transaction | Exactly-once effects under concurrent re-delivery; failed processing is not recorded so Stripe retries |
| Ordering | `SELECT … FOR UPDATE` + monotonic transitions: `pending→paid`, `pending→failed`, `failed→paid`; `paid` terminal | Late `expired`/`failed` can't downgrade a paid payment; a verified payment can recover a locally failed one |
| Network vs locks | Stripe API fetch happens before `BEGIN` | No row locks held during network I/O |
| Sync SQLAlchemy | Sync endpoints run in the threadpool; webhook reads the body async and offloads processing | Simpler transactional code; stripe-python is sync |
| Demo listing | `GET /payments` behind `DEMO_ENDPOINTS_ENABLED` (off unless set), no customer email exposed | Not meant to exist in a real deployment |
| Bad Stripe session | Amount mismatch / missing URL → payment `failed` **and** the session is expired on Stripe | Nobody can still pay a session the backend has rejected |
| Abuse | In-process sliding-window limit on `POST /payments/checkout` per client IP (`CHECKOUT_RATE_LIMIT_PER_MINUTE`) | Stops trivial flooding of `pending` rows; single-instance only (use Redis for replicas) |

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | DB connectivity check |
| `GET` | `/payments/products` | Server-side catalog |
| `POST` | `/payments/checkout` | Body `{product_id, quantity?, customer_email?}` → `201 {payment_id, checkout_session_id, checkout_url, amount, currency, status}` |
| `GET` | `/payments?status=&limit=&offset=` | Demo listing of transactions |
| `POST` | `/webhooks/stripe` | Stripe events: `checkout.session.completed`, `.async_payment_succeeded`, `.async_payment_failed`, `.expired` |

Webhook response: `200 {"received": true, "outcome": "<processed_paid|processed_failed|no_change|stale_event|duplicate|ignored_event_type|ignored_livemode|payment_not_found|verification_failed>"}`.
Errors: `400` invalid signature/payload, `413` too large, `429` checkout rate limit exceeded, `502` Stripe unreachable (Stripe will retry), `503` Stripe key or webhook secret not configured.

Swagger UI: http://localhost:8000/docs

## Run

### 1. Configure
```bash
cp .env.example .env
# set STRIPE_SECRET_KEY=sk_test_...  and STRIPE_WEBHOOK_SECRET=whsec_...
```

### 2. Docker Compose
```bash
docker compose up --build -d db api        # migrations run on container start
docker compose --profile test run --rm tests    # pytest against Postgres (payflow_test DB)
```
`db` and `api` use `restart: unless-stopped`: once started they come back by themselves whenever Docker starts (e.g. after a reboot), until you `docker compose stop` them.

Webhooks via the bundled Stripe CLI:
```bash
# 1. print the signing secret (stable for your Stripe account + this CLI) and put it in .env
docker compose --profile stripe run --rm stripe-cli listen --print-secret
# 2. recreate the api so it reads the new STRIPE_WEBHOOK_SECRET
docker compose up -d --force-recreate api
# 3. start forwarding test events to the api
docker compose --profile stripe up -d stripe-cli
```
The `payflow_test` database is created by `docker/postgres/init-test-db.sql` only on the first init of the volume (`docker compose down -v` to reset).

### 3. Local (without Docker for the API)
```bash
docker compose up -d db
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://payflow:payflow@localhost:5432/payflow
# settings are read from ../.env (project root) and then backend/.env; real env vars win
alembic upgrade head
uvicorn app.main:app --reload
pytest   # uses TEST_DATABASE_URL, default postgresql+psycopg://payflow:payflow@localhost:5432/payflow_test
```

### 4. Lint, types, CI
```bash
cd backend
ruff check . && ruff format --check .
mypy            # strict, configured in pyproject.toml
```
`.github/workflows/ci.yml` runs ruff, mypy and pytest (with a Postgres service) plus both Docker build targets on every push / PR.

## Stripe Test Mode setup

1. Create a Stripe account, keep the dashboard toggle on **Test mode**.
2. Developers → API keys → copy the **secret test key** `sk_test_...` into `.env` (`STRIPE_SECRET_KEY`). A restricted key `rk_test_...` with *Checkout Sessions: write* is even better.
3. Install Stripe CLI (`brew install stripe/stripe-cli/stripe`) and `stripe login`.
4. Forward webhooks:
   ```bash
   stripe listen --forward-to localhost:8000/webhooks/stripe \
     --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired
   ```
   Copy the printed `whsec_...` into `STRIPE_WEBHOOK_SECRET` and restart the API.
5. Create a checkout:
   ```bash
   curl -s -X POST localhost:8000/payments/checkout \
     -H 'content-type: application/json' -d '{"product_id":"payflow-pro","quantity":1}'
   ```
   Open `checkout_url`, pay with `4242 4242 4242 4242`, any future date, any CVC.
   Useful test cards: `4000 0000 0000 9995` (declined), `4000 0025 0000 3155` (3DS).
6. Check `curl localhost:8000/payments` → status `paid`.

`stripe trigger checkout.session.completed` creates sessions *not* created by this backend: they are acknowledged with `payment_not_found`, by design.

## Security checklist
- Only `sk_test_`/`rk_test_` keys accepted; `livemode` events and sessions never change state.
- Secrets only via environment (`SecretStr`, never logged); `.env` is git-ignored.
- No PAN/CVC stored or processed; only Stripe ids, amounts and status.
- Webhook body capped at 512 KB; signature tolerance 5 minutes (replay protection).
- `POST /payments/checkout` rate-limited per client IP (default 10/min). Behind a reverse proxy run uvicorn with `--proxy-headers` so the real client IP is used.
- Demo listing `GET /payments` is disabled unless `DEMO_ENDPOINTS_ENABLED=true`.
- No authentication: this is a demo, do not expose it publicly as is.

## License
MIT, see [LICENSE](LICENSE).
