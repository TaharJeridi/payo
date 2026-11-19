"""TAH-144: legitimate questions that the parser could not structure.

Each rule is tested on the blind-v3 question that exposed it (now a regression case) and on
variants, positive and negative: a rule must not turn a different question into a backend answer
(TAH-142), and a constraint the plan cannot represent must still keep the answer partial.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.db.session import get_session_factory
from app.payments.catalog import CATALOG
from app.payments.state import PaymentStatus
from app.rag.evaluation.seed import SEED_MERCHANT_ID, SEED_PAYMENTS, SeedPayment, seed, seed_payment_id
from app.rag.indexer import RagIndexer
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.service import RagService
from app.rag.structured import StructuredRetriever
from tests.rag_fakes import FakeChat, HashingEmbedder

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
PARSER = QueryParser(lambda: NOW)
OTHER_IDS = {str(p.id) for p in SEED_PAYMENTS if p.tenant == "other"}
PAID = (PaymentStatus.PAID,)


def day(d: int, month: int = 10) -> datetime:
    return datetime(2026, month, d, tzinfo=UTC)


def listed(answer: str) -> set[str]:
    return set(re.findall(r"^- (?:Pagamento|Payment) ([0-9a-f-]{36}):", answer, re.MULTILINE))


def amount(payment: SeedPayment) -> int:
    return CATALOG[payment.product_id].unit_amount * payment.quantity


def seeded(cents: int) -> set[str]:
    return {str(p.id) for p in SEED_PAYMENTS if p.tenant == "main" and amount(p) == cents}


@pytest.fixture
def kit(tmp_path: Path) -> Iterator[RagService]:
    with get_session_factory()() as session, session.begin():
        seed(session)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# PayFlow\n\n## Payments\nPayments have a status.\n")
    client = QdrantClient(location=":memory:")
    embedder = HashingEmbedder()
    store = QdrantVectorStore(client, "test_unanswered")
    RagIndexer(
        get_session_factory(), embedder, store, embed_model="fake", docs_dir=docs, chunk_chars=400, overlap_chars=40
    ).run()
    yield RagService(
        embedder,
        FakeChat(),
        store,
        top_k=5,
        min_score=-1.0,
        max_question_chars=500,
        parser=PARSER,
        structured=StructuredRetriever(get_session_factory()),
    )
    client.close()


# --- amounts written without a currency (v3 q040, q055, q149) ------------------------------------


@pytest.mark.parametrize(
    ("question", "cents"),
    [
        ("Trovami tutti i pagamenti da 9,90", 990),
        ("is there a Pro payment for exactly 59.80?", 5980),
        ("What happened with the 29.90 payment?", 2990),
        ("ma quel pagamento da 59,80 alla fine com'è finito?", 5980),
    ],
)
def test_an_amount_with_two_decimals_and_no_currency_is_an_amount(question, cents):
    plan = PARSER.parse(question)
    assert plan.structured and plan.complete
    assert plan.filters.amount is not None
    assert (plan.filters.amount.minimum, plan.filters.amount.maximum) == (cents, cents)
    assert plan.filters.currency is None


@pytest.mark.parametrize(
    "question",
    [
        "Quali pagamenti sono stati creati alle 16.30?",  # a time
        "pagamenti del 05.10.2026",  # a date
        "pagamenti con sconto del 12,50%",  # a percentage
        "pagamenti da 9,9",  # one decimal: not a price
        "pagamenti da 990",  # no decimals: could be a quantity, an id fragment, a year
    ],
)
def test_numbers_that_are_not_prices_are_not_taken_as_amounts(question):
    plan = PARSER.parse(question)
    assert plan.filters.amount is None


def test_a_bare_amount_lists_every_payment_of_that_amount_and_none_of_another_merchant(kit):
    retrieval = kit.retrieve(SEED_MERCHANT_ID, "Trovami tutti i pagamenti da 9,90")
    assert retrieval.route == "sql_list"
    ids = listed(retrieval.direct_answer or "")
    assert ids == seeded(990) and len(ids) == 4
    assert not ids & OTHER_IDS


def test_a_bare_amount_with_a_product_is_a_complete_lookup(kit):
    retrieval = kit.retrieve(SEED_MERCHANT_ID, "is there a Pro payment for exactly 59.80?")
    assert retrieval.route == "sql_list"
    assert listed(retrieval.direct_answer or "") == {str(seed_payment_id("pro2_unknown"))}


def test_an_ambiguous_amount_reaches_sql_with_every_matching_payment(kit):
    """q149: six payments of 29,90 €. The evidence holds all of them, never one chosen by similarity."""
    retrieval = kit.retrieve(SEED_MERCHANT_ID, "What happened with the 29.90 payment?")
    assert retrieval.route == "sql_find"
    payments = [c.chunk for c in retrieval.selected if c.chunk.source_type == "payment"]
    evidence = {chunk.source_key.removeprefix("payment:") for chunk in payments}
    assert evidence == seeded(2990) and len(evidence) == 6


# --- amount ranges with articles (v3 r002, r012) ----------------------------------------------------


@pytest.mark.parametrize(
    "question",
    ["pagamenti tra i 20 e i 50 euro", "payments between the 20 and the 50 euro", "pagamenti fra 20 e 50 euro"],
)
def test_a_range_with_articles_is_a_range(question):
    amount = PARSER.parse(question).filters.amount
    assert amount is not None and (amount.minimum, amount.maximum) == (2000, 5000)


def test_any_status_is_an_explicit_absence_of_status_filter(kit):
    question = "fammi vedere tutti i pagamenti tra i 40 e i 60 euro, in qualsiasi stato"
    plan = PARSER.parse(question)
    assert plan.complete and plan.filters.statuses == ()
    retrieval = kit.retrieve(SEED_MERCHANT_ID, question)
    assert retrieval.route == "sql_list"
    expected = {str(p.id) for p in SEED_PAYMENTS if p.tenant == "main" and 4000 <= amount(p) <= 6000}
    assert listed(retrieval.direct_answer or "") == expected and len(expected) == 7


def test_any_status_does_not_hide_a_status_the_question_names():
    plan = PARSER.parse("pagamenti falliti in qualsiasi stato")
    assert plan.filters.statuses == (PaymentStatus.FAILED,)
    assert "qualsiasi" in plan.unresolved


# --- counting collected payments (v3 r002) ----------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "quanti pagamneti tra i 20 e i 50 euro ho effetivamente incassato?",
        "how many payments did I collect in october?",
    ],
)
def test_a_count_of_collected_payments_counts_paid_payments(question):
    plan = PARSER.parse(question)
    assert plan.intent == "count" and plan.complete
    assert plan.filters.statuses == PAID


def test_a_list_of_collected_payments_lists_paid_payments():
    plan = PARSER.parse("mostrami i pagamenti che ho incassato a ottobre")
    assert plan.intent == "find" and plan.filters.statuses == PAID


def test_a_money_verb_never_replaces_a_status_the_question_names():
    assert PARSER.parse("quanti pagamenti falliti non ho incassato?").filters.statuses == (PaymentStatus.FAILED,)


def test_a_money_verb_on_one_identified_payment_is_not_a_filter():
    """A filter would turn "did I collect it?" into "is there a paid one?", and a no-match into a false no."""
    plan = PARSER.parse(f"il pagamento {seed_payment_id('pro_expired')} è stato incassato?")
    assert plan.intent == "find" and plan.filters.statuses == ()


def test_a_count_without_a_money_verb_keeps_every_status():
    plan = PARSER.parse("quanti pagamenti tra i 20 e i 50 euro ho?")
    assert plan.intent == "count" and plan.filters.statuses == ()


def test_a_count_of_collected_payments_is_the_paid_count(kit):
    retrieval = kit.retrieve(SEED_MERCHANT_ID, "quanti pagamneti tra i 20 e i 50 euro ho effetivamente incassato?")
    assert retrieval.route == "sql_aggregate"
    paid = [
        p for p in SEED_PAYMENTS if p.tenant == "main" and 2000 <= amount(p) <= 5000 and p.status is PaymentStatus.PAID
    ]
    assert len(paid) == 6
    assert (retrieval.direct_answer or "").startswith("Hai 6 pagamenti pagati")


@pytest.mark.parametrize(
    ("question", "word"),
    [
        ("quanti pagamenti sono falitti a ottobre?", "falitti"),
        ("quanti pagamenti annullati a ottobre?", "annullati"),
        ("quanti pagamenti stornati a ottobre?", "stornati"),
        ("quanti pagamenti contestati a ottobre?", "contestati"),
        ("quanti pagamenti cancellati a ottobre?", "cancellati"),  # 0,63 from "calcolami"
        ("quanti pagamenti internazionali a ottobre?", "internazionali"),  # 0,64 from "transazioni"
    ],
)
def test_the_tolerance_for_misspelt_neutral_words_does_not_swallow_a_constraint(question, word):
    plan = PARSER.parse(question)
    assert not plan.complete
    assert any(word in term for term in plan.unresolved)


# --- periods inside a month (v3 r009, r010, q038) ---------------------------------------------------


@pytest.mark.parametrize(
    ("question", "start", "end"),
    [
        ("quanto ho incassato col Pro nelle prime due settimane di ottobre?", day(1), day(15)),
        ("nei primi 10 giorni di ottobre quali pagamenti sono falliti?", day(1), day(11)),
        ("payments in the first 3 days of october", day(1), day(4)),
        ("pagamenti delle prime 6 settimane di ottobre", day(1), day(1, 11)),  # capped at the month
        ("What payments came in between October 5 and October 10?", day(5), day(11)),
        ("pagamenti tra il 28 settembre e il 3 ottobre", day(28, 9), day(4)),
    ],
)
def test_parts_of_a_month_are_date_ranges(question, start, end):
    plan = PARSER.parse(question)
    assert plan.structured and plan.complete
    assert (plan.filters.created_from, plan.filters.created_to) == (start, end)


def test_the_first_days_of_a_month_list_only_those_payments(kit):
    question = "nei primi 10 giorni di ottobre è andato storto qualcosa? quali pagamenti sono falliti o scaduti?"
    plan = PARSER.parse(question)
    assert set(plan.filters.statuses) == {PaymentStatus.FAILED, PaymentStatus.EXPIRED}
    assert (plan.filters.created_from, plan.filters.created_to) == (day(1), day(11))


def test_a_day_range_across_the_new_year():
    plan = PARSER.parse("pagamenti tra il 28 dicembre e il 3 gennaio")
    start, end = datetime(2025, 12, 28, tzinfo=UTC), datetime(2026, 1, 4, tzinfo=UTC)
    assert (plan.filters.created_from, plan.filters.created_to) == (start, end)


def test_an_inverted_day_range_is_not_a_range():
    plan = PARSER.parse("pagamenti tra il 10 ottobre e il 5 ottobre")
    assert (plan.filters.created_from, plan.filters.created_to) != (day(10), day(6))


def test_a_total_over_the_first_two_weeks(kit):
    question = "Mi serve un dato veloce: quanto ho incassato col Pro nelle prime due settimane di ottobre?"
    retrieval = kit.retrieve(SEED_MERCHANT_ID, question)
    assert retrieval.route == "sql_aggregate"
    assert (retrieval.direct_answer or "").startswith("Hai incassato 149,50 € lordi, da 3 pagamenti")


# --- colloquial words ---------------------------------------------------------------------------------


def test_e_passato_asks_whether_a_payment_went_through():
    plan = PARSER.parse("ehi, quel pagamento da 89,70 è passato o no?")
    assert plan.intent == "find" and plan.complete
    assert plan.asked_statuses == PAID or plan.filters.statuses == PAID


@pytest.mark.parametrize("question", ["è passato un mese dal pagamento da 9,90?", "sono passati 3 giorni"])
def test_e_passato_followed_by_a_period_is_not_a_status(question):
    plan = PARSER.parse(question)
    assert PaymentStatus.PAID not in (*plan.filters.statuses, *plan.asked_statuses)


def test_a_period_that_has_passed_stays_unresolved():
    plan = PARSER.parse("quali pagamenti sono falliti da quando è passato un mese?")
    assert not plan.complete and "passato" in plan.unresolved


def test_mese_passato_is_last_month():
    plan = PARSER.parse("quali pagamenti del mese passato sono falliti?")
    assert plan.complete and (plan.filters.created_from, plan.filters.created_to) == (day(1), day(1, 11))


def test_a_pro_payment_is_the_pro_product():
    assert PARSER.parse("is there a Pro payment for exactly 59.80?").filters.product_ids == ("payflow-pro",)
    assert PARSER.parse("pros and cons of 29,90 payments").filters.product_ids == ()


# --- counts of a status, units, mid-month, relative month parts, product rankings ------------------


@pytest.mark.parametrize(
    ("question", "status"),
    [
        ("quanti pagati?", PaymentStatus.PAID),
        ("how many failed?", PaymentStatus.FAILED),
        ("number of open checkouts right now", PaymentStatus.OPEN),
    ],
)
def test_a_count_of_a_status_is_a_count(question, status):
    plan = PARSER.parse(question)
    assert plan.intent == "count" and plan.complete and plan.filters.statuses == (status,)


@pytest.mark.parametrize("question", ["Quanti minuti dura il checkout?", "how many days does a checkout last?"])
def test_a_measure_is_not_a_count_of_payments(question):
    assert PARSER.parse(question).intent != "count"


def test_how_many_times_is_still_a_count():
    plan = PARSER.parse("Quante volte un cliente ha comprato più di un pezzo nello stesso ordine?")
    assert plan.intent == "count" and not plan.complete  # a count the plan cannot represent: partial, no figure


def test_counted_paid_payments(kit):
    assert kit.retrieve(SEED_MERCHANT_ID, "quanti pagati?").direct_answer == "Hai 10 pagamenti pagati [1]."


@pytest.mark.parametrize(
    ("question", "quantity"),
    [("Any orders where someone bought 5 units?", 5), ("ordini da 3 pezzi", 3), ("paid orders with quantity 1", 1)],
)
def test_units_are_a_quantity(question, quantity):
    plan = PARSER.parse(question)
    assert plan.filters.quantity == quantity and plan.complete


@pytest.mark.parametrize("question", ["ordini con più di un pezzo", "ordini con almeno 2 pezzi"])
def test_a_bound_on_units_is_not_an_exact_quantity(question):
    plan = PARSER.parse(question)
    assert plan.filters.quantity is None


@pytest.mark.parametrize(
    "question",
    [
        "Since mid-October, has any Pro checkout actually been paid?",
        "pagamenti pagati da metà ottobre in poi",
        "payments from the middle of october",
    ],
)
def test_from_mid_month_starts_on_the_sixteenth(question):
    plan = PARSER.parse(question)
    assert plan.complete and (plan.filters.created_from, plan.filters.created_to) == (day(16), None)


def test_mid_october_paid_pro_payments(kit):
    retrieval = kit.retrieve(SEED_MERCHANT_ID, "Since mid-October, has any Pro checkout actually been paid?")
    assert retrieval.route == "sql_list"
    assert listed(retrieval.direct_answer or "") == {str(seed_payment_id(p.ref)) for p in SEED_PAYMENTS
        if p.tenant == "main" and p.product_id == "payflow-pro" and p.status is PaymentStatus.PAID
        and p.created_at >= day(16)}  # fmt: skip
    assert (retrieval.direct_answer or "").startswith("I found")


@pytest.mark.parametrize(
    ("question", "start", "end"),
    [
        ("nella seconda metà del mese scorso quanto è entrato?", day(16), day(1, 11)),
        ("pagamenti della prima settimana di questo mese", day(1, 11), day(8, 11)),
        ("payments in the first half of last month", day(1), day(16)),
    ],
)
def test_parts_of_a_relative_month(question, start, end):
    plan = PARSER.parse(question)
    assert plan.complete and (plan.filters.created_from, plan.filters.created_to) == (start, end)


def test_relative_month_parts_across_the_new_year():
    plan = QueryParser(lambda: datetime(2027, 1, 10, tzinfo=UTC)).parse("pagamenti della seconda metà del mese scorso")
    expected = (datetime(2026, 12, 16, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert (plan.filters.created_from, plan.filters.created_to) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Which product brought in the most money?",
        "Nella prima metà di ottobre qual era il prodotto che vendeva più pezzi?",
        "quale prodotto mi ha portato più soldi?",
        "qual è il prodotto con più unità vendute?",
    ],
)
def test_which_product_sells_or_earns_most_reaches_the_sql_ranking(question):
    plan = PARSER.parse(question)
    assert plan.intent == "top_product" and plan.complete


def test_a_claimed_best_seller_is_checked_against_the_full_ranking(kit):
    question = "Il Pro è il prodotto che vende di più, giusto?"
    plan = PARSER.parse(question)
    assert plan.complete and plan.filters.product_ids == () and plan.subject_product == "payflow-pro"
    assert plan.ranking
    answer = kit.retrieve(SEED_MERCHANT_ID, question).direct_answer or ""
    assert answer.startswith("Il prodotto più venduto è PayFlow Starter: 7 unità")
    assert "Classifica per unità vendute" in answer


def test_two_named_products_stay_a_filter():
    plan = PARSER.parse("il prodotto più venduto tra il piano Pro e il piano Starter")
    assert set(plan.filters.product_ids) == {"payflow-pro", "payflow-starter"} and plan.subject_product is None


@pytest.mark.parametrize(
    "question",
    [
        "Since mid-October, has any Pro checkout actually been paid?",
        "Any orders where someone bought 5 units?",
        "Should I raise the Starter price to 12.90? would I sell less?",
        "Pro checkout has failed?",
    ],
)
def test_short_english_questions_are_answered_in_english(question):
    assert PARSER.parse(question).language == "en"


@pytest.mark.parametrize(
    "question", ["Quanti pagamenti ho avuto a ottobre?", "ci sono pagamenti da 9,90 o da 19,80?", "il Pro è andato?"]
)
def test_italian_questions_stay_italian(question):
    assert PARSER.parse(question).language == "it"
