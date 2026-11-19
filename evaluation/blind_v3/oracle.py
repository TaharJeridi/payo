"""Independent golden answers for the blind v3 dataset.

Two implementations compute every structured expectation from the fixtures:
- SQL written here against the ``payments`` and ``payment_events`` tables of the seeded database;
- Python over the seed definitions (``app/rag/evaluation/seed.py``).
Neither uses the query parser, the repository or the assistant. Usage, from ``backend/``:
    DATABASE_URL=... python ../evaluation/blind_v3/oracle.py <questions.json> <report.json>
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from app.payments.catalog import CATALOG
from app.payments.state import PaymentStatus
from app.rag.evaluation.seed import SEED_MERCHANT_ID, SEED_PAYMENTS

MAIN = [p for p in SEED_PAYMENTS if p.tenant == "main"]


def cents(value: object) -> int | None:
    return None if value is None else int((Decimal(str(value)) * 100).to_integral_value())


def day(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value).replace(tzinfo=UTC)


def python_rows() -> list[dict]:
    rows = []
    for p in MAIN:
        at = p.created_at
        history = []
        for step in p.steps:
            at = at + step.after
            history.append((step.to_status.value, step.source, at))
        statuses = [h[0] for h in history]
        recovery = None
        lost = False
        for status, source, _ in history:
            if status == "creation_unknown":
                lost = True
            elif lost and status in ("open", "paid"):
                recovery = source
                break
        paid = [h for h in history if h[0] == "paid"]
        rows.append(
            {
                "id": str(p.id),
                "product": p.product_id,
                "quantity": p.quantity,
                "amount": CATALOG[p.product_id].unit_amount * p.quantity,
                "status": p.status.value,
                "created_at": p.created_at,
                "paid_at": paid[-1][2] if paid else None,
                "recovery": recovery,
                "paid_after_expiry": any(
                    a == "expired" and b == "paid" for a, b in zip(statuses, statuses[1:], strict=False)
                ),
                "confirmed_by": paid[-1][1] if paid else None,
                "failure_reason": next(
                    (
                        s.reason
                        for s in reversed(p.steps)
                        if s.reason and s.to_status in (PaymentStatus.FAILED, PaymentStatus.EXPIRED)
                    ),
                    None,
                ),
                "session": p.has_session,
                "history": statuses,
            }
        )
    return rows


SQL = """
with h as (
  select payment_id, to_status::text as to_status, source, id,
         bool_or(to_status = 'creation_unknown') over (partition by payment_id order by id
             rows between unbounded preceding and 1 preceding) as lost_before,
         lag(to_status::text) over (partition by payment_id order by id) as previous
  from payment_events)
select p.id::text, p.product_id, p.quantity, p.amount, p.status::text, p.created_at, p.paid_at,
       (select h.source from h where h.payment_id = p.id and h.lost_before and h.to_status in ('open', 'paid')
        order by h.id limit 1) as recovery,
       exists (select 1 from h where h.payment_id = p.id and h.previous = 'expired' and h.to_status = 'paid'),
       (select h.source from h where h.payment_id = p.id and h.to_status = 'paid' order by h.id desc limit 1),
       p.failure_reason, p.stripe_checkout_session_id is not null,
       array(select h.to_status from h where h.payment_id = p.id order by h.id)
from payments p where p.merchant_id = %s
"""


def sql_rows() -> list[dict]:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        out = conn.execute(SQL, (SEED_MERCHANT_ID,)).fetchall()
    keys = (
        "id",
        "product",
        "quantity",
        "amount",
        "status",
        "created_at",
        "paid_at",
        "recovery",
        "paid_after_expiry",
        "confirmed_by",
        "failure_reason",
        "session",
        "history",
    )
    return [dict(zip(keys, r, strict=False)) for r in out]


def quantity_ok(value: int, wanted: object) -> bool:
    if wanted is None:
        return True
    if isinstance(wanted, dict):
        return all(
            bound is None or op(value, bound)
            for bound, op in (
                (wanted.get("eq"), lambda a, b: a == b),
                (wanted.get("gt"), lambda a, b: a > b),
                (wanted.get("min"), lambda a, b: a >= b),
                (wanted.get("lt"), lambda a, b: a < b),
                (wanted.get("max"), lambda a, b: a <= b),
            )
        )
    return value == wanted


def select(rows: list[dict], f: dict) -> list[dict]:
    a = f.get("amount_eur") or {}
    eq, lo, hi, gt, lt = (cents(a.get(k)) for k in ("eq", "min", "max", "gt", "lt"))
    names = {pid: pid for pid in CATALOG} | {CATALOG[pid].name.lower(): pid for pid in CATALOG}
    products = {names.get(str(x).lower(), str(x)) for x in f.get("products") or []}
    out = []
    for r in rows:
        checks = [
            not f.get("payment_ids") or r["id"] in f["payment_ids"],
            not products or r["product"] in products,
            not f.get("statuses") or r["status"] in f["statuses"],
            eq is None or r["amount"] == eq,
            lo is None or r["amount"] >= lo,
            hi is None or r["amount"] <= hi,
            gt is None or r["amount"] > gt,
            lt is None or r["amount"] < lt,
            quantity_ok(r["quantity"], f.get("quantity")),
            not f.get("history_includes") or bool(set(f["history_includes"]) & set(r["history"])),
            not f.get("created_weekdays") or r["created_at"].weekday() in f["created_weekdays"],
            not f.get("created_from") or r["created_at"] >= day(f["created_from"]),
            not f.get("created_before") or r["created_at"] < day(f["created_before"]),
            not f.get("paid_from") or (r["paid_at"] is not None and r["paid_at"] >= day(f["paid_from"])),
            not f.get("paid_before") or (r["paid_at"] is not None and r["paid_at"] < day(f["paid_before"])),
            f.get("recovered") is None or (r["recovery"] is not None) == f["recovered"],
            not f.get("recovery_source") or r["recovery"] == f["recovery_source"],
            f.get("paid_after_expiry") is None or r["paid_after_expiry"] == f["paid_after_expiry"],
            not f.get("confirmed_by") or r["confirmed_by"] == f["confirmed_by"],
            not f.get("failure_reasons") or r["failure_reason"] in f["failure_reasons"],
            f.get("checkout_page_created") is None or r["session"] == f["checkout_page_created"],
        ]
        if all(checks):
            out.append(r)
    return out


def measure(rows: list[dict], query: dict) -> dict:
    f = query.get("filters") or {}
    kind = query.get("measure")
    matched = select(rows, f)
    if kind == "sum_collected":
        paid = [r for r in matched if r["status"] == "paid"]
        return {"amount_cents": sum(r["amount"] for r in paid), "payment_ids": sorted(r["id"] for r in paid)}
    if kind == "sum_amount":
        return {"amount_cents": sum(r["amount"] for r in matched), "payment_ids": sorted(r["id"] for r in matched)}
    if kind == "sum_units":
        return {"units": sum(r["quantity"] for r in matched), "payment_ids": sorted(r["id"] for r in matched)}
    if kind in ("best_selling_units", "top_collected_product"):
        totals: dict[str, int] = defaultdict(int)
        for r in matched:
            if r["status"] == "paid":
                totals[r["product"]] += r["quantity"] if kind == "best_selling_units" else r["amount"]
        best = max(totals.values(), default=0)
        return {"products": sorted(CATALOG[p].name for p, v in totals.items() if v == best), "totals": dict(totals)}
    return {"payment_ids": sorted(r["id"] for r in matched), "count": len(matched)}


def compare(case: dict, expected: dict, query: dict, got: dict) -> list[str]:
    problems = []
    kind = query.get("measure")
    if kind in ("list", "single_payment", "count") and expected.get("payment_ids") is not None:
        if sorted(expected["payment_ids"]) != got["payment_ids"] and not (
            kind == "count" and not expected["payment_ids"]
        ):
            problems.append(f"payment_ids generator={sorted(expected['payment_ids'])} oracle={got['payment_ids']}")
    if kind == "count" and expected.get("count") is not None and expected["count"] != got["count"]:
        problems.append(f"count generator={expected['count']} oracle={got['count']}")
    if kind in ("sum_collected", "sum_amount") and cents(expected.get("amount_eur")) != got["amount_cents"]:
        problems.append(f"amount generator={expected.get('amount_eur')} oracle={got['amount_cents'] / 100:.2f}")
    if kind == "sum_units" and expected.get("units") != got["units"]:
        problems.append(f"units generator={expected.get('units')} oracle={got['units']}")
    if kind in ("best_selling_units", "top_collected_product") and expected.get("product") not in got["products"]:
        problems.append(f"product generator={expected.get('product')} oracle={got['products']}")
    return problems


def main(source: str, target: str) -> None:
    cases = json.loads(Path(source).read_text(encoding="utf-8"))
    overrides_path = Path(__file__).with_name("verification_queries.json")
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {}
    by_sql, by_python = sql_rows(), python_rows()
    report = []
    for case in cases:
        entries = []
        if case.get("data_query"):
            entries.append(("primary", case["data_query"], case["expected_answer"]))
        for i, interp in enumerate(case.get("interpretations") or []):
            if interp.get("data_query"):
                entries.append((f"interpretation {i + 1}", interp["data_query"], interp.get("expected_answer") or {}))
        for which, item in overrides.get(case["id"], {}).items():
            entries = [e for e in entries if e[0] != which]
            entries.append((which, item["query"], item["expected"]))
        for label, query, expected in entries:
            got_sql, got_py = measure(by_sql, query), measure(by_python, query)
            report.append(
                {
                    "id": case["id"],
                    "which": label,
                    "measure": query.get("measure"),
                    "sql": got_sql,
                    "python": got_py,
                    "oracles_agree": got_sql == got_py,
                    "problems": compare(case, expected, query, got_sql),
                }
            )
    Path(target).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    disagreements = [r for r in report if r["problems"] or not r["oracles_agree"]]
    print(f"checked {len(report)} expectations in {len(cases)} cases; disagreements {len(disagreements)}")
    print(Counter(r["measure"] for r in report))
    for r in disagreements:
        print(r["id"], r["which"], r["measure"], "agree" if r["oracles_agree"] else "ORACLES DIFFER", r["problems"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
