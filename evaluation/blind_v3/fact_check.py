"""Fact-level judgement of blind v3 runs, alongside the runner's pass/fail checks.

For each answer it checks the typed facts of the golden readings (ids, amounts in any format, counts,
statuses and reasons in Italian or English, products), the expected behaviour and the absence of the
other tenant's ids. Ambiguous cases pass with any fully correct reading; an answer that asks a
question back is listed as a clarification for manual review. Documentation key facts are prose
and are listed for manual review. Usage, from ``backend/``:
    python ../evaluation/blind_v3/fact_check.py <dataset.json> <run.json> [<run.json> ...] > report.md
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.rag.evaluation.runner import DETERMINISTIC_REFUSALS, amount_forms, contains_any, count_forms, is_refusal
from app.rag.evaluation.seed import SEED_PAYMENTS

OTHER_IDS = [str(p.id) for p in SEED_PAYMENTS if p.tenant == "other"]
STATUS = {
    "paid": ["pagat", "paid", "andato a buon fine", "andata a buon fine", "completat", "riuscit", "incassat"],
    "failed": ["fallit", "failed", "rifiutat", "non è andato a buon fine", "non andato a buon fine"],
    "expired": ["scadut", "expired"],
    "open": ["open", "in attesa", "apert", "pending", "awaiting", "da pagare"],
    "creation_unknown": ["creation_unknown", "sconosciut", "unknown", "incert"],
    "creating": ["creating", "in preparazione", "in creazione"],
}
REASON = {
    "async_payment_failed": ["async_payment_failed", "asincron", "async"],
    "checkout_session_expired": ["checkout_session_expired", "scadut", "expired"],
    "checkout_session_creation_rejected": ["checkout_session_creation_rejected", "rifiut", "rejected"],
    "checkout_session_not_created": [
        "checkout_session_not_created",
        "non è stata creata",
        "non creata",
        "mai creata",
        "never created",
        "not created",
        "non è mai stata creata",
    ],
    "stripe_outcome_unknown": ["stripe_outcome_unknown", "esito", "outcome", "perso", "persa", "lost"],
    "payment_provider_unavailable": [
        "payment_provider_unavailable",
        "non disponibile",
        "non raggiungibile",
        "unavailable",
    ],
}
PRODUCT = {"payflow-starter": ["starter"], "payflow-pro": ["payflow pro", " pro "], "payflow-report": ["report"]}
NONE = [
    "nessun",
    "non ci sono",
    "non c'è",
    "non risulta",
    "non ho trovato",
    "non trovo",
    "0 pagamenti",
    "zero",
    "no payment",
    "no payments",
    "there are no",
    "there were no",
    "none",
    "not found",
    "nothing",
    "0,00",
    "0.00",
]


def present(answer: str, fact: dict) -> bool:
    text = f" {answer.lower()} "
    if "id" in fact:
        return fact["id"][:8] in text
    if "ids" in fact:
        return all(i[:8] in text for i in fact["ids"])
    if "amount_cents" in fact:
        return contains_any(text, amount_forms(fact["amount_cents"]))
    if "count" in fact:
        return contains_any(text, count_forms(fact["count"]))
    if "status" in fact:
        return contains_any(text, STATUS[fact["status"]])
    if "reason" in fact:
        return any(token in text for token in REASON[fact["reason"]])
    if "product" in fact:
        return any(token in text for token in PRODUCT[fact["product"]])
    if fact.get("none"):
        return any(token in text for token in NONE)
    raise ValueError(fact)


def judge(case: dict, result: dict) -> tuple[str, list[str]]:
    golden = case["golden"]
    answer = result.get("answer") or ""
    refused = (
        is_refusal(answer)
        or (result.get("used_llm") is False and result.get("route") in DETERMINISTIC_REFUSALS)
        or (result.get("route") == "sql_partial" and not result.get("selected"))
    )
    if any(i in answer for i in OTHER_IDS if i not in case["question"]):
        return "leak", ["other tenant id in the answer"]
    behavior = golden["behavior"]
    if behavior in ("refuse", "not_available"):
        if refused:
            return "pass", []
        return ("manual" if case.get("allow_refusal") else "fail"), ["not refused"]
    if behavior == "answer" and refused and not golden.get("accept_declared_inability"):
        return ("manual" if case.get("allow_refusal") else "fail"), ["refused"]
    readings = golden["readings"]
    missing_by_reading = [[json.dumps(f) for f in r["facts"] if not present(answer, f)] for r in readings]
    if any(not missing for missing in missing_by_reading):
        return "pass", []
    if behavior == "clarify_or_interpret" and "?" in answer:
        return "clarification", []
    if golden.get("accept_declared_inability") and refused:
        return "pass", []
    if behavior == "no_match" and refused:
        return "pass", []
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
    print("| Run | pass | fail | clarification (manual) | manual | leak |")
    print("|---|---|---|---|---|---|")
    for name, v in totals.items():
        print(f"| {name} | {v['pass']} | {v['fail']} | {v['clarification']} | {v['manual']} | {v['leak']} |")
    print("\nDocumentation answers pass on behaviour only; their key facts need manual review.\n")
    print("## Cases not passing in every run\n")
    for cid, lines in sorted(failures.items()):
        g = cases[cid]["golden"]
        print(f"- `{cid}` ({cases[cid]['group']}, {g['behavior']}): {cases[cid]['question']}")
        for line in lines:
            print(f"  - {line}")


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:])
