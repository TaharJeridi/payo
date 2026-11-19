# ADR 0005: Refusals and answer language handled after generation

**Status:** Accepted (2026-11-17)

## Problem

Qwen sometimes refused in English or Chinese to Italian questions. A prompt rule asking for a special `NO_EVIDENCE` token fixed the language but made the model refuse answerable questions (2 cases 3/3) and shortened answers (amounts omitted).

## Alternatives considered

- Special token in the prompt: measured regressions (series *targeted*, mean 44.3/50).
- Change the global prompt again: affects every case.
- Normalize refusals after generation (chosen).

## Decision

Prompt rule 4 is the original ("say that you do not know"). The backend writes refusals itself when it knows there is no evidence, and replaces a model refusal only when it is in another language than the question's. Answers with content are never replaced.

## Motivation and evidence

Series *corrected*: false refusals 0/38, correct abstentions 12/12, language 50/50 in all three runs; amounts present again (prompt was the cause: documentation was unchanged between the series).

## Consequences

A normal answer in the wrong language is measured but not corrected.
