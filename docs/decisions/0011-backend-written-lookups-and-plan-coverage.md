# ADR 0011: Backend-written lookups and plan coverage

**Status:** Accepted (2026-11-17). Verified with real runs on 2026-11-18 (`consistency`, `gen`, `gen2`; see [generalization](../quality/generalization.md)).

## Problem

Qwen stated that payments did not exist although they were among its sources ("non ci sono pagamenti PayFlow Pro con quantità 3", "Non ci sono pagamenti che superano 90 euro… il più alto è 98,00 €").

The reconstruction of the four cases in TAH-139 shows two origins:

| Origin | Cases | What happened |
|---|---|---|
| Complete plan, correct SQL | `amt-over` (1 payment in evidence), `g-amt-atleast-8970` (2 payments) | SQL had already answered the question exactly, and the backend summary stated the count. Qwen omitted or denied a payment anyway. |
| Incomplete plan | `g-comb-pro-qty3` (quantity ignored), `g-typo-pro-failed` (misspelled status ignored) | SQL returned a superset of 8 payments, and Qwen had to filter them itself. It missed the matching one. |

In both, the model was asked to restate a fact that the database already knew.

## Alternatives considered

- **Change the prompt.** Rejected: it does not remove the failure mode, and earlier prompt changes caused regressions (ADR 0005).
- **A second model to check the first.** Rejected: more latency and cost, and it is still probabilistic.
- **Only a post-generation check.** Kept as a backstop. On its own it cannot catch omissions ("listed 1 of 2" without saying "no others").
- **Decide existence, membership and absence in the backend, and know when the plan is incomplete** (chosen).

## Decision

1. **Plan coverage.** The parser reports `unresolved` terms: parts of a structured question that no filter represents. Detection is generic:
   - numbers no filter accounts for;
   - a date qualified by an unmodelled operator ("dopo il 25 ottobre", "prima metà di ottobre");
   - a negated status or money verb ("non riuscite", "ancora da incassare");
   - superlatives;
   - a quantity word without a quantity filter;
   - misspelled filter words.
2. **Complete plan, listing question** ("trova", "quali", "c'è", "mostrami"… and no "perché / cosa / quando / why"): the backend writes the answer (`sql_list`, no LLM). It gives the total from SQL, every payment shown with its citation, and a failure reason when there is one. When more payments match than are shown, it says so ("Ne mostro 8, i più recenti: gli altri 2 non sono elencati qui"); the lookup limit is never presented as the full set. A yes/no status question about one payment gets "Sì/No: il pagamento … risulta …".
3. **Incomplete plan** (`sql_partial`, no LLM):
   - with matches: "Ho interpretato solo una parte della domanda: non ho interpretato «…». Con i criteri che ho interpretato (…) ho trovato N pagamenti, ma non tutti potrebbero corrispondere…";
   - without matches: "la verifica non è conclusiva", never "non ho trovato pagamenti";
   - for counts and totals: no figure, only a request to rephrase.
4. **Questions that need interpretation** (why, what happened, when, a specific payment id) still go to Qwen. If the answer **denies payments that SQL found**, it is replaced by the backend listing, and the warning `contradicted_evidence` is set.
5. Only a complete plan and an empty SQL result produce "Non ho trovato pagamenti con questi criteri".

## Evidence (2026-11-17, no models: parser, SQL and backend-written answers)

**Tests:**

- 45 tests in `test_evidence_consistency.py`, covering the 12 required scenarios;
- 676 backend tests in total;
- mutation testing: 18/18 mutants killed.

**Contradiction detector, re-checked on the 489 saved real Qwen answers on the SQL route (18 runs):**

| Triggers | Count | Assessment |
|---|---|---|
| Denials of payments present in the evidence | 11 | True contradictions |
| An invented "non ci sono pagamenti successivi a questo" | 3 | Unsupported claim |
| Denials on misread plans (statuses or dates lost); these now go to `sql_partial` and never reach the model | 8 | – |
| Answers with a complete plan wrongly replaced | 0 | – |

**The four TAH-139 cases:** now answered without Qwen. They become `sql_list` (`amt-over`, `g-amt-atleast-8970`: exact set) or `sql_partial` (`g-comb-pro-qty3`, `g-typo-pro-failed`: the unread term is named).

**Generalization v2** (102 new questions, written before the first run on them): see [generalization](../quality/generalization.md).

- Backend-written answers correct: 53/61.
- 4 incomplete plans detected.
- Not detected: 1 false absence ("non superano i 10 euro") and 3 lists for a different question.

## Consequences

- **Fewer LLM calls on lookups**, and listing answers become deterministic and testable without Qwen. In exchange they are less conversational.
- **More abstentions on misread questions:** `sql_partial` instead of a wrong figure. These count as refusals in the evaluation.
- **Coverage is heuristic.** A misreading it does not detect still produces a backend answer to a different question. That answer states the criteria it applied, which is the remaining mitigation (TAH-134, TAH-135).
- **The contradiction guard is lexical.** It catches explicit denials, not omissions inside a model's answer to an explanation question.
