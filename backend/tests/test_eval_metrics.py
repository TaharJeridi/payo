import json
from pathlib import Path

import pytest

from app.rag.evaluation.metrics import NOT_APPLICABLE, case_errors, case_metrics, p95, reliability_summary
from app.rag.evaluation.report import load_run, render_report
from app.rag.evaluation.runner import CASES_PATH, Case, load_dataset

DOC = Case(id="doc", category="docs_it", question="Come?", expect_sources=("doc:a.md",), expect_answer=("x",))
PAY = Case(id="pay", category="sql_lookup", question="Trova", expect_payments=("pro3_paid",), expect_answer=("89",))
NO = Case(id="no", category="out_of_domain", question="Capitale?", expect_refusal=True)
CASES = {c.id: c for c in (DOC, PAY, NO)}


def result(case_id: str, checks: dict, *, answer: str = "Risposta in italiano per il pagamento.", **extra) -> dict:
    case = CASES[case_id]
    row = {"id": case_id, "category": case.category, "question": case.question, "answer": answer, "checks": checks}
    row["passed"] = all(checks.values())
    return row | extra


def test_metrics_that_a_case_does_not_exercise_are_not_applicable_not_passed():
    metrics = case_metrics(result("no", {"refused": True, "refusal_language": True}), NO)
    assert metrics["correct_abstention"] == "pass"
    for name in ("financial_fact", "completeness", "citation", "retrieval", "payment_identification", "isolation"):
        assert metrics[name] == NOT_APPLICABLE


def test_answer_checks_count_as_financial_or_completeness_by_category():
    assert case_metrics(result("pay", {"answer": False}), PAY)["financial_fact"] == "fail"
    assert case_metrics(result("pay", {"answer": False}), PAY)["completeness"] == NOT_APPLICABLE
    assert case_metrics(result("doc", {"answer": False}), DOC)["completeness"] == "fail"


def test_errors_separate_retrieval_from_generation():
    bad_retrieval = result("pay", {"payments": False, "answer": False, "not_refused": True})
    assert case_errors(bad_retrieval, PAY) == ["retrieval", "financial_fact"]
    bad_generation = result("doc", {"retrieved": True, "answer": False, "cited_expected": False, "not_refused": True})
    assert case_errors(bad_generation, DOC) == ["generation", "completeness", "citation"]
    wrong_language = result("doc", {"retrieved": True, "answer": True}, answer="The keys are hashed with the sha256.")
    assert case_errors(wrong_language, DOC) == ["language"]
    misparsed = result("pay", {"payments": True}, diagnostics={"intent": True, "filters": False})
    assert case_errors(misparsed, PAY) == ["parser"]


def test_p95_uses_the_documented_index():
    assert p95([]) == 0
    assert p95(list(range(1, 51))) == 47
    assert p95([5]) == 5


def test_summary_rates_only_count_applicable_cases():
    rows = [
        result("doc", {"retrieved": True, "answer": True, "not_refused": True}, used_llm=True, latency_ms=100),
        result("no", {"refused": False}, used_llm=False, latency_ms=10),
    ]
    summary = reliability_summary(rows, CASES)
    assert summary["metrics"]["correct_abstention"] == {"passed": 0, "applicable": 1, "not_applicable": 1, "rate": 0.0}
    assert summary["metrics"]["financial_fact"]["rate"] is None
    assert summary["false_refusal_rate"] == 0.0
    assert summary["errors"]["abstention"] == ["no"]
    assert summary["llm_calls"] == 1
    assert summary["latency_ms"] == {"mean": 55, "p95": 10, "max": 100}


def write_run(tmp_path: Path, name: str, failing: set[str], warnings: bool = True) -> Path:
    cases = load_dataset("historical").cases
    rows = []
    for case in cases:
        ok = case.id not in failing
        row = {
            "id": case.id,
            "category": case.category,
            "question": case.question,
            "answer": "Risposta del pagamento." if ok else "No.",
            "checks": {"answer": ok},
            "passed": ok,
            "used_llm": True,
            "latency_ms": 1000,
        }
        if warnings:
            row["warnings"] = ["uncited"] if case.id == "agg-open" else []
        rows.append(row)
    path = tmp_path / f"rag_eval_phase_{name}.json"
    path.write_text(json.dumps({"settings": {"chat_model": "qwen", "dataset_sha256": None}, "results": rows}))
    return path


def test_report_finds_stable_variable_and_new_failures(tmp_path):
    dataset = load_dataset("historical")
    runs = [
        load_run(
            write_run(tmp_path, f"now-{n}", {"id-open", "ood-k8s", "agg-paid"} if n < 3 else {"id-open", "ood-k8s"})
        )
        for n in (1, 2, 3)
    ]
    baseline = [
        load_run(write_run(tmp_path, f"before-{n}", {"ood-poem", "ood-k8s"}, warnings=False)) for n in (1, 2, 3)
    ]
    report = render_report(dataset, runs, baseline, code_version="abc123")
    assert "- **Stable** (all 3 runs): `id-open`, `ood-k8s`" in report
    assert "`agg-paid` (2/3)" in report
    assert "passed in every baseline run): `agg-paid`, `id-open`\n" in report
    assert "passes now): `ood-poem`" in report
    assert "| Code version | abc123 |" in report
    assert "| Warning `uncited` | 1 | 1 | 1 | not recorded / not recorded / not recorded |" in report
    assert f"`{dataset.sha256}`" in report


def test_report_reads_runs_saved_before_the_metrics_existed():
    # Saved real runs are kept locally in the repository root and are not committed (see docs/quality/evaluation.md).
    saved = Path(__file__).resolve().parents[2] / "rag_eval_phase_validated-1.json"
    if not saved.is_file():
        pytest.skip("saved real runs are not available here (not committed)")
    report = render_report(load_dataset("historical"), [load_run(saved)])
    assert "| financial_fact | 24/24 |" in report


def test_both_dataset_formats_load_with_metadata():
    historical = load_dataset("historical")
    assert historical.name == "historical" and len(historical.cases) == 50
    assert historical.version == "unversioned"
    assert load_dataset(CASES_PATH).sha256 == historical.sha256
    general = load_dataset("generalization")
    assert general.name == "generalization" and general.version == "1.0.0"
    assert {c.language for c in historical.cases} == {"it", "en"}


def test_report_command_needs_no_database(tmp_path, monkeypatch, capsys):
    from app.rag.evaluation.__main__ import main

    monkeypatch.setenv("DATABASE_URL", "not-a-url")
    path = write_run(tmp_path, "solo-1", set())
    assert main(["report", "--dataset", "historical", "--runs", str(path)]) == 0
    assert "# Evaluation report: historical" in capsys.readouterr().out
