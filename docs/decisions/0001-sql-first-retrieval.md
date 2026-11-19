# ADR 0001: SQL-first retrieval for transactional facts

**Status:** Accepted (2026-11-17)

## Problem

Amounts, statuses and dates were retrieved by vector similarity. Embeddings do not separate "19,80 €" from other amounts (the right payment ranked 9th; all payments scored ~0.80), and counts were computed by the model over the top-5 chunks.

## Alternatives considered

- Better embeddings or a reranker: does not make similarity exact for numbers.
- LLM-generated SQL: flexible, but the model would control queries and tenant filters.
- Deterministic parser + typed filters + parameterized SQL (chosen).

## Decision

A deterministic parser turns the question into a `QueryPlan` with validated `PaymentFilters`; `PaymentRepository` runs merchant-scoped parameterized queries; matching payments become the evidence. Vector search remains for documentation and unrecognised questions.

## Motivation and evidence

Exact values and tenant scoping are enforced in code and testable without the model. Measured: amount/status/date lookups 0/10 → 10/10; payment described in words hit@1 0.33 → 1.00.

## Consequences

Questions phrased outside the parser's rules fall back to semantic retrieval. The parser was written against the evaluation cases; generalization must be measured on a held-out set.
