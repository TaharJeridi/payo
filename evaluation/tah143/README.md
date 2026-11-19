# TAH-143: wrong financial facts written by Qwen

Analysis of the Qwen errors on the blind dataset v3, the decision ([ADR 0014](../../docs/decisions/0014-verified-financial-claims.md)) and its verification. Inputs: the saved runs `blind-v3-1..3` (code `9b6f609942e8`) and `blind-v3-s1..3` (code `25fb355902b3`), judged with the TAH-145 scorer ([`evaluation/scorer_integrity/`](../scorer_integrity/README.md)). No dataset or golden answer was changed. The cases below informed the fix: from now on they are **regression** cases, not evidence of generalization.

## 1. Case reconstruction

Every case was re-read in the 6 saved runs. Plan and sources are identical across the 6 runs (TAH-142 did not change Qwen's input), so each error is reproducible on the same evidence.

| Case | Question | Intent / route | Evidence given to Qwen | Qwen's answer (stable part) | Expected | Error | Cause |
|---|---|---|---|---|---|---|---|
| q013 | "Per il pagamento 6ceb8bff… la pagina di checkout su Stripe è stata creata o no?" | `find`, `sql_find` (id lookup) | [1] the payment, `Stato: expired`, reason `checkout_session_expired`; [2][3] architecture chunks defining the states | "Sì, la pagina … esiste [1]. Il suo stato è `open`, il che significa che il Checkout Session exists and can be paid" (6/6) | yes, created; status expired | **Contradiction with the data** (status) | Interpretation: the documentation chunk on `open` prevails over the payment's own `Stato` line |
| r018 | "il 4343f610… a che ora è stato pagato? quanto ci ha messo il cliente dalla creazione?" | `find`, `sql_find` | [1] the payment: created 16:30:00, paid 16:33:01, history | paid 16:33:01 ✓; duration "31 secondi" / "3 minuti e 4 secondi" / "3 minuti e 5 secondi" (varies) | 3 min 1 s | **Calculation** | The model subtracts timestamps; the backend does not give the duration |
| q055 | "is there a Pro payment for exactly 59.80?" | `semantic` (amount without currency not parsed) | 5 payment chunks by similarity; the 59,80 € payment is not among them | "there is no Pro payment recorded for an exact amount of 59.80 EUR" (6/6) | yes, 4838cd98 | **False absence** | Retrieval (parser misses the bare amount, TAH-144) + the model treats 5 similar chunks as the whole data |
| r015 | "quali pagamenti hanno avuto problemi a parlare con Stripe nel momento in cui sono stati creati?…" | `semantic` (criterion not representable) | 5 payment chunks; 1 of the 3 expected among them | "Non si registrano pagamenti che hanno avuto problemi…" (6/6) | 3 payments | **False absence** | Retrieval + absence claimed from a non-exhaustive search |
| q090 | "Mi fai la classifica dei prodotti per unità vendute?" | `semantic` ("classifica" not a ranking word) | 3 doc chunks, 2 payments | "Financial Report Pack con 1 unità" / "PayFlow Pro 2 unità" (6/6, varies) | Starter 7, Pro 6, Report 4 | **Ranking computed by the model** on 2 payments | Routing: the backend's `top_product` ranking is never reached |
| q149 | "What happened with the 29.90 payment?" | `semantic` (bare amount) | 5 payment chunks, 2 of them at 29,90 € (6 exist) | "The payment for 29.90 EUR … resulted in an expired session [2]" (6/6) | 6 payments: ask or list | **Unjustified selection** | Ambiguity not stated; retrieval does not see all 6 |
| q140 | "Write me a marketing email to push the Pro plan to my customers" | `semantic` | 3 payment chunks | A marketing email; in `blind-v3-s1` with the invented code `PROUPGRADE20` (3/3 TAH-142 runs; the baseline had other content, also failing) | refusal: out of scope | **Out of scope; invented business fact** | No scope boundary before generation; the refusal heuristic hid it (TAH-145) |
| q142 | "Should I raise the Starter price to 12.90? would I sell less?" | `semantic` | 5 payment chunks | Pricing advice or a decline with a list of data to collect (varies) | refusal: out of scope | **Out of scope** | Same; also `detect_language` reads this English question as Italian |
| q099 | "Un errore di rete può far fallire un pagamento?" | `semantic` | failed payments + architecture chunks | "Sì, un errore di rete può causare il fallimento…" (6/6) | no: `creation_unknown`, never `failed` | Contradiction with the **documentation** | Interpretation of prose; not a financial value |

Error classes, as asked by TAH-143: **contradiction with data** q013; **calculation** r018, q090; **retrieval / routing** q055, r015, q090, q149; **ambiguity** q149; **out of scope** q140, q142; **contradiction with documentation** q099. Not the same cause, so not the same fix.

## 2. What else the re-scored runs show

With the TAH-145 scorer, the other v3 answers judged wrong are not Qwen factual errors:

- q001–q004, q006, q007, q009–q012, q018, q020, q025: single-payment lookups that answer the question but omit an amount or status that was not asked (a golden-fact issue recorded in TAH-142; unchanged).
- q034, q076, q087, q091, q108, r004: non-answers to legitimate questions (TAH-144).
- q125, q134, q135: refusals followed by other content, reviewed as acceptable.

On v2, the re-score found one more Qwen false absence of the same kind as q055: `v2-typo-rejected` ("pagamenti rifiutatti", semantic route) names 1 of the 3 rejected payments and adds "Non ci sono altri pagamenti rifiutati" (3 of 6 runs).

## 3. Decision

See [ADR 0014](../../docs/decisions/0014-verified-financial-claims.md). In short: the backend now checks the financial claims in Qwen's text against the evidence it gave Qwen, and an answer that contradicts verified data, or asserts what the evidence cannot support, is not shown as reliable. Durations are computed by the backend. Out-of-scope requests are refused before generation. Rankings asked as "classifica" reach the SQL ranking.

The verification sections (deterministic tests, retrospective checks on saved answers, real runs) are below.

## 4. Implementation

| Change | Where | Cases |
|---|---|---|
| Claim checks: status, duration, absence on the semantic route, selection, offers; each claim attributed to one payment of the evidence | `backend/app/rag/claims.py` (new) | q013, r018, q055, r015, q149, q140; v2 `v2-typo-rejected` |
| An answer with a contradicted or unverifiable claim is replaced by backend text (backend listing + verified duration, the non-exhaustive-search message, the payments that share the amount, or the scope message); warning codes in `warnings` | `backend/app/rag/service.py` (`_claims_fallback`) | same |
| "tempo dalla creazione al pagamento: 3 min 1 s (181 s)" in the verified facts of every paid payment | `backend/app/rag/documents.py` | r018 |
| Out-of-scope requests (promotional content, pricing or sales advice) answered by the backend before retrieval, route `out_of_scope` | `backend/app/rag/scope.py` (new), `service.py`, `structured.py` (route), runner (`DETERMINISTIC_REFUSALS`) | q140, q142, v2 `v2-oos-greeting` |
| "classifica / ranking … prodotti / products" and "per unità vendute / by units sold" reach the SQL ranking; a ranking question gets every product in order | `query_parser.py` (`_TOP_PRODUCT`, `QueryPlan.ranking`), `structured.py` | q090 |
| Backend abstentions written after a claim check count as refusals in the evaluation | runner (`CLAIM_ABSTENTIONS`), `fact_check_v2.py` | — |

Unchanged: prompt, models, retrieval settings, datasets, golden answers, tenant scoping. The fallback only uses sources already selected for the authenticated merchant.

Not addressed (documented in ADR 0014): q099 (contradiction with documentation prose), the root causes that belong to TAH-144 (amounts without a currency for q055/q149, the creation-problem criterion of r015). The claim checks make those answers safe, not useful: the user now gets "the search is not exhaustive" instead of a false absence.

## 5. Verification without models

**Tests.** `backend/tests/test_verified_claims.py`, 53 tests: each claim type positive and negative, real answers from the saved runs, attribution to the right payment, history and definitions not treated as current status, configuration durations ignored, precision of durations ("3 minuti" accepts 2:30–3:59, "3 minuti e 4 secondi" only that), offers in sources, refusals checked for offers only, the service fallbacks end to end, the scope rule (5 out-of-scope and 6 in-scope questions, including "scrivimi l'email del cliente"), the SQL ranking.

**Mutation testing** (targeted, `evaluation/mutation/`): 27/27 mutants killed on the TAH-143 code (claims 17, service 3, scope 3, structured 2, parser 1, documents 1). The first round left 8 survivors; each needed a missing test (history guard, minute precision, timing context, selection coverage and plural, offers present in sources, refusal handling, the offer fallback).

**Full suite:** 940 backend tests pass, 3 skipped (live Ollama/Qdrant, Stripe test mode, saved-runs path); 35 frontend tests pass; ruff, ruff format, mypy and `tsc` clean.

**Deterministic regression, four datasets** (`tests.eval_deterministic`, 419 questions, routes and backend answers before and after): exactly 4 questions change, all intended — `v2-oos-greeting`, q140, q142 → `out_of_scope`; q090 → `sql_aggregate` with the full ranking (Starter 7, Pro 6, Report 4). Every other route and backend answer is byte-identical. The scope rule matches no answerable question of any dataset.

**Retrospective check on saved Qwen answers** ([`retrospective.md`](retrospective.md), generated by `claims_retrospective.py`; evidence regenerated from the seed with the same code that builds it for Qwen):

| | 24 runs of the last two series (768 model answers) | All 59 saved runs (1,769 model answers) |
|---|---|---|
| Answers that would be replaced | 31 | 34 |
| — contradicted status | 6 (q013 6/6) | 6 |
| — contradicted duration | 4 (r018: the 4 wrong durations; the 2 runs saying "3 minuti" are verified) | 4 |
| — unverified absence | 14 (q055 6/6, r015 5/6, `v2-typo-rejected` 3/3 of the runs that deny) | 17 |
| — unjustified selection | 6 (q149 6/6) | 6 |
| — unsupported offer | 1 (q140, `PROUPGRADE20`) | 1 |
| **Correct answers that would be replaced** | **0** | **3** (`g-neg-report-failed-late`, older code: the absence was true by coincidence; the question now goes to SQL) |
| Status claims verified / duration claims verified | 54 / 4 | 169 / 8 |

Every replaced answer was read. Missed: r015 in `blind-v3-1` ("Non si verificò alcun problema…" denies a problem, not the payments) and q099 (documentation).

## 6. Real evaluation (Mac): pending

Qwen and bge-m3 run only on the Mac; this environment has neither, so **no real result is claimed here** and TAH-143 stays open until the runs below are done and read.

From the repository root on the Mac:

```bash
# 0. What is being measured
(cd backend && python3 -c "from app.rag.evaluation.__main__ import code_fingerprint; print(code_fingerprint())")
#    expected c0e82af410ef (code of 2026-11-19); a different value means the code changed since this README
shasum -a 256 backend/app/rag/evaluation/cases.json backend/app/rag/evaluation/generalization*.json
#    593241fb0d03…, 05c26453f7b4…, 3cc7194e4b0f…, e5454c628bdd… (the v3 hash is also checked by the script)
(cd baseline_tah142 && shasum -a 256 -c SHA256SUMS) && (cd baseline_blind_v3 && shasum -a 256 -c SHA256SUMS)

# 1. Baselines re-scored with the TAH-145 rules (already written on 2026-11-19; regenerate if missing)
docker compose run --rm -T --no-deps -v "$PWD:/repo" -w /repo/backend -e PYTHONPATH=/repo/backend \
  rag-indexer python ../evaluation/scorer_integrity/rescore.py --write-runs > /dev/null

# 2. Three real runs per dataset (rebuilds images and re-indexes: the duration line changes paid payments' text)
./scripts/rag_eval_phases.sh consistency-13 consistency-14 consistency-15 gen-t1 gen-t2 gen-t3 \
  gen2-t1 gen2-t2 gen2-t3 blind-v3-t1 blind-v3-t2 blind-v3-t3

# 3. Reports against the re-scored TAH-142 series (same scorer on both sides)
./scripts/rag_eval_report.sh historical consistency-13 consistency-14 consistency-15 \
  -- consistency-10-rescored consistency-11-rescored consistency-12-rescored > rag_eval_report_tah143_historical.md
./scripts/rag_eval_report.sh generalization gen-t1 gen-t2 gen-t3 \
  -- gen-s1-rescored gen-s2-rescored gen-s3-rescored > rag_eval_report_tah143_v1.md
./scripts/rag_eval_report.sh generalization-v2 gen2-t1 gen2-t2 gen2-t3 \
  -- gen2-s1-rescored gen2-s2-rescored gen2-s3-rescored > rag_eval_report_tah143_v2.md
./scripts/rag_eval_report.sh app/rag/evaluation/generalization_v3.json blind-v3-t1 blind-v3-t2 blind-v3-t3 \
  -- blind-v3-s1-rescored blind-v3-s2-rescored blind-v3-s3-rescored > rag_eval_report_tah143_v3.md
docker compose run --rm -T --no-deps -v "$PWD:/repo" -w /repo/backend -e PYTHONPATH=/repo/backend rag-indexer \
  python ../evaluation/scorer_integrity/fact_check_v2.py app/rag/evaluation/generalization_v3.json \
  /repo/rag_eval_phase_blind-v3-t1.json /repo/rag_eval_phase_blind-v3-t2.json /repo/rag_eval_phase_blind-v3-t3.json \
  > rag_eval_report_tah143_v3_facts.md

# 4. Claim warnings per run, to read one by one
for f in rag_eval_phase_{consistency-1{3,4,5},gen-t{1,2,3},gen2-t{1,2,3},blind-v3-t{1,2,3}}.json; do
  python3 -c "import json,sys,collections; r=json.load(open(sys.argv[1]))['results']; c=collections.Counter(w for x in r for w in x['warnings']); print(sys.argv[1], dict(c))" "$f"
done

# 5. Archive
mkdir -p baseline_tah143 && cp rag_eval_phase_{consistency-1{3,4,5},gen-t{1,2,3},gen2-t{1,2,3},blind-v3-t{1,2,3}}.{json,log} \
  rag_eval_report_tah143_*.md baseline_tah143/ && (cd baseline_tah143 && shasum -a 256 * > SHA256SUMS)
```

**Criteria to close TAH-143** (each checked by reading the answers, not only the counts):

1. q013, r018, q055, r015, q149, q140, q142, q090: no wrong financial fact in any of the 3 v3 runs (a backend replacement or abstention counts as correct; a wrong figure does not).
2. Every answer with `contradicted_status`, `contradicted_duration`, `unverified_absence`, `unjustified_selection` or `unsupported_offer` read: zero correct answers replaced. Any false positive is a blocker.
3. No case that passed in all 3 re-scored TAH-142 runs fails in the new runs for a reason other than documented Qwen variability.
4. Isolation 100% in every run; financial facts and abstention not lower than the re-scored baseline.
5. LLM calls and p95 latency reported per dataset (expected: 3 fewer LLM calls on v3 and 1 on v2, because q090, q140, q142 and `v2-oos-greeting` no longer reach Qwen; claim fallbacks add no call).
