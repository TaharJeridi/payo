"""The generalization datasets are frozen and their expectations are verified twice, independently of the assistant:
by a pure-Python oracle over the seed definitions and by SQL on the seeded database."""

import hashlib
import json
import uuid
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.payments.catalog import CATALOG
from app.payments.filters import PaymentFilters
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus
from app.rag.evaluation.runner import CASES_PATH, GENERALIZATION_PATH, GENERALIZATION_V2_PATH, load_dataset
from app.rag.evaluation.seed import SEED_MERCHANT_ID, SEED_PAYMENTS, SeedPayment, seed

FILES = {"generalization": GENERALIZATION_PATH, "generalization-v2": GENERALIZATION_V2_PATH}
VERSIONS = {"generalization": "1.0.0", "generalization-v2": "2.0.0"}
RAW = [c for path in FILES.values() for c in json.loads(path.read_text(encoding="utf-8"))["cases"]]
V1 = json.loads(GENERALIZATION_PATH.read_text(encoding="utf-8"))["cases"]
MAIN = [p for p in SEED_PAYMENTS if p.tenant == "main"]


def amount_of(payment: SeedPayment) -> int:
    return CATALOG[payment.product_id].unit_amount * payment.quantity


def recovery(payment: SeedPayment) -> str | None:
    lost = False
    for step in payment.steps:
        if step.to_status is PaymentStatus.CREATION_UNKNOWN:
            lost = True
        elif lost and step.to_status in (PaymentStatus.OPEN, PaymentStatus.PAID):
            return step.source
    return None


def paid_after_expiry(payment: SeedPayment) -> bool:
    statuses = [s.to_status for s in payment.steps]
    return any(
        a is PaymentStatus.EXPIRED and b is PaymentStatus.PAID for a, b in zip(statuses, statuses[1:], strict=False)
    )


def oracle(raw: dict) -> list[SeedPayment]:
    f = PaymentFilters.model_validate(raw)
    out = []
    for p in MAIN:
        a = amount_of(p)
        checks = [
            not f.statuses or p.status in f.statuses,
            not f.product_ids or p.product_id in f.product_ids,
            f.quantity is None or p.quantity == f.quantity,
            f.currency is None or CATALOG[p.product_id].currency == f.currency,
            f.created_from is None or p.created_at >= f.created_from,
            f.created_to is None or p.created_at < f.created_to,
            f.recovered is None or (recovery(p) is not None) == f.recovered,
            f.recovery_source is None or recovery(p) == f.recovery_source,
            f.paid_after_expiry is None or paid_after_expiry(p) == f.paid_after_expiry,
        ]
        if f.amount is not None:
            r = f.amount
            if r.minimum is not None:
                checks.append(a >= r.minimum if r.minimum_inclusive else a > r.minimum)
            if r.maximum is not None:
                checks.append(a <= r.maximum if r.maximum_inclusive else a < r.maximum)
        if all(checks):
            out.append(p)
    return out


def euro(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}".replace(".", ",")


@pytest.mark.parametrize("name", FILES)
def test_dataset_is_frozen(name):
    path = FILES[name]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_suffix(".sha256").read_text().split()[0]


@pytest.mark.parametrize("name", FILES)
def test_dataset_shape(name):
    dataset = load_dataset(name)
    assert dataset.name == name and dataset.version == VERSIONS[name]
    assert len(dataset.cases) >= 100
    assert len({c.group for c in dataset.cases}) >= 15
    assert {c.language for c in dataset.cases} == {"it", "en"}


def test_no_question_is_copied_from_an_earlier_set():
    normalize = lambda q: " ".join(q.lower().split()).strip(" ?.!")  # noqa: E731
    historical = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(GENERALIZATION_V2_PATH.read_text(encoding="utf-8"))["cases"]
    earlier = {normalize(c["question"]) for c in [*historical, *V1]}
    assert not [c["id"] for c in V1 if normalize(c["question"]) in {normalize(h["question"]) for h in historical}]
    assert not [c["id"] for c in v2 if normalize(c["question"]) in earlier]
    ids = [c["id"] for c in [*historical, *RAW]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("raw", [c for c in RAW if "truth_filters" in c], ids=lambda c: c["id"])
def test_expected_payments_match_the_oracle(raw):
    expected = sorted(raw["expect_payments"])
    assert sorted(p.ref for p in oracle(raw["truth_filters"])) == expected
    assert len(expected) <= 8, "lookups return at most 8 payments"


@pytest.mark.parametrize(
    "raw", [c for c in RAW if "expect_count_filters" in c or "expect_total_filters" in c], ids=lambda c: c["id"]
)
def test_expected_aggregates_match_the_oracle(raw):
    if "expect_count_filters" in raw:
        assert str(len(oracle(raw["expect_count_filters"]))) == raw["truth_value"]
    else:
        matched = oracle(raw["expect_total_filters"])
        assert euro(sum(amount_of(p) for p in matched)) == raw["truth_value"]


def test_top_product_expectations_match_the_oracle():
    paid = [p for p in MAIN if p.status is PaymentStatus.PAID]
    revenue: dict[str, int] = {}
    units: dict[str, int] = {}
    for p in paid:
        revenue[p.product_id] = revenue.get(p.product_id, 0) + amount_of(p)
        units[p.product_id] = units.get(p.product_id, 0) + p.quantity
    assert max(revenue, key=revenue.get) == "payflow-report" and euro(revenue["payflow-report"]) == "196,00"
    assert max(units, key=units.get) == "payflow-starter" and units["payflow-starter"] == 7


def test_sql_agrees_with_the_oracle():
    with get_session_factory()() as session, session.begin():
        seed(session)
    with get_session_factory()() as session:
        repo = PaymentRepository(session)
        for raw in RAW:
            for key in ("truth_filters", "expect_count_filters", "expect_total_filters"):
                if key not in raw:
                    continue
                filters = PaymentFilters.model_validate(raw[key])
                found, total = repo.find(SEED_MERCHANT_ID, filters, limit=100)
                assert sorted(str(p.id) for p in found) == sorted(str(p.id) for p in oracle(raw[key])), raw["id"]
                assert total == len(found), raw["id"]


def test_literal_ids_point_where_the_notes_say():
    upper = next(c for c in RAW if c["id"] == "g-sim-id-upper")["question"].split()[-1]
    typo = next(c for c in RAW if c["id"] == "g-sim-id-typo")["question"].split()[-1].rstrip("?")
    ids = {str(p.id) for p in SEED_PAYMENTS}
    assert upper.lower() in ids
    assert typo not in ids and uuid.UUID(typo)
