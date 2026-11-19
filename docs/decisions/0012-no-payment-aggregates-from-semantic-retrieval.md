# ADR 0012: No payment counts, totals or lists from semantic retrieval

**Status:** Accepted (2026-11-18)

## Problem

Questions about sets of payments that the parser does not turn into filters ("Quanti checkout aperti ho?", "quanti soldi mi ha portato il Report Pack?") went to the semantic route. There Qwen counted or summed over the 5 chunks it was given.

On the v2 runs this produced stable wrong facts:

- "Hai due checkout aperti" (there are 3);
- "un totale di 147,00 €" (the total is 196,00 €);
- "Cinque pagamenti PayFlow Starter… ottobre" (3 were paid).

ADR 0011 does not reach these questions because they never touch SQL (TAH-138).

## Alternatives considered

- **Extend the parser with more synonyms.** This fixes only the phrasings we have seen, and it is a parser change (TAH-134 and TAH-135 stay open for that).
- **Send all chunks of every payment to the model.** That is the long-context failure of TAH-139 again.
- **Abstain deterministically when a semantic-route question asks for a count, a total or a list of payments** (chosen).

## Decision

`asks_for_payment_set` (in `query_parser.py`) marks a semantic plan as a payment-set question when it contains any of:

- "quanti/quante/how many" near a payment noun;
- a money verb ("quanto ho incassato", "quanti soldi", "how much … bring in");
- a list cue with a plural payment noun ("elenco/quali/mostra/ci sono … pagamenti");
- a plural status adjective ("pagati", "scaduti", "expired") without an explanation word.

When the question quotes no payment id, the service answers without retrieval and without Qwen (`unrecognized_payment_set`). The answer says the criteria were not recognised and gives two phrasings that work. It contains no number.

## Evidence (2026-11-18)

- **Unit and mutation tests:** 17 tests; 8 of 8 mutants killed.
- **No false triggers:** no historical question, documentation question or single-payment question triggers it.
- **Exact re-score of the saved real runs.** The abstention is deterministic, and no other code path changed, so the affected cases can be re-scored exactly:

| | Before | After |
|---|---|---|
| v1, cases passed | 82 / 83 / 81 | 78 / 79 / 77 |
| v2, cases passed | 70 / 74 / 71 | 69 / 72 / 70 |
| Wrong count or total answers by Qwen, in 6 runs | 13 | 0 |
| Correct answers that are now refused | – | 16 |

- **No remaining counts on the semantic route:** in the 9 saved runs, no other non-documentation answer on the semantic route contains a count or a total of payments.

## Consequences

- **More refusals, no invented counts.** A merchant gets "riformula" instead of a plausible wrong figure. Recovering those answers needs parser coverage (TAH-134, TAH-135), not the model.
- **The detection is a vocabulary.** A count phrased in a way it does not recognise still reaches Qwen.
- **v2 is now a regression set** for this change. The next generalization measurement needs a new dataset.

## Follow-up: TAH-141 (2026-11-18)

The refusals measured above came from parser vocabulary, as expected. TAH-141 extends the parser, not this rule: the decision is unchanged.

- **Now structured:**
  - product names, "checkout" and "vendite" used as the subject;
  - status adjectives after a noun;
  - colloquial money phrases;
  - misspelled nouns and months.
- **Effect:** on v1 and v2 the cases routed to `unrecognized_payment_set` go from 20 to 0, without a model and without a backend answer to another question. See [generalization](../quality/generalization.md).
