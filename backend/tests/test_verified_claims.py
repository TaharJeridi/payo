"""TAH-143 / ADR 0014: financial claims in model answers are checked against the evidence the model was given.

Answers quoted from the saved v3 runs are marked with the run they come from.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.rag.claims import check_claims, payment_evidence
from app.rag.documents import format_duration, payment_document
from app.rag.evaluation.seed import SEED_MERCHANT_ID, seed, seed_payment_id
from app.rag.indexer import RagIndexer
from app.rag.ports import Chunk, ScoredChunk
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.scope import OUT_OF_SCOPE, out_of_scope_request
from app.rag.service import UNVERIFIED_ABSENCE, RagService, Retrieval
from app.rag.structured import StructuredRetriever
from tests.rag_fakes import FakeChat, HashingEmbedder

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def pid(ref: str) -> str:
    return str(seed_payment_id(ref))


@pytest.fixture
def docs() -> dict[str, str]:
    """Payment evidence exactly as the backend writes it, for every seed payment."""
    with get_session_factory()() as session, session.begin():
        seed(session)
    with get_session_factory()() as session:
        payments = list(session.query(Payment).all())
        histories = PaymentRepository(session).histories([p.id for p in payments])
        return {str(p.id): payment_document(p, histories[p.id]).text for p in payments}


def source(key: str, text: str, source_type: str = "payment") -> ScoredChunk:
    chunk = Chunk(uuid.uuid5(uuid.NAMESPACE_URL, key), key, source_type, key, text, scope="s", position=0)
    return ScoredChunk(chunk=chunk, score=0.9)


def payments(docs: dict[str, str], *refs: str) -> list[ScoredChunk]:
    return [source(f"payment:{pid(r)}", docs[pid(r)]) for r in refs]


STATUS_DOC = source("doc:architecture.md", "`open`: the Checkout Session exists and can be paid.", "doc")


# --- evidence ------------------------------------------------------------------------------------------------


def test_evidence_values_are_read_back_from_the_backend_text(docs):
    [evidence] = payment_evidence(payments(docs, "report_paid"))
    assert evidence.payment_id == pid("report_paid") and evidence.status.value == "paid"
    assert evidence.amount_cents == 4900 and evidence.currency == "EUR"
    assert (evidence.paid_at - evidence.created_at).total_seconds() == 181


def test_paid_payments_carry_the_elapsed_time_computed_by_the_backend(docs):
    assert "- tempo dalla creazione al pagamento: 3 min 1 s (181 s)" in docs[pid("report_paid")]
    assert "tempo dalla creazione al pagamento" not in docs[pid("report_expired")]
    assert format_duration(31) == "31 s" and format_duration(3725) == "1 h 2 min 5 s (3725 s)"


# --- status --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        'Sì, la pagina di checkout su Stripe esiste per questo pagamento [1]. Il suo stato è "open", il che significa '
        'che "Il Checkout Session exists and can be paid" [2] [3].',  # blind-v3-s1 q013
        "Sì, la pagina esiste [1][2]. Il pagamento si trova nel stato `open`, il che significa che può essere "
        "pagato [2] [1].",  # blind-v3-1 q013
        "Sì, esiste [1]. Questo si deduce dal fatto che il pagamento ha un stato `open` [2] [3].",  # blind-v3-3
        "The status of the payment is paid [1].",
    ],
)
def test_a_status_that_contradicts_the_evidence_is_contradicted(docs, answer):
    report = check_claims(
        answer, "La pagina è stata creata?", [*payments(docs, "report_expired"), STATUS_DOC], "sql_find"
    )
    assert report.verdict == "contradicted" and report.warnings == ["contradicted_status"]
    assert report.blocking[0].expected == "expired"


@pytest.mark.parametrize(
    "answer",
    [
        "Lo stato del pagamento è expired - la sessione è scaduta senza pagamento [1].",
        "Il pagamento risulta in stato scaduto [1].",
        "The payment is in status expired [1].",
    ],
)
def test_a_status_that_matches_the_evidence_is_verified(docs, answer):
    report = check_claims(answer, "Che stato ha?", payments(docs, "report_expired"), "sql_find")
    assert report.verdict == "verified" and report.warnings == []


@pytest.mark.parametrize(
    "answer",
    [
        "Il pagamento era in stato open, poi è scaduto [1].",  # history
        "Lo stato open indica che la sessione esiste [2].",  # definition, no verb
        "Il pagamento non è pagato [1].",  # no status claim pattern
    ],
)
def test_history_definitions_and_negations_are_not_checked_as_current_status(docs, answer):
    report = check_claims(answer, "Che stato ha?", [*payments(docs, "report_expired"), STATUS_DOC], "sql_find")
    assert report.claims == ()


def test_status_is_attributed_to_the_payment_named_in_the_sentence(docs):
    sources = payments(docs, "report_expired", "pro_paid_oct02")
    right = (
        f"Il pagamento {pid('pro_paid_oct02')} ha lo stato paid [2]. "
        f"Il pagamento {pid('report_expired')[:8]} è in stato expired [1]."
    )
    assert check_claims(right, "Q?", sources, "sql_find").verdict == "verified"
    wrong = f"Il pagamento {pid('pro_paid_oct02')} ha lo stato expired [2]."
    assert check_claims(wrong, "Q?", sources, "sql_find").verdict == "contradicted"
    unattributable = "Lo stato è paid."  # two payments, no id, no citation
    assert check_claims(unattributable, "Q?", sources, "sql_find").claims == ()


# --- duration ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stated", ["3 minuti e 4 secondi", "3 minuti e 5 secondi", "31 secondi", "5 minutes"])
def test_a_wrong_duration_is_contradicted(docs, stated):  # blind-v3 r018
    answer = (
        f"Il pagamento è stato pagato alle 16:33:01 UTC [1]. Dalla creazione, che avvenne alle 16:30:00 UTC, il "
        f"pagamento è stato completato in {stated} [1]."
    )
    report = check_claims(answer, "quanto ci ha messo?", payments(docs, "report_paid"), "sql_find")
    assert report.warnings == ["contradicted_duration"]
    assert report.blocking[0].expected == "3 min 1 s (181 s)"


@pytest.mark.parametrize("stated", ["3 minuti", "circa 3 minuti", "3 minuti e 1 secondo", "181 secondi", "3 min 1 s"])
def test_a_duration_at_the_stated_precision_is_verified(docs, stated):  # blind-v3-3 and -s3 r018: "3 minuti"
    answer = f"Dalla creazione il pagamento è stato completato in {stated} [1]."
    report = check_claims(answer, "quanto ci ha messo?", payments(docs, "report_paid"), "sql_find")
    assert report.verdict == "verified"


def test_configuration_durations_are_not_payment_durations(docs):
    answer = "La sessione di checkout dura 2 ore per impostazione predefinita, e il pagamento è stato creato [1]."
    assert check_claims(answer, "Q?", payments(docs, "report_paid"), "sql_find").claims == ()


# --- absence -------------------------------------------------------------------------------------------------


def test_absence_on_the_semantic_route_is_unverifiable(docs):  # blind-v3 q055
    answer = "Based on the provided context, there is no Pro payment recorded for an exact amount of 59.80 EUR."
    report = check_claims(answer, "is there a Pro payment for exactly 59.80?", payments(docs, "pro_open"), "semantic")
    assert report.verdict == "unverifiable" and report.warnings == ["unverified_absence"]


def test_absence_is_not_flagged_on_sql_routes_or_about_one_named_payment(docs):
    answer = "Non ci sono pagamenti effettuati per questo pagamento [1]."
    sources = payments(docs, "pro2_unknown")
    assert check_claims(answer, "Trova i pagamenti Pro", sources, "sql_find").claims == ()
    assert check_claims(answer, "Come è finito quel pagamento da 59,80?", sources, "semantic").claims == ()


# --- selection -----------------------------------------------------------------------------------------------


def test_choosing_one_of_several_payments_with_the_asked_amount_is_unverifiable(docs):  # blind-v3 q149
    sources = payments(docs, "starter2_expired", "pro_expired", "starter_paid_a", "pro2_unknown", "pro_open")
    answer = "The payment for 29.90 EUR resulted in an expired session as indicated by the status [2]."
    report = check_claims(answer, "What happened with the 29.90 payment?", sources, "semantic")
    assert report.warnings == ["unjustified_selection"]


def test_naming_the_several_payments_or_a_unique_amount_is_not_a_selection(docs):
    sources = payments(docs, "pro_expired", "pro_open", "report_paid")
    plural = "There are two payments of 29.90 EUR: one expired [1], one still open [2]."
    assert check_claims(plural, "What happened with the 29.90 payment?", sources, "semantic").claims == ()
    unique = "The payment of 49.00 EUR was paid [3]."
    assert check_claims(unique, "What about the 49.00 payment?", sources, "semantic").claims == ()


# --- offers --------------------------------------------------------------------------------------------------


def test_an_invented_discount_code_is_unsupported_even_after_a_refusal(docs):  # blind-v3-s1 q140
    answer = "I do not know your customers. Here is an example: use code PROUPGRADE20 to get 20% off."
    report = check_claims(answer, "Write a marketing email", payments(docs, "pro_open"), "semantic", refusal=True)
    assert report.warnings == ["unsupported_offer"]
    plain_refusal = check_claims("I do not know.", "Q?", payments(docs, "pro_open"), "semantic", refusal=True)
    assert plain_refusal.claims == ()


# --- scope ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Write me a marketing email to push the Pro plan to my customers",  # v3 q140
        "Should I raise the Starter price to 12.90? would I sell less?",  # v3 q142
        "Scrivi una email di auguri di Natale per un cliente",  # v2 v2-oos-greeting
        "Preparami un post promozionale per il Report Pack",
        "Mi conviene abbassare il prezzo del Pro?",
    ],
)
def test_promotional_content_and_pricing_advice_are_out_of_scope(question):
    assert out_of_scope_request(question)


@pytest.mark.parametrize(
    "question",
    [
        "Scrivimi l'email del cliente del pagamento da 49,50 €",  # a data request (restricted attribute)
        "What is the customer's email for payment 64cef59a?",
        "Quanto ho incassato con il Pro?",
        "Qual è il prezzo unitario dello Starter?",
        "Perché il pagamento è stato rifiutato da Stripe?",
        "Create a checkout for the Pro plan",
    ],
)
def test_data_and_documentation_questions_are_in_scope(question):
    assert not out_of_scope_request(question)


# --- service -------------------------------------------------------------------------------------------------


@pytest.fixture
def kit(tmp_path: Path) -> Iterator[tuple[RagService, FakeChat]]:
    with get_session_factory()() as session, session.begin():
        seed(session)
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "architecture.md").write_text("# PayFlow\n\n## States\n`open`: the Checkout Session exists.\n")
    client = QdrantClient(location=":memory:")
    embedder = HashingEmbedder()
    store = QdrantVectorStore(client, "test_claims")
    RagIndexer(
        get_session_factory(), embedder, store, embed_model="fake", docs_dir=folder, chunk_chars=400, overlap_chars=40
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


def test_a_contradicted_status_is_replaced_by_the_backend_data(kit):
    service, chat = kit
    chat.reply = "Sì, la pagina esiste [1]. Il suo stato è `open` [2]."
    question = f"Per il pagamento {pid('report_expired')} la pagina di checkout è stata creata?"
    answer = service.ask(SEED_MERCHANT_ID, question)
    assert answer.warnings == ["contradicted_status"] and answer.used_llm is True
    assert "`open`" not in answer.answer
    assert answer.answer.startswith("La risposta generata contraddiceva i dati verificati del pagamento (stato)")
    line = f"- Pagamento {pid('report_expired')}: Financial Report Pack, 49,00 €, stato scaduto, motivo: "
    assert line + "checkout_session_expired [1]." in answer.answer
    assert answer.sources[0].cited


def test_a_wrong_duration_is_replaced_by_the_backend_duration(kit):
    service, chat = kit
    chat.reply = "Dalla creazione il pagamento è stato completato in 3 minuti e 4 secondi [1]."
    answer = service.ask(SEED_MERCHANT_ID, f"il {pid('report_paid')} quanto ci ha messo dalla creazione?")
    assert answer.warnings == ["contradicted_duration"]
    assert "3 min 1 s (181 s) [1]" in answer.answer and "3 minuti e 4 secondi" not in answer.answer


def test_a_correct_answer_is_left_unchanged(kit):
    service, chat = kit
    chat.reply = "Il pagamento è stato completato in 3 minuti [1]. Il suo stato è paid [1]."
    answer = service.ask(SEED_MERCHANT_ID, f"il {pid('report_paid')} quanto ci ha messo dalla creazione?")
    assert answer.answer.startswith("Il pagamento è stato completato in 3 minuti [1].")
    assert "contradicted_status" not in answer.warnings and "contradicted_duration" not in answer.warnings


def test_an_absence_from_semantic_retrieval_states_the_limit_instead(kit, docs):
    # The q055 question itself reaches SQL since TAH-144 (tests/test_unanswered_questions.py); the
    # semantic route is built here so the claim check is still tested on the evidence Qwen saw.
    service, chat = kit
    chat.reply = "There is no Pro payment for exactly 59.80 [1]."
    question = "is there a Pro payment for exactly 59.80?"
    evidence = payments(docs, "pro_expired", "pro_open", "starter2_expired")
    answer = service.answer(Retrieval(SEED_MERCHANT_ID, question, candidates=evidence, selected=evidence))
    assert answer.warnings == ["unverified_absence"]
    assert answer.answer == UNVERIFIED_ABSENCE["en"]


def test_a_silent_selection_lists_the_payments_with_that_amount(kit, docs):
    service, chat = kit
    chat.reply = "The payment for 29.90 EUR resulted in an expired session [2]."
    question = "What happened with the 29.90 payment?"
    evidence = payments(docs, "starter2_expired", "pro_expired", "pro_open")
    retrieval = Retrieval(SEED_MERCHANT_ID, question, candidates=evidence, selected=evidence)
    answer = service.answer(retrieval)
    assert answer.warnings == ["unjustified_selection"]
    assert answer.answer.startswith("The documents I consulted contain 2 payments of 29,90 €")
    assert f"- Payment {pid('pro_expired')}: PayFlow Pro, 29,90 €, status expired [2]." in answer.answer
    assert f"- Payment {pid('pro_open')}: PayFlow Pro, 29,90 €, status awaiting payment [3]." in answer.answer
    assert [s.cited for s in answer.sources] == [False, True, True]


def test_out_of_scope_requests_are_refused_without_calling_the_model(kit):
    service, chat = kit
    answer = service.ask(SEED_MERCHANT_ID, "Write me a marketing email to push the Pro plan to my customers")
    assert answer.answer == OUT_OF_SCOPE["en"] and answer.used_llm is False and chat.calls == []


def test_a_product_ranking_comes_from_sql_and_lists_every_product(kit):
    service, chat = kit
    answer = service.ask(SEED_MERCHANT_ID, "Mi fai la classifica dei prodotti per unità vendute?")
    assert answer.used_llm is False and chat.calls == []
    assert "1. PayFlow Starter: 7 unità, 69,30 €" in answer.answer
    assert "2. PayFlow Pro: 6 unità, 179,40 €" in answer.answer
    assert "3. Financial Report Pack: 4 unità, 196,00 €" in answer.answer
    plain = service.ask(SEED_MERCHANT_ID, "Qual è il prodotto più venduto?")
    assert "Classifica" not in plain.answer


# --- guards found by mutation testing ------------------------------------------------------------------------

ONLY_TWO_MOMENTS = source(
    "payment:" + str(uuid.uuid4()),
    "Stato: paid - pagato\nCreato il: 2026-10-08T16:30:00+00:00\nPagato il: 2026-10-08T16:33:01+00:00",
)


def test_minutes_without_seconds_allow_the_minute_but_not_more():
    for stated, verdict in (("3 minuti", "verified"), ("2 minuti", "contradicted"), ("4 minuti", "contradicted")):
        answer = f"Dalla creazione il pagamento è stato completato in {stated} [1]."
        assert check_claims(answer, "Q?", [ONLY_TWO_MOMENTS], "sql_find").verdict == verdict
    exact = "Dalla creazione il pagamento è stato completato in 3 minuti e 0 secondi [1]."
    assert check_claims(exact, "Q?", [ONLY_TWO_MOMENTS], "sql_find").verdict == "contradicted"


def test_durations_unrelated_to_creation_or_payment_are_not_checked():
    answer = "Il webhook è arrivato 5 minuti dopo [1]."
    assert check_claims(answer, "Q?", [ONLY_TWO_MOMENTS], "sql_find").claims == ()


def test_a_status_described_as_past_is_not_checked_as_current(docs):
    answer = "Inizialmente il pagamento è in stato open [1]."
    assert check_claims(answer, "Q?", payments(docs, "report_expired"), "sql_find").claims == ()


def test_a_refusal_is_checked_only_for_offers(docs):
    answer = "I do not know why. Its status is open [1]."
    sources = payments(docs, "report_expired")
    assert check_claims(answer, "Q?", sources, "sql_find").warnings == ["contradicted_status"]
    assert check_claims(answer, "Q?", sources, "sql_find", refusal=True).claims == ()


def test_an_offer_present_in_the_sources_is_not_invented():
    doc = source("doc:promo.md", "Spring campaign: promo code SPRING2026 gives 10% off.", "doc")
    answer = "You can use the promo code SPRING2026 [1]."
    assert check_claims(answer, "Q?", [doc], "semantic").claims == ()


def test_selection_is_justified_when_the_answer_covers_the_payments_or_says_there_are_several(docs):
    sources = payments(docs, "pro_expired", "pro_open")
    question = "What happened with the 29.90 payment?"
    both = f"The payment {pid('pro_expired')[:8]} expired [1] and the payment {pid('pro_open')[:8]} is open [2]."
    assert check_claims(both, question, sources, "semantic").claims == ()
    several = "The payment of 29.90 EUR is one of several payments with that amount; one expired [1]."
    assert check_claims(several, question, sources, "semantic").claims == ()


def test_an_invented_offer_is_replaced_by_the_scope_message(kit):
    service, chat = kit
    chat.reply = "I do not know your plans. Use code PROUPGRADE20 for 20% off [1]."
    answer = service.ask(SEED_MERCHANT_ID, "Tell me about the Pro plan")
    assert answer.warnings == ["unsupported_offer"] and answer.answer == OUT_OF_SCOPE["en"]
