"""Re-score saved evaluation runs with the TAH-145 scorer rules, without calling any model.

Reads the saved ``rag_eval_phase_<run>.json`` files (repository root), never writes to them, and writes:

- ``evaluation/scorer_integrity/rescored/<run>.json``: per case, the old and new checks, pass/fail and review tags,
  plus the old and new fact-level verdicts for blind v3;
- ``evaluation/scorer_integrity/rescore_report.md``: the comparison, generated (do not edit by hand).

Only two checks can change: ``refused`` (must-refuse questions answered by the model) and the new ``answer_ids``
(payments presented by the answer, for cases with ``expect_payments``). Every other saved check is kept as it was
recorded. Before re-scoring, the script recomputes each saved ``refused`` / ``not_refused`` check with the old rule
from the saved fields and stops if one differs: the reconstruction must reproduce the recorded verdicts exactly.

Usage, from ``backend/``:
    PYTHONPATH=. python ../evaluation/scorer_integrity/rescore.py [--write-runs]

``--write-runs`` also writes each re-scored run in the runner's format, as ``rag_eval_phase_<run>-rescored.json`` in
the repository root (git-ignored like every run), so that ``scripts/rag_eval_report.sh`` can use the re-scored
series as the baseline of a new series judged by the same rules. The original run files are never modified.
"""

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.rag.evaluation.metrics import reliability_summary
from app.rag.evaluation.runner import DETERMINISTIC_REFUSALS, Case, is_refusal, load_dataset
from app.rag.evaluation.scoring import id_verdict, must_refuse_refused
from app.rag.evaluation.seed import SEED_PAYMENTS, seed_payment_id

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
V3 = "app/rag/evaluation/generalization_v3.json"
SERIES: dict[str, tuple[str, list[str]]] = {
    "historical": ("historical", [f"consistency-{n}" for n in range(7, 13)]),
    "v1": ("generalization", ["gen-q1", "gen-q2", "gen-q3", "gen-s1", "gen-s2", "gen-s3"]),
    "v2": ("generalization-v2", ["gen2-q1", "gen2-q2", "gen2-q3", "gen2-s1", "gen2-s2", "gen2-s3"]),
    "v3": (V3, ["blind-v3-1", "blind-v3-2", "blind-v3-3", "blind-v3-s1", "blind-v3-s2", "blind-v3-s3"]),
}
LABEL = {
    "consistency-7": "pre-TAH-142",
    "gen-q1": "pre-TAH-142",
    "gen2-q1": "pre-TAH-142",
    "blind-v3-1": "pre-TAH-142 (blind baseline)",
    "consistency-10": "TAH-142",
    "gen-s1": "TAH-142",
    "gen2-s1": "TAH-142",
    "blind-v3-s1": "TAH-142",
}
UNIVERSE = [str(p.id) for p in SEED_PAYMENTS]


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fact_old = _load(ROOT / "evaluation/blind_v3/fact_check.py", "fact_check_frozen")  # TAH-142 rules, frozen
fact_new = _load(OUT / "fact_check_v2.py", "fact_check_v2")  # TAH-145 rules


def legacy_refused(result: dict[str, Any]) -> bool:
    answer = result.get("answer") or ""
    return (
        is_refusal(answer)
        or (not result.get("used_llm") and result.get("route") in DETERMINISTIC_REFUSALS)
        or (result.get("route") == "sql_partial" and not result.get("selected"))
    )


def rescore(case: Case, result: dict[str, Any]) -> tuple[dict[str, bool], list[str]]:
    checks = dict(result["checks"])
    review: list[str] = []
    if result.get("answer") is None:
        return checks, review
    refused = legacy_refused(result)
    recorded = checks.get("refused", checks.get("not_refused"))
    expected_recorded = refused if case.expect_refusal else not refused
    if recorded is not None and recorded != expected_recorded:
        raise SystemExit(f"{case.id}: saved refusal check not reproduced by the old rule")
    if case.expect_refusal:
        if result.get("used_llm"):
            new, needs_review = must_refuse_refused(result["answer"], refused)
            if needs_review:
                review.append("refusal_uncertain")
            if new != refused:
                checks["refused"] = new
                if not new:
                    checks.pop("refusal_language", None)
        return checks, review
    if case.expect_payments is not None and not refused and result.get("route") != "sql_aggregate":
        verdict = id_verdict(
            result["answer"],
            [str(seed_payment_id(ref)) for ref in case.expect_payments],
            route=result.get("route") or "",
            used_llm=bool(result.get("used_llm")),
            question=case.question,
            universe=UNIVERSE,
        )
        if verdict.outcome in ("pass", "fail"):
            checks["answer_ids"] = verdict.outcome == "pass"
        else:
            review.append(f"answer_ids_{verdict.outcome}")
    return checks, review


def metric(summary: dict[str, Any], name: str) -> str:
    m = summary["metrics"][name]
    return f"{m['passed']}/{m['applicable']}"


def main(write_runs: bool = False) -> None:
    v3_raw = {c["id"]: c for c in json.loads((ROOT / "backend" / V3).read_text(encoding="utf-8"))["cases"]}
    lines = [
        "# TAH-145: saved runs re-scored with the new scorer",
        "",
        "Generated by `evaluation/scorer_integrity/rescore.py` from the saved runs. Same answers, same datasets and",
        "golden answers; no model was called. Old = verdicts recorded in the run file. New = TAH-145 rules",
        "(`app/rag/evaluation/scoring.py`). Only `refused` (model answers to must-refuse questions) and the new",
        "`answer_ids` check can differ; every other check is the recorded one.",
        "",
    ]
    changes: dict[str, list[str]] = {}
    added_passes: Counter[str] = Counter()
    for dataset, (name, runs) in SERIES.items():
        ds = load_dataset(name)
        cases = {c.id: c for c in ds.cases}
        lines += [f"## {dataset} (`{ds.name}`, sha256 `{ds.sha256[:12]}…`)", ""]
        header = "| Run | Code | Passed old → new | Abstention old → new | Payment identification old → new | Review |"
        lines += [header, "|---|---|---|---|---|---|"]
        fact_rows = []
        for run in runs:
            path = ROOT / f"rag_eval_phase_{run}.json"
            raw = path.read_bytes()
            saved = json.loads(raw)
            results = saved["results"]
            new_results = []
            per_case = []
            review_count = Counter()
            for result in results:
                case = cases[result["id"]]
                checks, review = rescore(case, result)
                row = dict(result) | {"checks": checks, "passed": all(checks.values()), "review": review}
                new_results.append(row)
                for tag in review:
                    review_count[tag] += 1
                entry: dict[str, Any] = {
                    "id": result["id"],
                    "route": result.get("route"),
                    "used_llm": result.get("used_llm"),
                    "old_passed": result["passed"],
                    "new_passed": row["passed"],
                    "changed": {
                        k: [result["checks"].get(k), v] for k, v in checks.items() if result["checks"].get(k) != v
                    }
                    | {k: [v, None] for k, v in result["checks"].items() if k not in checks},
                    "review": review,
                }
                if dataset == "v3":
                    entry["fact_old"] = fact_old.judge(v3_raw[result["id"]], result)[0]
                    verdict, detail = fact_new.judge(v3_raw[result["id"]], result)
                    entry["fact_new"] = verdict
                    entry["fact_new_detail"] = detail
                per_case.append(entry)
                relevant = {k: v for k, v in entry["changed"].items() if v != [None, True]}
                added_passes[dataset] += len(entry["changed"]) - len(relevant)
                if relevant or entry.get("fact_old") != entry.get("fact_new"):
                    what = ", ".join(f"{k} {o}→{n}" for k, (o, n) in relevant.items())
                    if dataset == "v3" and entry["fact_old"] != entry["fact_new"]:
                        what += ("; " if what else "") + f"fact {entry['fact_old']}→{entry['fact_new']}"
                    changes.setdefault(f"{dataset} `{result['id']}`: {what}", []).append(run)
            old_summary = reliability_summary(results, cases)
            new_summary = reliability_summary(new_results, cases)
            code = saved["settings"].get("code_fingerprint", "?")
            old_passed = sum(r["passed"] for r in results)
            new_passed = sum(r["passed"] for r in new_results)
            lines.append(
                f"| {run} | `{code}` | {old_passed} → {new_passed} | "
                f"{metric(old_summary, 'correct_abstention')} → {metric(new_summary, 'correct_abstention')} | "
                f"{metric(old_summary, 'payment_identification')} → "
                f"{metric(new_summary, 'payment_identification')} | "
                f"{', '.join(f'{k} {v}' for k, v in sorted(review_count.items())) or '-'} |"
            )
            if dataset == "v3":
                old_c = Counter(e["fact_old"] for e in per_case)
                new_c = Counter(e["fact_new"] for e in per_case)
                fact_rows.append(
                    f"| {run} | {old_c['pass']} → {new_c['pass']} | {old_c['fail']} → {new_c['fail']} | "
                    f"{new_c['partial']} | {new_c['uncertain']} | {old_c['clarification']} → "
                    f"{new_c['clarification']} | {old_c['manual']} → {new_c['manual']} | "
                    f"{old_c['leak']} → {new_c['leak']} |"
                )
            payload = {
                "run": run,
                "source_file": path.name,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "dataset": ds.name,
                "dataset_sha256": ds.sha256,
                "code_fingerprint": code,
                "passed_old": old_passed,
                "passed_new": new_passed,
                "metrics_old": old_summary["metrics"],
                "metrics_new": new_summary["metrics"],
                "cases": per_case,
            }
            if write_runs:
                settings = saved["settings"] | {
                    "rescored_by": "TAH-145",
                    "rescored_from_sha256": payload["source_sha256"],
                }
                (ROOT / f"rag_eval_phase_{run}-rescored.json").write_text(
                    json.dumps({"settings": settings, "results": new_results}, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
            (OUT / "rescored" / f"{run}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
        lines.append("")
        if fact_rows:
            lines += [
                "Fact-level check, `blind_v3/fact_check.py` (old) → `scorer_integrity/fact_check_v2.py` (new):",
                "",
                "| Run | pass | fail | partial (new) | uncertain (new) | clarification | manual | leak |",
                "|---|---|---|---|---|---|---|---|",
                *fact_rows,
                "",
            ]
    lines += [
        "## Cases whose verdict changed",
        "",
        "Not listed: the new `answer_ids` check passing where no check existed before "
        + ", ".join(f"({d}: {n} case-runs)" for d, n in added_passes.items())
        + ". `answer_ids` is recorded only when the outcome is `pass` or `fail`; `partial` and `uncertain`"
        " outcomes are review tags.",
        "",
    ]
    for what, runs in sorted(changes.items()):
        lines.append(f"- {what} ({len(runs)} run{'s' if len(runs) > 1 else ''}: {', '.join(runs)})")
    lines.append("")
    (OUT / "rescore_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main(write_runs="--write-runs" in sys.argv[1:]))
