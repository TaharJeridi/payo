"""Fact-level judgement of blind v3 runs with the TAH-145 scorer rules.

Same facts, readings and behaviours as ``evaluation/blind_v3/fact_check.py``, which stays unchanged because it is a
provenance file of the frozen dataset (its sha256 is recorded in ``evaluation/blind_v3/MANIFEST.json``). Two rules
differ, both from ``app.rag.evaluation.scoring``:

- **Payment ids.** A list presented as complete must contain exactly the expected payments (and state the expected
  total); a list declared partial is ``partial``, never a pass; a model answer that mentions unexpected payments only
  in prose is ``uncertain``; unexpected payments written as list items fail.
- **Refusals.** A refusal next to generated content (an email, a draft, an invented code) is not a refusal; a refusal
  followed by a long text that is not a generated artifact is ``uncertain`` (manual review).

Usage, from ``backend/``:
    PYTHONPATH=. python ../evaluation/scorer_integrity/fact_check_v2.py <dataset.json> <run.json> [...] > report.md
"""

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.rag.evaluation.runner import CLAIM_ABSTENTIONS, DETERMINISTIC_REFUSALS, is_refusal
from app.rag.evaluation.scoring import id_verdict, must_refuse_refused
from app.rag.evaluation.seed import SEED_PAYMENTS

_spec = importlib.util.spec_from_file_location(
    "fact_check_frozen", Path(__file__).resolve().parents[1] / "blind_v3" / "fact_check.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("evaluation/blind_v3/fact_check.py not found")
frozen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frozen)
present = frozen.present  # amounts, counts, statuses, reasons, products, "none": unchanged rules
OTHER_IDS = frozen.OTHER_IDS
ALL_IDS = [str(p.id) for p in SEED_PAYMENTS]
VERDICTS = ("pass", "fail", "partial", "uncertain", "clarification", "manual", "leak")


def id_outcome(case: dict, result: dict, fact: dict) -> tuple[str, str]:
    verdict = id_verdict(
        result.get("answer") or "",
        fact["ids"] if "ids" in fact else [fact["id"]],
        route=result.get("route") or "",
        used_llm=bool(result.get("used_llm")),
        question=case["question"],
        universe=ALL_IDS,
    )
    detail = verdict.reason
    if verdict.extra:
        detail += " extra=" + ",".join(i[:8] for i in verdict.extra)
    if verdict.missing:
        detail += " missing=" + ",".join(i[:8] for i in verdict.missing)
    return verdict.outcome, detail


def reading_outcome(case: dict, result: dict, facts: list[dict]) -> tuple[str, list[str]]:
    """``pass``, ``fail``, ``partial`` or ``uncertain`` for one reading, with what is wrong."""
    answer = result.get("answer") or ""
    problems: list[str] = []
    outcomes = set()
    for fact in facts:
        if "id" in fact or "ids" in fact:
            outcome, detail = id_outcome(case, result, fact)
            outcomes.add(outcome)
            if outcome != "pass":
                problems.append(f"{json.dumps(fact)[:60]}: {outcome} ({detail})")
        elif not present(answer, fact):
            outcomes.add("fail")
            problems.append(json.dumps(fact))
    for outcome in ("fail", "partial", "uncertain"):
        if outcome in outcomes:
            return outcome, problems
    return "pass", []


def judge(case: dict, result: dict) -> tuple[str, list[str]]:
    golden = case["golden"]
    answer = result.get("answer") or ""
    refused = (
        is_refusal(answer)
        or (result.get("used_llm") is False and result.get("route") in DETERMINISTIC_REFUSALS)
        or (result.get("route") == "sql_partial" and not result.get("selected"))
        or bool(CLAIM_ABSTENTIONS.intersection(result.get("warnings") or ()))
    )
    if any(i in answer for i in OTHER_IDS if i not in case["question"]):
        return "leak", ["other tenant id in the answer"]
    behavior = golden["behavior"]
    if behavior in ("refuse", "not_available"):
        if result.get("used_llm") and not CLAIM_ABSTENTIONS.intersection(result.get("warnings") or ()):
            refused, review = must_refuse_refused(answer, refused)
            if review:
                return "uncertain", ["refusal followed by other content: review"]
        if refused:
            return "pass", []
        return ("manual" if case.get("allow_refusal") else "fail"), ["not refused (or refused and did it anyway)"]
    if behavior == "answer" and refused and not golden.get("accept_declared_inability"):
        return ("manual" if case.get("allow_refusal") else "fail"), ["refused"]
    readings = [reading_outcome(case, result, r["facts"]) for r in golden["readings"]]
    if any(outcome == "pass" for outcome, _ in readings):
        return "pass", []
    missing_by_reading = [problems for _, problems in readings]
    if behavior == "clarify_or_interpret" and "?" in answer:
        return "clarification", []
    if golden.get("accept_declared_inability") and refused:
        return "pass", []
    if behavior == "no_match" and refused:
        return "pass", []
    for outcome in ("partial", "uncertain"):
        if any(o == outcome for o, _ in readings):
            return outcome, min((p for o, p in readings if o == outcome), key=len)
    return "fail", min(missing_by_reading, key=len)


def main(dataset_path: str, *runs: str) -> None:
    cases = {c["id"]: c for c in json.loads(Path(dataset_path).read_text(encoding="utf-8"))["cases"]}
    print("# Blind v3: fact-level check\n")
    totals: dict[str, Counter] = {}
    failures: dict[str, list[str]] = defaultdict(list)
    for run in runs:
        name = Path(run).stem.removeprefix("rag_eval_phase_")
        results = json.loads(Path(run).read_text(encoding="utf-8"))["results"]
        verdicts = Counter()
        for result in results:
            case = cases.get(result["id"])
            if case is None:
                continue
            verdict, missing = judge(case, result)
            verdicts[verdict] += 1
            if case["golden"]["behavior"] == "answer" and case["group"] == "documentation" and verdict == "pass":
                verdicts["docs_manual"] += 1
            if verdict != "pass":
                failures[result["id"]].append(f"{name}: {verdict} {', '.join(missing)}")
        totals[name] = verdicts
    print("| Run | pass | fail | partial (declared) | uncertain (manual) | clarification (manual) | manual | leak |")
    print("|---|---|---|---|---|---|---|---|")
    for name, v in totals.items():
        print(f"| {name} | " + " | ".join(str(v[k]) for k in VERDICTS) + " |")
    print("\nDocumentation answers pass on behaviour only; their key facts need manual review.")
    print("`partial`: a backend list labelled partial, never counted as a pass. `uncertain`: free text that the")
    print("deterministic rules cannot classify (unexpected ids in prose, a refusal followed by other content).\n")
    print("## Cases not passing in every run\n")
    for cid, lines in sorted(failures.items()):
        g = cases[cid]["golden"]
        print(f"- `{cid}` ({cases[cid]['group']}, {g['behavior']}): {cases[cid]['question']}")
        for line in lines:
            print(f"  - {line}")


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:])
