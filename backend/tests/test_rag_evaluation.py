import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus
from app.rag.chunking import split_markdown
from app.rag.evaluation import __main__ as cli
from app.rag.evaluation.runner import (
    CATEGORIES,
    Candidate,
    CaseResult,
    Evaluator,
    GroundTruth,
    amount_forms,
    balanced_accuracy,
    contains_any,
    is_refusal,
    load_cases,
    matches,
    render_json,
    render_text,
    summarize,
    threshold_analysis,
)
from app.rag.evaluation.seed import (
    SEED_MERCHANT_ID,
    SEED_PAYMENTS,
    SeedPayment,
    Step,
    seed,
    seed_payment_id,
    validate,
)
from app.rag.indexer import RagIndexer
from app.rag.language import NO_EVIDENCE
from app.rag.ports import Chunk
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.service import Answer, RagService
from tests.rag_fakes import FakeChat, HashingEmbedder

REPO_DOCS = Path(__file__).resolve().parents[2] / "docs"
EMBED = "fake-embed"


def run_seed():
    with get_session_factory()() as session, session.begin():
        return seed(session)


def test_seed_paths_follow_the_state_machine():
    validate()
    broken = SeedPayment(
        "bad",
        "payflow-pro",
        1,
        datetime(2026, 10, 1, tzinfo=UTC),
        (Step(PaymentStatus.PAID, "api", timedelta(0)), Step(PaymentStatus.OPEN, "api", timedelta(0))),
    )
    with pytest.raises(ValueError, match="illegal paid -> open"):
        validate([broken])
    with pytest.raises(ValueError, match="Duplicate"):
        validate([SEED_PAYMENTS[0], SEED_PAYMENTS[0]])


def test_seed_is_idempotent_and_consistent():
    first = run_seed()
    second = run_seed()
    assert (first.merchant_created, first.inserted, first.existing) == (True, len(SEED_PAYMENTS), 0)
    assert (second.merchant_created, second.inserted, second.existing) == (False, 0, len(SEED_PAYMENTS))

    with get_session_factory()() as session:
        repo = PaymentRepository(session)
        payments = session.execute(select(Payment).where(Payment.merchant_id == SEED_MERCHANT_ID)).scalars().all()
        assert len(payments) == len([p for p in SEED_PAYMENTS if p.tenant == "main"])
        specs = {p.id: p for p in SEED_PAYMENTS}
        for payment in payments:
            spec = specs[payment.id]
            assert payment.status is spec.status
            assert payment.amount == payment.unit_amount * payment.quantity
            assert payment.customer_email is None and payment.checkout_url is None
            assert payment.next_reconcile_at is None
            assert (payment.paid_at is not None) == any(s.to_status is PaymentStatus.PAID for s in spec.steps)
            if payment.stripe_checkout_session_id:
                assert payment.stripe_checkout_session_id.startswith("cs_test_seed_")
            history = repo.history(payment.id)
            assert history[0].from_status is None and history[0].to_status is PaymentStatus.CREATING
            for previous, current in zip(history, history[1:], strict=False):
                assert current.from_status is previous.to_status
                assert current.created_at >= previous.created_at
            assert history[-1].to_status is payment.status
            assert payment.updated_at == history[-1].created_at

        counts, paid = repo.stats_for_merchant(SEED_MERCHANT_ID)
        assert counts[PaymentStatus.OPEN] == 3
        assert counts[PaymentStatus.PAID] == 10
        assert paid == {"eur": sum(p.amount for p in payments if p.status is PaymentStatus.PAID)}


def test_seed_payments_are_never_claimed_by_reconciliation():
    run_seed()
    with get_session_factory()() as session, session.begin():
        claimed = PaymentRepository(session).claim_due_for_reconciliation(
            datetime.now(UTC) + timedelta(days=3650), 1000, timedelta(minutes=5)
        )
    assert claimed == []


def test_cases_resolve_and_reference_real_sections():
    cases = load_cases()
    assert {c.category for c in cases} == set(CATEGORIES)
    assert all("{payment:" not in c.question for c in cases)
    by_id = {c.id: c for c in cases}
    assert str(seed_payment_id("pro_open")) in by_id["id-open"].question
    assert all(
        c.expect_sources
        or c.expect_refusal
        or c.expect_count
        or c.expect_paid_total
        or c.expect_payments is not None
        or c.expect_count_filters
        or c.expect_total_filters
        or c.expect_answer
        for c in cases
    )

    if not REPO_DOCS.is_dir():
        pytest.skip("docs/ is not part of the test image")
    headings = {
        part
        for path in REPO_DOCS.glob("*.md")
        for chunk in split_markdown(path.read_text(encoding="utf-8"), max_chars=10_000, overlap_chars=0)
        for part in chunk.heading.split(" > ")
    }
    sections = {e.partition("#")[2] for c in cases for e in c.expect_sources if e.startswith("doc:")}
    assert sections and sections <= headings


def test_load_cases_rejects_unknown_references(tmp_path):
    bad = tmp_path / "cases.json"
    bad.write_text(json.dumps([{"id": "x", "category": "payment_lookup", "question": "{payment:nope}?"}]))
    with pytest.raises(ValueError, match="unknown seed payment nope"):
        load_cases(bad)
    bad.write_text(json.dumps([{"id": "x", "category": "weird", "question": "?"}]))
    with pytest.raises(ValueError, match="unknown category"):
        load_cases(bad)


def _chunk(source_key: str, title: str) -> Chunk:
    return Chunk(
        point_id=uuid.uuid4(),
        source_key=source_key,
        source_type="doc",
        scope="public",
        position=0,
        title=title,
        text=title,
    )


def test_matches_payment_refs_and_doc_sections_exactly():
    payment = _chunk(f"payment:{seed_payment_id('pro_open')}", "Pagamento")
    assert matches(payment, "payment:pro_open")
    assert not matches(payment, "payment:report_open")
    doc = _chunk("doc:architecture.md", "Architecture > PayFlow AI > 5. Reconciliation")
    assert matches(doc, "doc:architecture.md#5. Reconciliation")
    assert matches(doc, "doc:architecture.md")
    assert not matches(doc, "doc:architecture.md#5. Reconcil")
    assert not matches(doc, "doc:other.md#5. Reconciliation")


def test_answer_heuristics():
    assert all(is_refusal(text) for text in NO_EVIDENCE.values())
    assert is_refusal("Non è specificato il numero preciso nel contesto.")
    assert is_refusal("Non sono in grado di fornire informazioni su Kubernetes.")
    assert is_refusal("Do not know. [1][2]")
    assert not is_refusal("Il pagamento è stato pagato [1].")
    assert contains_any("Ci sono 3 pagamenti", ["3"])
    assert not contains_any("Ci sono 13 pagamenti", ["3"])
    assert not contains_any("Costa 3,50 EUR", ["3"])
    assert contains_any("Sono tre [1].", ["3", "tre"])
    assert contains_any("Totale: 1.234,50 EUR", amount_forms(123450))
    assert contains_any("Totale 98.00 EUR.", amount_forms(9800))


def _result(case_id: str, scores: list[tuple[float, bool]], category: str = "payment_lookup") -> CaseResult:
    candidates = [Candidate("t", "k", s, r) for s, r in scores]
    rank = next((i for i, c in enumerate(candidates, start=1) if c.relevant), None)
    return CaseResult(case_id, category, "q", candidates, candidates, rank, checks={"retrieved": True})


def test_threshold_analysis_finds_the_separating_score():
    cases = {c.id: c for c in load_cases()}
    results = [
        _result("lookup-pro3", [(0.70, True)]),
        _result("lookup-report2", [(0.66, False), (0.62, True)]),
        _result("lookup-starter5", [(0.90, False)]),
        _result("id-open", [(1.0, True)], "payment_by_id"),
        _result("ood-capital", [(0.48, False)], "out_of_domain"),
        _result("ood-poem", [(0.52, False)], "out_of_domain"),
        _result("ood-stock", [], "out_of_domain"),
    ]
    used = [cases[r.id] for r in results]
    analysis = threshold_analysis(results, used, current=0.35)
    assert analysis.positives == [0.62, 0.70]
    assert analysis.negatives == [0.0, 0.48, 0.52]
    assert analysis.not_ranked == 1
    assert analysis.current_balanced_accuracy == pytest.approx(0.667, abs=1e-3)
    assert analysis.best_balanced_accuracy == 1.0
    assert analysis.best_threshold == pytest.approx(0.57)


def test_threshold_analysis_prefers_the_lowest_of_equal_thresholds():
    cases = {c.id: c for c in load_cases()}
    results = [_result("lookup-pro3", [(0.4, True)]), _result("ood-capital", [(0.6, False)], "out_of_domain")]
    analysis = threshold_analysis(results, [cases["lookup-pro3"], cases["ood-capital"]], current=0.35)
    assert analysis.best_balanced_accuracy == 0.5
    assert analysis.best_threshold == pytest.approx(0.399)
    assert balanced_accuracy(0.5, [0.4], [0.6]) == 0.0


@dataclass
class EvalKit:
    service: RagService
    chat: FakeChat
    indexer: RagIndexer


@pytest.fixture
def kit(tmp_path) -> Iterator[EvalKit]:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "# PayFlow\n\n## 4. Unknown outcomes and recovery\n"
        "How long a checkout session lasts before it expires: the default lifetime is 2 hours.\n\n"
        "## 6. Merchants, API keys and isolation\nOnly sha256 of the API key is stored.\n"
    )
    client = QdrantClient(location=":memory:")
    embedder = HashingEmbedder()
    store = QdrantVectorStore(client, "test_eval")
    chat = FakeChat()
    indexer = RagIndexer(
        get_session_factory(), embedder, store, embed_model=EMBED, docs_dir=docs, chunk_chars=400, overlap_chars=40
    )
    service = RagService(embedder, chat, store, top_k=5, min_score=0.2, max_question_chars=500)
    yield EvalKit(service, chat, indexer)
    client.close()


def _truth() -> GroundTruth:
    with get_session_factory()() as session:
        counts, paid = PaymentRepository(session).stats_for_merchant(SEED_MERCHANT_ID)
    return GroundTruth(counts, paid)


def test_index_problems_until_seeded_and_indexed(kit):
    with get_session_factory()() as session:
        assert any("seed payments missing" in p for p in cli.index_problems(session, EMBED))
    run_seed()
    with get_session_factory()() as session:
        problems = cli.index_problems(session, EMBED)
    assert any("not indexed" in p for p in problems) and any("documentation" in p for p in problems)
    kit.indexer.run()
    with get_session_factory()() as session:
        assert cli.index_problems(session, EMBED) == []
        assert cli.index_problems(session, "other-model") != []


def test_cli_run_refuses_when_not_ready(capsys):
    assert cli.main(["run", "--retrieval-only"]) == 2
    assert "not ready" in capsys.readouterr().err


def test_cli_seed(capsys):
    assert cli.main(["seed"]) == 0
    assert f"inserted={len(SEED_PAYMENTS)}" in capsys.readouterr().out
    assert cli.main(["seed"]) == 0
    assert "inserted=0" in capsys.readouterr().out


def test_retrieval_only_evaluation_end_to_end(kit):
    run_seed()
    kit.indexer.run()
    cases = [c for c in load_cases() if c.expect_sources or c.expect_refusal]
    results = Evaluator(kit.service, SEED_MERCHANT_ID, _truth(), generate=False).run(cases)
    by_id = {r.id: r for r in results}

    assert kit.chat.calls == []
    assert by_id["id-open"].first_relevant_rank == 1
    assert by_id["id-open"].candidates[0].score == 1.0
    assert all(r.answer is None for r in results)
    assert all(set(r.checks) <= {"retrieved", "no_context", "payments", "isolated"} for r in results)

    summary = summarize(results, cases, min_score=0.2)
    assert summary["categories"]["payment_by_id"]["hit@1"] == 1.0
    assert "aggregate" not in summary["categories"]
    text = render_text(results, summary, {"min_score": 0.2})
    assert "payment_by_id" in text and "Score threshold" in text
    payload = json.loads(render_json(results, summary, {}))
    assert len(payload["results"]) == len(cases)


def test_generation_checks_answers_citations_and_refusals(kit):
    run_seed()
    kit.indexer.run()
    truth = _truth()
    open_count = truth.counts[PaymentStatus.OPEN]

    def reply(messages):
        question = messages[-1].content.rsplit("Question: ", 1)[1]
        if "Australia" in question:
            return "Non lo so."
        if "Quanti pagamenti sono in attesa" in question:
            return f"Ci sono {open_count} pagamenti in attesa [1]."
        if "Qual è lo stato del pagamento" in question:
            return "Il pagamento è open, in attesa [1]."
        return "Secondo i dati [2]."

    kit.chat.reply = reply
    cases = {c.id: c for c in load_cases()}
    evaluator = Evaluator(kit.service, SEED_MERCHANT_ID, truth, generate=True)

    by_id = evaluator.evaluate(cases["id-open"])
    assert by_id.checks == {
        "retrieved": True,
        "not_refused": True,
        "answer_language": True,
        "answer": True,
        "cited_expected": True,
    }

    aggregate = evaluator.evaluate(cases["agg-open"])
    assert aggregate.expected_answer[0] == str(open_count)
    assert aggregate.checks["answer"] is True

    ood = evaluator.evaluate(cases["ood-capital"])
    assert "no_context" not in ood.checks
    assert ood.checks["refused"] is True
    assert ood.checks["refusal_language"] is True

    kit.chat.reply = lambda messages: "Do not know. [1]"
    normalized = evaluator.evaluate(cases["ood-capital"])
    assert normalized.checks["refused"] is True
    assert normalized.checks["refusal_language"] is True

    class EnglishAnswers:
        def retrieve(self, merchant_id, question):
            return kit.service.retrieve(merchant_id, question)

        def answer(self, retrieval):
            return Answer(answer="Do not know.", sources=[], used_llm=True)

    raw = Evaluator(EnglishAnswers(), SEED_MERCHANT_ID, truth, generate=True).evaluate(cases["ood-capital"])
    assert raw.checks["refused"] is True
    assert raw.checks["refusal_language"] is False

    wrong = evaluator.evaluate(cases["agg-paid"])
    assert wrong.checks["answer"] is False


def test_retrieve_exposes_candidates_below_the_threshold(kit):
    run_seed()
    kit.indexer.run()
    strict = RagService(
        kit.service._embedder, kit.chat, kit.service._store, top_k=5, min_score=0.99, max_question_chars=500
    )
    retrieval = strict.retrieve(SEED_MERCHANT_ID, "Quanto dura una sessione di checkout?")
    assert retrieval.candidates and retrieval.selected == []
    answer = strict.answer(retrieval)
    assert answer.used_llm is False and answer.answer == NO_EVIDENCE["it"]
    assert kit.chat.calls == []
    assert re.fullmatch(r"[0-9a-f-]{36}", str(retrieval.merchant_id))


def test_seed_rows_do_not_leak_into_other_merchants(kit, merchant_a):
    run_seed()
    kit.indexer.run()
    retrieval = kit.service.retrieve(merchant_a.id, f"Stato del pagamento {seed_payment_id('pro_open')}")
    assert retrieval.candidates
    assert all(not c.chunk.source_key.startswith("payment:") for c in retrieval.candidates)


@pytest.mark.parametrize("cite_relevant", [True, False])
def test_cited_expected_follows_the_cited_number(kit, cite_relevant):
    run_seed()
    kit.indexer.run()

    def reply(messages):
        numbered = re.findall(r"^\[(\d+)\] (.+)$", messages[-1].content, flags=re.MULTILINE)
        wanted = [n for n, title in numbered if ("4. Unknown outcomes" in title) is cite_relevant]
        return f"Dura 2 hours [{wanted[0]}]."

    kit.chat.reply = reply
    service = RagService(
        kit.service._embedder, kit.chat, kit.service._store, top_k=5, min_score=0.0, max_question_chars=500
    )
    case = next(c for c in load_cases() if c.id == "docs-en-ttl")
    result = Evaluator(service, SEED_MERCHANT_ID, _truth(), generate=True).evaluate(case)
    assert result.checks["retrieved"] is True
    assert result.checks["cited_expected"] is cite_relevant
