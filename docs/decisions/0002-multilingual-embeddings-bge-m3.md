# ADR 0002: Multilingual embeddings (bge-m3)

**Status:** Accepted (2026-11-16)

## Problem

Documentation is in English, questions in Italian. With `nomic-embed-text` Italian documentation questions never retrieved the right section (hit@1 0/6), and the model then invented answers from unrelated data.

## Alternatives considered

- Translate the documentation: duplicates content.
- Translate the question with the LLM: one more model call and its errors.
- Multilingual embedding model (chosen).

## Decision

`bge-m3` via Ollama, no task prefixes, its own Qdrant collection; `RAG_MIN_SCORE` re-calibrated to 0.48 on the evaluation set.

## Motivation and evidence

Same 29 cases, same chat model: docs in Italian hit@1 0.00 → 1.00, cases passed 17 → 21, mean latency 9.0 → 7.4 s.

## Consequences

1.2 GB model; the threshold is model-specific and must be re-measured if the model changes.
