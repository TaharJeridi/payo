"""Reliability metrics computed from evaluation results.

Every metric is evaluated per case as ``pass``, ``fail`` or ``not_applicable``; rates are
computed over applicable cases only, so a case that does not exercise a metric never counts
as a success. The functions accept the result dictionaries written in the JSON report, so
runs saved before these metrics existed can be re-measured without re-running Qwen.
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.language import detect_language, reply_language

PASS, FAIL, NOT_APPLICABLE = "pass", "fail", "not_applicable"
FINANCIAL_CATEGORIES = frozenset({"payment_by_id", "payment_lookup", "sql_lookup", "aggregate"})
METRICS = (
    "intent_accuracy",
    "filter_accuracy",
    "retrieval",
    "payment_identification",
    "financial_fact",
    "completeness",
    "citation",
    "correct_abstention",
    "no_false_refusal",
    "isolation",
    "language",
)
ERROR_CLASSES = (
    "parser",
    "retrieval",
    "isolation",
    "generation",
    "abstention",
    "false_refusal",
    "financial_fact",
    "completeness",
    "citation",
    "language",
)
RETRIEVAL_CHECKS = ("retrieved", "payments")
ANSWER_CHECKS = ("answer", "answer_ids", "refused", "not_refused")


def _verdict(*values: bool | None) -> str:
    known = [v for v in values if v is not None]
    if not known:
        return NOT_APPLICABLE
    return PASS if all(known) else FAIL


def _expects_payment(case: Any) -> bool:
    return case.expect_payments is not None or any(s.startswith("payment:") for s in case.expect_sources)


def case_metrics(result: Mapping[str, Any], case: Any) -> dict[str, str]:
    checks: Mapping[str, bool] = result.get("checks", {})
    diagnostics: Mapping[str, bool] = result.get("diagnostics", {}) or {}
    answer = result.get("answer")
    financial = case.category in FINANCIAL_CATEGORIES
    language = None
    if answer:
        language = detect_language(answer) == reply_language(result.get("question", case.question))
    return {
        "intent_accuracy": _verdict(diagnostics.get("intent")),
        "filter_accuracy": _verdict(diagnostics.get("filters")),
        "retrieval": _verdict(*(checks.get(k) for k in RETRIEVAL_CHECKS)),
        "payment_identification": (
            _verdict(checks.get("payments"), checks.get("retrieved"), checks.get("answer_ids"))
            if _expects_payment(case)
            else NOT_APPLICABLE
        ),
        "financial_fact": _verdict(checks.get("answer")) if financial else NOT_APPLICABLE,
        "completeness": NOT_APPLICABLE if financial else _verdict(checks.get("answer")),
        "citation": _verdict(checks.get("cited_expected")),
        "correct_abstention": _verdict(checks.get("refused")),
        "no_false_refusal": _verdict(checks.get("not_refused")),
        "isolation": _verdict(checks.get("isolated")),
        "language": _verdict(language),
    }


def case_errors(result: Mapping[str, Any], case: Any) -> list[str]:
    checks: Mapping[str, bool] = result.get("checks", {})
    diagnostics: Mapping[str, bool] = result.get("diagnostics", {}) or {}
    metrics = case_metrics(result, case)
    errors = []
    if any(v is False for v in diagnostics.values()):
        errors.append("parser")
    if any(checks.get(k) is False for k in RETRIEVAL_CHECKS) or checks.get("no_context") is False:
        errors.append("retrieval")
    if checks.get("isolated") is False:
        errors.append("isolation")
    retrieval_ok = all(checks.get(k) is not False for k in RETRIEVAL_CHECKS)
    if retrieval_ok and any(checks.get(k) is False for k in ANSWER_CHECKS):
        errors.append("generation")
    if checks.get("refused") is False:
        errors.append("abstention")
    if checks.get("not_refused") is False:
        errors.append("false_refusal")
    for name in ("financial_fact", "completeness", "citation", "language"):
        if metrics[name] == FAIL:
            errors.append(name)
    if "language" not in errors and any(checks.get(k) is False for k in ("answer_language", "refusal_language")):
        errors.append("language")
    return errors


def p95(values: Sequence[int]) -> int:
    """Value at index floor(0.95 * (n - 1)) of the sorted values, as in earlier reports."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[math.floor(0.95 * (len(ordered) - 1))]


def _rates(per_case: Sequence[Mapping[str, str]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in METRICS:
        values = [m[name] for m in per_case]
        applicable = [v for v in values if v != NOT_APPLICABLE]
        passed = sum(v == PASS for v in applicable)
        out[name] = {
            "passed": passed,
            "applicable": len(applicable),
            "not_applicable": len(values) - len(applicable),
            "rate": round(passed / len(applicable), 3) if applicable else None,
        }
    return out


def _grouped(results: Sequence[Mapping[str, Any]], key: str, cases: Mapping[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        value = result.get(key) or getattr(cases[result["id"]], key, None)
        if value:
            groups[value].append(result)
    out = {}
    for value, items in sorted(groups.items()):
        failures = {r["id"]: case_errors(r, cases[r["id"]]) for r in items if not r.get("passed", False)}
        out[value] = {"cases": len(items), "passed": len(items) - len(failures), "failures": failures}
    return out


def reliability_summary(results: Sequence[Mapping[str, Any]], cases: Mapping[str, Any]) -> dict[str, Any]:
    per_case = [case_metrics(r, cases[r["id"]]) for r in results]
    errors: dict[str, list[str]] = {name: [] for name in ERROR_CLASSES}
    for result in results:
        for name in case_errors(result, cases[result["id"]]):
            errors[name].append(result["id"])
    latencies = [int(r.get("latency_ms", 0)) for r in results]
    answered = [r for r in results if r.get("answer") is not None]
    rates = _rates(per_case)
    refusal = rates["no_false_refusal"]
    return {
        "metrics": rates,
        "false_refusal_rate": (round(1 - refusal["rate"], 3) if refusal["rate"] is not None else None),
        "errors": errors,
        "by_category": _grouped(results, "category", cases),
        "by_group": _grouped(results, "group", cases),
        "by_language": _grouped(results, "language", cases),
        "llm_calls": sum(1 for r in results if r.get("used_llm")),
        "answers": len(answered),
        "latency_ms": {
            "mean": int(sum(latencies) / len(latencies)) if latencies else 0,
            "p95": p95(latencies),
            "max": max(latencies, default=0),
        },
    }
