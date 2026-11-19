import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.db.session import get_session_factory
from app.rag.evaluation.runner import contains_any, load_cases
from app.rag.evaluation.seed import SEED_MERCHANT_ID, seed, seed_payment_id
from app.rag.indexer import RagIndexer
from app.rag.language import NO_EVIDENCE, detect_language, reply_language
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.service import SYSTEM_PROMPT, RagService
from app.rag.structured import StructuredRetriever
from tests.rag_fakes import FakeChat, HashingEmbedder

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
RECOVERED = seed_payment_id("report_paid_recovered")
LATE = seed_payment_id("pro_paid_after_expiry")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Quanto costa il piano PayFlow Enterprise?", "it"),
        ("Quali dati personali non vengono indicizzati dall'assistente?", "it"),
        ("Trova il pagamento da 12,34 €.", "it"),
        ("How are merchant API keys stored?", "en"),
        ("Find the payment of 89.70 EUR.", "en"),
        ("Do not know. [1][2]", "en"),
        ("I found no payments matching these criteria: amount 250,00 €.", "en"),
        ("Yes: payment 73074148-bca3-5e91-969f-141d6491488b is paid [1].", "en"),
        ("I pagamenti scaduti sono 3 [1].", "it"),
        ("Il pagamento 73074148-bca3-5e91-969f-141d6491488b è fallito: checkout_session_not_created [1].", "it"),
        ("根据提供的信息，我没有找到关于PayFlow Enterprise的价格。", "other"),
        ("", "it"),
    ],
)
def test_language_detection(text, language):
    assert detect_language(text) == language


def test_replies_fall_back_to_italian_for_unknown_scripts():
    assert reply_language("根据提供的信息") == "it"
    assert reply_language("Which payments expired?") == "en"


@pytest.fixture
def kit(tmp_path: Path) -> Iterator[tuple[RagService, FakeChat]]:
    with get_session_factory()() as session, session.begin():
        seed(session)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "# PayFlow\n\n## Indexing\nCustomer email, checkout URL and Idempotency-Key are not indexed.\n"
    )
    client = QdrantClient(location=":memory:")
    embedder = HashingEmbedder()
    store = QdrantVectorStore(client, "test_refusals")
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


def test_prompt_rule_4_is_the_original_one_without_special_tokens():
    assert "4. If the context does not contain the answer, say that you do not know." in SYSTEM_PROMPT
    assert "NO_EVIDENCE" not in SYSTEM_PROMPT


def test_a_refusal_in_another_language_is_rewritten_in_the_question_language(kit):
    service, chat = kit
    chat.reply = "Do not know. [1][2]"
    italian = service.ask(SEED_MERCHANT_ID, "Quanto costa il piano PayFlow Enterprise?")
    assert italian.answer == NO_EVIDENCE["it"]
    assert italian.used_llm is True
    assert not any(s.cited for s in italian.sources)

    chat.reply = "根据提供的信息，我没有找到关于PayFlow Enterprise的价格。无法回答。"
    assert service.ask(SEED_MERCHANT_ID, "Quanto costa il piano PayFlow Enterprise?").answer == NO_EVIDENCE["it"]

    chat.reply = "Non lo so."
    english = service.ask(SEED_MERCHANT_ID, "How much does the PayFlow Enterprise plan cost?")
    assert english.answer == NO_EVIDENCE["en"]


def test_a_refusal_in_the_right_language_is_kept_as_written(kit):
    service, chat = kit
    chat.reply = "Non sono elencati dati personali specifici [1]."
    kept = service.ask(SEED_MERCHANT_ID, "Quali dati personali non vengono indicizzati dall'assistente?")
    assert kept.answer == "Non sono elencati dati personali specifici [1]."


def test_docs_it_isolation_regression_content_answers_are_not_replaced(kit):
    service, chat = kit
    chat.reply = "L'API risponde con 404, come per un id inesistente [1]."
    answer = service.ask(
        SEED_MERCHANT_ID, "Cosa risponde l'API se un merchant chiede un pagamento di un altro merchant?"
    )
    assert answer.answer == "L'API risponde con 404, come per un id inesistente [1]."
    assert answer.sources[0].cited


def test_low_but_passing_scores_still_reach_the_model(kit):
    service, chat = kit
    chat.reply = "Risposta [1]."
    retrieval = service.retrieve(SEED_MERCHANT_ID, "Spiegami la documentazione PayFlow")
    assert retrieval.route == "semantic" and retrieval.selected
    assert all(hit.score < 0.9 for hit in retrieval.selected)
    answer = service.answer(retrieval)
    assert answer.answer == "Risposta [1]." and len(chat.calls) == 1


def test_lookup_async_regression_the_model_answer_is_kept_on_the_sql_route(kit):
    service, chat = kit
    chat.reply = "Il pagamento a807d01f è fallito: async_payment_failed [1]."
    answer = service.ask(SEED_MERCHANT_ID, "Perché è fallito il pagamento PayFlow Pro dopo il pagamento asincrono?")
    assert answer.answer == "Il pagamento a807d01f è fallito: async_payment_failed [1]."
    assert answer.sources[0].source_key == f"payment:{seed_payment_id('pro_failed_async')}"

    listing = service.ask(SEED_MERCHANT_ID, "Quale pagamento PayFlow Pro è fallito dopo un pagamento asincrono?")
    assert listing.used_llm is False and len(chat.calls) == 1
    assert "stato fallito, motivo: async_payment_failed [1]." in listing.answer


def test_a_sql_route_refusal_stays_visible_next_to_the_payments_found(kit):
    service, chat = kit
    chat.reply = "I do not know."
    answer = service.ask(SEED_MERCHANT_ID, "Cosa è successo al pagamento da 98,00 €?")
    report2 = seed_payment_id("report2_paid")
    assert answer.answer.startswith("Nei dati dei pagamenti trovati non c'è l'informazione richiesta.\n\n")
    assert "Ho trovato 1 pagamento con questi criteri: importo 98,00 €." in answer.answer
    assert f"- Pagamento {report2}: Financial Report Pack, 98,00 €, stato pagato [1]." in answer.answer
    assert answer.sources[0].cited

    chat.reply = "Non lo so."
    assert service.ask(SEED_MERCHANT_ID, "Cosa è successo al pagamento da 98,00 €?").answer == "Non lo so."


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Qual è l'email del cliente che ha pagato 89,70 EUR?",
            "Ho trovato il pagamento {id} [1], ma l'email del cliente non è disponibile: "
            "l'assistente non ha accesso a questo dato.",
        ),
        (
            "Qual è il numero della carta usata per il pagamento da 89,70 €?",
            "Ho trovato il pagamento {id} [1], ma i dati della carta non sono disponibili: "
            "l'assistente non ha accesso a questi dati.",
        ),
        (
            "What is the customer's email for the payment of 89.70 EUR?",
            "I found payment {id} [1], but the customer's email is not available: the assistant has no access to it.",
        ),
    ],
)
def test_pii_email_regression_a_found_payment_does_not_answer_everything(kit, question, expected):
    service, chat = kit
    answer = service.ask(SEED_MERCHANT_ID, question)
    payment = seed_payment_id("pro3_paid")
    assert answer.answer == expected.format(id=payment)
    assert answer.used_llm is False and chat.calls == []
    assert answer.sources[0].source_key == f"payment:{payment}" and answer.sources[0].cited
    assert "@" not in answer.answer


def test_unavailable_attribute_with_several_payments(kit):
    service, _ = kit
    answer = service.ask(SEED_MERCHANT_ID, "Qual è l'email dei clienti che hanno pagato 29,90 €?")
    assert answer.answer.startswith("Ho trovato 6 pagamenti con questi criteri [1][2][3][4][5][6], ma l'email")


@pytest.mark.parametrize(
    "question", ["Cosa è successo al pagamento da 89,70 €?", "What happened to the payment of 89.70 EUR?"]
)
def test_amount_regression_the_amount_reaches_the_model(kit, question):
    service, chat = kit
    chat.reply = "Pagamento da 89,70 € [1]."
    service.ask(SEED_MERCHANT_ID, question)
    system, user = chat.calls[-1]
    assert system.content == SYSTEM_PROMPT.format(tag=re.search(r"<(context-[0-9a-f]+)>", user.content).group(1))
    assert "Importo: 89,70 € (89.70 EUR)" in user.content


@pytest.mark.parametrize(
    ("question", "payment", "line"),
    [
        (
            "Quali pagamenti sono stati recuperati?",
            RECOVERED,
            "- Pagamento {id}: recuperato tramite riconciliazione "
            "dopo che l'esito della creazione era andato perso [1].",
        ),
        (
            "Quali pagamenti sono arrivati dopo la scadenza della sessione?",
            LATE,
            "- Pagamento {id}: pagato dopo la scadenza della sessione di checkout [1].",
        ),
        (
            "Which payments were recovered?",
            RECOVERED,
            "- Payment {id}: recovered through reconciliation after the creation outcome had been lost [1].",
        ),
    ],
)
def test_verified_facts_explaining_a_derived_match_are_added_by_the_backend(kit, question, payment, line):
    service, chat = kit
    chat.reply = "Il pagamento è stato trovato [1]."
    answer = service.ask(SEED_MERCHANT_ID, question)
    assert answer.used_llm is False and chat.calls == []
    assert answer.answer.startswith(("Ho trovato 1 pagamento con questi criteri", "I found 1 payment matching"))
    assert line.format(id=payment) in answer.answer
    assert answer.sources[0].source_key == f"payment:{payment}" and answer.sources[0].cited
    cited_ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27}", answer.answer))
    assert cited_ids == {str(payment)}


def test_no_fact_lines_without_a_derived_fact_filter(kit):
    service, chat = kit
    chat.reply = "Pagato [1]."
    answer = service.ask(SEED_MERCHANT_ID, f"Qual è lo stato del pagamento {RECOVERED}?")
    assert answer.answer == "Pagato [1]."


def test_backend_refusals_follow_the_question_language(kit):
    service, chat = kit
    assert service.ask(SEED_MERCHANT_ID, "Find the payment of 12.34 EUR.").answer == (
        "I found no payments matching these criteria: amount 12,34 €."
    )
    assert service.ask(SEED_MERCHANT_ID, "Trova il pagamento da 12,34 €.").answer == (
        "Non ho trovato pagamenti con questi criteri: importo 12,34 €."
    )
    refund = service.ask(SEED_MERCHANT_ID, "How much was refunded?").answer
    assert refund.startswith("PayFlow does not record refunds:")
    assert chat.calls == []


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("Quanti pagamenti sono stati recuperati?", None),
        ("Quanti pagamenti PayFlow Pro sono falliti?", "Hai 1 pagamento fallito con prodotto PayFlow Pro [1]."),
        ("Quanti pagamenti sono scaduti?", "Hai 3 pagamenti scaduti [1]."),
        ("Quanti pagamenti hanno un esito sconosciuto?", "Hai 1 pagamento con esito sconosciuto [1]."),
        ("Quanto ho incassato il 7 ottobre 2026?", None),
    ],
)
def test_italian_agreement_in_backend_messages(kit, question, answer):
    service, _ = kit
    text = service.ask(SEED_MERCHANT_ID, question).answer
    if answer is not None:
        assert text == answer
    assert not re.search(r"\b1 pagamenti\b|\b1 unità in 1 pagamenti\b", text)
    assert "da 1 pagamenti" not in text


def test_listing_and_counts_use_singular_forms(kit):
    service, chat = kit
    total = service.ask(SEED_MERCHANT_ID, "Quanto ho incassato il 7 ottobre 2026?").answer
    assert total.startswith("Hai incassato 89,70 € lordi, da 1 pagamento con stato pagato")
    chat.reply = "I do not know."
    listing = service.ask(SEED_MERCHANT_ID, "Trova i pagamenti scaduti.").answer
    assert "Ho trovato 3 pagamenti con questi criteri: stato scaduto." in listing
    assert listing.count("stato scaduto, motivo: checkout_session_expired [") == 3
    asked = service.ask(SEED_MERCHANT_ID, "Il pagamento da 19,80 € per due PayFlow Starter è scaduto?").answer
    assert asked.startswith(f"Sì: il pagamento {seed_payment_id('starter2_expired')} risulta scaduto [1].")
    paid = service.ask(SEED_MERCHANT_ID, "Il pagamento da 19,80 € per due PayFlow Starter è stato pagato?").answer
    assert paid.startswith(f"No: il pagamento {seed_payment_id('starter2_expired')} risulta scaduto [1].")


def test_answer_checks_reject_negated_facts():
    assert contains_any("Sì, il pagamento è stato effettuato [1].", ["effettuat"])
    assert not contains_any("No, il pagamento non è stato effettuato [1].", ["effettuat"])
    assert not contains_any("Il pagamento non risulta pagato [1].", ["pagat"])
    assert contains_any("Il checkout non è stato creato [1].", ["non è stato creato"])
    assert contains_any("Non è scaduto: è stato pagato [1].", ["pagat"])


def test_changed_expectations_are_documented():
    raw = json.loads(Path(__file__).resolve().parents[1].joinpath("app/rag/evaluation/cases.json").read_text())
    notes = {c["id"]: c.get("note") for c in raw}
    assert notes["lookup-report2"] and notes["id-failed"]
    cases = {c.id: c for c in load_cases()}
    assert "pagat" in cases["lookup-report2"].expect_answer
    assert "checkout_session_not_created" in cases["id-failed"].expect_answer
