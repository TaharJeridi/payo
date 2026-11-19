"""Semantic constraint coverage (ADR 0013): every word of a backend-written answer's question is accounted for."""

import re
from datetime import UTC, datetime

import pytest

from app.payments.state import PaymentStatus
from app.rag.evaluation.seed import SEED_MERCHANT_ID
from app.rag.query_parser import QueryParser
from tests.test_evidence_consistency import kit  # noqa: F401

PARSER = QueryParser(lambda: datetime(2026, 11, 18, tzinfo=UTC))
FIGURE = re.compile(r"\d+[,.]\d\d|\b\d+\s+pagament|\b\d+\s+payment")


def plan(question: str):
    return PARSER.parse(question)


@pytest.mark.parametrize(
    "question",
    [
        "Mi puoi mostrare gentilmente i pagamenti falliti?",
        "ciao, per favore dimmi quanti pagamenti sono scaduti",
        "Could you please show me the expired payments?",
        "hey quick question: how many payments failed?",
        "Quanto ho incassato a ottobre 2026?",
        "Total collected for PayFlow Starter?",
        "What was my gross revenue in October 2026?",
        "Quali pagamenti costano meno di 20 euro?",
        "Pagamenti con importo compreso tra 40 e 50 euro",
        "Quali pagamenti sono stati pagati dopo la scadenza della sessione?",
        "Ci sono pagamenti recuperati tramite riconciliazione dopo che la risposta di Stripe era andata persa?",
        "Quanti pagamenti hanno un esito di creazione sconosciuto?",
        "c'è roba scaduta?",
        "Com'è andato il pagamento da 29,90 €?",
        "How many payments are currently sitting in creation_unknown?",
        "Quanto vale la somma dei pagamenti scaduti?",
        "quante vendite ho chiuso in tutto?",
        "Ci sono pagamenti in attesa che il cliente paghi?",
    ],
)
def test_conversational_and_restating_words_do_not_withhold_an_answer(question):
    assert plan(question).completeness == "complete", plan(question).unresolved


@pytest.mark.parametrize(
    ("question", "term"),
    [
        ("how many Pro payments did I receive in October 2026?", "pro"),
        ("Quanti pagamenti pagati sono stati confermati dal webhook?", "webhook"),
        ("Quanto ho incassato dai pagamenti confermati tramite riconciliazione?", "confermati"),
        ("Quanto ho incassato dagli ordini con più di due pezzi?", "pezzi"),
        ("How much did I collect from orders placed by repeat buyers?", "repeat"),
        ("Totale incassato dai pagamenti con la carta Visa", "visa"),
        ("quanti pagamenti falliti per mancanza di fondi?", "mancanza"),
    ],
)
def test_an_unknown_constraint_makes_the_plan_incomplete(question, term):
    p = plan(question)
    assert p.completeness == "incomplete"
    assert any(term in t for t in p.unresolved)


@pytest.mark.parametrize(
    ("question", "product"),
    [
        ("Incassato totale escluso il PayFlow Starter", "payflow-starter"),
        ("total collected excluding the Financial Report Pack", "payflow-report"),
        ("quanti pagamenti tranne quelli del piano Pro?", "payflow-pro"),
    ],
)
def test_an_excluded_product_is_never_applied_as_a_filter(question, product):
    p = plan(question)
    assert product not in p.filters.product_ids
    assert p.completeness == "incomplete"
    assert any(" … " in t for t in p.unresolved)


@pytest.mark.parametrize(
    ("question", "status"),
    [
        ("show me the Starter payments that never got paid", PaymentStatus.PAID),
        ("elenca i pagamenti non scaduti", PaymentStatus.EXPIRED),
        ("which Pro payments didn't go through?", PaymentStatus.PAID),
    ],
)
def test_a_negated_status_is_never_applied_as_a_filter(question, status):
    p = plan(question)
    assert status not in p.filters.statuses and status not in p.asked_statuses
    assert p.completeness == "incomplete"


@pytest.mark.parametrize(
    ("question", "minimum", "maximum"),
    [
        ("pagamenti di importo superiore a 30 euro", 3000, None),
        ("pagamenti di importo inferiore a 30 euro", None, 3000),
        ("pagamenti maggiore di 30 euro", 3000, None),
    ],
)
def test_singular_comparators_are_bounds_not_exact_amounts(question, minimum, maximum):
    amount = plan(question).filters.amount
    assert (amount.minimum, amount.maximum) == (minimum, maximum)
    assert plan(question).complete


@pytest.mark.parametrize(
    ("question", "word"),
    [
        ("Show me the big payments", "big"),
        ("elenca i pagamenti importanti", "importanti"),
        ("ci sono pagamenti strani questo mese?", "strani"),
        ("how many large payments did I get?", "large"),
    ],
)
def test_vague_qualifiers_make_the_plan_ambiguous(question, word):
    p = plan(question)
    assert p.completeness == "ambiguous" and word in p.ambiguous


@pytest.mark.parametrize(
    ("question", "word"),
    [
        ("Quanto ho incassato nel weekend?", "weekend"),
        ("how many payments were made on Saturdays?", "saturdays"),
        ("Quanti pagamenti ho ricevuto di sera?", "sera"),
    ],
)
def test_constraints_the_data_cannot_filter_on_are_unsupported(question, word):
    p = plan(question)
    assert p.completeness == "unsupported" and word in p.unsupported_constraints


def test_explanations_about_payments_keep_the_model_answer():
    p = plan("Perché è fallito il pagamento PayFlow Pro dopo il pagamento asincrono?")
    assert not p.listing and not p.ambiguous and p.unresolved == ()


def test_recognised_constraints_are_recorded_with_their_source_text():
    p = plan("Quanti pagamenti PayFlow Pro scaduti ci sono a ottobre 2026?")
    kinds = {kind for kind, _ in p.constraints}
    assert {"product", "status", "date", "intent", "subject"} <= kinds
    assert ("product", "payflow pro") in p.constraints


def test_completeness_states():
    assert plan("Quanti pagamenti sono falliti?").completeness == "complete"
    assert plan("Quanti pagamenti sono falliti per colpa della banca?").completeness == "incomplete"
    assert plan("Quanti pagamenti grandi ho?").completeness == "ambiguous"
    assert plan("Quanti pagamenti ho ricevuto di domenica?").completeness == "unsupported"
    assert plan("Quanti rimborsi ho fatto?").completeness == "unsupported"


@pytest.mark.parametrize(
    "question",
    [
        "how many Pro payments did I receive in October 2026?",
        "Quanto ho incassato dai pagamenti confermati tramite riconciliazione?",
        "Incassato totale escluso il PayFlow Starter",
        "Quanto ho incassato dagli ordini con più di due pezzi?",
    ],
)
def test_an_incomplete_aggregate_states_no_figure(kit, question):  # noqa: F811
    service, chat = kit
    answer = service.ask(SEED_MERCHANT_ID, question)
    assert answer.used_llm is False and chat.calls == []
    assert not FIGURE.search(answer.answer)
    assert answer.answer.startswith(("Non calcolo", "I am not computing"))


def test_an_incomplete_list_is_never_an_absence_claim(kit):  # noqa: F811
    service, _ = kit
    answer = service.ask(SEED_MERCHANT_ID, "pagamenti scaduti pagati con bonifico bancario")
    assert "non è conclusiva" in answer.answer or "Ho interpretato solo una parte" in answer.answer
    assert not answer.answer.startswith("Non ho trovato pagamenti con questi criteri")


def test_an_excluded_product_lists_a_labelled_superset_never_the_opposite_set(kit):  # noqa: F811
    service, _ = kit
    answer = service.ask(SEED_MERCHANT_ID, "pagamenti falliti tranne il Financial Report Pack")
    assert answer.answer.startswith("Ho interpretato solo una parte")
    assert "PayFlow Pro" in answer.answer or "PayFlow Starter" in answer.answer


@pytest.mark.parametrize(
    ("question", "route", "start"),
    [
        ("Show me the big payments", "clarify", "I am not computing or listing"),
        ("Quanti pagamenti grandi ho avuto?", "clarify", "Non calcolo né elenco"),
        ("Quanto ho incassato nel weekend?", "unsupported_constraint", "PayFlow non può filtrare"),
    ],
)
def test_ambiguous_and_unsupported_questions_run_no_query(kit, monkeypatch, question, route, start):  # noqa: F811
    service, chat = kit
    from app.payments.repository import PaymentRepository

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no SQL may run for an ambiguous or unsupported plan")

    for method in ("find", "count_by_status", "amount_totals", "product_totals"):
        monkeypatch.setattr(PaymentRepository, method, forbidden)
    retrieval = service.retrieve(SEED_MERCHANT_ID, question)
    answer = service.answer(retrieval)
    assert retrieval.route == route and answer.used_llm is False and chat.calls == []
    assert answer.answer.startswith(start) and not FIGURE.search(answer.answer)


@pytest.mark.parametrize(
    ("question", "fragment"),
    [
        ("Quanti pagamenti sono falliti?", "3 pagamenti falliti"),
        ("Total collected for PayFlow Pro?", "179,40"),
        ("pagamenti scaduti di importo superiore a 20 euro", "Ho trovato 2 pagamenti"),
        ("quante vendite ho chiuso in tutto?", "10 pagamenti"),
        ("Quali pagamenti costano meno di 10 euro?", "Ho trovato 4 pagamenti"),
    ],
)
def test_complete_questions_still_get_their_figure(kit, question, fragment):  # noqa: F811
    service, _ = kit
    assert fragment in service.ask(SEED_MERCHANT_ID, question).answer


def test_a_complete_query_with_no_match_still_says_none_were_found(kit):  # noqa: F811
    service, _ = kit
    answer = service.ask(SEED_MERCHANT_ID, "Ci sono pagamenti da 123,45 €?")
    assert answer.answer.startswith("Non ho trovato pagamenti con questi criteri")


def test_counts_over_an_unrecognised_noun_are_refused_without_a_figure(kit):  # noqa: F811
    service, _ = kit
    retrieval = service.retrieve(SEED_MERCHANT_ID, "how many Pro orders did I receive in October 2026?")
    assert retrieval.route == "unrecognized_payment_set"
    assert not FIGURE.search(service.answer(retrieval).answer)


@pytest.mark.parametrize("question", ["elenca i pagamenti lordi", "show me the payments worth a look"])
def test_words_that_describe_a_total_count_only_for_a_total(question):
    p = plan(question)
    assert p.intent == "find" and p.completeness == "incomplete"


@pytest.mark.parametrize("question", ["How much haven't I collected yet?", "Show me the payments I haven't collected"])
def test_contracted_negations_are_never_read_as_the_affirmative(question):
    p = plan(question)
    assert p.completeness == "incomplete"
    assert any("n't" in t for t in p.unresolved)


def test_the_evaluation_counts_a_clarification_as_a_non_answer(kit):  # noqa: F811
    from app.rag.evaluation.runner import Case, Evaluator, GroundTruth

    service, _ = kit
    evaluator = Evaluator(service, SEED_MERCHANT_ID, GroundTruth({}, {}), generate=True)
    result = evaluator.evaluate(Case(id="c", category="sql_lookup", question="Show me the big payments"))
    assert result.route == "clarify" and result.checks["not_refused"] is False
