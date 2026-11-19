"""Markdown report over several evaluation runs, optionally compared with a baseline series.

Inputs are the JSON files written by ``run --format json|both``. Metrics are recomputed from
the saved results, so runs written before the metrics existed are measured the same way;
the pass/fail checks are those stored in each file.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.rag.evaluation.metrics import ERROR_CLASSES, METRICS, case_errors, reliability_summary
from app.rag.evaluation.runner import Case, Dataset


@dataclass(frozen=True, slots=True)
class Run:
    name: str
    settings: dict[str, Any]
    results: list[dict[str, Any]]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.get("passed"))


def load_run(path: Path) -> Run:
    payload = json.loads(path.read_text(encoding="utf-8"))
    name = path.stem.removeprefix("rag_eval_phase_")
    return Run(name=name, settings=payload.get("settings", {}), results=payload["results"])


def _known(run: Run, cases: Mapping[str, Case]) -> list[dict[str, Any]]:
    return [r for r in run.results if r["id"] in cases]


def _metric_cell(summary: Mapping[str, Any], name: str) -> str:
    metric = summary["metrics"][name]
    return f"{metric['passed']}/{metric['applicable']}" if metric["applicable"] else "n/a"


def _range(values: Sequence[str]) -> str:
    return " / ".join(values)


def _failures(runs: Sequence[Run], cases: Mapping[str, Case]) -> dict[str, list[bool]]:
    outcome: dict[str, list[bool]] = {}
    for run in runs:
        for result in _known(run, cases):
            outcome.setdefault(result["id"], []).append(not result.get("passed", False))
    return outcome


def render_report(
    dataset: Dataset,
    runs: Sequence[Run],
    baseline: Sequence[Run] = (),
    *,
    code_version: str | None = None,
) -> str:
    cases = {c.id: c for c in dataset.cases}
    summaries = [reliability_summary(_known(run, cases), cases) for run in runs]
    base_summaries = [reliability_summary(_known(run, cases), cases) for run in baseline]
    settings = runs[0].settings if runs else {}
    lines = [f"# Evaluation report: {dataset.name}", ""]

    lines += ["## Configuration", "", "| | |", "|---|---|"]
    lines.append(f"| Dataset | `{dataset.name}` version `{dataset.version}`, {len(dataset.cases)} cases |")
    lines.append(f"| Dataset sha256 | `{dataset.sha256}` |")
    recorded = {str(v) for run in runs if (v := run.settings.get("dataset_sha256"))}
    if recorded and recorded != {dataset.sha256}:
        lines.append(f"| **Dataset changed** | runs recorded {', '.join(sorted(recorded))} |")
    lines.append(f"| Chat model | `{settings.get('chat_model', 'not recorded')}` |")
    lines.append(f"| Embedding model | `{settings.get('embed_model', 'not recorded')}` |")
    for key in ("min_score", "top_k", "max_doc_chunks", "sql_lookup", "sql_aggregates", "document_facts"):
        if key in settings:
            lines.append(f"| `{key}` | {settings[key]} |")
    lines.append(f"| Code version | {code_version or settings.get('code_version') or 'not available (no git)'} |")
    fingerprints = sorted({str(v) for run in runs if (v := run.settings.get("code_fingerprint"))})
    lines.append(f"| Code fingerprint | {', '.join(f'`{f}`' for f in fingerprints) or 'not recorded'} |")
    lines.append(f"| Runs | {len(runs)}: {', '.join(r.name for r in runs)} |")
    if baseline:
        lines.append(f"| Baseline | {len(baseline)}: {', '.join(r.name for r in baseline)} |")
    lines.append("")

    lines += ["## Metrics", "", "Passed / applicable cases per run. `n/a`: no case exercises the metric.", ""]
    header = "| Metric | " + " | ".join(r.name for r in runs)
    header += " | Baseline |" if baseline else " |"
    lines += [header, "|---" * (len(runs) + 1 + bool(baseline)) + "|"]

    def row(label: str, current: Sequence[str], base: Sequence[str]) -> str:
        cells = " | ".join(current)
        return f"| {label} | {cells} |" + (f" {_range(base)} |" if baseline else "")

    lines.append(
        row(
            "Cases passed",
            [f"{r.passed}/{len(_known(r, cases))}" for r in runs],
            [f"{r.passed}/{len(_known(r, cases))}" for r in baseline],
        )
    )
    for name in METRICS:
        lines.append(
            row(name, [_metric_cell(s, name) for s in summaries], [_metric_cell(s, name) for s in base_summaries])
        )
    lines.append(
        row(
            "LLM calls",
            [str(s["llm_calls"]) for s in summaries],
            [str(s["llm_calls"]) for s in base_summaries],
        )
    )
    for key in ("mean", "p95"):
        lines.append(
            row(
                f"Latency {key} (s)",
                [f"{s['latency_ms'][key] / 1000:.2f}" for s in summaries],
                [f"{s['latency_ms'][key] / 1000:.2f}" for s in base_summaries],
            )
        )

    def warnings(run: Run, code: str) -> str:
        if not any("warnings" in r for r in run.results):
            return "not recorded"
        return str(sum(code in r.get("warnings", []) for r in run.results))

    warning_codes = sorted({w for run in runs for r in run.results for w in r.get("warnings", [])})
    for code in warning_codes:
        lines.append(row(f"Warning `{code}`", [warnings(r, code) for r in runs], [warnings(r, code) for r in baseline]))
    lines.append("")

    outcome = _failures(runs, cases)
    stable = sorted(i for i, f in outcome.items() if f and all(f))
    variable = sorted(i for i, f in outcome.items() if any(f) and not all(f))
    lines += ["## Failures", ""]
    lines.append(f"- **Stable** (all {len(runs)} runs): {', '.join(f'`{i}`' for i in stable) or 'none'}")
    lines.append(
        "- **Variable**: " + (", ".join(f"`{i}` ({sum(outcome[i])}/{len(outcome[i])})" for i in variable) or "none")
    )
    if baseline:
        base = _failures(baseline, cases)
        regressions = sorted(i for i in outcome if any(outcome[i]) and i in base and not any(base[i]))
        fixed = sorted(i for i in base if all(base[i]) and i in outcome and not any(outcome[i]))
        lines.append(
            "- **New regressions** (passed in every baseline run): "
            + (", ".join(f"`{i}`" for i in regressions) or "none")
        )
        lines.append(
            "- **Fixed** (failed in every baseline run, passes now): " + (", ".join(f"`{i}`" for i in fixed) or "none")
        )
    lines.append("")

    failing = [i for i, f in outcome.items() if any(f)]
    if failing:
        lines += ["| Case | Group | Failed runs | Error classes |", "|---|---|---|---|"]
        for case_id in sorted(failing):
            classes: set[str] = set()
            for run in runs:
                for result in _known(run, cases):
                    if result["id"] == case_id and not result.get("passed"):
                        classes.update(case_errors(result, cases[case_id]))
            ordered = [c for c in ERROR_CLASSES if c in classes]
            group = cases[case_id].group or cases[case_id].category
            lines.append(
                f"| `{case_id}` | {group} | {sum(outcome[case_id])}/{len(outcome[case_id])} | {', '.join(ordered)} |"
            )
        lines.append("")

    groups = sorted({g for s in summaries for g in s["by_group"]}) or sorted(
        {g for s in summaries for g in s["by_category"]}
    )
    key = "by_group" if summaries and summaries[0]["by_group"] else "by_category"
    lines += [f"## Results {key.replace('_', ' ')}", "", "| | " + " | ".join(r.name for r in runs) + " |"]
    lines.append("|---" * (len(runs) + 1) + "|")
    for group in groups:
        cells = []
        for summary in summaries:
            stats = summary[key].get(group)
            cells.append(f"{stats['passed']}/{stats['cases']}" if stats else "-")
        lines.append(f"| {group} | " + " | ".join(cells) + " |")
    lines.append("")

    lines += [
        "## Limits of this measurement",
        "",
        f"- {len(runs)} runs show a range, not a distribution; differences of 1–3 cases are within the "
        "variability observed between identical runs.",
        "- Answer checks are substring heuristics with a negation guard, not semantic judgement.",
        "- Warnings are counted, not judged: read the flagged answers to classify false positives.",
        "- Synthetic seed data on one machine; latency depends on the host.",
    ]
    if dataset.name != "historical":
        lines.append(
            "- A failure analysed and fixed after this report moves the case into the regression set; "
            "generalization must then be measured on a new, unseen dataset."
        )
    return "\n".join(lines) + "\n"
