"""Apply the reviewer's findings to the generated questions before freezing. Every change is listed here.

Questions are never reworded. Only expectations change: alternative readings become explicit
interpretations, and pass criteria are made precise. Usage: review_resolution.py <merged.json> <reviewed.json>
"""

import copy
import json
import sys
from pathlib import Path

EMPTY = {
    "payment_ids": [],
    "products": [],
    "statuses": [],
    "amount_eur": {"eq": None, "min": None, "max": None, "gt": None, "lt": None},
    "quantity": None,
    "created_from": None,
    "created_before": None,
    "paid_from": None,
    "paid_before": None,
    "recovered": None,
    "recovery_source": None,
    "paid_after_expiry": None,
    "confirmed_by": None,
    "failure_reasons": [],
    "checkout_page_created": None,
}


def query(measure: str, **filters: object) -> dict:
    return {"measure": measure, "filters": {**copy.deepcopy(EMPTY), **filters}}


def answer(
    ids: list[str] | None = None, count: int | None = None, amount: float | None = None, facts: list[str] | None = None
) -> dict:
    return {"payment_ids": ids or [], "count": count, "amount_eur": amount, "product": None, "key_facts": facts or []}


P = {
    "P03": "c3dcc710-9742-5f8a-a48a-5c02065d5d3e",
    "P04": "ee1e4caa-c0e7-529f-806e-4fe62d29d9bf",
    "P05": "6f97abac-0cf7-5b24-ac75-ead99fb40ec8",
    "P06": "4343f610-5f37-53d9-9691-a6a60855d8f7",
    "P07": "0ffe4c86-91d9-53c5-b488-9c758a2de11d",
    "P08": "495e7eea-5125-53a6-a872-ea91fe3e8291",
    "P09": "73074148-bca3-5e91-969f-141d6491488b",
    "P10": "a807d01f-9ea7-531d-a38e-aa71369014db",
    "P13": "fb81ab87-a24f-5de6-80f5-9567db719da7",
    "P15": "7d25ddea-d6f7-5014-b252-49f1f9ac246c",
    "P16": "4ad709ca-2b89-54ab-b7f2-858d7c7a80b4",
    "P17": "68b1f78a-bd95-5ca8-bd25-8017ac984576",
    "P19": "f022dabf-46e8-5163-a7c2-87d33898a90a",
    "P20": "4838cd98-4bca-5e0b-b151-8235b47e0e85",
    "P01": "64cef59a-67fc-513e-877e-a7d47a8e3030",
    "P14": "677506ad-3644-5406-854b-b276c8e7e1e6",
    "P02": "a09af299-4928-5c76-8b7d-57384748df48",
    "P11": "371294c3-b3f9-5b4f-8495-2db0d1fec346",
}
OCT = {"created_from": "2026-10-01", "created_before": "2026-11-01"}


def ambiguous(case: dict, *readings: tuple[str, dict, dict]) -> str:
    case["expected_behavior"] = "clarify_or_interpret"
    case["interpretations"] = [{"meaning": m, "data_query": q, "expected_answer": a} for m, q, a in readings]
    return "second reading made explicit; clarify_or_interpret"


def resolve(cases: dict[str, dict]) -> list[dict]:
    log = []

    def record(cid: str, finding: str, change: str) -> None:
        log.append({"id": cid, "finding": finding, "change": change})

    c = cases["q038"]
    record(
        "q038",
        "major: 'came in' = created or paid",
        ambiguous(
            c,
            (
                "created 5-10 Oct inclusive",
                query("list", created_from="2026-10-05", created_before="2026-10-11"),
                answer([P["P03"], P["P04"], P["P05"], P["P06"], P["P07"], P["P08"]], 6),
            ),
            (
                "paid 5-10 Oct inclusive",
                query("list", statuses=["paid"], paid_from="2026-10-05", paid_before="2026-10-11"),
                answer([P["P03"], P["P05"], P["P06"], P["P08"]], 4),
            ),
        ),
    )
    multi = {"gt": 1}
    for cid, measure in (("q063", "count"), ("q035", "list")):
        c = cases[cid]
        c["data_query"]["filters"]["quantity"] = multi
        everyone = [P["P05"], P["P09"], P["P13"], P["P16"], P["P20"]]
        paid = [P["P05"], P["P09"], P["P13"]]
        record(
            cid,
            "reading 'bought' as completed purchases",
            ambiguous(
                c,
                ("quantity > 1, any status", query(measure, quantity=multi), answer(everyone, 5)),
                ("quantity > 1, paid only", query(measure, quantity=multi, statuses=["paid"]), answer(paid, 3)),
            ),
        )
    c = cases["q062"]
    record(
        "q062",
        "'did I get' = created or paid",
        ambiguous(
            c,
            (
                "Pro payments created in October, any status",
                query("count", products=["payflow-pro"], **OCT),
                answer(count=8),
            ),
            (
                "Pro payments paid in October",
                query("count", products=["payflow-pro"], statuses=["paid"], **OCT),
                answer(count=4),
            ),
        ),
    )
    c = cases["q079"]
    record(
        "q079",
        "measure was list for a sum; creation_unknown reading only in notes",
        ambiguous(
            c,
            (
                "amount of open payments",
                query("sum_amount", statuses=["open"]),
                answer([P["P17"], "633941c9-55a6-5efe-bd88-3e90e814cd5a", P["P19"]], 3, 88.8),
            ),
            (
                "open and creation_unknown",
                query("sum_amount", statuses=["open", "creation_unknown"]),
                answer([P["P17"], "633941c9-55a6-5efe-bd88-3e90e814cd5a", P["P19"], P["P20"]], 4, 148.6),
            ),
        ),
    )
    c = cases["r016"]
    record(
        "r016",
        "'never got paid' = not paid or final non-paid",
        ambiguous(
            c,
            (
                "not paid, amount < 30",
                query(
                    "list",
                    statuses=["open", "failed", "expired", "creation_unknown", "creating"],
                    amount_eur={"eq": None, "min": None, "max": None, "gt": None, "lt": 30},
                ),
                answer([P["P04"], P["P10"], P["P15"], P["P16"], P["P17"], P["P19"]], 6),
            ),
            (
                "failed or expired, amount < 30",
                query(
                    "list",
                    statuses=["failed", "expired"],
                    amount_eur={"eq": None, "min": None, "max": None, "gt": None, "lt": 30},
                ),
                answer([P["P04"], P["P10"], P["P15"], P["P16"]], 4),
            ),
        ),
    )
    c = cases["q040"]
    record(
        "q040",
        "'da 9,90' could mean the 9,90 product",
        ambiguous(
            c,
            ("amount exactly 9.90", c["data_query"], c["expected_answer"]),
            (
                "PayFlow Starter payments",
                query("list", products=["payflow-starter"]),
                answer([P["P02"], P["P11"], P["P13"], P["P15"], P["P16"], P["P19"]], 6),
            ),
        ),
    )
    c = cases["r004"]
    record(
        "r004",
        "found while building typed facts: 'metà mese' boundary changes the amount (notes only)",
        ambiguous(
            c,
            (
                "collected by product, created from 16 Oct",
                query("top_collected_product", statuses=["paid"], created_from="2026-10-16"),
                {**answer(amount=49.5), "product": "PayFlow Starter"},
            ),
            (
                "collected by product, created from 15 Oct",
                query("top_collected_product", statuses=["paid"], created_from="2026-10-15"),
                {**answer(amount=59.4), "product": "PayFlow Starter"},
            ),
        ),
    )
    cases["q082"]["accept_declared_inability"] = True
    record(
        "q082",
        "weekday filter outside the contract",
        "108,40 € or a declared inability without a number both pass; any other number fails",
    )
    cases["q091"]["expected_answer"]["key_facts"] = [
        "does not confirm Pro as best seller",
        "PayFlow Starter 7 units",
        "PayFlow Pro 6 units",
    ]
    record(
        "q091",
        "pass criterion too strict",
        "must not confirm; must give Starter 7 vs Pro 6 units; Pro leading by paid orders (4) may be added",
    )
    facts = cases["q125"]["expected_answer"]["key_facts"]
    cases["q125"]["expected_answer"]["key_facts"] = [f for f in facts if "CLI" not in f and "cli" not in f]
    record("q125", "key fact beyond the documentation", "removed the fact about issuing keys through the CLI")
    for cid, note in (
        ("q059", "count is by recorded status (3); mentioning that their checkout pages are past expiry is a plus"),
        (
            "q061",
            "count is by recorded status (3 expired); mentioning the 3 open payments past expiry is acceptable if not counted",
        ),
        ("q021", "kept: same fact as q010 asked in the other language"),
        ("q113", "kept: an operator-minded merchant question about legacy data"),
    ):
        cases[cid]["notes"] = (cases[cid]["notes"] + " " if cases[cid]["notes"] else "") + f"[review] {note}"
        record(cid, "minor", note)
    return log


def main(source: str, target: str) -> None:
    cases = {c["id"]: c for c in json.loads(Path(source).read_text(encoding="utf-8"))}
    log = resolve(cases)
    Path(target).write_text(json.dumps(list(cases.values()), indent=1, ensure_ascii=False), encoding="utf-8")
    Path(target).with_name("review_resolution_log.json").write_text(
        json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(len(log), "resolutions")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
