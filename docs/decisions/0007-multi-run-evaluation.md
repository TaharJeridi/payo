# ADR 0007: Multi-run evaluation with per-case comparison

**Status:** Accepted (2026-11-17)

## Problem

Identical runs gave 45, 48 and 46 cases out of 50. Single-run totals made noise look like improvements and hid regressions behind unchanged totals.

## Alternatives considered

- Single run per change: unreliable.
- LLM-as-judge: adds a second source of variability.
- Three runs, per-case comparison, separate metrics (chosen).

## Decision

Every change is measured with three runs of identical code, configuration and dataset; failures are classified as stable, variable or new regressions; metrics are reported separately; when checks change, previous series are re-scored from their saved answers.

## Motivation and evidence

The *targeted* series had an unchanged-looking total but four new stable failures, found only by the per-case comparison.

## Consequences

About 15 minutes per series on a laptop. Three runs show a range, not a distribution.
