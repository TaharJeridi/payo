# ADR 0014: Verified financial claims in LLM-generated answers

**Status:** Accepted (2026-11-19). Implemented and verified without models (unit, mutation and retrospective tests on saved answers). **Real runs pending** on the Mac (TAH-143 stays open until then).

## Context

ADR 0011–0013 moved existence, membership, counts, totals and rankings to the backend whenever the parser understands the question. Qwen still writes the answer for explanations, single-payment lookups and every question the parser routes to semantic retrieval. On the blind set v3, those answers contain wrong financial facts that the system presents like any other answer (analysis: [`evaluation/tah143/`](../../evaluation/tah143/README.md)):

| Case | What Qwen wrote | Truth | Kind |
|---|---|---|---|
| q013 | status `open` | `expired`, in its own evidence | contradiction with data |
| r018 | "31 secondi", "3 minuti e 4 secondi" | 3 min 1 s | calculation |
| q055, r015 (and v2 `v2-typo-rejected`) | "there is no …" | the payments exist | absence asserted from a non-exhaustive search |
| q090 | ranking from 2 payments | Starter 7, Pro 6, Report 4 | ranking computed by the model |
| q149 | picks one of the 29,90 € payments | 6 exist | unjustified selection |
| q140, q142 | marketing email with an invented discount code; pricing advice | out of scope | scope |

The existing validator (ADR 0008/0009) checks citations, ids and amounts, and only warns. Status words, durations, absence and selection are explicitly listed as "not verified" in the answer lifecycle. The `contradicted_evidence` guard (ADR 0011) catches only denials of payments that SQL found.

## Options

| Option | Accuracy | Complexity | Latency / cost | Maintenance | False positives | Regression risk |
|---|---|---|---|---|---|---|
| 1. Better prompt ("never compute durations", "do not say there are none") | Unproven; ADR 0005 measured regressions from a global prompt change | Low | None | Low | n/a | **High**: affects every answer |
| 2. More backend-written answers | Exact where it applies | Medium per question type | Lower (no LLM) | Parser vocabulary grows | None for the answer itself | Low, measurable deterministically. Cannot cover explanations |
| 3. **Deterministic claim checks** on Qwen's text against the evidence | Exact for the claims it recognises; silent on others | Medium (one module) | ~0 | Patterns per claim type | Must be measured; rules kept narrow | Low: only flagged answers change |
| 4. Structured JSON output (claims + sources) | Better extraction, still Qwen's values | High: prompt, parser, fallback for invalid JSON | Longer outputs | High | — | High: changes every answer (deferred in ADR 0003/0008) |
| 5. Second LLM as verifier | Probabilistic | Medium | ×2 latency and calls | Model behaviour drifts | Unknown | Medium. Rejected in ADR 0011 for the same reasons |
| 6. Abstain when a claim cannot be verified | Safe | Low | None | Low | Cost: useful answers lost | Must be limited to claims that are both financial and unverifiable |

## Decision

Combine 2, 3 and a narrow 6. No prompt change, no new model, no new service.

1. **Backend owns the facts it can compute.**
   - **Durations:** every paid payment's evidence gains a verified line, "tempo dalla creazione al pagamento: 3 min 1 s (181 s)", computed from `created_at` and `paid_at` (ADR 0004 style). Qwen can copy it instead of subtracting.
   - **Rankings:** "classifica / ranking … prodotti / products" and "per unità vendute / by units sold" reach the existing `top_product` SQL ranking. When the question asks for a ranking, the backend answer lists every product in order.
   - **Scope:** requests to write marketing or promotional content (email, newsletter, post, letter, campaign…) and requests for pricing or sales advice/forecasts get a deterministic `out_of_scope` answer before retrieval. No Qwen call.
2. **Claims in Qwen's text are checked against the evidence Qwen received** (`app/rag/claims.py`), as *entity → attribute → value → evidence*:

   | Claim | Recognised when | Verified against | Verdict when it disagrees |
   |---|---|---|---|
   | Status of a payment | "stato/status/state" + present-tense verb + a status word, attributed to one payment (id in the sentence, else the only payment in the evidence); not in a sentence about history or with a negation | `Stato:` of that payment's evidence | `contradicted_status` |
   | Duration for a payment | a duration ("3 minuti e 4 secondi", "31 seconds") in a sentence about creation/payment timing; not about configuration (session lifetime, backoff) | every interval between timestamps in that payment's evidence, at the precision stated | `contradicted_duration` |
   | Absence | the existing denial detector (ADR 0011) on the semantic route, where retrieval is top-k by similarity and therefore never exhaustive | — (cannot be verified) | `unverified_absence` |
   | Selection | the question names an amount; two or more payments in the evidence have it; the answer speaks of "the payment" and refers to only one of them | the evidence | `unjustified_selection` |
   | Offer | a discount/promo code or percentage discount not present in any source | all sources | `unsupported_offer` |

   A number is never "verified" just because it appears somewhere in the context: status and duration are checked on the payment the sentence is about.
3. **Verdicts and handling.** Each answer gets one of *verified*, *partially verified* (some claims verified, others not recognised), *unverifiable* (no checkable claim), *contradicted*. A contradicted or unsupported answer is **not shown**:
   - on `sql_find`, it is replaced by the notice "La risposta generata contraddiceva i dati verificati…" and the backend listing of the payments found (as the `contradicted_evidence` guard does);
   - on the semantic route, by a backend message that states the limit (the search is not exhaustive; which payments among the evidence match, with their verified status), or by the scope message for an unsupported offer;
   - never by a corrected sentence invented by the backend, and never by a second generation.

   The warning code is returned in `warnings`, as for ADR 0008. Unverifiable answers are unchanged.
4. **Not handled here:** contradictions with documentation prose (q099), citation relevance, amounts without a currency, omissions without an explicit denial. They stay listed as not verifiable in the answer lifecycle.

## Why this and not the alternatives

- Every observed error is a claim about a value the backend already holds (status, timestamps, the set of payments it gave the model). Checking that claim is exact, cheap and testable; asking the model to be more careful is not.
- Replacing only contradicted answers keeps correct explanations untouched. The guard can only make an answer shorter and backend-written, never add a figure.
- A structured JSON contract or a verifier model would change every answer to fix a handful; both remain options if claim recognition proves too narrow on a new blind set.

## Consequences

- Recognition is pattern-based. A status or duration phrased in a way the patterns miss is not checked (the answer stays unverifiable, as today). New phrasings need new patterns and tests; the retrospective check on saved answers is the guard against false positives.
- The scope rule refuses two kinds of request (promotional content, pricing advice); a legitimate question with the same words ("scrivi l'email del cliente" is a data request, handled as before) is excluded by construction and covered by tests.
- The `classifica` routing and the scope rule were written after seeing q090, q140 and q142: those cases are regression cases. Generalization needs a new blind set (TAH-140 rules).
- The duration line changes the indexed text of paid payments: a re-index (done by `rag_eval_phases.sh`) and a new series are required.

## Evidence (2026-11-19, without models)

Details in [`evaluation/tah143/README.md`](../../evaluation/tah143/README.md).

- **Tests:** 53 new tests (`tests/test_verified_claims.py`); 940 backend tests pass; ruff, mypy clean.
- **Mutation testing:** 27/27 mutants killed (claims, fallback, scope, ranking, duration fact).
- **Deterministic regression (419 questions, four datasets):** only the 4 intended routes change (q090 → SQL ranking; q140, q142, `v2-oos-greeting` → `out_of_scope`). Every other route and backend answer is byte-identical.
- **Retrospective, 768 saved Qwen answers of the last two series:** 31 answers would be replaced, all wrong (q013 6/6, r018 4/6, q055 6/6, r015 5/6, q149 6/6, q140 1, `v2-typo-rejected` 3); **0 correct answers replaced**. 54 status claims and 4 duration claims verified. On all 1,769 saved answers since `document_facts`: 3 correct answers would have been replaced, all from an older code version where the question did not yet reach SQL.
- **Real runs:** pending on the Mac; commands and closing criteria in the TAH-143 README. The status above changes to "verified with real runs" only after they are read.

**Verdict vocabulary** (`ClaimReport.verdict`): `verified` (every recognised claim matches the evidence; the rest of the text is not checked, so this is "partially verified" in the strict sense), `unchecked` (no recognised claim: answer unchanged, as before), `unverifiable` (an absence, a selection or an offer asserted without support: replaced), `contradicted` (a status or duration that disagrees with the evidence: replaced).
