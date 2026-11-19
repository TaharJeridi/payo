import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_session_factory
from app.main import app
from app.payments.filters import AmountRange, PaymentFilters
from app.rag.dependencies import get_rag_service
from app.rag.evaluation.runner import CaseResult, Evaluator, GroundTruth, load_cases, summarize
from app.rag.evaluation.seed import SEED_MERCHANT_ID, seed, seed_payment_id
from app.rag.indexer import RagIndexer
from app.rag.ports import Chunk, ScoredChunk
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.service import Answer, RagService
from app.rag.structured import StructuredRetriever
from app.rag.validation import amounts_in, classify, validate_answer
from tests.rag_fakes import FakeChat, HashingEmbedder

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
PAYMENT = "6f97abac-0cf7-5b24-ac75-ead99fb40ec8"


def source(key: str, text: str) -> ScoredChunk:
    chunk = Chunk(uuid.uuid4(), key, key.split(":", 1)[0], key, text, "scope", 0)
    return ScoredChunk(chunk=chunk, score=1.0)


SOURCES = [
    source(f"payment:{PAYMENT}", f"Pagamento {PAYMENT}\nImporto: 89,70 € (89.70 EUR)\nCreato il: 2026-10-07T10:05:00"),
    source("doc:architecture.md", "Backoff is 60 s, capped at 6 h. The session lifetime is 2 hours."),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Importo 89,70 € e 1.234,50 EUR", {8970, 123450}),
        ("€ 12 e 98.00 eur", {1200, 9800}),
        ("creato il 2026-10-07, quantità 3, id 6f97abac-0cf7", set()),
        ("prezzo 29,90", set()),
    ],
)
def test_only_currency_marked_numbers_are_amounts(text, expected):
    assert amounts_in(text) == expected


def test_a_correct_cited_answer_has_no_warnings():
    answer = f"Il pagamento {PAYMENT} da 89,70 € è stato pagato [1]."
    assert validate_answer(answer, "Trova il pagamento da 89,70 €", SOURCES) == []


@pytest.mark.parametrize(
    ("answer", "warning"),
    [
        ("Il pagamento è stato pagato.", "uncited"),
        ("Il pagamento è stato pagato [3].", "invalid_citation"),
        (f"Il pagamento {uuid.uuid4()} è stato pagato [1].", "unknown_payment_id"),
        ("Il pagamento da 98,00 € è stato pagato [1].", "amount_not_in_sources"),
        ("L'importo è 89,70 € [2].", "amount_wrong_payment"),
        ("Il cliente è mario.rossi@example.com [1].", "personal_data"),
    ],
)
def test_each_check_flags_its_defect(answer, warning):
    assert warning in validate_answer(answer, "domanda", SOURCES)


def test_an_id_quoted_in_the_question_is_not_unknown():
    foreign = str(uuid.uuid4())
    assert validate_answer(f"Non ho trovato {foreign} [1].", f"Stato di {foreign}?", SOURCES) == []


def test_amounts_are_checked_against_all_sources_when_nothing_is_cited():
    assert validate_answer("L'importo è 89,70 €.", "domanda", SOURCES) == ["uncited"]


@pytest.fixture
def kit(tmp_path: Path) -> Iterator[tuple[RagService, FakeChat]]:
    with get_session_factory()() as session, session.begin():
        seed(session)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# PayFlow\n\n## Keys\nOnly the sha256 of the API key is stored.\n")
    client = QdrantClient(location=":memory:")
    embedder = HashingEmbedder()
    store = QdrantVectorStore(client, "test_validation")
    RagIndexer(
        get_session_factory(), embedder, store, embed_model="fake", docs_dir=docs, chunk_chars=400, overlap_chars=40
    ).run()
    chat = FakeChat()
    service = RagService(
        embedder,
        chat,
        store,
        top_k=5,
        min_score=-1.0,
        max_question_chars=500,
        parser=QueryParser(lambda: NOW),
        structured=StructuredRetriever(get_session_factory()),
    )
    yield service, chat
    client.close()


def test_service_attaches_warnings_to_model_answers_only(kit):
    service, chat = kit
    pro3 = seed_payment_id("pro3_paid")
    chat.reply = f"Il pagamento {pro3} da 89,70 € è stato pagato [1]."
    assert service.ask(SEED_MERCHANT_ID, "Cosa è successo al pagamento da 89,70 €?").warnings == []

    chat.reply = "Il pagamento da 89,70 € è stato pagato."
    assert service.ask(SEED_MERCHANT_ID, "Cosa è successo al pagamento da 89,70 €?").warnings == ["uncited"]
    assert service.ask(SEED_MERCHANT_ID, "Trova il pagamento da 89,70 €.").used_llm is False

    chat.reply = "Non lo so."
    assert service.ask(SEED_MERCHANT_ID, "Come vengono salvate le API key?").warnings == []
    assert service.ask(SEED_MERCHANT_ID, "Quanti pagamenti sono scaduti?").warnings == []


def test_backend_fact_lines_do_not_hide_an_uncited_model_answer(kit):
    service, chat = kit
    chat.reply = "Un pagamento è stato recuperato."
    answer = service.ask(SEED_MERCHANT_ID, "Perché questi pagamenti sono stati recuperati?")
    assert "[1]" in answer.answer
    assert answer.warnings == ["uncited"]


class BrokenSession:
    def __enter__(self):
        raise OperationalError("SELECT 1", {}, Exception("connection refused at 10.0.0.5"))

    def __exit__(self, *exc):
        return False


def test_database_errors_become_a_503_without_internal_details(kit, anon_client, merchant_a):
    service, _ = kit
    broken = RagService(
        service._embedder,
        service._chat,
        service._store,
        top_k=5,
        min_score=-1.0,
        max_question_chars=500,
        parser=QueryParser(lambda: NOW),
        structured=StructuredRetriever(BrokenSession),
    )
    app.dependency_overrides[get_rag_service] = lambda: broken
    try:
        response = anon_client.post(
            "/rag/ask", json={"question": "Quanti pagamenti sono scaduti?"}, headers=merchant_a.headers
        )
    finally:
        app.dependency_overrides.pop(get_rag_service, None)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "rag_unavailable"
    assert "10.0.0.5" not in response.text and "SELECT" not in response.text


def test_model_errors_become_a_503(kit, anon_client, merchant_a):
    service, chat = kit
    chat.fail = True
    app.dependency_overrides[get_rag_service] = lambda: service
    try:
        response = anon_client.post(
            "/rag/ask", json={"question": "Come vengono salvate le API key?"}, headers=merchant_a.headers
        )
    finally:
        app.dependency_overrides.pop(get_rag_service, None)
        chat.fail = False
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rag_unavailable"


def test_api_exposes_warnings(kit, anon_client, merchant_a):
    service, chat = kit
    chat.reply = "Risposta senza fonti."
    app.dependency_overrides[get_rag_service] = lambda: service
    try:
        body = anon_client.post(
            "/rag/ask", json={"question": "Come vengono salvate le API key?"}, headers=merchant_a.headers
        ).json()
    finally:
        app.dependency_overrides.pop(get_rag_service, None)
    assert body["warnings"] == ["uncited"]


def test_eval_checks_the_language_of_normal_answers_and_counts_warnings(kit):
    service, _ = kit
    cases = {c.id: c for c in load_cases()}

    class Fixed:
        def __init__(self, text: str, warnings: list[str]) -> None:
            self.text, self.warnings = text, warnings

        def retrieve(self, merchant_id, question):
            return service.retrieve(merchant_id, question)

        def answer(self, retrieval):
            return Answer(answer=self.text, sources=[], used_llm=True, warnings=self.warnings)

    truth = GroundTruth({}, {})
    english = Evaluator(Fixed("Keys are stored as sha256 hashes.", ["uncited"]), SEED_MERCHANT_ID, truth, generate=True)
    result = english.evaluate(cases["docs-it-keys"])
    assert result.checks["answer_language"] is False
    assert result.warnings == ["uncited"]

    italian = Evaluator(Fixed("Viene salvato solo l'hash sha256.", []), SEED_MERCHANT_ID, truth, generate=True)
    ok = italian.evaluate(cases["docs-it-keys"])
    assert ok.checks["answer_language"] is True

    summary = summarize([result, ok], [cases["docs-it-keys"]], min_score=0.48)
    assert summary["warnings"]["by_code"] == {"uncited": 1}
    assert summary["warnings"]["answers_with_warnings"] == 1
    assert isinstance(result, CaseResult)


def test_engineering_docs_in_subfolders_are_not_part_of_the_assistant_corpus(tmp_path):
    from app.rag.documents import markdown_documents

    (tmp_path / "architecture.md").write_text("# Corpus\ntext")
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "evaluation.md").write_text("# Not corpus\ntext")
    assert [d.source_key for d in markdown_documents(tmp_path)] == ["doc:architecture.md"]


def test_the_real_docs_folder_keeps_engineering_docs_out_of_the_index():
    from app.rag.documents import markdown_documents

    docs = Path(__file__).resolve().parents[2] / "docs"
    if not docs.is_dir():
        pytest.skip("docs/ is not part of the test image")
    assert [d.source_key for d in markdown_documents(docs)] == ["doc:architecture.md"]
    assert (docs / "quality" / "evaluation.md").is_file()


PAY_A = "73074148-bca3-5e91-969f-141d6491488b"
PAY_B = "f022dabf-46e8-5163-a7c2-87d33898a90a"
PAY_USD = "1b2c3d4e-0000-4000-8000-000000000001"


def payment_source(payment_id: str, amount: str, unit: str) -> ScoredChunk:
    return source(
        f"payment:{payment_id}",
        f"Pagamento {payment_id}\nProdotto: X, quantità 2\nImporto: {amount} (prezzo unitario {unit})\n"
        "Stato: paid\nCreato il: 2026-10-12T11:00:00",
    )


LOOKUP = [
    payment_source(PAY_A, "98,00 € (98.00 EUR)", "49,00 € (49.00 EUR)"),
    payment_source(PAY_B, "9,90 € (9.90 EUR)", "9,90 € (9.90 EUR)"),
    payment_source(PAY_USD, "12,00 USD (12.00 USD)", "12,00 USD (12.00 USD)"),
]
OVER_90 = PaymentFilters(amount=AmountRange(minimum=9000, minimum_inclusive=False), currency="eur")
UNDER_10 = PaymentFilters(amount=AmountRange(maximum=1000, maximum_inclusive=False), currency="eur")
BETWEEN = PaymentFilters(amount=AmountRange(minimum=900, maximum=10000), currency="eur")


def amount_warnings(answer: str, filters: PaymentFilters | None, sources=LOOKUP) -> list[str]:
    return [w for w in validate_answer(answer, "domanda", sources, applied_filters=filters) if w != "uncited"]


@pytest.mark.parametrize(
    ("text", "roles"),
    [
        ("supera il limite di 90,00 €, ammontando a 98,00 € [1]", ["threshold:min:False", "value"]),
        ("importo inferiore a 10,00 € sono:", ["threshold:max:False"]),
        ("almeno 90 € e non superiore a 100 €", ["threshold:min:True", "threshold:max:True"]),
        ("payments between €9 and €100.00", ["range_low:min", "range_high:max"]),
        ("da 9,00 € a 100,00 €", ["range_low:min", "range_high:max"]),
        ("il pagamento da 98,00 € [1]", ["value"]),
        ("hai incassato in totale 107,90 € [1]", ["aggregate"]),
        ("il più recente costa 29,90 € [2]", ["value"]),
        ("over $5 and under 2 GBP", ["threshold:min:None", "threshold:max:False"]),
        ("compreso tra 40 e 50 euro:", ["range_low:min", "range_high:max"]),
        ("payments between 40 and 50 EUR", ["range_low:min", "range_high:max"]),
        ("con un importo minimo di 40 euro e massimo di 50 euro", ["threshold:min:True", "threshold:max:True"]),
    ],
)
def test_amounts_are_classified_by_their_role_in_the_sentence(text, roles):
    def label(m):
        if m.role == "threshold":
            return f"threshold:{m.side}:{m.inclusive}"
        return f"{m.role}:{m.side}" if m.side else m.role

    assert [label(m) for m in classify(text)] == roles


@pytest.mark.parametrize(
    ("answer", "filters"),
    [
        (f"C'è un pagamento che supera i 90 euro:\n\nPagamento {PAY_A} (paid) con importo di 98,00 € [1].", OVER_90),
        (f"Il pagamento con ID {PAY_A} supera il limite di 90,00 €, ammontando a 98,00 € [1].", OVER_90),
        (
            "I pagamenti che corrispondono all'importo inferiore a 10,00 € sono:\n\n"
            f"1. Pagamento {PAY_B} con importo di 9,90 € [2]",
            UNDER_10,
        ),
        (f"One payment is over €90.00: {PAY_A}, amount 98.00 EUR [1].", OVER_90),
        (f"Payments under 10.00 EUR: {PAY_B} (9.90 EUR) [2].", UNDER_10),
        (f"Tra 9,00 € e 100,00 € ho trovato {PAY_A} da 98,00 € [1] e {PAY_B} da 9,90 € [2].", BETWEEN),
        (f"Il pagamento {PAY_A} costa 98,00 €, cioè 2 × 49,00 € [1].", None),
        (f"Il pagamento {PAY_USD} è di 12,00 USD [3].", None),
        (f"Il pagamento {PAY_A} ha un importo totale di 98,00 € [1].", None),
        (
            f"Ci sono 2 pagamenti con un importo compreso tra 9 e 100 euro:\n\n1. Pagamento {PAY_A}: 98,00 € [1]",
            BETWEEN,
        ),
        ("Questi pagamenti sono stati filtrati con un importo minimo di 9 euro e massimo di 100 euro [1].", BETWEEN),
    ],
)
def test_correct_amounts_and_repeated_thresholds_have_no_warnings(answer, filters):
    assert amount_warnings(answer, filters) == []


@pytest.mark.parametrize(
    ("answer", "filters", "warning"),
    [
        (f"Il pagamento {PAY_A} da 97,00 € [1].", OVER_90, "amount_not_in_sources"),
        (f"Il pagamento {PAY_A} da 9,90 € [1].", None, "amount_wrong_payment"),
        (f"Pagamento {PAY_A}: 9,90 € [1]. Pagamento {PAY_B}: 98,00 € [2].", None, "amount_wrong_payment"),
        ("L'importo è 9,90 € [1].", None, "amount_wrong_payment"),
        (f"Il pagamento {PAY_USD} è di 12,00 € [3].", None, "amount_not_in_sources"),
        (f"Il pagamento {PAY_A} da 98,00 USD [1].", None, "amount_not_in_sources"),
        (f"Supera gli 80,00 €: {PAY_A} da 98,00 € [1].", OVER_90, "threshold_unverified"),
        (f"È inferiore a 90,00 €: {PAY_A} da 98,00 € [1].", OVER_90, "threshold_unverified"),
        (f"È almeno di 90,00 €: {PAY_A} da 98,00 € [1].", OVER_90, "threshold_unverified"),
        (f"Supera i 90,00 USD: {PAY_A} da 98,00 € [1].", OVER_90, "threshold_unverified"),
        (f"Supera i 90,00 €: {PAY_A} da 98,00 € [1].", None, "threshold_unverified"),
        (f"Tra 9,00 € e 99,00 € ho trovato {PAY_A} [1].", BETWEEN, "threshold_unverified"),
        (f"Tra 8 e 100 euro ho trovato {PAY_A} [1].", BETWEEN, "threshold_unverified"),
        (f"Massimo di 90 euro: {PAY_B} [2].", BETWEEN, "threshold_unverified"),
        ("Il totale dei pagamenti trovati è 107,90 € [1][2].", None, "aggregate_unverified"),
    ],
)
def test_wrong_amounts_are_flagged(answer, filters, warning):
    assert warning in amount_warnings(answer, filters)


def test_an_amount_typed_in_the_question_is_not_evidence():
    answer = f"Il pagamento {PAY_A} è di 55,00 € [1]."
    assert "amount_not_in_sources" in validate_answer(answer, "Trova il pagamento da 55,00 €", LOOKUP)


def test_an_aggregate_must_match_the_backend_computation():
    total = source("sql:total", "Incassato lordo.\nPagamenti: 2.\nTotale: 107,90 €.")
    assert amount_warnings("Hai incassato 107,90 € lordi [1].", None, [total]) == []
    assert amount_warnings("Hai incassato 117,90 € lordi [1].", None, [total]) == ["aggregate_unverified"]


def test_backend_aggregate_answers_pass_the_validator(kit):
    service, _ = kit
    questions = [c.question for c in load_cases() if c.category == "aggregate"]
    checked = 0
    for question in questions:
        retrieval = service.retrieve(SEED_MERCHANT_ID, question)
        if retrieval.direct_answer is None or retrieval.route != "sql_aggregate":
            continue
        checked += "€" in retrieval.direct_answer
        warnings = validate_answer(
            retrieval.direct_answer, question, retrieval.selected, applied_filters=retrieval.plan.filters
        )
        assert warnings == [], (question, retrieval.direct_answer, warnings)
    assert checked >= 2


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        (
            "Quali pagamenti superano 90 euro?",
            "Il pagamento con ID {report2_paid} supera il limite di 90,00 €, ammontando a 98,00 € [1].",
        ),
        (
            "Quali pagamenti sono sotto i 10 €?",
            "I pagamenti che corrispondono all'importo inferiore a 10,00 € sono:\n\n"
            "1. Pagamento {starter_open} con importo di 9,90 € [1]",
        ),
    ],
)
def test_real_answers_of_the_validated_runs_are_no_longer_flagged(kit, question, reply):
    service, chat = kit
    refs = {"report2_paid", "starter_open"}
    reply = reply.format(**{ref: seed_payment_id(ref) for ref in refs})
    retrieval = service.retrieve(SEED_MERCHANT_ID, question)
    assert retrieval.plan is not None
    assert validate_answer(reply, question, retrieval.selected, applied_filters=retrieval.plan.filters) == []


def test_backend_written_refusals_count_as_refusals_in_any_language(kit):
    from app.rag.evaluation.runner import Case

    service, _ = kit
    evaluator = Evaluator(service, SEED_MERCHANT_ID, GroundTruth({}, {}), generate=True)
    case = Case(id="x", category="no_result", question="Find the payment of 250.00 EUR", expect_refusal=True)
    result = evaluator.evaluate(case)
    assert result.route == "no_match" and result.answer.startswith("I found no payments")
    assert result.checks["refused"] is True
