# PayFlow — project handover

Entry point for an engineer or agent who takes over PayFlow without the earlier conversations. It summarises and links; it does not repeat the detailed documents. Last verified against the repository on **2026-11-19**. Code fingerprint before that session's changes: `25fb355902b3` (the code of the TAH-142 real runs); after them: `c0e82af410ef` (not yet run with Qwen).

This file is in `docs/engineering/`, which the RAG indexer does **not** read (it indexes only `docs/*.md`). Editing it does not change the assistant's answers.

## A. Overview

**Purpose.** PayFlow is a payment engine (Stripe Checkout in TEST mode, PostgreSQL ledger, hardened webhooks, reconciliation) with a merchant assistant (`POST /rag/ask`) that answers questions about the merchant's own payments and about the platform documentation. The current work is not adding AI features: it is making the assistant's answers **correct and measurable** (Linear project *PayFlow — AI Reliability & Quality*).

**Features.**

- Payments: checkout creation with client idempotency, state machine (`creating → open → paid/failed/expired`, `creation_unknown` when Stripe's reply is lost), webhooks with signature check, reconciler for unknown outcomes, event history (`payment_events`).
- Multi-tenant: merchants and API keys (`backend/app/merchants/`); every query is scoped by the merchant taken from the key.
- Assistant: deterministic parser → SQL-first answers for lookups, counts, totals, rankings; semantic retrieval (bge-m3 + Qdrant) for documentation and explanations; Qwen 2.5 7B writes only the explanatory answers.
- Dashboard (React) with an assistant page that shows sources and backend warnings.

**Stack.**

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, psycopg 3, pydantic-settings |
| Database | PostgreSQL 16 (source of truth, amounts in integer minor units) |
| Vector store | Qdrant 1.12 (one collection per embedding model) |
| Models | Ollama on the Mac host: `qwen2.5:7b-instruct` (temperature 0), `bge-m3` |
| Payments | Stripe Checkout, TEST mode only |
| Frontend | React 18, TanStack Query, Tailwind, Vite, Vitest |
| Tooling | pytest, ruff, mypy (strict), Docker Compose |

## B. Architecture

Read in this order: [`docs/architecture/ai-system.md`](../architecture/ai-system.md) (components, trust boundaries), [`docs/architecture/answer-lifecycle.md`](../architecture/answer-lifecycle.md) (every step of an answer), [`docs/architecture.md`](../architecture.md) (payment engine; §8 is also part of the assistant's corpus).

**Request flow** (`backend/app/rag/service.py`):

1. `/rag/ask` authenticates the API key → `merchant_id`. The tenant never comes from the question.
2. `QueryParser.parse` (`query_parser.py`, deterministic) → `QueryPlan`: intent (`find`, `count`, `total`, `top_product`, `unsupported_metric`, `semantic`), typed `PaymentFilters`, language, `listing`, and **constraint coverage** (`completeness` = complete / incomplete / ambiguous / unsupported, ADR 0013).
3. Routing:
   - semantic plan that asks for a set of payments → deterministic abstention `unrecognized_payment_set` (ADR 0012);
   - structured plan → `StructuredRetriever` (`structured.py`): parameterised, merchant-scoped SQL. Routes: `sql_list` (backend writes the list), `sql_aggregate` (template), `sql_partial` (incomplete plan, labelled partial), `clarify`, `unsupported_constraint`, `no_match`, `attribute_unavailable`, `unsupported`, or `sql_find` (evidence for Qwen, plus a backend `found_listing`);
   - otherwise semantic search (top-k chunks above `RAG_MIN_SCORE`, merchant scope + public docs).
4. Qwen is called only when there is evidence and no deterministic answer. The prompt fences the evidence in a random tag.
5. Post-processing: refusal language normalisation (ADR 0005), `contradicted_evidence` guard on the SQL route (ADR 0011), backend "verified facts" lines, non-blocking warnings from `validate_answer` (ADR 0008/0009).

**Responsibility boundaries.**

| Owner | Facts |
|---|---|
| PostgreSQL + backend | ids, amounts, currencies, statuses, existence, counts, totals, rankings, derived facts (recovered, paid after expiry, confirmed by) |
| Qwen | explanations, documentation summaries, phrasing of single-payment answers |
| Evaluation | judging answers against frozen goldens, without models in the loop |

## C. Important decisions

All ADRs are in [`docs/decisions/`](../decisions/README.md). One line each:

| ADR | Decision | Rejected alternatives (main) |
|---|---|---|
| 0001 | SQL-first retrieval for transactional facts | vector-only RAG over payments |
| 0002 | bge-m3 embeddings (IT questions on EN docs) | nomic-embed-text (0/6 cross-language) |
| 0003 | Deterministic answers for aggregates and missing data | letting Qwen count/sum |
| 0004 | Derived facts computed from `payment_events` | asking Qwen to infer from history |
| 0005 | Refusals/language normalised after generation | special `NO_EVIDENCE` token in the prompt (measured regressions) |
| 0006 | "Found payment, attribute unavailable" answer | generic refusal |
| 0007 | Three runs per series, per-case comparison | single-run scores |
| 0008 | Non-blocking validation warnings | blocking/rewriting answers |
| 0009 | Amounts verified by their role in the sentence | currency-amount presence check (false positives) |
| 0010 | Independent, frozen generalization dataset | tuning on the historical set |
| 0011 | Backend writes listings; contradiction guard | prompt changes, second LLM |
| 0012 | No counts/totals/lists from semantic retrieval | more parser synonyms only |
| 0013 | Semantic constraint coverage (every word accounted for) | open-ended regex lists, LLM parser |
| 0014 | Verified financial claims in LLM answers: claim checks against the evidence, backend durations, out-of-scope route (TAH-143) | global prompt change, JSON claim contract, second LLM verifier |

Recurring principles: deterministic over probabilistic on the financial path; no second LLM; no global prompt changes without measurement; abstain or label as partial rather than present an unverified figure; every claim measured with three real runs.

## D. Current state

**Implemented and verified with real Qwen runs** (Mac, 2026-11-18, code `25fb355902b3`): everything up to TAH-142 (ADR 0001–0013). Results in [`evaluation/tah142/README.md`](../../evaluation/tah142/README.md) and [`docs/quality/evaluation.md`](../quality/evaluation.md#history).

| Dataset | Cases | Role | TAH-142 runs (passed) |
|---|---|---|---|
| historical (`cases.json`) | 50 | development + regression | 49 / 48 / 47 (`consistency-10..12`) |
| generalization v1 | 117 | regression since TAH-139 | 98 / 101 / 100 (`gen-s1..3`) |
| generalization v2 | 102 | regression since TAH-138/141 | 93 / 95 / 92 (`gen2-s1..3`) |
| blind v3 | 150 | blind for its first runs, regression since | 110 / 108 / 111 (`blind-v3-s1..3`) |

False backend answers presented as complete on v3: 10 → 0 per run. Isolation passed in every run. These pass counts use the **old scorer**. Re-scored with the TAH-145 rules (same answers): v3 109 / 108 / 111, v2 91 / 94 / 91, v1 and historical unchanged ([`evaluation/scorer_integrity/`](../../evaluation/scorer_integrity/README.md)).

**Implemented and verified without models (2026-11-19), real runs pending:** TAH-145 (scorer integrity) and TAH-143 / ADR 0014 (claim checks, backend durations, SQL rankings for "classifica", out-of-scope route). Evidence: [`evaluation/tah143/README.md`](../../evaluation/tah143/README.md).

**Tests** (2026-11-19): 940 backend tests pass, 3 skipped (live Ollama/Qdrant, Stripe test mode, saved-runs path); ruff, ruff format and mypy clean; 35 frontend tests pass and `tsc` is clean. Targeted mutation testing: 56/56 mutants killed (specs and runner in `evaluation/mutation/`).

**Open problems:** TAH-143 (Qwen factual errors in explanations), TAH-144 (legitimate questions without an answer), TAH-136 (aggregate templates only in Italian), TAH-132 (docs-it-pii, low), TAH-145 (scorer integrity, this session).

**Not implemented / proposed:** regeneration on `uncited`; JSON claim contract; LLM-assisted parsing. Not used any more: `nomic-embed-text` collection (kept only for the comparison logs).

### Repository state — read before any git command

- `git log` has **one commit** (`72f9a92`, payment engine). Almost all AI work (RAG, parser, evaluation, docs, frontend, migrations 0002–0004) was **uncommitted** on 2026-11-19. Saved runs, reports and the `baseline_*/` folders are git-ignored by design (see [evaluation](../quality/evaluation.md#artefacts-that-are-not-in-the-repository)); keep an off-repository copy.
- Do **not** run `git checkout`, `reset`, `clean` or `stash`: they would destroy the work. Ask the owner before the first commit; a commit (with the baselines force-added or archived elsewhere) is the most urgent hygiene task.
- In the sandboxed development shell used on this project, deleting files in the folder is disabled by default, so git commands that delete lock files fail; ask for delete permission first.

## E. Linear

Project: [PayFlow — AI Reliability & Quality](https://linear.app/tahar-jeridi-workspace/project/payflow-ai-reliability-and-quality-69edf4b6c8d2) (`P-TAH-3`, team `TAH`). Issues are written in Italian; titles carry the area tag `[REL-0x]`.

| Issue | Area | State (2026-11-19) | Notes |
|---|---|---|---|
| TAH-126…131, 133–135, 137–142 | various | Done | TAH-142 closed after real runs |
| TAH-145 | REL-01 scorer integrity | In Progress (created 2026-11-19) | blocks TAH-143; closes with the first real series judged by the new rules |
| TAH-143 | REL-02 Qwen factual errors | In Progress | ADR 0014 implemented; closes only after the real runs in `evaluation/tah143/README.md` §6 |
| TAH-144 | REL-05 unanswered legitimate questions | Backlog | q052, lookup-async, parser gaps |
| TAH-136 | REL-04 IT templates for EN questions | Todo | `_count`, `_total`, `_top_product` |
| TAH-132 | REL-03 docs-it-pii | Backlog, low | Qwen variability |

Rule from the project description: results are recorded only when measured with real Qwen; issues close only with every acceptance criterion verified.

## F. Development environment

**Start the system (Mac).**

```bash
cp .env.example .env                                  # STRIPE_SECRET_KEY=sk_test_... ; never commit .env
brew install ollama && ollama serve &
ollama pull qwen2.5:7b-instruct && ollama pull bge-m3
docker compose up --build -d db api reconciler qdrant rag-indexer dashboard
./scripts/smoke_local.sh
```

**Tests.**

```bash
docker compose --profile test run --rm tests                      # backend, against Postgres payflow_test
cd backend && ruff check . && mypy                                # lint and types
cd frontend && npm ci && npx vitest run && npm run typecheck      # frontend
docker compose --profile test run --rm tests python -m tests.eval_deterministic generalization   # parser + SQL, no models
```

Without Docker (as done in the cloud/VM sessions): a local PostgreSQL 16 with user/password `payflow`/`payflow` and database `payflow_test`, Python 3.12 with `backend/requirements-dev.txt`, then `pytest` from `backend/` (it reads `TEST_DATABASE_URL`).

**Evaluation.** See [`docs/quality/evaluation.md`](../quality/evaluation.md#commands). `./scripts/rag_eval_phases.sh <phase>...` refuses to overwrite a saved run and checks the v3 hash. Three runs per dataset; compare per case. Re-score saved runs when the scorer changes (`evaluation/scorer_integrity/rescore.py`).

**Qwen and bge-m3.** Available only on the Mac host through Ollama (`host.docker.internal:11434` from containers). Cloud and VM sessions have neither: anything there is measured without models, and real runs must be prepared as commands for the Mac.

## G. Next priorities

1. **Real runs on the Mac** (closes TAH-145 and, if the criteria hold, TAH-143): exact commands and closing criteria in [`evaluation/tah143/README.md` §6](../../evaluation/tah143/README.md#6-real-evaluation-mac-pending). Compare only with the `-rescored` baselines.
2. **TAH-143 follow-ups if the runs show them:** claim phrasings not recognised; q099 (documentation contradiction) is out of reach of deterministic checks.
3. **TAH-144.** Reason-based filters (`checkout_session_expired`, async failures), amounts without a currency (q040, q055, q149: today they reach the semantic route, where ADR 0014 now blocks the false answers but cannot give the right one), the "creation problem" criterion (r015).
4. **TAH-136.** English templates for aggregates (including the new ranking lines) and the tenant notice; `detect_language` reads "Should I raise the Starter price…" (q142) as Italian because "I" is counted as the Italian article.
5. **Hygiene.** First commit of the AI work and an off-repo copy of the baselines; a new blind set before any new generalization claim.
