# ADR 0004: Derived facts computed from payment events

**Status:** Accepted (2026-11-17)

## Problem

"Recovered by reconciliation" and "paid after the session expired" existed only as history lines; the model had to infer them and cited a look-alike payment.

## Alternatives considered

- Store the facts as columns: duplicates the event history and needs a migration.
- Let the model infer them: observed failures.
- Compute them from `payment_events` (chosen).

## Decision

One rule implemented in Python (documents, answers) and SQL (`EXISTS` filters), with a test that both agree on every seed payment. Lookups filtered on a derived fact get backend-written "Fatti verificati" lines citing the payment.

## Motivation and evidence

`derived-recovered` failed 3/3 before, passes 3/3 after; `derived-late` 3/3.

## Consequences

New derived facts need both implementations and the parity test.
