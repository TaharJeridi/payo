"""Build the runner-compatible blind v3 dataset from the reviewed questions.

Questions are copied verbatim. Runner fields follow the public contract mechanically; every case
also keeps a ``golden`` block with the readings, typed facts and the reviewer-checked key facts,
used by ``fact_check.py``. Usage, from ``backend/``:
    python ../evaluation/blind_v3/build_dataset.py <reviewed.json> <out.json>
"""

import importlib.util
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from app.payments.catalog import CATALOG
from app.rag.evaluation.runner import amount_forms, count_forms
from app.rag.evaluation.seed import SEED_PAYMENTS

_spec = importlib.util.spec_from_file_location("blind_oracle", Path(__file__).with_name("oracle.py"))
oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oracle)
ROWS = oracle.python_rows()
OVERRIDES = json.loads(Path(__file__).with_name("verification_queries.json").read_text(encoding="utf-8"))
REF = {str(p.id): p.ref for p in SEED_PAYMENTS}
MAIN = {str(p.id): p for p in SEED_PAYMENTS if p.tenant == "main"}
OTHER_REFS = [p.ref for p in SEED_PAYMENTS if p.tenant == "other"]
NAME_TO_ID = {CATALOG[p].name.lower(): p for p in CATALOG} | {p: p for p in CATALOG}
PRODUCT_TOKEN = {"payflow-starter": ["starter"], "payflow-pro": ["payflow pro"], "payflow-report": ["report"]}
EITHER_WAY = {"q120", "q129", "q131", "q132", "q136"}
CATEGORY = {
    "payment_by_id": "payment_by_id",
    "payment_explanation": "payment_by_id",
    "find": "sql_lookup",
    "count": "aggregate",
    "total": "aggregate",
    "ranking": "aggregate",
    "not_recorded": "unanswerable",
    "personal_data": "unanswerable",
    "other_merchant_or_manipulation": "security",
    "out_of_scope": "out_of_domain",
}
STATUS_WORDS = ("creation_unknown", "creating", "open", "paid", "failed", "expired")
REASONS = (
    "checkout_session_expired",
    "checkout_session_creation_rejected",
    "async_payment_failed",
    "checkout_session_not_created",
    "stripe_outcome_unknown",
    "payment_provider_unavailable",
)


def cents(value: object) -> int | None:
    return None if value is None else int((Decimal(str(value)) * 100).to_integral_value())


def product_id(name: object) -> str | None:
    return None if not name else NAME_TO_ID.get(str(name).lower())


def payment_facts(pid: str, key_facts: list[str]) -> list[dict]:
    """Typed facts stated in the key facts of a single-payment case, kept only when the fixture confirms them."""
    p = MAIN[pid]
    amount = CATALOG[p.product_id].unit_amount * p.quantity
    unit = CATALOG[p.product_id].unit_amount
    history = {s.to_status.value for s in p.steps}
    reasons = {s.reason for s in p.steps if s.reason} | (
        {"payment_provider_unavailable"} if p.last_reconcile_error else set()
    )
    facts: list[dict] = []
    text = " ".join(key_facts)
    for m in re.finditer(r"(\d+)[.,](\d\d)\b", text):
        value = int(m.group(1)) * 100 + int(m.group(2))
        if value in (amount, unit):
            facts.append({"amount_cents": value})
    words = set(re.findall(r"[a-z_]+", text.lower()))
    for status in STATUS_WORDS:
        if status in words and status in history:
            facts.append({"status": status})
    for reason in REASONS:
        if reason in words and reason in reasons:
            facts.append({"reason": reason})
    unique = []
    for f in facts:
        if f not in unique:
            unique.append(f)
    return unique


def reading_facts(query: dict | None, answer: dict, behavior: str) -> list[dict]:
    if behavior == "no_match":
        return [{"none": True}]
    if not query:
        return []
    measure = query.get("measure")
    ids = answer.get("payment_ids") or []
    if measure == "count":
        return [{"count": answer["count"]}]
    if measure == "sum_units":
        return [{"count": answer.get("units", answer.get("count"))}]
    if measure in ("sum_collected", "sum_amount"):
        return [{"amount_cents": cents(answer["amount_eur"])}]
    if measure in ("best_selling_units", "top_collected_product"):
        winner = product_id(answer.get("product"))
        value = oracle.measure(ROWS, query)["totals"][winner]
        return [{"product": winner}, {"count": value} if measure == "best_selling_units" else {"amount_cents": value}]
    if measure == "list":
        return [{"ids": ids}] if 0 < len(ids) <= 8 else [{"count": len(ids)}]
    if measure == "single_payment" and ids and ids[0] in MAIN:
        return [{"id": ids[0]}, *payment_facts(ids[0], answer.get("key_facts") or [])]
    return []


def convert(c: dict) -> dict:
    behavior = c["expected_behavior"]
    query = c.get("data_query") or {}
    measure = query.get("measure")
    ids = (c.get("expected_answer") or {}).get("payment_ids") or []
    out: dict = {"id": f"v3-{c['id']}", "group": c["area"], "language": c["language"], "question": c["question"]}
    category = CATEGORY.get(c["area"], "sql_lookup")
    if c["area"] == "documentation":
        category = "docs_it" if c["language"] == "it" else "docs_en"
        out["expect_sources"] = [f"doc:architecture.md#{c['doc_section']}"]
    elif c["area"] == "ambiguous":
        category = (
            "aggregate"
            if (c["interpretations"][0]["data_query"] or {})
            .get("measure", "")
            .startswith(("count", "sum", "best", "top"))
            else "sql_lookup"
        )
    if behavior == "no_match" and measure in (None, "list", "single_payment"):
        category = "no_result"
    out["category"] = category
    out["forbid_payments"] = OTHER_REFS
    if behavior in ("refuse", "not_available") or (behavior == "no_match" and category == "no_result"):
        if c["id"] in EITHER_WAY:
            out["allow_refusal"] = True
        else:
            out["expect_refusal"] = True
        if behavior == "no_match":
            out["expect_payments"] = []
    elif behavior == "clarify_or_interpret":
        out["allow_refusal"] = True
    elif behavior == "no_match":
        out["allow_refusal"] = True
        out["expect_answer"] = amount_forms(0) + count_forms(0) if measure == "sum_collected" else count_forms(0)
    else:
        if c.get("accept_declared_inability"):
            out["allow_refusal"] = True
        if measure == "single_payment" and ids and ids[0] in MAIN:
            out["expect_sources"] = [f"payment:{REF[ids[0]]}"]
        elif measure == "list" and 0 < len(ids) <= 8 and all(i in MAIN for i in ids):
            out["expect_payments"] = sorted(REF[i] for i in ids)
        elif measure == "list" and len(ids) > 8:
            out["expect_answer"] = count_forms(len(ids))
        elif measure == "count":
            out["expect_answer"] = count_forms(c["expected_answer"]["count"])
        elif measure == "sum_collected":
            out["expect_answer"] = amount_forms(cents(c["expected_answer"]["amount_eur"]))
        elif measure in ("best_selling_units", "top_collected_product"):
            out["expect_answer"] = PRODUCT_TOKEN[product_id(c["expected_answer"]["product"])]
    readings = []
    checked = OVERRIDES.get(c["id"], {})
    if behavior == "clarify_or_interpret":
        for i, r in enumerate(c["interpretations"], start=1):
            item = checked.get(f"interpretation {i}")
            q, a = (item["query"], item["expected"]) if item else (r.get("data_query"), r.get("expected_answer") or {})
            readings.append({"meaning": r["meaning"], "facts": reading_facts(q, a, "answer")})
    else:
        item = checked.get("primary")
        q, a = (item["query"], item["expected"]) if item else (query, c.get("expected_answer") or {})
        readings.append({"meaning": c["intended_meaning"], "facts": reading_facts(q, a, behavior)})
    out["golden"] = {
        "behavior": behavior,
        "phrasing": c["phrasing"],
        "intended_meaning": c["intended_meaning"],
        "readings": readings,
        "key_facts": (c.get("expected_answer") or {}).get("key_facts") or [],
        "doc_section": c.get("doc_section"),
        "accept_declared_inability": bool(c.get("accept_declared_inability")),
        "notes": c.get("notes") or "",
        "source_id": c["id"],
    }
    return out


def main(source: str, target: str) -> None:
    reviewed = json.loads(Path(source).read_text(encoding="utf-8"))
    cases = [convert(c) for c in reviewed]
    document = {
        "name": "generalization-v3",
        "version": "3.0.0",
        "frozen_at": "2026-11-18",
        "description": (
            "Blind generalization set (TAH-140). Questions and expected answers written by an isolated agent from a neutral "
            "specification, without the parser, earlier datasets or system outputs; numeric goldens verified by independent "
            "SQL and Python oracles; reviewed by a second isolated agent. Never run before freezing."
        ),
        "seed": "app/rag/evaluation/seed.py (20 payments of the main merchant, 2 of the other tenant, October 2026, EUR)",
        "valid_run_window": "2026-11-16/2026-11-30 UTC (relative dates in some questions)",
        "provenance": "evaluation/blind_v3/MANIFEST.json",
        "cases": cases,
    }
    Path(target).write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(len(cases), "cases")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
