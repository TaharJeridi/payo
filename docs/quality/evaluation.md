# Evaluation

## Datasets

There are four datasets. They are reported separately and never merged into one score.

| Dataset | File | Cases | Role |
|---|---|---|---|
| `historical` | `app/rag/evaluation/cases.json` | 50 | Development and regression set. The parser was written against it, so its accuracy here proves nothing about new questions. |
| `generalization` | `app/rag/evaluation/generalization.json`, v1.0.0, sha256 `05c26453f7b4…` | 117 | Frozen; its first runs (`gen-1..3`) are the independent baseline. **A regression set since TAH-139**, whose fixes were developed on its failures. |
| `generalization-v2` | `app/rag/evaluation/generalization_v2.json`, v2.0.0, sha256 `3cc7194e4b0f…` | 102 | Frozen before its first run (`gen2-1..3`). **A regression set since TAH-138/TAH-141**. See [ADR 0010](../decisions/0010-independent-generalization-dataset.md). |
| `generalization-v3` | `app/rag/evaluation/generalization_v3.json` (loaded by path), v3.0.0, sha256 `e5454c628bdd…` | 150 | **Blind set** (TAH-140): written and reviewed by isolated agents from a neutral specification, goldens verified by independent oracles, frozen before its first runs (`blind-v3-1..3`, the baseline); a regression set from then on. See [generalization](generalization.md#blind-dataset-v3-tah-140). |

- **Seed data** (`app/rag/evaluation/seed.py`):
  - a synthetic merchant with 20 payments: uuid5 ids, histories validated against the state machine, `cs_test_seed_*` session ids, never reconciled;
  - a second tenant with 2 payments, used only for isolation cases.

  `python -m app.rag.evaluation seed` is idempotent.
- **Historical cases:** 50 questions in 12 categories. The first 29 have not changed since the first run. Expectation changes are recorded in each case's `note` field.
- **Expected values** for counts and totals are computed with SQL from the seed at run time, independently of the assistant. The generalization dataset is also checked against a pure-Python oracle (`tests/test_generalization_dataset.py`).

## Checks per case (pass/fail)

| Check | Meaning |
|---|---|
| `retrieved` | Expected source among the evidence given to the model |
| `payments` | Exact set of payments given to the model (SQL lookups) |
| `isolated` | Another tenant's ids never in sources or answer (except ids typed in the question) |
| `refused` / `refusal_language` | Must-refuse cases: refusal, in the question's language. Backend-written "no match", "not recorded", "not available" and `out_of_scope` answers, and the backend abstentions written after a claim check (`unverified_absence`, `unjustified_selection`, `unsupported_offer`), count as refusals in either language. **Since TAH-145** a model answer counts as a refusal only if it does not also produce what was asked: an e-mail, a draft or an invented code after a refusal sentence is a failure; a refusal followed by more than 40 words of other content keeps the earlier verdict and is tagged `refusal_uncertain` for review ([scorer integrity](../../evaluation/scorer_integrity/README.md)). |
| `not_refused` / `answer_language` | Answerable cases: no refusal, answer in the question's language. Skipped when the case sets `allow_refusal` and the answer is a refusal. |
| `answer` | Expected fact present (substring, negation-aware). A zero count also matches "nessun", "non ci sono", "there are no". |
| `answer_ids` | Since TAH-145. Cases with `expect_payments`, answer not refused, not an aggregate: the payments the answer presents. A list presented as complete must hold exactly the expected payments and state the expected total; a model answer fails when an expected payment is missing or an unexpected one is a list item. A list declared partial (`answer_ids_partial`) and unexpected ids in prose (`answer_ids_uncertain`) are review tags, not pass or fail. Ids typed in the question are never extra or missing. |
| `cited_expected` | Expected source cited |

Diagnostics, outside pass/fail: parser intent and filter accuracy, answer warnings per code, and threshold analysis on the semantic route.

## Metrics computed by the runner

The runner computes each metric per case as `pass`, `fail` or `not_applicable`. Rates are taken over applicable cases only, so a case that does not exercise a metric never counts as a success. The JSON output keeps every earlier key and adds:

- `metrics` and `errors` for each result;
- `summary.reliability`: metrics, error lists, results by category, group and language, and the number of LLM calls;
- `summary.latency_ms.p95`.

| Metric | Applies to | Pass when |
|---|---|---|
| `intent_accuracy`, `filter_accuracy` | Cases with `expect_intent` / `expect_filters` | Parser output matches |
| `retrieval` | Cases with `retrieved` or `payments` | Both pass |
| `payment_identification` | Cases expecting payments | Right payment set / payment source, and (since TAH-145) `answer_ids` |
| `financial_fact` | `answer` check in `payment_by_id`, `payment_lookup`, `sql_lookup`, `aggregate` | Expected fact present |
| `completeness` | `answer` check in other categories | Expected fact present |
| `citation` | Cases with `cited_expected` | Expected source cited |
| `correct_abstention` | Must-refuse cases | Refused |
| `no_false_refusal` | Answerable cases | Not refused; `false_refusal_rate` = 1 − rate |
| `isolation` | Cases with `isolated` | No foreign id |
| `language` | Every answer | Answer language = question language |

**Error classes** (a case can have several):

- `parser`
- `retrieval`
- `isolation`
- `generation`: an answer check failed although retrieval passed
- `abstention`
- `false_refusal`
- `financial_fact`
- `completeness`
- `citation`
- `language`

**Latency:** mean, and p95 = value at index ⌊0.95·(n−1)⌋ of the sorted latencies, the same formula as the earlier reports.

Each run also records:

- dataset name, version and sha256;
- `code_fingerprint`: the sha256 of the backend's Python sources, used because the repository may not have git;
- `code_version`, taken from `git rev-parse` when available. Otherwise it is left empty: no invented hash.

## Artefacts that are not in the repository

The saved runs (`rag_eval_phase_*.json` / `.log`), the generated reports (`rag_eval_report_*.md`) and the frozen baseline folders (`baseline_tah142/`, `baseline_blind_v3/`, with their `SHA256SUMS`) are kept locally and are git-ignored: a single run of the blind set is about 0.5 MB, and the full history is about 21 MB. They are archived separately from the repository. Every figure in these documents names the run it comes from; the derived, committed evidence is in `evaluation/` (per-case comparisons, re-scored verdicts, retrospective checks). Tests that need a saved run skip when it is absent.

## Requirements

- Docker Compose stack (`db`, `api`, `qdrant`).
- Ollama on the host with `qwen2.5:7b-instruct` and `bge-m3`.

The cloud test environment has neither model. Automated tests use fakes and **do not** measure answer quality. The deterministic baseline in [generalization](generalization.md) runs without models and measures only the parser, the SQL route and backend-written answers.

## Commands

```bash
./scripts/rag_eval_phases.sh hardening-1 hardening-2 hardening-3        # 3 full runs, historical dataset
./scripts/rag_eval_phases.sh gen-1 gen-2 gen-3                          # 3 full runs, generalization v1 (regression)
./scripts/rag_eval_phases.sh gen2-1 gen2-2 gen2-3                       # 3 full runs, generalization v2
./scripts/rag_eval_report.sh historical hardening-1 hardening-2 hardening-3 -- validated-1 validated-2 validated-3 > rag_eval_report_hardening.md
./scripts/rag_eval_report.sh generalization gen-1 gen-2 gen-3 > rag_eval_report_generalization.md
./scripts/rag_eval_phases.sh blind-v3-1 blind-v3-2 blind-v3-3                 # 3 full runs, blind v3 (hash checked first)
./scripts/rag_eval_report.sh app/rag/evaluation/generalization_v3.json blind-v3-1 blind-v3-2 blind-v3-3 > rag_eval_report_blind_v3.md
./scripts/rag_eval_blind_v3_facts.sh blind-v3-1 blind-v3-2 blind-v3-3 > rag_eval_report_blind_v3_facts.md   # fact-level verdicts

docker compose exec -T rag-indexer python -m app.rag.evaluation run --retrieval-only                   # seconds, no LLM
docker compose exec -T rag-indexer python -m app.rag.evaluation run --dataset generalization --format json
./scripts/rag_eval_phases.sh baseline sql-first aggregations                                            # phases switched off/on
docker compose --profile test run --rm tests python -m tests.eval_deterministic generalization          # parser + SQL route, no models
```

Scorer integrity and TAH-143 (no model needed):

```bash
cd backend
PYTHONPATH=. python ../evaluation/scorer_integrity/rescore.py --write-runs          # re-score saved runs with the TAH-145 rules
PYTHONPATH=. python ../evaluation/scorer_integrity/fact_check_v2.py app/rag/evaluation/generalization_v3.json ../rag_eval_phase_<run>.json
PYTHONPATH=. python ../evaluation/tah143/claims_retrospective.py > ../evaluation/tah143/retrospective.md   # needs the seeded DB
```

Each result in a run file also has `review`: tags for answers the deterministic rules could not classify (`refusal_uncertain`, `answer_ids_partial`, `answer_ids_uncertain`). They are counted, never turned into a pass.

`rag_eval_phases.sh` does the following:

- rebuilds the images;
- stops the background indexer and re-indexes;
- writes `rag_eval_phase_<name>.log` (readable) and `.json` (every answer, source, check and metric);
- uses the generalization dataset for `gen-*` phases, v2 for `gen2-*`, the blind v3 file for `blind-v3-*` (after checking its frozen hash) and the historical one otherwise;
- refuses to overwrite an existing `rag_eval_phase_<name>.json` (set `OVERWRITE=1` to force it).

`rag_eval_report.sh` writes a Markdown report containing:

- configuration and dataset hash;
- metrics per run next to the baseline;
- stable failures, variable failures, new regressions and fixed cases;
- results by group;
- limits of the measurement.

It reads JSON files written before these metrics existed, so earlier series can be compared. The output files are git-ignored.

## Procedure for a change

1. Record the current series (3 runs) before the change.
2. Make one change at a time; run the automated suite (`pytest`, `npx vitest run`).
3. Run 3 evaluations with identical code, configuration and dataset.
4. Compare **per case** with `rag_eval_report.sh`: stable failures (3/3), variable failures (1–2/3), new regressions (passing in every baseline run, failing now).
5. If the checks changed, re-score the previous series from their saved answers before comparing.
6. Record results and regressions in the Linear issue of the area (project *PayFlow — AI Reliability & Quality*).
7. Do not change the parser in response to generalization failures before their baseline is saved. Cases used for a fix become regression cases.

## Reading the results

- A single run is not a result. Three runs show only the range.
- **Variability:** identical runs differed by up to 3 cases out of 50 (e.g. 45/48/46). Treat a difference within that range as noise, except in financial facts, isolation and abstention: these must stay at 100% in every run, and a single failure is investigated.
- Separate the metrics: a higher total can hide a financial or isolation regression.
- `docs/*.md` is part of the assistant's corpus: editing it changes retrieval and requires a new series. Files in `docs/*/` are not indexed.

## Assessing the validator

Warnings do not change pass/fail. For each run, read every answer that has a warning and classify it as a true or a false positive.

The saved JSON does not store source text, so offline re-checks rebuild the sources:

- payment documents from the seed;
- documentation chunks from `docs/architecture.md`, matched by chunk title.

The rebuild is valid only if the old validator reproduces the flags recorded in the run. For `validated` it reproduced all 6, so the rebuild was used for [ADR 0009](../decisions/0009-amount-verification-by-role.md).

## History

### Historical dataset

| Series | Code | Cases passed (3 runs) | Notes |
|---|---|---|---|
| baseline / sql-first / aggregations | phases off → on | 26 / 40 / 45 | one run each |
| final | SQL-first complete | 46 / 46 / 45 re-scored (45 / 48 / 46 original checks) | |
| targeted | `NO_EVIDENCE` marker, fact lines, §8 split | 45 / 44 / 44 | regressions from the marker |
| corrected | marker removed, attribute route, grammar | 49 / 49 / 48 | same answer behaviour as validated |
| validated | + answer validator, DB errors → 503, `answer_language` check | 50 / 49 / 50 | |
| hardening | + amount roles (ADR 0009), runner metrics, refusal routing in the scorer | 49 / 48 / 49 | answer text unchanged by the code. Failures variable: `docs-it-pii` and `docs-it-open-check` 2/3 each. Amount warnings 0. `amt-over` falsely denies a payment in 1 run but passes (TAH-139). |
| consistency | + backend-written lookups, plan coverage, contradiction guard (ADR 0011), language detector fix | 48 / 48 / 48 | Stable failures: `lookup-late-paid` (substring wording) and `docs-it-pii`. LLM calls 32 → 15, p95 latency 10.4–11.7 → 7.7–9.0 s. |
| consistency-4..6 | + TAH-138, TAH-134, TAH-135 | 48 / 48 / 48 | Same failures, every metric identical to `consistency-1..3`. |
| consistency-7..9 | + TAH-141 | 49 / 49 / 48 | `lookup-late-paid` stable; `docs-it-pii` 1/3. Other metrics identical. |

### Generalization dataset

| Series | Where | Result |
|---|---|---|
| deterministic v1.0.0 | cloud, no models | intent 89/103, filters 35/56, payment set 36/53, backend-written answers 20/36 (23/36 after manual review) |
| gen-1..3 | Mac, Qwen + bge-m3 | 74 / 73 / 75 of 117 (80 / 79 / 81 after manual review); financial facts 36–37/46, isolation 6/6; 42 stable failures, analysed in [generalization](generalization.md) |
| v1 after TAH-139 (regression) | cloud, no models | intent 89/103, filters 35/56, payment set 36/53, backend-written answers 61/77 |
| v2 deterministic | cloud, no models | intent 71/93, filters 31/44, payment set 31/38, backend-written answers 53/61 |
| gen-1..3 after TAH-139 (v1 regression) | Mac | 82 / 83 / 81 of 117 (baseline 74 / 73 / 75); 8 fixed, `g-id-bare` new variable failure |
| v1 / v2 after TAH-138 (exact re-score of saved runs) | cloud | v1 78 / 79 / 77, v2 69 / 72 / 70: 13 wrong Qwen counts or totals removed, 16 correct answers now refused |
| v1 / v2 after TAH-134 and TAH-135 (deterministic, regression) | cloud | filters v1 47/56, v2 35/44; payment set v1 47/55, v2 37/45; false absence and wrong-list answers on v2: 0 |
| **gen2-1..3** | Mac, Qwen + bge-m3 | **70 / 74 / 71 of 102**: first independent measurement after TAH-139; 13 of 27 stable failures come from the semantic route (TAH-138) |
| gen-p1..3, gen2-p1..3 after TAH-138, TAH-134 and TAH-135 (regression) | Mac, Qwen + bge-m3 | v1 92 / 91 / 91, v2 80 / 79 / 81; no wrong backend figure; 4 v1 cases lost to TAH-138 refusals; LLM calls v1 33 → 26, v2 34 → 24 |
| v1 / v2 after TAH-141 (deterministic, regression) | cloud, no models | filters v1 54/56, v2 43/44; payment set v1 50/55, v2 46/46; `unrecognized_payment_set` v1 7 → 0, v2 13 → 0; historical answers identical |
| gen-q1..3, gen2-q1..3 after TAH-141 (regression) | Mac, Qwen + bge-m3 | v1 99 / 101 / 101, v2 93 / 94 / 93; financial facts v1 42/46, v2 36/38; no false refusal v1 85/90, v2 75/77; no wrong backend figure; no v2 regression |
| **blind-v3-1..3** (first measurement, blind) | Mac, Qwen + bge-m3 | **105 / 106 / 105 of 150**; isolation 150/150; financial facts 19/33; 5 aggregates with a wrong figure written by the backend (TAH-142); full analysis in [generalization](generalization.md#11-first-measurement-mac-2026-11-18) |
| v1 / v2 / v3 after TAH-142 (deterministic, regression) | cloud, no models | false backend answers presented as complete: v3 7 → 0, v1 3 → 2, v2 0 → 0, historical 1 → 1; 3 false abstentions (criterion not representable); ADR 0013 |
| **blind-v3-s1..3, gen-s1..3, gen2-s1..3, consistency-10..12** after TAH-142 | Mac, Qwen + bge-m3 | v3 **110 / 108 / 111** (baseline 105 / 106 / 105); v1 98 / 101 / 100; v2 93 / 95 / 92; historical 49 / 48 / 47. False backend answers presented as complete on v3: **10 → 0 in every run** (the baseline count includes 3 lists missed by `fact_check.py`). 2 unjustified abstentions (q052, lookup-async). Isolation unchanged. Every other difference is Qwen variability on identical input. Analysis: [`evaluation/tah142/`](../../evaluation/tah142/README.md) |
| **TAH-145: scorer integrity** (re-score of the 24 saved runs above, same answers) | cloud/VM, no models | historical and v1 unchanged; v2 TAH-142 runs 93 / 95 / 92 → **91 / 94 / 91**, v3 TAH-142 runs 110 / 108 / 111 → **109 / 108 / 111**; pre-TAH-142 v3 baseline 105 / 106 / 105 → 106 / 108 / 106. Changes: q140 and `v2-oos-greeting` (content written after a refusal) now fail; 3 hidden false complete lists in the v3 baseline (q144, r007, r014) now caught by the scorer; a false absence by Qwen in `v2-typo-rejected` now caught; 3 short refusals recognised. Details: [`evaluation/scorer_integrity/`](../../evaluation/scorer_integrity/README.md). **From now on, compare new series with the `-rescored` baselines.** |
| TAH-143 / ADR 0014 (deterministic, regression) | VM, no models | 419 questions: only q090 (→ SQL ranking), q140, q142, `v2-oos-greeting` (→ `out_of_scope`) change route. Claim checks on 768 saved Qwen answers of the last two series: 31 wrong answers would be replaced, 0 correct ones. Real runs pending: [`evaluation/tah143/`](../../evaluation/tah143/README.md#6-real-evaluation-mac-pending) |
