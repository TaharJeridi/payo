# ADR 0010: Independent, frozen generalization dataset

**Status:** Accepted (2026-11-17)

## Problem

The query parser was written against the 50 historical cases, and its 100% accuracy there is not evidence that it understands new questions. Adding new cases to the same file would mix development cases with measurement cases in one score.

## Decision

- A second dataset, `backend/app/rag/evaluation/generalization.json`, version 1.0.0:
  - 117 questions in 16 groups;
  - written from the seed data and the product, not from the parser's rules or from model answers.
- **Frozen:** its sha256 is stored in `generalization.sha256`, and a test fails if the file changes. Every run records the dataset name, version and hash.
- **Expectations verified twice**, independently of the assistant:
  - by a pure-Python oracle over the seed definitions;
  - by SQL on the seeded database.

  Each lookup has a `truth_filters` field, and each aggregate a hand-computed `truth_value`. Writing them caught 1 wrong hand value out of 117 cases before the freeze.
- **Reported separately**, never merged into one score with the historical set.
- **The parser is not changed before the baseline is recorded.** A case used to develop a fix then becomes a regression case, and generalization must be measured on a new dataset (v2).

## Consequences

- Cases have a `group` (the 16 groups) and a `language`; reports break results down by both.
- Parser generalization is a measured number, not an assumption. The first deterministic baseline is in [generalization](../quality/generalization.md).
- Each parser fix spends part of the dataset's independence, so v2 must be written before the next generalization claim.
