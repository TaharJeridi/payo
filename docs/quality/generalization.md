# Generalization dataset

Does the assistant understand questions it was not developed on? The 50 historical cases cannot answer that: the parser was written against them. See [ADR 0010](../decisions/0010-independent-generalization-dataset.md).

## Dataset

| | |
|---|---|
| File | `backend/app/rag/evaluation/generalization.json` |
| Version | 1.0.0, frozen 2026-11-17 |
| sha256 | `05c26453f7b48016680d653570b546a753ab3fe47d8ffe94bf77db2d39baadd3` (also in `generalization.sha256`; `tests/test_generalization_dataset.py` fails if the file changes) |
| Cases | 117: 95 Italian, 22 English |
| Data | The seed of `app/rag/evaluation/seed.py`: 20 payments of the main merchant and 2 of a second tenant, October 2026, EUR, three products (Starter 9,90 €, Pro 29,90 €, Financial Report Pack 49,00 €), all statuses, one payment recovered by reconciliation, one paid after expiry |

| Group | Cases | What varies |
|---|---|---|
| amount_lookup | 8 | exact amounts, ranges, "almeno", "fino a", "meno di" |
| status_lookup | 8 | synonyms ("non riuscite", "da pagare"), status names, English |
| id_lookup | 7 | questions around an id, a bare id |
| date_filter | 8 | single days, ranges, "dopo"/"prima", parts of a month, English dates, a month with no data |
| combined_filter | 8 | product + status, amount + status, quantity, date + status |
| aggregate | 10 | counts, totals, parts of a month, top product by revenue, English |
| events_reconciliation | 7 | recovery, paid after expiry, who confirmed, attempts, last error |
| negative | 7 | nothing matches; totals and counts that are zero |
| ambiguous | 6 | several payments share an amount or a day; relative date; "ultimo pagamento" |
| out_of_scope | 6 | weather, translation, marketing, sport, exchange rates, a non-existent plan |
| personal_data | 7 | phone, email, card, name, checkout link, Idempotency-Key, address |
| english | 8 | lookups, aggregates, documentation in English |
| colloquial | 8 | informal Italian, amount without currency, year omitted |
| typos | 7 | realistic misspellings |
| similar_values | 6 | 9,80 vs 9,90 €, 89,90 vs 89,70 €, upper-case id, id with one wrong character |
| adversarial | 6 | other tenant's merchant id, amount or payment id; prompt injection; "debug mode" |

## How expectations were set and checked

1. Each question was written first, as a merchant would ask it. Then the expected result was derived from the seed: the payment set (`expect_payments`), the count or total (`expect_count_filters` and `expect_total_filters`, computed with SQL at run time), or a refusal.
2. Each lookup carries `truth_filters`, the intended criteria, and each aggregate a hand-computed `truth_value`. The test then checks three things agree: the hand-written expectation, a pure-Python oracle over the seed definitions, and SQL on the seeded database. The parser and the assistant play no part. The check caught 1 wrong hand value out of 117 cases before the freeze: the first half of October totals 365,30 €, not 295,30 €.
3. `expect_intent` and `expect_filters` are the ideal interpretation. They are diagnostics, not pass/fail checks.
4. Interpretation choices are written in each case's `note`:
   - "ultima settimana di ottobre" = 25–31 October;
   - "ancora da pagare" = open;
   - "andati male" = failed;
   - a year left out means 2026.
5. A question must not copy a historical one; a test checks this.

## Baseline 1: deterministic components (cloud, 2026-11-17)

Reproduce with `docker compose --profile test run --rm tests python -m tests.eval_deterministic generalization` (or `historical`).

**What is measured:** the parser, the SQL route and the answers written by the backend, using the current code with no embedding model and no LLM. Every case runs; only the checks that do not depend on bge-m3 or Qwen are counted. Two runs gave identical results. The same harness on the historical set gives 45/45 intents, 12/12 filters and 13/13 payment sets.

| Group | Cases | Intent | Filters | Payment set (SQL route) | Backend-written answers correct | Semantic route (Mac) |
|---|---|---|---|---|---|---|
| amount_lookup | 8 | 8/8 | 8/8 | 8/8 | – | 0 |
| status_lookup | 8 | 7/8 | 6/8 | 6/7 | – | 1 |
| id_lookup | 7 | 7/7 | – | – | – | 0 |
| date_filter | 8 | 7/8 | 1/7 | 2/7 | 1/3 | 1 |
| combined_filter | 8 | 8/8 | 5/8 | 5/8 | 0/1 | 0 |
| aggregate | 10 | 9/10 | 6/8 | – | 6/10 | 0 |
| events_reconciliation | 7 | 7/7 | 2/3 | 2/3 | – | 0 |
| negative | 7 | 6/7 | 1/2 | 4/5 | 4/5 | 1 |
| ambiguous | 6 | 4/5 | – | 2/4 | 1/2 | 1 |
| out_of_scope | 6 | 6/6 | – | – | – | 6 |
| personal_data | 7 | – | – | – | 3/4 | 1 |
| english | 8 | 7/8 | 3/3 | 2/2 | 0/2 | 2 |
| colloquial | 8 | 2/8 | 0/2 | 0/1 | 2/2 | 6 |
| typos | 7 | 5/7 | 2/5 | 1/3 | 0/1 | 3 |
| similar_values | 6 | 5/5 | 1/2 | 3/4 | 3/3 | 0 |
| adversarial | 6 | 1/1 | – | 1/1 | 0/3 | 3 |
| **Total** | 117 | **89/103** | **35/56** | **36/53** | **20/36** | 25 |

How to read the table:

- "–" means no case in the group exercises that check.
- **Intent** is 71/84 on Italian questions and 18/19 on English ones.
- **Backend-written answers correct (20/36):** 3 of the 16 failures are scorer errors. They are short English answers that the language detector classifies as Italian (TAH-137). After manual review the figure is 23/36.
- **Not yet measured:**
  - 25 cases go to the semantic route, and the 56 SQL lookups need Qwen to write the answer.
  - Retrieval on the semantic route, citations, completeness and model answers wait for the real runs.
  - Only 35 of the 117 cases are fully measured here.

**Isolation:** in the 3 adversarial cases that reach SQL, no other tenant's payment was retrieved.

### Failure analysis

| Cause | Cases | Effect | Linear |
|---|---|---|---|
| Date expressions | 8 | "dopo il 25" / "prima del 4" become a single day, giving "no payments" when 4 or 2 exist. "Prima metà di ottobre" totals all of October: **444,70 € instead of 365,30 €**. "October 9" becomes the whole month. | TAH-134 |
| Negation, opposites, attributes | 8 | "Non riuscite" becomes `paid`. "Ancora da incassare" is answered with the total collected. "Prodotto che ha incassato di più" is answered with the total. "Numero di telefono" becomes a count. Status or quantity is lost next to an amount. A misspelled comparator becomes an exact amount. | TAH-135 |
| Aggregate answers in Italian to English questions | 5 | Language wrong; one count also lost its status (20 instead of 3) | TAH-136 |
| Language detector on short English text | 3 | Scorer reports correct English answers as Italian | TAH-137 |
| Colloquial phrasing and typos route to semantic | 8 | Effect on the answer to be measured on the Mac | TAH-138 |

**Most serious:** nine answers written by the backend, without Qwen, answer a different question from the one asked. The answers are the three "no payments", the wrong total, "incassato" given for "da incassare", the total given for the top product, the count given for a phone-number question, the misspelled threshold and the headline count of 20. They cite the SQL source, so they look verified. Each one states the criteria it applied, which is the only mitigation in place.

## Baseline 2: full runs (Mac, 2026-11-17)

`gen-1..3`: Qwen 2.5 7B, bge-m3, code `e9cfc0f` (fingerprint `a3030f09d86d`), dataset sha256 `05c26453f7b4…`. The parser was not changed between the deterministic baseline and these runs. The JSON files `rag_eval_phase_gen-{1,2,3}.json` are the independent baseline: keep them.

| Metric | gen-1 | gen-2 | gen-3 | Historical (hardening) |
|---|---|---|---|---|
| Cases passed | 74/117 | 73/117 | 75/117 | 49 / 48 / 49 of 50 |
| After manual review (6 scorer or dataset errors) | 80 | 79 | 81 | – |
| Intent / filters | 89/103, 35/56 | same | same | 45/45, 12/12 |
| Retrieval | 58/77 | 58/77 | 58/77 | 33/33 |
| Payment identification | 55/74 | 55/74 | 55/74 | 24/24 |
| Financial facts | 36/46 | 37/46 | 37/46 | 24/24 |
| Citation | 29/40 | 28/40 | 30/40 | 26 / 25 / 25 of 26 |
| Correct abstention | 25/27 | 25/27 | 25/27 | 12/12 |
| Answerable questions not refused | 79/90 | 80/90 | 81/90 | 38/38 |
| Isolation | 6/6 | 6/6 | 6/6 | 4/4 |
| Language | 107/117 | 108/117 | 106/117 | 50/50 |
| LLM calls | 74 | 74 | 74 | 32 |
| Latency mean / p95 (s) | 6.77 / 23.34 | 7.05 / 23.03 | 6.60 / 18.83 | 4.9–5.5 / 10.4–11.7 |

Failures are almost all stable: 42 cases fail in all three runs and 2 vary. The answers are wrong in the same way every time, so the errors are systematic, not noise.

### Manual review of the 44 failing cases

| Class | Cases | Notes |
|---|---|---|
| Scorer or dataset error: the system behaved correctly | 6 | Language detector on "I found no…" (3, TAH-137). Two adversarial requests labelled answerable, where a refusal is acceptable (`g-adv-other-merchant-list`, `g-adv-debug-all`). "Non ci sono pagamenti falliti" not matched by the expected "0" (`g-neg-report-failed-late`). To fix in v2, not in the frozen v1.0.0. |
| English aggregate answered in Italian | 5 | Figures correct except one headline count (TAH-136) |
| Parser: wrong plan, so wrong evidence or wrong backend-written answer | 26 | Dates (TAH-134), negations and missing filters (TAH-135), colloquial phrasing and typos (TAH-138) |
| Qwen with correct evidence | 7 | Correct content without citations (3, all flagged `uncited`). Citation to a payment instead of the doc (2 cases, 2/3 and 1/3). One incomplete lookup. One garbled refusal ("Non know.") to an address question, with no personal data. |

### What the Qwen answers add to the deterministic baseline

- **False negatives over long evidence (new, TAH-139).**
  - With a missing filter the SQL route gives Qwen 8 payments plus 2 documentation chunks. Qwen then says that a payment **in its evidence** does not exist, in 3/3 runs:
    - "no PayFlow Pro payment with quantity 3": `pro3_paid` is source 5;
    - "no failed PayFlow Pro payment": `pro_failed_async` is source 4;
    - "nessun altro pagamento superiore a 89,70 €": `report2_paid` (98,00 €) is in the evidence.
  - Two of these answers are in English to an Italian question.
  - The historical set shows the same pattern on `amt-over` in 2 of 9 runs ("Non ci sono pagamenti che superano 90 euro… il più alto è 98,00 €"). The evaluation passed that answer because it contains "98".
- **Semantic fallback for misparsed questions** (12 cases):
  - 5 correct;
  - 4 refusals (safe);
  - 1 scorer error;
  - 2 wrong facts computed by Qwen from 5 chunks: "c'è un solo pagamento andato male" when there are 3, and a wrong "ultimo pagamento".
- **Isolation held:** 6/6 in every run. Out-of-scope questions were refused 6/6.
- **Latency:** p95 roughly doubles (19–23 s) because answers are longer when evidence holds 8 payments.

### Validator on these answers

| Warning | Count (3 runs) | Assessment |
|---|---|---|
| `uncited` | 12 / 12 / 13 | All correct. Every non-refusal answer without a citation was flagged. |
| `threshold_unverified` | 1 / 1 / 0 | **True positive:** "superiori a 89,70 €" for a filter "almeno 89,70 €", in a sentence that is also false |
| `amount_not_in_sources` | 1 / 1 / 1 | **False positive,** fixed after the runs: "tra 40 e 50 euro", with the currency only on the upper bound, was not read as a range. Re-checked on the saved answers of all 18 real runs (662 model answers): 0 false positives, 2 true positives, 221/221 answers flagged when an amount is raised by 1 €. |

## After TAH-139: v1 becomes a regression set, v2 measures

The fixes for TAH-139 ([ADR 0011](../decisions/0011-backend-written-lookups-and-plan-coverage.md)) were developed on v1 failures:

- plan coverage;
- backend-written lookups;
- the contradiction guard;
- the language-detector fix (TAH-137).

**v1 is therefore a regression set from now on.** Its numbers are no longer a generalization claim.

### Scorer changes, applied to every dataset

- `allow_refusal`: a case where refusing is acceptable checks only isolation. It was added for adversarial requests in v2. v1 is frozen and does not use it.
- A zero count is also matched by "nessun", "non ci sono" and "there are no".
- Backend-written refusals count as refusals in either language; an inconclusive `sql_partial` without evidence counts as an abstention.
- The language detector ignores ids and identifiers. Re-scoring the 18 saved real runs changes 9 verdicts, all expected: the 3 TAH-137 cases in each of the 3 gen runs.

### Dataset v2

| | |
|---|---|
| File | `backend/app/rag/evaluation/generalization_v2.json` (`--dataset generalization-v2`) |
| Version | 2.0.0, frozen 2026-11-17 |
| sha256 | `3cc7194e4b0f59e8713390e09d6a950676e6fb5a3c1b590c2070434ea36cc089` |
| Cases | 102 (81 Italian, 21 English), same 16 groups, no question copied from the historical set or v1 |
| Checks | Every expectation agrees with the hand value, the Python oracle and SQL (0 disagreements) |

**Limit:** v2 was written by the same author as the fixes, after seeing v1's failures. It is new data, not a blind test. A set written by someone else would be stronger evidence.

### Deterministic results after TAH-139 (cloud, no models, code `e9498a42b689`)

Parser, SQL route and backend-written answers only. v2 was run once, after the code was final, and nothing was changed afterwards.

| | Historical | v1 (regression) | **v2 (new)** |
|---|---|---|---|
| Intent | 45/45 | 89/103 | **71/93** |
| Filters | 12/12 | 35/56 | **31/44** |
| Exact payment set on the SQL route | 13/13 | 36/53 | **31/38** |
| Backend-written answers correct | 30/31 | 61/77 | **53/61** |
| Answered without Qwen | 31 | 77 | 61 |
| Incomplete plans detected (`sql_partial`) | 0 | 14 | 4 |
| Complete plan, "no payments", but payments exist | 0 | 1 | **1** |
| Complete plan, list for a different question | 0 | 4 | **3** |

`lookup-late-paid` (historical) fails its substring check. The backend writes "pagato dopo la scadenza della sessione"; the case expects "scadut". The answer is correct, but the expectation is not changed.

### v2 failures in backend-written answers (8)

| Case | Answer | Cause |
|---|---|---|
| `v2-amt-not-over-10` | "Non ho trovato pagamenti con questi criteri: importo 10,00 €." | **False absence.** "Non superano" is not detected as a negated comparator: the coverage negation list has no comparison verbs. |
| `v2-amt-max-1980` | 1 payment of exactly 19,80 € | "massimo" without "al" is not a parser comparator, and coverage does not flag it |
| `v2-ev-confirmed-after-expiry` | the 3 expired payments | "confermati dopo che il checkout era scaduto" is read as status expired |
| `v2-ev-attempts`, `v2-pii-mobile` | "Hai 1 pagamento con id …" | "quante riconciliazioni", "numero di cellulare" read as counts (TAH-135) |
| `v2-agg-en-paid`, `v2-en-unknown-count`, `v2-en-oct-gross` | Italian answer to an English question | TAH-136 |

Not counted above: 4 incomplete plans that safely abstain (`v2-date-en-oct27`, `v2-date-20-22`, `v2-date-second-half`, `v2-typo-paid-count`). Each costs an answer, but none states a wrong fact.

Answers written by Qwen on v2 (explanations, single-payment questions, the semantic route) are measured only by the real runs below.

### Real runs after TAH-139 (Mac, 2026-11-18, code fingerprint `e9498a42b689`)

All runs used the same code as the deterministic results above. The v1 baseline JSON files are kept in `baseline_v1/`.

| Metric | Historical `consistency` | v1 regression `gen` (baseline) | **v2 `gen2` (new, first run)** |
|---|---|---|---|
| Cases passed | 48 / 48 / 48 (49 / 48 / 49) | 82 / 83 / 81 (74 / 73 / 75) | **70 / 74 / 71 of 102** |
| Financial facts | 23/24 ×3 (24/24) | 39/46 (36–37) | 28–29/38 |
| Citations | 26/26 ×3 (25–26) | 35–36/40 (28–30) | 23–25/31 |
| Correct abstention | 12/12 | 24/27 (25) | 22–24/25 |
| Answerable questions refused | 0/38 | 13/90 (9–11) | 7–8/76 |
| Isolation | 4/4 | 6/6 | 5/5 |
| LLM calls | 15 (32) | 33 (74) | 34 |
| Latency mean / p95 | 2.1–2.3 / 7.7–9.0 s (4.9–5.5 / 10.4–11.7) | 2.2–2.3 / 9.9–10.5 s (6.6–7.1 / 18.8–23.3) | 3.6–3.9 / 15.6–15.8 s |

**TAH-139 criterion: met.**

- The four cases in the issue no longer reach Qwen: they are answered as `sql_list` or `sql_partial`.
- In the 9 runs, no Qwen answer on the SQL route denied a payment that SQL had found.
- The guard fired once per run, always on `g-id-info-rejected`, which invented "non ci sono pagamenti successivi a questo".

**Historical set:**

- `lookup-late-paid` fails 3/3, as predicted (substring wording; the answer is correct).
- `docs-it-pii` fails 3/3 (before: 5 of 9). It is a documentation answer the change does not touch.

**v1 (regression set):** 8 cases fixed. One new variable failure: `g-id-bare`, answered in English in 1 of 3 runs. False refusals rise from 9–11 to 13, because partial plans now abstain instead of answering wrongly.

**v2, the new independent result.** 27 stable failures:

| Cause | Cases |
|---|---|
| Question not recognised as structured, so it goes to the **semantic route**, where Qwen lists, counts or sums from only 5 chunks | 13 |
| Misread plan answered by the backend (false absence "non superano i 10 euro", "massimo", "confermati dopo che…", attribute counts) | 7 |
| Partial plan, safe abstention | 4 |
| English aggregate in Italian (TAH-136) | 3 |

**Wrong financial facts written by Qwen on the semantic route** (stable or 2 of 3):

- "Hai due checkout aperti": there are 3;
- "Cinque pagamenti PayFlow Starter… ottobre": 3 paid, 6 in total;
- "un totale di 147,00 €" or "98,00 €" for the Financial Report Pack: 196,00 €;
- "only one purchase of PayFlow Starter".

These questions never reach SQL, so neither ADR 0011 nor the contradiction guard applies to them (TAH-138).

**Validator on v2:**

| Flag | Assessment |
|---|---|
| `amount_not_in_sources` on "un totale di 147,00 €" | True positive: the total is wrong |
| A Qwen-computed total that happens to be right (2) | Flagged by design: not computed by the backend |
| "Importo totale: 89,70 €" for a single payment in an answer without citations | False positive |
| A wrong total of 98,00 € that equals one payment's amount | Not detected |

### TAH-138: semantic-route abstention ([ADR 0012](../decisions/0012-no-payment-aggregates-from-semantic-retrieval.md))

This fix was developed on v2 failures, so **v2 is now a regression set as well**. Questions that ask for a count, a total or a list of payments and that the parser cannot structure are refused without Qwen.

The abstention is deterministic and no other code path changed, so the saved runs can be re-scored exactly:

| | Before | After |
|---|---|---|
| v1 | 82 / 83 / 81 | 78 / 79 / 77 |
| v2 | 70 / 74 / 71 | 69 / 72 / 70 |

What changed:

- **Wrong answers removed:** 13 counts or totals written by Qwen ("due checkout aperti", "totale 147,00 €", "cinque pagamenti", "un solo pagamento andato male").
- **Correct answers lost:** 16, now refused. They had been right because the matching payments happened to be in the 5 chunks.

The next independent measurement needs a new set (v3), preferably written by someone other than the developer.

### TAH-134 and TAH-135: parser fixes (2026-11-18, code `254cc5f66e25`)

These fixes were developed on v1 and v2 failures; both sets are regression sets for them.

The parser now handles:

- **Day ranges:** "tra il 1 e il 5", "dal 20 al 22".
- **Day operators:** "dopo il" (from the next day), "prima del" (up to that day), "dal" or "a partire dal", "fino al" or "entro il" (inclusive), "after", "before", "since", "until", "by".
- **Parts of a month:** first or second half (1–15, 16–end), first week (1–7), last week (the last 7 days).
- **English dates** such as "October 9, 2026"; "from October 27" means that day.
- **Negated comparators and bounds:** "non superano", "non più di", "not more than" (maximum, inclusive); "non inferiori a", "no less than" (minimum, inclusive); "massimo X".
- **Articles after a comparator:** "superano i 90 euro".
- **Quantity written after the product:** "con quantità 3", "quantity 3".
- **"Dopo che il checkout era scaduto"** and "after their checkout session expired" are read as paid after expiry.
- **A plural list with an exact amount keeps its status filter:** "Pagamenti da 29,90 € pagati".
- **Not counts:** questions about one payment id or about a restricted attribute ("numero di telefono") are lookups, not counts.
- **"Awaiting payment"** means open.

Plan coverage knows these forms, so they are no longer reported as unresolved.

Deterministic results (parser and SQL route; the historical set is unchanged at 45/45 intents, 12/12 filters, 13/13 payment sets, 30/31 answers):

| | v1 before → after | v2 before → after |
|---|---|---|
| Filters | 35/56 → **47/56** | 31/44 → **35/44** |
| Exact payment set on the SQL route | 36/53 → **47/55** | 31/38 → **37/45** |
| Partial plans (abstentions) | 14 → 5 | 4 → 1 |
| Complete plan, false "no payments" | 1 → 1 | 1 → **0** |
| Complete plan, list for another question | 4 → 1 | 3 → **0** |

**Still not handled:**

- abbreviated months ("Oct 4");
- vague periods ("a metà ottobre", "verso l'inizio di ottobre", "nella settimana del 12"), which are reported as unresolved;
- negated statuses ("non riuscite", "non sono stati pagati"), also reported as unresolved;
- yes/no questions about several payments ("Il pagamento di PayFlow Pro è andato a buon fine?").

These numbers come from regression sets. Generalization must be measured on v3 (TAH-140).

### Real runs after TAH-134 and TAH-135 (Mac, 2026-11-18, code `254cc5f66e25`)

Three runs per dataset, Qwen 2.5 7B and bge-m3, compared with the series after TAH-139 (`e9498a42b689`). These runs include TAH-138 as well, so the series compared with the TAH-139 runs measures both changes.

| | historical | v1 (regression) | v2 (regression) |
|---|---|---|---|
| Runs | `consistency-4..6` | `gen-p1..3` | `gen2-p1..3` |
| Cases passed | 48 / 48 / 48 (was 48 / 48 / 48) | **92 / 91 / 91** (was 82 / 83 / 81) | **80 / 79 / 81** (was 70 / 74 / 71) |
| Filters | 12/12 | 47/56 (was 35/56) | 35/44 (was 31/44) |
| Payment identification | 24/24 | 65/74 (was 55/74) | 52/63 (was 46/63) |
| Financial facts | 23/24 | 38 / 37 / 37 of 46 (was 39) | 30/38 (was 29 / 29 / 28) |
| Correct abstention | 12/12 | 26/27 (was 24/27) | 25 / 24 / 25 of 25 (was 23 / 24 / 22) |
| No false refusal | 38/38 | 76/90 (was 77/90) | 61/76 (was 68–69/76) |
| Isolation | 4/4 | 6/6 | 5/5 |
| LLM calls | 15 | 26 (was 33) | 24 (was 34) |
| p95 latency | 6.9–8.4 s (was 7.7–9.0) | 7.8–9.2 s (was 9.9–10.5) | 10.0–11.6 s (was 15.6–15.8) |

- **Historical:** the same two stable failures (`docs-it-pii`, `lookup-late-paid`), no regression.
- **v1:** 13 cases fixed in all three runs. The 4 new failures (`g-st-unknown`, `g-typo-expired`, `g-col-checkout-expired`, `g-neg-report-failed-late`) are TAH-138 refusals of questions Qwen had answered correctly.
- **v2:** 9 cases fixed, no new failure. Lower "no false refusal" comes from TAH-138: 13 cases now go to `unrecognized_payment_set`.
- **Financial facts:** every failure was read. None is a wrong backend figure. They are refusals, Qwen non-answers, and two older problems:
  - `g-amb-last-paid` (semantic route): Qwen names the wrong "last" payment in 2 of 3 runs, as in the earlier series.
  - `g-amb-oct28-paid`: the yes/no question becomes a `paid` filter and gets "no payments found" instead of "it is open".
- `contradicted_evidence` fired on `g-id-info-rejected` in 2 runs, as in the earlier series; the final answer is correct.

**Main remaining cause: the payment-set vocabulary.** Most new refusals, and most stable failures on v2, are payment-set questions the parser does not recognise:

- a product name instead of "pagamenti" ("Starter pagati a ottobre", "quanti soldi mi ha portato il Report Pack");
- "checkout" ("quanti checkout aperti");
- status adjectives without a noun ("Pagamenti rifiutati", "ancora aperti");
- typos in the noun or month ("pagamneti", "ottbre", "incasato");
- colloquial money words ("quanti soldi").

On the saved earlier runs, Qwen answered some of these correctly (196,00 € for the Report Pack). Others it answered wrongly ("due checkout aperti" instead of 3; Starter "9,90 €" instead of 69,30 €).

### TAH-141: payment sets named without "pagamenti" (2026-11-18)

The fix stays in the deterministic parser: none of these questions goes back to Qwen. It was developed on the v1 and v2 failures above, so both sets are regression sets for it.

| Form | Example | Plan |
|---|---|---|
| Product, "checkout" or "vendite" counted | "Quanti Financial Report Pack sono falliti…", "Quanti checkout aperti ho?", "quante vendite…" | count; "vendite", "sales" and "incassi" imply `paid` when no status is given |
| Noun or product followed by a status adjective | "Pagamenti rifiutati", "Starter pagati a ottobre", "Financial Report Pack scaduti o falliti", "quelli pagati col Pro" | find, unless the question also has an explanation word or a modal ("possono", "can") |
| List verb with a plural noun and no filter | "Elenca tutti i pagamenti" | find over all payments (8 most recent shown, the rest counted) |
| Colloquial money phrase | "quanti soldi ho fatto", "quanto ho tirato su", "how much did … bring in" | total |
| List of income | "Mostrami gli incassi da 49,50 €" | find of paid payments, not a total |
| Status phrases | "andati male", "non riuscite", "unsuccessful" → failed; "non si conosce l'esito" → creation unknown | filters |
| Other forms | "salvati dalla riconciliazione" → recovered by reconciliation; "più unità vendute" → top product; bare "Pro" after a preposition → PayFlow Pro | filters or intent |

**Typos:** misspelled payment nouns, money verbs and months ("pagamneti", "incasato", "ottbre") are corrected before parsing. The rule needs the same first three letters, a length difference of at most 2 and a similarity of at least 0.85. Each correction is added to the plan notes, and the answer states the criteria applied. Misspelled statuses ("fallitti", "pagatti") are not corrected: plan coverage still reports them, so the answer is partial instead of guessed.

**Tests:** 764 backend tests pass. All 25 mutants of the new rules were caught by the tests. The first run left 2 alive, a list request phrased as an explanation and "mese scorso" read as a vague period; tests for both were added, and the first case showed a real defect. After the fix the deterministic results below are identical, case by case. Ruff and mypy are clean.

**New safeguards:**

- vague periods ("last spring", "trimestre scorso", "l'anno scorso") are reported as unresolved terms;
- "quanti minuti", "how long", modal questions ("I pagamenti scaduti possono essere pagati?") and list verbs used in an explanation ("Mostrami come vengono riconciliati i pagamenti") are no longer treated as payment-set questions;
- a list request that mentions other merchants ("di tutti i merchant", "degli altri merchant", "every tenant's") returns the merchant's own payments followed by the notice that other merchants are ignored.

**Deterministic results** (cloud, no models; code `254cc5f66e25` before, `9b6f609942e8` after):

| | v1 before → after | v2 before → after |
|---|---|---|
| Intent | 89 → **96** / 103 | 72 → **87** / 93 |
| Filters | 47 → **54** / 56 | 35 → **43** / 44 |
| Exact payment set | 47 → **50** / 55 | 37 → **46** / 46 |
| Backend-written answers correct | 67/84 → **76/86** | 57/73 → **69/74** |
| Refused as `unrecognized_payment_set` | 7 → **0** | 13 → **0** |

- Every case that changed passes all its checks, except `v2-agg-en-starter`. Its figure is now correct (69,30 €), but in Italian, which is TAH-136.
- The historical set is identical answer for answer: 45/45 intents, 12/12 filters, 13/13 payment sets, 30/31 answers.
- The cases still refused on v1 and v2 (`unrecognized_payment_set`) went from 20 to 0. Phrasings outside the new vocabulary are still refused ("Quanti addebiti ho fatto?", "Pagamenti in sospeso").

These numbers come from regression sets. Generalization must be measured on v3 (TAH-140).

### Real runs after TAH-141 (Mac, 2026-11-18, code `9b6f609942e8`)

Three runs per dataset with Qwen 2.5 7B and bge-m3, compared with the runs after TAH-134 and TAH-135.

| | historical | v1 (regression) | v2 (regression) |
|---|---|---|---|
| Runs | `consistency-7..9` | `gen-q1..3` | `gen2-q1..3` |
| Cases passed | 49 / 49 / 48 (was 48 / 48 / 48) | **99 / 101 / 101** (was 92 / 91 / 91) | **93 / 94 / 93** (was 80 / 79 / 81) |
| Financial facts | 23/24 | 42/46 (was 37–38) | 36/38 (was 30) |
| No false refusal | 38/38 | 85/90 (was 76) | 75/77 (was 61/76) |
| Correct abstention | 12/12 | 26/27 | 24 / 25 / 25 of 25 (was 25 / 24 / 25) |
| Isolation | 4/4 | 6/6 | 5/5 |
| LLM calls | 15 | 25 (was 26) | 23 (was 24) |

- **Fixed in all three runs:** 10 v1 cases and 14 v2 cases, all of them listed in TAH-141. On v2 there are no new failures.
- **Historical:** `lookup-late-paid` still fails on the expectation's wording. `docs-it-pii` failed in 1 run of 3, as in earlier series.
- **Flagged as new v1 regressions:** `g-id-bare` (language) and `g-typo-docs-stripe-down` (citation), each in 1 run of 3. Their routes are the same as before and both answers come from Qwen, so this is run-to-run variability.
- **Financial facts:** every failing answer was read, and none is a wrong figure written by the backend. The remaining ones are:
  - `lookup-late-paid` (wording);
  - `g-amb-oct28-paid`: a yes/no question read as a `paid` filter;
  - `g-amb-last-paid`: Qwen names the wrong "last" payment, now in all 3 runs;
  - Qwen non-answers: `g-en-top-units`, `v2-col-report-98`.
- **Warnings:**
  - `contradicted_evidence` on `g-id-info-rejected` in every run, with a correct final answer, as before.
  - One `aggregate_unverified` on `v2-id-all-about`: Qwen calls a single payment's amount "importo totale" (89,70 €, correct). It is a false positive of the amount validator; the answer is unchanged, because warnings never alter it.
- **Still failing on v2:** English answers to aggregates (TAH-136: `v2-agg-en-paid`, `v2-agg-en-starter`, `v2-en-oct-gross`, `v2-en-unknown-count`), the system-prompt request answered by Qwen in English, a status typo answered as partial (`v2-typo-paid-count`, by design), and two colloquial questions about one product ("il report da 98 è andato?", "Il Financial Report Pack è stato pagato?").


## Blind dataset v3 (TAH-140)

v1 and v2 were written by the developer, who knew the parser. v3 is meant to be blind: it was written without knowledge of the system's errors, implementation or earlier datasets, and frozen before any run. Everything below is in [`evaluation/blind_v3/`](../../evaluation/blind_v3/README.md); the dataset is `backend/app/rag/evaluation/generalization_v3.json`.

| | |
|---|---|
| Version | 3.0.0, frozen 2026-11-18 |
| sha256 | `e5454c628bdd3e63e89e12ae51a96779e0e5b434f3952d0ac08daf2e2b0d61e8` |
| Cases | 150: 89 Italian, 61 English |
| Code at freeze | fingerprint `9b6f609942e8`; no code changed for v3 (the runner loads the file by path) |
| Status | Frozen. No parser, routing or Qwen call on any v3 question before freezing. **First measured on 2026-11-18** (`blind-v3-1..3`), so it is now a regression set for any fix developed on its failures. |

### 1. Isolated agents

Three independent LLM sub-agents (general-purpose type), each started with a fresh context. The tool and model identity are withheld in the public repository; they are recorded in the private originals of the provenance files (see [`evaluation/blind_v3/README.md`](../../evaluation/blind_v3/README.md#public-copy)). None of them received this conversation, the development plan or any information about regressions.

| Agent | Role | Inputs | Tool calls (from its transcript) |
|---|---|---|---|
| `payflow-blind-eval-designer` | 150 questions with golden answers | specification | Read spec, Write output, 1 Edit on its own output (a typo in one key fact; outside the stated rules, recorded) |
| `payflow-blind-eval-designer` (second run) | 19 blind replacements | specification + list of the other v3 questions | Read ×2, Write |
| `payflow-blind-eval-reviewer` | independent review | specification, dataset, oracle results | Read ×6 (one file in parts), Write |

The tool restrictions were instructions, not a sandbox. Compliance was checked afterwards on each agent's transcript (`agent_audit.json`): no agent opened any other file, ran a command or used another tool.

### 2. What the agents received

They received only `specification.md`, written for this purpose:

- a neutral product description;
- the payment data model and statuses, with their meaning;
- the derived facts and financial definitions;
- the product catalog;
- the 20 synthetic payments, generated directly from the seeded database, with their full status histories;
- the existence of a second merchant and its confidential ids;
- the assistant's public contract: what it must answer, refuse or declare unavailable;
- the privacy rules;
- the public documentation verbatim (`docs/architecture.md` sections 1–7 and 9).

### 3. What was excluded

- The historical, v1 and v2 datasets, and any earlier score or result.
- The parser, routing and prompt code.
- Linear issues, ADRs and `docs/quality`.
- `docs/architecture.md` section 8 (the assistant, including SQL-first routing) and section 10 (known limitations).

The suggested mix of questions followed the public capabilities, not the failures of v1 or v2.

**What leaked anyway.** The agents' context carried some metadata:

- the attached project name and the file name of one project document (not its content);
- the tool list;
- for the first agent only, the working directory `payflow-user/evaluation/blind_v3`.

The other two ran from a directory outside the repository.

### 4. Golden answers

- **Generator:** wrote every expected answer by hand from the data and a structured query over neutral fields (status, product, amount, dates, derived facts).
- **Independent oracle (`oracle.py`):** recomputes every structured expectation twice, without the parser, the repository or the assistant:
  - with SQL written for this purpose on `payments` and `payment_events`;
  - with Python on the seed definitions.

  Criteria the query schema cannot express ("quantity > 1", "on weekends", "went through `creation_unknown`") get an explicit verification query (`verification_queries.json`).
- **Result:** 125 expectations, including every interpretation of the ambiguous cases. SQL and Python agree, and both agree with the dataset.
- **No model involvement:** Qwen was never used, and no golden was taken from a system answer.
- **Reviewer:** checked every id, amount, count, date and documentation fact against the specification by hand. It found no wrong golden answer.

### 5. Review and resolutions

The reviewer rated 135 cases ok, 13 minor and 2 major. Both major findings were undeclared ambiguities (q038 "came in" = created or paid; q063 "ha comprato" = any status or paid). Disagreements were resolved without looking at any system answer, and all 15 resolutions are in `review_resolution_log.json`:

- **8 cases became "clarify or answer under a stated interpretation":** q035, q038, q040, q062, q063, q079, r004, r016. Each interpretation has its own oracle-checked answer. r004 (the mid-month boundary changes the amount) was found while building the typed facts, not by the reviewer.
- **Pass criteria made precise:** q082 (a declared inability without a number also passes), q091, q125.
- **4 cases annotated, golden unchanged:** q059, q061, q021, q113.
- **No question was reworded.**

### 6. Independence from earlier datasets

The overlap check ran only after generation. It compares each v3 question with all 269 historical, v1 and v2 questions, after normalising ids, numbers, accents and punctuation. It uses a sequence ratio and token Jaccard.

**Rule, fixed before deciding:** a case is replaced when it is an exact or cosmetic duplicate, or when it is near-identical or similar with the same meaning (same measure and filter kinds, differing only in values or surface words).

- **First generation:** 4 duplicates (3 exact, 1 cosmetic) and 15 same-meaning near-copies, for example "Quanto ho incassato a ottobre?" and "Pagamenti del 28 ottobre". All 19 were replaced by a second blind generation, which kept their area and language and saw no earlier dataset.
- **All 19 replacements:** independent.
- **Final set:** 129 independent, 17 similar and 4 near-identical in wording but different in meaning. For example, "il pagamnto <id> e andato a buon fine??" is close to "Il pagamento di ieri è andato a buon fine?" in wording only. They are listed in `independence_final.json`.

| Area | Cases | Area | Cases |
|---|---|---|---|
| find | 31 | other merchant / manipulation | 9 |
| documentation | 22 | ambiguous | 8 |
| payment by id | 15 | not recorded | 7 |
| count | 15 | personal data | 7 |
| total | 15 | ranking | 6 |
| payment explanation | 10 | out of scope | 5 |

- **Expected behaviour:** answer 97, refuse 19, clarify or interpret 16, no match 11, not available 7.
- **Phrasing:** precise 51, colloquial 40, terse 26, multi-part 15, indirect 9, polite 5, typo 3, formal 1. 31 questions quote a payment id.

### 7. Immutability

- **Hash:** `generalization_v3.sha256` stores the dataset hash.
- **Manifest:** `MANIFEST.json` records:
  - the stages (generated → verified → reviewed → frozen → first measurement pending);
  - the generation configuration, the agents and their tool calls;
  - the hash of every provenance file.
- **Tests** (`backend/tests/test_generalization_v3.py`) check:
  - the hash and the manifest;
  - that the file loads in the runner;
  - that every golden matches the Python oracle;
  - that the file is rebuilt byte for byte from the reviewed questions;
  - the fact checker on synthetic answers.
- **`rag_eval_phases.sh`** refuses a `blind-v3-*` phase when the hash differs, and never overwrites an existing `rag_eval_phase_<name>.json`.
- **Earlier hashes:** two intermediate hashes exist from rebuilds before freezing (typed facts added, r004 resolved). No system run happened before or between them, and they are listed in the manifest.

### 8. Scoring

The runner's checks apply as usual:

- refusal;
- exact payment set for lists of at most 8;
- count, amount or product substrings;
- documentation source;
- isolation, with the other tenant's ids forbidden in every case.

`fact_check.py` adds a fact-level verdict per answer:

- **Typed facts:** ids by prefix, amounts in any format, counts, statuses and failure reasons in Italian or English, products. For rankings, also the winner's units or amount.
- **Ambiguous cases:** pass with any fully correct interpretation. An answer that asks back is listed as a clarification for manual review.
- **Documentation answers:** pass on behaviour only; their key facts are prose, for manual review.

### 9. Limits of independence

- The generator and the reviewer are the same model family as the assistant that developed the parser. They are not humans and not "someone other than the developer" in the full sense TAH-140 asked for.
- That same developer assistant prepared the specification, the prompts, the replacement slots, the runner conversion and the resolutions. It never reworded a question or chose one.
- Tool restrictions were enforced by instructions and verified afterwards, not sandboxed. Some metadata reached the agents (section 3).
- The data are 20 synthetic payments. Questions can only probe what these data contain.
- **Gaps noted by the reviewer:**
  - several questions reduce to the same payment (q032, q034 and q078 all to P08);
  - no list or count asks about "paid after expiry";
  - the 8-payment list limit is exercised by only two questions;
  - English questions contain no typos.
- Relative dates ("ieri", "questo mese", "il mese scorso") assume a run between 2026-11-16 and 2026-11-30 UTC. A later run invalidates those cases.

### 10. Running the measurement

On the Mac, with unchanged code (fingerprint `9b6f609942e8`), `qwen2.5:7b-instruct`, `bge-m3` and the default configuration:

```bash
./scripts/rag_eval_phases.sh blind-v3-1 blind-v3-2 blind-v3-3
./scripts/rag_eval_report.sh app/rag/evaluation/generalization_v3.json blind-v3-1 blind-v3-2 blind-v3-3 > rag_eval_report_blind_v3.md
./scripts/rag_eval_blind_v3_facts.sh blind-v3-1 blind-v3-2 blind-v3-3 > rag_eval_report_blind_v3_facts.md
```

- These three runs are the first measurement of generalization for TAH-134, TAH-135 and TAH-141.
- Report v3 on its own and never merge it with the other datasets.
- From the first run on, every failure analysed and fixed turns v3 into a regression set, like v1 and v2.

### 11. First measurement (Mac, 2026-11-18)

**Setup:** code fingerprint `9b6f609942e8`, `qwen2.5:7b-instruct`, `bge-m3`, default configuration, dataset sha256 `e5454c62…`. The raw runs are kept unchanged on the Mac in `baseline_blind_v3/`, with `SHA256SUMS`.

**Runner checks**

| Metric | blind-v3-1 | blind-v3-2 | blind-v3-3 |
|---|---|---|---|
| Cases passed | 105/150 | 106/150 | 105/150 |
| Payment identification | 41/52 | 41/52 | 41/52 |
| Financial facts | 19/33 | 19/33 | 19/33 |
| Correct abstention | 27/29 | 25/29 | 27/29 |
| No false refusal | 101/116 | 101/116 | 101/116 |
| Isolation | 150/150 | 150/150 | 150/150 |
| Language | 138/150 | 138/150 | 138/150 |
| LLM calls | 65 | 65 | 65 |
| p95 latency | 13.4 s | 14.5 s | 14.5 s |

| Area | Passed (3 runs) | Area | Passed (3 runs) |
|---|---|---|---|
| payment by id | 14/14/14 of 15 | documentation | 16/18/16 of 22 |
| payment explanation | 9/10/9 of 10 | personal data | 7/6/7 of 7 |
| find | 20 of 31 | not recorded | 7 of 7 |
| count | 7 of 15 | other merchant / manipulation | 7/6/7 of 9 |
| total | 5 of 15 | out of scope | 3 of 5 |
| ranking | 2 of 6 | ambiguous | 8 of 8 |

**Fact-level check** (`fact_check.py`, unchanged after the runs): 97 / 94 / 95 pass, 47 / 50 / 49 fail, 2 clarifications and 4 manual cases per run, 0 leaks. Three biases of the checker are recorded here instead of being fixed after the results:

- **Single-payment cases are over-strict.** The checker requires every typed fact of the key facts (amount, status) even when the question asks for one thing. A manual review of run 1 finds 2 wrong answers out of 25, not the 16 fails reported.
- **Refusals counted as clarifications.** The `unrecognized_payment_set` refusal contains a "?" inside its example, so q035 and q040 appear as clarifications. They are refusals.
- **Correct refusals missed by the runner.** In q123 and q135 the runner's refusal markers miss correct refusals worded in other ways.

**Every failing answer was read. They fall into five classes.**

| Class | Cases (all stable in 3 runs unless noted) | What happens |
|---|---|---|
| **A. Wrong figure written by the backend** | q062 (20 instead of 8), q065 (10 instead of 9), q078 (444,70 € instead of 49,00 €), q085 (196,00 € instead of 248,70 €), r008 (444,70 € instead of 237,20 €) | The parser ignores a criterion it does not know: "Pro" without a preposition, "confirmed through the webhook / via riconciliazione", "escluso", "più di un pezzo". Plan coverage does not report a word it does not know, so the SQL aggregate answers a broader or opposite question as if complete. |
| A. False absence by the backend | q044 ("scaduti di importo superiore a 20 euro") | The adjective "superiore" (not "superiori") is not a comparator, so the plan becomes "amount exactly 20 €" and the answer says nothing matches. It does state that wrong criterion. |
| A. List for another question | q147 ("the big payments": all 20, "no filter"); r016 ("never got paid" read as `paid`, shown as a partial list) | "Big" is ignored; in r016 the negation is reported, but the partial list shows the opposite set. |
| **B. Wrong facts written by Qwen** | q013 (an expired payment called `open`), r018 (31 s and 3 min 4 s instead of 3 min 1 s, in 2 runs), q055 and r015 (deny payments that exist), q090 (wrong ranking), q099 (documentation: "yes, a network error can make a payment fail"; the documentation says the opposite), q149 (picks one of six 29,90 € payments silently), q140 (writes a marketing email instead of refusing) | Semantic route, or `sql_find` with Qwen. The amount validator flagged q055 (`amount_not_in_sources`) without changing the answer. |
| **C. Safe non-answers** (21) | q058, q059, q036, q042, q035, q040, r017 (`unrecognized_payment_set`); q080 (English "quantity" read as a misspelling of "quantità"); r002, r012 ("tra i 20 e i 50 euro" with articles); r009, r010, q038 (partial: "prime due settimane", "primi 10 giorni", "between October 5 and October 10"); q076, q087, q091, r003, r004, q145 (totals and rankings in forms not recognised, answered by Qwen with "not enough information"); q034, q108 (retrieval misses) | No wrong figure, but the merchant gets no answer. |
| **D. Language** | q057, q062, q067, q070, q072, q074, q077, q084, q131 (aggregate templates in Italian, TAH-136); q129, q133, q136 (the other-merchant notice is appended in Italian to English answers) | |
| E. Citations | q092, q095, q105, q107, q111, q113 (variable) | Documentation answers that do not cite the expected section in some runs |

**What this says about generalization**

- **Isolation held everywhere:** 150/150 in 3 runs. No answer contained another merchant's data.
- **What worked:** refusals for not-recorded metrics and personal data, payment-by-id lookups and ambiguous questions.
- **Class A is the important finding.** v1 and v2 did not show it after TAH-134/135/141: on the regression sets no backend figure was wrong. On unseen questions, 5 aggregates out of 30 count and total questions give a wrong number presented as complete. ADR 0011 assumed plan coverage would catch an incomplete plan; it only catches words it knows. These cases go to TAH-142.
- **Counts and totals** pass in 12 of 30. The rest are mostly safe refusals (class C) or Italian answers to English questions (class D).
- **Not a regression of the fixes:** the date, negation and payment-set fixes (TAH-134, 135, 141) generalise only partly. Several forms they target still fail on new wording: English day ranges, "prime due settimane", "never got paid", amounts without a currency.

## TAH-142: semantic constraint coverage (2026-11-18, ADR 0013)

**Root cause of class A.** Plan coverage (ADR 0011) only looked for known kinds of trouble, so an unknown word left the plan "complete". For the 7 class-A cases of v3 the plan had `unresolved = ()` and the SQL was correct for that plan:

| Case | Plan before | SQL before (WHERE, merchant omitted) |
|---|---|---|
| q062 | count, October | `created_at` in October: "Pro" not a product without a preposition |
| q065 | count, `paid` | `status = paid`: "confirmed through the webhook" dropped |
| q078 | total, no filter | none: "confermati via riconciliazione" dropped |
| q085 | total, product Report | `product_id = payflow-report`: "escluso" ignored, the excluded product applied |
| r008 | total, no filter | none: "più di un pezzo" dropped |
| q044 | find, expired, amount = 20 € | `amount = 2000`: "superiore" (singular) not a comparator |
| q147 | find, no filter | none: "big" dropped |

**Fix:** every word is accounted for (see [answer lifecycle](../architecture/answer-lifecycle.md)). Excluded and negated constraints are removed from the filters. The singular comparators ("superiore", "inferiore", "maggiore", "minore") are recognised. The TAH-141 typo corrector no longer rewrites known filter words: it turned "maggiore" into "maggio".

**Deterministic results** (cloud, no models; v1, v2 and v3 are regression sets). A "false financial answer" is a backend-written count, total, list or "none found", presented as complete, that fails its expectation (for v3, `fact_check.py`):

| Dataset | Correct complete answers before → after | False financial answers before → after |
|---|---|---|
| historical | 28 → 27 | 1 → 1 (`lookup-late-paid`: the wording of the expectation; the answer is correct) |
| v1 | 74 → 73 | 3 → 2 (`g-amb-oct28-paid`, `g-amb-pro-ok`: yes/no about "the" payment of a day or product, unchanged) |
| v2 | 67 → 66 | 0 → 0 |
| v3 | 46 → 36 | **7 → 0** |

**Answers no longer written as complete** (16):

| Kind | Cases | Assessment |
|---|---|---|
| Real criterion not represented; the old answer was right by coincidence | `lookup-async` ("dopo un pagamento asincrono"), q052 ("checkout_session_expired"), r014 ("pagina di checkout creata") | **False abstentions: 3.** Each still lists the matching payments, labelled partial. |
| Ambiguous by the golden answer | q063, q143, q144, q146, r007 | Non-answer accepted by the golden |
| Manipulation or other-merchant requests | g-adv-debug-all, v2-adv-en-admin, q129, q131, q132, q136 | The contract accepts a refusal or the merchant's own data. Lists stay partial with own data; counts give no figure. |

The 7 class-A cases are now non-answers that name what was not interpreted, except q044, which is answered correctly (2 expired payments above 20 €). Every other case of the four datasets keeps its route and answer.

**Tests:** `tests/test_constraint_coverage.py`, 65 tests with general wording, not the v3 wording. 846 backend tests pass; ruff and mypy are clean; code fingerprint `25fb355902b3`. They cover:

- conversational words that must not cause a refusal;
- unknown constraints, exclusions and negations;
- singular comparators;
- ambiguous and unsupported qualifiers;
- that explanations keep the model answer;
- no figure on incomplete aggregates;
- no absence claim on incomplete lists;
- no SQL on ambiguous or unsupported plans (repository methods patched to fail);
- complete questions still answered, and a complete query with no match still saying "none".

**Mutation testing:** all 21 mutants of the coverage, the policy and the scorer are caught by the tests:

- 17 mutants of the coverage, for example: coverage off, unknown words ignored, exclusions kept as filters, a window starting inside a word, explanations covered, descriptive words counted without their constraint, the singular comparator lost, the typo corrector rewriting filter words, contracted negations missed;
- 3 of the execution policy;
- 1 of the scorer (a clarification not counted as a non-answer).

The first run left 2 alive (total words counted outside a total, contracted negation); both are now covered by tests. The second exposed a real gap: without it, "How much haven't I collected yet?" would compute the amount collected.

Two earlier tests were updated with the reason stated: `lookup-async` is now partial on purpose, and the historical check now uses the real questions instead of unresolved placeholders.

### Real runs after TAH-142 (Mac, 2026-11-18, code `25fb355902b3`)

Three runs per dataset, with the configuration unchanged, were compared case by case with the three baseline runs (`9b6f609942e8`). The full analysis and the evaluation errors are in [`evaluation/tah142/`](../../evaluation/tah142/README.md); the per-case table is generated by `compare_runs.py`.

| Dataset | Cases | Passed (TAH-142) | Passed (baseline) | False backend answers presented as complete | Isolation |
|---|---|---|---|---|---|
| historical | 50 | 49 / 48 / 47 | 49 / 49 / 48 | 1 → 1 (evaluation wording) | 4/4 |
| v1 | 117 | 98 / 101 / 100 | 99 / 101 / 101 | 2 → 2 (yes/no on "the" payment, true but incomplete) | 6/6 |
| v2 | 102 | 93 / 95 / 92 | 93 / 94 / 93 | 0 → 0 | 5/5 |
| v3 | 150 | 110 / 108 / 111 | 105 / 106 / 105 | **10 → 0** in every run | 150/150 |

**Every difference was attributed.**

- **Deterministic paths match the harness exactly.** Routes match in all 2,514 case-runs, and every SQL-route backend answer is byte-identical to the deterministic harness.
- **Qwen-answered cases had the same input.** For all 128 questions answered by Qwen, the plan and the selected sources are identical before and after.
- **Pass-count differences on Qwen-answered cases are generation variability:**
  - citations: docs-it-keys, g-id-report-open, v2-st-uncertain, q101;
  - completeness: docs-it-pii, which also failed in 8 of the 12 earlier historical runs.
- **The historical run at 47/50 has no deterministic cause.**

**v3 had 10 false complete answers per baseline run, not 7.** `fact_check.py` checks only that the expected ids appear in an answer. Three complete lists that answered a different question were therefore missed:

- q144 listed all 6 Starter payments for "didn't go through";
- r007 listed all 20 payments for "the biggest order";
- r014 listed all 3 failed payments when one is correct.

After TAH-142 there are none in any run. Useful correct answers to the 108 answerable v3 questions stay at 67/67/67 (baseline 67/66/65): the protection did not turn answers into refusals.

**Abstentions.** Every new non-answer replaces a false complete answer, or is a clarification or manipulation case that the contract accepts. Two are unjustified:

- q052 ("checkout_session_expired");
- lookup-async ("dopo un pagamento asincrono").

Both were right by coincidence. The criterion is the failure or expiry reason, which the data records but the parser cannot filter on. Both still list exactly the right payment, labelled partial. r014, counted as a false abstention in the deterministic analysis, was a false complete answer before (see above).

**Evaluation errors found, not corrected:**

- **id check:** `fact_check.py` checks inclusion, not equality.
- **q140:** the runner's refusal heuristic passes it, although Qwen still writes a marketing email in 3/3 runs.
- **single-payment lookups:** the fact check requires facts that were not asked in 11 of them.

## Rules from here

- Parser fixes for TAH-134 and TAH-135 turn the cases they fix into **regression** cases.
- v1, v2 and, since its first measurement, v3 are regression sets. The next generalization claim needs another blind set, built the same way (ideally by a person).
- Never merge this dataset's score with the historical one.
- **2026-11-19:** q013, r018, q055, r015, q090, q149, q140, q142 (v3) and `v2-oos-greeting`, `v2-typo-rejected` (v2) were used to design ADR 0014 (TAH-143). They are regression cases now. The scorer rules of TAH-145 were also written after reading v2 and v3 answers: compare series only through `evaluation/scorer_integrity/rescore.py`, and measure generalization of both on a new blind set.
