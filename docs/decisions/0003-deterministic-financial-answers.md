# ADR 0003: Deterministic answers for aggregates and missing data

**Status:** Accepted (2026-11-17)

## Problem

The 7B model miscounted ("4 pagati" instead of 10) and summed only what it saw. Some questions have no answer in the data (refunds, payouts) or ask for data the assistant must not see (customer email).

## Alternatives considered

- Tool calling by the model: the model would still choose and phrase numbers.
- Full JSON answer contract: more surface, not needed for the observed defects.
- Templates filled by the backend (chosen).

## Decision

Counts, totals ("incassato" = gross of `paid` payments, per currency), best-selling product, no match, metrics not recorded and unavailable attributes are answered by the backend without the LLM, in the question's language, citing the SQL result.

## Motivation and evidence

Aggregates 2/7 → 7/7 in every run; 17–18 of 50 questions no longer call the model.

## Consequences

Aggregate answers are Italian only; English aggregate questions get Italian text. Refunds, net and payouts stay unavailable until they are modelled.
