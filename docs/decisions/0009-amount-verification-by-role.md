# ADR 0009: Amounts verified by their role in the sentence

**Status:** Accepted (2026-11-17). Verified on real runs (`hardening-1..3`, `gen-1..3`).

## Problem

`amount_not_in_sources` compared every currency-marked amount in an answer with the amounts in the cited sources. In the `validated` series it fired 6 times, and all 6 were false positives. Range and threshold answers repeat the bound from the question ("supera i 90 €"), and no payment has that amount. Applied to the 366 saved answers of 12 real runs, the old rule raised 25 flags, all on correct threshold or range answers.

## Alternatives considered

- **Whitelist every amount typed in the question** (proposed in TAH-133). Rejected: any figure a user types would become "verified". "Il pagamento da 55 € è pagato?" would let a model answer "sì, 55 €" without warning.
- **Drop the amount check.** Rejected: it is the only deterministic check on financial figures written by the model.
- **Classify each amount by role and verify it against the matching evidence** (chosen).

## Decision

Each currency-marked amount (EUR, USD, GBP; Italian and English formats; integer cents, no floating point) gets a role from the words before it in its sentence, and is verified against the evidence for that role:

| Role | Detected by | Verified against | Warning |
|---|---|---|---|
| Threshold | "supera", "superiore a", "almeno", "meno di", "at most", ">", … | Bound of the amount filter that the backend **applied with SQL**: same value, same direction (minimum/maximum) and same currency. Strictness is checked when the wording is unambiguous ("almeno" vs "superiore a"); "oltre", "sopra", "fino a", "over" and "up to" are ambiguous. | `threshold_unverified` |
| Range bound | "tra X e Y", "between X and Y", "da X a Y" | Minimum and maximum of the applied filter | `threshold_unverified` |
| Aggregate | "totale", "incassato", "somma", "in total", … not tied to a single payment | Amounts computed by the backend (`sql:*` sources) | `aggregate_unverified` |
| Value | anything else | Amount or unit price of the payment the amount is attributed to: the nearest payment id in the sentence, otherwise the nearest citation, otherwise every cited source | `amount_wrong_payment` (found in another payment), `amount_not_in_sources` (found nowhere) |

The question itself is never evidence. Without an applied filter (semantic route), a threshold cannot be verified and is flagged.

## Motivation and evidence (2026-11-17)

- **Unit tests:** 9 role classifications, 9 correct answers without warnings, 13 defects flagged, the aggregate path, and the two real answers from the `validated` runs. All backend aggregate answers of the historical set also pass the validator.
- **Mutation tests:** 11 of 12 targeted mutants killed. The survivor reads payment amounts from the whole text instead of the `Importo:` line. It is equivalent for today's payment documents, which contain no other amounts.
- **Retrospective test on real answers:** the 366 saved answers of 12 real runs, with their sources rebuilt from the seed.
  - The rebuild was checked first: the old rule reproduces exactly the 6 flags of `validated`.
  - The old rule flags 25 answers, all false positives. The new rule flags 0.
  - Raising one amount by 1 € in each of the 126 answers that contain one produced 126 flags out of 126 (108 `amount_not_in_sources`, 18 `threshold_unverified`).

## Real runs (2026-11-17)

- **`hardening-1..3`** (historical set): 0 amount warnings, including on the threshold and range answers that produced the 6 false positives before.
- **`gen-1..3`** (generalization set):
  - 1 true positive per run in 2 of 3 runs: `threshold_unverified` on "superiori a 89,70 €" for an "almeno" filter, in a sentence that is also false;
  - 1 false positive per run: "tra 40 e 50 euro", where only the upper bound has the currency.
- **Fix for the false positive:** a bare number joined to a currency-marked one ("tra 40 e 50 euro", "between 40 and 50 EUR", "da 40 a 50 €") is now read as a range, and "massimo" alone as an inclusive maximum.
- **Re-check on all 662 saved model answers of 18 real runs:**
  - 0 false positives;
  - 2 true positives;
  - 221/221 answers flagged after an amount was raised by 1 €.
- **Mutation tests:** 13 of 14 mutants killed.

## Consequences

- Five warning codes cover amounts. The dashboard labels all of them.
- Not verified:
  - amounts written without a currency;
  - whether a correctly verified amount is used in a true sentence ("nessun pagamento supera i 90 €; il più alto è 98,00 €" passes);
  - statuses written in free text.
- The role is decided by a word list in Italian and English. Unusual wording falls back to "value", which is the strictest path for transactional amounts.
