# ADR 0013: Semantic constraint coverage for financial queries

**Status:** Accepted (2026-11-18), verified deterministically and on real runs. Supersedes the detection part of ADR 0011, decision 1 ("plan coverage"); the execution policy of ADR 0011 is kept.

## Context

ADR 0011 made the backend write lookups, counts and totals itself, and introduced *plan coverage*: a structured question with `unresolved` terms gets no figure, no absence claim and no list presented as complete.

The first blind measurement (dataset v3, `blind-v3-1..3`, code `9b6f609942e8`) showed that this protection fails on unseen wording. Stable in all three runs:

| Case | Question | Backend answer | Correct |
|---|---|---|---|
| q062 | how many Pro payments did I get last month | 20 | 8 (4 paid) |
| q065 | How many of my paid payments were confirmed through the webhook? | 10 | 9 |
| q078 | Totale incassato dai pagamenti confermati via riconciliazione | 444,70 € | 49,00 € |
| q085 | Incassato totale escluso il Financial Report Pack | 196,00 € | 248,70 € |
| r008 | Quanto ho incassato in tutto dagli ordini con più di un pezzo? | 444,70 € | 237,20 € |
| q044 | pagamenti scaduti di importo superiore a 20 euro | "nessun pagamento" | 2 payments |
| q147 | Show me the big payments | all 20, "no filter" | ambiguous |

The SQL was correct for the plan each time. The plan was wrong.

**Root cause.** `unresolved_terms()` looks for *known kinds* of trouble: unexplained numbers, temporal operators, negations of known status words, superlatives, a quantity word, misspellings of filter words. A word outside those lists cannot be reported, so a plan that ignores "webhook", "escluso", "pezzo", "big" or a bare "Pro" is declared complete. Two further defects make it worse:

- **A negated or excluded constraint is still applied as a filter.** "escluso il Report" filters on the Report; "never got paid" filters on `paid`.
- **"superiore" (singular) is not a comparator**, so "superiore a 20 euro" becomes "exactly 20 €".

**Risk.** A deterministic answer cites the SQL source and states its criteria. It looks verified, so a wrong figure is more convincing than a model's mistake.

## Alternatives considered

| Alternative | Assessment |
|---|---|
| Extend the regexes for each new phrasing | Fixes only what has been seen. v3 shows the failure is open-ended. Rejected as the main mechanism; still used to fix the singular comparator bug. |
| Lexical coverage of known trouble words (current) | Cannot report what it does not know. This is the defect. |
| **Semantic constraint coverage by accounting for every word** | Every word of a structured question must be explained by a recognised constraint, by the intent, or by a closed list of non-constraining words. Anything left over is a constraint the plan does not represent. Deterministic, testable, and fails safe on new words. **Chosen.** |
| LLM-assisted structured parsing with validation | Better recall of paraphrases, but a second probabilistic step on the financial path. Validation would still need the same accounting. Not justified before measuring the deterministic option. |
| Conservative abstention on every structured question with any doubt | Safe, but a measured false-abstention cost would make the assistant useless. Kept only as the fallback for words that cannot be accounted for. |

## Decision

1. **Account for every word.** For a structured plan without payment ids, the parser records which spans of the question are explained:
   - by a recognised constraint: product, status, amount and comparator, quantity, date, derived fact, restricted attribute, other-tenant reference;
   - by the intent: count, total, ranking, listing words;
   - by the subject: payment nouns.

   Every remaining word must belong to a **closed list of non-constraining words**: articles, prepositions, pronouns, auxiliaries, politeness and discourse words, and neutral domain words such as "pagamenti", "importo", "dati". Otherwise it is a constraint the plan does not represent.
2. **Classify what is left.**
   - **Unrecognised:** any other word. The plan is **INCOMPLETE**.
   - **Ambiguous:** a small closed list of vague qualifiers ("grandi", "big", "importanti", "strani", "recenti"…). The plan is **AMBIGUOUS**.
   - **Unsupported:** constraints the data model cannot filter on (customer, weekday, weekend, time of day). The plan is **UNSUPPORTED**.
   - The existing detectors (numbers, temporal operators, negations, superlatives, misspellings) still add to the unrecognised terms.
3. **Never apply a negated or excluded constraint.** A product or status preceded by an exclusion or negation ("escluso", "tranne", "senza", "except", "non", "never"…) is removed from the filters and reported as unrecognised. A partial list then shows a labelled superset, never the opposite set.
4. **Expose completeness.** `QueryPlan` gains:
   - `ambiguous`, `unsupported` and `constraints` (each recognised constraint with the text it came from);
   - `completeness`, one of `complete`, `incomplete`, `ambiguous`, `unsupported`.
5. **Execution policy**, applied before any figure is written:

| Completeness | Count / total / ranking | List | Nothing found |
|---|---|---|---|
| complete | figure | list (at most 8 shown, total stated) | "no payment matches …" |
| incomplete | no SQL, no figure; names the terms not interpreted (unchanged from ADR 0011) | labelled partial list, never presented as exhaustive | "the check is not conclusive", never "none" |
| ambiguous | no SQL; asks for a precise criterion | no SQL; asks for a precise criterion | — |
| unsupported | no SQL; states which constraint PayFlow cannot filter on | same | — |

   An ambiguous or unsupported answer is a backend-written non-answer (route `clarify` or `unsupported_constraint`). The evaluator counts it as a refusal, like the other deterministic refusals.
6. **Not changed:** the Qwen prompt, the model, the services, tenant isolation, the semantic route, single-payment lookups by id.

## Consequences

- **Accuracy:** a structured answer is written only when every word of the question is explained. A new constraint word therefore produces a non-answer, not a wrong figure.
- **Abstentions:** the cost is false abstentions on correct plans whose wording contains a word missing from the closed list. It is measured case by case on all four datasets. The list may only grow with words that cannot change the result; a word that can constrain the result must stay out of it.
- **Limits:**
  - The closed list is still a vocabulary, built in Italian and English.
  - A constraint expressed only with words that are on the list goes unnoticed: for example a meaning carried by word order, or a neutral word used as a constraint.
  - Ambiguity detection covers only the listed qualifiers.
  - Explanations about one payment (with an id) are not affected.

## Verification plan

- **Unit tests** for each class (recognised, conversational, unrecognised, ambiguous, unsupported), for exclusions and negations, and for every v3 case above. These must use general wording, not the case ids.
- **Mutation testing** of the coverage and of the policy.
- **Deterministic harness** on historical, v1, v2 and v3, compared case by case with the saved pre-change runs, counting:
  - false financial answers (wrong figure, false absence, or a list for another question, presented as complete);
  - false abstentions (an answer that was correct before and is now withheld).
- **Real runs** on the Mac with unchanged configuration.

## Results (deterministic, 2026-11-18)

| Dataset | False financial answers before → after | Correct complete answers before → after |
|---|---|---|
| historical | 1 → 1 (expectation wording, answer correct) | 28 → 27 |
| v1 | 3 → 2 (yes/no on "the" payment of a day or product, outside this change) | 74 → 73 |
| v2 | 0 → 0 | 67 → 66 |
| v3 | **7 → 0** | 46 → 36 |

- **False abstentions:** 3, where a real criterion is not represented: failure reason (`lookup-async`, q052) and "checkout page created" (r014). They still list the matching payments, labelled partial. The real runs reclassify r014 (see below).
- **Other answers no longer complete:** 5 cases are ambiguous in the golden and 6 are manipulation requests. The contract accepts a non-answer for both.
- **Two defects found while testing:**
  - the negation window could start inside a word (the "no" of "han**no**");
  - the TAH-141 typo corrector rewrote "maggiore" into "maggio".

  Both are fixed.
- **Tests and mutation testing:** 65 tests in `tests/test_constraint_coverage.py`; all 21 mutants of the coverage and the policy are caught.

## Results (real runs, Mac, 2026-11-18)

Three runs per dataset (Qwen 2.5 7B, bge-m3, configuration unchanged), compared case by case with the three baseline runs:

| Dataset | Passed after | Passed before | False backend answers presented as complete |
|---|---|---|---|
| historical | 49 / 48 / 47 | 49 / 49 / 48 | 1 → 1 (expectation wording) |
| v1 | 98 / 101 / 100 | 99 / 101 / 101 | 2 → 2 (true but incomplete yes/no) |
| v2 | 93 / 95 / 92 | 93 / 94 / 93 | 0 → 0 |
| v3 | 110 / 108 / 111 | 105 / 106 / 105 | **10 → 0 in every run** |

- **The deterministic results hold.** Routes and backend answers in the real runs are identical to the deterministic harness. The other differences are Qwen variability: the input to the model is identical.
- **The v3 baseline count is 10, not 7.** `fact_check.py` missed three complete lists that answered a different question: q144, r007 and r014.
- **Unjustified abstentions: 2** (q052, lookup-async). Useful correct answers to the 108 answerable v3 questions: 67/67/67 after, against 67/66/65 before.
- **Isolation:** passes in every run, and no id of the other tenant appears in any answer.

Details: [generalization](../quality/generalization.md#tah-142-semantic-constraint-coverage-2026-11-18-adr-0013) and [`evaluation/tah142/`](../../evaluation/tah142/README.md).

## Future evolution

- A structured-output parser (an LLM proposing filters, validated by the same accounting) if the false-abstention cost measured here stays high.
- New filters for constraints the data already supports but the parser does not (for example "confirmed by"). Each one turns an INCOMPLETE answer into a complete one, without weakening the rule.
