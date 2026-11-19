# ADR 0006: Found payment, unavailable attribute

**Status:** Accepted (2026-11-17)

## Problem

A question about a found payment's customer email was answered with the payment listing, as if the payment answered any question about it.

## Alternatives considered

- Let the model refuse: its refusals were overridden on the SQL route, and model refusals are not reliable.
- Expose the attribute: personal and bearer data must not reach the assistant.
- Deterministic answer (chosen).

## Decision

The parser recognises requests for customer email, name, phone, address, card data, checkout link and Idempotency-Key. On a SQL lookup the backend answers "Ho trovato il pagamento … [1], ma l'email del cliente non è disponibile…" without reading the attribute.

## Motivation and evidence

`pii-email` 3/3 in series *corrected*.

## Consequences

Recognition is rule-based; an unrecognised phrasing reaches the model, which never has the attribute in its evidence.
