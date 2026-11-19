# Blind generalization dataset v3 (TAH-140)

This folder holds the provenance of `backend/app/rag/evaluation/generalization_v3.json`:

- the neutral specification the agents worked from;
- the agents' prompts and raw outputs;
- the independent checks;
- the review;
- the manifest.

The dataset is frozen. Do not edit any file here, or the dataset itself, after the first run.

| File | Role |
|---|---|
| `specification.md` | Neutral product specification: functional description, data model, catalog, statuses, the 20 synthetic payments with full histories, public contract, privacy rules, public documentation (`docs/architecture.md` sections 1–7 and 9). The only information the agents received. |
| `generator_prompt.md`, `generator2_prompt.md`, `reviewer_prompt.md` | Prompts given to the isolated agents, with local paths replaced by `<workdir>` / `<repo>` (public copy, see below) |
| `generated_raw.json` | First generator output, 150 questions, unchanged |
| `generated_replacements_raw.json` | Second, blind generation of 19 replacement questions, unchanged |
| `independence_check.py`, `independence_first_generation.json`, `independence_final.json`, `replacements.json` | Post-generation overlap check against the historical, v1 and v2 questions, and the 19 cases replaced |
| `oracle.py`, `verification_queries.json`, `oracle_report.json` | Independent golden answers: SQL on `payments`/`payment_events` and Python on the seed definitions; no parser, repository or assistant |
| `review.json`, `review_resolution.py`, `review_resolution_log.json`, `reviewed_questions.json` | Independent review and the 15 recorded resolutions (no question reworded) |
| `build_dataset.py` | Conversion to the runner format, with a `golden` block of typed facts per case |
| `fact_check.py` | Fact-level judgement of runs: ids, amounts, counts, statuses, reasons and products, in any format and either language |
| `agent_audit.json` | Every tool call each agent made, extracted from its transcript |
| `MANIFEST.json` | Dataset hash, stages, configuration, independence limits and the hash of every file here |

How the dataset was built, its limits and how to run it: [docs/quality/generalization.md](../../docs/quality/generalization.md#blind-dataset-v3-tah-140).

## Public copy

This folder is the public copy of the frozen provenance. Four files differ from the originals frozen on 2026-11-18:
`agent_audit.json`, `generator_prompt.md`, `generator2_prompt.md`, `reviewer_prompt.md`, plus this README and
`MANIFEST.json`. The only changes are the replacements listed in `MANIFEST.json` → `public_copy` (the generation
tool and model identifiers are withheld; local working paths become `<workdir>` / `<repo>`). Questions, golden
answers, oracle results, scripts and the dataset `generalization_v3.json` (sha256 `e5454c628bdd…`) are byte-identical
to the originals. `MANIFEST.json` → `files` lists the hashes of this public copy; `public_copy.original_sha256`
lists the hashes of the frozen originals, which are kept privately and unchanged. The tool and model identity is
therefore not reproducible from this repository alone; everything needed to re-run the dataset is.
