# ADR 0008: Non-blocking answer validation

**Status:** Accepted (2026-11-17). Amount check superseded by [ADR 0009](0009-amount-verification-by-role.md)

## Problem

Model answers were occasionally uncited (1/3 runs) or cited an irrelevant chunk; earlier a look-alike payment was cited. The API presented every answer the same way.

## Alternatives considered

- Full JSON answer contract with regeneration: larger change, latency cost not measured.
- Block or rewrite answers: risks hiding correct content.
- Deterministic checks that return warnings (chosen).

## Decision

`validate_answer` flags `uncited`, `invalid_citation`, `unknown_payment_id`, `amount_not_in_sources` (currency-marked amounts only) and `personal_data`. The text is unchanged; `warnings` is returned by the API, shown in the dashboard and counted by the evaluation.

## Motivation and evidence

Unit and mutation tests pass. On the 366 real answers of 12 runs: `uncited` 3/3 correct, no other check fired except `amount_not_in_sources`, which gave only false positives on thresholds (fixed by ADR 0009).

## Consequences

Citation relevance remains unverified. Warnings could be used later to trigger one regeneration, if measured worthwhile.
