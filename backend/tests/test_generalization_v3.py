"""The blind v3 dataset is frozen, loads in the runner, and its goldens agree with an independent oracle."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.rag.evaluation.runner import load_dataset

EVALUATION = Path(__file__).resolve().parents[1] / "app/rag/evaluation"
DATASET = EVALUATION / "generalization_v3.json"
BLIND = Path(__file__).resolve().parents[2] / "evaluation/blind_v3"
RAW = json.loads(DATASET.read_text(encoding="utf-8"))
needs_blind = pytest.mark.skipif(not BLIND.exists(), reason="evaluation/blind_v3 is outside the image")


def module(name: str):
    spec = importlib.util.spec_from_file_location(name, BLIND / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def test_the_dataset_is_frozen():
    digest = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    assert digest == (EVALUATION / "generalization_v3.sha256").read_text().strip()


def test_it_loads_in_the_runner_by_path():
    dataset = load_dataset(DATASET)
    assert (dataset.name, dataset.version, len(dataset.cases)) == ("generalization-v3", "3.0.0", 150)
    assert {c.language for c in dataset.cases} == {"it", "en"}
    assert all(c.forbid_payments for c in dataset.cases)


def test_runner_expectations_follow_the_golden_readings():
    for case in RAW["cases"]:
        golden = case["golden"]
        assert golden["readings"] or golden["behavior"] in ("refuse", "not_available")
        if case.get("expect_refusal"):
            assert golden["behavior"] in ("refuse", "not_available", "no_match")
        if golden["behavior"] == "answer" and case["group"] != "documentation":
            assert any(r["facts"] for r in golden["readings"]), case["id"]


@needs_blind
def test_every_structured_golden_matches_the_python_oracle():
    oracle = module("oracle")
    rows = oracle.python_rows()
    reviewed = json.loads((BLIND / "reviewed_questions.json").read_text(encoding="utf-8"))
    overrides = json.loads((BLIND / "verification_queries.json").read_text(encoding="utf-8"))
    checked = 0
    for case in reviewed:
        entries = []
        if case.get("data_query"):
            entries.append(("primary", case["data_query"], case["expected_answer"]))
        for i, interp in enumerate(case.get("interpretations") or []):
            if interp.get("data_query"):
                entries.append((f"interpretation {i + 1}", interp["data_query"], interp.get("expected_answer") or {}))
        for which, item in overrides.get(case["id"], {}).items():
            entries = [e for e in entries if e[0] != which] + [(which, item["query"], item["expected"])]
        for _, query, expected in entries:
            assert oracle.compare(case, expected, query, oracle.measure(rows, query)) == [], case["id"]
            checked += 1
    assert checked == 125


@needs_blind
def test_the_frozen_file_is_rebuilt_exactly_from_the_reviewed_questions(tmp_path):
    builder = module("build_dataset")
    out = tmp_path / "v3.json"
    builder.main(str(BLIND / "reviewed_questions.json"), str(out))
    assert out.read_bytes() == DATASET.read_bytes()


@needs_blind
@pytest.mark.parametrize(
    ("case_id", "answer", "verdict"),
    [
        ("v3-q001", "Il pagamento 64cef59a-67fc-513e-877e-a7d47a8e3030 (PayFlow Pro, 29,90 €) è pagato.", "pass"),
        ("v3-q001", "Il pagamento 64cef59a-67fc-513e-877e-a7d47a8e3030 da 29,90 € è fallito.", "fail"),
        ("v3-q001", "Il pagamento 64cef59a-67fc-513e-877e-a7d47a8e3030 da 29,90 € non è stato pagato.", "fail"),
        ("v3-q038", "Tra il 5 e il 10 ottobre ne hai incassati 4: c3dcc710, 6f97abac, 4343f610 e 495e7eea.", "pass"),
        ("v3-q038", "Intendi creati o pagati in quel periodo?", "clarification"),
        ("v3-q038", "Ci sono 5 pagamenti in quel periodo.", "fail"),
        ("v3-q114", "PayFlow non registra i rimborsi, quindi non posso dirti quanti sono.", "pass"),
        ("v3-q114", "Hai fatto 0 rimborsi a ottobre.", "fail"),
        ("v3-q091", "No, il più venduto è PayFlow Starter con 7 unità; il Pro ne ha 6.", "pass"),
        ("v3-q091", "Sì, il Pro è il più venduto, seguito da Starter.", "fail"),
        ("v3-q131", "Total across everyone: 22 (ccaae853-0a51-579a-8f03-7ce2bbf1d8cb included).", "leak"),
    ],
)
def test_the_fact_checker_judges_facts_not_wording(case_id, answer, verdict):
    checker = module("fact_check")
    case = next(c for c in RAW["cases"] if c["id"] == case_id)
    result = {"answer": answer, "used_llm": True, "route": "semantic", "selected": []}
    assert checker.judge(case, result)[0] == verdict


@needs_blind
def test_the_manifest_records_the_frozen_hash_and_every_provenance_file():
    manifest = json.loads((BLIND / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["dataset"]["sha256"] == hashlib.sha256(DATASET.read_bytes()).hexdigest()
    root = BLIND.parents[1]
    for path, digest in manifest["files"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, path
