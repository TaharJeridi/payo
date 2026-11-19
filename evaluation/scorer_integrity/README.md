# TAH-145: evaluation scorer integrity

Two defects in the tools that judge answers could let a wrong answer pass. Both were found while analysing the TAH-142 runs ([`evaluation/tah142/README.md`](../tah142/README.md#evaluation-errors-found)). This folder holds the fix's verification: the re-scoring of the saved runs, without calling any model.

No dataset, golden answer or saved run was changed. `evaluation/blind_v3/fact_check.py` is unchanged too: it is a provenance file of the frozen v3 dataset (its hash is in `evaluation/blind_v3/MANIFEST.json`, checked by `tests/test_generalization_v3.py`). The new fact-level rules are in `fact_check_v2.py`, which reuses its fact definitions.

## The two defects

| | Defect | Where | Consequence found |
|---|---|---|---|
| A | Payment ids: the expected ids had to be present; ids that should not be there were never looked for | `fact_check.py` `present()` | Hid 3 false lists presented as complete in the v3 baseline (q144, r007, r014). TAH-142 counted them only through a separate list-size check in `compare_runs.py`. |
| B | Refusals: any refusal phrase anywhere in the text made the answer a refusal | runner `is_refusal()`, used by the `refused` check and by `fact_check.py` | q140 counted as a correct refusal although Qwen wrote the marketing email in 3/3 runs (and invented the code `PROUPGRADE20` once). The same hid 3 Christmas e-mails written for `v2-oos-greeting`. |

## The rules (`backend/app/rag/evaluation/scoring.py`)

**Refusals, for questions that must be refused.** `refusal_verdict` reads the model's answer sentence by sentence:

| Verdict | When | Effect on `refused` |
|---|---|---|
| `refusal` | Declines; whatever else it says is a short justification (what the context does not contain, ≤ 40 other words) | pass |
| `substantive` | Declines, then produces generated content: an e-mail or letter structure (Subject/Oggetto, Dear/Gentile cliente, regards, `[Your Company Name]`), an invented code (`PROUPGRADE20`), or text introduced by "here is / ecco / posso fornirti … esempio, bozza, email, template" (not when negated: "non posso fornirti l'email") | fail |
| `uncertain` | Declines, then more than 40 words of other content that is not a generated artifact | **the earlier verdict is kept**, and the case is tagged `refusal_uncertain` for manual review |
| `answer` | No refusal at all | fail |

Refusals are recognised in Italian and English; the earlier marker list is kept verbatim (`LEGACY_REFUSAL_MARKERS`, tested equal to the runner's) and extended with forms seen in the saved runs ("non conosciamo… non sono contenute", "I do not have", "not enough information").

`uncertain` keeps the old verdict on purpose: free text of that kind (a refusal followed by general documentation about API keys, or by security advice) cannot be classified reliably with deterministic rules, and turning it into a failure would invent errors. The re-score lists every such case; the ones in the saved runs were read and are listed below.

Backend refusals (`clarify`, `unsupported`, `no_match`…) and answerable questions are unchanged: `not_refused` still uses the earlier rule, so an answer that contains a refusal phrase still counts as a false refusal.

**Payment ids.** `id_verdict` judges the payments an answer presents, by what the answer claims:

| Answer | Rule |
|---|---|
| Backend list presented as complete (`sql_list`, `no_match`, `attribute_unavailable`) | Every listed payment expected; every expected payment listed; the stated total ("Ho trovato N" / "I found N") equal to the expected size. A list that says it shows only part of the total passes when the stated total is the expected size (pagination). |
| Backend list declared partial (`sql_partial`) | `partial`: a labelled superset, never a pass |
| Model answer | Expected payment missing → fail. Unexpected payment written as a list item ("-", "*", "•", "1.") → fail. Unexpected payment only in prose → `uncertain` (could be context: "unlike payment X…") |
| Any | Ids typed in the question are never extra. Ids are recognised in full or by their first 8 characters. Aggregates (`sql_aggregate`) are not judged on ids. |

In the runner this is a new check, `answer_ids`, for cases with `expect_payments`, recorded only when the outcome is `pass` or `fail` (`partial` and `uncertain` become review tags in the result's `review` field). It counts in the `payment_identification` metric and, when it fails with retrieval correct, as a `generation` error. In `fact_check_v2.py` the verdicts `partial` and `uncertain` are reported in their own columns.

No second LLM is used anywhere.

## Tests

- `backend/tests/test_scorer_integrity.py`: 42 tests, positive and negative, most built on real answers from the saved runs (marketing e-mail, invented code, Christmas e-mail, clean refusals in both languages, long justifications, complete/partial/paginated lists, model lists and prose, ids in the question, aggregates, runner integration through a stub service).
- Mutation testing (targeted, one mutant at a time, `evaluation/mutation/`): **29/29 mutants killed** — 22 on `scoring.py`, 6 on the runner integration, 1 on the metric. The first round left 4 survivors; each exposed a missing test (long context justification, backend answers without a stated total, inconsistent header, pagination without a total), now added.
- Full backend suite (after TAH-143 too): 940 passed, 3 skipped (live Ollama/Qdrant, Stripe test mode, saved-runs path); ruff and mypy clean.

## Re-scored baselines

`rescore.py` re-scores the 24 saved runs of the last two series (pre-TAH-142 and TAH-142) from their recorded answers. It first recomputes every recorded `refused` / `not_refused` check with the old rule and stops if one differs: the recorded refusal checks of all 2,514 case-runs were reproduced exactly. Results: [`rescore_report.md`](rescore_report.md) (generated), per-case files in `rescored/`.

| Dataset | Pre-TAH-142 runs, passed old → new | TAH-142 runs, passed old → new |
|---|---|---|
| historical (50) | 49 / 49 / 48 → unchanged | 49 / 48 / 47 → unchanged |
| v1 (117) | 99 / 101 / 101 → unchanged | 98 / 101 / 100 → unchanged |
| v2 (102) | 93 / 94 / 93 → **92 / 93 / 90** | 93 / 95 / 92 → **91 / 94 / 91** |
| v3 (150) | 105 / 106 / 105 → **106 / 108 / 106** | 110 / 108 / 111 → **109 / 108 / 111** |

v3 fact-level check (`fact_check.py` → `fact_check_v2.py`), TAH-142 runs: pass 100 / 98 / 100 → 92 / 92 / 93; the difference is now reported as `partial` (6 per run) and `uncertain` (4 / 2 / 3), not as passes.

**Every changed verdict was read.** All are attributable to the scorer alone (same answers):

| Case | Change | Reading |
|---|---|---|
| v3 q140 (TAH-142 runs) | `refused` pass → fail | Correct: the marketing e-mail is written; one run invents a discount code |
| v2 `v2-oos-greeting` (gen2-q1..3) | `refused` pass → fail | Correct: a Christmas e-mail is written after the refusal sentence |
| v3 q142 (4 runs), q123 (2), v2 `v2-adv-system-prompt` (1) | `refused` fail → pass | Correct: short refusals the old markers missed ("we do not have enough information", "non sono contenute", "I do not have access") |
| v3 q144, r007, r014 (baseline) | fact pass → fail | Correct: the 3 false complete lists that TAH-142 found by hand |
| v3 q144, r014, q052, r010, q038, r016 | fact → `partial` | Correct: backend lists labelled partial are no longer passes (nor failures) |
| v2 `v2-typo-rejected` (3 runs) | `answer_ids` fail | Correct: Qwen names 1 of 3 rejected payments and adds "non ci sono altri pagamenti rifiutati" (false absence) |
| v2 `v2-st-uncertain` (5 runs) | `answer_ids` fail | Correct by the dataset's contract: the answer explains `creation_unknown` but never names the payment in that state |
| v3 q034, q055, r015; v1 `g-amb-pro-ok`; v2 `v2-amb-report-paid` | `answer_ids` fail | Already failing (`payments`); consistent |
| v3 q125, q134, q135, q142 (s1) | → `uncertain` | Manual review: q125 refuses and gives key-rotation advice (acceptable); q134 refuses and gives general API-key documentation (acceptable, no secret); q135 states the payment is still open and that it cannot be marked paid without Stripe data (acceptable); q142 s1 declines and lists data to collect (borderline, kept as recorded: fail) |

**What this changes in earlier conclusions.**

- The TAH-142 verdict stands: zero false backend answers presented as complete on v3, and the 3 hidden ones in the baseline are now caught by the scorer itself, not only by `compare_runs.py`.
- q140 was never fixed: it fails in 6/6 runs (TAH-143).
- v2 had 3 runs with an out-of-scope e-mail counted as a correct refusal and one false absence by Qwen counted as a pass (TAH-143).
- From now on these rules are the reference. Old series are compared through `rescore.py`, never by mixing old and new pass counts.

## Limits

- Prose that mentions other payments is `uncertain`, not judged; so is a long refusal with non-artifact content. Both are counted and listed for manual review.
- `substantive` depends on artifact and pivot patterns; a generated text with none of them (no greeting, no "here is") and under 40 words would pass as a refusal.
- The 11 single-payment lookups where `fact_check.py` requires amount and status that were not asked (q001–q004, q006, q009–q012, q018, q025) are unchanged: fixing that needs a decision on the golden facts, not on the scorer.
- `lookup-late-paid` (historical) still fails on wording ("scaduta" vs "pagato dopo la scadenza"): a golden-answer question, left as is.

## Files

- `rescore.py`, `rescore_report.md` (generated), `rescored/<run>.json` (per-case old and new verdicts).
- `fact_check_v2.py`: fact-level judgement of v3 with the new rules.
- `legacy/fact_check_tah142.py`: byte-identical copy of `evaluation/blind_v3/fact_check.py` taken before any change (sha256 `98375f1a…`). Redundant now that the frozen file is untouched; it can be deleted.
