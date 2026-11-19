# AI reliability

## What counts as a reliable answer

1. It is about the right payments (retrieval) of the authenticated merchant only (isolation).
2. Amounts, currencies, statuses, counts, ids and derived facts are exactly those in PostgreSQL (financial fact accuracy).
3. It contains what the question asks for (completeness).
4. Each fact cites a source that supports it (citations).
5. When evidence is missing, it says so instead of guessing (abstention), and it does not refuse when the answer is available (false refusals).
6. It is in the language of the question.
7. Repeated runs give factually equivalent answers (consistency); identical wording is not required.

Financial and isolation errors have priority over wording or language errors.

## Metrics

Measured by the evaluation runner (`app/rag/evaluation/`) on two datasets that are reported separately: 50 historical cases and 117 independent generalization cases. Each metric is `pass`, `fail` or `not_applicable` per case; see [evaluation](evaluation.md) for how.

| Metric | How it is measured |
|---|---|
| Financial fact accuracy | Expected fact (amount, status, count, total, reason) present in answers of `payment_by_id`, `payment_lookup`, `sql_lookup`, `aggregate` cases |
| Retrieval accuracy | Expected source among the evidence given to the model; exact set of payments for SQL lookups |
| Citation correctness | The answer cites the expected source |
| Completeness | Expected fact present in documentation answers |
| Correct abstention | Questions that must be refused are refused |
| False refusal rate | Answerable questions answered with a refusal |
| Language correctness | Answer language equals question language (all answers) |
| Tenant isolation | Another tenant's payment ids never in sources or answers |
| Parser generalization | Intent and filter accuracy on the generalization dataset (never on the historical one) |
| Answer warnings | Backend validation flags per run (`uncited`, `amount_not_in_sources`, `amount_wrong_payment`, `threshold_unverified`, `aggregate_unverified`, …); assessed for false positives by reading the flagged answers |
| LLM calls, latency | Mean and p95 per question |

## Measured state (2026-11-17, historical dataset)

Series `validated` (current code, 3 runs) next to `corrected` (same answer behaviour, before the validator):

| Metric | corrected-1 / 2 / 3 | validated-1 / 2 / 3 |
|---|---|---|
| Cases passed | 49 / 49 / 48 | 50 / 49 / 50 |
| Financial fact accuracy | 24/24 ×3 | 24/24 ×3 |
| Retrieval | 39/39 ×3 | 39/39 ×3 |
| Citations | 26 / 26 / 24 of 26 | 26/26 ×3 |
| Documentation completeness | 9 / 9 / 10 of 10 | 10 / 9 / 10 of 10 |
| Correct abstention | 12/12 ×3 | 12/12 ×3 |
| False refusals | 0/38 ×3 | 0/38 ×3 |
| Language (all answers) | 50/50 ×3 | 50/50 ×3 |
| Isolation | 4/4 ×3 | 4/4 ×3 |
| LLM calls | 32 | 32 |
| Latency mean / p95 | 5.0–5.1 / 11.6–12.3 s | 5.0–5.3 / 11.7–12.2 s |

No case fails in all six runs. Remaining failures are variable and limited to documentation answers: `docs-it-pii` incomplete (3 of 6 runs), one uncited and one irrelevant citation (1 of 6 each).

### Answer validator on real answers

| Check | Real flags | Assessment |
|---|---|---|
| `uncited` | 0 in `validated`; 3 when applied to the 366 saved model answers of all 12 runs | 3/3 correct; detects both citation failures found by the evaluation |
| `invalid_citation`, `unknown_payment_id`, `personal_data` | 0 in 366 answers | No false positives; no real occurrence, recall shown only by unit tests |
| Amounts, before ADR 0009 | 6 in `validated`; 25 in the 366 answers | **All false positives**: thresholds and range bounds repeated from the question |
| Amounts, by role ([ADR 0009](../decisions/0009-amount-verification-by-role.md)) | `hardening`: 0. `gen`: 2 true positives and 3 false positives (range with the currency only on the upper bound, now fixed). Re-checked on 662 saved answers of 18 runs: 0 false positives. | 221/221 answers flagged after one amount was raised by 1 € |
| `uncited` on the generalization runs | 37 | All correct; every non-refusal answer without a citation was flagged |
| Citation relevance | not checked | An irrelevant citation (1 of 6 runs) is not detectable deterministically |

**What is verified and how:**

| Status | Checks |
|---|---|
| Implemented and verified (unit, mutation and retrospective tests) | Threshold and range bounds against the applied SQL filter: value, direction, currency, strictness when unambiguous. Transactional amounts against the payment they are attributed to. Aggregates against backend-computed sources. EUR, USD and GBP. Italian and English formats. Integer cents. |
| Not implemented | Amounts without a currency. Status words in free text. Citation relevance. Consistency between a verified amount and the sentence around it. |
| Not verifiable deterministically | Whether a sentence is true given correct numbers, e.g. "no payment exceeds 90 €; the highest is 98 €" |

### Historical set after the validator change (`hardening-1..3`)

| Metric | Result |
|---|---|
| Cases passed | 49 / 48 / 49 |
| Financial facts | 24/24 ×3 |
| Citations | 26 / 25 / 25 |
| Abstention | 12/12 |
| False refusals | 0/38 |
| Language | 50/50 |
| Isolation | 4/4 |

Failures are variable: `docs-it-pii` and `docs-it-open-check`, 2/3 each. The code change does not touch the answer text, so the difference from `validated` is generation variability.

### Generalization: full runs (`gen-1..3`)

| Metric | Result |
|---|---|
| Cases passed | 74 / 73 / 75 of 117 (80 / 79 / 81 after removing 6 scorer or dataset errors) |
| Financial facts | 36–37/46 |
| Citations | 28–30/40 |
| False refusals | 9–11 of 90 |
| Language | 106–108/117 |
| Isolation | 6/6 |
| p95 latency | 19–23 s |

42 of 44 failures are stable. Causes:

- parser: 26;
- English aggregate templates: 5;
- Qwen with correct evidence: 7;
- scorer or dataset errors: 6.

**New:** Qwen denies payments that are in its evidence when it receives 8 payments (TAH-139). See [generalization](generalization.md).

### Financial evidence consistency (TAH-139, [ADR 0011](../decisions/0011-backend-written-lookups-and-plan-coverage.md))

Qwen denied payments that were among its sources. In 2 cases SQL had already answered the question exactly; in the other 2 the parser had dropped a filter and Qwen had to search 8 payments itself.

**The fix:**

- Lookups that ask for existence or a list are answered by the backend from SQL, with total, citations and the lookup limit stated.
- The parser reports what it did not interpret. An incomplete plan never produces "no payments" or a count or total figure.
- A model answer that denies payments SQL found is replaced, with the warning `contradicted_evidence`.

**Measured without models:**

| Measure | Result |
|---|---|
| Model denials of present payments that are no longer possible, because those cases are answered without Qwen | 11 in the 18 saved real runs |
| Answers with a complete plan wrongly replaced by the guard (489 saved SQL-route answers) | 0 |
| Historical backend-written answers | 30/31; the failure is substring wording |
| v2 backend-written answers | 53/61 |

**Residual risk on v2** (backend-written answers to a different question):

- 1 false absence ("non superano i 10 euro");
- 3 lists for misread questions;
- 2 counts for attribute questions.

**Real runs (2026-11-18):**

| Measure | Result |
|---|---|
| Denials of payments found by SQL, in 9 runs | 0 |
| Historical: LLM calls / p95 latency | 15 instead of 32 / 7.7–9.0 s |
| v1 (regression set): cases passed | 82 / 83 / 81 instead of 74 / 73 / 75 |
| v2 (independent): cases passed | 70 / 74 / 71 of 102 |

**The largest remaining source of wrong financial facts is the semantic route.** Questions the parser does not recognise as structured ("quanti checkout aperti ho?") are answered by Qwen from 5 chunks, which produced "due checkout aperti" (3 exist) and wrong totals (TAH-138).

### Generalization (deterministic baseline, 2026-11-17)

On 117 questions never used in development, with the current code and no models:

| Metric | Result |
|---|---|
| Parser intent | 89/103 |
| Parser filters | 35/56 |
| Exact payment set on the SQL route | 36/53 |
| Backend-written answers correct | 20/36 (23/36 after manual review of 3 language-detector errors) |

On the historical set the same harness scores 100%. **The parser does not generalize well.**

- **Weakest groups:**
  - dates: 1/7 filters;
  - colloquial phrasing: 2/8 intents;
  - typos;
  - combined filters.
- **Wrong answers written by the backend:** nine answers, written without Qwen, answer a different question from the one asked. One of them is a total of 444,70 € instead of 365,30 €.
- **Not yet measured:** answers written by Qwen and the semantic route. Details in [generalization](generalization.md).

### Evaluation integrity (TAH-145, 2026-11-19)

Two scorer defects could pass wrong answers: payment ids were checked for presence only (extra ids in a "complete" list went unseen), and any refusal phrase made an answer a refusal (an e-mail written after "the context does not contain…" passed). Both are fixed in `app/rag/evaluation/scoring.py`, with an explicit `uncertain` verdict where free text cannot be classified. Re-scoring the saved runs changed no historical or v1 result; on v2 and v3 it turned 4 hidden failures into failures and 3 missed refusals into passes. See [`evaluation/scorer_integrity/`](../../evaluation/scorer_integrity/README.md).

### Verified financial claims (TAH-143, [ADR 0014](../decisions/0014-verified-financial-claims.md))

Qwen still writes explanations and single-payment answers. On the blind set it stated a wrong status (q013), a wrong duration (r018), absences from a top-5 similarity search (q055, r015, v2 `v2-typo-rejected`), a ranking from 2 payments (q090), an arbitrary choice among payments of the same amount (q149), and out-of-scope content with an invented discount code (q140, q142).

The backend now checks those claims against the evidence it gave Qwen and replaces the answer when one is contradicted or cannot be supported; it computes elapsed times itself; it answers rankings and out-of-scope requests without Qwen.

| Measure (no models) | Result |
|---|---|
| Saved Qwen answers re-checked (last two series) | 768 |
| Wrong answers that would be replaced | 31 (all the TAH-143 cases above, in every run where they were wrong) |
| Correct answers that would be replaced | **0** (3 in 1,769 answers since `document_facts`, all from older code whose question now goes to SQL) |
| Deterministic regression, 419 questions | only the 4 intended routes change |
| Mutation testing | 27/27 killed |

**Real runs: pending** ([commands and closing criteria](../../evaluation/tah143/README.md#6-real-evaluation-mac-pending)). Until then this is verified behaviour on saved answers, not a measured improvement.

**What claim checks do not cover:** statuses or durations phrased outside the recognised patterns; contradictions with documentation prose (q099); denials of something other than payments; omissions. These answers are shown as before.

## Error classification

| Class | Example observed | Where it is handled |
|---|---|---|
| Retrieval miss | Right doc chunk ranked 8th inside a long mixed section | Section 8 split into subsections (doc structure, not chunker) |
| Ranking by look-alike | Payment with similar history cited instead of the right one | Other payments excluded when ids are quoted; derived facts written by the backend |
| Financial fact error | "4 pagati" counted from top-5 chunks | Deterministic SQL aggregates |
| Incomplete answer | Personal data question answered with bearer data only | Open (documentation answer, variable 2/3) |
| Missing citation | Correct answer without `[n]` | Warning `uncited`; measured, not corrected |
| Irrelevant citation | Cites [2] for a fact in [3] | Not detectable deterministically; measured by the eval |
| Unjustified refusal | Model declined with the answer in context after a prompt change | Prompt change reverted; regression tests |
| Wrong language | Refusal in English or Chinese to an Italian question | Backend normalization of refusals |
| Unavailable attribute | "Qual è l'email del cliente…?" | Deterministic "found, but not available" (but "numero di telefono" is parsed as a count: TAH-135) |
| Misparsed question | "dopo il 25 ottobre" read as "on 25 October"; "non riuscite" read as `paid` | Open: TAH-134, TAH-135. The answer states the criteria applied |
| Wrong language of a backend template | English aggregate question answered in Italian | Open: TAH-136 |
| Cross-tenant attempt | Other merchant's id or merchant id in the question | Scope from API key only; ids ignored or not found |

## Known limits

- The parser was written against the historical cases. On independent questions it reaches 89/103 intents and 35/56 filters, and some misparses produce deterministic answers to the wrong question (TAH-134, TAH-135). A deterministic answer is correct only for the plan it states.
- Lookups return at most 8 payments; dates are UTC calendar ranges.
- A normal answer in the wrong language is measured, not corrected; with long evidence Qwen sometimes answers Italian questions in English.
- Qwen can deny a payment that is among its sources. Lookups no longer ask it to. On explanation questions an explicit denial is replaced, but an omission is not detected (TAH-139).
- Counts, totals and lists of payments in a form the parser does not recognise are refused on the semantic route ([ADR 0012](../decisions/0012-no-payment-aggregates-from-semantic-retrieval.md)). Phrasings outside the detection vocabulary still reach Qwen.
  - Since TAH-141 the parser structures a product name, "checkout" or "vendite" as the subject, status adjectives, colloquial money phrases and misspelled nouns or months. No v1 or v2 question is refused this way any more.
  - Other nouns ("addebiti", "ordini in sospeso") are still refused.
- A misspelled payment noun, money verb or month is corrected before parsing, and the correction is recorded in the plan notes. The answer states the criteria applied, not the correction itself. Misspelled statuses are reported, not corrected.
- **Constraint coverage is a vocabulary** (ADR 0013, TAH-142). Every word of a backend-written answer's question must be explained by a recognised constraint, by the intent, or by a closed list of non-constraining words; otherwise no figure, no exhaustive list and no absence claim is written. Before this, a criterion the parser did not know was dropped silently: on blind v3, 5 counts and totals gave a wrong figure. After it, deterministically, no backend-written answer presented as complete is wrong on v1, v2 or v3, except two yes/no questions about "the" payment of a day or product (v1, outside this change).
  - **Measured on real runs** (3 per dataset, [`evaluation/tah142/`](../../evaluation/tah142/README.md)). On v3 there are no false backend answers presented as complete in any run; the baseline had 10 per run. Useful correct answers to answerable v3 questions are unchanged: 67/67/67, against 67/66/65 before.
  - **Cost:** 2 answers that were correct by coincidence became labelled partial answers, because their criterion is the failure or expiry reason, which the parser cannot filter on (q052, lookup-async). A third, r014, was in fact a false complete list before.
  - **Residual risks:**
    - a constraint expressed only with words on the closed list;
    - a vague qualifier missing from the ambiguity list (it is reported as not interpreted, not as ambiguous);
    - explanations by Qwen ("perché…"), which are outside this check.
- Plan coverage is a heuristic. A misreading it misses still yields a backend answer to another question, and each such answer states the criteria it applied. The known v2 cases ("non superano i 10 euro", "importo massimo", "confermati dopo che il checkout era scaduto") are fixed (TAH-134 and TAH-135, measured only on regression sets). Abbreviated months and vague periods are not handled.
- Citation relevance and completeness of free text are not verified by the backend.
- Three runs per series cannot estimate the distribution of results; differences of 1–3 cases out of 50 are within observed variability.
- Answer checks in the evaluation are substring heuristics with a negation guard, not semantic judgement.
- The amount check depends on Italian and English comparison words. An amount without a currency is not checked, and a verified amount can still sit in a false sentence.
- English aggregate answers are written in Italian (TAH-136). The language detector misreads short English sentences starting with "I" (TAH-137).
- All measurements use synthetic seed data on one laptop; production data volumes and phrasing are not represented.

## Requirements before production

- A new blind set. v3 (TAH-140) has been measured and is now a regression set; it was written by isolated LLM agents, not by a different person, so a human-written set remains the stronger test.
- Real runs of ADR 0014 (TAH-143) with the closing criteria in `evaluation/tah143/README.md`.
- Payment-set vocabulary: product names, "checkout", status adjectives and typos used instead of "pagamenti" are now refused (see [generalization](generalization.md)).
- English templates for aggregates (TAH-136).
- More runs per release (at least 5) and a fixed acceptance threshold per metric.
- Monitoring of `warnings` and refusals in production logs, without question text.
- Server-side sessions for the dashboard instead of a browser-held API key; Row-Level Security if more code paths reach the database.
